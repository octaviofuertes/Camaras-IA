"""Contrato Python del plugin de IA de Percepta. Fuente única: docs/CONTRACTS.md §3."""

from .module import (
    PLUGIN_API_VERSION,
    Frame,
    Detection,
    InferenceResult,
    ModuleContext,
    PerceptaModule,
)

__all__ = [
    "PLUGIN_API_VERSION",
    "Frame",
    "Detection",
    "InferenceResult",
    "ModuleContext",
    "PerceptaModule",
]
