"""Módulo de ejemplo: Uso de casco (EPP).

Implementa el contrato canónico PerceptaModule (docs/CONTRACTS.md §3).
El modelo real (YOLO fine-tuneado para EPP) se resuelve vía model.artifactRef del
manifest; aquí se estructura la integración. Invariantes del contrato:
  - infer() devuelve confianza CRUDA, sin umbralizar ni filtrar por horario/zona.
  - Sin efectos secundarios: solo produce detecciones (human-in-the-loop).
"""
from __future__ import annotations

import time
from typing import Any

from percepta_contracts import (
    Detection,
    Frame,
    InferenceResult,
    ModuleContext,
    PerceptaModule,
)


class HelmetDetectionModule(PerceptaModule):
    CLASSES = {0: "person", 1: "helmet", 2: "no_helmet"}

    def __init__(self) -> None:
        self._model: Any = None
        self._ctx: ModuleContext | None = None

    def load(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        # El ai-worker resuelve manifest.model.artifactRef a una ruta local de pesos
        # (model registry / bundle firmado en on-prem) y la pasa en ctx.config["_modelPath"].
        # from ultralytics import YOLO
        # self._model = YOLO(ctx.config["_modelPath"]).to(ctx.device)
        self._model = object()  # placeholder hasta integrar el registry de modelos

    def warmup(self) -> None:
        # Inferencias dummy para estabilizar kernels/allocs (no-op con el placeholder).
        pass

    def infer(self, frame: Frame) -> InferenceResult:
        t0 = time.perf_counter()
        detections: list[Detection] = []
        # results = self._model.predict(frame.image, verbose=False)
        # for box in results[0].boxes:
        #     x, y, w, h = _to_normalized_xywh(box.xyxy[0], frame.width, frame.height)
        #     detections.append(Detection(
        #         class_label=self.CLASSES[int(box.cls)],
        #         class_id=int(box.cls),
        #         confidence=float(box.conf),   # CRUDA: rules-engine decide
        #         bbox=(x, y, w, h),
        #     ))
        return InferenceResult(
            detections=detections,
            inference_ms=(time.perf_counter() - t0) * 1000.0,
        )

    def health(self) -> dict[str, Any]:
        return {
            "ok": self._model is not None,
            "device": self._ctx.device if self._ctx else "unloaded",
            "module_key": "helmet-detection",
        }

    def release(self) -> None:
        self._model = None


# Punto de entrada que el ai-worker busca al cargar el módulo.
MODULE_CLASS = HelmetDetectionModule
