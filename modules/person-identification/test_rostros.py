"""Pruebas de la identificación de personas.

Lo que se protege acá es lo que puede hacerle daño a alguien: que no se le
atribuya a una persona el tiempo de otra, que no se guarde nada de quien no
está dado de alta, y que la cola de revisión no se vuelva inservible.
"""
from __future__ import annotations

import math
import random

from rostros import (
    ConfigRostros,
    Identificador,
    Persona,
    Rostro,
    asociar_a_cuerpo,
    coseno,
)

DIM = 512


def vector(semilla: int, ruido: float = 0.0) -> list[float]:
    """Vector reproducible, opcionalmente perturbado.

    `ruido` simula la variación entre dos fotos de la misma persona: 0 es la
    misma foto, valores altos la van alejando hasta que deja de reconocerse.
    """
    r = random.Random(semilla)
    base = [r.gauss(0, 1) for _ in range(DIM)]
    if ruido > 0:
        rr = random.Random(semilla * 7919 + 13)
        base = [b + rr.gauss(0, ruido) for b in base]
    n = math.sqrt(sum(x * x for x in base)) or 1.0
    return [x / n for x in base]


def rostro(v: list[float], x=0.4, y=0.1, w=0.12, h=0.16) -> Rostro:
    return Rostro(vector=v, x=x, y=y, w=w, h=h)


def a_parecido(v: list[float], objetivo: float, semilla: int = 999) -> list[float]:
    """Otro vector cuyo coseno contra `v` es exactamente `objetivo`.

    Se mezcla el original con una dirección perpendicular. Es preferible a
    agregarle ruido a ojo: el parecido es el dato del que depende la prueba y
    tiene que quedar fijado, no estimado.
    """
    otro = vector(semilla)
    proy = sum(a * b for a, b in zip(otro, v))
    perp = [o - proy * a for o, a in zip(otro, v)]
    n = math.sqrt(sum(x * x for x in perp)) or 1.0
    perp = [x / n for x in perp]
    k = math.sqrt(max(1.0 - objetivo ** 2, 0.0))
    return [objetivo * a + k * b for a, b in zip(v, perp)]


def con_empleados(*personas: Persona, cfg: ConfigRostros | None = None) -> Identificador:
    ident = Identificador(cfg or ConfigRostros())
    ident.galeria.actualizar(list(personas))
    return ident


# ═══════════════════════════════════════════════════════════════════

def test_reconoce_a_un_empleado_dado_de_alta():
    juan = Persona("p1", "Juan Rodríguez", [vector(1)])
    ident = con_empleados(juan)
    # Otra foto suya: mismo vector con algo de variación.
    r = ident.identificar([rostro(vector(1, ruido=0.35))], ahora=1000.0)[0]
    assert r.persona_id == "p1", f"debía reconocer a Juan; dio {r.motivo}"
    assert not r.preguntar


def test_no_le_atribuye_una_persona_a_otra():
    """El error caro: darle el tiempo de uno a otro sin que nadie se entere."""
    juan = Persona("p1", "Juan", [vector(1)])
    ana = Persona("p2", "Ana", [vector(2)])
    ident = con_empleados(juan, ana)

    r = ident.identificar([rostro(vector(2, ruido=0.3))], ahora=1000.0)[0]
    assert r.persona_id != "p1", "le atribuyó a Juan un rostro de Ana"
    assert r.persona_id == "p2"


def test_un_desconocido_dispara_la_pregunta_una_sola_vez():
    """Si preguntara en cada frame, la cola de revisión sería inservible."""
    ident = con_empleados(Persona("p1", "Juan", [vector(1)]))
    desconocido = vector(99)

    primero = ident.identificar([rostro(desconocido)], ahora=1000.0)[0]
    assert primero.preguntar, "la primera vez tiene que preguntar"

    # Los siguientes 30 frames del mismo desconocido: ni una pregunta más.
    preguntas = 0
    for i in range(1, 31):
        res = ident.identificar([rostro(vector(99, ruido=0.2))], ahora=1000.0 + i)[0]
        if res.preguntar:
            preguntas += 1
    assert preguntas == 0, f"volvió a preguntar {preguntas} veces por el mismo desconocido"


def test_al_desconocido_no_se_le_guarda_nada_persistente():
    """El registro de desconocidos vive en memoria y se olvida al reiniciar.

    Es la diferencia entre un antirrebote y un fichero de gente que no dio su
    consentimiento. Un proceso nuevo no puede saber nada del anterior.
    """
    cfg = ConfigRostros()
    uno = con_empleados(cfg=cfg)
    uno.identificar([rostro(vector(77))], ahora=1000.0)
    assert uno.desconocidos.recordados == 1

    # "Reinicio": una instancia nueva no arrastra nada.
    otro = con_empleados(cfg=cfg)
    assert otro.desconocidos.recordados == 0
    assert otro.identificar([rostro(vector(77))], ahora=2000.0)[0].preguntar, (
        "tras reiniciar tiene que volver a preguntar: no quedó nada guardado"
    )


def test_el_antirrebote_cuenta_desde_la_ultima_vez_que_se_la_vio():
    """El plazo se mide desde la última aparición, no desde la primera pregunta.

    Importa la diferencia: si contara desde la primera, alguien que está media
    hora en el cuadro generaría una pregunta cada diez minutos. Contando desde
    la última vez, sólo se vuelve a preguntar si la persona estuvo ausente todo
    ese tiempo — que es cuando puede ser otra visita, otro día, otra persona.
    """
    cfg = ConfigRostros(askCooldownMinutes=10.0)
    ident = con_empleados(cfg=cfg)
    v = vector(55)

    assert ident.identificar([rostro(v)], ahora=1000.0)[0].preguntar, "la primera vez pregunta"

    # Presente sin interrupciones durante media hora: ni una pregunta más.
    ultima = 1000.0
    for i in range(1, 31):
        ultima = 1000.0 + i * 60
        assert not ident.identificar([rostro(v)], ahora=ultima)[0].preguntar, (
            f"volvió a preguntar en el minuto {i} con la persona presente todo el tiempo"
        )

    # Se va, y vuelve once minutos después: ahí sí corresponde preguntar.
    assert ident.identificar([rostro(v)], ahora=ultima + 11 * 60)[0].preguntar


def test_dos_personas_parecidas_no_se_turnan_el_nombre():
    """Ante ambigüedad, no identifica en vez de elegir a una.

    Sin el margen, dos personas parecidas se irían turnando el nombre según el
    ruido de cada frame, y el informe repartiría el tiempo entre las dos de
    forma arbitraria.
    """
    base = vector(3)
    # Dos personas cuyos vectores son casi el mismo.
    a = Persona("pa", "Persona A", [base])
    b = Persona("pb", "Persona B", [[x + 0.01 for x in base]])
    ident = con_empleados(a, b)

    r = ident.identificar([rostro(vector(3, ruido=0.2))], ahora=1000.0)[0]
    assert r.persona_id is None, (
        f"con dos candidatos casi idénticos no puede elegir uno; eligió {r.nombre}"
    )
    assert "ambiguo" in r.motivo


def test_una_cara_diminuta_no_identifica_ni_pregunta():
    """Ni afirma una identidad ni le muestra una mancha al operador."""
    ident = con_empleados(Persona("p1", "Juan", [vector(1)]))
    r = ident.identificar([rostro(vector(1), h=0.02)], ahora=1000.0)[0]
    assert r.persona_id is None
    assert not r.preguntar
    assert "chica" in r.motivo


def test_varias_plantillas_por_persona_mejoran_el_reconocimiento():
    """De frente y de perfil son vectores distintos de la misma persona."""
    de_frente, de_perfil = vector(10), vector(20)
    juan = Persona("p1", "Juan", [de_frente, de_perfil])
    ident = con_empleados(juan)

    for etiqueta, v in (("de frente", de_frente), ("de perfil", de_perfil)):
        r = ident.identificar([rostro(v)], ahora=1000.0)[0]
        assert r.persona_id == "p1", f"no lo reconoció {etiqueta}"


def test_el_telefono_se_asocia_al_cuerpo_correcto():
    """La razón de ser de todo esto: no culpar al de al lado.

    Dos personas una junto a la otra; la cara de la segunda tiene que asociarse
    al cuerpo de la segunda, para que el teléfono detectado sobre ESE cuerpo sea
    de quien corresponde.
    """
    cuerpos = [
        (0.05, 0.20, 0.20, 0.70),   # persona A, a la izquierda
        (0.55, 0.20, 0.20, 0.70),   # persona B, a la derecha
    ]
    cara_a = rostro(vector(1), x=0.10, y=0.22, w=0.10, h=0.13)
    cara_b = rostro(vector(2), x=0.60, y=0.22, w=0.10, h=0.13)

    assert asociar_a_cuerpo(cara_a, cuerpos) == 0
    assert asociar_a_cuerpo(cara_b, cuerpos) == 1


def test_una_cara_sin_cuerpo_no_se_asocia_a_cualquiera():
    cuerpos = [(0.05, 0.20, 0.20, 0.70)]
    lejos = rostro(vector(1), x=0.80, y=0.22, w=0.10, h=0.13)
    assert asociar_a_cuerpo(lejos, cuerpos) is None


def test_el_parecido_coseno_se_comporta():
    v = vector(42)
    assert coseno(v, v) > 0.999, "un vector consigo mismo tiene que dar 1"
    assert coseno(v, vector(43)) < 0.5, "vectores distintos no pueden parecerse tanto"
    assert coseno(v, []) == -1.0, "sin vector no hay parecido"
    assert coseno(v, [0.0] * DIM) == -1.0, "un vector nulo no se parece a nada"


def test_la_memoria_de_desconocidos_esta_acotada():
    """En un lugar de paso, no puede crecer sin límite."""
    cfg = ConfigRostros(maxDesconocidos=20)
    ident = con_empleados(cfg=cfg)
    for i in range(200):
        ident.identificar([rostro(vector(1000 + i))], ahora=1000.0 + i * 0.1)
    assert ident.desconocidos.recordados <= 20, (
        f"la memoria creció a {ident.desconocidos.recordados}"
    )


# ── que la cola de revisión siga siendo usable ─────────────────────

def test_no_se_repregunta_por_la_misma_persona_de_otro_angulo():
    """El fallo real: 29 preguntas por 3 personas en dos minutos.

    Entre dos vistas de la misma cara con distinto giro, el parecido cae —medido
    en cámara, hasta +0.30—. Al compararlo contra el umbral de IDENTIDAD (0.42)
    cada vista parecía una persona nueva y volvía a preguntar.
    """
    ident = con_empleados()
    v1 = vector(7)
    assert ident.identificar([rostro(v1)], ahora=1000.0)[0].preguntar

    # Misma persona, otro ángulo: parecido por encima del umbral de repregunta
    # pero por debajo del de identidad. 0.30 es el mínimo medido en cámara
    # entre dos vistas de la misma cara.
    v2 = a_parecido(v1, 0.30)
    parecido = coseno(v1, v2)
    assert 0.25 <= parecido < 0.42, f"el caso de prueba no reproduce el escenario ({parecido:.3f})"

    r = ident.identificar([rostro(v2)], ahora=1010.0)[0]
    assert not r.preguntar, f"se repreguntó por la misma persona (parecido {parecido:.3f})"


def test_a_una_persona_distinta_si_se_le_pregunta():
    """El umbral bajo no puede tragarse a alguien nuevo."""
    ident = con_empleados()
    ident.identificar([rostro(vector(11))], ahora=1000.0)
    r = ident.identificar([rostro(vector(12))], ahora=1001.0)[0]
    assert r.preguntar, "otra persona tiene que generar su propia pregunta"


def test_no_se_pregunta_por_una_nuca():
    """Un recorte de perfil es una pregunta que el operador no puede contestar."""
    ident = con_empleados()
    r = ident.identificar(
        [Rostro(vector=vector(21), x=.4, y=.1, w=.12, h=.16, calidad=.9, yaw=75.0)],
        ahora=1000.0,
    )[0]
    assert not r.preguntar and "perfil" in r.motivo, r.motivo


def test_no_se_pregunta_por_una_coronilla():
    """De frente a la cámara pero mirando el escritorio: se ve la cabeza, no la cara."""
    ident = con_empleados()
    r = ident.identificar(
        [Rostro(vector=vector(23), x=.4, y=.1, w=.12, h=.16, calidad=.9, yaw=3.0, pitch=-55.0)],
        ahora=1000.0,
    )[0]
    assert not r.preguntar and "piso" in r.motivo, r.motivo


def test_una_cara_de_frente_si_pregunta():
    """El contrapeso de los dos anteriores: el filtro no puede bloquear todo."""
    ident = con_empleados()
    r = ident.identificar(
        [Rostro(vector=vector(24), x=.4, y=.1, w=.12, h=.16, calidad=.8, yaw=-12.0, pitch=-9.0)],
        ahora=1000.0,
    )[0]
    assert r.preguntar, r.motivo


def test_no_se_pregunta_por_una_cara_borrosa():
    """Y además sería una mala primera plantilla para esa persona."""
    ident = con_empleados()
    r = ident.identificar(
        [Rostro(vector=vector(22), x=.4, y=.1, w=.12, h=.16, calidad=.4)], ahora=1000.0,
    )[0]
    assert not r.preguntar, r.motivo


def test_una_cara_que_no_sirve_para_preguntar_igual_identifica():
    """Reconocer a alguien ya dado de alta tolera mucho más que darlo de alta."""
    juan = Persona("p1", "Juan", [vector(31)])
    ident = con_empleados(juan)
    r = ident.identificar(
        [Rostro(vector=vector(31, ruido=0.3), x=.4, y=.1, w=.06, h=.06,
                calidad=.5, yaw=70.0)],
        ahora=1000.0,
    )[0]
    assert r.persona_id == "p1", f"no se lo identificó: {r.motivo}"


def test_una_cara_descartada_no_se_recuerda_como_preguntada():
    """Si no se preguntó, no puede bloquear la pregunta de cuando sí se pueda."""
    ident = con_empleados()
    perfil = Rostro(vector=vector(41), x=.4, y=.1, w=.12, h=.16, calidad=.9, yaw=75.0)
    ident.identificar([perfil], ahora=1000.0)
    assert ident.desconocidos.recordados == 0

    # La misma persona, ahora de frente: acá sí hay que preguntar.
    r = ident.identificar([rostro(vector(41))], ahora=1005.0)[0]
    assert r.preguntar, "al darse vuelta tiene que poder preguntarse"


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
