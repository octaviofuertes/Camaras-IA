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


# ═══════════════════════════════════════════════════════════════════
# Los umbrales, fijados uno por uno
#
# Estas pruebas existen porque se midió que faltaban: al romper el detector a
# propósito (test_mutaciones.py), aflojar cualquiera de los cuatro umbrales
# pasaba sin que ninguna prueba se quejara. O sea que alguien podía devolver el
# detector al comportamiento que lo hacía inservible —alertar cuando alguien se
# sienta— y verlo todo en verde.
#
# Cada una construye un caso que cae DENTRO del margen: entre el valor correcto
# y el valor aflojado. Así la prueba falla exactamente cuando el umbral se
# mueve, y no antes.
# ═══════════════════════════════════════════════════════════════════

def _descenso_en(segundos: float, bajada: float = 0.36, quieto: float = 3.0):
    """Trayectoria que baja hasta el piso en el tiempo indicado y se queda ahí.

    La velocidad que mide el detector no es la del centro del cuerpo: al rotar,
    los hombros recorren bastante más. Por eso la prueba se calibra por DURACIÓN
    —que es lo que se controla— y después verifica el valor medido, en vez de
    suponer una fórmula.
    """
    n = max(int(segundos * FPS), 2)
    pasos = de_pie(0, quieto)
    for i in range(1, n + 1):
        f = i / n
        pasos.append((quieto + i * DT, 0.5 + bajada * f, DE_PIE, 3 + (TENDIDO_ANG - 3) * f))
    t = quieto + n * DT
    pasos += [(t + i * DT, 0.5 + bajada, DE_PIE, TENDIDO_ANG) for i in range(25)]
    return pasos


def test_umbral_de_velocidad_no_se_puede_aflojar():
    """Un descenso a media velocidad no es una caída, aunque termine abajo.

    Fija `fallVelocity`: el caso baja a ~0.5 estaturas/s, entre el umbral real
    (0.70) y la mitad. Si alguien baja el umbral, esto empieza a alertar.
    """
    cfg = FallConfig()
    d = FallDetector(cfg)
    # Dos segundos de descenso: medido, ~0.59 estaturas/s. Cae entre la mitad
    # del umbral y el umbral, que es justo el margen que hay que proteger.
    res = correr(d, _descenso_en(2.0))
    pico = max(r.velocity for r in res)
    assert cfg.fallVelocity / 2 < pico < cfg.fallVelocity, (
        f"el escenario debía caer en el margen protegido; midió {pico:.2f} "
        f"contra un umbral de {cfg.fallVelocity}"
    )
    assert not any(r.is_fall for r in res), (
        "bajar a media velocidad y quedar abajo NO es una caída: es acostarse"
    )


def test_dejarse_caer_en_una_silla_rapido_no_alerta():
    """Sentarse de golpe: rápido de verdad, pero el tronco baja poco.

    Fija `trunkDropRatio`. El caso tiene velocidad muy por encima del umbral —lo
    que descarta que pase por lento— y una bajada de tronco de sentarse (~0.30).
    Es lo que separa una silla del piso.
    """
    d = FallDetector(FallConfig())
    # Cae 0.16 estaturas en 0.2 s: unas 0.8 estaturas/s, bien sobre el umbral.
    pasos = de_pie(0, 3.0)
    pasos += [(3.0, 0.55, DE_PIE, 8.0), (3.1, 0.58, DE_PIE, 10.0)]
    pasos += [(3.2 + i * DT, 0.58, DE_PIE, 10.0) for i in range(30)]

    res = correr(d, pasos)
    pico = max(r.velocity for r in res)
    assert pico >= FallConfig().fallVelocity, (
        f"el escenario debía ser rápido para no pasar por lento; midió {pico:.2f}"
    )
    assert not any(r.is_fall for r in res), (
        "dejarse caer en una silla es rápido pero el tronco baja poco: no es una caída"
    )


def test_bajada_intermedia_sin_cuerpo_horizontal_no_alerta():
    """Baja bastante y rápido, pero el cuerpo se sigue viendo vertical.

    Fija `trunkDropSure` y `downVerticality` a la vez: la bajada queda entre
    `trunkDropRatio` y `trunkDropSure`, así que se necesita además una postura
    horizontal. Como no la hay, no debe alertar. Si alguien baja `trunkDropSure`
    o vuelve permisiva la verticalidad, esto alerta.
    """
    d = FallDetector(FallConfig())
    pasos = de_pie(0, 3.0)
    # Cae rápido quedando de rodillas: el cuerpo se sigue proyectando vertical
    # (relación alto/ancho medida: 2.58, muy por encima del umbral de 1.2).
    pasos += [(3.0, 0.68, DE_PIE * 0.60, 12.0), (3.1, 0.72, DE_PIE * 0.60, 14.0)]
    pasos += [(3.2 + i * DT, 0.72, DE_PIE * 0.60, 14.0) for i in range(30)]

    res = correr(d, pasos)
    caidas = [r for r in res if r.trunk_drop is not None]
    assert caidas, "el escenario no llegó a medir la bajada de tronco"
    maxima = max(r.trunk_drop for r in caidas)
    cfg = FallConfig()
    assert cfg.trunkDropRatio < maxima < cfg.trunkDropSure, (
        f"la bajada ({maxima:.2f}) debía caer entre {cfg.trunkDropRatio} y {cfg.trunkDropSure}"
    )
    assert not any(r.is_fall for r in res), (
        "una bajada intermedia con el cuerpo vertical no alcanza para afirmar una caída"
    )


def test_la_altura_de_referencia_es_la_envolvente_no_la_mediana():
    """Aprende la altura DE PIE aunque la persona pase agachada casi todo el rato.

    Fija cómo se estima la referencia. Con la mediana, alguien que trabaja
    agachado termina con una referencia baja, y después estar de pie se lee como
    una altura imposible — se midieron 246 % en cámara real.
    """
    d = FallDetector(FallConfig())
    # 6 frames de pie y 30 agachado: la mediana daría la altura agachada.
    pasos = de_pie(0, 0.6)
    pasos += [(0.6 + i * DT, 0.62, DE_PIE * 0.55, 25.0) for i in range(30)]
    correr(d, pasos)

    base = d.tracks[1].baseline
    assert base is not None, "no llegó a fijar una referencia"
    assert base > DE_PIE * 0.85, (
        f"la referencia debe ser la altura DE PIE (~{DE_PIE}), no la agachada; dio {base:.3f}"
    )


def test_la_referencia_se_congela_tras_alertar():
    """Alguien tirado mucho rato no debe pasar a leerse como si estuviera de pie.

    Si la referencia siguiera aprendiendo mientras la persona está en el suelo,
    la ventana se llenaría de alturas de alguien tendido y el detector concluiría
    que se levantó sin que se haya movido — perdiendo la severidad creciente
    justo en el caso más grave, el de quien no puede levantarse.
    """
    d = FallDetector(FallConfig())
    pasos = de_pie(0, 2.0) + transicion(2.0, 0.3, 0.5, 0.85, 3, 85) + tendido(2.3, 25.0)
    res = correr(d, pasos)
    assert any(r.is_fall for r in res), "el escenario debía alertar primero"

    base_al_alertar = None
    for r in res:
        if r.is_fall:
            base_al_alertar = d.tracks[1].baseline
            break
    assert d.tracks[1].state == State.ALERTED, "debería seguir marcada como caída"
    assert abs(d.tracks[1].baseline - base_al_alertar) < 1e-9, (
        "la referencia cambió mientras la persona seguía en el suelo"
    )
    assert res[-1].down_seconds > 20, (
        "tras 25 s tirada, el contador de permanencia debería seguir corriendo"
    )


def test_el_esquema_y_el_codigo_no_pueden_divergir():
    """Los valores por omisión del esquema son los mismos que los del código.

    Se encontraron cuatro desincronizados —el esquema decía fallVelocity 0.55
    cuando el código usaba 0.70— y eso no es cosmético: la interfaz muestra esos
    números y, si los guarda, devuelve el detector a la configuración anterior
    sin que nadie lo note.
    """
    import json
    from dataclasses import fields
    from pathlib import Path

    esquema = json.loads(
        (Path(__file__).parent / "config.schema.json").read_text(encoding="utf-8")
    )["properties"]
    d = FallConfig()

    problemas = []
    for f in fields(FallConfig):
        if f.name not in esquema:
            continue
        real, declarado = getattr(d, f.name), esquema[f.name].get("default")
        if declarado != real:
            problemas.append(f"{f.name}: esquema={declarado!r} código={real!r}")
        lo, hi = esquema[f.name].get("minimum"), esquema[f.name].get("maximum")
        if lo is not None and real < lo:
            problemas.append(f"{f.name}: el default {real} es menor que el mínimo {lo}")
        if hi is not None and real > hi:
            problemas.append(f"{f.name}: el default {real} supera el máximo {hi}")

    assert not problemas, "esquema y código desincronizados:\n  " + "\n  ".join(problemas)


def test_una_pose_mala_no_contamina_la_velocidad():
    """Un frame descartado por mala no puede dejar rastro en el historial.

    Se midió que sí lo dejaba: la guarda de calidad impedía DECIDIR sobre ese
    frame pero sus coordenadas basura quedaban registradas, y el frame siguiente
    —bueno— medía 3.83 de velocidad contra ellas con la persona inmóvil y de
    pie. El umbral de alerta es 0.70. Era una fábrica de caídas inexistentes.
    """
    d = FallDetector(FallConfig())
    res = correr(d, de_pie(0, 3.0))
    assert not any(r.is_fall for r in res)

    # Pose inservible: todos los puntos por debajo del umbral de confianza, y un
    # recuadro que la ubica arriba de todo.
    d.update(
        PoseFrame(
            track_id=1, ts=3.0,
            keypoints=make_pose(0.5, DE_PIE, 3.0, visible=False),
            bbox=(0.4, 0.0, 0.12, 0.04), det_score=0.3,
        )
    )
    r = d.update(
        PoseFrame(track_id=1, ts=3.1, keypoints=make_pose(0.5, DE_PIE, 3.0),
                  bbox=bbox_de(DE_PIE, 3.0), det_score=0.92)
    )
    assert r.velocity < FallConfig().fallVelocity, (
        f"una pose descartada inyectó velocidad falsa: {r.velocity:.2f}"
    )
    assert not r.is_fall


def test_coordenadas_imposibles_no_dejan_ciego_al_detector():
    """Un NaN en el esqueleto no puede apagar la detección en silencio.

    Sin la limpieza previa, el NaN se propagaba hasta la bajada de tronco, y como
    toda comparación con NaN da falso, el detector dejaba de afirmar que alguien
    estaba abajo. No fallaba: se volvía ciego. Para algo cuya razón de existir es
    avisar, ése es el peor modo de romperse, porque desde afuera se ve igual que
    "no pasó nada".
    """
    import math as _m

    d = FallDetector(FallConfig())
    correr(d, de_pie(0, 3.0))

    k = make_pose(0.5, DE_PIE, 3.0)
    k[5] = Keypoint(float("nan"), float("nan"), 0.9)
    k[11] = Keypoint(float("inf"), 0.5, 0.9)
    r = d.update(PoseFrame(1, 3.0, k, bbox_de(DE_PIE, 3.0), 0.9))

    for nombre, v in (("velocidad", r.velocity), ("confianza", r.confidence),
                      ("troncoBajo", r.trunk_drop), ("verticalidad", r.verticality)):
        assert v is None or _m.isfinite(v), f"{nombre} quedó contaminado: {v}"

    # Y sigue detectando: la basura no puede dejarlo inutilizado.
    res = correr(d, transicion(3.1, 0.3, 0.5, 0.85, 3, 85) + tendido(3.4, 3.0))
    assert any(x.is_fall for x in res), (
        "tras recibir coordenadas imposibles, el detector dejó de funcionar"
    )


def test_una_configuracion_absurda_no_tumba_el_detector():
    """Un valor imposible tiene que dar un detector conservador, no una excepción.

    La configuración llega desde la base y ahí puede tener cualquier cosa. Un
    `baselineFrames: 0` guardado a mano rompía el módulo con un IndexError, y en
    producción eso es el pipeline de esa cámara caído.
    """
    for bf, bw in ((0, 0), (-5, 3), (1, 1)):
        cfg = FallConfig(baselineFrames=bf, baselineWindowFrames=bw)
        d = FallDetector(cfg)
        for i in range(8):
            d.update(
                PoseFrame(track_id=1, ts=i * DT, keypoints=make_pose(0.5, DE_PIE, 3.0),
                          bbox=bbox_de(DE_PIE, 3.0), det_score=0.9)
            )


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
