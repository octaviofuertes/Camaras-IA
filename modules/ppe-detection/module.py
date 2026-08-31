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

import json
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

from reglas import (  # noqa: E402
    ELEMENTOS,
    POR_CLAVE,
    ConfigEpp,
    VigiladorEpp,
    calibracion,
    de_quien_es,
    evaluar_cuadro,
)

log = logging.getLogger("ppe-detection")

#: Dónde queda el modelo entrenado, relativo a la raíz del repositorio.
PESOS_POR_OMISION = "training/models/epp.pt"

#: Cómo se llama la persona en el modelo entrenado.
CLASE_PERSONA = "Person"


def _medido(pesos: Path) -> dict[str, float]:
    """Los umbrales que midió `training/ppe/umbral.py` para ESTE modelo.

    Vive al lado del .pt, en `epp.json`, porque es una propiedad del modelo y no
    de la cámara: cuando se reentrena y se vuelve a medir, todas las cámaras
    quedan bien calibradas sin que nadie edite nada.

    Si no está el archivo se devuelve vacío, y eso significa "no se midió
    nada". La consecuencia es deliberada: sin medición no se alerta. Un módulo
    que avisa con umbrales inventados acusa gente por nada, y es exactamente lo
    que pasaba antes de esto.
    """
    ficha = pesos.with_suffix(".json")
    if not ficha.is_file():
        log.warning(
            "no hay mediciones en %s: no se va a alertar de nada hasta correr "
            "python training/ppe/umbral.py", ficha,
        )
        return {}
    try:
        datos = json.loads(ficha.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("no se pudo leer %s: %r", ficha, exc)
        return {}
    medidos = (datos.get("umbrales") or {}).get("porElemento") or {}
    return {str(k): float(v) for k, v in medidos.items()}


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
        # Lo último que vio la cámara, para dibujarlo en pantalla. Vive sólo en
        # memoria y se pisa en cada cuadro: es el presente, no un registro.
        self._en_vivo: list[dict] = []
        self._en_vivo_personas: list[dict] = []
        self._en_vivo_ts: float = 0.0
        # A quién le toca la segunda mirada. Ver abajo por qué es de a uno.
        self._turno = 0
        self._segundas = 0
        self._segunda_activa = True

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

        exigidos = cfg.get("exigidos") or ["casco", "chaleco", "guantes"]
        desconocidos = [e for e in exigidos if e not in POR_CLAVE]
        if desconocidos:
            # Se avisa y se sigue con los que sí existen: dejar la cámara sin
            # vigilar por un nombre mal escrito es peor que vigilar de menos.
            log.error(
                "elementos desconocidos en la configuración: %s (los válidos son %s)",
                desconocidos, list(POR_CLAVE),
            )
            exigidos = [e for e in exigidos if e in POR_CLAVE]

        # Los umbrales salen de lo MEDIDO sobre este modelo, no de lo que quedó
        # guardado en la cámara cuando se asignó el módulo. Esa config se
        # escribe una vez y no se vuelve a tocar: cuando después se midió que el
        # casco no estaba para alertar, las cámaras siguieron avisando igual.
        # Se puede pisar por cámara, para el caso raro de un encuadre propio.
        umbrales, callados = calibracion(tuple(exigidos), _medido(ruta))
        if cfg.get("umbralPorElemento"):
            umbrales = {k: float(v) for k, v in cfg["umbralPorElemento"].items()}
        if cfg.get("sinAlertar") is not None and cfg.get("sinAlertar") != []:
            callados = tuple(cfg["sinAlertar"])
        if callados:
            log.warning(
                "sin medición suficiente para alertar de: %s. Se detectan y se "
                "dibujan, pero no generan eventos. Corré python training/ppe/umbral.py",
                ", ".join(callados),
            )

        self._vigilador = VigiladorEpp(ConfigEpp(
            exigidos=tuple(exigidos),
            minConfianza=float(cfg.get("minConfianza", 0.45)),
            minConfianzaFalta=float(cfg.get("minConfianzaFalta", 0.45)),
            umbralPorElemento=umbrales,
            sinAlertar=callados,
            verificarPosicion=bool(cfg.get("verificarPosicion", True)),
            solapeMinimo=float(cfg.get("solapeMinimo", 0.55)),
            framesSeguidos=int(cfg.get("framesSeguidos", 4)),
            repetirSegundos=float(cfg.get("repetirSegundos", 120.0)),
        ))
        # Cuesta alrededor de un 77% más de CPU por cuadro. Vale la pena
        # —sin ella el de atrás no recibe veredicto— pero en una máquina justa
        # se apaga y el módulo sigue andando con el cuadro entero.
        self._segunda_activa = bool(cfg.get("segundaMirada", True))
        self._cargado_en = time.time()
        log.info(
            "EPP cargado desde %s; se exige: %s; se alerta de: %s",
            ruta.name, ", ".join(exigidos) or "nada",
            ", ".join(k for k in exigidos if k not in callados) or "nada todavía",
        )

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

        # Segunda mirada, de a una persona por cuadro.
        #
        # En una escena real la gente está a distintas distancias: el que está
        # cerca ocupa media pantalla y el del fondo, cincuenta píxeles. Mirando
        # el cuadro entero el modelo resuelve al de adelante y se pierde a los
        # de atrás — se ve como "detecta a uno solo de los tres".
        #
        # Recortar a la persona y ampliarla le da al de atrás el mismo tamaño
        # que al de adelante. Medido sobre el split de prueba: las detecciones
        # de "sin chaleco" pasan de 38 a 45, y cada persona recibe un veredicto
        # propio en vez de depender de que el modelo la vea entre las demás.
        #
        # Va de a UNA por cuadro y no todas, porque cada recorte es otra
        # inferencia: con cuatro personas serían cinco pasadas por cuadro y el
        # pipeline no llegaría. Como el EPP no cambia de un cuadro al otro,
        # turnarse alcanza: con tres personas cada una se revisa una vez por
        # segundo, y el estado se sostiene entre medio.
        if personas and self._segunda_activa:
            elementos += self._segunda_mirada(frame.image, personas)

        # Lo que va a dibujar la pantalla: cada elemento con su nombre en
        # castellano y si está puesto o falta. Va TODO lo detectado, no sólo lo
        # que genera alerta: ver "casco" verde sobre alguien es lo que le dice
        # al operador que el módulo está mirando y funcionando. Sin eso, un
        # módulo que no alerta y uno que está roto se ven exactamente igual.
        self._en_vivo, self._en_vivo_personas = self._armar_en_vivo(personas, ids, elementos)
        self._en_vivo_ts = frame.captured_at

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

    def _segunda_mirada(
        self,
        imagen: Any,
        personas: list[tuple[float, float, float, float]],
    ) -> list[tuple[str, tuple[float, float, float, float], float]]:
        """Mira de cerca a una persona y devuelve lo que encuentre, en
        coordenadas del cuadro completo."""
        if self._modelo is None:
            return []
        h, w = imagen.shape[:2]
        self._turno = (self._turno + 1) % len(personas)
        px, py, pw, ph = personas[self._turno]

        # Un poco de margen: un casco asoma por arriba de la caja del cuerpo, y
        # sin margen se recorta justo lo que se quiere ver.
        margen = 0.12 * pw
        x1 = max(0, int((px - margen) * w))
        y1 = max(0, int((py - margen) * h))
        x2 = min(w, int((px + pw + margen) * w))
        y2 = min(h, int((py + ph + margen) * h))
        if x2 - x1 < 32 or y2 - y1 < 32:
            return []

        recorte = imagen[y1:y2, x1:x2]
        try:
            r = self._modelo.predict(
                recorte, verbose=False, device="cpu", imgsz=384, conf=0.25,
            )[0]
        except Exception as exc:  # noqa: BLE001
            log.error("falló la segunda mirada: %r", exc)
            return []
        self._segundas += 1

        salida: list[tuple[str, tuple[float, float, float, float], float]] = []
        for b in getattr(r, "boxes", []) or []:
            clase = self._nombres.get(int(b.cls.item()), "")
            if clase == CLASE_PERSONA:
                continue
            bx1, by1, bx2, by2 = (float(v) for v in b.xyxy[0].tolist())
            # De coordenadas del recorte a las del cuadro completo. Si esto se
            # equivocara, las cajas aparecerían corridas y se le atribuirían a
            # la persona de al lado.
            salida.append((
                clase,
                (
                    (x1 + bx1) / w,
                    (y1 + by1) / h,
                    (bx2 - bx1) / w,
                    (by2 - by1) / h,
                ),
                float(b.conf.item()),
            ))
        return salida

    def _armar_en_vivo(
        self,
        personas: list[tuple[float, float, float, float]],
        ids: list[int],
        elementos: list[tuple[str, tuple[float, float, float, float], float]],
    ) -> tuple[list[dict], list[dict]]:
        """Lo que hay que dibujar: los elementos y el estado de cada persona.

        Se descartan las detecciones por debajo de `minConfianza`, la misma vara
        con la que se decide alertar. Antes se dibujaba todo lo que saliera del
        detector (que corre a 0,25) y eso pintaba de rojo a gente con el casco
        puesto: una caja floja de "NO-Hardhat" no alcanza para avisar, así que
        tampoco tiene por qué alcanzar para acusar en pantalla.
        """
        cfg = self._vigilador.cfg if self._vigilador else ConfigEpp()

        dibujables = [(c, caja, conf) for c, caja, conf in elementos
                      if conf >= cfg.minConfianza]

        salida: list[dict] = []
        for clase, caja, conf in dibujables:
            elem = next((e for e in ELEMENTOS if clase in (e.puesto, e.falta)), None)
            if elem is None:
                continue
            salida.append({
                "clave": elem.clave,
                "nombre": elem.nombre,
                # True = lo tiene puesto; False = se ve que le falta.
                "tiene": clase == elem.puesto,
                # Si en esta cámara no se exige, se dibuja igual pero apagado:
                # sirve para ver qué hay sin que parezca que va a alertar.
                "exigido": elem.clave in cfg.exigidos,
                "conf": round(conf, 3),
                "bbox": [round(v, 4) for v in caja],
                "persona": de_quien_es(caja, personas, cfg.solapeMinimo),
            })

        # El estado por persona se manda aparte y con la caja del cuerpo, no con
        # un índice. El índice no servía: la pantalla numera a las personas con
        # las que detecta el módulo de ingreso, que es otro modelo y las
        # encuentra en otro orden, así que el "le falta el casco" del EPP caía
        # sobre la persona equivocada apenas había más de una en cuadro.
        sabido = evaluar_cuadro(personas, dibujables, cfg, solo_exigidos=False)
        gente: list[dict] = []
        for i, caja in enumerate(personas):
            de_esta = sabido.get(i, {})
            estado: dict[str, str] = {}
            for clave in cfg.exigidos:
                dato = de_esta.get(clave)
                # Tres estados y no dos: "no se sabe" es la respuesta honesta
                # cuando la persona está de espaldas o el detector no vio nada,
                # y mostrarla como "le falta" sería acusar por no haber visto.
                estado[clave] = "no_se_sabe" if dato is None else ("tiene" if dato[0] else "falta")
            gente.append({
                "trackId": ids[i] if i < len(ids) else -1,
                "bbox": [round(v, 4) for v in caja],
                "estado": estado,
            })
        return salida, gente

    def en_vivo(self) -> dict:
        """Lo último que vio la cámara. Lo consume la vista ampliada."""
        return {
            "ts": self._en_vivo_ts,
            "exigidos": list(self._vigilador.cfg.exigidos) if self._vigilador else [],
            # De qué se vigila pero todavía no se avisa, porque el modelo no
            # distingue esa ausencia con precisión suficiente. Viaja hasta la
            # pantalla a propósito: sin decirlo, un elemento que nunca aparece
            # se lee como "está roto", cuando en realidad está callado por
            # honestidad.
            "sinAlertar": list(self._vigilador.cfg.sinAlertar) if self._vigilador else [],
            "elementos": self._en_vivo,
            "personas": self._en_vivo_personas,
        }

    # ── diagnóstico ──────────────────────────────────────────────────
    def health(self) -> dict[str, Any]:
        return {
            "ok": self._modelo is not None,
            "pesos": self._pesos,
            "clasesDelModelo": [self._nombres[i] for i in sorted(self._nombres)],
            "seExige": list(self._vigilador.cfg.exigidos) if self._vigilador else [],
            "seAlerta": [k for k in (self._vigilador.cfg.exigidos if self._vigilador else ())
                         if k not in (self._vigilador.cfg.sinAlertar if self._vigilador else ())],
            "umbralesMedidos": dict(self._vigilador.cfg.umbralPorElemento) if self._vigilador else {},
            "cuadrosProcesados": self._cuadros,
            "segundasMiradas": self._segundas,
            "personasVistas": self._personas_vistas,
            "faltasAvisadas": self._faltas,
            **(self._vigilador.estado() if self._vigilador else {}),
            "loadedAt": self._cargado_en,
        }


MODULE_CLASS = PpeDetectionModule
