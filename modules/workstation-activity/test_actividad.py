"""Pruebas de la contabilidad de tiempo por puesto.

Lo que se protege acá es la honestidad del informe: que los segundos que reporta
sean segundos que realmente observó, que un corte de cámara no se rellene con
suposiciones, y que el teléfono de una persona no se le atribuya a otra.
"""
from __future__ import annotations

from actividad import (
    Caja,
    ConfigActividad,
    ContadorActividad,
    Observacion,
    Zona,
)

DT = 0.2  # 5 fps


def persona(x=0.4, y=0.3, w=0.15, h=0.5, conf=0.9) -> Caja:
    return Caja(x, y, w, h, conf)


def telefono_en(p: Caja, alto_rel=0.25, conf=0.6) -> Caja:
    """Teléfono a la altura del pecho de esa persona."""
    return Caja(p.x + p.w * 0.4, p.y + p.h * alto_rel, 0.04, 0.06, conf)


def correr(c: ContadorActividad, pasos, t0=1000.0):
    """pasos = lista de (personas, telefonos) a DT de separación."""
    muestras = []
    for i, (ps, ts_) in enumerate(pasos):
        muestras += c.observar(Observacion(ts=t0 + i * DT, personas=ps, telefonos=ts_))
    return muestras


# ═══════════════════════════════════════════════════════════════════

def test_cuenta_el_tiempo_ocupado():
    c = ContadorActividad([], ConfigActividad(windowSeconds=10.0))
    # 10 s con una persona presente (más un frame para cerrar la ventana).
    muestras = correr(c, [([persona()], []) for _ in range(51)])
    assert muestras, "debía cerrarse una ventana a los 10 s"
    m = muestras[0]
    assert 9.5 <= m.ocupado_s <= 10.5, f"debía contar ~10 s ocupados, contó {m.ocupado_s}"
    assert m.vacio_s == 0.0
    assert m.telefono_s == 0.0


def test_cuenta_el_tiempo_vacio():
    c = ContadorActividad([], ConfigActividad(windowSeconds=10.0))
    muestras = correr(c, [([], []) for _ in range(51)])
    m = muestras[0]
    assert 9.5 <= m.vacio_s <= 10.5, f"debía contar ~10 s vacíos, contó {m.vacio_s}"
    assert m.ocupado_s == 0.0


def test_el_telefono_solo_cuenta_si_esta_sobre_el_cuerpo():
    """Un teléfono sobre el escritorio, lejos del cuerpo, no es uso de teléfono."""
    c = ContadorActividad([], ConfigActividad(windowSeconds=10.0))
    p = persona()
    lejos = Caja(0.85, 0.75, 0.04, 0.06, 0.8)   # otra punta de la imagen
    muestras = correr(c, [([p], [lejos]) for _ in range(51)])
    assert muestras[0].telefono_s == 0.0, "un teléfono lejos del cuerpo no es uso"

    c2 = ContadorActividad([], ConfigActividad(windowSeconds=10.0))
    muestras2 = correr(c2, [([p], [telefono_en(p)]) for _ in range(51)])
    assert muestras2[0].telefono_s > 9.0, "el teléfono sobre el cuerpo sí cuenta"


def test_el_telefono_no_se_le_atribuye_a_otra_persona():
    """Dos puestos, teléfono en uno: el otro no debe acumular tiempo de teléfono."""
    izq = Zona("a", "Puesto A", [(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)])
    der = Zona("b", "Puesto B", [(0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 1.0)])
    c = ContadorActividad([izq, der], ConfigActividad(windowSeconds=10.0))

    p_izq = persona(x=0.15, y=0.3, w=0.15, h=0.5)
    p_der = persona(x=0.65, y=0.3, w=0.15, h=0.5)
    muestras = correr(c, [([p_izq, p_der], [telefono_en(p_der)]) for _ in range(51)])

    por_zona = {m.zona_nombre: m for m in muestras}
    assert por_zona["Puesto A"].ocupado_s > 9.0
    assert por_zona["Puesto B"].ocupado_s > 9.0
    assert por_zona["Puesto A"].telefono_s == 0.0, (
        "el teléfono del puesto B se le atribuyó al puesto A"
    )
    assert por_zona["Puesto B"].telefono_s > 9.0


def test_un_corte_de_camara_no_se_rellena():
    """Los segundos que no se observaron NO se le atribuyen a ningún estado.

    Es la propiedad que hace creíble al informe. Rellenar un hueco con el último
    estado conocido es inventar datos: si la cámara estuvo caída dos horas, el
    informe no puede decir que el puesto estuvo ocupado dos horas.
    """
    cfg = ConfigActividad(windowSeconds=1000.0, maxGapSeconds=5.0)
    c = ContadorActividad([], cfg)

    c.observar(Observacion(ts=1000.0, personas=[persona()]))
    c.observar(Observacion(ts=1001.0, personas=[persona()]))
    # La cámara se cae 10 minutos.
    c.observar(Observacion(ts=1601.0, personas=[persona()]))
    c.observar(Observacion(ts=1602.0, personas=[persona()]))

    muestras = c.cerrar_pendiente(1603.0)
    m = muestras[0]
    assert m.sin_cobertura_s >= 590, f"el corte debía quedar sin cobertura, dio {m.sin_cobertura_s}"
    assert m.ocupado_s < 10, f"no puede atribuirse el corte a tiempo ocupado, dio {m.ocupado_s}"


def test_el_tiempo_observado_cierra():
    """Ocupado + vacío = el tiempo realmente observado. Sin fugas ni invenciones."""
    c = ContadorActividad([], ConfigActividad(windowSeconds=20.0))
    pasos = []
    for i in range(101):
        # Alterna presencia cada 20 frames.
        hay = (i // 20) % 2 == 0
        pasos.append(([persona()] if hay else [], []))
    muestras = correr(c, pasos)
    assert muestras
    m = muestras[0]
    total = m.ocupado_s + m.vacio_s + m.sin_cobertura_s
    ventana = m.hasta - m.desde
    assert abs(total - ventana) < 0.05, (
        f"la suma de estados ({total:.2f}) no coincide con la ventana ({ventana:.2f})"
    )


def test_el_telefono_nunca_supera_al_tiempo_ocupado():
    """Invariante del informe: no se puede usar el teléfono sin estar presente."""
    c = ContadorActividad([], ConfigActividad(windowSeconds=10.0))
    p = persona()
    pasos = []
    for i in range(51):
        hay = i % 3 != 0
        pasos.append(([p] if hay else [], [telefono_en(p)]))  # teléfono siempre visible
    muestras = correr(c, pasos)
    m = muestras[0]
    assert m.telefono_s <= m.ocupado_s + 1e-6, (
        f"teléfono ({m.telefono_s}) superó al tiempo ocupado ({m.ocupado_s})"
    )


def test_las_zonas_separan_por_donde_estan_los_pies():
    """Alguien parado justo afuera del borde no ocupa el puesto.

    Se usa el punto de apoyo y no el centro del cuerpo: las zonas son regiones
    del suelo, y quien está parado afuera está afuera aunque su torso se
    superponga con el polígono.
    """
    puesto = Zona("a", "Puesto A", [(0.0, 0.6), (0.5, 0.6), (0.5, 1.0), (0.0, 1.0)])
    c = ContadorActividad([puesto], ConfigActividad(windowSeconds=5.0))

    # Cuerpo que se superpone con la zona, pero con los pies fuera (a la derecha).
    afuera = Caja(0.45, 0.30, 0.20, 0.45, 0.9)   # pies en x=0.55 -> fuera
    muestras = correr(c, [([afuera], []) for _ in range(30)])
    assert muestras[0].ocupado_s == 0.0, "los pies estaban fuera del puesto"

    c2 = ContadorActividad([puesto], ConfigActividad(windowSeconds=5.0))
    adentro = Caja(0.20, 0.30, 0.20, 0.45, 0.9)  # pies en x=0.30 -> dentro
    muestras2 = correr(c2, [([adentro], []) for _ in range(30)])
    assert muestras2[0].ocupado_s > 4.0


def test_detecciones_flojas_no_cuentan():
    c = ContadorActividad([], ConfigActividad(windowSeconds=5.0, personConfidence=0.45))
    floja = persona(conf=0.20)
    muestras = correr(c, [([floja], []) for _ in range(30)])
    assert muestras[0].ocupado_s == 0.0, "una detección por debajo del umbral no ocupa el puesto"
    assert muestras[0].vacio_s > 4.0


def test_el_reloj_hacia_atras_no_produce_tiempo_negativo():
    """Al reconectar, la cámara puede mandar un timestamp anterior."""
    c = ContadorActividad([], ConfigActividad(windowSeconds=1000.0))
    for i in range(10):
        c.observar(Observacion(ts=1000.0 + i * DT, personas=[persona()]))
    c.observar(Observacion(ts=500.0, personas=[persona()]))
    for i in range(10):
        c.observar(Observacion(ts=500.0 + i * DT, personas=[persona()]))

    muestras = c.cerrar_pendiente(510.0)
    for m in muestras:
        assert m.ocupado_s >= 0 and m.vacio_s >= 0 and m.sin_cobertura_s >= 0, (
            f"tiempo negativo en la muestra: {m}"
        )


def test_la_ocupacion_media_refleja_cuanta_gente_hubo():
    c = ContadorActividad([], ConfigActividad(windowSeconds=10.0))
    pasos = []
    for i in range(51):
        # La mitad del tiempo hay 1 persona, la otra mitad 3.
        n = 1 if i < 25 else 3
        pasos.append(([persona(x=0.1 + 0.2 * k) for k in range(n)], []))
    muestras = correr(c, pasos)
    media = muestras[0].ocupacion_media
    assert 1.8 <= media <= 2.2, f"la ocupación media debería rondar 2, dio {media}"
    assert muestras[0].max_personas == 3


def test_cerrar_pendiente_no_pierde_el_ultimo_tramo():
    """Al soltar la cámara, lo observado desde la última ventana tiene que salir."""
    c = ContadorActividad([], ConfigActividad(windowSeconds=3600.0))
    for i in range(25):
        c.observar(Observacion(ts=1000.0 + i * DT, personas=[persona()]))
    muestras = c.cerrar_pendiente(1000.0 + 25 * DT)
    assert muestras, "el tramo observado se perdió al cerrar"
    assert muestras[0].ocupado_s > 4.0


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
