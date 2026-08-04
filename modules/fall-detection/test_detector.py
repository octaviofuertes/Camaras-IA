"""Pruebas del detector de caídas con trayectorias sintéticas.

Cada prueba simula una secuencia de poses que representa una situación real.
Esto permite verificar el comportamiento SIN tener que tirarse al piso, y sobre
todo comprobar los casos que separan un detector usable de uno que satura al
operador con falsas alarmas: agacharse, sentarse, caminar.
"""
from __future__ import annotations

import math

from detector import (
    FallConfig,
    FallDetector,
    Keypoint,
    PoseFrame,
    State,
    torso_angle_deg,
)

FPS = 10.0
DT = 1.0 / FPS


def make_pose(
    hip_y: float,
    angle_deg: float = 0.0,
    height: float = 0.5,
    visible: bool = True,
    center_x: float = 0.5,
) -> list[Keypoint]:
    """Construye un esqueleto con el torso inclinado `angle_deg` de la vertical.

    0° = de pie (hombros encima de la cadera). 90° = tendido (hombros al lado).
    """
    score = 0.9 if visible else 0.05
    torso = height * 0.4
    rad = math.radians(angle_deg)
    # Desde la cadera hacia los hombros: vertical si el ángulo es 0.
    sx = center_x - torso * math.sin(rad)
    sy = hip_y - torso * math.cos(rad)

    kps = [Keypoint(center_x, hip_y, 0.1) for _ in range(17)]
    kps[0] = Keypoint(sx, sy - torso * 0.35, score)            # nariz
    kps[5] = Keypoint(sx - 0.04, sy, score)                     # hombro izq
    kps[6] = Keypoint(sx + 0.04, sy, score)                     # hombro der
    kps[11] = Keypoint(center_x - 0.03, hip_y, score)           # cadera izq
    kps[12] = Keypoint(center_x + 0.03, hip_y, score)           # cadera der
    kps[13] = Keypoint(center_x - 0.03, hip_y + height * 0.25, score)  # rodilla izq
    kps[14] = Keypoint(center_x + 0.03, hip_y + height * 0.25, score)  # rodilla der
    kps[15] = Keypoint(center_x - 0.03, hip_y + height * 0.5, score)   # tobillo izq
    kps[16] = Keypoint(center_x + 0.03, hip_y + height * 0.5, score)   # tobillo der
    return kps


def bbox_for(angle_deg: float, height: float = 0.5) -> tuple[float, float, float, float]:
    """Caja coherente con la postura: de pie es alta y angosta; tendida al revés."""
    rad = math.radians(angle_deg)
    h = height * max(math.cos(rad), 0.12)
    w = height * max(math.sin(rad), 0.18)
    return (0.4, 0.3, w, h)


def run(detector: FallDetector, frames: list[tuple[float, float, float]], track: int = 1):
    """Ejecuta una secuencia de (ts, hip_y, angle) y devuelve todos los resultados."""
    out = []
    for ts, hip_y, angle in frames:
        pf = PoseFrame(
            track_id=track,
            ts=ts,
            keypoints=make_pose(hip_y, angle),
            bbox=bbox_for(angle),
            det_score=0.92,
        )
        out.append(detector.update(pf))
    return out


def seq_standing(t0: float, seconds: float, hip_y: float = 0.5):
    n = int(seconds * FPS)
    return [(t0 + i * DT, hip_y, 3.0) for i in range(n)]


def seq_transition(t0: float, seconds: float, y0: float, y1: float, a0: float, a1: float):
    """Interpola posición y ángulo: sirve para caer, agacharse o levantarse."""
    n = max(int(seconds * FPS), 1)
    return [
        (t0 + i * DT, y0 + (y1 - y0) * (i / n), a0 + (a1 - a0) * (i / n))
        for i in range(n)
    ]


def seq_lying(t0: float, seconds: float, hip_y: float = 0.85):
    n = int(seconds * FPS)
    return [(t0 + i * DT, hip_y, 86.0) for i in range(n)]


# ═══════════════════════════════════════════════════════════════════
# Casos
# ═══════════════════════════════════════════════════════════════════

def test_angulo_de_torso():
    """La medición de postura debe ser correcta antes que nada."""
    de_pie = torso_angle_deg(make_pose(0.5, 0.0), 0.3)
    tendido = torso_angle_deg(make_pose(0.8, 90.0), 0.3)
    assert de_pie is not None and de_pie < 10, f"de pie deberia ser ~0°, dio {de_pie}"
    assert tendido is not None and tendido > 80, f"tendido deberia ser ~90°, dio {tendido}"


def test_caida_real_alerta():
    """Caída: de pie -> descenso brusco -> horizontal -> sigue en el suelo."""
    d = FallDetector(FallConfig(confirmSeconds=3.0))
    frames = (
        seq_standing(0.0, 2.0)
        + seq_transition(2.0, 0.5, 0.5, 0.85, 3.0, 88.0)   # caída rápida
        + seq_lying(2.5, 5.0)                                # queda tendido
    )
    res = run(d, frames)
    alertas = [r for r in res if r.is_fall]
    assert alertas, "una caída con permanencia en el suelo DEBE alertar"
    a = alertas[0]
    assert a.confidence >= 0.55, f"confianza demasiado baja: {a.confidence}"
    assert len(alertas) == 1, "debe alertar UNA sola vez, no en cada frame"


def test_agacharse_no_alerta():
    """Atarse los cordones: baja y se levanta enseguida. NO debe alertar.

    Es el falso positivo clásico de los detectores basados sólo en la caja.
    """
    d = FallDetector(FallConfig(confirmSeconds=3.0))
    frames = (
        seq_standing(0.0, 2.0)
        + seq_transition(2.0, 0.6, 0.5, 0.75, 3.0, 70.0)   # se agacha
        + [(2.6 + i * DT, 0.75, 70.0) for i in range(10)]   # 1 s abajo
        + seq_transition(3.6, 0.6, 0.75, 0.5, 70.0, 3.0)    # se levanta
        + seq_standing(4.2, 2.0)
    )
    res = run(d, frames)
    assert not any(r.is_fall for r in res), "agacharse y levantarse NO debe alertar"


def test_sentarse_en_el_piso_lento_no_dispara_por_velocidad():
    """Sentarse despacio y volver a levantarse tampoco alerta."""
    d = FallDetector(FallConfig(confirmSeconds=3.0))
    frames = (
        seq_standing(0.0, 2.0)
        + seq_transition(2.0, 2.5, 0.5, 0.8, 3.0, 65.0)     # baja lento
        + [(4.5 + i * DT, 0.8, 65.0) for i in range(15)]     # 1,5 s abajo
        + seq_transition(6.0, 1.5, 0.8, 0.5, 65.0, 3.0)      # se levanta
    )
    res = run(d, frames)
    assert not any(r.is_fall for r in res), "sentarse y levantarse no es una caída"


def test_caminar_no_alerta():
    """Movimiento normal de pie: nunca debe alertar."""
    d = FallDetector()
    frames = [(i * DT, 0.5 + 0.01 * math.sin(i / 3.0), 4.0) for i in range(120)]
    res = run(d, frames)
    assert not any(r.is_fall for r in res)
    assert all(r.state == State.UPRIGHT for r in res[-10:])


def test_se_levanta_antes_de_confirmar_no_alerta():
    """Cae pero se incorpora antes de la ventana: no se confirma."""
    d = FallDetector(FallConfig(confirmSeconds=4.0))
    frames = (
        seq_standing(0.0, 1.5)
        + seq_transition(1.5, 0.4, 0.5, 0.85, 3.0, 88.0)     # cae
        + seq_lying(1.9, 2.0)                                 # 2 s en el suelo
        + seq_transition(3.9, 0.8, 0.85, 0.5, 88.0, 3.0)      # se levanta
        + seq_standing(4.7, 2.0)
    )
    res = run(d, frames)
    assert not any(r.is_fall for r in res), "si se levanta antes de confirmar, no se alerta"
    assert res[-1].state == State.UPRIGHT


def test_pose_insuficiente_no_afirma():
    """Con el esqueleto casi invisible, el detector NO debe afirmar una caída."""
    d = FallDetector()
    out = []
    for i in range(40):
        pf = PoseFrame(
            track_id=7,
            ts=i * DT,
            keypoints=make_pose(0.85, 88.0, visible=False),  # puntos con score 0.05
            bbox=bbox_for(88.0),
            det_score=0.9,
        )
        out.append(d.update(pf))
    assert not any(r.is_fall for r in out), "sin pose fiable no se puede afirmar una caída"
    assert all(not r.quality_ok for r in out)


def test_segunda_caida_se_detecta():
    """Tras recuperarse, una nueva caída vuelve a alertar (el estado se rearma)."""
    d = FallDetector(FallConfig(confirmSeconds=2.0))
    frames = (
        seq_standing(0.0, 1.0)
        + seq_transition(1.0, 0.4, 0.5, 0.85, 3.0, 88.0)
        + seq_lying(1.4, 3.0)                                 # 1ª caída -> alerta
        + seq_transition(4.4, 0.8, 0.85, 0.5, 88.0, 3.0)      # se levanta
        + seq_standing(5.2, 1.5)
        + seq_transition(6.7, 0.4, 0.5, 0.85, 3.0, 88.0)
        + seq_lying(7.1, 3.0)                                 # 2ª caída -> alerta
    )
    res = run(d, frames)
    alertas = [r for r in res if r.is_fall]
    assert len(alertas) == 2, f"deberían detectarse 2 caídas, se detectaron {len(alertas)}"


def test_personas_independientes():
    """El estado es por persona: la caída de una no afecta a la otra."""
    d = FallDetector(FallConfig(confirmSeconds=2.0))
    for i in range(60):
        ts = i * DT
        # Persona 1 cae a los 1,0 s; persona 2 sigue de pie todo el tiempo.
        if ts < 1.0:
            y1, a1 = 0.5, 3.0
        else:
            y1, a1 = 0.85, 88.0
        d.update(PoseFrame(1, ts, make_pose(y1, a1), bbox_for(a1), 0.9))
        r2 = d.update(PoseFrame(2, ts, make_pose(0.5, 3.0), bbox_for(3.0), 0.9))
        assert not r2.is_fall, "la persona 2 nunca se cayó"
    assert d.tracks[1].state == State.ALERTED
    assert d.tracks[2].state == State.UPRIGHT


def test_confianza_crece_con_la_evidencia():
    """Más tiempo en el suelo y postura más horizontal => más confianza."""
    d = FallDetector(FallConfig(confirmSeconds=2.0))
    frames = (
        seq_standing(0.0, 1.0)
        + seq_transition(1.0, 0.4, 0.5, 0.88, 3.0, 89.0)
        + seq_lying(1.4, 4.0)
    )
    res = run(d, frames)
    alerta = next(r for r in res if r.is_fall)
    assert alerta.confidence > 0.7, f"una caída clara debería superar 0,7: {alerta.confidence}"


def test_olvida_personas_que_desaparecen():
    """El estado no crece sin límite si la gente sale de cuadro."""
    d = FallDetector(FallConfig(trackTimeoutSeconds=2.0))
    for t in range(5):
        d.update(PoseFrame(t, 0.0, make_pose(0.5, 3.0), bbox_for(3.0), 0.9))
    assert len(d.tracks) == 5
    d.purge(now=10.0)
    assert len(d.tracks) == 0, "las personas que ya no se ven deben olvidarse"


if __name__ == "__main__":
    import sys

    pruebas = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    fallos = 0
    for nombre, fn in pruebas:
        try:
            fn()
            print(f"  OK   {nombre}")
        except AssertionError as e:
            fallos += 1
            print(f"  FALLA {nombre}: {e}")
        except Exception as e:  # noqa: BLE001
            fallos += 1
            print(f"  ERROR {nombre}: {e!r}")
    print(f"\n{len(pruebas) - fallos}/{len(pruebas)} pruebas OK")
    sys.exit(1 if fallos else 0)
