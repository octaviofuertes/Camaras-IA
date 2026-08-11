"""Identificación de personas por rostro. Sin dependencias del motor de caras.

QUÉ HACE Y QUÉ NO GUARDA
------------------------
Compara el rostro detectado contra las plantillas de los empleados dados de alta
CON consentimiento registrado, y devuelve a quién corresponde.

De quien NO está dado de alta no se guarda nada. Nada de nada: ni plantilla, ni
foto, ni un identificador. Eso tiene una consecuencia incómoda que hay que
resolver con cuidado y está resuelta acá: si no se recuerda a los desconocidos,
el sistema volvería a preguntar "¿reconocés a esta persona?" cada vez que el
mismo repartidor entra al cuadro, y en un día la cola de revisión sería
inservible.

La solución es recordarlos SÓLO EN MEMORIA y por un rato: `RegistroDesconocidos`
mantiene sus vectores mientras el proceso vive, para no volver a preguntar, y
todo desaparece al reiniciar. Nunca toca la base. Es la diferencia entre "no
molestar dos veces por lo mismo" y "armar un fichero de gente que no consintió".

POR QUÉ EL UMBRAL DE PARECIDO ES ALTO
-------------------------------------
Equivocarse acá no es simétrico. Un falso negativo hace que un rato de trabajo
quede como "no identificado", que es visible y corregible. Un falso POSITIVO le
atribuye a una persona el tiempo de otra —incluido el tiempo con el teléfono— y
nadie se entera. El umbral está puesto para que el segundo error sea raro,
aunque cueste identificaciones.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class ConfigRostros:
    """Parámetros de la identificación."""

    # Parecido mínimo (coseno, 0..1) para afirmar que es la misma persona.
    # 0.42 es exigente para embeddings ArcFace: reduce el falso positivo, que es
    # el error caro. Ver el encabezado.
    matchThreshold: float = 0.42
    # Margen que el mejor candidato debe sacarle al segundo. Sin esto, dos
    # personas parecidas —hermanos, gemelos— se turnarían el nombre según el
    # ruido del frame.
    matchMargin: float = 0.06
    # Fracción del alto de la imagen que debe ocupar la cara. Más chica que
    # esto, el vector es ruido y no alcanza para afirmar una identidad.
    minFaceSize: float = 0.05
    # Cuánto se recuerda a un desconocido EN MEMORIA para no volver a preguntar.
    askCooldownMinutes: float = 10.0
    # Cuántos desconocidos distintos se recuerdan a la vez. Acota el uso de
    # memoria en un lugar de paso.
    maxDesconocidos: int = 200

    # ── no volver a preguntar por la misma persona ──────────────────
    # Umbral SEPARADO del de identidad, y más bajo. Acá los errores cuestan al
    # revés: confundir a dos desconocidos sólo hace que no se repregunte por uno
    # (se vuelve a preguntar cuando venza la memoria), mientras que no reconocer
    # que ya se preguntó llena la cola de revisión con la misma cara.
    #
    # Medido sobre 54 rostros de esta cámara: la misma persona nunca bajó de
    # +0.303 y dos personas distintas nunca pasaron de +0.135. Con 0.42 —el de
    # identidad, que era el que se usaba— se repreguntaba por la misma persona
    # una y otra vez: 29 preguntas para 3 personas en dos minutos.
    repeatThreshold: float = 0.25

    # ── calidad mínima para PREGUNTAR ───────────────────────────────
    # Preguntar es más exigente que identificar, por dos razones distintas:
    # el recorte tiene que poder contestarlo un humano (una nuca no se puede
    # reconocer), y el vector se convierte en la PRIMERA plantilla de esa
    # persona si la respuesta es que sí. Identificar a alguien ya dado de alta
    # tolera mucho menos que eso, y por eso conserva sus propios umbrales.
    askMinFaceSize: float = 0.07
    askMinScore: float = 0.65
    # Giro de la cabeza, en grados. El corte salió de mirar 90 rostros de esta
    # cámara ordenados por pose: hasta |yaw| 17 se le ve la cara y se puede
    # decir quién es; desde 21 ya son perfiles y desde 30, nucas.
    askMaxYaw: float = 20.0
    # Cabeceo. Alguien mirando su escritorio tiene la cara de frente pero
    # apuntando al piso, y el recorte es la coronilla.
    askMaxPitch: float = 25.0

    # Consecuencia asumida de los dos: a quien nunca mira hacia la cámara no se
    # lo pregunta, y su tiempo aparece como "sin identificar" en el informe. Es
    # preferible a una alerta que nadie puede contestar. Con levantar la vista
    # una sola vez en diez minutos alcanza para que se pregunte: de 90 rostros,
    # 19 pasaron este filtro, y hace falta uno solo por persona.


@dataclass
class Persona:
    """Un empleado dado de alta, con sus plantillas."""
    id: str
    nombre: str
    vectores: list[list[float]] = field(default_factory=list)


@dataclass
class Rostro:
    """Una cara detectada en un frame."""
    vector: list[float]
    # Recuadro normalizado 0..1 — sirve para asociar la cara a un cuerpo.
    x: float
    y: float
    w: float
    h: float
    calidad: float = 1.0
    # Pose de la cabeza en grados (0 = mirando a la cámara). La estima el
    # detector: `yaw` es girar a los costados, `pitch` es levantar o bajar la
    # vista. Deciden si el recorte se puede reconocer.
    yaw: float = 0.0
    pitch: float = 0.0


@dataclass
class Identificacion:
    """A quién corresponde un rostro."""
    rostro: Rostro
    persona_id: str | None
    nombre: str | None
    parecido: float
    # True si hay que preguntarle al operador quién es.
    preguntar: bool
    motivo: str


class Galeria:
    """Las plantillas de los empleados dados de alta."""

    def __init__(self, personas: list[Persona] | None = None) -> None:
        self.personas: list[Persona] = personas or []

    def actualizar(self, personas: list[Persona]) -> None:
        self.personas = personas

    def buscar(self, vector: list[float], cfg: ConfigRostros) -> tuple[Persona | None, float, float]:
        """Devuelve (persona, parecido, parecido_del_segundo).

        Se compara contra TODAS las plantillas de cada persona y se toma la
        mejor: alguien dado de alta con varias fotos (de frente, de perfil) se
        reconoce en más situaciones.
        """
        mejor: tuple[Persona | None, float] = (None, -1.0)
        segundo = -1.0
        for p in self.personas:
            suyo = max((coseno(vector, v) for v in p.vectores), default=-1.0)
            if suyo > mejor[1]:
                segundo = mejor[1]
                mejor = (p, suyo)
            elif suyo > segundo:
                segundo = suyo
        return mejor[0], mejor[1], segundo


class RegistroDesconocidos:
    """Recuerda caras no identificadas SÓLO en memoria, para no repreguntar.

    No se persiste jamás. Al reiniciar el proceso se olvida todo, y eso es
    correcto: es un antirrebote, no un registro de personas.
    """

    def __init__(self, cfg: ConfigRostros) -> None:
        self.cfg = cfg
        # (vector, ultima_vez)
        self._vistos: list[tuple[list[float], float]] = []

    def ya_preguntado(self, vector: list[float], ahora: float) -> bool:
        """¿Ya se preguntó por esta cara hace poco? Refresca el reloj si sí."""
        limite = self.cfg.askCooldownMinutes * 60.0
        self._vistos = [(v, t) for v, t in self._vistos if ahora - t <= limite]

        for i, (v, _) in enumerate(self._vistos):
            if coseno(vector, v) >= self.cfg.repeatThreshold:
                self._vistos[i] = (v, ahora)
                return True
        return False

    def anotar(self, vector: list[float], ahora: float) -> None:
        self._vistos.append((vector, ahora))
        if len(self._vistos) > self.cfg.maxDesconocidos:
            # Se descartan los más viejos: en un lugar de paso, la memoria no
            # puede crecer sin límite.
            self._vistos.sort(key=lambda x: x[1], reverse=True)
            del self._vistos[self.cfg.maxDesconocidos:]

    def olvidar_todo(self) -> None:
        """Descarta lo recordado. Se usa cuando cambia quién está dado de alta."""
        self._vistos.clear()

    @property
    def recordados(self) -> int:
        return len(self._vistos)


class Identificador:
    """Decide, para cada rostro, a quién corresponde o si hay que preguntar."""

    def __init__(self, cfg: ConfigRostros | None = None) -> None:
        self.cfg = cfg or ConfigRostros()
        self.galeria = Galeria()
        self.desconocidos = RegistroDesconocidos(self.cfg)
        # Cuántas caras se vieron pero quedaron afuera por chicas. Sin este
        # número, "no llega la pregunta" no se distingue de "la persona está
        # demasiado lejos de la cámara".
        self.descartados_por_tamano = 0

    def identificar(self, rostros: list[Rostro], ahora: float) -> list[Identificacion]:
        cfg = self.cfg
        salida: list[Identificacion] = []

        for r in rostros:
            if r.h < cfg.minFaceSize:
                self.descartados_por_tamano += 1
                # Demasiado chica: el vector no da para afirmar nada, y tampoco
                # para preguntar —le mostraríamos al operador una mancha.
                salida.append(Identificacion(r, None, None, 0.0, False, "cara demasiado chica"))
                continue

            persona, parecido, segundo = self.galeria.buscar(r.vector, cfg)
            margen = parecido - segundo if segundo > 0 else 1.0

            if persona is not None and parecido >= cfg.matchThreshold and margen >= cfg.matchMargin:
                salida.append(
                    Identificacion(r, persona.id, persona.nombre, parecido, False, "identificado")
                )
                continue

            if persona is not None and parecido >= cfg.matchThreshold:
                # Pasa el umbral pero hay otra persona casi igual de parecida.
                # Atribuirle el tiempo a cualquiera de las dos sería inventar.
                salida.append(
                    Identificacion(r, None, None, parecido, False,
                                   f"ambiguo entre dos personas (margen {margen:.2f})")
                )
                continue

            if self.desconocidos.ya_preguntado(r.vector, ahora):
                salida.append(Identificacion(r, None, None, parecido, False, "ya se preguntó"))
                continue

            # Preguntar por una cara que el operador no puede reconocer no es
            # una alerta: es ruido en la cola de revisión. Y su vector sería
            # además una mala primera plantilla, así que la persona quedaría
            # dada de alta con algo que no la representa.
            if r.calidad < cfg.askMinScore:
                salida.append(Identificacion(r, None, None, parecido, False, "cara poco nítida"))
                continue
            if r.h < cfg.askMinFaceSize:
                salida.append(Identificacion(r, None, None, parecido, False, "cara chica para preguntar"))
                continue
            if abs(r.yaw) > cfg.askMaxYaw:
                salida.append(Identificacion(r, None, None, parecido, False,
                                             f"de perfil ({r.yaw:.0f}°): irreconocible"))
                continue
            if abs(r.pitch) > cfg.askMaxPitch:
                salida.append(Identificacion(r, None, None, parecido, False,
                                             f"mirando al piso ({r.pitch:.0f}°): irreconocible"))
                continue

            self.desconocidos.anotar(r.vector, ahora)
            salida.append(Identificacion(r, None, None, parecido, True, "desconocido"))

        return salida


def coseno(a: list[float], b: list[float]) -> float:
    """Parecido coseno entre dos vectores. 1 = idénticos, 0 = sin relación."""
    if len(a) != len(b) or not a:
        return -1.0
    num = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        num += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return -1.0
    return num / math.sqrt(na * nb)


def asociar_a_cuerpo(rostro: Rostro, cuerpos: list[tuple[float, float, float, float]]) -> int | None:
    """Índice del cuerpo al que pertenece esta cara, o None.

    La cara tiene que estar dentro del recuadro del cuerpo y en su parte
    superior. Se usa para atribuir a la persona identificada el uso de teléfono
    que se detectó sobre SU cuerpo, y no sobre el de al lado — que es justamente
    el problema que hacía injusto el informe agregado.
    """
    cx = rostro.x + rostro.w / 2
    cy = rostro.y + rostro.h / 2
    mejor, mejor_area = None, float("inf")

    for i, (bx, by, bw, bh) in enumerate(cuerpos):
        if not (bx <= cx <= bx + bw):
            continue
        # La cara está en el tercio superior del cuerpo.
        if not (by - 0.02 <= cy <= by + bh * 0.40):
            continue
        area = bw * bh
        # Ante superposición, gana el cuerpo más chico: es el que encierra a esa
        # cara más ajustadamente.
        if area < mejor_area:
            mejor, mejor_area = i, area
    return mejor
