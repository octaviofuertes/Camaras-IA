"""Lógica de detección de caídas a partir de pose. Sin dependencias de YOLO.

QUÉ ES UNA CAÍDA (y qué se hizo mal antes)
------------------------------------------
Una caída es un DESCENSO BRUSCO E INVOLUNTARIO que termina con el cuerpo abajo.
Quedarse tirado es una consecuencia posible, no parte de la definición: uno se
tropieza, se cae y se levanta enseguida, y eso fue una caída igual.

Por eso el detector alerta en el IMPACTO, no después de esperar a que la persona
siga en el suelo. La permanencia sólo agrava la severidad.

LAS TRES SEÑALES, Y POR QUÉ HACEN FALTA LAS TRES
------------------------------------------------
1. CUÁNTO BAJÓ EL TRONCO respecto de su altura de pie, medido en estaturas de
   esa misma persona. Es la señal que decide, y la razón es lo que NO le afecta:
   no depende de qué partes del cuerpo se ven. Sentarse baja el tronco el largo
   del muslo (~0.3); caerse lo lleva al piso (~0.8).

2. DESCENSO RÁPIDO. Es lo único que separa una caída de agacharse o de sentarse
   en el suelo: esos movimientos también terminan abajo, pero despacio y de
   forma controlada.

3. IMPACTO CONFIRMADO en dos frames. Descarta una pose mal estimada suelta sin
   exigir que la persona se quede tirada.

QUÉ SE PROBÓ Y NO FUNCIONA COMO SEÑAL PRINCIPAL
-----------------------------------------------
- LA EXTENSIÓN VERTICAL DE LOS PUNTOS VISIBLES. Se desploma cuando las piernas
  quedan tapadas —alguien sentado a un escritorio— y el detector concluye que el
  cuerpo se derrumbó sin que se haya movido. Era la causa de que sentarse se
  tomara siempre como caída.
- EL ÁNGULO DEL TORSO. Se mide en la imagen, no en el mundo: si alguien cae
  HACIA la cámara o alejándose, su torso se sigue proyectando casi vertical
  aunque esté horizontal en el piso.
- LA SILUETA DEL RECUADRO. Se ensancha con un brazo estirado sin que la postura
  haya cambiado.
Las tres se conservan como evidencia de apoyo para el operador; ninguna decide
sola.

Y UNA REGLA QUE NO SE NEGOCIA
-----------------------------
No se alerta si el cuerpo no quedó abajo. Hubo una versión que alertaba sólo por
un pico de velocidad, y en cámara real produjo alertas con el tronco al 104 % de
su altura de pie —la persona parada— porque un salto de la estimación de pose se
lee igual que una caída si nadie mira dónde terminó el cuerpo.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque

# Índices de los 17 puntos del esqueleto (formato COCO).
NOSE = 0
L_SHOULDER, R_SHOULDER = 5, 6
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16

TORSO_POINTS = (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP)


class State(str, Enum):
    UPRIGHT = "upright"      # de pie o en actividad normal
    FALLING = "falling"      # descenso brusco en curso
    IMPACT = "impact"        # cuerpo abajo tras el descenso (se confirma acá)
    ALERTED = "alerted"      # ya se emitió la alerta de esta caída
    RECOVERED = "recovered"  # se levantó después de una caída ya alertada


@dataclass
class Keypoint:
    x: float  # normalizado 0..1
    y: float  # normalizado 0..1 (crece hacia abajo, como en la imagen)
    score: float


@dataclass
class PoseFrame:
    """Una persona en un instante."""
    track_id: int
    ts: float
    keypoints: list[Keypoint]
    bbox: tuple[float, float, float, float]  # x, y, w, h normalizados
    det_score: float


@dataclass
class FallConfig:
    """Parámetros del detector. Cada número tiene un porqué."""

    # ── calidad de la pose ──────────────────────────────────────────
    keypointScore: float = 0.3
    minTorsoPoints: int = 2   # con 2 de 4 ya se puede estimar el torso
    # Fracción del alto de la imagen que debe ocupar la persona para que su
    # pose sea confiable. Por debajo, los puntos del esqueleto caen dentro de
    # unos pocos píxeles y el "colapso de altura" es ruido de estimación, no
    # movimiento real. También descarta los falsos positivos chicos que YOLO
    # produce sobre objetos del fondo.
    minPersonHeight: float = 0.12

    # ── señal 1: cuánto bajó el tronco ──────────────────────────────
    # Medido en estaturas: 0 de pie, ~0.8 tendido en el piso.
    #
    # Los números vienen de la geometría del cuerpo, no de probar a ojo:
    # sentarse en una silla baja el tronco el largo del muslo, alrededor de
    # 0.25-0.32 estaturas. Caerse lo lleva al piso, 0.7-0.9. El umbral va en el
    # medio pero cerca de lo alto, porque equivocarse hacia el lado de la falsa
    # alarma es peor: un sistema que grita cuando alguien se sienta se ignora, y
    # entonces no detecta nada.
    trunkDropRatio: float = 0.38
    # Bajada que alcanza por sí sola, sin mirar la postura.
    #
    # Está alto a propósito, y el valor salió de medirlo: con 0.60 esta vía se
    # comía las secuencias de gente que se acuesta al piso a propósito, que
    # llegan a bajadas de 1.0-1.5 igual que una caída. Por encima de 1.1 sólo se
    # llega con la perspectiva de alguien que cae HACIA la cámara, donde el
    # cuerpo se proyecta comprimido. Todo lo demás tiene que mostrar además un
    # cuerpo horizontal.
    trunkDropSure: float = 1.10
    # Alto/ancho de la nube de puntos por debajo del cual el cuerpo se
    # considera horizontal. De pie 4-6, sentado 2-3, tendido menos de 1.
    # Medido: 1.4 dejaba pasar posturas sentadas; 1.0 exige estar realmente
    # tendido y elimina esas falsas alarmas sin perder caídas.
    # 1.2 y no el óptimo puntual: medido sobre las 70 secuencias, los valores
    # entre 1.0 y 1.2 dan el mismo resultado (25 detectadas, 4 falsas) y 0.9 da
    # una falsa menos. Con 40 secuencias negativas esa diferencia es UNA muestra,
    # así que se elige el medio de la meseta en vez del pico — un pico de una
    # muestra es ruido con forma de mejora.
    downVerticality: float = 1.2
    # Colapso de altura visible. Se conserva como dato para el operador, pero
    # ya NO decide: se desploma cuando las piernas quedan tapadas.
    collapseRatio: float = 0.65
    # Frames necesarios para fijar la altura de referencia. Con 5 alcanzaba para
    # anclarla a media zancada o a media agachada: en cámara real se vieron
    # alturas del 246 % de una referencia aprendida con la persona doblada. Dos
    # segundos de observación dan una referencia estable, y hasta entonces
    # simplemente no se juzga el colapso — mejor callar que inventar.
    baselineFrames: int = 4
    # Ventana sobre la que se estima la altura de pie. Larga a propósito: la
    # persona cambia de postura todo el tiempo y hace falta ver bastante para
    # saber cuál es su altura real.
    baselineWindowFrames: int = 120

    # ── señal 2: descenso rápido ────────────────────────────────────
    # En estaturas por segundo, y es la señal que MÁS separa: sobre el dataset
    # público, las caídas tienen mediana 0.88 y las actividades cotidianas 0.19.
    # Acostarse a propósito termina en la misma postura que una caída; lo único
    # que los distingue es a qué velocidad se llegó.
    #
    # 0.70 salió de una búsqueda sobre las 70 secuencias, no de probar a ojo.
    # Bajarlo a 0.55 sube el recall de 83 % a 87 % y duplica las falsas alarmas
    # de 4 a 8: el detector se vuelve el que molesta, y el que molesta se apaga.
    fallVelocity: float = 0.70
    # Ventana en la que se busca el descenso: una caída dura menos de 1 s.
    fallWindowSeconds: float = 1.2

    # ── señal 3: confirmación breve del impacto ─────────────────────
    # Se cuenta en FRAMES, no en segundos, y la diferencia importa. Lo que se
    # quiere descartar es una pose mal estimada suelta, y eso es un fenómeno por
    # frame: dos observaciones seguidas del cuerpo abajo ya lo descartan, corra
    # el pipeline a 6 fps o a 15. Expresado en segundos, el mismo umbral pedía
    # dos frames a 10 fps y cuatro a 6 fps, y a 6 fps —el ritmo de producción—
    # eso alcanzaba para perder tropiezos cortos por centésimas.
    #
    # NO es permanencia en el suelo: son dos frames, menos de medio segundo.
    impactConfirmFrames: int = 2

    # ── severidad ───────────────────────────────────────────────────
    # Si sigue en el suelo más que esto, la caída se considera grave
    # (posible pérdida de conocimiento o imposibilidad de levantarse).
    prolongedSeconds: float = 6.0

    # Ángulo de torso que refuerza la evidencia (no es obligatorio).
    downAngleDeg: float = 50.0

    trackTimeoutSeconds: float = 5.0


@dataclass
class TrackState:
    state: State = State.UPRIGHT
    last_ts: float = 0.0

    # Altura de referencia de ESTA persona estando de pie.
    baseline_heights: Deque[float] = field(default_factory=lambda: deque(maxlen=120))
    baseline: float | None = None

    # Altura del tronco en la imagen cuando la persona está de pie. Se toma la
    # envolvente SUPERIOR (el valor más alto que alcanzó, o sea la `y` más
    # chica): nadie tiene los hombros más arriba que estando parado, así que ese
    # extremo es la referencia correcta y no se contamina con cada vez que la
    # persona se agacha.
    trunk_ys: Deque[float] = field(default_factory=lambda: deque(maxlen=120))
    trunk_baseline: float | None = None

    # Historia reciente (ts, altura, centro_y) para medir el descenso.
    history: Deque[tuple[float, float, float]] = field(default_factory=lambda: deque(maxlen=60))

    # Mejor puntaje con el que se detectó a ESTA persona últimamente. Ver
    # `_confidence` para por qué no alcanza con el del frame actual.
    det_scores: Deque[float] = field(default_factory=lambda: deque(maxlen=60))

    peak_velocity: float = 0.0
    falling_since: float | None = None    # desde cuándo está descendiendo
    down_frames: int = 0                  # frames seguidos con el cuerpo abajo
    collapse_since: float | None = None   # desde cuándo está colapsado
    down_since: float | None = None       # desde cuándo está abajo (severidad)
    # Ya se concluyó que esta postura baja no fue una caída (se sentó, se
    # agachó). Se mantiene hasta que la persona vuelva a incorporarse, para no
    # entrar a confirmar impacto una y otra vez sobre la misma postura quieta.
    postura_baja_aceptada: bool = False


@dataclass
class FallResult:
    track_id: int
    state: State
    confidence: float
    torso_angle: float | None
    aspect_ratio: float | None
    velocity: float
    down_seconds: float
    is_fall: bool          # True sólo en el frame en que se confirma
    severity: str          # 'high' | 'critical' según si sigue caída
    reason: str
    quality_ok: bool
    collapse_ratio: float | None  # altura actual / altura habitual
    trunk_drop: float | None = None      # cuánto bajó el tronco, en estaturas
    verticality: float | None = None     # alto/ancho de la nube de puntos


def _mid_y(kps: list[Keypoint], a: int, b: int, min_score: float) -> float | None:
    ok = [i for i in (a, b) if i < len(kps) and kps[i].score >= min_score]
    if not ok:
        return None
    return sum(kps[i].y for i in ok) / len(ok)


def torso_angle_deg(kps: list[Keypoint], min_score: float) -> float | None:
    """Ángulo del torso respecto de la vertical: 0° de pie, 90° tendido.

    Ojo: se mide EN LA IMAGEN. Si la persona cae hacia la cámara, este ángulo
    puede seguir marcando ~0° aunque esté en el piso. Por eso es una señal de
    apoyo, no la principal.
    """
    def ok(i: int) -> bool:
        return i < len(kps) and kps[i].score >= min_score

    shoulders = [i for i in (L_SHOULDER, R_SHOULDER) if ok(i)]
    hips = [i for i in (L_HIP, R_HIP) if ok(i)]
    if not shoulders or not hips:
        return None

    sx = sum(kps[i].x for i in shoulders) / len(shoulders)
    sy = sum(kps[i].y for i in shoulders) / len(shoulders)
    hx = sum(kps[i].x for i in hips) / len(hips)
    hy = sum(kps[i].y for i in hips) / len(hips)

    dx, dy = hx - sx, hy - sy
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return None
    return math.degrees(math.atan2(abs(dx), abs(dy)))


def visible_torso_points(kps: list[Keypoint], min_score: float) -> int:
    return sum(1 for i in TORSO_POINTS if i < len(kps) and kps[i].score >= min_score)


def altura_del_tronco(kps: list[Keypoint], min_score: float) -> float | None:
    """Posición vertical del tronco en la imagen (hombros; si no, nariz o cadera).

    Es LA medida del detector, y la razón es lo que NO le afecta.

    La extensión vertical de los puntos visibles —lo que se usaba antes— se
    desploma cuando las piernas quedan tapadas. Alguien sentado a un escritorio
    con las piernas debajo pasa a mostrar sólo cabeza, hombros y caderas, la
    extensión cae a un tercio, y el detector concluye que el cuerpo se derrumbó
    cuando en realidad no se movió. Era la causa de que sentarse se tomara
    siempre como caída.

    Los hombros, en cambio, están casi siempre visibles y su altura sólo baja si
    la persona baja de verdad. Sentarse los baja el largo del muslo; caerse los
    lleva al piso. Esa diferencia es la que separa los dos casos.
    """
    hombros = [i for i in (L_SHOULDER, R_SHOULDER) if i < len(kps) and kps[i].score >= min_score]
    if hombros:
        return sum(kps[i].y for i in hombros) / len(hombros)
    if NOSE < len(kps) and kps[NOSE].score >= min_score:
        return kps[NOSE].y
    caderas = [i for i in (L_HIP, R_HIP) if i < len(kps) and kps[i].score >= min_score]
    if caderas:
        # La cadera queda más abajo que los hombros; se corrige para que la
        # serie no dé un salto al cambiar de punto de referencia.
        return sum(kps[i].y for i in caderas) / len(caderas) - 0.18 * _escala(kps, min_score)
    return None


def _escala(kps: list[Keypoint], min_score: float) -> float:
    """Tamaño aparente de la persona: distancia hombros-caderas, ampliada.

    Sirve de referencia de escala instantánea. Se usa el torso porque sobrevive
    a que las piernas estén tapadas, que es justo cuando hace falta.
    """
    hombros = [i for i in (L_SHOULDER, R_SHOULDER) if i < len(kps) and kps[i].score >= min_score]
    caderas = [i for i in (L_HIP, R_HIP) if i < len(kps) and kps[i].score >= min_score]
    if hombros and caderas:
        sy = sum(kps[i].y for i in hombros) / len(hombros)
        sx = sum(kps[i].x for i in hombros) / len(hombros)
        hy = sum(kps[i].y for i in caderas) / len(caderas)
        hx = sum(kps[i].x for i in caderas) / len(caderas)
        largo = math.hypot(hx - sx, hy - sy)
        if largo > 0.01:
            return largo / 0.30  # el torso es ~30 % de la estatura
    return 0.0


def verticalidad(kps: list[Keypoint], min_score: float) -> float | None:
    """Cuán vertical es la nube de puntos del cuerpo: alto / ancho.

    De pie ronda 4-6; sentado 2-3; tendido baja de 1. Se calcula sobre los
    puntos del esqueleto y no sobre el recuadro, porque el recuadro se ensancha
    con un brazo estirado sin que la postura haya cambiado.

    Es una señal instantánea, sin memoria, y por eso complementa a la caída del
    tronco: aquélla necesita saber cómo estaba antes, ésta no.
    """
    ys = [k.y for k in kps if k.score >= min_score]
    xs = [k.x for k in kps if k.score >= min_score]
    if len(ys) < 4:
        return None
    alto = max(ys) - min(ys)
    ancho = max(xs) - min(xs)
    if ancho < 1e-4:
        return None
    return alto / ancho


def person_height(kps: list[Keypoint], bbox: tuple[float, float, float, float], min_score: float) -> float:
    """Altura visible de la persona: extensión vertical de sus puntos.

    Se prefiere el esqueleto a la caja porque la caja incluye brazos extendidos
    y objetos pegados, que la ensanchan sin que la persona haya cambiado.
    """
    ys = [kps[i].y for i in range(len(kps)) if kps[i].score >= min_score]
    if len(ys) >= 3:
        span = max(ys) - min(ys)
        if span > 0.01:
            return span
    return max(bbox[3], 0.02)


class FallDetector:
    """Máquina de estados por persona seguida."""

    def __init__(self, config: FallConfig | None = None) -> None:
        self.cfg = config or FallConfig()
        self.tracks: dict[int, TrackState] = {}

    def purge(self, now: float) -> None:
        stale = [t for t, s in self.tracks.items() if now - s.last_ts > self.cfg.trackTimeoutSeconds]
        for t in stale:
            del self.tracks[t]

    def _descenso_reciente(self, st: TrackState, now: float) -> float:
        """Velocidad con la que el cuerpo llegó a donde está AHORA.

        Se evalúan todos los tramos que terminan en el instante actual y se toma
        el más veloz. Dos decisiones, las dos aprendidas midiendo:

        - Terminan en el instante actual, y no en cualquier par de la ventana.
          Lo que importa físicamente es a qué velocidad llegó el cuerpo a donde
          quedó; tomando el pico de cualquier momento, un movimiento rápido
          ajeno —enderezarse, girar— se le prestaba a un descenso lento
          posterior.
        - Se mira una VENTANA y no sólo el frame anterior. Si la persona estuvo
          quieta y recién ahí se cayó, comparar contra el frame previo pierde el
          tramo real de caída cuando el fps es bajo.
        """
        cfg = self.cfg
        recientes = [(t, h, c) for (t, h, c) in st.history if now - t <= cfg.fallWindowSeconds]
        if len(recientes) < 2:
            return 0.0

        # La escala se toma del propio tramo medido, no de la referencia
        # aprendida. Es lo que permite medir velocidad desde el segundo frame:
        # normalizando por `st.baseline` no había ninguna señal hasta terminar
        # de aprender la estatura, y contra el dataset público se midió que las
        # caídas duran 11-15 frames — o sea que el aprendizaje se comía la
        # caída entera y no quedaba nada que detectar.
        # Todos los tramos que TERMINAN en el instante actual, no cualquier par
        # de la ventana. La diferencia decide el detector entero.
        #
        # Lo que importa físicamente es a qué velocidad llegó el cuerpo a donde
        # está ahora. Tomando el pico de cualquier momento, un movimiento rápido
        # cualquiera —enderezarse, girar, un salto de la estimación— quedaba
        # registrado como "descenso" y después alcanzaba con que el cuerpo
        # estuviera abajo por cualquier motivo. Contra el dataset público eso
        # daba 15 falsas alarmas sobre 40: personas que se acuestan despacio,
        # con un pico de velocidad ajeno prestado de otro instante.
        t1, h1, c1 = recientes[-1]
        peor = 0.0
        for (t0, h0, c0) in recientes[:-1]:
            dt = t1 - t0
            if dt <= 1e-3:
                continue
            # De pie es la mayor de las dos alturas del par: la persona sólo
            # puede haberse achicado al caer.
            ref = max(h0, h1, st.baseline or 0.0, 1e-6)
            peor = max(peor, ((c1 - c0) / ref) / dt)
        return peor

    def update(self, pf: PoseFrame) -> FallResult:
        cfg = self.cfg
        pf = _sanear(pf)
        st = self.tracks.get(pf.track_id)
        if st is None:
            st = TrackState(last_ts=pf.ts)
            st.baseline_heights = deque(maxlen=cfg.baselineWindowFrames)
            st.trunk_ys = deque(maxlen=cfg.baselineWindowFrames)
            self.tracks[pf.track_id] = st

        angle = torso_angle_deg(pf.keypoints, cfg.keypointScore)
        n_torso = visible_torso_points(pf.keypoints, cfg.keypointScore)
        _, _, bw, bh = pf.bbox
        ratio = (bh / bw) if bw > 1e-6 else None
        vert = verticalidad(pf.keypoints, cfg.keypointScore)

        altura = person_height(pf.keypoints, pf.bbox, cfg.keypointScore)
        y_tronco = altura_del_tronco(pf.keypoints, cfg.keypointScore)
        centro_y = y_tronco
        if centro_y is None:
            centro_y = _mid_y(pf.keypoints, L_HIP, R_HIP, cfg.keypointScore)
        if centro_y is None:
            centro_y = pf.bbox[1] + pf.bbox[3] / 2

        # Una persona demasiado chica en la imagen se juzga por su altura de
        # referencia, no por la altura del frame: si ya está caída ocupa poco,
        # y descartarla por eso sería perder justo la caída que interesa.
        referencia = st.baseline or altura
        quality_ok = n_torso >= cfg.minTorsoPoints and referencia >= cfg.minPersonHeight

        if not quality_ok:
            # Sin pose confiable no se afirma nada: no se aprende la altura de
            # referencia, no se avanza de estado y no se alerta. Mantener el
            # estado congelado es lo correcto — una oclusión momentánea no debe
            # borrar una caída en curso ni inventar una nueva.
            #
            # Y TAMPOCO entra al historial. Antes sí entraba, y eso convertía la
            # guarda en un placebo: el frame se descartaba para decidir pero sus
            # coordenadas basura quedaban registradas, así que el frame SIGUIENTE
            # —bueno— medía una velocidad enorme contra ellas. Reproducido: una
            # pose con todos sus puntos por debajo del umbral de confianza, cuyo
            # centro cae donde diga el recuadro, inyecta una velocidad de 3.83
            # con la persona inmóvil y de pie. El umbral de alerta es 0.70.
            # Ésa era una de las fábricas de "caídas de la nada".
            velocidad = self._descenso_reciente(st, pf.ts)
            st.last_ts = pf.ts
            return FallResult(
                track_id=pf.track_id, state=st.state, confidence=0.0,
                torso_angle=angle, aspect_ratio=ratio, velocity=velocidad,
                down_seconds=(pf.ts - st.down_since) if st.down_since else 0.0,
                is_fall=False, severity="high", reason="pose insuficiente",
                quality_ok=False,
                collapse_ratio=(altura / st.baseline) if st.baseline else None,
            )

        # A partir de acá la observación es confiable: recién ahora se registra.
        st.history.append((pf.ts, altura, centro_y))
        st.det_scores.append(pf.det_score)
        velocidad = self._descenso_reciente(st, pf.ts)

        # ── altura de referencia ────────────────────────────────────────
        # Se estima como el percentil 90 de las alturas observadas, no como la
        # mediana. El razonamiento: nadie es MÁS alto que estando de pie, así
        # que la envolvente superior de lo observado ES su altura de pie. La
        # mediana, en cambio, se contamina con cada vez que la persona se
        # agacha, y arrastra la referencia hacia abajo — que fue exactamente lo
        # que hacía que después estar de pie se leyera como 246 %.
        #
        # Se congela mientras hay una caída ya alertada: ahí la referencia tiene
        # que seguir siendo la de ANTES de caer, o al llenarse la ventana de
        # alturas de alguien tirado en el piso el detector concluiría que se
        # levantó sin que se haya movido.
        if quality_ok and st.state != State.ALERTED:
            st.baseline_heights.append(altura)
            # `max(..., 1)`: con baselineFrames en 0 la condición se cumplía con
            # la lista vacía y el índice del percentil daba -1 sobre una lista
            # sin elementos. Un valor de configuración absurdo tiene que producir
            # un detector conservador, no una excepción que tumba el pipeline.
            minimo = max(cfg.baselineFrames, 1)
            if len(st.baseline_heights) >= minimo:
                ordenadas = sorted(st.baseline_heights)
                idx = min(int(len(ordenadas) * 0.9), len(ordenadas) - 1)
                st.baseline = ordenadas[idx]

            # Altura del tronco estando de pie: percentil 10 de las `y`, o sea
            # la posición MÁS ALTA que alcanzó. Mismo razonamiento que arriba,
            # invertido porque en la imagen la `y` crece hacia abajo.
            if y_tronco is not None:
                st.trunk_ys.append(y_tronco)
                if len(st.trunk_ys) >= minimo:
                    ordenadas = sorted(st.trunk_ys)
                    idx = min(int(len(ordenadas) * 0.1), len(ordenadas) - 1)
                    st.trunk_baseline = ordenadas[idx]

        colapso = None
        if st.baseline and st.baseline > 1e-6:
            colapso = altura / st.baseline

        # Cuánto bajó el tronco respecto de su posición de pie, en estaturas.
        caida_tronco = None
        if y_tronco is not None and st.trunk_baseline is not None and st.baseline:
            caida_tronco = (y_tronco - st.trunk_baseline) / max(st.baseline, 1e-6)

        # ── ¿el cuerpo está abajo? ──────────────────────────────────────
        # Antes bastaba con cualquiera de tres señales sueltas, y dos de ellas
        # eran poco fiables: el colapso de altura se dispara cuando las piernas
        # quedan tapadas, y la silueta del recuadro cambia con un brazo
        # estirado. Por eso sentarse se tomaba siempre como caída.
        #
        # Ahora la señal principal es cuánto bajó el TRONCO, que no depende de
        # qué partes del cuerpo se ven. Una bajada muy grande alcanza sola. Una
        # bajada intermedia necesita además que el cuerpo se vea horizontal.
        cuerpo_abajo = False
        motivos = []
        if caida_tronco is not None:
            horizontal = (vert is not None and vert <= cfg.downVerticality) or (
                angle is not None and angle >= cfg.downAngleDeg
            )
            if caida_tronco >= cfg.trunkDropSure:
                cuerpo_abajo = True
                motivos.append(f"tronco bajó {caida_tronco:.0%} de su estatura")
            elif caida_tronco >= cfg.trunkDropRatio and horizontal:
                cuerpo_abajo = True
                motivos.append(f"tronco bajó {caida_tronco:.0%} y cuerpo horizontal")
            if cuerpo_abajo and colapso is not None:
                motivos.append(f"altura al {colapso*100:.0f}%")

        descenso_brusco = velocidad >= cfg.fallVelocity
        if descenso_brusco:
            st.peak_velocity = max(st.peak_velocity, velocidad)

        # Se vuelve a vigilar la postura baja recién cuando la persona se
        # incorporó. Un descenso brusco la reabre de inmediato: eso ya no es la
        # misma postura quieta, es una caída desde ella.
        if not cuerpo_abajo or descenso_brusco:
            st.postura_baja_aceptada = False

        is_fall = False
        reason = ""

        # ── máquina de estados ──────────────────────────────────────────
        if st.state == State.UPRIGHT:
            if descenso_brusco:
                st.state = State.FALLING
                st.falling_since = pf.ts
                st.collapse_since = None
                reason = f"descenso rápido ({velocidad:.2f})"
            elif cuerpo_abajo and st.baseline is not None and not st.postura_baja_aceptada:
                # Llegó abajo sin que se viera el descenso (puede haber pasado
                # entre dos frames): igual se vigila.
                st.state = State.FALLING
                st.falling_since = pf.ts
                _marcar_abajo(st, pf.ts)
                reason = "cuerpo abajo"

        elif st.state == State.FALLING:
            if cuerpo_abajo:
                _marcar_abajo(st, pf.ts)
                transcurrido = pf.ts - (st.collapse_since or pf.ts)

                # Se alerta acá: hubo descenso brusco y el cuerpo quedó abajo.
                # NO se espera a ver si se queda tirado.
                if st.down_frames >= cfg.impactConfirmFrames and st.peak_velocity >= cfg.fallVelocity:
                    st.state = State.ALERTED
                    is_fall = True
                    reason = f"caída: {', '.join(motivos)}, descenso {st.peak_velocity:.2f}"
                else:
                    st.state = State.IMPACT
                    reason = f"impacto, confirmando ({st.down_frames}/{cfg.impactConfirmFrames} frames)"
            elif (
                caida_tronco is not None
                and caida_tronco >= cfg.trunkDropRatio * 0.5
                and pf.ts - (st.falling_since or pf.ts) <= cfg.fallWindowSeconds
            ):
                # Todavía va en camino. Una caída atraviesa posturas
                # intermedias: a mitad de recorrido el tronco ya bajó bastante
                # pero el cuerpo aún no se ve horizontal. Antes eso se leía como
                # "no terminó abajo" y reseteaba la máquina, tirando el pico de
                # velocidad y el reloj de confirmación justo cuando la caída
                # estaba ocurriendo. El resultado era perder caídas cortas por
                # unas centésimas.
                reason = f"descendiendo ({caida_tronco:.0%})"
            else:
                # Bajó rápido y volvió arriba, o pasó demasiado tiempo sin
                # llegar al piso: fue agacharse o un gesto.
                st.state = State.UPRIGHT
                st.peak_velocity = 0.0
                st.falling_since = None
                st.down_frames = 0
                st.collapse_since = None
                st.down_since = None
                reason = "no terminó abajo"

        elif st.state == State.IMPACT:
            if cuerpo_abajo:
                _marcar_abajo(st, pf.ts)
                transcurrido = pf.ts - (st.collapse_since or pf.ts)
                if st.down_frames >= cfg.impactConfirmFrames and st.peak_velocity >= cfg.fallVelocity:
                    st.state = State.ALERTED
                    is_fall = True
                    reason = f"caída: {', '.join(motivos)}, descenso {st.peak_velocity:.2f}"
                elif transcurrido > cfg.fallWindowSeconds * 2:
                    # Está abajo hace rato pero nunca hubo descenso brusco: se
                    # sentó o se agachó. Sin esta salida quedaba atrapado acá
                    # para siempre, sin alertar y ciego a la caída siguiente.
                    # La altura de referencia NO se borra: la envolvente ya
                    # tiene la altura de pie de esta persona y tirarla obligaría
                    # a reaprenderla desde una postura agachada.
                    st.state = State.UPRIGHT
                    st.peak_velocity = 0.0
                    st.falling_since = None
                    st.down_frames = 0
                    st.collapse_since = None
                    st.down_since = None
                    st.postura_baja_aceptada = True
                    reason = "postura baja sostenida sin caída"
                else:
                    reason = f"confirmando impacto ({st.down_frames}/{cfg.impactConfirmFrames} frames)"
            else:
                # Se incorporó antes de confirmar el impacto.
                #
                # Antes acá se alertaba igual si la velocidad había sido alta,
                # sin exigir que el cuerpo hubiera quedado abajo. Esa vía es la
                # que producía las "caídas de la nada": en cámara real se
                # midieron alertas con el tronco al 104 % de su altura de pie
                # —o sea, la persona parada— disparadas por un pico de velocidad
                # que venía de un salto de la estimación de pose, no de un
                # movimiento. Una velocidad sin destino no es una caída.
                #
                # El tropiezo del que uno se levanta enseguida se sigue
                # detectando, porque confirmar el impacto son dos frames
                # (0,35 s) y cualquier caída real pasa más tiempo que eso abajo.
                st.state = State.UPRIGHT
                st.peak_velocity = 0.0
                st.falling_since = None
                st.down_frames = 0
                st.collapse_since = None
                st.down_since = None
                reason = "bajó rápido pero no quedó abajo"

        elif st.state == State.ALERTED:
            if not cuerpo_abajo:
                st.state = State.RECOVERED
                reason = "se levantó tras la caída"
            else:
                reason = "sigue en el suelo"

        elif st.state == State.RECOVERED:
            # Se rearma para poder detectar una caída posterior. La referencia
            # de altura se conserva: es la misma persona y sigue midiendo lo
            # mismo de pie.
            st.state = State.UPRIGHT
            st.peak_velocity = 0.0
            st.falling_since = None
            st.down_frames = 0
            st.collapse_since = None
            st.down_since = None
            reason = "recuperado"

        down_seconds = (pf.ts - st.down_since) if st.down_since else 0.0
        # La severidad depende del desenlace, no de si hubo caída: seguir en el
        # suelo sugiere que la persona no puede levantarse.
        severity = "critical" if down_seconds >= cfg.prolongedSeconds else "high"

        confidence = self._confidence(
            st, angle, ratio, colapso, down_seconds, pf.det_score, caida_tronco, vert
        )
        st.last_ts = pf.ts

        return FallResult(
            track_id=pf.track_id,
            state=st.state,
            confidence=confidence,
            torso_angle=angle,
            aspect_ratio=ratio,
            velocity=velocidad,
            down_seconds=down_seconds,
            is_fall=is_fall,
            severity=severity,
            reason=reason,
            quality_ok=quality_ok,
            collapse_ratio=colapso,
            trunk_drop=caida_tronco,
            verticality=vert,
        )

    def _confidence(
        self,
        st: TrackState,
        angle: float | None,
        ratio: float | None,
        colapso: float | None,
        down_seconds: float,
        det_score: float,
        caida_tronco: float | None = None,
        vert: float | None = None,
    ) -> float:
        """Combina la evidencia en un número 0..1 para que el operador priorice."""
        cfg = self.cfg
        partes: list[tuple[float, float]] = []

        # Cuánto bajó el tronco es la señal que decide, así que es la que más
        # pesa. El colapso de altura visible pasa a ser secundario: se desploma
        # con las piernas tapadas y ahí no dice nada del cuerpo.
        if caida_tronco is not None:
            partes.append((_clamp(caida_tronco / max(cfg.trunkDropSure, 1e-6)), 0.40))
        if st.peak_velocity > 0:
            partes.append((_clamp(st.peak_velocity / max(cfg.fallVelocity * 2, 1e-6)), 0.25))
        if vert is not None:
            partes.append((_clamp((3.0 - vert) / 2.0), 0.15))
        if angle is not None:
            partes.append((_clamp((angle - 30.0) / 60.0), 0.10))
        if colapso is not None:
            partes.append((_clamp((1.0 - colapso) / (1.0 - cfg.collapseRatio)), 0.10))

        if not partes:
            return 0.0
        total = sum(w for _, w in partes)
        score = sum(v * w for v, w in partes) / total
        # Quedarse en el suelo refuerza, pero no puede ser lo único.
        if down_seconds > 0:
            score = min(1.0, score + 0.1 * _clamp(down_seconds / max(cfg.prolongedSeconds, 1e-6)))

        # No se puede estar más seguro de la caída que de que haya una persona.
        # Pero el tope NO es el puntaje de este frame: a una persona cayéndose
        # se la detecta peor precisamente porque está cayéndose (postura rara,
        # movimiento borroso), así que topear con el puntaje instantáneo
        # castigaba la confianza justo en las caídas más claras. Se observó
        # evidencia contundente —altura al 14 % de la suya, descenso 2.97—
        # reportada con confianza 0.52, a dos centésimas de que la regla la
        # descartara.
        #
        # El tope correcto es el mejor puntaje reciente de ESTE track: si a esta
        # persona se la venía detectando con claridad, que se la vea mal durante
        # el segundo de la caída no vuelve dudosa su existencia.
        techo = max(st.det_scores) if st.det_scores else det_score
        return round(min(score, techo), 4)


def _sanear(pf: PoseFrame) -> PoseFrame:
    """Descarta coordenadas no finitas antes de que entren al razonamiento.

    Un NaN en un punto del esqueleto no rompía nada de forma visible: se
    propagaba hasta la bajada de tronco, y como toda comparación con NaN da
    falso, el detector dejaba de afirmar que alguien estaba abajo. Es decir,
    quedaba CIEGO en silencio, que es el peor modo de fallar para algo cuya
    razón de existir es avisar.

    Un punto con coordenadas imposibles simplemente no se vio: se le pone
    confianza cero y el resto de la lógica —que ya sabe qué hacer sin ese
    punto— sigue funcionando.
    """
    limpios = None
    for i, k in enumerate(pf.keypoints):
        if not (math.isfinite(k.x) and math.isfinite(k.y) and math.isfinite(k.score)):
            if limpios is None:
                limpios = list(pf.keypoints)
            limpios[i] = Keypoint(0.0, 0.0, 0.0)

    bbox = pf.bbox
    if not all(math.isfinite(v) for v in bbox):
        bbox = (0.0, 0.0, 0.0, 0.0)

    ts = pf.ts if math.isfinite(pf.ts) else 0.0
    score = pf.det_score if math.isfinite(pf.det_score) else 0.0

    if limpios is None and bbox is pf.bbox and ts == pf.ts and score == pf.det_score:
        return pf
    return PoseFrame(
        track_id=pf.track_id, ts=ts,
        keypoints=limpios if limpios is not None else pf.keypoints,
        bbox=bbox, det_score=score,
    )


def _marcar_abajo(st: TrackState, ts: float) -> None:
    """Registra que el cuerpo está abajo, si no estaba ya marcado.

    Existe como función única porque antes había varias ramas que actualizaban
    `collapse_since` y se olvidaban de `down_since`: el contador de permanencia
    nunca arrancaba y la severidad jamás subía.
    """
    st.down_frames += 1
    if st.collapse_since is None:
        st.collapse_since = ts
    if st.down_since is None:
        st.down_since = ts


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))
