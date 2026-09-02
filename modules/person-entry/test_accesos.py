"""Pruebas del registro de pasos y de la alerta de acceso denegado.

Lo que se protege: que el registro se lea como un control de accesos —una
entrada por visita, no una por frame—, que una alerta urgente no se ahogue en
repeticiones, y que quitarle el acceso a alguien no reescriba lo que ya pasó.
"""
from __future__ import annotations

import sys

from accesos import Permanencia, ConfigAccesos, RegistroDePasos


def registro(**kw) -> RegistroDePasos:
    return RegistroDePasos(ConfigAccesos(**kw))


# ═══════════════════════════════════════════════════════════════════

def test_una_jornada_es_un_paso_y_no_mil():
    """Cuatro horas frente a la cámara son UNA entrada en el registro."""
    r = registro(cierreSegundos=90.0)
    t = 1000.0
    for i in range(4 * 60 * 30):        # 4 h a 2 s por frame
        t = 1000.0 + i * 2.0
        r.ver("p-juan", "Juan", ahora=t)

    assert len(r.en_curso) == 1
    paso = r.en_curso[0]
    assert paso.desde == 1000.0
    assert abs((paso.hasta - paso.desde) - 4 * 3600 + 2) < 3, (paso.desde, paso.hasta)


def test_irse_y_volver_son_dos_pasos():
    """Un control de accesos tiene que distinguir dos visitas."""
    r = registro(cierreSegundos=90.0, minimoParaRegistrarSegundos=1.0)
    for t in (1000.0, 1002.0, 1004.0):
        r.ver("p-juan", "Juan", ahora=t)

    # Se va media hora.
    cerrados = r.cerrar_vencidos(ahora=1004.0 + 1800)
    assert len(cerrados) == 1 and cerrados[0].desde == 1000.0

    # Vuelve: paso nuevo, no continuación del anterior.
    p = r.ver("p-juan", "Juan", ahora=1004.0 + 1800)
    assert p.desde == 1004.0 + 1800, "le pegó la visita de la mañana con la de la tarde"


def test_taparse_un_momento_no_parte_la_visita():
    """La gente se tapa entre sí y se agacha. Eso no es irse."""
    r = registro(cierreSegundos=90.0, minimoParaRegistrarSegundos=1.0)
    r.ver("p-juan", "Juan", ahora=1000.0)
    # Desaparece 40 s y vuelve.
    p = r.ver("p-juan", "Juan", ahora=1040.0)
    assert p.desde == 1000.0, "partió la visita por una oclusión de 40 s"
    assert r.cerrar_vencidos(ahora=1041.0) == []


def test_pasar_de_largo_no_deja_registro():
    """Cruzar el cuadro camino a otro lado no es una visita."""
    r = registro(minimoParaRegistrarSegundos=3.0, cierreSegundos=10.0)
    r.ver("p-x", "X", ahora=1000.0)
    r.ver("p-x", "X", ahora=1001.0)
    assert r.cerrar_vencidos(ahora=1030.0) == [], "registró a alguien que pasó un segundo"


def test_el_paso_se_reporta_apenas_entra():
    """Quién está adentro AHORA es la primera pregunta de un control de accesos.

    Si el paso sólo se guardara al cerrarse, alguien que lleva ocho horas
    adentro no aparecería en el registro hasta irse.
    """
    r = registro(reporteSegundos=30.0)
    p = r.ver("p-juan", "Juan", ahora=1000.0)
    assert r.toca_reportar(p, ahora=1000.0), "no se reportó al entrar"


def test_el_paso_abierto_no_se_reporta_en_cada_frame():
    """Una escritura por frame en la base, por persona, no escala."""
    r = registro(reporteSegundos=30.0)
    p = r.ver("p-juan", "Juan", ahora=1000.0)
    r.toca_reportar(p, ahora=1000.0)

    reportes = 0
    for i in range(1, 60):              # 2 minutos a 2 s
        t = 1000.0 + i * 2.0
        r.ver("p-juan", "Juan", ahora=t)
        if r.toca_reportar(p, ahora=t):
            reportes += 1
    # Dos minutos a uno cada treinta segundos.
    assert 2 <= reportes <= 5, f"reportó {reportes} veces en dos minutos"


def test_la_alerta_suena_al_entrar():
    r = registro()
    p = r.ver("p-ajeno", "Ex empleado", ahora=1000.0, tiene_acceso=False)
    assert r.debe_alertar(p, ahora=1000.0, tiene_acceso=False), "no avisó de alguien sin acceso"


def test_la_alerta_no_se_repite_en_cada_frame():
    """Una alerta urgente que suena mil veces deja de ser urgente."""
    r = registro(repetirAlertaSegundos=300.0)
    p = r.ver("p-ajeno", "Ex empleado", ahora=1000.0, tiene_acceso=False)
    assert r.debe_alertar(p, ahora=1000.0, tiene_acceso=False)

    repeticiones = 0
    for i in range(1, 150):             # 5 minutos a 2 s
        t = 1000.0 + i * 2.0
        r.ver("p-ajeno", "Ex empleado", ahora=t, tiene_acceso=False)
        if r.debe_alertar(p, ahora=t, tiene_acceso=False):
            repeticiones += 1
    assert repeticiones <= 1, f"emitió {repeticiones} alertas en cinco minutos"


def test_pero_si_sigue_adentro_vuelve_a_sonar():
    """Que no se repita no puede significar que se olvide."""
    r = registro(repetirAlertaSegundos=300.0)
    p = r.ver("p-ajeno", "Ex empleado", ahora=1000.0, tiene_acceso=False)
    r.debe_alertar(p, ahora=1000.0, tiene_acceso=False)
    assert r.debe_alertar(p, ahora=1000.0 + 301, tiene_acceso=False), "dejó de avisar de alguien que sigue adentro"


def test_quien_tiene_acceso_no_dispara_nada():
    r = registro()
    p = r.ver("p-juan", "Juan", ahora=1000.0, tiene_acceso=True)
    assert not r.debe_alertar(p, ahora=1000.0, tiene_acceso=True)
    assert not r.debe_alertar(p, ahora=1000.0 + 10_000, tiene_acceso=True)


def test_quitarle_el_acceso_a_alguien_que_ESTA_ADENTRO_alerta():
    """El caso más importante, y el que estaba roto.

    Si a alguien se le quita el acceso mientras está en el cuadro, lo que hace
    falta saber es que está adentro AHORA. La alerta mira el acceso de ahora; lo
    que se congela es el registro de lo que ya pasó, no la alarma.
    """
    r = registro()
    p = r.ver("p-juan", "Juan", ahora=1000.0, tiene_acceso=True)
    assert not r.debe_alertar(p, ahora=1000.0, tiene_acceso=True)

    # Se le revoca el acceso sin que se haya movido del lugar.
    p = r.ver("p-juan", "Juan", ahora=1010.0, tiene_acceso=False)
    assert r.debe_alertar(p, ahora=1010.0, tiene_acceso=False), (
        "no avisó de alguien a quien se le quitó el acceso y sigue adentro"
    )
    # Y el registro de esa visita sigue diciendo que entró con permiso.
    assert p.tenia_acceso is True


def test_quitarle_el_acceso_no_reescribe_lo_que_ya_paso():
    """Lo que hizo con permiso, lo hizo con permiso.

    Si al revocarle el acceso el paso en curso pasara a figurar como denegado,
    el registro diría que entró sin permiso a una hora en la que sí lo tenía.
    """
    r = registro()
    p = r.ver("p-juan", "Juan", ahora=1000.0, tiene_acceso=True)
    for i in range(1, 20):
        p = r.ver("p-juan", "Juan", ahora=1000.0 + i * 2.0, tiene_acceso=False)
    assert p.tenia_acceso is True, "el paso en curso cambió de estado retroactivamente"

    # La próxima visita sí arranca sin acceso.
    r.cerrar_vencidos(ahora=1000.0 + 10_000)
    nuevo = r.ver("p-juan", "Juan", ahora=1000.0 + 10_000, tiene_acceso=False)
    assert nuevo.tenia_acceso is False


def test_el_mejor_parecido_es_el_del_mejor_momento():
    """Un paso sostenido por continuidad no puede mostrarse como si se le
    hubiera visto la cara todo el tiempo, ni al revés."""
    r = registro()
    r.ver("p-juan", "Juan", ahora=1000.0, parecido=0.0, por_rostro=False)
    r.ver("p-juan", "Juan", ahora=1002.0, parecido=0.71, por_rostro=True)
    p = r.ver("p-juan", "Juan", ahora=1004.0, parecido=0.30, por_rostro=False)
    assert p.mejor_parecido == 0.71
    assert p.visto_por_rostro is True


def test_soltar_la_camara_no_pierde_lo_que_estaba_pasando():
    r = registro(minimoParaRegistrarSegundos=1.0)
    r.ver("p-a", "Ana", ahora=1000.0)
    r.ver("p-a", "Ana", ahora=1010.0)
    cerrados = r.cerrar_todo()
    assert len(cerrados) == 1 and cerrados[0].persona_id == "p-a"
    assert r.en_curso == []


if __name__ == "__main__":
    pruebas = [(n, f) for n, f in sorted(globals().items())
               if n.startswith("test_") and callable(f)]
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


# ── el cronómetro de un cuerpo en el cuadro ─────────────────────────


def test_el_cronometro_arranca_cuando_aparece_el_cuerpo():
    """Y no cuando se le reconoce la cara, que puede ser mucho después."""
    p = Permanencia(gracia_segundos=5.0)
    p.ver([7], 1000.0)
    assert p.desde_de(7, 1000.0) == 1000.0
    p.ver([7], 1030.0)
    assert p.desde_de(7, 1030.0) == 1000.0, "seguir viéndolo no puede reiniciar el conteo"


def test_un_parpadeo_del_seguidor_no_reinicia_el_cronometro():
    """Pasa varias veces por minuto: alguien se tapa, se agacha, pasa otro por delante.

    Si cada parpadeo volviera el conteo a cero, el número que muestra la
    pantalla no significaría nada.
    """
    p = Permanencia(gracia_segundos=5.0)
    p.ver([7], 1000.0)
    p.ver([], 1002.0)          # se lo perdió dos segundos
    p.ver([7], 1004.0)
    assert p.desde_de(7, 1004.0) == 1000.0


def test_si_se_fue_de_verdad_el_cronometro_empieza_de_nuevo():
    p = Permanencia(gracia_segundos=5.0)
    p.ver([7], 1000.0)
    p.ver([], 1020.0)          # veinte segundos sin verlo: se fue
    p.ver([7], 1021.0)
    assert p.desde_de(7, 1021.0) == 1021.0


def test_cada_cuerpo_lleva_su_propio_tiempo():
    p = Permanencia(gracia_segundos=5.0)
    p.ver([7], 1000.0)
    p.ver([7, 9], 1050.0)
    assert p.desde_de(7, 1050.0) == 1000.0
    assert p.desde_de(9, 1050.0) == 1050.0, "el que recién llega no hereda el tiempo del otro"


def test_de_un_cuerpo_que_no_se_vio_nunca_no_se_inventa_tiempo():
    p = Permanencia(gracia_segundos=5.0)
    assert p.desde_de(99, 1000.0) == 1000.0, "tiene que dar cero, no un tiempo cualquiera"


def test_la_memoria_no_crece_con_los_que_ya_no_estan():
    """En un lugar de paso, el seguidor reparte cientos de identificadores por hora."""
    p = Permanencia(gracia_segundos=5.0)
    for i in range(500):
        p.ver([i], 1000.0 + i)
    assert len(p) <= 6, f"quedaron {len(p)} cuerpos colgados"
