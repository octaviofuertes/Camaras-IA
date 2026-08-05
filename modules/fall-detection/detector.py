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
1. COLAPSO DE ALTURA. Se aprende la altura habitual de CADA persona mientras
   está de pie, y se mide cuánto se desploma respecto de SU propia referencia.
   Esto funciona sin importar la distancia a la cámara ni la estatura.

2. DESCENSO RÁPIDO. Es lo único que separa una caída de agacharse: agacharse
   también baja el cuerpo, pero despacio y de forma controlada.

3. POSTURA BAJA SOSTENIDA (menos de un segundo). Evita alertar por un frame
   ruidoso o una pose mal estimada, sin exigir que la persona se quede tirada.

POR QUÉ NO ALCANZA EL ÁNGULO DEL TORSO
--------------------------------------
El ángulo se mide en la imagen, no en el mundo. Si alguien cae HACIA la cámara
o alejándose, su torso sigue proyectándose casi vertical aunque esté horizontal
en el piso. Por eso el ángulo aporta, pero el colapso de altura es la señal
principal: esa sí funciona en cualquier orientación.
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

    # ── señal 1: colapso de altura ──────────────────────────────────
    # La persona pasa a ocupar menos de esta fracción de su altura habitual.
    # 0.65 tolera agacharse un poco sin marcar caída.
    collapseRatio: float = 0.65
    # Frames de pie necesarios para fijar la altura de referencia. Hasta
    # entonces no se puede afirmar un colapso: no hay con qué comparar.
    baselineFrames: int = 5

    # ── señal 2: descenso rápido ────────────────────────────────────
    # En alturas de cuerpo por segundo. Agacharse ronda 0.2-0.4; una caída
    # supera holgadamente 0.8.
    fallVelocity: float = 0.55
    # Ventana en la que se busca el descenso: una caída dura menos de 1 s.
    fallWindowSeconds: float = 1.2

    # ── señal 3: confirmación breve del impacto ─────────────────────
    # NO es permanencia: sólo evita alertar por un frame ruidoso. A 6 fps son
    # dos frames, que ya descartan una pose mal estimada aislada sin retrasar
    # la alerta de un tropiezo del que la persona se levanta enseguida.
    impactConfirmSeconds: float = 0.35

    # ── severidad ───────────────────────────────────────────────────
    # Si sigue en el suelo más que esto, la caída se considera grave
    # (posible pérdida de conocimiento o imposibilidad de levantarse).
    prolongedSeconds: float = 6.0

    # Ángulo de torso que refuerza la evidencia (no es obligatorio).
    downAngleDeg: float = 50.0
    # Relación alto/ancho por debajo de la cual el cuerpo está tendido.
    downRatio: float = 1.1

    minConfidence: float = 0.5
    trackTimeoutSeconds: float = 5.0

    # Compatibilidad con configuraciones anteriores; ya no gatea la alerta.
    confirmSeconds: float = 0.5
    recoverySeconds: float = 2.0
    stillnessVelocity: float = 0.15


@dataclass
class TrackState:
    state: State = State.UPRIGHT
    last_ts: float = 0.0

    # Altura de referencia de ESTA persona estando de pie.
    baseline_heights: Deque[float] = field(default_factory=lambda: deque(maxlen=30))
    baseline: float | None = None

    # Historia reciente (ts, altura, centro_y) para medir el descenso.
    history: Deque[tuple[float, float, float]] = field(default_factory=lambda: deque(maxlen=60))

    peak_velocity: float = 0.0
    collapse_since: float | None = None   # desde cuándo está colapsado
    down_since: float | None = None       # desde cuándo está abajo (severidad)
    alerted_at: float | None = None


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
        """Mayor velocidad de descenso dentro de la ventana de caída.

        Se comparan TODOS los pares de instantes de la ventana, no cada frame
        contra el primero. La diferencia importa: si la persona estuvo quieta
        medio segundo y recién ahí se cayó, medir contra el inicio de la ventana
        reparte la caída sobre todo ese tiempo y diluye la velocidad justo por
        debajo del umbral. Buscando el mejor par se encuentra el tramo real de
        caída, dure lo que dure.

        La ventana es corta (~1 s) y el fps bajo, así que son unos pocos frames:
        el costo cuadrático es irrelevante.
        """
        cfg = self.cfg
        recientes = [(t, c) for (t, _, c) in st.history if now - t <= cfg.fallWindowSeconds]
        if len(recientes) < 2:
            return 0.0

        ref = max(st.baseline or 0.3, 1e-6)
        peor = 0.0
        for i, (t0, c0) in enumerate(recientes[:-1]):
            for (t1, c1) in recientes[i + 1:]:
                dt = t1 - t0
                if dt <= 1e-3:
                    continue
                peor = max(peor, ((c1 - c0) / ref) / dt)
        return peor

    def update(self, pf: PoseFrame) -> FallResult:
        cfg = self.cfg
        st = self.tracks.setdefault(pf.track_id, TrackState(last_ts=pf.ts))

        angle = torso_angle_deg(pf.keypoints, cfg.keypointScore)
        n_torso = visible_torso_points(pf.keypoints, cfg.keypointScore)
        _, _, bw, bh = pf.bbox
        ratio = (bh / bw) if bw > 1e-6 else None

        altura = person_height(pf.keypoints, pf.bbox, cfg.keypointScore)
        centro_y = _mid_y(pf.keypoints, L_HIP, R_HIP, cfg.keypointScore)
        if centro_y is None:
            centro_y = pf.bbox[1] + pf.bbox[3] / 2

        # Una persona demasiado chica en la imagen se juzga por su altura de
        # referencia, no por la altura del frame: si ya está caída ocupa poco,
        # y descartarla por eso sería perder justo la caída que interesa.
        referencia = st.baseline or altura
        quality_ok = n_torso >= cfg.minTorsoPoints and referencia >= cfg.minPersonHeight

        st.history.append((pf.ts, altura, centro_y))
        velocidad = self._descenso_reciente(st, pf.ts)

        if not quality_ok:
            # Sin pose confiable no se afirma nada: no se aprende la altura de
            # referencia, no se avanza de estado y no se alerta. Mantener el
            # estado congelado es lo correcto — una oclusión momentánea no debe
            # borrar una caída en curso ni inventar una nueva.
            st.last_ts = pf.ts
            return FallResult(
                track_id=pf.track_id, state=st.state, confidence=0.0,
                torso_angle=angle, aspect_ratio=ratio, velocity=velocidad,
                down_seconds=(pf.ts - st.down_since) if st.down_since else 0.0,
                is_fall=False, severity="high", reason="pose insuficiente",
                quality_ok=False,
                collapse_ratio=(altura / st.baseline) if st.baseline else None,
            )

        # ── altura de referencia: sólo se aprende estando de pie ────────
        if st.state == State.UPRIGHT and quality_ok:
            st.baseline_heights.append(altura)
            if len(st.baseline_heights) >= cfg.baselineFrames:
                ordenadas = sorted(st.baseline_heights)
                # Mediana: resiste un frame raro sin arrastrar la referencia.
                st.baseline = ordenadas[len(ordenadas) // 2]

        colapso = None
        if st.baseline and st.baseline > 1e-6:
            colapso = altura / st.baseline

        # ── ¿el cuerpo está abajo? ──────────────────────────────────────
        # Tres formas de estarlo; alcanza con una, porque cada cámara ve
        # distinto según su ángulo.
        cuerpo_abajo = False
        motivos = []
        if colapso is not None and colapso <= cfg.collapseRatio:
            cuerpo_abajo = True
            motivos.append(f"altura al {colapso*100:.0f}%")
        if angle is not None and angle >= cfg.downAngleDeg:
            cuerpo_abajo = True
            motivos.append(f"torso {angle:.0f}°")
        if ratio is not None and ratio <= cfg.downRatio:
            cuerpo_abajo = True
            motivos.append(f"silueta {ratio:.1f}")

        descenso_brusco = velocidad >= cfg.fallVelocity
        if descenso_brusco:
            st.peak_velocity = max(st.peak_velocity, velocidad)

        is_fall = False
        reason = ""

        # ── máquina de estados ──────────────────────────────────────────
        if st.state == State.UPRIGHT:
            if descenso_brusco:
                st.state = State.FALLING
                st.collapse_since = None
                reason = f"descenso rápido ({velocidad:.2f})"
            elif cuerpo_abajo and st.baseline is not None:
                # Llegó abajo sin que se viera el descenso (puede haber pasado
                # entre dos frames): igual se vigila.
                st.state = State.FALLING
                _marcar_abajo(st, pf.ts)
                reason = "cuerpo abajo"

        elif st.state == State.FALLING:
            if cuerpo_abajo:
                _marcar_abajo(st, pf.ts)
                transcurrido = pf.ts - (st.collapse_since or pf.ts)

                # Se alerta acá: hubo descenso brusco y el cuerpo quedó abajo.
                # NO se espera a ver si se queda tirado.
                if transcurrido >= cfg.impactConfirmSeconds and st.peak_velocity >= cfg.fallVelocity:
                    st.state = State.ALERTED
                    st.alerted_at = pf.ts
                    is_fall = True
                    reason = f"caída: {', '.join(motivos)}, descenso {st.peak_velocity:.2f}"
                else:
                    st.state = State.IMPACT
                    reason = f"impacto, confirmando ({transcurrido:.1f}s)"
            else:
                # Bajó rápido pero no terminó abajo: fue agacharse o un gesto.
                st.state = State.UPRIGHT
                st.peak_velocity = 0.0
                st.collapse_since = None
                st.down_since = None
                reason = "no terminó abajo"

        elif st.state == State.IMPACT:
            if cuerpo_abajo:
                _marcar_abajo(st, pf.ts)
                transcurrido = pf.ts - (st.collapse_since or pf.ts)
                if transcurrido >= cfg.impactConfirmSeconds and st.peak_velocity >= cfg.fallVelocity:
                    st.state = State.ALERTED
                    st.alerted_at = pf.ts
                    is_fall = True
                    reason = f"caída: {', '.join(motivos)}, descenso {st.peak_velocity:.2f}"
                elif transcurrido > cfg.fallWindowSeconds * 2:
                    # Está abajo hace rato pero nunca hubo descenso brusco: se
                    # sentó o se agachó. Sin esta salida quedaba atrapado acá
                    # para siempre, sin alertar y sin volver a aprender su
                    # altura de referencia — o sea, ciego a la caída siguiente.
                    st.state = State.UPRIGHT
                    st.peak_velocity = 0.0
                    st.collapse_since = None
                    st.down_since = None
                    st.baseline_heights.clear()
                    st.baseline = None
                    reason = "postura baja sostenida sin caída"
                else:
                    reason = f"confirmando impacto ({transcurrido:.1f}s)"
            else:
                # Se levantó antes de confirmar: si el descenso fue muy brusco
                # igual se alerta —un tropiezo con recuperación inmediata sigue
                # siendo una caída—; si fue suave, era agacharse.
                if st.peak_velocity >= cfg.fallVelocity * 1.6:
                    st.state = State.ALERTED
                    st.alerted_at = pf.ts
                    is_fall = True
                    reason = f"caída con recuperación inmediata (descenso {st.peak_velocity:.2f})"
                else:
                    st.state = State.UPRIGHT
                    st.peak_velocity = 0.0
                    st.collapse_since = None
                    st.down_since = None
                    reason = "se recuperó, sin caída"

        elif st.state == State.ALERTED:
            if not cuerpo_abajo:
                st.state = State.RECOVERED
                reason = "se levantó tras la caída"
            else:
                reason = "sigue en el suelo"

        elif st.state == State.RECOVERED:
            # Se rearma para poder detectar una caída posterior.
            st.state = State.UPRIGHT
            st.peak_velocity = 0.0
            st.collapse_since = None
            st.down_since = None
            st.baseline_heights.clear()
            reason = "recuperado"

        down_seconds = (pf.ts - st.down_since) if st.down_since else 0.0
        # La severidad depende del desenlace, no de si hubo caída: seguir en el
        # suelo sugiere que la persona no puede levantarse.
        severity = "critical" if down_seconds >= cfg.prolongedSeconds else "high"

        confidence = self._confidence(st, angle, ratio, colapso, down_seconds, pf.det_score)
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
        )

    def _confidence(
        self,
        st: TrackState,
        angle: float | None,
        ratio: float | None,
        colapso: float | None,
        down_seconds: float,
        det_score: float,
    ) -> float:
        """Combina la evidencia en un número 0..1 para que el operador priorice."""
        cfg = self.cfg
        partes: list[tuple[float, float]] = []

        # El colapso de altura es la señal más confiable: pesa más.
        if colapso is not None:
            partes.append((_clamp((1.0 - colapso) / (1.0 - cfg.collapseRatio)), 0.35))
        if st.peak_velocity > 0:
            partes.append((_clamp(st.peak_velocity / max(cfg.fallVelocity * 2, 1e-6)), 0.30))
        if angle is not None:
            partes.append((_clamp((angle - 30.0) / 60.0), 0.20))
        if ratio is not None:
            partes.append((_clamp((1.8 - ratio) / 1.0), 0.15))

        if not partes:
            return 0.0
        total = sum(w for _, w in partes)
        score = sum(v * w for v, w in partes) / total
        # Quedarse en el suelo refuerza, pero no puede ser lo único.
        if down_seconds > 0:
            score = min(1.0, score + 0.1 * _clamp(down_seconds / max(cfg.prolongedSeconds, 1e-6)))
        return round(min(score, det_score), 4)


def _marcar_abajo(st: TrackState, ts: float) -> None:
    """Registra que el cuerpo está abajo, si no estaba ya marcado.

    Existe como función única porque antes había varias ramas que actualizaban
    `collapse_since` y se olvidaban de `down_since`: el contador de permanencia
    nunca arrancaba y la severidad jamás subía.
    """
    if st.collapse_since is None:
        st.collapse_since = ts
    if st.down_since is None:
        st.down_since = ts


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))
