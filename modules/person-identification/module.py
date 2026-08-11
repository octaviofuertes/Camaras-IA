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
    asociar_a_cuerpo,
)
from continuidad import (  # noqa: E402
    ConfigContinuidad,
    IdentidadSostenida,
    firma_apariencia,
)

log = logging.getLogger(__name__)


class PersonIdentificationModule(PerceptaModule):
    def __init__(self) -> None:
        self._app: Any = None
        self._yolo: Any = None
        self._ctx: ModuleContext | None = None
        self._ident: Identificador | None = None
        self._sostenida: IdentidadSostenida | None = None
        self._loaded_at = 0.0
        self._identificados = 0
        self._preguntas = 0
        # Para poder distinguir "la alerta no llega" de "la cámara no le ve la
        # cara a nadie", que desde afuera se parecen y se arreglan distinto.
        self._rostros_vistos = 0
        self._ultima_cara_alto = 0.0
        self._poses_medidas = 0
        self._pose = None
        # Cuerpos por los que ya se preguntó: track_id -> cuándo. Vence con el
        # mismo plazo que la memoria de caras, para que no crezca sin límite y
        # para que un identificador de seguimiento reutilizado más tarde no
        # silencie una pregunta legítima.
        self._preguntados: dict[int, float] = {}
        # Quiénes estaban dados de alta la última vez que se miró.
        self._firma_actual: tuple | None = None
        self._ultima_galeria = 0.0
        self._parar = threading.Event()

    # ── ciclo de vida ───────────────────────────────────────────────
    def load(self, ctx: ModuleContext) -> None:
        from insightface.app import FaceAnalysis  # import perezoso

        from ultralytics import YOLO

        self._ctx = ctx
        self._ident = Identificador(self._config_validada(ctx.config))

        # La cara es el ancla, pero en una oficina la gente pasa el día sentada
        # y de espaldas. Sin seguir cuerpos, la identidad se perdería apenas la
        # persona se da vuelta y el informe diría "sin identificar" casi
        # siempre. Ver `continuidad.py`.
        cfg_cont = ConfigContinuidad()
        for campo in ("aparienciaThreshold", "aparienciaMargin", "aparienciaHoras",
                      "trackGraciaSegundos", "puestoRadio"):
            if campo in (ctx.config or {}):
                try:
                    setattr(cfg_cont, campo, float(ctx.config[campo]))
                except (TypeError, ValueError):
                    log.warning("config: %s inválido, se usa el valor por omisión", campo)
        self._sostenida = IdentidadSostenida(cfg_cont)

        self._yolo = YOLO(str(ctx.config.get("personWeights", "yolov8n.pt")))
        self._yolo.to("cpu" if ctx.device.startswith("cpu") else ctx.device)

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

        # Estimador de pose, POR SEPARADO y no dentro del pipeline de arriba.
        #
        # Medido en esta máquina: incluirlo en `allowed_modules` lleva el cuadro
        # de 663 ms a 1112 ms —un 68% más, en todos los cuadros y para todas las
        # caras—. Suelto cuesta 114 ms y se paga sólo por las pocas caras que son
        # candidatas a preguntar, que son un puñado por persona cada diez
        # minutos. Da el mismo valor: se comparó cara por cara y la diferencia
        # fue 0.00 grados.
        self._pose = self._cargar_pose(str(ctx.config.get("faceModel", "buffalo_l")))

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

    @staticmethod
    def _cargar_pose(nombre_modelo: str):
        """Modelo de puntos 3D del rostro: es el que sabe hacia dónde mira.

        Si no está, se devuelve None y NO se pregunta por nadie: sin poder medir
        la pose no hay forma de saber si el recorte muestra una cara o una nuca,
        y preguntar por una nuca es una alerta que nadie puede contestar.
        """
        try:
            import os

            from insightface.model_zoo import get_model
            from insightface.utils import ensure_available

            raiz = ensure_available("models", nombre_modelo, root="~/.insightface")
            ruta = os.path.join(raiz, "1k3d68.onnx")
            if not os.path.exists(ruta):
                log.error("falta %s: sin estimación de pose no se puede preguntar", ruta)
                return None
            m = get_model(ruta, providers=["CPUExecutionProvider"])
            m.prepare(ctx_id=-1)
            return m
        except Exception:  # noqa: BLE001
            log.exception("no se pudo cargar el estimador de pose")
            return None

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
        ctx = self._ctx
        base = (getattr(ctx, "analytics_url", "") or "http://127.0.0.1:3005").rstrip("/")
        token = getattr(ctx, "service_token", "") if ctx else ""
        if not token:
            # Sin credencial no hay galería, y sin galería toda persona conocida
            # vuelve a ser desconocida. Se dice fuerte en vez de degradar callado.
            log.error("no hay token de servicio: la galería de empleados no se puede consultar")
            return
        headers = {"Authorization": f"Bearer {token}"}

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
            # Si cambió QUIÉN está dado de alta, se olvida a quién se le
            # preguntó. Sin esto queda una zona muerta: a la persona que se
            # borró de la galería ya no se la reconoce, pero tampoco se vuelve a
            # preguntar por ella —porque hace diez minutos se preguntó— y el
            # sistema se queda callado sin razón visible. Pasa igual al dar de
            # alta a alguien: su cara sigue en la lista de desconocidos, y ahí
            # no tiene nada que hacer.
            firma = self._firma_galeria(personas)
            self._ident.galeria.actualizar(personas)
            if firma != self._firma_actual:
                if self._firma_actual is not None:
                    # Sólo se olvida a quien ya ES alguien. Olvidar a todos haría
                    # que dar de alta a una persona reabriera las preguntas de
                    # todas las demás que estaban en el cuadro.
                    n = self._ident.desconocidos.olvidar_a_los_conocidos(
                        self._ident.galeria, self._ident.cfg
                    )
                    if n:
                        log.info("%d cara(s) dejaron de ser desconocidas: ya están dadas de alta", n)
                    self._preguntados.clear()
                self._firma_actual = firma
        self._ultima_galeria = time.time()
        log.info("galería de rostros: %d persona(s) dadas de alta", len(personas))

    @staticmethod
    def _firma_galeria(personas: list[Persona]) -> tuple:
        """Identifica el contenido de la galería: quiénes y con cuántas fotos.

        Cuenta las plantillas además de los ids porque sumarle un ángulo a
        alguien también cambia a quién se reconoce.
        """
        return tuple(sorted((p.id, len(p.vectores)) for p in personas))

    # ── inferencia ──────────────────────────────────────────────────
    def infer(self, frame: Frame) -> InferenceResult:
        if self._app is None or self._ident is None or self._sostenida is None:
            raise RuntimeError("el módulo no fue cargado (falta load())")

        t0 = time.perf_counter()
        h, w = frame.image.shape[:2]

        # 1. Cuerpos con identidad de seguimiento. Es lo que permite sostener
        #    quién es alguien cuando se da vuelta.
        cuerpos: list[tuple[int, tuple[float, float, float, float]]] = []
        for r in self._yolo.track(
            frame.image, classes=[0], conf=0.35, persist=True,
            tracker="bytetrack.yaml", verbose=False, device="cpu",
        ):
            cajas = getattr(r, "boxes", None)
            if cajas is None:
                continue
            for b in cajas:
                tid = int(b.id.item()) if getattr(b, "id", None) is not None else -1
                if tid < 0:
                    continue
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
                cuerpos.append((tid, (x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h)))

        # 2. Caras visibles en este frame.
        rostros: list[Rostro] = []
        crudos: list = []
        for c in self._app.get(frame.image):
            crudos.append(c)
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
        self._rostros_vistos += len(rostros)
        if rostros:
            self._ultima_cara_alto = max(r.h for r in rostros)
        self._medir_pose_de_candidatas(frame.image, rostros, crudos)
        identificaciones = self._ident.identificar(rostros, ahora=frame.captured_at)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        cajas_cuerpo = [c for _, c in cuerpos]
        detecciones: list[Detection] = []
        anclados: set[int] = set()

        # 3. Las caras reconocidas ANCLAN su identidad al cuerpo que las lleva.
        for res in identificaciones:
            idx = asociar_a_cuerpo(res.rostro, cajas_cuerpo)

            if res.persona_id and idx is not None:
                tid, caja = cuerpos[idx]
                self._sostenida.anclar_por_rostro(
                    track_id=tid,
                    persona_id=res.persona_id,
                    nombre=res.nombre or "",
                    apariencia=self._apariencia(frame.image, caja),
                    posicion=(caja[0] + caja[2] / 2, caja[1] + caja[3]),
                    ahora=frame.captured_at,
                )
                anclados.add(tid)
                self._identificados += 1
                detecciones.append(
                    self._identidad(caja, res.persona_id, res.nombre or "", "rostro", res.parecido)
                )
                continue

            if res.preguntar:
                # Si ya se preguntó por el cuerpo que lleva esta cara, no se
                # vuelve a preguntar aunque el vector no lo reconozca. Es el
                # único criterio que no se rompe cuando la persona gira la
                # cabeza, y girar la cabeza es lo que la gente hace todo el día.
                tid_cara = cuerpos[idx][0] if idx is not None else None
                if tid_cara is not None:
                    vence = self._ident.cfg.askCooldownMinutes * 60.0
                    ahora = frame.captured_at
                    self._preguntados = {
                        k: v for k, v in self._preguntados.items() if ahora - v <= vence
                    }
                    if tid_cara in self._preguntados:
                        self._preguntados[tid_cara] = ahora
                        continue
                    self._preguntados[tid_cara] = ahora
                self._preguntas += 1
                detecciones.append(
                    Detection(
                        class_label="person.unknown", class_id=0,
                        confidence=float(res.rostro.calidad),
                        bbox=(res.rostro.x, res.rostro.y, res.rostro.w, res.rostro.h),
                        attributes={
                            "kind": "alert",
                            "confirmed": "true",
                            "faceThumbnail": self._recorte(frame.image, res.rostro),
                            "embedding": _empaquetar(res.rostro.vector),
                            "reason": "rostro no reconocido",
                        },
                    )
                )

        # 4. Los cuerpos sin cara reconocida en este frame: se resuelve por
        #    continuidad de seguimiento, apariencia o puesto. Es la vía que
        #    cubre a quien está sentado de espaldas, o sea casi toda la jornada.
        for tid, caja in cuerpos:
            if tid in anclados:
                continue
            r = self._sostenida.resolver(
                track_id=tid,
                apariencia=self._apariencia(frame.image, caja),
                posicion=(caja[0] + caja[2] / 2, caja[1] + caja[3]),
                ahora=frame.captured_at,
            )
            if r.persona_id:
                self._identificados += 1
                detecciones.append(
                    self._identidad(caja, r.persona_id, r.nombre or "", r.via, r.confianza)
                )
            else:
                detecciones.append(
                    Detection(
                        class_label="person.unidentified", class_id=0, confidence=0.0,
                        bbox=caja,
                        attributes={"kind": "identity", "personId": "", "via": "ninguna"},
                    )
                )

        return InferenceResult(detections=detecciones, inference_ms=elapsed_ms)

    def _medir_pose_de_candidatas(self, imagen, rostros: list[Rostro], crudos: list) -> None:
        """Estima la pose sólo de las caras que podrían generar una pregunta.

        El filtro barato va primero —tamaño y nitidez—: descarta la mayoría sin
        gastar nada. Lo que queda son las pocas caras que un operador podría
        llegar a ver, y ahí sí vale pagar los 114 ms.
        """
        if self._pose is None or self._ident is None:
            return
        cfg = self._ident.cfg
        for r, c in zip(rostros, crudos):
            if r.calidad < cfg.askMinScore or r.h < cfg.askMinFaceSize:
                continue
            try:
                self._pose.get(imagen, c)
                pose = getattr(c, "pose", None)
                if pose is not None and len(pose) > 1:
                    r.pitch = float(pose[0])
                    r.yaw = float(pose[1])
                    self._poses_medidas += 1
            except Exception:  # noqa: BLE001
                log.exception("no se pudo estimar la pose de un rostro")

    def _identidad(
        self, caja: tuple[float, float, float, float],
        persona_id: str, nombre: str, via: str, confianza: float,
    ) -> Detection:
        """Identidad de un cuerpo, con la vía por la que se supo.

        `via` viaja hasta el informe a propósito: mostrar "Juan: 6 h" sin decir
        que cuatro de esas horas salen de continuidad y no de haberle visto la
        cara sería esconder de dónde sale el propio número.
        """
        return Detection(
            class_label="person.identified", class_id=0,
            confidence=round(float(confianza), 4), bbox=caja,
            attributes={
                "kind": "identity",
                "personId": persona_id,
                "personName": nombre,
                "via": via,
            },
        )

    def _apariencia(self, imagen: np.ndarray, caja: tuple[float, float, float, float]) -> list[float]:
        """Firma de apariencia del torso: colores de la ropa.

        Histograma HSV grueso de la franja superior del cuerpo. No es
        reconocimiento de personas: sirve sólo para volver a enganchar a alguien
        DENTRO del mismo día, y por eso vence (ver `continuidad.py`). Mañana la
        misma persona con otra ropa da otra firma, y eso es correcto.
        """
        h, w = imagen.shape[:2]
        x, y, bw, bh = caja
        # Franja del torso: se saltea la cabeza y no se llega a las piernas,
        # que quedan tapadas por el escritorio en la mitad de los casos.
        x1 = max(int((x + bw * 0.15) * w), 0)
        x2 = min(int((x + bw * 0.85) * w), w)
        y1 = max(int((y + bh * 0.18) * h), 0)
        y2 = min(int((y + bh * 0.55) * h), h)
        if x2 - x1 < 4 or y2 - y1 < 4:
            return []

        torso = cv2.cvtColor(imagen[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([torso], [0, 1], None, [12, 4], [0, 180, 0, 256])
        return firma_apariencia(hist.flatten().tolist())

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
            "rostrosDetectados": self._rostros_vistos,
            "rostrosDescartadosPorTamano": self._ident.descartados_por_tamano if self._ident else 0,
            # Alto de la última cara vista, como fracción del alto de la imagen.
            # Si queda por debajo de `minFaceSize`, la persona está demasiado
            # lejos de la cámara y no hay nada que el software pueda arreglar.
            "ultimaCaraAlto": round(self._ultima_cara_alto, 4),
            "estimadorDePose": self._pose is not None,
            "posesMedidas": self._poses_medidas,
            "minFaceSize": self._ident.cfg.minFaceSize if self._ident else None,
            "desconocidosEnMemoria": self._ident.desconocidos.recordados if self._ident else 0,
            "cuerposYaPreguntados": len(self._preguntados),
            # Lo que sostiene la identidad cuando la cara no se ve: a cuánta
            # gente se le conoce el puesto es la respuesta a "¿va a seguir
            # sabiendo quién es cuando se dé vuelta?".
            **(self._sostenida.estado() if self._sostenida else {}),
            "galeriaActualizadaHace": (
                round(time.time() - self._ultima_galeria, 1) if self._ultima_galeria else None
            ),
            "loadedAt": self._loaded_at or None,
        }


def _empaquetar(vector: list[float]) -> str:
    """Vector a base64, para que viaje con la alerta sin inflar el JSON."""
    return base64.b64encode(np.asarray(vector, dtype=np.float32).tobytes()).decode("ascii")


MODULE_CLASS = PersonIdentificationModule
