"""Módulo de IA: detección de caídas con estimación de pose (YOLOv8-pose).

Usa el esqueleto de cada persona —no sólo su rectángulo— porque la orientación
del torso es lo único que distingue de verdad "está tendido" de "está agachado".

La lógica de decisión vive en `detector.py`, sin dependencias de YOLO, y está
cubierta por pruebas (`test_detector.py`). Acá sólo se hace el puente: correr el
modelo, seguir a cada persona entre frames y traducir el resultado al contrato.

Reparto de responsabilidades (CONTRACTS §3):
  - Este módulo hace PERCEPCIÓN, incluida la parte temporal: una caída no existe
    en un frame aislado, necesita velocidad de descenso y permanencia.
  - `rules-engine` aplica las REGLAS DE NEGOCIO: umbral de confianza, horarios,
    zonas, enfriamiento entre alertas.

Human-in-the-loop: el módulo nunca "decide" que alguien se cayó. Emite una alerta
con su nivel de confianza y la evidencia que la sostiene (ángulo del torso,
segundos en el suelo, velocidad de descenso) para que una persona la revise.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from percepta_contracts import (
    Detection,
    Frame,
    InferenceResult,
    ModuleContext,
    PerceptaModule,
)

# `detector.py` vive junto a este archivo; el worker carga el módulo por ruta.
sys.path.insert(0, str(Path(__file__).parent))
from detector import FallConfig, FallDetector, Keypoint, PoseFrame  # noqa: E402

log = logging.getLogger(__name__)


class FallDetectionModule(PerceptaModule):
    def __init__(self) -> None:
        self._model: Any = None
        self._ctx: ModuleContext | None = None
        self._detector: FallDetector | None = None
        self._imgsz = 640
        self._loaded_at = 0.0
        self._last_purge = 0.0

    def load(self, ctx: ModuleContext) -> None:
        from ultralytics import YOLO  # import perezoso

        self._ctx = ctx
        weights = str(ctx.config.get("weights", "yolov8n-pose.pt"))
        self._imgsz = int(ctx.config.get("imgsz", 640))

        # La configuración de la cámara alimenta directamente al detector: cada
        # cámara puede tener su propia sensibilidad sin tocar código.
        cfg = FallConfig()
        for campo in (
            "keypointScore", "minTorsoPoints", "downAngleDeg", "downRatio",
            "fallVelocity", "confirmSeconds", "recoverySeconds",
            "stillnessVelocity", "minConfidence", "trackTimeoutSeconds",
        ):
            if campo in ctx.config:
                setattr(cfg, campo, type(getattr(cfg, campo))(ctx.config[campo]))
        self._detector = FallDetector(cfg)

        log.info("cargando modelo de pose %s en %s", weights, ctx.device)
        self._model = YOLO(weights)
        self._model.to("cpu" if ctx.device.startswith("cpu") else ctx.device)
        self._loaded_at = time.time()

    def warmup(self) -> None:
        if self._model is None:
            return
        dummy = np.zeros((self._imgsz, self._imgsz, 3), dtype=np.uint8)
        self._model.predict(dummy, imgsz=self._imgsz, verbose=False, device="cpu")

    def infer(self, frame: Frame) -> InferenceResult:
        if self._model is None or self._detector is None:
            raise RuntimeError("el módulo no fue cargado (falta load())")

        t0 = time.perf_counter()
        # `track` mantiene la identidad de cada persona entre frames: sin eso no
        # se puede medir cuánto tiempo lleva UNA persona en el suelo.
        results = self._model.track(
            frame.image,
            imgsz=self._imgsz,
            classes=[0],            # sólo personas
            conf=0.25,
            persist=True,           # conserva los tracks entre llamadas
            tracker="bytetrack.yaml",
            verbose=False,
            device="cpu",
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        h, w = frame.image.shape[:2]
        detections: list[Detection] = []
        ts = frame.captured_at

        for r in results:
            boxes = getattr(r, "boxes", None)
            kps_all = getattr(r, "keypoints", None)
            if boxes is None or kps_all is None or kps_all.data is None:
                continue

            for i, b in enumerate(boxes):
                if i >= len(kps_all.data):
                    break
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
                bbox = (x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h)
                det_score = float(b.conf.item())
                track_id = int(b.id.item()) if getattr(b, "id", None) is not None else -1
                if track_id < 0:
                    # Sin identidad no hay historia temporal: se omite este frame
                    # para esa persona en lugar de inventar un track.
                    continue

                kp_data = kps_all.data[i].tolist()  # [[x, y, score], ...] en píxeles
                keypoints = [Keypoint(x=p[0] / w, y=p[1] / h, score=p[2]) for p in kp_data]

                res = self._detector.update(
                    PoseFrame(
                        track_id=track_id, ts=ts, keypoints=keypoints,
                        bbox=bbox, det_score=det_score,
                    )
                )

                # Se reporta a la persona cuando hay algo que mirar: la caída
                # confirmada, o el estado previo con evidencia suficiente.
                interesante = res.is_fall or res.state.value in ("falling", "down", "alerted")
                if not interesante:
                    continue

                detections.append(
                    Detection(
                        # 'fall' sólo en el frame en que se confirma; el resto es
                        # contexto para el operador, no una alerta.
                        class_label="fall" if res.is_fall else f"person_{res.state.value}",
                        class_id=0,
                        confidence=res.confidence,
                        bbox=bbox,
                        track_id=track_id,
                        keypoints=[
                            (str(n), k.x, k.y, k.score) for n, k in enumerate(keypoints)
                        ],
                        attributes={
                            # El módulo ya confirmó temporalmente (segundos en el
                            # suelo). `rules-engine` no debe exigir persistencia
                            # extra: la alerta vive en UN solo frame.
                            "confirmed": "true" if res.is_fall else "false",
                            "state": res.state.value,
                            "torsoAngle": f"{res.torso_angle:.1f}" if res.torso_angle is not None else "",
                            "aspectRatio": f"{res.aspect_ratio:.2f}" if res.aspect_ratio is not None else "",
                            "downSeconds": f"{res.down_seconds:.1f}",
                            "velocity": f"{res.velocity:.2f}",
                            "reason": res.reason,
                            "poseQuality": "ok" if res.quality_ok else "insuficiente",
                        },
                    )
                )

        # Olvidar de vez en cuando a quien ya no se ve.
        if ts - self._last_purge > 5.0:
            self._detector.purge(ts)
            self._last_purge = ts

        return InferenceResult(detections=detections, inference_ms=elapsed_ms)

    def health(self) -> dict[str, Any]:
        # Se expone el estado por persona: permite ver en vivo cómo evoluciona
        # la máquina (de pie -> cayendo -> en el suelo) al probar el módulo.
        estados = (
            {
                str(tid): {
                    "state": st.state.value,
                    "downSeconds": round(max(0.0, time.time() - st.down_since), 1) if st.down_since else 0.0,
                }
                for tid, st in self._detector.tracks.items()
            }
            if self._detector
            else {}
        )
        return {
            "ok": self._model is not None,
            "model": "yolov8n-pose" if self._model is not None else None,
            "device": self._ctx.device if self._ctx else None,
            "tracked": len(estados),
            "people": estados,
            "loadedAt": self._loaded_at or None,
        }

    def release(self) -> None:
        self._model = None
        self._detector = None


MODULE_CLASS = FallDetectionModule
