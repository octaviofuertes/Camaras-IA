"""Módulo de IA: identificación de personas por rostro.

Reconoce a los empleados dados de alta para que la actividad pueda atribuirse a
cada uno en vez de repartirse entre todos. Es lo que convierte "en este puesto
hubo 1 h de teléfono" en "Juan estuvo 1 h con el teléfono de las 8 que estuvo".

LO QUE SALE DE ACÁ
------------------
Dos cosas distintas, y el pipeline las trata distinto:

  identidades  — telemetría: qué persona está en qué recuadro. La consume el
                 módulo de actividad, que corre después.
  desconocidos — alerta: una pregunta para el operador, "¿reconocés a esta
                 persona?". Es lo único de este módulo que entra en la cola de
                 revisión, y sólo una vez por persona.

LO QUE NO SE GUARDA
-------------------
De quien no está dado de alta, nada persistente. La imagen del rostro viaja con
la pregunta para que el operador pueda responderla, y se borra al responder. Si
la respuesta es "no trabaja acá", no queda ni la imagen ni la plantilla: el
sistema se olvida y volverá a preguntar si esa persona reaparece días después.
Es más molesto y es la decisión correcta.

La lógica de comparación vive en `rostros.py`, sin dependencias del motor, y
está cubierta por pruebas.
"""
from __future__ import annotations

import base64
import json
import logging
import sys
import threading
import time
from dataclasses import fields
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import requests

from percepta_contracts import (
    Detection,
    Frame,
    InferenceResult,
    ModuleContext,
    PerceptaModule,
)

sys.path.insert(0, str(Path(__file__).parent))
from rostros import (  # noqa: E402
    ConfigRostros,
    Identificador,
    Persona,
    Rostro,
)

log = logging.getLogger(__name__)


class PersonIdentificationModule(PerceptaModule):
    def __init__(self) -> None:
        self._app: Any = None
        self._ctx: ModuleContext | None = None
        self._ident: Identificador | None = None
        self._loaded_at = 0.0
        self._identificados = 0
        self._preguntas = 0
        self._ultima_galeria = 0.0
        self._parar = threading.Event()

    # ── ciclo de vida ───────────────────────────────────────────────
    def load(self, ctx: ModuleContext) -> None:
        from insightface.app import FaceAnalysis  # import perezoso

        self._ctx = ctx
        self._ident = Identificador(self._config_validada(ctx.config))

        # Sólo detección y reconocimiento. `buffalo_l` trae además estimación de
        # edad, género y puntos faciales: cuesta tiempo de CPU y produce
        # atributos sobre personas que este sistema no tiene por qué inferir.
        self._app = FaceAnalysis(
            name=str(ctx.config.get("faceModel", "buffalo_l")),
            allowed_modules=["detection", "recognition"],
            providers=["CPUExecutionProvider"],
        )
        det = int(ctx.config.get("detSize", 640))
        self._app.prepare(ctx_id=-1, det_size=(det, det))

        self._refrescar_galeria()
        # Las altas ocurren mientras el sistema corre: sin esto, un empleado
        # recién dado de alta seguiría apareciendo como desconocido hasta
        # reiniciar, y el operador respondería la misma pregunta dos veces.
        threading.Thread(target=self._vigilar_galeria, name="galeria-rostros", daemon=True).start()
        self._loaded_at = time.time()

    def _config_validada(self, config: dict) -> ConfigRostros:
        esquema = {}
        ruta = Path(__file__).parent / "config.schema.json"
        try:
            esquema = json.loads(ruta.read_text(encoding="utf-8")).get("properties", {})
        except Exception:  # noqa: BLE001
            log.warning("no se pudo leer %s: se aplica sin validar", ruta.name)

        cfg = ConfigRostros()
        propios = {f.name for f in fields(ConfigRostros)}
        for clave, valor in (config or {}).items():
            if clave not in propios:
                continue
            tipo = type(getattr(cfg, clave))
            try:
                v = tipo(valor)
            except (TypeError, ValueError):
                log.warning("config: %s=%r no es %s; se ignora", clave, valor, tipo.__name__)
                continue
            regla = esquema.get(clave, {})
            lo, hi = regla.get("minimum"), regla.get("maximum")
            if lo is not None and v < lo:
                v = tipo(lo)
            if hi is not None and v > hi:
                v = tipo(hi)
            setattr(cfg, clave, v)
        return cfg

    def warmup(self) -> None:
        if self._app is not None:
            self._app.get(np.zeros((480, 640, 3), dtype=np.uint8))

    def release(self) -> None:
        self._parar.set()
        self._app = None
        self._ident = None

    # ── galería de empleados ────────────────────────────────────────
    def _vigilar_galeria(self) -> None:
        while not self._parar.wait(30.0):
            try:
                self._refrescar_galeria()
            except Exception:  # noqa: BLE001
                log.exception("no se pudo refrescar la galería de rostros")

    def _refrescar_galeria(self) -> None:
        """Trae las plantillas de los empleados dados de alta.

        Si el servicio no responde, se conserva la galería anterior: quedarse
        sin galería convertiría a todos los empleados conocidos en desconocidos
        y llenaría la cola de revisión de preguntas ya respondidas.
        """
        cfg = (self._ctx.config if self._ctx else {}) or {}
        base = str(cfg.get("analyticsUrl", "http://127.0.0.1:3005")).rstrip("/")
        token = str(cfg.get("serviceToken", "")) or None
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        try:
            r = requests.get(f"{base}/api/v1/persons/faces", headers=headers, timeout=6)
            if r.status_code != 200:
                log.warning("galería: el servicio respondió %s", r.status_code)
                return
            datos = r.json().get("items", [])
        except requests.RequestException as exc:
            log.warning("galería no disponible (%s); se conserva la anterior", exc)
            return

        personas = [
            Persona(id=p["id"], nombre=p.get("displayName", ""), vectores=p.get("embeddings", []))
            for p in datos
            if p.get("embeddings")
        ]
        if self._ident is not None:
            self._ident.galeria.actualizar(personas)
        self._ultima_galeria = time.time()
        log.info("galería de rostros: %d persona(s) dadas de alta", len(personas))

    # ── inferencia ──────────────────────────────────────────────────
    def infer(self, frame: Frame) -> InferenceResult:
        if self._app is None or self._ident is None:
            raise RuntimeError("el módulo no fue cargado (falta load())")

        t0 = time.perf_counter()
        caras = self._app.get(frame.image)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        h, w = frame.image.shape[:2]
        rostros: list[Rostro] = []
        for c in caras:
            x1, y1, x2, y2 = (float(v) for v in c.bbox)
            emb = np.asarray(c.normed_embedding, dtype=np.float32)
            rostros.append(
                Rostro(
                    vector=emb.tolist(),
                    x=max(x1 / w, 0.0), y=max(y1 / h, 0.0),
                    w=(x2 - x1) / w, h=(y2 - y1) / h,
                    calidad=float(getattr(c, "det_score", 1.0)),
                )
            )

        resultados = self._ident.identificar(rostros, ahora=frame.captured_at)
        detecciones: list[Detection] = []

        for res in resultados:
            r = res.rostro
            bbox = (r.x, r.y, r.w, r.h)

            if res.persona_id:
                self._identificados += 1
                detecciones.append(
                    Detection(
                        class_label="person.identified",
                        class_id=0,
                        confidence=round(res.parecido, 4),
                        bbox=bbox,
                        attributes={
                            "kind": "identity",
                            "personId": res.persona_id,
                            "personName": res.nombre or "",
                            "similarity": f"{res.parecido:.4f}",
                        },
                    )
                )
                continue

            if res.preguntar:
                self._preguntas += 1
                detecciones.append(
                    Detection(
                        class_label="person.unknown",
                        class_id=0,
                        confidence=float(r.calidad),
                        bbox=bbox,
                        attributes={
                            # Alerta: es una pregunta para una persona, no una
                            # medición. Va a la cola de revisión.
                            "kind": "alert",
                            "confirmed": "true",   # no necesita persistencia extra
                            "faceThumbnail": self._recorte(frame.image, r),
                            "embedding": _empaquetar(r.vector),
                            "reason": "rostro no reconocido",
                        },
                    )
                )
                continue

            # Presente pero sin identificar (ambiguo, chico, ya preguntado).
            detecciones.append(
                Detection(
                    class_label="person.unidentified",
                    class_id=0,
                    confidence=0.0,
                    bbox=bbox,
                    attributes={"kind": "identity", "personId": "", "reason": res.motivo},
                )
            )

        return InferenceResult(detections=detecciones, inference_ms=elapsed_ms)

    def _recorte(self, imagen: np.ndarray, r: Rostro) -> str:
        """Recorte del rostro para que el operador pueda responder la pregunta.

        Viaja con la alerta y se borra al responderla. Es el único momento en
        que existe una imagen de alguien que no está dado de alta, y dura lo que
        tarda una persona en decidir.
        """
        h, w = imagen.shape[:2]
        m = 0.25  # margen: una cara recortada al ras es difícil de reconocer
        x1 = max(int((r.x - r.w * m) * w), 0)
        y1 = max(int((r.y - r.h * m) * h), 0)
        x2 = min(int((r.x + r.w * (1 + m)) * w), w)
        y2 = min(int((r.y + r.h * (1 + m)) * h), h)
        if x2 <= x1 or y2 <= y1:
            return ""
        recorte = imagen[y1:y2, x1:x2]
        ok, buf = cv2.imencode(".jpg", recorte, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        return base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""

    def health(self) -> dict[str, Any]:
        return {
            "ok": self._app is not None,
            "model": "buffalo_l" if self._app is not None else None,
            "device": self._ctx.device if self._ctx else None,
            "empleadosDadosDeAlta": len(self._ident.galeria.personas) if self._ident else 0,
            "identificaciones": self._identificados,
            "preguntasEmitidas": self._preguntas,
            "desconocidosEnMemoria": self._ident.desconocidos.recordados if self._ident else 0,
            "galeriaActualizadaHace": (
                round(time.time() - self._ultima_galeria, 1) if self._ultima_galeria else None
            ),
            "loadedAt": self._loaded_at or None,
        }


def _empaquetar(vector: list[float]) -> str:
    """Vector a base64, para que viaje con la alerta sin inflar el JSON."""
    return base64.b64encode(np.asarray(vector, dtype=np.float32).tobytes()).decode("ascii")


MODULE_CLASS = PersonIdentificationModule
