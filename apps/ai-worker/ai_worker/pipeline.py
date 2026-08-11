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
        analytics_url: str = "http://127.0.0.1:3005",
    ) -> None:
        super().__init__(name=f"pipe-{assignment.camera_id}", daemon=True)
        self.a = assignment
        self.instances = instances
        self.media_url = media_url.rstrip("/")
        self.event_url = event_url.rstrip("/")
        self.analytics_url = analytics_url.rstrip("/")
        self.token = token
        self.interval = 1.0 / fps
        self._stop = threading.Event()
        self._state: dict[str, _ModuleState] = {m["moduleKey"]: _ModuleState() for m in assignment.modules}

        self.frames_processed = 0
        self.events_created = 0
        self.metrics_sent = 0
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
    def _evaluate(self, mod_cfg: dict, dets: list, st: _ModuleState, now: float) -> tuple[bool, list]:
        """Aplica la configuración de la cámara. Devuelve (dispara, detecciones).

        Estas son las reglas que NO viven en el módulo: el módulo entrega
        confianza cruda y acá se decide si eso amerita molestar a un operador.
        """
        cfg = mod_cfg.get("config", {})
        clave = mod_cfg.get("moduleKey", "?")
        min_conf = float(cfg.get("minConfidence", 0.45))
        min_persist = int(cfg.get("minPersistenceFrames", 3))
        cooldown = float(cfg.get("cooldownSeconds", 60))
        min_persons = int(cfg.get("minPersons", 1))
        # `classes` es la lista blanca de qué detecciones ameritan una alerta.
        # Estaba por defecto en ["person"], un valor inventado que no sale de
        # ningún módulo: cualquier módulo cuya alerta se llame distinto —la
        # pregunta "¿reconocés a esta persona?" se llama 'person.unknown'— era
        # descartada acá, sin evento y sin una sola línea de log. Si el módulo no
        # declara la lista, no se filtra por clase: el módulo es el que sabe qué
        # emite, y adivinar por él es lo que rompió esto.
        declaradas = cfg.get("classes")
        wanted = set(declaradas) if declaradas else None

        strong = [
            d for d in dets
            if d.confidence >= min_conf and (wanted is None or d.class_label in wanted)
        ]

        # Un módulo que ya confirmó la caída y queda afuera por confianza es un
        # descarte que hay que poder ver: si no, el operador sólo observa que la
        # caída "no se detectó" y no hay forma de saber que sí se detectó y la
        # regla la filtró. No se anula su umbral —esa decisión es suya— pero se
        # deja registro.
        for d in dets:
            if d.attributes.get("confirmed") != "true":
                continue
            if wanted is not None and d.class_label not in wanted:
                # El caso que dejó la pregunta por un desconocido sin llegar
                # nunca a Eventos. Un módulo que confirmó algo y queda afuera
                # por la lista blanca es un error de configuración, no una
                # decisión: se dice con el nombre de la clase, que es el dato
                # que hace falta para arreglarlo.
                log.error(
                    "[%s] %s confirmó '%s' pero `classes` sólo acepta %s: la alerta se descarta",
                    self.a.camera_id, clave, d.class_label, sorted(wanted),
                )
                continue
            if d.confidence < min_conf:
                log.warning(
                    "[%s] detección confirmada por el módulo DESCARTADA por minConfidence: "
                    "'%s' con confianza %.2f < %.2f (%s)",
                    self.a.camera_id, d.class_label, d.confidence, min_conf,
                    d.attributes.get("reason", ""),
                )

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
        # tiempo, para que un reintento de la misma alerta no cree dos eventos.
        #
        # La ventana es la MISMA que el enfriamiento, y eso importa: estaba fija
        # en un minuto mientras el enfriamiento configurado era de 20 s, así que
        # una segunda caída legítima 25 s después de la primera pasaba el
        # enfriamiento, chocaba con la clave del mismo minuto y se descartaba en
        # silencio. Dos mecanismos de supresión con ventanas distintas hacen
        # justo lo que no se quiere: perder una caída real sin dejar rastro.
        ventana = max(float(mod_cfg.get("config", {}).get("cooldownSeconds", 60)), 1.0)
        bucket = int(now // ventana)
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
                # La pregunta "¿reconocés a esta persona?" necesita mostrar de
                # quién se habla, y guardar su vector para poder darla de alta
                # si la respuesta es que sí. Ambos viajan con el evento y se van
                # con él: si la respuesta es "no trabaja acá", no queda nada.
                **(
                    {"faceThumbnail": top.attributes["faceThumbnail"]}
                    if top.attributes.get("faceThumbnail") else {}
                ),
                **(
                    {"faceEmbedding": top.attributes["embedding"]}
                    if top.attributes.get("embedding") else {}
                ),
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
            if r.status_code in (401, 403):
                # Este caso merece grito propio: el detector sigue funcionando y
                # los logs se ven normales, pero NINGUNA alerta llega al panel.
                # Es el fallo más engañoso de todo el pipeline.
                self.last_error = (
                    f"event-service {r.status_code}: el SERVICE_TOKEN no sirve para dar de alta "
                    f"eventos (¿vencido, o emitido con un rol sin events:ingest?). "
                    f"Las caídas se están detectando pero NO se registran. Detalle: {r.text[:120]}"
                )
                log.error("[%s] %s", self.a.camera_id, self.last_error)
            else:
                self.last_error = f"event-service {r.status_code}: {r.text[:120]}"
                log.warning("[%s] alta rechazada: %s", self.a.camera_id, self.last_error)
        except requests.RequestException as exc:
            self.last_error = f"event-service: {exc}"
        return False

    def _modulos_ordenados(self) -> list[dict]:
        """Los módulos de esta cámara, con los dependidos primero.

        Orden topológico simple: si A declara depender de B, B corre antes. Sin
        esto, el módulo de actividad recibiría el contexto del frame ANTERIOR y
        atribuiría el teléfono de alguien al que estaba ahí un segundo antes.

        Ante un ciclo —que sería un error de manifiestos, no de configuración—
        se conserva el orden original y se avisa, en vez de colgarse.
        """
        pendientes = {m["moduleKey"]: m for m in self.a.modules}
        salida: list[dict] = []
        visitando: set[str] = set()
        resueltos: set[str] = set()

        def visitar(clave: str) -> None:
            if clave in resueltos or clave not in pendientes:
                return
            if clave in visitando:
                log.warning("[%s] dependencia circular en %s", self.a.camera_id, clave)
                return
            visitando.add(clave)
            for dep in pendientes[clave].get("requires") or []:
                visitar(dep)
            visitando.discard(clave)
            resueltos.add(clave)
            salida.append(pendientes[clave])

        for m in self.a.modules:
            visitar(m["moduleKey"])
        return salida

    # ── mediciones (informes) ────────────────────────────────────────
    def _emitir_medicion(self, mod_cfg: dict, det) -> None:
        """Persiste una ventana de actividad en analytics-service.

        No crea evento, no dispara notificación y no pasa por el enfriamiento de
        alertas: es una serie de tiempo. Si el servicio no está disponible, la
        muestra se pierde y se registra — se prefiere un hueco declarado en el
        informe antes que reintentos que dupliquen tiempo contado.
        """
        a = det.attributes
        payload = {
            "cameraId": self.a.camera_id,
            "siteId": self.a.site_id,
            "moduleKey": mod_cfg["moduleKey"],
            "moduleVersion": mod_cfg.get("moduleVersion", "1.0.0"),
            "zoneId": a.get("zoneId") or None,
            "zoneName": a.get("zoneName") or "Toda la cámara",
            "from": float(a.get("from", 0.0)),
            "to": float(a.get("to", 0.0)),
            "occupiedSeconds": float(a.get("occupiedSeconds", 0.0)),
            "phoneSeconds": float(a.get("phoneSeconds", 0.0)),
            "emptySeconds": float(a.get("emptySeconds", 0.0)),
            "uncoveredSeconds": float(a.get("uncoveredSeconds", 0.0)),
            "maxPeople": int(a.get("maxPeople", 0)),
            "meanOccupancy": float(a.get("meanOccupancy", 0.0)),
        }
        try:
            r = requests.post(
                f"{self.analytics_url}/api/v1/analytics/activity",
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=6,
            )
            if r.status_code in (200, 201):
                self.metrics_sent += 1
                self.last_error = None
                return
            self.last_error = f"analytics-service {r.status_code}: {r.text[:120]}"
            log.warning("[%s] muestra rechazada: %s", self.a.camera_id, self.last_error)
        except requests.RequestException as exc:
            self.last_error = f"analytics-service: {exc}"
            log.warning("[%s] no se pudo guardar la muestra: %s", self.a.camera_id, exc)

    def _emitir_medicion_persona(self, mod_cfg: dict, det) -> None:
        """Persiste el tiempo atribuido a una persona.

        Va a un endpoint distinto del de puestos porque son datos de distinta
        naturaleza y distinta sensibilidad: éste lleva nombre y apellido, y su
        lectura está detrás de un permiso que un operador no tiene.
        """
        a = det.attributes
        payload = {
            "cameraId": self.a.camera_id,
            "siteId": self.a.site_id,
            "zoneId": a.get("zoneId") or None,
            "zoneName": a.get("zoneName") or "Toda la cámara",
            "personId": a.get("personId") or None,
            "from": float(a.get("from", 0.0)),
            "to": float(a.get("to", 0.0)),
            "presentSeconds": float(a.get("presentSeconds", 0.0)),
            "phoneSeconds": float(a.get("phoneSeconds", 0.0)),
        }
        try:
            r = requests.post(
                f"{self.analytics_url}/api/v1/persons/activity",
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=6,
            )
            if r.status_code in (200, 201):
                self.metrics_sent += 1
                self.last_error = None
                return
            self.last_error = f"analytics-service {r.status_code}: {r.text[:120]}"
            # Con el payload: un 500 repetido sin saber qué se mandó no se puede
            # diagnosticar, y esta muestra es la única fuente del informe por
            # persona — si se rechaza, ese informe queda vacío para siempre.
            log.warning("[%s] muestra por persona rechazada: %s — payload: %s",
                        self.a.camera_id, self.last_error, payload)
        except requests.RequestException as exc:
            self.last_error = f"analytics-service: {exc}"
            log.warning("[%s] no se pudo guardar la muestra por persona: %s", self.a.camera_id, exc)

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
            # Lo que ya detectaron los módulos anteriores en ESTE frame. Es lo
            # que permite que un módulo dependa de otro: el de actividad recibe
            # acá quién es cada persona del cuadro, para atribuirle su tiempo en
            # vez de repartirlo entre todos los que estaban presentes.
            producido: dict[str, list] = {}

            for mod_cfg in self._modulos_ordenados():
                key = mod_cfg["moduleKey"]
                inst = self.instances.get(key)
                if inst is None:
                    continue
                st = self._state.setdefault(key, _ModuleState())

                requiere = mod_cfg.get("requires") or []
                contexto = [d for k in requiere for d in producido.get(k, [])]
                if contexto or requiere:
                    try:
                        inst.observar_contexto(contexto)
                    except Exception:  # noqa: BLE001
                        log.exception("[%s] %s falló al recibir el contexto", self.a.camera_id, key)

                try:
                    res = inst.infer(frame)
                except Exception as exc:  # un módulo que falla no frena a los demás
                    self.last_error = f"{key}: {exc!r}"
                    log.exception("[%s] fallo en %s", self.a.camera_id, key)
                    continue

                # Una MEDICIÓN no es una alerta. Los módulos de informe —el de
                # actividad por puesto— emiten ventanas de tiempo ya cerradas,
                # que se persisten como serie y nunca entran en la cola de
                # revisión humana. Mezclarlas llenaría esa cola de datos que
                # nadie tiene que atender, y el operador dejaría de mirarla.
                producido[key] = res.detections

                mediciones = [d for d in res.detections if d.attributes.get("kind") == "telemetry"]
                # `identity` no es alerta ni medición: es contexto para el módulo
                # que corre después. No se persiste ni molesta a nadie.
                alertas = [
                    d for d in res.detections
                    if d.attributes.get("kind") not in ("telemetry", "identity")
                ]
                for m in mediciones:
                    if m.attributes.get('serie') == 'person':
                        self._emitir_medicion_persona(mod_cfg, m)
                    else:
                        self._emitir_medicion(mod_cfg, m)

                fire, strong = self._evaluate(mod_cfg, alertas, st, t0)
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
        salud: dict[str, dict] = {}
        for clave, inst in self.instances.items():
            try:
                salud[clave] = inst.health()
            except Exception as exc:  # noqa: BLE001
                salud[clave] = {"ok": False, "error": repr(exc)}

        return {
            "cameraId": self.a.camera_id,
            "modules": [m["moduleKey"] for m in self.a.modules],
            # Lo que el módulo dice de sí mismo: cuántas caras vio, cuántas
            # preguntas emitió, a cuántos empleados tiene cargados. Es la
            # diferencia entre "la alerta no llega" y "no hay nada que alertar".
            "moduleHealth": salud,
            # Qué reglas se le están aplicando. Un `classes` que no incluye lo
            # que el módulo emite hace desaparecer la alerta, y era invisible.
            "rules": {
                m["moduleKey"]: {
                    "eventType": m.get("eventType"),
                    "severity": m.get("severity"),
                    "classes": (m.get("config") or {}).get("classes"),
                    "minConfidence": (m.get("config") or {}).get("minConfidence", 0.45),
                }
                for m in self.a.modules
            },
            "framesProcessed": self.frames_processed,
            "eventsCreated": self.events_created,
            "metricsSent": self.metrics_sent,
            "lastError": self.last_error,
            "liveDetections": self.last_detections,
        }
