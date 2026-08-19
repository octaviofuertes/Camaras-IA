"""Sostener la identidad cuando la cara no se ve. Sin dependencias del motor.

EL PROBLEMA
-----------
En una oficina la gente pasa la mayor parte del día sentada y de espaldas. Un
sistema que sólo identifica por rostro diría "sin identificar" durante casi toda
la jornada, y el informe no serviría para nada.

LAS CUATRO VÍAS, DE MÁS FIRME A MENOS
-------------------------------------
1. ROSTRO. La única definitiva. Cuando alguien mira a la cámara, se sabe quién
   es y se ANCLA esa identidad al seguimiento en curso.
2. SEGUIMIENTO. Mientras el tracker no pierda a la persona, la identidad
   anclada la acompaña aunque se dé vuelta. Es la vía que cubre el día normal.
3. APARIENCIA. Si el seguimiento se corta —alguien pasa por delante, la persona
   sale y vuelve— se la reengancha por cómo se ve: ropa, colores, proporciones.
4. PUESTO. Si además vuelve al mismo lugar donde se la había identificado, eso
   refuerza. Nunca alcanza sola: dos personas pueden compartir escritorio.

POR QUÉ LA APARIENCIA VENCE AL FINAL DEL DÍA
--------------------------------------------
Porque mañana viene con otra ropa, y una firma de ayer aplicada a hoy
identificaría mal a alguien con la misma remera. El vencimiento no es una
limitación: es lo que hace que esta firma sea un dato efímero de trabajo y no
un rasgo biométrico permanente de la persona.

QUÉ SE REPORTA
--------------
Cada identificación dice POR QUÉ vía se resolvió. Un informe que muestra
"Juan: 6 h" sin decir que cuatro de esas horas salen de continuidad y no de
haberle visto la cara está escondiendo de dónde sale su propio número.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class ConfigContinuidad:
    """Parámetros de las vías que no son el rostro."""

    # Parecido mínimo de apariencia para reenganchar a alguien tras perder el
    # seguimiento. Alto a propósito: confundir a dos personas acá le atribuye a
    # una el tiempo de la otra, que es el error que nadie detecta.
    aparienciaThreshold: float = 0.80
    # Ventaja mínima sobre el segundo candidato. Con uniformes o ropa parecida,
    # sin este margen el sistema elegiría a cualquiera de los dos.
    aparienciaMargin: float = 0.06

    # Cuánto vive una firma de apariencia. Ocho horas cubren una jornada; al día
    # siguiente la persona viene con otra ropa y la firma vieja sólo puede
    # equivocarse.
    aparienciaHoras: float = 8.0

    # Cuánto se conserva una identidad anclada a un seguimiento después de que
    # el tracker deja de reportarlo. Cubre una oclusión breve —alguien que pasa
    # por delante— sin heredarle la identidad al próximo que ocupe ese id.
    trackGraciaSegundos: float = 5.0

    # Distancia máxima (en anchos de imagen) para considerar que alguien está
    # "en el mismo puesto" donde se le vio la cara.
    puestoRadio: float = 0.12
    # Cuánto vale un puesto sin volver a verle la cara a nadie ahí. Cubre una
    # jornada: quien se sentó a la mañana sigue siendo el de ese escritorio a
    # la tarde. Al día siguiente hay que volver a verle la cara.
    puestoHoras: float = 8.0
    # Confianza de una identificación resuelta SÓLO por puesto. Baja a
    # propósito: es la vía más débil y el informe la muestra.
    puestoConfianza: float = 0.62
    # Cuánto puede desaparecer alguien del cuadro sin que el puesto deje de
    # valer para identificarlo.
    #
    # Es LA salvaguarda de esta vía. Sostiene el caso real —se sentó, se le vio
    # la cara, se dio vuelta y sigue ahí— y corta el caso peligroso: Juan se fue,
    # el escritorio quedó vacío un rato y se sentó otro. Lo que los distingue no
    # es la posición, que es idéntica, sino el hueco. Sin esto, cualquiera que se
    # siente en el puesto de Juan hereda su nombre y su tiempo con el teléfono.
    puestoContinuidadSegundos: float = 30.0


@dataclass
class Anclaje:
    """Una identidad confirmada por rostro y sostenida por otras vías."""
    persona_id: str
    nombre: str
    # Firmas de apariencia vistas hoy para esta persona. Varias porque alguien
    # se saca el saco a media mañana y sigue siendo la misma persona.
    apariencias: list[tuple[list[float], float]] = field(default_factory=list)
    # Dónde se le vio la CARA por última vez: su puesto de trabajo.
    #
    # Sólo lo mueve `anclar_por_rostro`. Antes lo pisaba cualquier resolución,
    # así que alguien que se levantaba a buscar algo dejaba su "puesto" junto a
    # la impresora y volver a su escritorio ya no lo reconocía. El puesto es
    # dónde se sabe que estuvo esa persona, no dónde estaba el último cuerpo.
    puesto: tuple[float, float] | None = None
    puesto_visto: float = 0.0
    visto_por_rostro: float = 0.0
    ultima_vez: float = 0.0


@dataclass
class Resolucion:
    """A quién corresponde un cuerpo, y por qué vía se supo."""
    persona_id: str | None
    nombre: str | None
    via: str          # rostro | seguimiento | apariencia | puesto | ninguna
    confianza: float


class IdentidadSostenida:
    """Mantiene quién es quién cuando la cara deja de verse."""

    def __init__(self, cfg: ConfigContinuidad | None = None) -> None:
        self.cfg = cfg or ConfigContinuidad()
        # track_id -> (persona_id, ultima_vez)
        self._por_track: dict[int, tuple[str, float]] = {}
        # persona_id -> Anclaje
        self._anclajes: dict[str, Anclaje] = {}

    # ── anclar ──────────────────────────────────────────────────────
    def anclar_por_rostro(
        self,
        track_id: int,
        persona_id: str,
        nombre: str,
        apariencia: list[float] | None,
        posicion: tuple[float, float] | None,
        ahora: float,
    ) -> None:
        """Se le vio la cara: a partir de acá la identidad viaja con el track."""
        self._por_track[track_id] = (persona_id, ahora)

        a = self._anclajes.get(persona_id)
        if a is None:
            a = Anclaje(persona_id=persona_id, nombre=nombre)
            self._anclajes[persona_id] = a
        a.nombre = nombre or a.nombre
        a.visto_por_rostro = ahora
        a.ultima_vez = ahora
        if posicion is not None:
            a.puesto = posicion
            a.puesto_visto = ahora
        if apariencia:
            a.apariencias.append((apariencia, ahora))
            # Se conservan unas pocas firmas recientes: alcanzan para cubrir un
            # cambio de abrigo sin volver laxa la comparación.
            if len(a.apariencias) > 5:
                del a.apariencias[0]

    # ── resolver ────────────────────────────────────────────────────
    def resolver(
        self,
        track_id: int,
        apariencia: list[float] | None,
        posicion: tuple[float, float] | None,
        ahora: float,
    ) -> Resolucion:
        """Quién es este cuerpo, sin haberle visto la cara en este frame."""
        self._olvidar_vencidos(ahora)
        cfg = self.cfg

        # 1. El seguimiento no se cortó: sigue siendo la misma persona.
        anclado = self._por_track.get(track_id)
        if anclado is not None:
            persona_id, visto = anclado
            if ahora - visto <= cfg.trackGraciaSegundos:
                a = self._anclajes.get(persona_id)
                if a is not None:
                    self._por_track[track_id] = (persona_id, ahora)
                    a.ultima_vez = ahora
                    return Resolucion(persona_id, a.nombre, "seguimiento", 0.95)
            else:
                # El id se reutiliza: sin esto, el próximo que reciba este
                # número heredaría la identidad del anterior.
                del self._por_track[track_id]

        # 2. Reenganche por apariencia.
        if apariencia:
            mejor, parecido, segundo = self._mejor_por_apariencia(apariencia, ahora)
            margen = parecido - segundo if segundo > 0 else 1.0
            if (
                mejor is not None
                and parecido >= cfg.aparienciaThreshold
                and margen >= cfg.aparienciaMargin
            ):
                # El puesto refuerza pero no es requisito: alguien puede
                # levantarse a buscar algo y volver.
                via = "apariencia"
                confianza = min(parecido, 0.90)
                if posicion is not None and mejor.puesto is not None:
                    if _cerca(posicion, mejor.puesto, cfg.puestoRadio):
                        via = "puesto"
                        confianza = min(parecido + 0.05, 0.92)

                self._por_track[track_id] = (mejor.persona_id, ahora)
                mejor.ultima_vez = ahora
                return Resolucion(mejor.persona_id, mejor.nombre, via, confianza)

        # 3. El puesto, solo. Es la vía que cubre a quien se sentó de espaldas y
        #    ya no se le va a ver la cara ni la ropa igual que cuando llegó:
        #    basta con haberle visto la cara UNA vez en ese escritorio.
        #
        #    Sólo resuelve si hay UN candidato en ese lugar. Dos personas pueden
        #    compartir escritorio, y ahí atribuirle el tiempo a cualquiera de las
        #    dos sería inventar — se prefiere "sin identificar", que es visible.
        if posicion is not None:
            cerca = [
                a for a in self._anclajes.values()
                if a.puesto is not None   # `_olvidar_vencidos` ya anuló los de ayer
                # Sin interrupción: si desapareció del cuadro, ya no se puede
                # afirmar que el cuerpo que hay ahora en ese puesto sea el suyo.
                and ahora - a.ultima_vez <= cfg.puestoContinuidadSegundos
                and _cerca(posicion, a.puesto, cfg.puestoRadio)
            ]
            if len(cerca) == 1:
                a = cerca[0]
                self._por_track[track_id] = (a.persona_id, ahora)
                a.ultima_vez = ahora
                return Resolucion(a.persona_id, a.nombre, "puesto", cfg.puestoConfianza)

        return Resolucion(None, None, "ninguna", 0.0)

    def _mejor_por_apariencia(
        self, apariencia: list[float], ahora: float
    ) -> tuple[Anclaje | None, float, float]:
        mejor: tuple[Anclaje | None, float] = (None, -1.0)
        segundo = -1.0
        for a in self._anclajes.values():
            suyo = max((_coseno(apariencia, v) for v, _ in a.apariencias), default=-1.0)
            if suyo > mejor[1]:
                segundo = mejor[1]
                mejor = (a, suyo)
            elif suyo > segundo:
                segundo = suyo
        return mejor[0], mejor[1], segundo

    def _olvidar_vencidos(self, ahora: float) -> None:
        """Descarta firmas de apariencia viejas y anclajes que quedaron sin ellas.

        Es lo que impide que la ropa de ayer identifique a alguien hoy.
        """
        limite = self.cfg.aparienciaHoras * 3600.0
        limite_puesto = self.cfg.puestoHoras * 3600.0
        vacios = []
        for pid, a in self._anclajes.items():
            a.apariencias = [(v, t) for v, t in a.apariencias if ahora - t <= limite]
            # El puesto vence por su cuenta: mañana el escritorio puede ser de
            # otro, y heredarle la identidad al que se siente ahí sería el peor
            # error posible de este módulo.
            if a.puesto is not None and ahora - a.puesto_visto > limite_puesto:
                a.puesto = None
            if not a.apariencias and a.puesto is None and ahora - a.ultima_vez > limite:
                vacios.append(pid)
        for pid in vacios:
            del self._anclajes[pid]

        gracia = self.cfg.trackGraciaSegundos
        self._por_track = {
            t: (p, v) for t, (p, v) in self._por_track.items() if ahora - v <= gracia * 4
        }

    # ── diagnóstico ─────────────────────────────────────────────────
    def estado(self) -> dict:
        return {
            "identidadesSostenidas": len(self._anclajes),
            "seguimientosAnclados": len(self._por_track),
            "firmasDeApariencia": sum(len(a.apariencias) for a in self._anclajes.values()),
            "puestosConocidos": sum(1 for a in self._anclajes.values() if a.puesto is not None),
        }


def firma_apariencia(histograma: list[float]) -> list[float]:
    """Normaliza un descriptor de apariencia para poder compararlo por coseno."""
    n = math.sqrt(sum(x * x for x in histograma))
    if n <= 0:
        return []
    return [x / n for x in histograma]


def _coseno(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return -1.0
    num = na = nb = 0.0
    for x, y in zip(a, b):
        num += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return -1.0
    return num / math.sqrt(na * nb)


def _cerca(a: tuple[float, float], b: tuple[float, float], radio: float) -> bool:
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= radio
