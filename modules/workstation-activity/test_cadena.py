"""La cadena completa: se le ve la cara una vez y se le atribuye el teléfono.

Es lo que se le pide al sistema y lo que ningún test cubría de punta a punta:
cada módulo estaba probado por separado, pero el traspaso entre los dos —quién
es cada cuerpo, y de ahí a quién se le cuenta el teléfono— no.

Se simula lo que producen los detectores para no depender de los modelos: los
recuadros y las poses son los datos, no las imágenes.
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "modules" / "person-identification"))
sys.path.insert(0, str(RAIZ / "modules" / "workstation-activity"))
sys.path.insert(0, str(RAIZ / "packages" / "py-contracts"))

from continuidad import IdentidadSostenida, firma_apariencia  # noqa: E402
from percepta_contracts import Detection  # noqa: E402

from actividad import Caja, ConfigActividad, ContadorActividad, Observacion  # noqa: E402
from module import WorkstationActivityModule, _superposicion  # noqa: E402

DT = 0.5


def ropa(semilla: int) -> list[float]:
    r = random.Random(semilla)
    return firma_apariencia([abs(r.gauss(0, 1)) for _ in range(64)])


def identidad(persona_id: str, nombre: str, caja: tuple, via: str = "puesto") -> Detection:
    """Lo que emite el módulo de identificación para un cuerpo reconocido."""
    return Detection(
        class_label="person.identified", class_id=0, confidence=0.62, bbox=caja,
        attributes={"kind": "identity", "personId": persona_id, "personName": nombre, "via": via},
    )


# ═══════════════════════════════════════════════════════════════════

def test_de_espaldas_el_telefono_se_le_cuenta_a_quien_corresponde():
    """EL CASO PEDIDO.

    A Juan se le ve la cara UNA vez al sentarse. Después se da vuelta y no se le
    ve más: lo sostiene su puesto. Mientras tanto usa el teléfono, y Ana —al
    lado, también de espaldas— no. El informe no puede dejarlos igual.
    """
    sostenida = IdentidadSostenida()
    juan_caja = (0.10, 0.30, 0.18, 0.55)
    ana_caja = (0.60, 0.30, 0.18, 0.55)
    centro = lambda c: (c[0] + c[2] / 2, c[1] + c[3])  # noqa: E731

    # Un solo instante con las caras visibles.
    sostenida.anclar_por_rostro(1, "p-juan", "Juan", ropa(1), centro(juan_caja), ahora=0.0)
    sostenida.anclar_por_rostro(2, "p-ana", "Ana", ropa(2), centro(ana_caja), ahora=0.0)

    contador = ContadorActividad([], ConfigActividad(windowSeconds=60.0, maxGapSeconds=5.0))

    t = 0.0
    for i in range(1, 121):          # 60 s a 2 fps
        t = i * DT
        # De espaldas: sin cara, con el tracker perdido (track nuevo cada vez) y
        # la ropa irreconocible. Sólo el puesto puede sostener la identidad.
        ids = []
        for tid, (pid, nombre, caja) in enumerate(
            [("p-juan", "Juan", juan_caja), ("p-ana", "Ana", ana_caja)], start=100
        ):
            r = sostenida.resolver(tid * 1000 + i, ropa(777), centro(caja), ahora=t)
            assert r.persona_id == pid, f"perdió a {nombre} en t={t:.0f}s (vía {r.via})"
            ids.append(identidad(r.persona_id, r.nombre, caja, r.via))

        personas = [Caja(*juan_caja, 0.9), Caja(*ana_caja, 0.9)]
        # El teléfono, a la altura del pecho de Juan.
        tel = Caja(juan_caja[0] + 0.06, juan_caja[1] + 0.14, 0.04, 0.06, 0.7)
        contador.observar(
            Observacion(ts=t, personas=personas, telefonos=[tel],
                        identidades=[_emparejar(p, ids) for p in personas])
        )

    contador.cerrar_pendiente(t + DT)
    por_persona = {m.persona_id: m for m in contador.ultimas_personas}

    assert "p-juan" in por_persona and "p-ana" in por_persona, por_persona.keys()
    juan, ana = por_persona["p-juan"], por_persona["p-ana"]

    assert juan.presente_s > 55, f"Juan presente {juan.presente_s:.1f}s de 60"
    assert ana.presente_s > 55, f"Ana presente {ana.presente_s:.1f}s de 60"
    assert juan.telefono_s > 55, f"a Juan se le contaron {juan.telefono_s:.1f}s de teléfono"
    assert ana.telefono_s == 0, (
        f"a Ana se le atribuyeron {ana.telefono_s:.1f}s de teléfono que no usó"
    )


def _emparejar(persona: Caja, ids: list[Detection]) -> tuple[str, str] | None:
    """Misma regla que usa el módulo: superposición de recuadros."""
    mejor, mejor_iou = None, 0.0
    for d in ids:
        iou = _superposicion((persona.x, persona.y, persona.w, persona.h), d.bbox)
        if iou > mejor_iou:
            mejor, mejor_iou = d, iou
    if mejor is None or mejor_iou < 0.45:
        return None
    return (mejor.attributes["personId"], mejor.attributes.get("personName", ""))


def test_si_se_pierde_la_identidad_el_tiempo_no_se_le_regala_a_nadie():
    """Sin identidad, el tiempo va a la bolsa de 'sin identificar'.

    Es la alternativa correcta a adivinar: repartirlo entre los presentes le
    sumaría a alguien minutos que pueden no haber sido suyos.
    """
    contador = ContadorActividad([], ConfigActividad(windowSeconds=20.0, maxGapSeconds=5.0))
    caja = (0.10, 0.30, 0.18, 0.55)
    t = 0.0
    for i in range(1, 41):
        t = i * DT
        contador.observar(
            Observacion(ts=t, personas=[Caja(*caja, 0.9)], telefonos=[], identidades=[None])
        )
    contador.cerrar_pendiente(t + DT)

    ids = [m.persona_id for m in contador.ultimas_personas]
    assert ids == [None], f"se le atribuyó a alguien: {ids}"


def test_el_traspaso_no_le_da_la_identidad_al_de_al_lado():
    """Dos cuerpos juntos: cada identidad tiene que ir a su propio cuerpo."""
    juan_caja = (0.10, 0.30, 0.18, 0.55)
    ana_caja = (0.26, 0.30, 0.18, 0.55)     # pegada a la de Juan
    ids = [identidad("p-juan", "Juan", juan_caja), identidad("p-ana", "Ana", ana_caja)]

    assert _emparejar(Caja(*juan_caja, 0.9), ids)[0] == "p-juan"
    assert _emparejar(Caja(*ana_caja, 0.9), ids)[0] == "p-ana"


def test_un_cuerpo_sin_identidad_cercana_queda_sin_identificar():
    """Superposición baja: mejor sin nombre que con el nombre equivocado."""
    ids = [identidad("p-juan", "Juan", (0.10, 0.30, 0.18, 0.55))]
    assert _emparejar(Caja(0.60, 0.30, 0.18, 0.55, 0.9), ids) is None


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
