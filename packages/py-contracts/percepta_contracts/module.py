"""Contrato ÚNICO del plugin de IA. Canónico: docs/CONTRACTS.md §3.

Todo módulo de IA implementa `PerceptaModule`. El core (ai-worker) solo conoce esta
interfaz: instalar una capacidad nueva = publicar un módulo que la implemente + su
`module.json`, SIN tocar el core.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # evita dependencia dura de numpy en import time del contrato
    import numpy as np

PLUGIN_API_VERSION = "1.0.0"  # el core carga módulos con el MISMO major


@dataclass(frozen=True)
class Frame:
    camera_id: str
    frame_seq: int
    captured_at: float          # epoch segundos UTC
    image: "np.ndarray"         # BGR HxWx3 (referencia zero-copy; NO copiar)
    width: int
    height: int
    ring_buffer_key: str


@dataclass(frozen=True)
class Detection:
    class_label: str
    class_id: int
    confidence: float           # 0..1 CRUDO, sin umbralizar
    bbox: tuple[float, float, float, float]   # x,y,w,h normalizado 0..1
    track_id: int = -1
    keypoints: list[tuple[str, float, float, float]] = field(default_factory=list)
    in_zones: list[str] = field(default_factory=list)
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class InferenceResult:
    detections: list[Detection]
    inference_ms: float


@dataclass
class ModuleContext:
    ai_module_id: str
    module_key: str
    module_version: str
    device: str                 # "cuda:0" | "cpu"
    config: dict[str, Any]      # config VALIDADA contra el JSON Schema del manifest
    zones: dict[str, list[tuple[float, float]]]   # polígonos normalizados por zona


class PerceptaModule(ABC):
    """Contrato único que todo módulo de IA implementa."""

    plugin_api_version: str = PLUGIN_API_VERSION

    @abstractmethod
    def load(self, ctx: ModuleContext) -> None:
        """Carga pesos/engine (ONNX/TensorRT/PyTorch) en el device. Idempotente."""

    @abstractmethod
    def warmup(self) -> None:
        """Corre inferencias dummy para estabilizar latencia."""

    @abstractmethod
    def infer(self, frame: Frame) -> InferenceResult:
        """Detección CRUDA sobre un frame.

        NO aplica horarios/zonas/umbrales de negocio: eso es responsabilidad de
        rules-engine a partir de camera_module_configs.config.
        """

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Estado para readiness/liveness."""

    @abstractmethod
    def release(self) -> None:
        """Libera memoria GPU/CPU."""
