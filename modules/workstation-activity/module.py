"""Módulo de IA: actividad por puesto de trabajo.

Mide cuánto tiempo cada puesto está ocupado, vacío, y con alguien usando el
teléfono. NO genera alertas: alimenta el apartado de Informes.

DOS DECISIONES QUE DEFINEN ESTE MÓDULO
--------------------------------------
1. NO SIGUE A LAS PERSONAS. No usa el tracker y no maneja identificadores por
   persona. Cuenta ocupación de una REGIÓN. Eso no es una simplificación: es lo
   que hace que el informe hable de puestos y no de individuos, y que no exista
   ningún dato que permita reconstruir quién estuvo dónde.

2. NO EMITE EVENTOS DE ALERTA. Las detecciones que produce llevan
   `attributes["kind"] = "telemetry"`, y el pipeline las manda a analytics-service
   en vez de crear un evento. Una medición no es una alerta: mezclarlas llenaría
   la cola de revisión con datos que nadie tiene que atender.

La lógica de contabilidad vive en `actividad.py`, sin dependencias de YOLO, y
está cubierta por pruebas. Acá sólo se hace el puente.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import fields
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

sys.path.insert(0, str(Path(__file__).parent))
from actividad import (  # noqa: E402
    Caja,
    ConfigActividad,
    ContadorActividad,
    MuestraZona,
    Observacion,
    Zona,
)

log = logging.getLogger(__name__)

# Clases COCO que le interesan al módulo.
CLASE_PERSONA = 0
CLASE_TELEFONO = 67


class WorkstationActivityModule(PerceptaModule):
    def __init__(self) -> None:
        self._model: Any = None
        self._ctx: ModuleContext | None = None
        self._contador: ContadorActividad | None = None
        self._imgsz = 640
        # Resolución a la que se mira el recorte de cada persona buscando el
        # teléfono. Es lo que fija cuántos píxeles tiene el objeto.
        self._phone_imgsz = 320
        # Cuánto se ensancha el recorte a cada lado, en anchos de cuerpo. Un
        # brazo estirado sale bastante del recuadro de la persona.
        self._phone_crop_margin = 0.35
        self._telefonos_vistos = 0
        self._loaded_at = 0.0
        self._muestras_emitidas = 0
        # Identidades que dejó `person-identification` en este frame.
        self._identidades_frame: list = []

    def load(self, ctx: ModuleContext) -> None:
        from ultralytics import YOLO  # import perezoso

        self._ctx = ctx
        weights = str(ctx.config.get("weights", "yolov8n.pt"))
        self._imgsz = int(ctx.config.get("imgsz", 640))
        self._phone_imgsz = int(ctx.config.get("phoneImgsz", 320))
        self._phone_crop_margin = float(ctx.config.get("phoneCropMargin", 0.35))

        cfg = self._config_validada(ctx.config)
        self._contador = ContadorActividad(self._zonas_de(ctx), cfg)

        log.info(
            "cargando %s en %s — %d puesto(s), ventana de %.0f s",
            weights, ctx.device, len(self._contador.zonas), cfg.windowSeconds,
        )
        self._model = YOLO(weights)
        self._model.to("cpu" if ctx.device.startswith("cpu") else ctx.device)
        self._loaded_at = time.time()

    def observar_contexto(self, detecciones: list) -> None:
        """Recibe quién es cada persona, del módulo de identificación.

        Sin esto el informe repartiría el tiempo de teléfono entre todos los
        presentes, y quien no lo usó quedaría igual de mal que quien sí. El
        manifiesto declara esa dependencia con `requires`, así que el pipeline
        garantiza que este contexto llegó antes de llamar a `infer`.
        """
        self._identidades_frame = [
            d for d in detecciones
            if d.attributes.get("kind") == "identity" and d.attributes.get("personId")
        ]

    def _zonas_de(self, ctx: ModuleContext) -> list[Zona]:
        """Traduce las zonas de la cámara a puestos de trabajo.

        Sin zonas configuradas el módulo mide la cámara entera como un puesto:
        tiene que servir apenas se lo asigna, sin obligar a dibujar polígonos.
        Dibujarlos lo mejora —permite separar puestos dentro de una misma
        cámara— pero no es un requisito para empezar.
        """
        zonas = []
        for zid, poligono in (ctx.zones or {}).items():
            zonas.append(
                Zona(
                    id=str(zid),
                    nombre=str(zid),
                    poligono=[(float(x), float(y)) for x, y in poligono],
                )
            )
        return zonas

    def _config_validada(self, config: dict) -> ConfigActividad:
        """Aplica la configuración respetando los rangos de `config.schema.json`.

        Mismo criterio que el módulo de caídas: los valores fuera de rango se
        acotan y se avisa, en vez de tumbar el pipeline de esa cámara por un
        número mal guardado.
        """
        esquema = {}
        ruta = Path(__file__).parent / "config.schema.json"
        try:
            esquema = json.loads(ruta.read_text(encoding="utf-8")).get("properties", {})
        except Exception:  # noqa: BLE001
            log.warning("no se pudo leer %s: se aplica sin validar", ruta.name)

        cfg = ConfigActividad()
        propios = {f.name for f in fields(ConfigActividad)}
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
        if self._model is None:
            return
        dummy = np.zeros((self._imgsz, self._imgsz, 3), dtype=np.uint8)
        self._model.predict(dummy, imgsz=self._imgsz, verbose=False, device="cpu")

    def infer(self, frame: Frame) -> InferenceResult:
        if self._model is None or self._contador is None:
            raise RuntimeError("el módulo no fue cargado (falta load())")

        t0 = time.perf_counter()
        # `predict` y no `track`: el módulo cuenta ocupación de regiones, no
        # personas individuales. No pedirle identidad al detector es más barato
        # y, sobre todo, es lo que hace que no exista el dato que permitiría
        # reconstruir quién estuvo en qué puesto.
        resultados = self._model.predict(
            frame.image,
            imgsz=self._imgsz,
            classes=[CLASE_PERSONA],
            conf=0.20,   # el filtro fino lo aplica la contabilidad, por clase
            verbose=False,
            device="cpu",
        )

        h, w = frame.image.shape[:2]
        personas: list[Caja] = []

        for r in resultados:
            cajas = getattr(r, "boxes", None)
            if cajas is None:
                continue
            for b in cajas:
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
                personas.append(Caja(
                    x=x1 / w, y=y1 / h, w=(x2 - x1) / w, h=(y2 - y1) / h,
                    confianza=float(b.conf.item()),
                ))

        telefonos = self._buscar_telefonos(frame.image, personas)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        muestras = self._contador.observar(
            Observacion(
                ts=frame.captured_at,
                personas=personas,
                telefonos=telefonos,
                identidades=[self._quien_es(p) for p in personas],
            )
        )
        self._muestras_emitidas += len(muestras)

        detecciones = [self._a_deteccion(m) for m in muestras]
        # Además del puesto, el tiempo atribuido a cada persona. Van como
        # mediciones distintas porque responden preguntas distintas: una dice
        # cuánto se usó una posición de trabajo, la otra qué hizo alguien.
        if muestras:
            detecciones += [self._a_deteccion_persona(mp) for mp in self._contador.ultimas_personas]

        return InferenceResult(detections=detecciones, inference_ms=elapsed_ms)

    def _a_deteccion_persona(self, m) -> Detection:
        return Detection(
            class_label="workstation.person",
            class_id=0,
            confidence=1.0,
            bbox=(0.0, 0.0, 1.0, 1.0),
            attributes={
                "kind": "telemetry",
                # Marca a qué serie pertenece: el pipeline la manda al endpoint
                # de personas y no al de puestos.
                "serie": "person",
                "zoneId": m.zona_id,
                "zoneName": m.zona_nombre,
                "personId": m.persona_id or "",
                "personName": m.nombre,
                "from": f"{m.desde:.3f}",
                "to": f"{m.hasta:.3f}",
                "presentSeconds": f"{m.presente_s:.2f}",
                "phoneSeconds": f"{m.telefono_s:.2f}",
            },
        )

    def _buscar_telefonos(self, imagen, personas: list[Caja]) -> list[Caja]:
        """Busca el teléfono DENTRO del recorte de cada persona, no en el cuadro.

        Un teléfono mide alrededor de una doceava parte del alto de una persona.
        Buscándolo en el cuadro entero, su tamaño depende de a qué distancia esté
        esa persona de la cámara: medido acá, 28 px para alguien cerca y 15 px
        para alguien al fondo. Quince píxeles no los detecta nadie, y por eso el
        informe decía 0 s de teléfono siempre, para todo el mundo.

        Recortando a cada persona y llevando ESE recorte a `phoneImgsz`, el
        teléfono queda en unos 33 px sin importar la distancia. Cuesta lo mismo
        que procesar el cuadro entero al doble de resolución, que igual dejaría a
        la persona del fondo por debajo del límite.

        El recorte abarca del pecho a la cintura y se ensancha para que entren
        los brazos: es donde está un teléfono que alguien está mirando.
        """
        if self._model is None:
            return []

        alto, ancho = imagen.shape[:2]
        # NO se reusa `phoneMargin`: ese dice cuán lejos del cuerpo puede estar
        # un teléfono para contárselo a esa persona (0.12), y es una decisión de
        # atribución. Esto otro es cuánto hay que ensanchar el recorte para que
        # entren los brazos estirados, que es geometría del cuerpo.
        margen = self._phone_crop_margin
        imgsz = self._phone_imgsz
        salida: list[Caja] = []

        for p in personas:
            x1, y1 = int(p.x * ancho), int(p.y * alto)
            pw, ph = int(p.w * ancho), int(p.h * alto)
            cx1 = max(int(x1 - pw * margen), 0)
            cx2 = min(int(x1 + pw * (1 + margen)), ancho)
            cy1 = max(int(y1 - ph * 0.05), 0)
            cy2 = min(int(y1 + ph * 0.80), alto)
            if cx2 - cx1 < 16 or cy2 - cy1 < 16:
                continue

            recorte = imagen[cy1:cy2, cx1:cx2]
            try:
                res = self._model.predict(
                    recorte, imgsz=imgsz, classes=[CLASE_TELEFONO],
                    conf=0.10, verbose=False, device="cpu",
                )
            except Exception:  # noqa: BLE001
                log.exception("no se pudo buscar el teléfono en un recorte")
                continue

            for r in res:
                for b in (getattr(r, "boxes", None) or []):
                    bx1, by1, bx2, by2 = (float(v) for v in b.xyxy[0].tolist())
                    # De coordenadas del recorte a coordenadas del cuadro.
                    salida.append(Caja(
                        x=(cx1 + bx1) / ancho, y=(cy1 + by1) / alto,
                        w=(bx2 - bx1) / ancho, h=(by2 - by1) / alto,
                        confianza=float(b.conf.item()),
                    ))
        self._telefonos_vistos += len(salida)
        return salida

    def _quien_es(self, persona: Caja) -> tuple[str, str] | None:
        """Empareja un cuerpo detectado acá con una identidad del módulo anterior.

        Se emparejan por superposición de recuadros: los dos módulos miran el
        mismo frame pero corren su propio detector, así que las cajas son
        parecidas y no idénticas. Se exige una superposición alta para no
        atribuirle a alguien la identidad de quien tiene al lado.
        """
        mejor, mejor_iou = None, 0.0
        for d in self._identidades_frame:
            iou = _superposicion((persona.x, persona.y, persona.w, persona.h), d.bbox)
            if iou > mejor_iou:
                mejor, mejor_iou = d, iou
        if mejor is None or mejor_iou < 0.45:
            return None
        return (mejor.attributes["personId"], mejor.attributes.get("personName", ""))

    def _a_deteccion(self, m: MuestraZona) -> Detection:
        """Traduce una ventana cerrada al contrato de detección.

        `kind=telemetry` es lo que le dice al pipeline que esto NO es una alerta:
        se persiste como medición y nunca entra en la cola de revisión.
        """
        return Detection(
            class_label="workstation.activity",
            class_id=0,
            confidence=1.0,   # es una medición, no una inferencia con duda
            bbox=(0.0, 0.0, 1.0, 1.0),
            attributes={
                "kind": "telemetry",
                "zoneId": m.zona_id,
                "zoneName": m.zona_nombre,
                "from": f"{m.desde:.3f}",
                "to": f"{m.hasta:.3f}",
                "occupiedSeconds": f"{m.ocupado_s:.2f}",
                "phoneSeconds": f"{m.telefono_s:.2f}",
                "emptySeconds": f"{m.vacio_s:.2f}",
                "uncoveredSeconds": f"{m.sin_cobertura_s:.2f}",
                "maxPeople": str(m.max_personas),
                "meanOccupancy": f"{m.ocupacion_media:.2f}",
            },
        )

    def health(self) -> dict[str, Any]:
        return {
            "ok": self._model is not None,
            "model": "yolov8n" if self._model is not None else None,
            "device": self._ctx.device if self._ctx else None,
            "muestrasEmitidas": self._muestras_emitidas,
            # Sin esto, "el informe dice 0 s de teléfono" no se distingue de
            # "el detector no ve ningún teléfono", que se arreglan distinto.
            "telefonosDetectados": self._telefonos_vistos,
            "resolucionDelRecorte": self._phone_imgsz,
            "enCurso": self._contador.estado() if self._contador else {},
            "loadedAt": self._loaded_at or None,
        }

    def release(self) -> None:
        # Se cierra la ventana en curso antes de soltar: si no, el último tramo
        # observado desaparece y en el informe queda un hueco inexplicable.
        if self._contador is not None:
            pendientes = self._contador.cerrar_pendiente(time.time())
            if pendientes:
                log.info("se cierran %d muestra(s) pendientes al liberar", len(pendientes))
        self._model = None
        self._contador = None


def _superposicion(a: tuple, b: tuple) -> float:
    """Intersección sobre unión de dos recuadros normalizados."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


MODULE_CLASS = WorkstationActivityModule
