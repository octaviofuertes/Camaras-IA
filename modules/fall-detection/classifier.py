"""Clasificador entrenado de caídas (ONNX). Complementa a las reglas.

Cómo se combinan reglas y modelo, y por qué:

  - Las REGLAS son la puerta de entrada. Sólo se consulta al modelo cuando la
    máquina de estados ya vio algo compatible con una caída. Así el costo de
    inferencia se paga sólo cuando hace falta, y —más importante— nunca se
    alerta por algo que las reglas descartan de plano.

  - El MODELO afina la confianza. Donde las reglas dicen "esto parece una
    caída", el modelo aporta cuánto se parece a las caídas reales que vio
    durante el entrenamiento. Es lo que distingue una caída de alguien que se
    tiró al piso a propósito, algo que la geometría sola no puede juzgar.

Si el modelo no está disponible, el módulo sigue funcionando sólo con reglas:
degradación elegante, no una caída del servicio.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

# El entrenamiento deja el modelo acá.
RUTA_MODELO = Path(__file__).parent.parent.parent / "training" / "models" / "fall_classifier.onnx"
RUTA_META = RUTA_MODELO.parent / "metadata.json"


class FallClassifier:
    """Envoltorio del modelo entrenado. Nunca lanza: si falla, se desactiva."""

    def __init__(self, ruta: Path | None = None) -> None:
        self.disponible = False
        self.umbral = 0.5
        self.metadata: dict[str, Any] = {}
        self._sesion: Any = None
        self._entrada = "sequence"
        self._cargar(ruta or RUTA_MODELO)

    def _cargar(self, ruta: Path) -> None:
        if not ruta.is_file():
            log.info("sin modelo entrenado en %s: se usan sólo las reglas", ruta)
            return
        try:
            import onnxruntime as ort
        except ImportError:
            log.warning("falta onnxruntime: se usan sólo las reglas")
            return

        try:
            self._sesion = ort.InferenceSession(str(ruta), providers=["CPUExecutionProvider"])
            self._entrada = self._sesion.get_inputs()[0].name
            if RUTA_META.is_file():
                self.metadata = json.loads(RUTA_META.read_text(encoding="utf-8"))
                self.umbral = float(self.metadata.get("umbralRecomendado", 0.5))
            self.disponible = True
            log.info("modelo de caídas cargado (umbral %.2f)", self.umbral)
        except Exception as exc:  # noqa: BLE001
            log.warning("no se pudo cargar el modelo (%s): se usan sólo las reglas", exc)

    def predecir(self, secuencia: np.ndarray) -> float | None:
        """Probabilidad de que la ventana sea una caída. None si no hay modelo."""
        if not self.disponible or self._sesion is None:
            return None
        try:
            x = secuencia.astype(np.float32)[None, :, :]
            logit = self._sesion.run(None, {self._entrada: x})[0]
            return float(1.0 / (1.0 + np.exp(-float(np.asarray(logit).ravel()[0]))))
        except Exception as exc:  # noqa: BLE001
            log.debug("fallo la inferencia del clasificador: %s", exc)
            return None

    # Cuánto pesa el modelo frente a las reglas. Es BAJO a propósito.
    #
    # El modelo entrenado con el dataset público resultó poco calibrado: en la
    # evaluación pasa de detectar todas las caídas a ninguna entre 0,40 y 0,60
    # de umbral. Con 30 caídas de entrenamiento no da para más, así que se lo
    # usa como una señal de apoyo y no como el que decide.
    #
    # A medida que el feedback de los operadores sume caídas reales de ESTE
    # entorno (retrain.py), el modelo se vuelve más confiable y este peso puede
    # subir. La constante está acá, sola, para que ese ajuste sea de una línea.
    PESO_MODELO = 0.3

    def combinar(self, confianza_reglas: float, prob_modelo: float | None) -> tuple[float, str]:
        """Funde ambas señales en una sola confianza, y explica de dónde sale.

        Las reglas mandan: son explicables y auditables —se puede leer POR QUÉ
        alertó: 78° de torso, 4,2 s en el suelo—. El modelo sólo corrige el
        número hacia arriba o hacia abajo.
        """
        if prob_modelo is None:
            return confianza_reglas, "reglas"
        w = self.PESO_MODELO
        combinada = (1 - w) * confianza_reglas + w * prob_modelo
        return round(combinada, 4), f"reglas {confianza_reglas:.2f} + modelo {prob_modelo:.2f}"
