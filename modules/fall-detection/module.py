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
from features import WINDOW, frame_features, hip_y, pad_or_trim  # noqa: E402
from classifier import FallClassifier  # noqa: E402

log = logging.getLogger(__name__)


class FallDetectionModule(PerceptaModule):
    def __init__(self) -> None:
        self._model: Any = None
        self._ctx: ModuleContext | None = None
        self._detector: FallDetector | None = None
        self._imgsz = 640
        self._loaded_at = 0.0
        self._last_purge = 0.0
        # Ventana de features por persona: es lo que se adjunta a la alerta
        # para poder reentrenar con el veredicto del operador.
        self._ventanas: dict[int, list] = {}
        self._ultimo_hip: dict[int, float] = {}
        self._ultimo_ts: dict[int, float] = {}
        self._clasificador: FallClassifier | None = None
        # Último resultado por persona: alimenta el diagnóstico en vivo, que es
        # lo que permite ver QUÉ señal falló cuando una caída no se detecta.
        self._ultimo: dict[int, dict] = {}

    def load(self, ctx: ModuleContext) -> None:
        from ultralytics import YOLO  # import perezoso

        self._ctx = ctx
        weights = str(ctx.config.get("weights", "yolov8n-pose.pt"))
        self._imgsz = int(ctx.config.get("imgsz", 640))
        # Deliberadamente bajo: quien filtra las detecciones flojas es el
        # tracker, que las usa en su segunda asociación para no soltar a una
        # persona mientras se cae (es cuando peor se la detecta). Las personas
        # fantasma se cortan con `new_track_thresh` en bytetrack.yaml y con el
        # tamaño mínimo del detector, no bajando el recall acá.
        self._person_conf = float(ctx.config.get("personConfidence", 0.15))
        self._tracker_cfg = str(Path(__file__).parent / "bytetrack.yaml")

        # La configuración de la cámara alimenta directamente al detector: cada
        # cámara puede tener su propia sensibilidad sin tocar código.
        cfg = FallConfig()
        for campo in (
            "keypointScore", "minTorsoPoints", "downAngleDeg", "downRatio",
            "fallVelocity", "collapseRatio", "baselineFrames",
            "impactConfirmSeconds", "impactConfirmFrames", "fallWindowSeconds", "prolongedSeconds",
            "trunkDropRatio", "trunkDropSure", "downVerticality",
            "minConfidence", "trackTimeoutSeconds", "minPersonHeight",
            "baselineWindowFrames",
        ):
            if campo in ctx.config:
                setattr(cfg, campo, type(getattr(cfg, campo))(ctx.config[campo]))
        self._detector = FallDetector(cfg)

        # El clasificador entrenado es opcional: si todavía no se entrenó,
        # el módulo funciona sólo con reglas.
        self._clasificador = FallClassifier()
        if not self._clasificador.disponible:
            log.info("sin clasificador entrenado: decisión por reglas geométricas")

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
            conf=self._person_conf,
            persist=True,           # conserva los tracks entre llamadas
            tracker=self._tracker_cfg,
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

                # Acumular la ventana de features de ESTA persona. Se hace
                # siempre, aunque no haya alerta: cuando la caída se confirme,
                # lo que importa es lo que pasó ANTES, y para entonces ya sería
                # tarde para empezar a grabar.
                kp_arr = np.array([[k.x, k.y, k.score] for k in keypoints], dtype=np.float32)
                dt = ts - self._ultimo_ts.get(track_id, ts - 0.1)
                vec = frame_features(kp_arr, bbox, self._ultimo_hip.get(track_id), max(dt, 1e-3))
                ventana = self._ventanas.setdefault(track_id, [])
                ventana.append(vec)
                if len(ventana) > WINDOW * 2:
                    del ventana[: len(ventana) - WINDOW * 2]
                self._ultimo_hip[track_id] = hip_y(kp_arr) or self._ultimo_hip.get(track_id, 0.0)
                self._ultimo_ts[track_id] = ts

                self._ultimo[track_id] = {
                    "estado": res.state.value,
                    "colapso": round(res.collapse_ratio, 2) if res.collapse_ratio is not None else None,
                    # La señal que ahora decide: cuánto bajó el tronco.
                    "troncoBajo": round(res.trunk_drop, 2) if res.trunk_drop is not None else None,
                    "verticalidad": round(res.verticality, 1) if res.verticality is not None else None,
                    "velocidad": round(res.velocity, 2),
                    "anguloTorso": round(res.torso_angle, 0) if res.torso_angle is not None else None,
                    "altoAncho": round(res.aspect_ratio, 2) if res.aspect_ratio is not None else None,
                    "segundosAbajo": round(res.down_seconds, 1),
                    "motivo": res.reason,
                    "poseOk": res.quality_ok,
                }

                # Se reporta a la persona cuando hay algo que mirar: la caída
                # confirmada, o el estado previo con evidencia suficiente.
                interesante = res.is_fall or res.state.value in ("falling", "impact", "alerted")
                if not interesante:
                    continue

                # Cuando las reglas confirman, se le pregunta al modelo
                # entrenado: aporta el juicio aprendido de caídas reales, que
                # la geometría sola no puede dar.
                confianza = res.confidence
                origen = "reglas"
                secuencia_np = None
                if res.is_fall and ventana and self._clasificador:
                    secuencia_np = pad_or_trim(np.stack(ventana))
                    prob = self._clasificador.predecir(secuencia_np)
                    confianza, origen = self._clasificador.combinar(res.confidence, prob)

                detections.append(
                    Detection(
                        # 'fall' sólo en el frame en que se confirma; el resto es
                        # contexto para el operador, no una alerta.
                        class_label="fall" if res.is_fall else f"person_{res.state.value}",
                        class_id=0,
                        confidence=confianza,
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
                            "severidad": res.severity,
                            "colapsoAltura": (
                                f"{res.collapse_ratio:.2f}" if res.collapse_ratio is not None else ""
                            ),
                            "troncoBajo": (
                                f"{res.trunk_drop:.2f}" if res.trunk_drop is not None else ""
                            ),
                            "verticalidad": (
                                f"{res.verticality:.1f}" if res.verticality is not None else ""
                            ),
                            "poseQuality": "ok" if res.quality_ok else "insuficiente",
                            "origenConfianza": origen,
                        },
                        # Sólo en la alerta confirmada: es la única que un
                        # operador va a etiquetar, y guardar la ventana de cada
                        # frame intermedio sería desperdicio.
                        sequence=(
                            [[round(float(v), 4) for v in fila] for fila in secuencia_np]
                            if secuencia_np is not None
                            else None
                        ),
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
                    # Altura de referencia aprendida para esta persona: si es
                    # None, todavía no hay con qué comparar y no puede haber
                    # detección de colapso.
                    "alturaBase": round(st.baseline, 3) if st.baseline else None,
                    "picoVelocidad": round(st.peak_velocity, 2),
                    **(self._ultimo.get(tid) or {}),
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
            "clasificador": (
                {"activo": True, "umbral": self._clasificador.umbral,
                 "test": self._clasificador.metadata.get("test", {})}
                if self._clasificador and self._clasificador.disponible
                else {"activo": False, "motivo": "todavía no se entrenó"}
            ),
            "tracked": len(estados),
            "people": estados,
            "loadedAt": self._loaded_at or None,
        }

    def release(self) -> None:
        self._model = None
        self._detector = None


MODULE_CLASS = FallDetectionModule
