"""Pruebas de la identidad sostenida cuando la cara no se ve.

El caso que motiva todo esto: alguien identificado al llegar, que se sienta de
espaldas el resto del día. Si la identidad no lo acompaña, el informe dice "sin
identificar" durante siete horas y medias y no sirve para nada.

Y la contracara, que es la que puede hacer daño: esa continuidad no puede
heredarle la identidad de una persona a otra.
"""
from __future__ import annotations

import math
import random

from continuidad import ConfigContinuidad, IdentidadSostenida, firma_apariencia

DIM = 64


def apariencia(semilla: int, ruido: float = 0.0) -> list[float]:
    """Firma de apariencia reproducible (ropa, colores)."""
    r = random.Random(semilla)
    base = [abs(r.gauss(0, 1)) for _ in range(DIM)]
    if ruido > 0:
        rr = random.Random(semilla * 31 + 7)
        base = [max(b + rr.gauss(0, ruido), 0.0) for b in base]
    return firma_apariencia(base)


# ═══════════════════════════════════════════════════════════════════

def test_la_identidad_sobrevive_a_darse_vuelta():
    """EL CASO QUE MOTIVA TODO: se lo identifica al llegar y se sienta de espaldas.

    Se recorre una jornada entera al ritmo real del módulo (un frame cada dos
    segundos). Sin esta continuidad, el informe diría "sin identificar" durante
    siete horas y media.
    """
    ident = IdentidadSostenida()
    ropa = apariencia(1)

    # 08:00 — llega mirando a la cámara y se le ve la cara.
    ident.anclar_por_rostro(7, "p-juan", "Juan", ropa, (0.3, 0.6), ahora=0.0)

    # El resto de la jornada, de espaldas. El tracker no lo pierde.
    paso = 2.0
    for i in range(1, int(8 * 3600 / paso)):
        t = i * paso
        r = ident.resolver(7, None, (0.3, 0.6), ahora=t)
        assert r.persona_id == "p-juan", f"lo perdió a las {t/3600:.1f} h ({r.via})"
        assert r.via == "seguimiento"


def test_si_el_pipeline_se_atrasa_cae_a_la_apariencia():
    """Un parate largo corta la continuidad, y ahí tiene que salvarlo la ropa.

    Importa que degrade a la vía siguiente y no a "sin identificar": un atraso
    del sistema no puede convertirse en un agujero en el informe de alguien.
    """
    cfg = ConfigContinuidad(trackGraciaSegundos=5.0)
    ident = IdentidadSostenida(cfg)
    ropa = apariencia(12)
    ident.anclar_por_rostro(4, "p-juan", "Juan", ropa, (0.3, 0.6), ahora=0.0)

    # El worker se atrasa medio minuto: la continuidad del track ya no vale.
    r = ident.resolver(4, apariencia(12, ruido=0.04), (0.3, 0.6), ahora=30.0)
    assert r.persona_id == "p-juan", f"perdió a Juan tras el atraso ({r.via})"
    assert r.via in ("apariencia", "puesto"), (
        f"debía reengancharlo por otra vía, no por seguimiento; usó {r.via}"
    )


def test_se_reengancha_por_la_ropa_tras_perder_el_seguimiento():
    """Alguien pasa por delante, el tracker le da un id nuevo, sigue de espaldas."""
    ident = IdentidadSostenida()
    ropa = apariencia(2)
    ident.anclar_por_rostro(3, "p-ana", "Ana", ropa, (0.7, 0.6), ahora=100.0)

    # El seguimiento se corta y vuelve con OTRO id, sin ver la cara.
    r = ident.resolver(88, apariencia(2, ruido=0.05), (0.7, 0.6), ahora=400.0)
    assert r.persona_id == "p-ana", f"no la reenganchó: {r.via}"
    assert r.via in ("apariencia", "puesto")


def test_no_le_hereda_la_identidad_a_otra_persona():
    """El error caro: que el track reciclado le pase el nombre al siguiente.

    Los identificadores del tracker se reutilizan. Sin caducidad, la persona que
    reciba el número 7 después de que Juan se fue heredaría su nombre y su
    tiempo — incluido el del teléfono.
    """
    cfg = ConfigContinuidad(trackGraciaSegundos=5.0)
    ident = IdentidadSostenida(cfg)
    ident.anclar_por_rostro(7, "p-juan", "Juan", apariencia(1), (0.3, 0.6), ahora=100.0)

    # Juan se va. Un rato después, otra persona con otra ropa recibe el id 7.
    r = ident.resolver(7, apariencia(999), (0.3, 0.6), ahora=100.0 + 600)
    assert r.persona_id != "p-juan", "le heredó la identidad de Juan a otra persona"
    assert r.persona_id is None


def test_dos_personas_con_ropa_parecida_no_se_confunden():
    """Con uniformes, la apariencia deja de distinguir: mejor no identificar.

    Elegir a cualquiera de los dos le atribuiría a uno el tiempo del otro sin
    que nadie pueda notarlo.
    """
    ident = IdentidadSostenida()
    base = apariencia(5)
    casi_igual = firma_apariencia([x + 0.001 for x in base])

    ident.anclar_por_rostro(1, "p-a", "Persona A", base, (0.2, 0.6), ahora=0.0)
    ident.anclar_por_rostro(2, "p-b", "Persona B", casi_igual, (0.8, 0.6), ahora=0.0)

    # Un track nuevo, de espaldas, con esa misma ropa.
    r = ident.resolver(50, apariencia(5, ruido=0.02), None, ahora=300.0)
    assert r.persona_id is None, (
        f"con dos personas de ropa casi idéntica eligió a {r.nombre}"
    )


def test_la_ropa_de_ayer_no_identifica_hoy():
    """La firma de apariencia vence: mañana viene con otra remera.

    Sin vencimiento, alguien que hoy usa la misma ropa que Juan usó ayer sería
    identificado como Juan.
    """
    cfg = ConfigContinuidad(aparienciaHoras=8.0)
    ident = IdentidadSostenida(cfg)
    ropa_de_ayer = apariencia(3)
    ident.anclar_por_rostro(1, "p-juan", "Juan", ropa_de_ayer, (0.3, 0.6), ahora=0.0)

    # Al día siguiente, otra persona con esa misma ropa.
    veinticuatro_horas = 24 * 3600.0
    r = ident.resolver(60, ropa_de_ayer, (0.3, 0.6), ahora=veinticuatro_horas)
    assert r.persona_id is None, "la firma de ayer identificó a alguien hoy"


def test_el_puesto_refuerza_pero_no_decide_solo():
    """Volver al mismo lugar sube la confianza; no alcanza sin la apariencia."""
    ident = IdentidadSostenida()
    ident.anclar_por_rostro(1, "p-juan", "Juan", apariencia(4), (0.35, 0.62), ahora=0.0)

    # Misma ropa Y mismo puesto: la vía es "puesto" y la confianza sube.
    con_puesto = ident.resolver(70, apariencia(4, ruido=0.04), (0.35, 0.62), ahora=500.0)
    assert con_puesto.persona_id == "p-juan"
    assert con_puesto.via == "puesto"

    # Otro que se sienta en el puesto de Juan, con SU propia ropa: no es Juan.
    otro = IdentidadSostenida()
    otro.anclar_por_rostro(1, "p-juan", "Juan", apariencia(4), (0.35, 0.62), ahora=0.0)
    r = otro.resolver(71, apariencia(888), (0.35, 0.62), ahora=500.0)
    assert r.persona_id is None, "el puesto solo no puede decidir quién es"


def test_se_reconoce_aunque_se_saque_el_saco():
    """Se guardan varias firmas: cambiar de abrigo no borra la identidad."""
    ident = IdentidadSostenida()
    con_saco = apariencia(10)
    sin_saco = apariencia(11)

    ident.anclar_por_rostro(1, "p-juan", "Juan", con_saco, (0.3, 0.6), ahora=0.0)
    # A media mañana se le ve la cara de nuevo, ya sin el saco.
    ident.anclar_por_rostro(1, "p-juan", "Juan", sin_saco, (0.3, 0.6), ahora=3600.0)

    for etiqueta, ropa in (("con saco", con_saco), ("sin saco", sin_saco)):
        r = ident.resolver(99, ropa, None, ahora=4000.0)
        assert r.persona_id == "p-juan", f"no lo reconoció {etiqueta}"


def test_cada_identificacion_dice_por_que_via_se_supo():
    """El informe tiene que poder mostrar de dónde sale su propio número."""
    ident = IdentidadSostenida()
    ident.anclar_por_rostro(1, "p-juan", "Juan", apariencia(1), (0.3, 0.6), ahora=0.0)

    por_seguimiento = ident.resolver(1, None, (0.3, 0.6), ahora=2.0)
    assert por_seguimiento.via == "seguimiento"
    assert por_seguimiento.confianza > 0.9

    sin_nada = ident.resolver(555, None, None, ahora=10.0)
    assert sin_nada.via == "ninguna"
    assert sin_nada.confianza == 0.0


def test_la_memoria_no_crece_sin_limite():
    """Un día entero de tracks reciclados no puede acumularse."""
    ident = IdentidadSostenida()
    ident.anclar_por_rostro(1, "p-juan", "Juan", apariencia(1), (0.3, 0.6), ahora=0.0)
    for i in range(5000):
        ident.resolver(1000 + i, None, None, ahora=i * 1.0)
    estado = ident.estado()
    assert estado["seguimientosAnclados"] < 100, (
        f"quedaron {estado['seguimientosAnclados']} seguimientos en memoria"
    )


# ── el puesto como identidad, y lo que impide que se herede ────────

def test_el_puesto_lo_identifica_aunque_la_ropa_ya_no_sirva():
    """Lo que se le pide al sistema: verle la cara UNA vez y saber que es él ahí.

    Se le ve la cara al llegar, se sienta de espaldas y —cambio de luz, se saca
    el saco, se tapa el torso con el escritorio— la apariencia deja de servir.
    El escritorio sigue siendo el suyo mientras no se haya ido.
    """
    ident = IdentidadSostenida()
    ident.anclar_por_rostro(1, "p-juan", "Juan", apariencia(1), (0.35, 0.62), ahora=0.0)

    t = 0.0
    vias = set()
    for i in range(1, 400):          # ~13 minutos a 2 s por frame
        t = i * 2.0
        # Track nuevo en cada frame (el peor caso: el tracker no ayuda nada) y
        # una apariencia que no se parece a la que se registró.
        r = ident.resolver(1000 + i, apariencia(777), (0.35, 0.62), ahora=t)
        assert r.persona_id == "p-juan", f"lo perdió en el minuto {t/60:.1f} ({r.via})"
        vias.add(r.via)

    assert vias == {"puesto"}, vias


def test_si_se_fue_el_puesto_ya_no_lo_identifica():
    """La contracara: el escritorio vacío un rato deja de ser de nadie.

    Es el error caro de esta vía. Juan se fue, se sienta otro en su lugar, y sin
    esto le quedaría el nombre de Juan y su tiempo con el teléfono.
    """
    ident = IdentidadSostenida()
    ident.anclar_por_rostro(1, "p-juan", "Juan", apariencia(1), (0.35, 0.62), ahora=0.0)
    assert ident.resolver(50, apariencia(777), (0.35, 0.62), ahora=10.0).persona_id == "p-juan"

    # Nadie en ese puesto durante un minuto. Después llega otro.
    r = ident.resolver(51, apariencia(777), (0.35, 0.62), ahora=80.0)
    assert r.persona_id is None, f"le heredó la identidad de Juan a quien se sentó después ({r.via})"


def test_un_escritorio_compartido_no_identifica_a_ninguno():
    """Dos personas con la cara vista en el mismo lugar: no se puede elegir."""
    ident = IdentidadSostenida()
    ident.anclar_por_rostro(1, "p-a", "Ana", apariencia(2), (0.40, 0.60), ahora=0.0)
    ident.anclar_por_rostro(2, "p-b", "Beto", apariencia(3), (0.42, 0.61), ahora=0.0)

    r = ident.resolver(60, apariencia(999), (0.41, 0.60), ahora=10.0)
    assert r.persona_id is None, f"eligió a {r.nombre} en un escritorio compartido"


def test_un_cuerpo_lejos_del_puesto_no_es_esa_persona():
    """El puesto identifica a quien está EN el puesto, no a cualquiera del cuadro.

    Sin la distancia, el escritorio de Juan le pondría su nombre a todo el que
    aparezca mientras él sigue sentado ahí.
    """
    ident = IdentidadSostenida()
    ident.anclar_por_rostro(1, "p-juan", "Juan", apariencia(1), (0.35, 0.62), ahora=0.0)

    # Otro cuerpo, al otro lado del cuadro, sin ropa reconocible.
    r = ident.resolver(80, apariencia(777), (0.85, 0.62), ahora=10.0)
    assert r.persona_id is None, f"identificó como Juan a alguien lejos de su puesto ({r.via})"


def test_el_puesto_caduca_aunque_la_persona_no_se_haya_movido():
    """Presente sin interrupción todo el día, con la cara vista sólo a la mañana.

    El puesto no puede valer para siempre por el solo hecho de que haya un
    cuerpo ahí: pasadas las horas de una jornada hay que volver a verle la cara.
    De lo contrario, el turno siguiente hereda el nombre del anterior sin que el
    escritorio haya estado vacío un segundo.
    """
    cfg = ConfigContinuidad(puestoHoras=8.0)
    ident = IdentidadSostenida(cfg)
    ident.anclar_por_rostro(1, "p-juan", "Juan", apariencia(1), (0.35, 0.62), ahora=0.0)

    # Presente cada 10 s durante nueve horas, sin volver a mostrar la cara ni
    # una ropa reconocible: sólo lo sostiene el puesto.
    t = 0.0
    identificado_hasta = 0.0
    while t < 9 * 3600.0:
        t += 10.0
        r = ident.resolver(int(t), apariencia(777), (0.35, 0.62), ahora=t)
        if r.persona_id == "p-juan":
            identificado_hasta = t
        else:
            break

    assert identificado_hasta > 7 * 3600.0, (
        f"lo perdió demasiado pronto: a las {identificado_hasta/3600:.1f} h"
    )
    assert identificado_hasta < 8.5 * 3600.0, (
        f"lo siguió identificando por puesto {identificado_hasta/3600:.1f} h después "
        "de verle la cara: el turno siguiente heredaría su nombre"
    )


def test_perder_el_seguimiento_unos_segundos_no_hereda_identidad():
    """La ventana corta: entre la gracia del track y su purga.

    Cinco segundos después de perderlo, ese número de seguimiento ya no
    garantiza que sea la misma persona — y el tracker los reutiliza.
    """
    cfg = ConfigContinuidad(trackGraciaSegundos=5.0)
    ident = IdentidadSostenida(cfg)
    ident.anclar_por_rostro(7, "p-juan", "Juan", apariencia(1), (0.30, 0.62), ahora=100.0)

    # A los 10 s: la entrada del track todavía existe, pero ya venció su gracia.
    # Otra persona, otra ropa, otro lugar.
    r = ident.resolver(7, apariencia(999), (0.80, 0.62), ahora=110.0)
    assert r.persona_id is None, (
        f"a los 10 s de perder el seguimiento le pasó la identidad de Juan a otro ({r.via})"
    )


def test_el_puesto_no_sobrevive_a_la_jornada():
    """Mañana ese escritorio puede ser de otro: hay que volver a verle la cara."""
    cfg = ConfigContinuidad(puestoHoras=8.0)
    ident = IdentidadSostenida(cfg)
    ident.anclar_por_rostro(1, "p-juan", "Juan", apariencia(1), (0.35, 0.62), ahora=0.0)

    al_dia_siguiente = 20 * 3600.0
    r = ident.resolver(90, apariencia(1), (0.35, 0.62), ahora=al_dia_siguiente)
    assert r.persona_id is None, "el puesto de ayer no puede identificar a nadie hoy"


def test_caminar_por_la_oficina_no_le_mueve_el_puesto():
    """El puesto es donde se le vio la CARA, no donde estaba el último cuerpo.

    Antes lo pisaba cualquier resolución: quien se levantaba a la impresora
    dejaba su "puesto" allá, y volver a su escritorio ya no lo reconocía.
    """
    ident = IdentidadSostenida()
    ropa = apariencia(1)
    ident.anclar_por_rostro(1, "p-juan", "Juan", ropa, (0.35, 0.62), ahora=0.0)

    # Se levanta y camina: se lo sigue reconociendo por la ropa, lejos del puesto.
    for i, x in enumerate([0.45, 0.55, 0.70, 0.85], start=1):
        r = ident.resolver(1, ropa, (x, 0.62), ahora=i * 2.0)
        assert r.persona_id == "p-juan"

    # Vuelve a sentarse. Ahora sin ropa reconocible: sólo puede salvarlo el puesto.
    r = ident.resolver(200, apariencia(777), (0.35, 0.62), ahora=12.0)
    assert r.persona_id == "p-juan", f"se le movió el puesto al caminar ({r.via})"


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


# ── dar de baja a alguien tiene que borrar lo que se dedujo de él ───


def test_la_baja_de_una_persona_se_lleva_su_identidad_sostenida():
    """Sin esto, a quien se borra mientras está frente a la cámara se le sigue
    mostrando el nombre en pantalla hasta que se vaya del cuadro.

    Pasó de verdad: la pantalla decía un nombre y al mismo tiempo no sabía si
    esa persona tenía acceso, porque el nombre salía del anclaje y el acceso de
    la galería, que ya no la tenía.
    """
    s = IdentidadSostenida(ConfigContinuidad())
    s.anclar_por_rostro(
        track_id=7, persona_id="p1", nombre="Juan",
        apariencia=[1.0, 0.0], posicion=(0.5, 0.8), ahora=1000.0,
    )
    assert s.resolver(7, [1.0, 0.0], (0.5, 0.8), 1001.0).persona_id == "p1"

    olvidados = s.conservar_solo({"p2", "p3"})   # a p1 se le dio de baja

    assert olvidados == 1
    r = s.resolver(7, [1.0, 0.0], (0.5, 0.8), 1002.0)
    assert r.persona_id is None, "se le sigue poniendo el nombre a alguien dado de baja"
    assert r.via == "ninguna"


def test_conservar_solo_no_toca_a_los_que_siguen_dados_de_alta():
    s = IdentidadSostenida(ConfigContinuidad())
    s.anclar_por_rostro(7, "p1", "Juan", [1.0, 0.0], (0.5, 0.8), 1000.0)
    s.anclar_por_rostro(8, "p2", "Ana", [0.0, 1.0], (0.2, 0.8), 1000.0)

    assert s.conservar_solo({"p1", "p2"}) == 0
    assert s.resolver(7, [1.0, 0.0], (0.5, 0.8), 1001.0).persona_id == "p1"
    assert s.resolver(8, [0.0, 1.0], (0.2, 0.8), 1001.0).persona_id == "p2"


def test_la_baja_tampoco_deja_al_puesto_reconociendo_al_que_se_fue():
    """El puesto identifica solo cuando hay un único candidato en ese lugar.

    Si el anclaje sobreviviera a la baja, ese candidato seguiría siendo la
    persona borrada y cualquiera que se sentara ahí heredaría su nombre.
    """
    s = IdentidadSostenida(ConfigContinuidad())
    s.anclar_por_rostro(7, "p1", "Juan", None, (0.5, 0.8), 1000.0)
    s.conservar_solo(set())
    assert s.resolver(99, None, (0.5, 0.8), 1010.0).persona_id is None
