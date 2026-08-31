"""Pruebas de cuándo se avisa que a alguien le falta un elemento de protección.

Lo que se protege acá es lo que separa un módulo que se usa de uno que se
apaga a la semana:

  - que no ver un casco NO sea lo mismo que ver que falta;
  - que un cuadro suelto no dispare una alerta;
  - que la misma persona no genere sesenta avisos por minuto;
  - que con dos personas juntas el casco se le atribuya a la correcta.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reglas import (  # noqa: E402
    ConfigEpp,
    VigiladorEpp,
    de_quien_es,
    evaluar_cuadro,
    solape,
)

#: Una persona parada a la izquierda, y otra a la derecha.
IZQ = (0.10, 0.10, 0.20, 0.80)
DER = (0.60, 0.10, 0.20, 0.80)

#: Un casco sobre la cabeza de cada una.
CASCO_IZQ = (0.14, 0.11, 0.10, 0.08)
CASCO_DER = (0.64, 0.11, 0.10, 0.08)


def cfg(**kw) -> ConfigEpp:
    base = dict(exigidos=("casco",), framesSeguidos=1, repetirSegundos=0.0)
    base.update(kw)
    return ConfigEpp(**base)


# ── a quién pertenece cada elemento ──────────────────────────────────

def test_el_casco_es_de_quien_lo_tiene_encima():
    assert de_quien_es(CASCO_IZQ, [IZQ, DER], 0.55) == 0
    assert de_quien_es(CASCO_DER, [IZQ, DER], 0.55) == 1


def test_un_casco_a_medio_camino_va_a_quien_mas_lo_contiene():
    # Dos personas juntas: el casco cae mayormente sobre la segunda.
    a = (0.10, 0.10, 0.20, 0.80)
    b = (0.28, 0.10, 0.20, 0.80)
    casco = (0.26, 0.11, 0.08, 0.07)  # 2/8 sobre `a`, 6/8 sobre `b`
    assert de_quien_es(casco, [a, b], 0.55) == 1


def test_un_casco_de_nadie_no_se_le_asigna_a_nadie():
    # Un casco colgado en la pared, lejos de todos.
    assert de_quien_es((0.90, 0.90, 0.05, 0.05), [IZQ, DER], 0.55) is None


def test_el_solape_se_mide_contra_el_elemento():
    # Un casco entero adentro de una persona da 1, aunque sea diminuto al lado
    # del cuerpo. Medirlo contra la unión daría casi cero y no asociaría nunca.
    assert solape(CASCO_IZQ, IZQ) == 1.0


def test_un_elemento_sin_area_no_rompe():
    assert solape((0.1, 0.1, 0.0, 0.0), IZQ) == 0.0


# ── qué se sabe en un cuadro ─────────────────────────────────────────

def test_ver_el_casco_puesto_es_saber_que_lo_tiene():
    r = evaluar_cuadro([IZQ], [("Hardhat", CASCO_IZQ, 0.9)], cfg())
    assert r[0]["casco"] == (True, 0.9)


def test_ver_una_cabeza_sin_casco_es_saber_que_falta():
    r = evaluar_cuadro([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], cfg())
    assert r[0]["casco"] == (False, 0.8)


def test_no_detectar_nada_no_es_saber_nada():
    # ESTA es la prueba que define el módulo. Sin detecciones, el resultado
    # está vacío: no se afirma que la persona esté sin casco.
    assert evaluar_cuadro([IZQ], [], cfg()) == {}


def test_una_deteccion_floja_se_descarta():
    r = evaluar_cuadro([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.10)], cfg(minConfianza=0.45))
    assert r == {}


def test_ante_dos_lecturas_manda_la_mas_confiable():
    r = evaluar_cuadro(
        [IZQ],
        [("NO-Hardhat", CASCO_IZQ, 0.55), ("Hardhat", CASCO_IZQ, 0.92)],
        cfg(),
    )
    assert r[0]["casco"] == (True, 0.92)


def test_ante_empate_gana_que_si_lo_tiene():
    # Acusar de una falta pide más evidencia que descartarla.
    r = evaluar_cuadro(
        [IZQ],
        [("NO-Hardhat", CASCO_IZQ, 0.70), ("Hardhat", CASCO_IZQ, 0.70)],
        cfg(),
    )
    assert r[0]["casco"] == (True, 0.70)


def test_lo_que_no_se_exige_no_se_mira():
    r = evaluar_cuadro([IZQ], [("NO-Safety Vest", CASCO_IZQ, 0.9)], cfg(exigidos=("casco",)))
    assert r == {}


def test_para_dibujar_se_miran_todos_los_elementos():
    """Lo que no se exige no alerta, pero sí se muestra.

    La pantalla tiene que poder dibujar el chaleco que la cámara ve aunque en
    esa cámara no sea obligatorio. Si sólo se devolviera lo exigido, un módulo
    que está mirando bien y uno que está roto se verían iguales.
    """
    detecciones = [("NO-Safety Vest", CASCO_IZQ, 0.9)]
    assert evaluar_cuadro([IZQ], detecciones, cfg(exigidos=("casco",))) == {}
    r = evaluar_cuadro([IZQ], detecciones, cfg(exigidos=("casco",)), solo_exigidos=False)
    assert r[0]["chaleco"] == (False, 0.9)


def test_mirar_todo_no_baja_la_vara_de_confianza():
    """Dibujar de más no es creer de más: la confianza mínima sigue rigiendo.

    Una caja floja de "NO-Hardhat" no alcanza para avisar, así que tampoco
    puede alcanzar para pintar de rojo a alguien en pantalla.
    """
    r = evaluar_cuadro(
        [IZQ], [("NO-Hardhat", CASCO_IZQ, 0.20)],
        cfg(minConfianza=0.45), solo_exigidos=False,
    )
    assert r == {}


# ── cuándo se avisa ──────────────────────────────────────────────────

def test_avisa_cuando_ve_que_falta():
    v = VigiladorEpp(cfg())
    f = v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], ahora=1.0)
    assert len(f) == 1
    assert f[0].elemento.clave == "casco"
    assert f[0].elemento.evento == "ppe.helmet_missing"


def test_no_avisa_por_un_cuadro_suelto():
    v = VigiladorEpp(cfg(framesSeguidos=3))
    assert v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], 1.0) == []
    assert v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], 1.3) == []
    assert len(v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], 1.6)) == 1


def test_ponerse_el_casco_reinicia_la_cuenta():
    v = VigiladorEpp(cfg(framesSeguidos=3))
    v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], 1.0)
    v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], 1.3)
    v.ver([IZQ], [("Hardhat", CASCO_IZQ, 0.9)], 1.6)          # se lo puso
    assert v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], 1.9) == []


def test_darse_vuelta_no_borra_lo_que_se_venia_viendo():
    # Un cuadro sin evidencia no acumula ni reinicia: la persona se dio vuelta,
    # no se puso el casco.
    v = VigiladorEpp(cfg(framesSeguidos=3))
    v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], 1.0)
    v.ver([IZQ], [], 1.3)                                       # se dio vuelta
    v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], 1.6)
    assert len(v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], 1.9)) == 1


def test_no_repite_el_aviso_por_la_misma_persona():
    v = VigiladorEpp(cfg(repetirSegundos=120.0))
    assert len(v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], 1.0)) == 1
    assert v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], 30.0) == []
    assert v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], 60.0) == []


def test_pasado_el_tiempo_vuelve_a_avisar():
    v = VigiladorEpp(cfg(repetirSegundos=120.0))
    v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], 1.0)
    assert len(v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], 200.0)) == 1


def test_dos_personas_se_cuentan_por_separado():
    v = VigiladorEpp(cfg())
    f = v.ver(
        [IZQ, DER],
        [("Hardhat", CASCO_IZQ, 0.9), ("NO-Hardhat", CASCO_DER, 0.8)],
        ahora=1.0,
    )
    assert len(f) == 1
    assert f[0].indice_persona == 1, "se le avisó a la persona equivocada"


def test_el_seguimiento_va_por_id_y_no_por_posicion():
    # Si dos personas se cruzan, sus posiciones en la lista se intercambian.
    # Con ids, la cuenta de cuadros seguidos sigue a la persona.
    v = VigiladorEpp(cfg(framesSeguidos=2))
    v.ver([IZQ, DER], [("NO-Hardhat", CASCO_DER, 0.8)], 1.0, ids=[7, 9])
    # Ahora vienen al revés: la del id 9 quedó primera.
    f = v.ver([DER, IZQ], [("NO-Hardhat", DER, 0.8)], 1.3, ids=[9, 7])
    assert len(f) == 1, "la cuenta se perdió al intercambiarse el orden"


def test_se_avisa_por_cada_elemento_exigido():
    v = VigiladorEpp(cfg(exigidos=("casco", "chaleco")))
    f = v.ver(
        [IZQ],
        [("NO-Hardhat", CASCO_IZQ, 0.8), ("NO-Safety Vest", (0.12, 0.25, 0.16, 0.30), 0.7)],
        ahora=1.0,
    )
    assert {x.elemento.clave for x in f} == {"casco", "chaleco"}


def test_cada_elemento_sale_con_su_tipo_de_evento():
    v = VigiladorEpp(cfg(exigidos=("chaleco",)))
    f = v.ver([IZQ], [("NO-Safety Vest", (0.12, 0.25, 0.16, 0.30), 0.8)], 1.0)
    assert f[0].elemento.evento == "ppe.vest_missing"


def test_se_olvida_a_quien_ya_no_esta():
    v = VigiladorEpp(cfg())
    v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.8)], 1.0, ids=[1])
    assert v.estado()["personasSeguidas"] == 1
    v.ver([], [], 1000.0)
    assert v.estado()["personasSeguidas"] == 0, "la memoria crecería toda la jornada"


def test_sin_personas_no_pasa_nada():
    v = VigiladorEpp(cfg())
    assert v.ver([], [("NO-Hardhat", CASCO_IZQ, 0.9)], 1.0) == []


# ── se le pide más evidencia a la ausencia que a la presencia ────────

def test_una_ausencia_floja_no_alcanza():
    # 0.50 pasa el umbral de presencia (0.45) pero no el de ausencia (0.60).
    # Un falso "sin casco" acusa a alguien; un falso "con casco" no.
    c = ConfigEpp(exigidos=("casco",), minConfianza=0.45, minConfianzaFalta=0.60)
    assert evaluar_cuadro([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.50)], c) == {}


def test_una_presencia_floja_sí_alcanza():
    c = ConfigEpp(exigidos=("casco",), minConfianza=0.45, minConfianzaFalta=0.60)
    r = evaluar_cuadro([IZQ], [("Hardhat", CASCO_IZQ, 0.50)], c)
    assert r[0]["casco"] == (True, 0.50)


def test_una_ausencia_firme_pasa():
    c = ConfigEpp(exigidos=("casco",), minConfianza=0.45, minConfianzaFalta=0.60)
    r = evaluar_cuadro([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.75)], c)
    assert r[0]["casco"] == (False, 0.75)


# ── el elemento tiene que caer donde va en el cuerpo ─────────────────

def test_un_casco_a_la_altura_de_los_pies_se_descarta():
    # Si el modelo pone un "sin casco" abajo del todo, se equivocó. Sin este
    # filtro esa equivocación se convierte en una alerta contra una persona.
    c = ConfigEpp(exigidos=("casco",), minConfianzaFalta=0.5)
    pies = (0.14, 0.82, 0.10, 0.08)
    assert evaluar_cuadro([IZQ], [("NO-Hardhat", pies, 0.9)], c) == {}


def test_un_casco_en_la_cabeza_pasa():
    c = ConfigEpp(exigidos=("casco",), minConfianzaFalta=0.5)
    r = evaluar_cuadro([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.9)], c)
    assert r[0]["casco"] == (False, 0.9)


def test_los_guantes_pueden_estar_abajo():
    # A diferencia del casco: las manos caen a media altura o más abajo.
    c = ConfigEpp(exigidos=("guantes",), minConfianzaFalta=0.5)
    mano = (0.12, 0.62, 0.06, 0.06)
    r = evaluar_cuadro([IZQ], [("NO-Gloves", mano, 0.9)], c)
    assert r[0]["guantes"] == (False, 0.9)


def test_se_puede_apagar_el_filtro_de_posicion():
    # Para una cámara con un encuadre muy raro, donde el filtro estorbe.
    c = ConfigEpp(exigidos=("casco",), minConfianzaFalta=0.5, verificarPosicion=False)
    pies = (0.14, 0.82, 0.10, 0.08)
    r = evaluar_cuadro([IZQ], [("NO-Hardhat", pies, 0.9)], c)
    assert r[0]["casco"] == (False, 0.9)


def test_la_banda_se_mide_contra_la_persona_no_contra_la_imagen():
    # La misma persona, lejos y chica arriba del cuadro: su casco sigue estando
    # en SU cabeza aunque en la imagen esté a media altura.
    lejos = (0.40, 0.30, 0.06, 0.24)
    casco = (0.41, 0.31, 0.04, 0.03)
    c = ConfigEpp(exigidos=("casco",), minConfianzaFalta=0.5)
    r = evaluar_cuadro([lejos], [("NO-Hardhat", casco, 0.9)], c)
    assert r[0]["casco"] == (False, 0.9)


# ── umbral propio de cada elemento ───────────────────────────────────

def test_cada_elemento_puede_tener_su_umbral():
    # El modelo está seguro de un chaleco y mucho menos de unas antiparras: un
    # solo número obliga a elegir entre no avisar de lo que ve bien o avisar de
    # más con lo que ve mal.
    c = ConfigEpp(exigidos=("casco", "chaleco"), minConfianzaFalta=0.60,
                  umbralPorElemento={"chaleco": 0.35})
    torso = (0.12, 0.25, 0.16, 0.30)
    r = evaluar_cuadro([IZQ], [("NO-Safety Vest", torso, 0.40)], c)
    assert r[0]["chaleco"] == (False, 0.40), "el umbral propio del chaleco no se aplicó"

    # El casco, sin umbral propio, sigue con el general.
    assert evaluar_cuadro([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.40)], c) == {}


def test_un_elemento_silenciado_no_alerta_pero_se_sigue_viendo():
    # El caso real: el modelo ve mal la ausencia de casco, así que no conviene
    # acusar a nadie por eso todavía; pero lo que detecta se sigue dibujando.
    c = ConfigEpp(exigidos=("casco",), framesSeguidos=1, repetirSegundos=0.0,
                  minConfianzaFalta=0.4, sinAlertar=("casco",))
    v = VigiladorEpp(c)
    assert v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.9)], 1.0) == []
    # Pero el cuadro lo sigue sabiendo: es lo que alimenta el dibujo.
    assert evaluar_cuadro([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.9)], c)[0]["casco"] == (False, 0.9)


def test_lo_no_silenciado_sigue_alertando():
    c = ConfigEpp(exigidos=("casco", "chaleco"), framesSeguidos=1, repetirSegundos=0.0,
                  minConfianzaFalta=0.4, sinAlertar=("casco",))
    v = VigiladorEpp(c)
    torso = (0.12, 0.25, 0.16, 0.30)
    f = v.ver([IZQ], [("NO-Hardhat", CASCO_IZQ, 0.9), ("NO-Safety Vest", torso, 0.9)], 1.0)
    assert [x.elemento.clave for x in f] == ["chaleco"]
