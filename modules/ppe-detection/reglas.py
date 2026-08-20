"""Qué elemento de protección le falta a cada persona, y cuándo avisarlo.

Acá no hay modelo ni imágenes: entran cajas ya detectadas y sale la decisión.
Está separado del resto porque es lo único que decide si alguien recibe una
alerta sobre otra persona, y eso tiene que poder leerse y probarse solo.

── La decisión de fondo: no ver algo no es verlo ausente ───────────────────

Un detector de EPP se puede armar de dos maneras. La fácil es: "busco cascos;
si en la cabeza de alguien no encontré uno, le falta el casco". Esa es la que
llena de alertas falsas al operador, porque confunde tres cosas distintas:

  - la persona no tiene casco          → hay que avisar
  - la persona está de espaldas        → no se sabe
  - el modelo no lo detectó esta vez   → no se sabe

Las dos últimas son la mayoría de los cuadros de una jornada real. Un sistema
que las trata como la primera avisa todo el tiempo, el operador deja de
mirarlo, y a la semana el módulo está apagado.

Por eso este módulo se apoya en que el dataset trae la ausencia ANOTADA: hay
una clase `NO-Hardhat` que marca cabezas sin casco. Se avisa cuando el modelo
VIO que falta, no cuando no encontró nada. Si no hay evidencia en ninguno de
los dos sentidos, la respuesta es "no se sabe" y no pasa nada.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Caja normalizada: x, y, ancho, alto en fracciones de la imagen.
Caja = tuple[float, float, float, float]


@dataclass(frozen=True)
class Elemento:
    """Un tipo de EPP, con cómo lo nombra el modelo y cómo se avisa."""

    clave: str
    #: Cómo se lo llama en pantalla.
    nombre: str
    #: Clase del modelo que dice "lo tiene puesto".
    puesto: str
    #: Clase del modelo que dice "no lo tiene puesto".
    falta: str
    #: Tipo de evento con el que sale la alerta.
    evento: str


#: Los cuatro que se pueden vigilar. El orden es el de la severidad con la que
#: se los suele exigir: sin casco es lo más grave que se ve desde una cámara.
ELEMENTOS: tuple[Elemento, ...] = (
    Elemento("casco", "casco", "Hardhat", "NO-Hardhat", "ppe.helmet_missing"),
    Elemento("chaleco", "chaleco", "Safety Vest", "NO-Safety Vest", "ppe.vest_missing"),
    Elemento("antiparras", "antiparras", "Goggles", "NO-Goggles", "ppe.goggles_missing"),
    Elemento("guantes", "guantes", "Gloves", "NO-Gloves", "ppe.gloves_missing"),
)

POR_CLAVE = {e.clave: e for e in ELEMENTOS}


@dataclass
class ConfigEpp:
    """Cómo se comporta el módulo en una cámara.

    Los exigidos se configuran POR CÁMARA porque el EPP obligatorio depende del
    lugar: en un obrador se exige casco y chaleco, en un laboratorio antiparras
    y guantes, y en una oficina nada. Un valor global obligaría a elegir el
    mínimo común y el módulo no serviría en ningún lado.
    """

    exigidos: tuple[str, ...] = ("casco", "chaleco")
    #: Confianza mínima para creerle a una caja de EPP.
    minConfianza: float = 0.45
    #: Cuánto de la caja del elemento tiene que caer dentro de la persona para
    #: considerarlo suyo.
    solapeMinimo: float = 0.55
    #: Cuadros seguidos con la falta antes de avisar.
    framesSeguidos: int = 4
    #: Cuánto esperar antes de volver a avisar por la misma persona.
    repetirSegundos: float = 120.0


@dataclass
class _EstadoPersona:
    """Lo que se recuerda de un cuerpo entre cuadro y cuadro."""

    seguidos: dict[str, int] = field(default_factory=dict)
    #: Cuándo se avisó por última vez. Ausente = nunca, que NO es lo mismo que
    #: "hace muchísimo": tratarlo como el instante cero hacía que el primer
    #: aviso quedara silenciado si el reloj arrancaba cerca de cero.
    ultimo_aviso: dict[str, float] = field(default_factory=dict)
    visto: float = 0.0


def solape(elemento: Caja, persona: Caja) -> float:
    """Qué fracción del elemento cae dentro de la persona.

    Se mide contra el área del ELEMENTO y no contra la unión: un casco es
    diminuto al lado de un cuerpo, así que cualquier medida que divida por la
    unión da casi cero y nunca los asociaría.
    """
    ex, ey, ew, eh = elemento
    px, py, pw, ph = persona
    if ew <= 0 or eh <= 0:
        return 0.0
    ix = max(0.0, min(ex + ew, px + pw) - max(ex, px))
    iy = max(0.0, min(ey + eh, py + ph) - max(ey, py))
    return (ix * iy) / (ew * eh)


def de_quien_es(elemento: Caja, personas: list[Caja], minimo: float) -> int | None:
    """A qué persona pertenece un elemento. None si no se sabe.

    Se elige el mayor solape y no el primero que alcance el mínimo: con dos
    personas juntas, el casco de una cae parcialmente sobre la otra, y quedarse
    con la primera de la lista le atribuiría el casco a quien no lo tiene —lo
    que además dejaría a la otra marcada como sin casco.
    """
    mejor, mejor_valor = None, minimo
    for i, p in enumerate(personas):
        v = solape(elemento, p)
        if v >= mejor_valor:
            mejor, mejor_valor = i, v
    return mejor


@dataclass(frozen=True)
class Falta:
    """Una persona a la que le falta un elemento exigido."""

    indice_persona: int
    elemento: Elemento
    confianza: float
    caja_persona: Caja


def evaluar_cuadro(
    personas: list[Caja],
    detecciones: list[tuple[str, Caja, float]],
    cfg: ConfigEpp,
) -> dict[int, dict[str, tuple[bool, float]]]:
    """Qué se sabe de cada persona en ESTE cuadro.

    Devuelve, por persona y por elemento exigido: (lo_tiene, confianza).
    Un elemento que no aparece en el resultado es "no se sabe", y eso es
    distinto de "no lo tiene": el que no se sabe no genera nada.
    """
    salida: dict[int, dict[str, tuple[bool, float]]] = {}
    for clase, caja, conf in detecciones:
        if conf < cfg.minConfianza:
            continue
        elemento = next(
            (e for e in ELEMENTOS if clase in (e.puesto, e.falta) and e.clave in cfg.exigidos),
            None,
        )
        if elemento is None:
            continue
        quien = de_quien_es(caja, personas, cfg.solapeMinimo)
        if quien is None:
            continue
        lo_tiene = clase == elemento.puesto
        previo = salida.setdefault(quien, {}).get(elemento.clave)
        # Ante dos detecciones del mismo elemento para la misma persona manda la
        # más confiable, y ante empate, la que dice que SÍ lo tiene: acusar de
        # una falta pide más evidencia que descartarla.
        if previo is None or conf > previo[1] or (conf == previo[1] and lo_tiene):
            salida[quien][elemento.clave] = (lo_tiene, conf)
    return salida


class VigiladorEpp:
    """Sostiene la decisión entre cuadros: persistencia y no repetir.

    La persistencia existe porque un detector se equivoca en cuadros sueltos, y
    una alerta por un solo cuadro es una alerta por un parpadeo. El no repetir
    existe porque la misma persona sin casco sigue sin casco el minuto
    siguiente, y avisarlo sesenta veces es la forma más rápida de que alguien
    apague el módulo.
    """

    def __init__(self, cfg: ConfigEpp | None = None) -> None:
        self.cfg = cfg or ConfigEpp()
        self._estado: dict[int, _EstadoPersona] = {}

    def ver(
        self,
        personas: list[Caja],
        detecciones: list[tuple[str, Caja, float]],
        ahora: float,
        ids: list[int] | None = None,
    ) -> list[Falta]:
        """Procesa un cuadro y devuelve las faltas que hay que avisar ahora.

        `ids` son identificadores de seguimiento. Sin ellos se usa la posición
        en la lista, que sirve para una prueba pero no para una cámara: si dos
        personas se cruzan, sus índices se intercambian y la cuenta de cuadros
        seguidos pasa de una a la otra.
        """
        cfg = self.cfg
        claves = ids if ids is not None else list(range(len(personas)))
        conocido = evaluar_cuadro(personas, detecciones, cfg)

        faltas: list[Falta] = []
        for i, caja in enumerate(personas):
            pid = claves[i] if i < len(claves) else i
            est = self._estado.setdefault(pid, _EstadoPersona())
            est.visto = ahora
            sabido = conocido.get(i, {})

            for clave in cfg.exigidos:
                elemento = POR_CLAVE.get(clave)
                if elemento is None:
                    continue
                dato = sabido.get(clave)
                if dato is None:
                    # No se sabe: no se acumula ni se reinicia. Que la persona
                    # se dé vuelta un segundo no puede borrar lo que se venía
                    # viendo, ni contar como si le faltara.
                    continue
                lo_tiene, conf = dato
                if lo_tiene:
                    est.seguidos[clave] = 0
                    continue

                est.seguidos[clave] = est.seguidos.get(clave, 0) + 1
                if est.seguidos[clave] < cfg.framesSeguidos:
                    continue
                ultimo = est.ultimo_aviso.get(clave)
                if ultimo is not None and ahora - ultimo < cfg.repetirSegundos:
                    continue
                est.ultimo_aviso[clave] = ahora
                faltas.append(Falta(i, elemento, conf, caja))

        self._olvidar_viejos(ahora)
        return faltas

    def _olvidar_viejos(self, ahora: float, vencimiento: float = 300.0) -> None:
        """Suelta a quien ya no está. Sin esto la memoria crece toda la jornada."""
        for pid in [p for p, e in self._estado.items() if ahora - e.visto > vencimiento]:
            self._estado.pop(pid, None)

    def estado(self) -> dict:
        return {
            "personasSeguidas": len(self._estado),
            "elementosExigidos": list(self.cfg.exigidos),
        }
