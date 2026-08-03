"""Módulo de IA REAL: detección de personas con YOLO (COCO).

Es la capacidad base de la plataforma: casi todos los módulos de personas
(conteo, merodeo, zona restringida, permanencia) se construyen sobre estas
detecciones más seguimiento y reglas temporales.

Devuelve confianza CRUDA, sin umbralizar: filtrar por confianza, horario o
zona es responsabilidad de `rules-engine` a partir de la configuración de cada
cámara (CONTRACTS §3).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from percepta_contracts import (
    Detection,
    Frame,
    InferenceResult,
    ModuleContext,
    PerceptaModule,
)

log = logging.getLogger(__name__)

# Clases COCO que este módulo reporta. 'person' es la principal; el resto
# aporta contexto útil para módulos derivados (vehículos, objetos).
COCO_KEEP = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
    24: "backpack",
    26: "handbag",
    28: "suitcase",
}


class PersonDetectionModule(PerceptaModule):
    def __init__(self) -> None:
        self._model: Any = None
        self._ctx: ModuleContext | None = None
        self._imgsz = 640
        self._classes: list[int] = []
        self._loaded_at = 0.0

    def load(self, ctx: ModuleContext) -> None:
        from ultralytics import YOLO  # import perezoso: no penaliza si el módulo no se usa

        self._ctx = ctx
        weights = str(ctx.config.get("weights", "yolov8n.pt"))
        self._imgsz = int(ctx.config.get("imgsz", 640))

        only = ctx.config.get("classes")
        if only:
            names = {v: k for k, v in COCO_KEEP.items()}
            self._classes = [names[c] for c in only if c in names]
        else:
            self._classes = list(COCO_KEEP.keys())

        log.info("cargando YOLO %s en %s", weights, ctx.device)
        self._model = YOLO(weights)          # descarga los pesos la primera vez
        self._model.to("cpu" if ctx.device.startswith("cpu") else ctx.device)
        self._loaded_at = time.time()

    def warmup(self) -> None:
        if self._model is None:
            return
        dummy = np.zeros((self._imgsz, self._imgsz, 3), dtype=np.uint8)
        self._model.predict(dummy, imgsz=self._imgsz, verbose=False, device="cpu")

    def infer(self, frame: Frame) -> InferenceResult:
        if self._model is None:
            raise RuntimeError("el módulo no fue cargado (falta load())")

        t0 = time.perf_counter()
        results = self._model.predict(
            frame.image,
            imgsz=self._imgsz,
            classes=self._classes or None,
            conf=0.01,          # umbral casi nulo: el filtrado real es de rules-engine
            verbose=False,
            device="cpu",
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        detections: list[Detection] = []
        h, w = frame.image.shape[:2]

        for r in results:
            boxes = getattr(r, "boxes", None)
            if boxes is None:
                continue
            for b in boxes:
                cls_id = int(b.cls.item())
                label = COCO_KEEP.get(cls_id)
                if label is None:
                    continue
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
                detections.append(
                    Detection(
                        class_label=label,
                        class_id=cls_id,
                        confidence=float(b.conf.item()),
                        # Normalizado 0..1: independiente de la resolución de la cámara
                        bbox=(x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h),
                        track_id=int(b.id.item()) if getattr(b, "id", None) is not None else -1,
                    )
                )

        return InferenceResult(detections=detections, inference_ms=elapsed_ms)

    def health(self) -> dict[str, Any]:
        # No miente: si el modelo no está cargado, ok=False.
        return {
            "ok": self._model is not None,
            "model": "yolov8n" if self._model is not None else None,
            "device": self._ctx.device if self._ctx else None,
            "imgsz": self._imgsz,
            "loadedAt": self._loaded_at or None,
        }

    def release(self) -> None:
        self._model = None


MODULE_CLASS = PersonDetectionModule
