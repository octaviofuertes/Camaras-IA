"""Elementos de protección personal: casco, chaleco, antiparras y guantes.

Mira a cada persona en el cuadro y avisa cuando le falta un elemento que en esa
cámara es obligatorio. Qué es obligatorio se configura por cámara, porque
depende del lugar: en un obrador se exige casco y chaleco, en un laboratorio
antiparras y guantes, y en una oficina nada.

── Qué modelo usa y por qué está entrenado acá ─────────────────────────────

El YOLO de siempre está entrenado con COCO, que no tiene "casco" ni "chaleco"
entre sus ochenta clases: con él no hay forma de detectar EPP. Este módulo usa
un modelo propio, entrenado en este repositorio con un dataset público
(`training/ppe/`), cuyas métricas por clase quedan en `training/models/epp.json`.

Se entrenó acá en vez de bajar pesos de terceros por dos motivos: se sabe con
qué datos se entrenó y bajo qué licencia, y hay números medidos sobre imágenes
que el modelo nunca vio. Sin eso, "funciona bien" es una opinión.

── Lo que NO hace ──────────────────────────────────────────────────────────

No decide sanciones ni lleva un legajo por persona. Emite una alerta para que
alguien mire y decida: la detección es una ayuda, no un veredicto. Y no avisa
cuando "no vio" el elemento, sino cuando vio que falta — la diferencia está
explicada en reglas.py y es lo que separa este módulo de uno que se apaga a la
semana por avisar de más.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reglas import ConfigEpp, VigiladorEpp, POR_CLAVE  # noqa: E402

log = logging.getLogger("ppe-detection")

#: Dónde queda el modelo entrenado, relativo a la raíz del repositorio.
PESOS_POR_OMISION = "training/models/epp.pt"

#: Cómo se llama la persona en el modelo entrenado.
CLASE_PERSONA = "Person"


class PpeDetectionModule(PerceptaModule):
    def __init__(self) -> None:
        self._modelo: Any = None
        self._ctx: ModuleContext | None = None
        self._vigilador: VigiladorEpp | None = None
        self._nombres: dict[int, str] = {}
        self._cuadros = 0
        self._faltas = 0
        self._personas_vistas = 0
        self._cargado_en: float | None = None
        self._pesos = ""

    # ── ciclo de vida ────────────────────────────────────────────────
    def load(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        cfg = dict(ctx.config or {})

        pesos = str(cfg.get("pesos") or cfg.get("_modelPath") or PESOS_POR_OMISION)
        ruta = Path(pesos)
        if not ruta.is_absolute():
            # Relativo a la raíz del repositorio, no al directorio de trabajo:
            # el worker se arranca desde cualquier lado.
            ruta = Path(__file__).resolve().parents[2] / pesos
        if not ruta.is_file():
            raise RuntimeError(
                f"No está el modelo de EPP en {ruta}. "
                "Entrenalo con: python training/ppe/entrenar.py"
            )

        from ultralytics import YOLO

        self._modelo = YOLO(str(ruta))
        self._modelo.to("cpu" if ctx.device.startswith("cpu") else ctx.device)
        self._nombres = dict(self._modelo.names)
        self._pesos = str(ruta)

        exigidos = cfg.get("exigidos") or ["casco", "chaleco"]
        desconocidos = [e for e in exigidos if e not in POR_CLAVE]
        if desconocidos:
            # Se avisa y se sigue con los que sí existen: dejar la cámara sin
            # vigilar por un nombre mal escrito es peor que vigilar de menos.
            log.error(
                "elementos desconocidos en la configuración: %s (los válidos son %s)",
                desconocidos, list(POR_CLAVE),
            )
            exigidos = [e for e in exigidos if e in POR_CLAVE]

        self._vigilador = VigiladorEpp(ConfigEpp(
            exigidos=tuple(exigidos),
            minConfianza=float(cfg.get("minConfianza", 0.45)),
            solapeMinimo=float(cfg.get("solapeMinimo", 0.55)),
            framesSeguidos=int(cfg.get("framesSeguidos", 4)),
            repetirSegundos=float(cfg.get("repetirSegundos", 120.0)),
        ))
        self._cargado_en = time.time()
        log.info("EPP cargado desde %s; se exige: %s", ruta.name, ", ".join(exigidos))

    def warmup(self) -> None:
        if self._modelo is None:
            return
        # Una inferencia en vacío deja los kernels listos: sin esto el primer
        # cuadro real tarda varios segundos y se pierde.
        self._modelo.predict(
            np.zeros((640, 640, 3), dtype=np.uint8), verbose=False, device="cpu",
        )

    def release(self) -> None:
        self._modelo = None

    # ── inferencia ───────────────────────────────────────────────────
    def infer(self, frame: Frame) -> InferenceResult:
        if self._modelo is None or self._vigilador is None:
            raise RuntimeError("el módulo no fue cargado (falta load())")

        t0 = time.perf_counter()
        h, w = frame.image.shape[:2]
        self._cuadros += 1

        personas: list[tuple[float, float, float, float]] = []
        ids: list[int] = []
        elementos: list[tuple[str, tuple[float, float, float, float], float]] = []

        # Se sigue a las personas para que la cuenta de cuadros seguidos no se
        # mezcle cuando dos se cruzan.
        for r in self._modelo.track(
            frame.image, persist=True, tracker="bytetrack.yaml",
            conf=0.25, verbose=False, device="cpu",
        ):
            cajas = getattr(r, "boxes", None)
            if cajas is None:
                continue
            for b in cajas:
                clase = self._nombres.get(int(b.cls.item()), "")
                conf = float(b.conf.item())
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
                caja = (x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h)
                if clase == CLASE_PERSONA:
                    tid = int(b.id.item()) if getattr(b, "id", None) is not None else -1
                    personas.append(caja)
                    ids.append(tid)
                else:
                    elementos.append((clase, caja, conf))

        self._personas_vistas += len(personas)

        detecciones: list[Detection] = []
        for falta in self._vigilador.ver(personas, elementos, frame.captured_at, ids):
            self._faltas += 1
            detecciones.append(Detection(
                class_label=falta.elemento.falta,
                class_id=0,
                confidence=round(falta.confianza, 4),
                bbox=falta.caja_persona,
                attributes={
                    "kind": "ppe",
                    "elemento": falta.elemento.clave,
                    "nombreElemento": falta.elemento.nombre,
                    "eventType": falta.elemento.evento,
                    # El pipeline confirma por su cuenta; acá ya se aplicó la
                    # persistencia, así que esta detección viene decidida.
                    "confirmed": "true",
                    "reason": f"no se le ve el {falta.elemento.nombre}",
                },
            ))

        return InferenceResult(
            detections=detecciones,
            inference_ms=(time.perf_counter() - t0) * 1000.0,
        )

    # ── diagnóstico ──────────────────────────────────────────────────
    def health(self) -> dict[str, Any]:
        return {
            "ok": self._modelo is not None,
            "pesos": self._pesos,
            "clasesDelModelo": [self._nombres[i] for i in sorted(self._nombres)],
            "cuadrosProcesados": self._cuadros,
            "personasVistas": self._personas_vistas,
            "faltasAvisadas": self._faltas,
            **(self._vigilador.estado() if self._vigilador else {}),
            "loadedAt": self._cargado_en,
        }


MODULE_CLASS = PpeDetectionModule
