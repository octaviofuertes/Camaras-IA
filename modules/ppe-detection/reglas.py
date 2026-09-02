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

── Y sin embargo, lo que NO aparece también dice algo ──────────────────────

Pedirle a una sola caja de `NO-Hardhat` toda la evidencia dejaba al módulo casi
mudo: para que 7 de cada 10 alertas fueran correctas había que exigirle tanta
confianza que se veían 2 de cada 10 faltas reales.

Lo que faltaba era mirar el contexto. El modelo encuentra el casco PUESTO mucho
mejor de lo que encuentra una cabeza descubierta, así que "a esta persona no le
vi ningún casco por ninguna parte" no es lo mismo que no haber mirado: es un
dato. Una caja floja de ausencia sobre alguien a quien además se le encontró un
casco es casi siempre un error; la misma caja sobre alguien a quien no se le
encontró ninguno, no.

Eso es `umbralCorroborado`, y NO afloja la regla de arriba: sigue haciendo
falta una caja de `NO-*` para avisar. Lo único que cambia es cuánta confianza
se le pide según haya o no algo que la contradiga. Los dos números salen de
medir el veredicto por persona (`training/ppe/evaluar_personas.py`), no de
razonar cuál suena prudente.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

#: Caja normalizada: x, y, ancho, alto en fracciones de la imagen.
Caja = tuple[float, float, float, float]

# ── Cómo se mira el cuadro ──────────────────────────────────────────────
#
# Estos tres números viven acá, y no en `module.py`, porque los usan dos lados
# que TIENEN que coincidir: la cámara y la calibración
# (`training/ppe/evaluar_personas.py`). Los umbrales se eligen midiendo con
# estos valores; si la cámara mirara con otros, las confianzas no significarían
# lo mismo y los umbrales medidos dejarían de valer. Ya pasó: el módulo entraba
# a 640 —el valor por omisión de ultralytics— mientras todo se medía a 512.

#: Tamaño de entrada del cuadro completo. Es el mismo con el que se entrenó.
IMGSZ: int = 512
#: Tamaño de entrada del recorte de una persona. Más chico porque ya viene
#: acotado, y así la segunda mirada cuesta menos.
IMGSZ_RECORTE: int = 384
#: Cada cuántos cuadros se mira de cerca cuando la máquina no da abasto.
#: Con 3 y un cuadro por segundo, a cada persona de una escena de tres le toca
#: su turno cada nueve segundos: tarde para un movimiento, pero de sobra para
#: un casco, que no aparece y desaparece. Bajarlo devuelve precisión y saca
#: fluidez a los recuadros; subirlo, al revés.
MIRAR_CADA: int = 3

#: Piso de confianza del detector. Por debajo no se junta nada, así que ningún
#: umbral puede estar más abajo. Bajarlo no mejoró nada medido: sólo agrega
#: cajas flojas que tapan la corroboración.
PISO_DEL_DETECTOR: float = 0.25


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
    #: Dónde va este elemento en el cuerpo, como fracción del alto de la
    #: persona: (desde, hasta) medido desde la cabeza. Un casco arriba, unas
    #: botas abajo. Sale de medir el dataset (`training/ppe/verificar.py`), con
    #: margen para encuadres raros.
    banda: tuple[float, float]


#: Los cuatro que se pueden vigilar. El orden es el de la severidad con la que
#: se los suele exigir: sin casco es lo más grave que se ve desde una cámara.
ELEMENTOS: tuple[Elemento, ...] = (
    Elemento("casco", "casco", "Hardhat", "NO-Hardhat", "ppe.helmet_missing", (0.0, 0.45)),
    Elemento("chaleco", "chaleco", "Safety Vest", "NO-Safety Vest", "ppe.vest_missing", (0.05, 0.80)),
    Elemento("antiparras", "antiparras", "Goggles", "NO-Goggles", "ppe.goggles_missing", (0.0, 0.50)),
    Elemento("guantes", "guantes", "Gloves", "NO-Gloves", "ppe.gloves_missing", (0.10, 1.0)),
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

    exigidos: tuple[str, ...] = ("casco", "chaleco", "guantes")
    #: Confianza mínima para creerle a una caja de EPP.
    minConfianza: float = 0.45
    #: Confianza mínima para creerle a una caja que dice que el elemento FALTA.
    #:
    #: Los dos errores no cuestan lo mismo: pasar por alto un casco puesto no le
    #: hace nada a nadie, y decir que alguien no lo tiene cuando sí lo tiene es
    #: acusarlo delante de su jefe de algo que no hizo. Por eso la ausencia se
    #: mira aparte de la presencia.
    #:
    #: Pero el número NO se elige a ojo. Se puso 0,60 razonando "más evidencia
    #: para acusar", y medido sobre el split de prueba ese valor descartaba 18
    #: de cada 19 faltas detectadas: el módulo dejaba de avisar casi siempre. La
    #: confianza no significa lo mismo en todas las clases —el modelo está
    #: seguro de un casco y mucho menos de una cabeza descubierta— así que sale
    #: de medir: `training/ppe/evaluar_personas.py --calibrar`. Este valor es
    #: sólo el que se usa mientras no haya medición, y sin medición no se
    #: alerta, así que en una cámara andando nunca decide nada.
    minConfianzaFalta: float = 0.45
    #: Umbral propio de cada elemento, cuando se lo midió. Lo que no esté acá
    #: usa `minConfianzaFalta`.
    #:
    #: Existe porque un solo número para los cuatro obliga a elegir entre no
    #: avisar de lo que el modelo sí ve bien, o avisar de más con lo que ve mal.
    umbralPorElemento: dict[str, float] = field(default_factory=dict)
    #: Umbral, más bajo, para la ausencia que ADEMÁS no tiene nada que la
    #: contradiga: el detector no le vio ese elemento a esa persona por ningún
    #: lado. Lo llena `training/ppe/evaluar_personas.py`.
    #:
    #: Existe porque el umbral de arriba, solo, dejaba al módulo casi mudo. Con
    #: el modelo actual, para que 7 de cada 10 alertas de casco sean correctas
    #: hay que pedirle 0,67 de confianza a un "NO-Hardhat", y a esa altura se
    #: ven 15 de cada 100 cabezas descubiertas: el módulo mira todo el día y
    #: avisa una vez.
    #:
    #: Lo que cambia el número es el contexto. Una caja floja de "sin casco"
    #: sobre alguien a quien el modelo TAMBIÉN le encontró un casco es
    #: probablemente un error; la misma caja floja sobre alguien a quien no le
    #: encontró ningún casco por ninguna parte es otra cosa. El modelo acierta
    #: el casco puesto mucho mejor que la cabeza descubierta (mAP50 0,85 contra
    #: 0,28), así que "no le vi ninguno" es evidencia de verdad y no ausencia
    #: de evidencia.
    #:
    #: Sigue haciendo falta que el modelo VEA la ausencia: sin una caja de
    #: `NO-*` no se avisa nada, por más que no aparezca el elemento. La
    #: diferencia con "no lo encontré, entonces falta" es justo esa.
    umbralCorroborado: dict[str, float] = field(default_factory=dict)
    #: Elementos que NO se alertan aunque estén en `exigidos`.
    #:
    #: Sirve para el caso real de un modelo que ve bien unas cosas y mal otras:
    #: se sigue dibujando lo que detecta —que es información útil en pantalla—
    #: sin mandar a Eventos alertas que serían mayormente falsas.
    sinAlertar: tuple[str, ...] = ()
    #: Verificar que el elemento caiga donde va en el cuerpo.
    verificarPosicion: bool = True
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
        # `>= minimo` para entrar y `> mejor_valor` para desplazar al que ya
        # estaba: con `>=` en las dos, dos personas con el mismo solape —el caso
        # de una tapando a la otra, donde el casco cae entero dentro de ambas—
        # se resolvía por el orden de la lista, que cambia entre cuadros.
        if v >= minimo and (mejor is None or v > mejor_valor):
            mejor, mejor_valor = i, v
    return mejor


def en_su_lugar(elemento: Elemento, caja: Caja, persona: Caja) -> bool:
    """¿La caja cae donde va esa parte del cuerpo?

    Un casco tiene que estar en la cabeza. Si el modelo pone un "sin casco" a
    la altura de los pies, se equivocó — y sin este filtro esa equivocación se
    convierte en una alerta que acusa a alguien.

    Se mide el centro de la caja contra la altura de la persona, así que
    funciona igual con alguien lejos o cerca de la cámara. La banda es ancha
    porque un encuadre picado corre todo hacia abajo; lo que se descarta son
    los disparates, no los casos raros.
    """
    _px, py, _pw, ph = persona
    if ph <= 0:
        return False
    _ex, ey, _ew, eh = caja
    centro = (ey + eh / 2 - py) / ph
    desde, hasta = elemento.banda
    return desde - 0.08 <= centro <= hasta + 0.08


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
    solo_exigidos: bool = True,
) -> dict[int, dict[str, tuple[bool, float]]]:
    """Qué se sabe de cada persona en ESTE cuadro.

    Devuelve, por persona y por elemento exigido: (lo_tiene, confianza).
    Un elemento que no aparece en el resultado es "no se sabe", y eso es
    distinto de "no lo tiene": el que no se sabe no genera nada.

    Con `solo_exigidos=False` se miran todos los elementos y no sólo los
    obligatorios. Eso NO sirve para decidir alertas —lo que no se exige no se
    alerta— pero sí para dibujar: la pantalla muestra lo que la cámara ve, y
    ocultar un casco detectado porque en esa cámara no es obligatorio deja al
    operador sin saber si el módulo está mirando o está roto.
    """
    evidencia = evidencia_por_persona(personas, detecciones, cfg, solo_exigidos)
    salida: dict[int, dict[str, tuple[bool, float]]] = {}
    for quien, elementos in evidencia.items():
        for clave, ev in elementos.items():
            veredicto = decidir(clave, ev.get("tiene"), ev.get("falta"), cfg)
            if veredicto is not None:
                salida.setdefault(quien, {})[clave] = veredicto
    return salida


def evidencia_por_persona(
    personas: list[Caja],
    detecciones: list[tuple[str, Caja, float]],
    cfg: ConfigEpp,
    solo_exigidos: bool = True,
) -> dict[int, dict[str, dict[str, float]]]:
    """La evidencia cruda de cada persona: {persona: {elemento: {tiene, falta}}}.

    Cada valor es la confianza de la mejor detección de ese signo. Está separado
    de la decisión porque para elegir cuánta confianza pedirle a una ausencia
    hay que saber antes si algo la contradice, y eso puede venir en una
    detección posterior —así que primero se junta todo y después se decide.

    Separarlo también es lo que permite barrer umbrales sin volver a correr el
    modelo: `training/ppe/evaluar_personas.py` extrae esta evidencia una vez y
    prueba miles de combinaciones contra `decidir`, que es la función de verdad
    y no una copia suya.
    """
    evidencia: dict[int, dict[str, dict[str, float]]] = {}
    for clase, caja, conf in detecciones:
        elemento = next(
            (e for e in ELEMENTOS if clase in (e.puesto, e.falta)
             and (not solo_exigidos or e.clave in cfg.exigidos)),
            None,
        )
        if elemento is None:
            continue
        lo_tiene = clase == elemento.puesto
        if not lo_tiene and conf < piso_de_ausencia(elemento.clave, cfg):
            # Por debajo del umbral más bajo que este elemento pueda llegar a
            # usar no hay nada que decidir.
            continue
        quien = de_quien_es(caja, personas, cfg.solapeMinimo)
        if quien is None:
            continue
        if cfg.verificarPosicion and not en_su_lugar(elemento, caja, personas[quien]):
            continue
        signo = "tiene" if lo_tiene else "falta"
        de_este = evidencia.setdefault(quien, {}).setdefault(elemento.clave, {})
        if conf > de_este.get(signo, -1.0):
            de_este[signo] = conf
    return evidencia


def piso_de_ausencia(clave: str, cfg: ConfigEpp) -> float:
    """La confianza más baja que una caja de ausencia puede llegar a necesitar.

    Es también la vara con la que se decide qué se dibuja en pantalla: si algo
    puede llegar a generar una alerta, tiene que verse. Que el operador reciba
    en Eventos un "sin chaleco" que en la cámara no estaba marcado es la forma
    más rápida de que deje de creerle al módulo.
    """
    directo = cfg.umbralPorElemento.get(clave, cfg.minConfianzaFalta)
    corroborado = cfg.umbralCorroborado.get(clave)
    return min(directo, corroborado) if corroborado is not None else directo


def decidir(
    clave: str,
    tiene: float | None,
    falta: float | None,
    cfg: ConfigEpp,
) -> tuple[bool, float] | None:
    """El veredicto sobre un elemento de una persona, con toda la evidencia junta.

    None es "no se sabe", que no es lo mismo que "lo tiene": el que no se sabe
    no genera nada.
    """
    directo = cfg.umbralPorElemento.get(clave, cfg.minConfianzaFalta)
    corroborado = cfg.umbralCorroborado.get(clave)

    # 1) Ausencia por sí sola. Alcanza para acusar aunque el modelo también
    #    haya creído ver el elemento puesto, si la ausencia es más confiable:
    #    ante dos detecciones contradictorias manda la más segura, y ante
    #    empate la que NO acusa.
    if falta is not None and falta >= directo and (tiene is None or falta > tiene):
        return (False, falta)

    # 2) Ausencia corroborada por lo que no aparece. Confianza más baja, pero
    #    sólo cuando el detector no le vio ese elemento a esa persona por
    #    ningún lado —ni siquiera flojo—. Ese silencio es informativo: el
    #    modelo encuentra el elemento puesto mucho mejor de lo que encuentra su
    #    ausencia, así que no haberlo encontrado pesa.
    if (
        falta is not None
        and corroborado is not None
        and falta >= corroborado
        and tiene is None
    ):
        return (False, falta)

    # 3) Lo tiene puesto. Se le pide menos confianza que a la ausencia porque
    #    equivocarse acá no acusa a nadie.
    if tiene is not None and tiene >= cfg.minConfianza:
        return (True, tiene)

    return None


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
                if clave in cfg.sinAlertar:
                    # Se sigue viendo y dibujando, pero no se avisa: el modelo
                    # todavía no distingue esta ausencia con precisión suficiente
                    # como para acusar a alguien.
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


def huella_de_pesos(ruta: Path) -> str:
    """Identifica un archivo de pesos, para saber sobre cuál se midió un umbral.

    Los umbrales son una propiedad del modelo: los de un modelo no valen para
    otro. Sin una huella no había forma de notar que la medición había quedado
    vieja, y el módulo alertaba con números de un modelo que ya no existía.

    Se lee en bloques porque el .pt pesa varios megas y no hace falta tenerlo
    entero en memoria para resumirlo.
    """
    h = hashlib.sha1()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()[:16]


def calibracion(
    exigidos: tuple[str, ...],
    medidos: dict[str, float],
    corroborados: dict[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, float], tuple[str, ...]]:
    """Qué se puede alertar y con qué umbrales, según lo que se midió del modelo.

    `medidos` y `corroborados` salen de `training/ppe/evaluar_personas.py`: por
    cada elemento, la confianza con la que sus alertas alcanzan la precisión
    pedida MIDIENDO EL VEREDICTO POR PERSONA, que es lo que el módulo emite. Un
    elemento que no está en ninguna de las dos listas es uno que el modelo no
    distingue lo bastante bien como para acusar a nadie.

    Antes esto se calibraba con `umbral.py`, que mide caja por caja contra las
    anotadas. Es la métrica del detector, no la del módulo, y castiga cosas que
    al operador no le importan: dos cajas mal puestas sobre la misma persona
    son dos errores para el detector y una sola alerta —correcta— en pantalla.
    Con esa vara el chaleco daba 0,41 de precisión y quedaba silenciado, cuando
    medido por persona da 0,83 y ve 3 de cada 4 faltas reales.

    La regla es simple y es la que hace que el módulo no invente: **sólo alerta
    lo que tiene un umbral medido**. Lo demás se sigue detectando y dibujando
    —sirve para ver que el módulo está mirando— pero no manda nada a Eventos.

    Existe porque el error que reportó el usuario nace justo de lo contrario: la
    configuración guardada en la cámara se escribió al asignar el módulo, con
    los valores por omisión de ese momento, y nunca más se tocó. Cuando después
    se midió que el casco no estaba para alertar y que el chaleco necesitaba
    0,35, esa cámara siguió con 0,45 para todo: avisaba de cascos que estaban
    puestos y se comía las faltas de chaleco reales. La calibración pertenece al
    modelo, no a la cámara.
    """
    umbrales = {e: u for e, u in medidos.items() if e in exigidos}
    # El corroborado es un extra sobre el directo, nunca un reemplazo: sin
    # umbral directo medido no hay nada que corroborar, y dejar que un elemento
    # alertara sólo por el corroborado lo haría caer en `minConfianzaFalta`,
    # que es un valor por omisión y no una medición.
    corro = {e: u for e, u in (corroborados or {}).items() if e in umbrales}
    callados = tuple(e for e in exigidos if e not in umbrales)
    return umbrales, corro, callados
