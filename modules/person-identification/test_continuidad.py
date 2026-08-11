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
