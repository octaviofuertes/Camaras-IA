"""Pipeline real: frame de la cámara -> inferencia -> reglas -> evento persistido.

Este es el lazo que convierte video en alertas. Por cada cámara con módulos
asignados corre un hilo que:
  1. toma el último frame de media-service,
  2. lo pasa por cada módulo asignado (un solo decode compartido),
  3. aplica la configuración de esa cámara (confianza, persistencia, cooldown),
  4. crea el evento vía event-service, que lo persiste con RLS.

Human-in-the-loop: el evento nace en estado `new` y ningún paso automático lo
mueve de ahí. La revisión es siempre de una persona.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
import requests

from percepta_contracts import Frame, ModuleContext, PerceptaModule

log = logging.getLogger("pipeline")


@dataclass
class CameraAssignment:
    """Una cámara y los módulos que ejecuta (= filas de camera_module_configs)."""
    camera_id: str
    site_id: str
    organization_id: str
    modules: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _ModuleState:
    """Estado temporal por módulo: lo que convierte detecciones sueltas en alertas."""
    consecutive: int = 0
    last_event_ts: float = 0.0
    last_detections: list[dict] = field(default_factory=list)


class CameraPipeline(threading.Thread):
    def __init__(
        self,
        assignment: CameraAssignment,
        instances: dict[str, PerceptaModule],
        *,
        media_url: str,
        event_url: str,
        token: str,
        fps: float = 4.0,
    ) -> None:
        super().__init__(name=f"pipe-{assignment.camera_id}", daemon=True)
        self.a = assignment
        self.instances = instances
        self.media_url = media_url.rstrip("/")
        self.event_url = event_url.rstrip("/")
        self.token = token
        self.interval = 1.0 / fps
        self._stop = threading.Event()
        self._state: dict[str, _ModuleState] = {m["moduleKey"]: _ModuleState() for m in assignment.modules}

        self.frames_processed = 0
        self.events_created = 0
        self.last_error: str | None = None
        self.last_detections: list[dict] = []

    def stop(self) -> None:
        self._stop.set()

    # ── captura ──────────────────────────────────────────────────────
    def _grab(self) -> np.ndarray | None:
        """Trae el frame más reciente de media-service."""
        try:
            r = requests.get(
                f"{self.media_url}/cameras/{self.a.camera_id}/snapshot.jpg", timeout=4
            )
            if r.status_code != 200:
                return None
            arr = np.frombuffer(r.content, np.uint8)
            return cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except requests.RequestException as exc:
            self.last_error = f"media-service: {exc}"
            return None

    # ── reglas ───────────────────────────────────────────────────────
    def _evaluate(self, cfg: dict, dets: list, st: _ModuleState, now: float) -> tuple[bool, list]:
        """Aplica la configuración de la cámara. Devuelve (dispara, detecciones).

        Estas son las reglas que NO viven en el módulo: el módulo entrega
        confianza cruda y acá se decide si eso amerita molestar a un operador.
        """
        min_conf = float(cfg.get("minConfidence", 0.45))
        min_persist = int(cfg.get("minPersistenceFrames", 3))
        cooldown = float(cfg.get("cooldownSeconds", 60))
        min_persons = int(cfg.get("minPersons", 1))
        wanted = set(cfg.get("classes") or ["person"])

        strong = [d for d in dets if d.confidence >= min_conf and d.class_label in wanted]

        if len(strong) >= min_persons:
            st.consecutive += 1
        else:
            st.consecutive = 0
            return False, []

        # Hay módulos que ya confirman por su cuenta a lo largo del tiempo: la
        # detección de caídas, por ejemplo, exige segundos de permanencia en el
        # suelo y recién ahí emite la alerta, en UN solo frame. Pedirles
        # persistencia adicional haría que la alerta no se emitiera nunca.
        self_confirmed = any(d.attributes.get("confirmed") == "true" for d in strong)

        # Persistencia: evita alertar por un parpadeo de un solo frame.
        if not self_confirmed and st.consecutive < min_persist:
            return False, strong

        # Cooldown: no repetir la misma alerta cada pocos segundos.
        if now - st.last_event_ts < cooldown:
            return False, strong

        return True, strong

    # ── alta del evento ──────────────────────────────────────────────
    def _emit(self, mod_cfg: dict, dets: list, now: float) -> bool:
        top = max(dets, key=lambda d: d.confidence)
        # La clave de deduplicación agrupa por cámara+módulo+tipo y ventana de
        # tiempo: dos alertas del mismo motivo en el mismo minuto son una sola.
        bucket = int(now // 60)
        raw = f"{self.a.camera_id}|{mod_cfg['moduleKey']}|{mod_cfg['eventType']}|{bucket}"
        dedup = hashlib.sha1(raw.encode()).hexdigest()

        payload = {
            "siteId": self.a.site_id,
            "cameraId": self.a.camera_id,
            "aiModuleId": mod_cfg["aiModuleId"],
            "moduleKey": mod_cfg["moduleKey"],
            "moduleVersion": mod_cfg.get("moduleVersion", "1.0.0"),
            "eventType": mod_cfg["eventType"],
            "severity": mod_cfg.get("severity", "medium"),
            "confidence": round(float(top.confidence), 4),
            "dedupKey": dedup,
            "detection": {
                "classLabel": top.class_label,
                "bbox": [round(v, 4) for v in top.bbox],
                "count": len(dets),
                "all": [
                    {"classLabel": d.class_label, "confidence": round(float(d.confidence), 3)}
                    for d in dets[:10]
                ],
            },
            "metadata": {"detector": mod_cfg["moduleKey"], "objects": len(dets)},
        }

        # Si el módulo aportó la ventana temporal que sostiene la alerta, viaja
        # con el evento: es la materia prima del reentrenamiento.
        if getattr(top, "sequence", None):
            payload["trainingSequence"] = top.sequence
        try:
            r = requests.post(
                f"{self.event_url}/api/v1/events",
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=6,
            )
            if r.status_code in (200, 201):
                created = r.json().get("created", False)
                if created:
                    self.events_created += 1
                    log.info(
                        "[%s] EVENTO %s (%s conf=%.2f, %d objeto(s))",
                        self.a.camera_id, mod_cfg["eventType"], top.class_label,
                        top.confidence, len(dets),
                    )
                return created
            self.last_error = f"event-service {r.status_code}: {r.text[:120]}"
            log.warning("[%s] alta rechazada: %s", self.a.camera_id, self.last_error)
        except requests.RequestException as exc:
            self.last_error = f"event-service: {exc}"
        return False

    # ── lazo principal ───────────────────────────────────────────────
    def run(self) -> None:
        log.info("[%s] pipeline iniciado con %d módulo(s)", self.a.camera_id, len(self.a.modules))
        seq = 0
        while not self._stop.is_set():
            t0 = time.time()
            img = self._grab()
            if img is None:
                self._stop.wait(1.0)
                continue

            seq += 1
            self.frames_processed += 1
            h, w = img.shape[:2]
            frame = Frame(
                camera_id=self.a.camera_id, frame_seq=seq, captured_at=t0,
                image=img, width=w, height=h, ring_buffer_key=f"{self.a.camera_id}:{seq}",
            )

            snapshot: list[dict] = []
            for mod_cfg in self.a.modules:
                key = mod_cfg["moduleKey"]
                inst = self.instances.get(key)
                if inst is None:
                    continue
                st = self._state.setdefault(key, _ModuleState())
                try:
                    res = inst.infer(frame)
                except Exception as exc:  # un módulo que falla no frena a los demás
                    self.last_error = f"{key}: {exc!r}"
                    log.exception("[%s] fallo en %s", self.a.camera_id, key)
                    continue

                fire, strong = self._evaluate(mod_cfg.get("config", {}), res.detections, st, t0)
                snapshot.extend(
                    {
                        "moduleKey": key,
                        "classLabel": d.class_label,
                        "confidence": round(float(d.confidence), 3),
                        "bbox": [round(v, 4) for v in d.bbox],
                    }
                    for d in strong
                )
                if fire and self._emit(mod_cfg, strong, t0):
                    st.last_event_ts = t0
                    st.consecutive = 0

            self.last_detections = snapshot

            elapsed = time.time() - t0
            if elapsed < self.interval:
                self._stop.wait(self.interval - elapsed)

        log.info("[%s] pipeline detenido", self.a.camera_id)

    def stats(self) -> dict:
        return {
            "cameraId": self.a.camera_id,
            "modules": [m["moduleKey"] for m in self.a.modules],
            "framesProcessed": self.frames_processed,
            "eventsCreated": self.events_created,
            "lastError": self.last_error,
            "liveDetections": self.last_detections,
        }
