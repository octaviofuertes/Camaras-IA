"""Lógica de detección de caídas a partir de pose. Sin dependencias de YOLO.

Por qué pose y no la caja del cuerpo: con sólo el rectángulo, cualquiera que se
agache, se siente en el piso o se estire genera la misma señal que una caída.
Los puntos del esqueleto dan la ORIENTACIÓN del torso, que es lo que distingue
"está horizontal" de "está agachado".

Cómo decide (esto es lo que separa un detector serio de una demo):

  1. DESCENSO RÁPIDO   — la cadera baja a gran velocidad (normalizada por la
                         altura de la persona, así funciona igual de cerca o lejos).
  2. CAMBIO DE POSTURA — el torso pasa de vertical a horizontal.
  3. PERMANENCIA       — y, sobre todo, SIGUE EN EL SUELO unos segundos.

El punto 3 es el que elimina los falsos positivos: agacharse a atarse los
cordones cumple 1 y 2, pero la persona se levanta enseguida. Sólo se alerta si
sigue caída pasada la ventana de confirmación.

Este módulo mantiene estado temporal por persona porque una caída NO se puede
ver en un frame aislado: es percepción, no regla de negocio. Las reglas de
negocio (umbral de confianza, horarios, zonas, enfriamiento) siguen viviendo en
`rules-engine`, como manda el contrato.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

# Índices de los 17 puntos del esqueleto (formato COCO).
NOSE = 0
L_SHOULDER, R_SHOULDER = 5, 6
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16

# Puntos mínimos para poder afirmar algo sobre la postura.
TORSO_POINTS = (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP)


class State(str, Enum):
    UPRIGHT = "upright"      # de pie o en actividad normal
    FALLING = "falling"      # descenso brusco en curso
    DOWN = "down"            # en el suelo, contando para confirmar
    ALERTED = "alerted"      # ya se emitió la alerta de esta caída


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
    """Parámetros del detector. Todos con un porqué, no números mágicos."""

    # Confianza mínima de un punto del esqueleto para usarlo.
    keypointScore: float = 0.3
    # Puntos de torso visibles mínimos (de 4) para juzgar la postura.
    minTorsoPoints: int = 3

    # Ángulo del torso respecto de la vertical a partir del cual se considera
    # "en el suelo". 90° es completamente horizontal.
    downAngleDeg: float = 55.0
    # Relación alto/ancho de la caja por debajo de la cual el cuerpo está tendido.
    downRatio: float = 1.0

    # Velocidad de descenso que dispara la sospecha, en "alturas de cuerpo por
    # segundo". Normalizar por la altura hace que funcione igual cerca y lejos.
    fallVelocity: float = 0.7

    # Segundos que debe permanecer en el suelo para confirmar la caída.
    # Es el filtro principal contra falsos positivos.
    confirmSeconds: float = 3.0
    # Si se levanta antes de esto, se descarta sin alertar.
    recoverySeconds: float = 2.0

    # Movimiento máximo (altura de cuerpo por segundo) para considerarla inmóvil.
    stillnessVelocity: float = 0.15

    # Confianza mínima del resultado para reportarlo.
    minConfidence: float = 0.55

    # Cuánto tiempo se recuerda a una persona que dejó de verse.
    trackTimeoutSeconds: float = 5.0


@dataclass
class TrackState:
    state: State = State.UPRIGHT
    last_ts: float = 0.0
    last_hip_y: float | None = None
    last_body_h: float | None = None
    peak_velocity: float = 0.0
    down_since: float | None = None
    falling_since: float | None = None
    still_since: float | None = None
    history: list[tuple[float, float]] = field(default_factory=list)  # (ts, hip_y)


@dataclass
class FallResult:
    """Resultado por persona en un frame."""
    track_id: int
    state: State
    confidence: float
    torso_angle: float | None
    aspect_ratio: float | None
    velocity: float
    down_seconds: float
    is_fall: bool          # True sólo en el frame en que se confirma
    reason: str
    quality_ok: bool       # False si la pose no alcanza para juzgar


def _mid(a: Keypoint, b: Keypoint) -> tuple[float, float]:
    return ((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)


def torso_angle_deg(kps: list[Keypoint], min_score: float) -> float | None:
    """Ángulo del torso respecto de la vertical: 0° de pie, 90° tendido.

    Devuelve None si no se ven suficientes puntos para afirmarlo.
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

    dx = hx - sx
    dy = hy - sy
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return None

    # Ángulo contra el eje vertical: atan2(horizontal, vertical).
    return math.degrees(math.atan2(abs(dx), abs(dy)))


def visible_torso_points(kps: list[Keypoint], min_score: float) -> int:
    return sum(1 for i in TORSO_POINTS if i < len(kps) and kps[i].score >= min_score)


def body_height(kps: list[Keypoint], bbox: tuple[float, float, float, float], min_score: float) -> float:
    """Altura de referencia de la persona, para normalizar velocidades."""
    ys = [kps[i].y for i in range(len(kps)) if kps[i].score >= min_score]
    if len(ys) >= 2:
        span = max(ys) - min(ys)
        if span > 0.01:
            return span
    # Sin pose fiable, la diagonal de la caja es una aproximación razonable.
    _, _, w, h = bbox
    return max(math.hypot(w, h), 0.05)


class FallDetector:
    """Máquina de estados por persona seguida."""

    def __init__(self, config: FallConfig | None = None) -> None:
        self.cfg = config or FallConfig()
        self.tracks: dict[int, TrackState] = {}

    def _hip_y(self, kps: list[Keypoint]) -> float | None:
        ok = [i for i in (L_HIP, R_HIP) if i < len(kps) and kps[i].score >= self.cfg.keypointScore]
        if not ok:
            return None
        return sum(kps[i].y for i in ok) / len(ok)

    def purge(self, now: float) -> None:
        """Olvida a quien dejó de verse (evita que el estado crezca sin fin)."""
        stale = [t for t, s in self.tracks.items() if now - s.last_ts > self.cfg.trackTimeoutSeconds]
        for t in stale:
            del self.tracks[t]

    def update(self, pf: PoseFrame) -> FallResult:
        cfg = self.cfg
        st = self.tracks.setdefault(pf.track_id, TrackState(last_ts=pf.ts))

        angle = torso_angle_deg(pf.keypoints, cfg.keypointScore)
        n_torso = visible_torso_points(pf.keypoints, cfg.keypointScore)
        _, _, bw, bh = pf.bbox
        ratio = (bh / bw) if bw > 1e-6 else None
        h_ref = body_height(pf.keypoints, pf.bbox, cfg.keypointScore)
        hip_y = self._hip_y(pf.keypoints)

        dt = max(pf.ts - st.last_ts, 1e-3)

        # Velocidad vertical en alturas de cuerpo por segundo (positiva = baja).
        velocity = 0.0
        if hip_y is not None and st.last_hip_y is not None and h_ref > 1e-6:
            velocity = ((hip_y - st.last_hip_y) / h_ref) / dt

        quality_ok = n_torso >= cfg.minTorsoPoints and angle is not None

        # Postura tendida: por ángulo del torso o, si la pose falla, por la caja.
        posture_down = False
        if angle is not None:
            posture_down = angle >= cfg.downAngleDeg
        if ratio is not None and ratio < cfg.downRatio:
            posture_down = posture_down or (angle is None)

        moving = abs(velocity) > cfg.stillnessVelocity
        reason = ""
        is_fall = False

        # ── máquina de estados ───────────────────────────────────────
        if st.state in (State.UPRIGHT, State.FALLING):
            if velocity > cfg.fallVelocity:
                st.peak_velocity = max(st.peak_velocity, velocity)
                if st.state == State.UPRIGHT:
                    st.state = State.FALLING
                    st.falling_since = pf.ts
                    reason = "descenso rápido"

            if posture_down and quality_ok:
                # Llegó al suelo: puede venir de un descenso brusco (caída) o de
                # bajar despacio (acostarse). Se registran ambos, la permanencia
                # decide.
                st.state = State.DOWN
                st.down_since = pf.ts
                st.still_since = pf.ts if not moving else None
                reason = "postura horizontal"
            elif st.state == State.FALLING and st.falling_since is not None:
                # No terminó en el suelo dentro de la ventana: no era una caída.
                if pf.ts - st.falling_since > cfg.recoverySeconds:
                    st.state = State.UPRIGHT
                    st.peak_velocity = 0.0
                    st.falling_since = None
                    reason = "se recuperó"

        elif st.state == State.DOWN:
            if not posture_down:
                # Se levantó antes de confirmar: NO se alerta. Este es el caso
                # de agacharse, atarse los cordones o sentarse en el piso.
                st.state = State.UPRIGHT
                st.down_since = None
                st.still_since = None
                st.peak_velocity = 0.0
                reason = "se levantó antes de confirmar"
            else:
                if moving:
                    st.still_since = None
                elif st.still_since is None:
                    st.still_since = pf.ts

                down_for = pf.ts - (st.down_since or pf.ts)
                if down_for >= cfg.confirmSeconds:
                    st.state = State.ALERTED
                    is_fall = True
                    reason = f"en el suelo {down_for:.1f}s"

        elif st.state == State.ALERTED:
            if not posture_down:
                # Se levantó: se rearma para poder detectar una caída futura.
                st.state = State.UPRIGHT
                st.down_since = None
                st.still_since = None
                st.peak_velocity = 0.0
                reason = "recuperado tras la alerta"
            else:
                reason = "sigue en el suelo (ya alertado)"

        down_seconds = (pf.ts - st.down_since) if st.down_since else 0.0
        confidence = self._confidence(st, angle, ratio, down_seconds, pf.det_score)

        st.last_ts = pf.ts
        st.last_hip_y = hip_y if hip_y is not None else st.last_hip_y
        st.last_body_h = h_ref
        st.history.append((pf.ts, hip_y if hip_y is not None else 0.0))
        if len(st.history) > 90:
            st.history.pop(0)

        return FallResult(
            track_id=pf.track_id,
            state=st.state,
            confidence=confidence,
            torso_angle=angle,
            aspect_ratio=ratio,
            velocity=velocity,
            down_seconds=down_seconds,
            is_fall=is_fall,
            reason=reason,
            quality_ok=quality_ok,
        )

    def _confidence(
        self,
        st: TrackState,
        angle: float | None,
        ratio: float | None,
        down_seconds: float,
        det_score: float,
    ) -> float:
        """Combina la evidencia disponible en un único número 0..1.

        No es un umbral binario: un evento con 0,60 y otro con 0,95 son distintos
        para el operador que después revisa la alerta.
        """
        cfg = self.cfg
        parts: list[tuple[float, float]] = []  # (valor, peso)

        if angle is not None:
            # 45° empieza a ser sospechoso, 90° es tendido del todo.
            parts.append((_clamp((angle - 45.0) / 45.0), 0.35))
        if ratio is not None:
            # 1.6 es una persona de pie; por debajo de 0.9 está tendida.
            parts.append((_clamp((1.6 - ratio) / 0.7), 0.20))
        if st.peak_velocity > 0:
            parts.append((_clamp(st.peak_velocity / max(cfg.fallVelocity, 1e-6)), 0.20))
        if down_seconds > 0:
            parts.append((_clamp(down_seconds / max(cfg.confirmSeconds, 1e-6)), 0.25))

        if not parts:
            return 0.0
        total_w = sum(w for _, w in parts)
        score = sum(v * w for v, w in parts) / total_w
        # La confianza de la propia detección de persona acota el resultado:
        # si el detector apenas ve a la persona, la caída no puede ser certera.
        return round(min(score, det_score), 4)


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))
