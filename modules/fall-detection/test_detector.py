"""Pruebas del detector de caídas con trayectorias sintéticas.

Cubren lo que separa un detector de producción de una demo:
  - un tropiezo del que la persona se levanta enseguida SÍ es una caída;
  - agacharse o sentarse despacio NO lo es, aunque el cuerpo baje igual;
  - una caída hacia la cámara (donde el torso sigue viéndose vertical) también
    tiene que detectarse, porque la señal principal es el colapso de altura.
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


# Dónde cae cada punto a lo largo del cuerpo, desde la cabeza (0) a los pies (1).
A_LO_LARGO = {0: 0.00, 5: 0.18, 6: 0.18, 11: 0.55, 12: 0.55, 13: 0.78, 14: 0.78, 15: 1.0, 16: 1.0}
# Separación lateral izquierda/derecha, en fracciones del largo del cuerpo.
# Proporciones humanas reales: los hombros miden ~24 % de la estatura.
A_LO_ANCHO = {5: -0.12, 6: +0.12, 11: -0.09, 12: +0.09, 13: -0.07, 14: +0.07, 15: -0.07, 16: +0.07}


def make_pose(
    centro_y: float,
    largo: float,
    angulo: float = 0.0,
    visible: bool = True,
    centro_x: float = 0.5,
    ancho_ref: float | None = None,
) -> list[Keypoint]:
    """Esqueleto de `largo` metros-de-imagen, rotado `angulo` grados.

    El cuerpo se rota ENTERO alrededor de su centro, como pasa de verdad. La
    versión anterior sólo corría la cabeza de costado y dejaba el resto en su
    lugar, así que un cuerpo "tendido" seguía siendo una columna angosta: la
    relación alto/ancho daba 2 cuando en la realidad da menos de 1. Las pruebas
    quedaban validando un cuerpo que ninguna cámara ve.
    """
    s = 0.9 if visible else 0.05
    rad = math.radians(angulo)
    sin_a, cos_a = math.sin(rad), math.cos(rad)
    # El ancho no se comprime junto con el largo. Cuando alguien cae HACIA la
    # cámara, la perspectiva acorta el eje que apunta al lente pero los hombros
    # se siguen viendo del mismo ancho —incluso más, porque quedan más cerca—.
    # Escalar todo por igual dejaba una silueta con la misma proporción que una
    # persona parada, y eso hacía que la prueba midiera una geometría que
    # ninguna cámara produce.
    ancho = ancho_ref if ancho_ref is not None else largo

    kps = [Keypoint(centro_x, centro_y, 0.1) for _ in range(17)]
    for i, f in A_LO_LARGO.items():
        # Distancia al centro del cuerpo a lo largo de su eje.
        eje = (f - 0.5) * largo
        lado = A_LO_ANCHO.get(i, 0.0) * ancho
        x = centro_x + eje * sin_a + lado * cos_a
        y = centro_y + eje * cos_a - lado * sin_a
        kps[i] = Keypoint(x, y, s)
    return kps


def bbox_de(largo: float, angulo: float) -> tuple[float, float, float, float]:
    """Recuadro que envuelve a ese cuerpo."""
    rad = math.radians(angulo)
    ancho_cuerpo = largo * 0.16
    h = abs(largo * math.cos(rad)) + abs(ancho_cuerpo * math.sin(rad))
    w = abs(largo * math.sin(rad)) + abs(ancho_cuerpo * math.cos(rad))
    return (0.4, 0.3, max(w, 0.02), max(h, 0.02))


DE_PIE = 0.50      # altura típica de una persona de pie en la imagen
# Un cuerpo tendido mide lo mismo: lo que cambia es cómo se proyecta. Por eso
# los escenarios se escriben con (largo, ángulo) y no con "altura visible" — esa
# es una consecuencia, no algo que la persona controle.
TENDIDO_ANG = 85.0  # tendida en el piso, de costado a la cámara
# Cayendo HACIA la cámara el cuerpo se ve comprimido pero sigue vertical: es el
# caso donde el ángulo no sirve de nada.
HACIA_CAMARA = 0.35


def correr(det: FallDetector, pasos, track: int = 1):
    """pasos = lista de (ts, centro_y, largo_cuerpo, angulo)."""
    out = []
    for ts, cy, largo, ang in pasos:
        out.append(
            det.update(
                PoseFrame(
                    track_id=track, ts=ts,
                    keypoints=make_pose(cy, largo, ang),
                    bbox=bbox_de(largo, ang), det_score=0.92,
                )
            )
        )
    return out


def de_pie(t0, segundos, cy=0.5):
    return [(t0 + i * DT, cy, DE_PIE, 3.0) for i in range(int(segundos * FPS))]


def transicion(t0, segundos, cy0, cy1, a0, a1, largo0=DE_PIE, largo1=DE_PIE):
    n = max(int(segundos * FPS), 1)
    return [
        (
            t0 + i * DT,
            cy0 + (cy1 - cy0) * (i / n),
            largo0 + (largo1 - largo0) * (i / n),
            a0 + (a1 - a0) * (i / n),
        )
        for i in range(n)
    ]


def tendido(t0, segundos, cy=0.88, ang=TENDIDO_ANG):
    return [(t0 + i * DT, cy, DE_PIE, ang) for i in range(int(segundos * FPS))]


# ═══════════════════════════════════════════════════════════════════
# Casos
# ═══════════════════════════════════════════════════════════════════

def test_angulo_de_torso():
    de = torso_angle_deg(make_pose(0.5, DE_PIE, 0.0), 0.3)
    ten = torso_angle_deg(make_pose(0.88, DE_PIE, 88.0), 0.3)
    assert de is not None and de < 20, f"de pie deberia ser bajo, dio {de}"
    assert ten is not None and ten > de, "tendido deberia dar un angulo mayor que de pie"


def test_caida_con_permanencia_alerta():
    """Caída clásica: descenso brusco y queda en el suelo."""
    d = FallDetector()
    pasos = de_pie(0, 2.0) + transicion(2.0, 0.4, 0.5, 0.85, 3, 85, DE_PIE, DE_PIE) + tendido(2.4, 4.0)
    res = correr(d, pasos)
    alertas = [r for r in res if r.is_fall]
    assert alertas, "una caída con permanencia DEBE alertar"
    assert len(alertas) == 1, "debe alertar una sola vez"


def test_tropiezo_con_recuperacion_rapida_alerta():
    """LO QUE ANTES FALLABA: se cae y se levanta en menos de 3 s.

    Sigue siendo una caída y hay que reportarla.
    """
    d = FallDetector()
    pasos = (
        de_pie(0, 2.0)
        + transicion(2.0, 0.3, 0.5, 0.85, 3, 80, DE_PIE, DE_PIE)   # tropieza
        + tendido(2.3, 0.8)                                          # menos de 1 s abajo
        + transicion(3.1, 0.6, 0.85, 0.5, 80, 3, DE_PIE, DE_PIE)    # se levanta
        + de_pie(3.7, 2.0)
    )
    res = correr(d, pasos)
    assert any(r.is_fall for r in res), "un tropiezo con recuperación rápida DEBE alertar"


def test_caida_tras_estar_quieto_y_levantarse_rapido():
    """Quieto, se cae, y se levanta apenas confirmado el impacto.

    Éste es el caso que separa medir bien la velocidad de medirla mal. Comparando
    cada frame contra el inicio de la ventana, el descenso queda repartido sobre
    todo el tiempo quieto previo y la velocidad recién cruza el umbral cuando esa
    quietud sale de la ventana, medio segundo tarde. Para entonces la persona ya
    se levantó y la caída se perdió. Comparando todos los pares aparece el tramo
    real de caída en el primer frame.
    """
    d = FallDetector()
    pasos = (
        de_pie(0, 2.0)
        + [(2.0 + i * DT, 0.5, DE_PIE, 3.0) for i in range(9)]        # 0.9 s quieto
        + transicion(2.9, 0.3, 0.5, 0.75, 3, 60, DE_PIE, DE_PIE)     # cae
        # Abajo lo mínimo que exige la confirmación del impacto: 0,4 s. Menos
        # que eso ya no se reporta, y es deliberado — antes se alertaba con el
        # cuerpo arriba sólo porque la velocidad había sido alta, y eso llenaba
        # el panel de caídas que nunca ocurrieron.
        + [(3.2 + i * DT, 0.78, DE_PIE, 75.0) for i in range(4)]
        + [(3.6, 0.60, 0.40, 20.0), (3.7, 0.55, 0.46, 8.0)]           # se incorpora
        + de_pie(3.8, 1.5)
    )
    res = correr(d, pasos)
    assert any(r.is_fall for r in res), "la caída no debe diluirse por el tiempo quieto previo"


def test_caida_a_6_fps_alerta():
    """Al fps con el que corre en producción, no al de laboratorio."""
    dt = 1.0 / 6.0
    d = FallDetector()
    pasos = [(i * dt, 0.5, DE_PIE, 3.0) for i in range(18)]           # 3 s de pie
    t = 18 * dt
    pasos += [(t + i * dt, 0.5 + 0.38 * (i / 3), DE_PIE, 3 + 27 * i) for i in range(3)]
    t += 3 * dt
    pasos += [(t + i * dt, 0.88, DE_PIE, 84.0) for i in range(12)]
    res = correr(d, pasos)
    assert any(r.is_fall for r in res), "a 6 fps una caída real todavía tiene que detectarse"


def test_sentarse_largo_no_ciega_al_detector():
    """Sentarse un rato largo y DESPUÉS caerse.

    El estado de confirmación no puede volverse una trampa: si alguien se queda
    en postura baja sin haber caído, el detector tiene que volver a de pie y
    reaprender su altura, o queda ciego a la caída siguiente.
    """
    d = FallDetector()
    pasos = (
        de_pie(0, 2.0)
        + transicion(2.0, 2.0, 0.5, 0.8, 3, 20, DE_PIE, 0.22)   # se sienta despacio
        + [(4.0 + i * DT, 0.8, 0.22, 20.0) for i in range(80)]   # 8 s sentado
    )
    res = correr(d, pasos)
    assert not any(r.is_fall for r in res), "sentarse no es caerse"
    assert res[-1].state == State.UPRIGHT, f"quedó atrapado en {res[-1].state}"

    # Ahora sí se levanta, y después se cae de verdad.
    t = 12.0
    pasos2 = de_pie(t, 2.0) + transicion(t + 2.0, 0.3, 0.5, 0.85, 3, 85, DE_PIE, DE_PIE) + tendido(t + 2.3, 3.0)
    res2 = correr(d, pasos2)
    assert any(r.is_fall for r in res2), "tras estar sentado, una caída posterior DEBE detectarse"


def test_confianza_no_se_hunde_si_el_detector_ve_mal_la_caida():
    """La confianza no puede depender de lo bien que se vea el frame del impacto.

    A una persona cayéndose el detector de personas la ve peor —postura rara,
    movimiento borroso—, justo cuando la evidencia geométrica es más clara. Con
    el tope puesto en el puntaje del frame, una caída contundente se reportaba
    con confianza apenas por encima del umbral de la regla, a un pelo de que la
    descartara. El tope tiene que ser el mejor puntaje reciente del track.
    """
    d = FallDetector()
    pasos = (
        de_pie(0, 2.0)
        + transicion(2.0, 0.3, 0.5, 0.85, 3, 85, DE_PIE, DE_PIE)
        + tendido(2.3, 2.0)
    )
    alertas = []
    for ts, cy, alt, ang in pasos:
        # De pie se la ve nítida; durante y después de la caída, mucho peor.
        score = 0.95 if ts < 2.0 else 0.35
        r = d.update(
            PoseFrame(track_id=1, ts=ts, keypoints=make_pose(cy, alt, ang),
                      bbox=bbox_de(alt, ang), det_score=score)
        )
        if r.is_fall:
            alertas.append(r)

    assert alertas, "la caída debe detectarse igual"
    assert alertas[0].confidence > 0.6, (
        f"la confianza se hundió a {alertas[0].confidence} por lo mal que se vio "
        f"el frame del impacto, no por falta de evidencia"
    )


def pose_sentado_en_escritorio(y_hombros: float, estatura: float) -> list[Keypoint]:
    """Persona sentada con las piernas tapadas por el escritorio.

    Sólo se ven cabeza, hombros y caderas. Es la postura que en cámara real
    hacía que el detector viera un desplome: la extensión vertical de los puntos
    visibles cae a un tercio aunque el cuerpo no se haya movido.
    """
    kps = [Keypoint(0.5, y_hombros, 0.02) for _ in range(17)]
    kps[0] = Keypoint(0.5, y_hombros - estatura * 0.12, 0.9)   # nariz
    kps[5] = Keypoint(0.46, y_hombros, 0.9)                     # hombro izq
    kps[6] = Keypoint(0.54, y_hombros, 0.9)                     # hombro der
    kps[11] = Keypoint(0.47, y_hombros + estatura * 0.27, 0.9)  # cadera izq
    kps[12] = Keypoint(0.53, y_hombros + estatura * 0.27, 0.9)  # cadera der
    # Rodillas y tobillos con score bajísimo: el escritorio los tapa.
    return kps


def test_sentarse_en_una_silla_no_alerta():
    """EL CASO QUE REPORTÓ EL USUARIO: sentarse siempre se tomaba como caída.

    Sentarse baja el tronco el largo del muslo, alrededor de 0.3 estaturas.
    Caerse lo lleva al piso, 0.7-0.9. Son magnitudes distintas y el detector
    tiene que distinguirlas aunque el movimiento sea rápido: uno se puede dejar
    caer en una silla de golpe y sigue sin ser una caída.
    """
    d = FallDetector()
    estatura = DE_PIE
    y_de_pie = 0.5 - estatura / 2 + estatura * 0.18   # hombros estando parado

    pasos = []
    for i in range(30):                                # 3 s de pie
        pasos.append((i * DT, y_de_pie, False))
    # Se sienta RÁPIDO: medio segundo. El tronco baja 0.30 estaturas.
    for i in range(5):
        t = 3.0 + i * DT
        pasos.append((t, y_de_pie + estatura * 0.30 * (i + 1) / 5, False))
    for i in range(60):                                # 6 s sentado
        pasos.append((3.5 + i * DT, y_de_pie + estatura * 0.30, True))

    hubo_alerta = False
    for ts, y_h, tapado in pasos:
        kps = (
            pose_sentado_en_escritorio(y_h, estatura)
            if tapado
            else make_pose(y_h + estatura * 0.32, estatura, 3.0)
        )
        alto = max(k.y for k in kps if k.score >= 0.3) - min(k.y for k in kps if k.score >= 0.3)
        r = d.update(
            PoseFrame(track_id=1, ts=ts, keypoints=kps,
                      bbox=(0.4, y_h - 0.05, estatura * 0.3, max(alto, 0.03)), det_score=0.9)
        )
        hubo_alerta = hubo_alerta or r.is_fall

    assert not hubo_alerta, "sentarse en una silla NO es una caída"


def test_pico_de_velocidad_con_el_cuerpo_arriba_no_alerta():
    """LA OTRA FALLA REPORTADA: alertas 'de la nada'.

    Un salto de la estimación de pose produce un pico de velocidad enorme sin
    que la persona se haya movido. En cámara real eso generó alertas con el
    tronco al 104 % de su altura de pie. Una velocidad sin destino no es una
    caída: si el cuerpo no quedó abajo, no pasó nada.
    """
    d = FallDetector()
    # Reproduce la traza observada en cámara: dos frames de estimación mala
    # —uno a media altura, otro abajo— y la persona parada otra vez. La versión
    # anterior alertaba acá porque la velocidad había superado el umbral, sin
    # mirar que el cuerpo había vuelto arriba enseguida.
    pasos = de_pie(0, 3.0)
    pasos.append((3.0, 0.68, 0.35, 40.0))    # pose a media altura
    pasos.append((3.1, 0.88, DE_PIE, 85.0))  # un solo frame "abajo"
    pasos += de_pie(3.2, 3.0)                 # y de vuelta parada

    res = correr(d, pasos)
    alertas = [r for r in res if r.is_fall]
    assert not alertas, (
        f"un pico de velocidad sin que el cuerpo quede abajo no puede alertar; "
        f"alertó con: {alertas[0].reason if alertas else ''}"
    )


def test_persona_diminuta_no_alerta():
    """Detección muy chica: es ruido del detector, no una persona que se cayó.

    A esa escala los puntos del esqueleto caen en un puñado de píxeles y el
    "colapso de altura" mide el temblor del estimador. La cámara real produce
    varias de estas por minuto sobre objetos del fondo.
    """
    chico = 0.06                      # 6 % del alto de la imagen
    d = FallDetector()
    pasos = (
        [(i * DT, 0.5, chico, 3.0) for i in range(20)]
        + transicion(2.0, 0.3, 0.5, 0.85, 3, 85, chico, chico * 0.3)
        + [(2.3 + i * DT, 0.85, chico * 0.3, 85.0) for i in range(40)]
    )
    res = correr(d, pasos)
    assert not any(r.is_fall for r in res), "una detección diminuta no puede generar una alerta"
    assert all(not r.quality_ok for r in res), "debe reportarse como pose insuficiente"


def test_caida_hacia_la_camara_alerta():
    """Cae hacia la cámara: el torso se sigue proyectando casi vertical.

    El caso más difícil, y el que descarta al ángulo del torso como señal
    principal. La perspectiva acorta el cuerpo a un tercio pero los hombros se
    ven del mismo ancho, así que la silueta queda achatada aunque el esqueleto
    apunte "hacia arriba". Lo que delata la caída es cuánto bajó el tronco.
    """
    d = FallDetector()
    corto = DE_PIE * HACIA_CAMARA
    pasos = []
    # Al acercarse al lente el cuerpo no sólo se acorta: lo que queda cerca se
    # ve MÁS grande. Los hombros de alguien tirado con la cabeza hacia la
    # cámara se proyectan más anchos que estando parado a la distancia
    # original, y por eso la silueta termina achatada.
    cerca = DE_PIE * 1.5
    for i in range(20):                                   # 2 s de pie
        pasos.append((i * DT, 0.5, DE_PIE, 3.0, DE_PIE))
    for i in range(4):                                    # cae hacia el lente
        f = (i + 1) / 4
        pasos.append((2.0 + i * DT, 0.5 + 0.34 * f, DE_PIE + (corto - DE_PIE) * f,
                      3 + 2 * f, DE_PIE + (cerca - DE_PIE) * f))
    for i in range(30):                                   # queda en el piso
        pasos.append((2.4 + i * DT, 0.84, corto, 5.0, cerca))

    res = []
    for ts, cy, largo, ang, ancho in pasos:
        res.append(
            d.update(
                PoseFrame(
                    track_id=1, ts=ts,
                    keypoints=make_pose(cy, largo, ang, ancho_ref=ancho),
                    bbox=bbox_de(largo, ang), det_score=0.92,
                )
            )
        )
    assert any(r.is_fall for r in res), (
        "una caída hacia la cámara debe detectarse por cuánto bajó el tronco"
    )


def test_agacharse_no_alerta():
    """Agacharse a atarse los cordones: baja despacio y se levanta."""
    d = FallDetector()
    pasos = (
        de_pie(0, 2.0)
        + transicion(2.0, 1.5, 0.5, 0.66, 3, 40, DE_PIE, DE_PIE * 0.72)  # lento y parcial
        + [(3.5 + i * DT, 0.66, DE_PIE * 0.72, 40.0) for i in range(10)]
        + transicion(4.5, 1.2, 0.66, 0.5, 40, 3, DE_PIE * 0.72, DE_PIE)
        + de_pie(5.7, 1.5)
    )
    res = correr(d, pasos)
    assert not any(r.is_fall for r in res), "agacharse despacio NO es una caída"


def test_sentarse_en_el_piso_no_alerta():
    """Sentarse en el piso a propósito: baja despacio aunque termine abajo."""
    d = FallDetector()
    pasos = (
        de_pie(0, 2.0)
        + transicion(2.0, 3.0, 0.5, 0.78, 3, 30, DE_PIE, DE_PIE * 0.55)  # 3 s: controlado
        + [(5.0 + i * DT, 0.78, DE_PIE * 0.55, 30.0) for i in range(30)]
    )
    res = correr(d, pasos)
    assert not any(r.is_fall for r in res), "sentarse despacio no es una caída"


def test_caminar_no_alerta():
    d = FallDetector()
    pasos = [(i * DT, 0.5 + 0.008 * math.sin(i / 3.0), DE_PIE, 4.0) for i in range(150)]
    res = correr(d, pasos)
    assert not any(r.is_fall for r in res)
    assert res[-1].state == State.UPRIGHT


def test_severidad_sube_si_sigue_en_el_suelo():
    """Quedarse tirado agrava la caída, pero no condiciona la alerta."""
    d = FallDetector(FallConfig(prolongedSeconds=3.0))
    pasos = de_pie(0, 2.0) + transicion(2.0, 0.4, 0.5, 0.85, 3, 85, DE_PIE, DE_PIE) + tendido(2.4, 8.0)
    res = correr(d, pasos)
    assert any(r.is_fall for r in res)
    assert res[-1].severity == "critical", "seguir en el suelo debe elevar la severidad"


def test_pose_insuficiente_no_afirma():
    d = FallDetector()
    out = []
    for i in range(40):
        out.append(
            d.update(
                PoseFrame(
                    track_id=7, ts=i * DT,
                    keypoints=make_pose(0.88, DE_PIE, 88.0, visible=False),
                    bbox=bbox_de(DE_PIE, 88.0), det_score=0.9,
                )
            )
        )
    assert all(not r.quality_ok for r in out), "sin pose fiable no se puede afirmar nada"


def test_segunda_caida_se_detecta():
    """Tras levantarse, una nueva caída vuelve a alertar."""
    d = FallDetector()
    pasos = (
        de_pie(0, 1.5)
        + transicion(1.5, 0.3, 0.5, 0.85, 3, 85, DE_PIE, DE_PIE)
        + tendido(1.8, 2.0)
        + transicion(3.8, 0.5, 0.85, 0.5, 85, 3, DE_PIE, DE_PIE)
        + de_pie(4.3, 2.0)
        + transicion(6.3, 0.3, 0.5, 0.85, 3, 85, DE_PIE, DE_PIE)
        + tendido(6.6, 2.0)
    )
    res = correr(d, pasos)
    assert len([r for r in res if r.is_fall]) >= 2, "debe detectar las dos caídas"


def test_personas_independientes():
    d = FallDetector()
    for i in range(70):
        ts = i * DT
        if ts < 2.0:
            cy1, alt1, a1 = 0.5, DE_PIE, 3.0
        else:
            cy1, alt1, a1 = 0.88, DE_PIE, 85.0
        d.update(PoseFrame(1, ts, make_pose(cy1, alt1, a1), bbox_de(alt1, a1), 0.9))
        r2 = d.update(PoseFrame(2, ts, make_pose(0.5, DE_PIE, 3.0), bbox_de(DE_PIE, 3.0), 0.9))
        assert not r2.is_fall, "la persona 2 nunca se cayó"
    assert d.tracks[2].state == State.UPRIGHT


def test_olvida_personas_que_desaparecen():
    d = FallDetector(FallConfig(trackTimeoutSeconds=2.0))
    for t in range(5):
        d.update(PoseFrame(t, 0.0, make_pose(0.5, DE_PIE, 3.0), bbox_de(DE_PIE, 3.0), 0.9))
    assert len(d.tracks) == 5
    d.purge(now=10.0)
    assert len(d.tracks) == 0


if __name__ == "__main__":
    import sys

    pruebas = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    fallos = 0
    for nombre, fn in pruebas:
        try:
            fn()
            print(f"  OK    {nombre}")
        except AssertionError as e:
            fallos += 1
            print(f"  FALLA {nombre}: {e}")
        except Exception as e:  # noqa: BLE001
            fallos += 1
            print(f"  ERROR {nombre}: {e!r}")
    print(f"\n{len(pruebas) - fallos}/{len(pruebas)} pruebas OK")
    sys.exit(1 if fallos else 0)
