"""El teléfono se busca en un recorte: que vuelva al lugar correcto del cuadro.

Es la parte del cambio que puede fallar en silencio. Si las coordenadas del
recorte se traducen mal, el teléfono aparece en otra parte del cuadro: o no se
le cuenta a nadie, o —peor— se le cuenta a la persona de al lado.

Se usa un detector de mentira que devuelve una caja conocida, para probar la
traducción y no el modelo.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "modules" / "workstation-activity"))
sys.path.insert(0, str(RAIZ / "packages" / "py-contracts"))

from percepta_contracts import ModuleContext  # noqa: E402

import module as mod  # noqa: E402
from actividad import Caja  # noqa: E402

ANCHO, ALTO = 1280, 720


class CajaFalsa:
    def __init__(self, xyxy, conf):
        self.xyxy = [np.array(xyxy, dtype=float)]
        self.conf = np.array(conf)
        self.cls = np.array(67.0)


class ResultadoFalso:
    def __init__(self, cajas):
        self.boxes = cajas


class ModeloFalso:
    """Devuelve el teléfono siempre en el centro de lo que le pasen."""

    def __init__(self):
        self.recortes: list[tuple[int, int]] = []

    def predict(self, imagen, **kw):
        h, w = imagen.shape[:2]
        self.recortes.append((w, h))
        cx, cy = w / 2, h / 2
        return [ResultadoFalso([CajaFalsa([cx - 5, cy - 8, cx + 5, cy + 8], 0.8)])]


def modulo() -> "mod.WorkstationActivityModule":
    m = mod.WorkstationActivityModule()
    m.load(ModuleContext(ai_module_id="x", module_key="workstation-activity",
                         module_version="1.0.0", device="cpu", config={}, zones={}))
    m._model = ModeloFalso()
    return m


def imagen():
    return np.zeros((ALTO, ANCHO, 3), dtype=np.uint8)


# ═══════════════════════════════════════════════════════════════════

def test_el_telefono_vuelve_al_lugar_de_su_persona():
    """La caja detectada en el recorte tiene que caer sobre ESA persona."""
    m = modulo()
    persona = Caja(x=0.60, y=0.30, w=0.15, h=0.50, confianza=0.9)
    tels = m._buscar_telefonos(imagen(), [persona])

    assert len(tels) == 1
    t = tels[0]
    # El centro del teléfono cae dentro del recorte de esa persona, que está
    # centrado en ella.
    cx = t.x + t.w / 2
    assert persona.x - 0.10 < cx < persona.x + persona.w + 0.10, (
        f"el teléfono cayó en x={cx:.3f}, lejos de la persona en x={persona.x:.2f}"
    )
    assert 0.0 <= t.x <= 1.0 and 0.0 <= t.y <= 1.0, t


def test_dos_personas_dos_recortes_cada_uno_en_su_lugar():
    """El de la izquierda no puede terminar sobre el de la derecha."""
    m = modulo()
    izq = Caja(x=0.05, y=0.30, w=0.15, h=0.50, confianza=0.9)
    der = Caja(x=0.70, y=0.30, w=0.15, h=0.50, confianza=0.9)
    tels = m._buscar_telefonos(imagen(), [izq, der])

    assert len(tels) == 2, tels
    centros = sorted(t.x + t.w / 2 for t in tels)
    assert centros[0] < 0.35, f"el teléfono de la izquierda cayó en {centros[0]:.2f}"
    assert centros[1] > 0.60, f"el de la derecha cayó en {centros[1]:.2f}"


def test_el_recorte_es_el_torso_y_los_brazos():
    """Ni la cabeza sola ni el cuerpo entero: donde se sostiene un teléfono."""
    m = modulo()
    persona = Caja(x=0.40, y=0.20, w=0.20, h=0.60, confianza=0.9)
    m._buscar_telefonos(imagen(), [persona])

    w_rec, h_rec = m._model.recortes[0]
    # Ensanchado por los DOS lados: un teléfono se sostiene con cualquiera de
    # las dos manos, y ensanchar sólo hacia un lado deja media persona afuera.
    assert w_rec > persona.w * ANCHO * 1.5, (
        f"el recorte mide {w_rec}px para una persona de {persona.w*ANCHO:.0f}px: "
        "no entran los brazos"
    )
    assert h_rec < persona.h * ALTO, "el recorte llega hasta los pies: sobra imagen"
    assert h_rec > persona.h * ALTO * 0.5, "el recorte se quedó corto"


def test_una_persona_diminuta_no_genera_recorte():
    """Un recorte de pocos píxeles no aporta nada y cuesta igual."""
    m = modulo()
    assert m._buscar_telefonos(imagen(), [Caja(x=0.5, y=0.5, w=0.005, h=0.01, confianza=0.9)]) == []


def test_sin_personas_no_se_busca_nada():
    """Ahí está el ahorro: sin nadie en el cuadro no se gasta una sola inferencia."""
    m = modulo()
    assert m._buscar_telefonos(imagen(), []) == []
    assert m._model.recortes == [], "corrió el detector sin nadie a quien mirar"


def test_el_recorte_no_se_sale_del_cuadro():
    """Alguien pegado al borde: el recorte se recorta, no se rompe."""
    m = modulo()
    for persona in (
        Caja(x=0.00, y=0.00, w=0.12, h=0.40, confianza=0.9),
        Caja(x=0.88, y=0.62, w=0.12, h=0.38, confianza=0.9),
    ):
        tels = m._buscar_telefonos(imagen(), [persona])
        assert len(tels) == 1
        t = tels[0]
        assert 0.0 <= t.x and t.x + t.w <= 1.0, t
        assert 0.0 <= t.y and t.y + t.h <= 1.0, t


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
