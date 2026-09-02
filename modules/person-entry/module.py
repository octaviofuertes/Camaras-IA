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
import math
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
from accesos import Permanencia, RegistroDePasos  # noqa: E402
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
        self._pasos = RegistroDePasos()
        # Lo último que vio la cámara, listo para dibujar en pantalla. Vive sólo
        # en memoria y se pisa en cada frame: es el presente, no un registro.
        self._en_vivo: list[dict] = []
        self._en_vivo_ts: float = 0.0
        # La mejor cara que se le vio a cada cuerpo TODAVÍA sin identificar,
        # para que el operador pueda ponerle un nombre desde la pantalla sin
        # esperar a que la cámara le agarre otro buen ángulo.
        #
        # Es el mismo trato que la alerta "¿reconocés a esta persona?": vive en
        # memoria, se pisa cuando aparece una foto mejor, y se borra en cuanto
        # esa persona sale del cuadro o deja de ser un desconocido. De quien no
        # está dado de alta no queda nada en ninguna parte.
        self._para_nombrar: dict[int, dict] = {}
        # Desde cuándo está en el cuadro cada cuerpo. Es lo que permite
        # cronometrar a alguien ANTES de saber quién es —y a quien nunca se
        # sepa—. Ver `Permanencia` en accesos.py.
        self._permanencia = Permanencia()
        # persona -> si tiene acceso. Se refresca con la galería.
        self._acceso_de: dict[str, bool] = {}
        self._alertas_acceso = 0
        # Para poder cerrar los pasos por su cuenta al soltar la cámara.
        self._camera_id = ""
        self._site_id = ""
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
        self._camera_id = getattr(ctx, "camera_id", "") or ""
        self._site_id = getattr(ctx, "site_id", "") or ""
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
        # El mismo parpadeo que sostiene la identidad sostiene el cronómetro:
        # si el seguidor pierde a alguien un instante, no es que se haya ido.
        self._permanencia = Permanencia(cfg_cont.trackGraciaSegundos)

        # Detección, no segmentación. La pantalla marca a cada persona con un
        # recuadro sobre la CARA —que es donde está su identidad— y para eso el
        # contorno del cuerpo no aporta nada. Se usaba el modelo `-seg`, que
        # cuesta alrededor del doble de CPU por frame, para pintar una silueta
        # que ya no se dibuja.
        pesos = str(ctx.config.get("personWeights", "yolov8n.pt"))
        self._yolo = YOLO(pesos)
        self._yolo.to("cpu" if ctx.device.startswith("cpu") else ctx.device)

        # Sólo detección y reconocimiento. `buffalo_l` trae además estimación de
        # edad, género y puntos faciales: cuesta tiempo de CPU y produce
        # atributos sobre personas que este sistema no tiene por qué inferir.
        self._app = FaceAnalysis(
            name=str(ctx.config.get("faceModel", "buffalo_l")),
            allowed_modules=["detection", "recognition"],
            providers=["CPUExecutionProvider"],
        )
        # `detSize` es el LADO LARGO, no un cuadrado. Ver `_ajustar_detector`.
        self._det_lado = int(ctx.config.get("detSize", 640))
        # Resolución con la que se buscan los CUERPOS. Es distinta de la de las
        # caras: una cara chica hay que verla con detalle para reconocerla, un
        # cuerpo se ve igual de bien con menos.
        #
        # Estaba sin fijar, así que ultralytics usaba 640 y cada cuadro costaba
        # 265 ms —medido en esta máquina, con la CPU libre—. A 512 baja a 157,
        # un 41% menos, y el cuerpo se sigue detectando igual: lo que se pierde
        # a esta resolución son objetos chicos, y una persona no lo es.
        #
        # Importa porque de acá sale la velocidad con la que los recuadros
        # siguen a la gente en pantalla: con el pipeline a 1 cuadro por segundo
        # y el video a 18, las cajas quedaban flotando en el aire.
        self._cuerpo_lado = int(ctx.config.get("cuerpoDetSize", 512))
        self._det_forma: tuple[int, int] | None = None
        self._app.prepare(ctx_id=-1, det_size=(self._det_lado, self._det_lado))

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
        # Lo que estaba pasando cuando se apagó el módulo igual pasó. Sin esto,
        # a quien estaba adentro le quedaba la hora de salida del último reporte
        # —hasta medio minuto antes— o directamente no quedaba registrado si su
        # paso nunca se había reportado.
        self._cerrar_pasos_pendientes()
        self._parar.set()
        self._app = None
        self._ident = None

    def _cerrar_pasos_pendientes(self) -> None:
        """Persiste los pasos abiertos al soltar la cámara.

        Se manda desde acá y no por el pipeline porque el pipeline ya no va a
        llamar a `infer` nunca más: si la última palabra la tuviera él, estos
        pasos no llegarían a ninguna parte.
        """
        pasos = self._pasos.cerrar_todo()
        if not pasos or self._ctx is None:
            return

        base = (getattr(self._ctx, "analytics_url", "") or "").rstrip("/")
        token = getattr(self._ctx, "service_token", "")
        if not base or not token:
            log.warning("no se pudieron cerrar %d paso(s): falta la credencial", len(pasos))
            return

        for paso in pasos:
            try:
                r = requests.post(
                    f"{base}/api/v1/persons/sightings",
                    json={
                        "siteId": self._site_id,
                        "cameraId": self._camera_id,
                        "personId": paso.persona_id,
                        "from": paso.desde,
                        "to": paso.hasta,
                        "bestScore": paso.mejor_parecido,
                        "seenByFace": paso.visto_por_rostro,
                        "hadAccess": paso.tenia_acceso,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=5,
                )
                if r.status_code not in (200, 201):
                    log.warning("cierre de paso rechazado: %s %s", r.status_code, r.text[:120])
            except requests.RequestException as exc:
                log.warning("no se pudo cerrar el paso de %s: %s", paso.nombre, exc)
        log.info("se cerraron %d paso(s) al soltar la cámara", len(pasos))

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
            Persona(
                id=p["id"], nombre=p.get("displayName", ""), vectores=p.get("embeddings", []),
                tiene_acceso=bool(p.get("hasAccess", True)),
            )
            for p in datos
            if p.get("embeddings")
        ]
        self._acceso_de = {p.id: p.tiene_acceso for p in personas}
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
            # Y con la galería, lo que se dedujo de ella. Una baja tiene que
            # llevarse el nombre que quedó anclado a un seguimiento en curso:
            # si no, a quien se borra mientras está frente a la cámara se le
            # sigue mostrando el nombre hasta que se vaya del cuadro.
            if self._sostenida is not None:
                olvidados = self._sostenida.conservar_solo({p.id for p in personas})
                if olvidados:
                    log.info(
                        "%d identidad(es) sostenida(s) se olvidaron: ya no están dadas de alta",
                        olvidados,
                    )
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
        self._ajustar_detector(h, w)

        # 1. Cuerpos con identidad de seguimiento. Es lo que permite sostener
        #    quién es alguien cuando se da vuelta.
        cuerpos: list[tuple[int, tuple[float, float, float, float]]] = []
        for r in self._yolo.track(
            frame.image, classes=[0], conf=0.35, persist=True,
            tracker="bytetrack.yaml", verbose=False, device="cpu",
            imgsz=self._cuerpo_lado,
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

        self._permanencia.ver([t for t, _ in cuerpos], frame.captured_at)

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
        # Quiénes están en el cuadro EN ESTE FRAME. No es lo mismo que tener un
        # paso abierto: el paso tolera hasta minuto y medio de ausencia para no
        # partir una visita en pedazos, y la vista en vivo no puede tolerar
        # nada. Son dos preguntas distintas y necesitan dos respuestas.
        en_cuadro: dict[str, tuple[str, bool]] = {}
        # Quién es cada cuerpo que se ve, y por qué vía se supo. Se llena en los
        # dos caminos —por cara y por continuidad— porque la pantalla tiene que
        # poder marcar también a quien está de espaldas, que es la mitad de la
        # jornada.
        quien_es: dict[int, tuple[str, str, str]] = {}
        # Dónde está exactamente la cara de cada cuerpo. Es lo que la pantalla
        # dibuja: un recuadro sobre la cara y no sobre el cuerpo entero, que
        # incluye la pared, el escritorio y medio compañero de al lado.
        caras: dict[int, tuple[float, float, float, float]] = {}
        # Por qué no se reconoció a quien no se reconoció. Viaja hasta la
        # pantalla: "sin identificar" a secas deja al operador sin saber si el
        # módulo está roto, si la persona está de espaldas o si está tan lejos
        # que no hay nada que se pueda hacer desde el software.
        motivos: dict[int, str] = {}

        # 3. Las caras reconocidas ANCLAN su identidad al cuerpo que las lleva.
        for res in identificaciones:
            idx = asociar_a_cuerpo(res.rostro, cajas_cuerpo)
            if idx is not None:
                caras[cuerpos[idx][0]] = (
                    res.rostro.x, res.rostro.y, res.rostro.w, res.rostro.h,
                )
                if not res.persona_id:
                    # Todavía no es nadie: se guarda su mejor foto para que el
                    # operador pueda darle un nombre desde la pantalla, y se
                    # anota por qué no se lo reconoció.
                    motivos[cuerpos[idx][0]] = res.motivo
                    self._recordar_para_nombrar(frame.image, cuerpos[idx][0], res.rostro)

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
                en_cuadro[res.persona_id] = (res.nombre or "", True)
                quien_es[tid] = (res.persona_id, res.nombre or "", "rostro")
                detecciones += self._registrar_paso(
                    res.persona_id, res.nombre or "", caja,
                    ahora=frame.captured_at, parecido=res.parecido, por_rostro=True,
                )
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
                quien_es[tid] = (r.persona_id, r.nombre or "", r.via)
                if r.persona_id not in en_cuadro:
                    en_cuadro[r.persona_id] = (r.nombre or "", False)
                detecciones += self._registrar_paso(
                    r.persona_id, r.nombre or "", caja,
                    ahora=frame.captured_at, parecido=0.0, por_rostro=False,
                )
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

        # Los pasos que terminaron se emiten para que queden registrados.
        for paso in self._pasos.cerrar_vencidos(frame.captured_at):
            detecciones.append(self._a_deteccion_paso(paso))

        # Lo que va a dibujar la pantalla: cada persona con el recuadro de su
        # cara y, si se sabe, quién es. Se rehace entero en cada frame en vez de
        # acumularse: un recuadro que sobrevive al frame en el que se vio es una
        # marca verde pegada a una pared vacía.
        self._en_vivo = self._armar_en_vivo(cuerpos, caras, quien_es, motivos, frame.captured_at)
        self._en_vivo_ts = frame.captured_at
        self._olvidar_fotos_que_ya_no_hacen_falta(quien_es, {t for t, _ in cuerpos})

        # Y en cada frame, quién está en el cuadro ahora. Va SIEMPRE, aunque no
        # haya nadie: es lo que hace que la lista se vacíe apenas se van, en vez
        # de esperar a que algo venza.
        detecciones.append(Detection(
            class_label="access.presence", class_id=0, confidence=1.0,
            bbox=(0.0, 0.0, 1.0, 1.0),
            attributes={
                "kind": "telemetry",
                "serie": "presence",
                "presentes": json.dumps([
                    {
                        "personId": pid,
                        "displayName": nombre,
                        "seenByFace": por_rostro,
                        "hasAccess": self._acceso_de.get(pid, True),
                        "desde": (self._pasos.desde_de(pid) or frame.captured_at),
                    }
                    for pid, (nombre, por_rostro) in en_cuadro.items()
                ]),
            },
        ))

        return InferenceResult(detections=detecciones, inference_ms=elapsed_ms)

    def _ajustar_detector(self, alto: int, ancho: int) -> None:
        """Le da al detector de caras la forma del video, no un cuadrado.

        El detector recibe la imagen encajada en un lienzo del tamaño que se le
        haya pedido, rellenando con negro lo que sobra. Pidiéndole 640×640 para
        un video 16:9, el 44% de lo que procesa es relleno: la imagen entra
        como 640×360 y las 280 filas de abajo son negras. Se paga tiempo de CPU
        por mirar nada.

        Ajustando el lienzo a la proporción del video —640×384 para 16:9— la
        imagen entra EXACTAMENTE igual de grande: la escala la fija el lado
        largo, que no cambia. Se detectan las mismas caras, del mismo tamaño
        mínimo, con la misma confianza; lo único que desaparece es el relleno.
        Medido sobre este modelo: 319 ms el cuadrado, 196 ms la proporción real.

        Se hace acá y no al cargar porque la proporción es del video, y el
        módulo no la conoce hasta que llega el primer cuadro. Se recalcula sólo
        cuando cambia.
        """
        if self._app is None or self._det_forma == (alto, ancho):
            return
        # Se redondea PARA ARRIBA al múltiplo de 32 que pide la red. Para abajo
        # el lienzo quedaría más chico que la imagen encajada y el detector la
        # achicaría un poco más, que es justo lo que no se quiere: la escala
        # tiene que quedar igual que con el cuadrado. Medido, además, 640×352
        # sale más lento que 640×384 pese a ser más chico.
        lado = self._det_lado
        if ancho >= alto:
            w = lado
            h = max(32, math.ceil(lado * alto / ancho / 32) * 32)
        else:
            h = lado
            w = max(32, math.ceil(lado * ancho / alto / 32) * 32)
        modelo = getattr(self._app, "det_model", None)
        if modelo is not None:
            # (ancho, alto): es el orden que espera el detector.
            modelo.input_size = (w, h)
        self._det_forma = (alto, ancho)
        log.info("detector de caras ajustado a %dx%d para un video de %dx%d", w, h, ancho, alto)

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

    # ── control de accesos ──────────────────────────────────────────
    def _registrar_paso(
        self, persona_id: str, nombre: str, caja: tuple[float, float, float, float],
        ahora: float, parecido: float, por_rostro: bool,
    ) -> list[Detection]:
        """Anota que se vio a esta persona y alerta si no tiene acceso."""
        acceso = self._acceso_de.get(persona_id, True)
        paso = self._pasos.ver(
            persona_id=persona_id, nombre=nombre, ahora=ahora,
            parecido=parecido, por_rostro=por_rostro, tiene_acceso=acceso,
        )

        salida: list[Detection] = []
        # El paso se persiste mientras ocurre: así el registro muestra quién
        # está adentro ahora, no sólo quién estuvo.
        if self._pasos.toca_reportar(paso, ahora):
            salida.append(self._a_deteccion_paso(paso))

        if not self._pasos.debe_alertar(paso, ahora, tiene_acceso=acceso):
            return salida

        self._alertas_acceso += 1
        log.warning("ACCESO DENEGADO: %s está en el cuadro y no tiene acceso", nombre)
        salida.append(Detection(
            class_label="access.denied", class_id=0,
            confidence=max(paso.mejor_parecido, 0.5), bbox=caja,
            attributes={
                "kind": "alert",
                # El módulo ya decidió: no se le pide persistencia extra ni se
                # la retiene por confianza. Una alerta de acceso denegado que
                # llega tarde no sirve para nada.
                "confirmed": "true",
                "personId": persona_id,
                "personName": nombre,
                "reason": "la persona no tiene acceso a este lugar",
                "vistoPorRostro": "true" if paso.visto_por_rostro else "false",
            },
        ))
        return salida

    def _a_deteccion_paso(self, paso) -> Detection:
        """Un paso terminado, listo para el registro de accesos.

        Es telemetría y no alerta: saber que Juan entró a las nueve no tiene por
        qué ocupar a un operador. Lo que sí entra en su cola es el acceso
        denegado, y es una sola alerta por visita.
        """
        return Detection(
            class_label="access.sighting", class_id=0, confidence=1.0,
            bbox=(0.0, 0.0, 1.0, 1.0),
            attributes={
                "kind": "telemetry",
                "serie": "sighting",
                "personId": paso.persona_id,
                "personName": paso.nombre,
                "from": f"{paso.desde:.3f}",
                "to": f"{paso.hasta:.3f}",
                "bestScore": f"{paso.mejor_parecido:.3f}",
                "seenByFace": "true" if paso.visto_por_rostro else "false",
                "hadAccess": "true" if paso.tenia_acceso else "false",
            },
        )


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

    def _armar_en_vivo(
        self,
        cuerpos: list[tuple[int, tuple[float, float, float, float]]],
        caras: dict[int, tuple[float, float, float, float]],
        quien_es: dict[int, tuple[str, str, str]],
        motivos: dict[int, str],
        ahora: float,
    ) -> list[dict]:
        """Lo que se ve en la cámara ahora mismo, listo para dibujar.

        Va todo el mundo, tenga nombre o no: alguien sin identificar igual está
        ahí y la pantalla tiene que poder mostrarlo —y dejar que le pongan un
        nombre—. Lo que cambia es que sin identidad no hay acceso que informar
        ni hora de llegada que contar.
        """
        salida: list[dict] = []
        for tid, caja in cuerpos:
            persona_id, nombre, via = quien_es.get(tid, ("", "", "ninguna"))
            motivo = motivos.get(tid, "")
            tiene_acceso: bool | None = None
            if persona_id:
                ficha = self._ident.galeria.por_id(persona_id) if self._ident else None
                if ficha is None:
                    # Hay un identificador que la galería ya no conoce: se dio
                    # de baja a esa persona, o se le borraron todas las fotos.
                    # No se muestra el nombre. Sostenerlo dejaría la pantalla
                    # diciendo quién es alguien y a la vez que no sabe si puede
                    # estar ahí, que es contradecirse a la vista de todos.
                    persona_id, nombre, via = "", "", "ninguna"
                    motivo = "esa persona ya no está dada de alta"
                else:
                    tiene_acceso = bool(ficha.tiene_acceso)
            desde = self._pasos.desde_de(persona_id) if persona_id else None

            # El recuadro va sobre la cara. Cuando en este frame no se le ve
            # —está de espaldas, o mirando su pantalla— se marca dónde está la
            # cabeza y se dice que es una estimación, para que la pantalla lo
            # dibuje distinto: un recuadro punteado sobre una nuca es honesto,
            # uno lleno diría que se le está viendo la cara.
            cara = caras.get(tid)
            estimada = cara is None
            if cara is None:
                cara = cabeza_estimada(caja)

            # Acá va sólo SI HAY foto, no la foto. La imagen sale del módulo
            # cuando alguien la pide para nombrar a esa persona —ver
            # `en_vivo`—, y no en cada cuadro para todo el que mire la pantalla.
            hay_foto = not persona_id and tid in self._para_nombrar

            salida.append({
                "trackId": tid,
                "personId": persona_id,
                "nombre": nombre,
                # None y False no son lo mismo: None es "no sé quién es", False
                # es "sé quién es y no tendría que estar acá".
                "tieneAcceso": tiene_acceso,
                # Por qué vía se sabe quién es. La pantalla lo muestra: decir
                # "Juan" cuando en realidad se dedujo por el puesto y no por
                # haberle visto la cara es esconder de dónde sale el nombre.
                "via": via,
                # Los dos instantes en que empezó a contar, en epoch. Van
                # como marcas de tiempo y no como "hace tantos segundos" para
                # que el cronómetro de la pantalla corra con el reloj del
                # navegador: un número calculado acá llega tan seguido como
                # cuadros procese la cámara —dos por segundo—, y un contador
                # que salta de a medio segundo no es un contador.
                #
                # `desdeTs` es la visita del registro de accesos: tolera que la
                # persona se tape o salga un momento sin volver a arrancar.
                "desdeTs": round(desde, 3) if desde else None,
                # `enCuadroDesdeTs` es este cuerpo en el cuadro, se sepa o no
                # quién es. Es lo único que hay para cronometrar a un
                # desconocido, y es exacto desde el primer frame en que aparece.
                "enCuadroDesdeTs": round(self._permanencia.desde_de(tid, ahora), 3),
                "bbox": [round(v, 4) for v in caja],
                "rostro": [round(v, 4) for v in cara],
                "rostroEstimado": estimada,
                # Si se le puede poner un nombre ahora mismo. La foto en sí
                # no viaja hasta que alguien la pide.
                "hayFoto": hay_foto,
                # Por qué no se lo reconoció, cuando no se lo reconoció.
                # Vacío si no se le vio la cara en este frame: ahí el motivo es
                # justamente ése y la pantalla ya lo sabe por `rostroEstimado`.
                "motivo": "" if persona_id else motivo,
            })
        return salida

    def _recordar_para_nombrar(self, imagen: np.ndarray, track_id: int, r: Rostro) -> None:
        """Guarda la mejor foto de un cuerpo sin identificar, para poder nombrarlo.

        Mismo listón que para preguntar en la cola de revisión, y por las mismas
        dos razones: el recorte tiene que poder reconocerlo una persona, y ese
        vector va a ser la PRIMERA plantilla de quien se dé de alta. Una nuca no
        sirve para ninguna de las dos cosas.

        Se queda con la mejor y no con la última: la cara mejora cuando la
        persona levanta la vista, y sería una lástima pisarla con el frame
        siguiente, en el que ya volvió a mirar su pantalla.
        """
        if self._ident is None:
            return
        cfg = self._ident.cfg
        if r.calidad < cfg.askMinScore or r.h < cfg.askMinFaceSize:
            return
        if r.yaw is None or r.pitch is None:
            return
        if abs(r.yaw) > cfg.askMaxYaw or abs(r.pitch) > cfg.askMaxPitch:
            return

        guardada = self._para_nombrar.get(track_id)
        if guardada is not None and guardada["calidad"] >= r.calidad:
            return
        recorte = self._recorte(imagen, r)
        if not recorte:
            return
        self._para_nombrar[track_id] = {
            "foto": recorte,
            "vector": _empaquetar(r.vector),
            "calidad": float(r.calidad),
        }

    def _olvidar_fotos_que_ya_no_hacen_falta(
        self, quien_es: dict[int, tuple[str, str, str]], presentes: set[int]
    ) -> None:
        """Borra las caras de quien se fue del cuadro o ya dejó de ser un desconocido.

        Es la contracara de guardarlas: existen mientras sirven para contestar
        "¿quién es este?" y desaparecen en cuanto la pregunta deja de tener
        sentido. Sin esto, el módulo iría juntando las caras de todos los que
        pasaron en el día, que es exactamente lo que no hace.
        """
        self._para_nombrar = {
            t: v for t, v in self._para_nombrar.items()
            if t in presentes and not quien_es.get(t, ("", "", ""))[0]
        }

    def en_vivo(self, nombrar: int | None = None) -> dict:
        """Lo último que vio la cámara. Lo consume la pantalla del dashboard.

        La cara de un desconocido viaja SÓLO si se pide con `nombrar`, y sólo
        la de ese cuerpo. Mandarla siempre significaría que la imagen de alguien
        que no está dado de alta sale del proceso varias veces por segundo, para
        todo el que tenga la pantalla abierta, la mire o no. Acá sale cuando un
        operador tocó a esa persona para ponerle un nombre, que es el único
        momento en que hace falta —el mismo criterio que usa el reconocimiento
        a pedido desde una alerta—.
        """
        personas = self._en_vivo
        if nombrar is not None:
            guardada = self._para_nombrar.get(nombrar)
            if guardada is not None:
                personas = [
                    ({**p, "foto": guardada["foto"], "vector": guardada["vector"]}
                     if p["trackId"] == nombrar else p)
                    for p in personas
                ]
        return {
            "ts": self._en_vivo_ts,
            # El reloj del worker AHORA, al contestar, no cuando se procesó el
            # cuadro. Es lo que deja que el cronómetro de la pantalla sea
            # exacto sin comparar relojes de dos máquinas: la resta
            # `ahora - desdeTs` se hace entera acá, con un solo reloj, y el
            # navegador nada más la sigue contando.
            #
            # Con `ts` no alcanzaba: entre que se captura un cuadro y se
            # termina de analizarlo pasan segundos —los modelos corren en CPU—,
            # y contar desde ahí daba un cronómetro atrasado por ese tanto.
            "ahora": time.time(),
            "personas": personas,
        }

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
            "carasListasParaNombrar": len(self._para_nombrar),
            "cuerposCronometrados": len(self._permanencia),
            "alertasDeAccesoDenegado": self._alertas_acceso,
            **self._pasos.estado(),
            # Lo que sostiene la identidad cuando la cara no se ve: a cuánta
            # gente se le conoce el puesto es la respuesta a "¿va a seguir
            # sabiendo quién es cuando se dé vuelta?".
            **(self._sostenida.estado() if self._sostenida else {}),
            "galeriaActualizadaHace": (
                round(time.time() - self._ultima_galeria, 1) if self._ultima_galeria else None
            ),
            "loadedAt": self._loaded_at or None,
        }


def cabeza_estimada(
    caja: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Dónde está la cabeza de un cuerpo al que no se le ve la cara.

    No es una detección: es la parte de arriba del cuerpo, que es donde la
    cabeza está siempre. Se usa para poder marcar igual a quien está de
    espaldas —la mitad de la jornada en una oficina— sin dibujarle encima el
    cuerpo entero. La pantalla lo distingue de una cara detectada de verdad.

    Las proporciones salen del cuerpo y no de un tamaño fijo: alguien cerca de
    la cámara tiene una caja grande y una cabeza grande.
    """
    x, y, w, h = caja
    alto = h * 0.24
    # Una cara es más alta que ancha. El tope por el ancho del cuerpo evita que
    # un cuerpo recortado por el borde del cuadro produzca una cabeza enorme.
    ancho = min(w * 0.6, alto * 0.8)
    return (x + w / 2 - ancho / 2, y + h * 0.02, ancho, alto)


def _empaquetar(vector: list[float]) -> str:
    """Vector a base64, para que viaje con la alerta sin inflar el JSON."""
    return base64.b64encode(np.asarray(vector, dtype=np.float32).tobytes()).decode("ascii")


MODULE_CLASS = PersonIdentificationModule
