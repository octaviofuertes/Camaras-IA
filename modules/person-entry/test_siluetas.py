"""Pruebas del recorte de contornos.

Lo que se protege es chico pero se rompe fácil: que el contorno que se manda a
la pantalla sea el de la persona correcta, y que simplificarlo no lo deforme ni
lo deje sin puntos.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from siluetas import Silueta, siluetas_de, simplificar  # noqa: E402


class MascarasFalsas:
    def __init__(self, contornos):
        self.xyn = contornos


class ResultadoFalso:
    def __init__(self, contornos=None):
        self.masks = MascarasFalsas(contornos) if contornos is not None else None


def contorno(n: int, desplazamiento: float = 0.0):
    """Un contorno cualquiera de n puntos, reconocible por su desplazamiento."""
    return [(i / n + desplazamiento, i / n) for i in range(n)]


# ── simplificar ──────────────────────────────────────────────────────

def test_un_contorno_chico_no_se_toca():
    p = contorno(10)
    assert len(simplificar(p, 48)) == 10


def test_uno_grande_se_recorta_al_maximo():
    assert len(simplificar(contorno(400), 48)) == 48


def test_el_recorte_respeta_el_orden():
    salida = simplificar(contorno(400), 48)
    xs = [x for x, _ in salida]
    assert xs == sorted(xs), "el contorno quedó desordenado y el polígono se cruzaría"


def test_arranca_donde_arrancaba():
    # Si el primer punto cambiara entre frames, el polígono giraría solo.
    assert simplificar(contorno(400), 48)[0] == (0.0, 0.0)


def test_un_contorno_vacio_no_explota():
    assert simplificar([], 48) == ()


def test_maximo_de_uno():
    assert len(simplificar(contorno(400), 1)) == 1


# ── emparejar con los tracks ─────────────────────────────────────────

def test_cada_track_se_queda_con_su_contorno():
    r = ResultadoFalso([contorno(60, 0.0), contorno(60, 0.5)])
    s = siluetas_de(r, [7, 9])
    assert set(s) == {7, 9}
    # El segundo track tiene que quedarse con el contorno desplazado: si se
    # cruzaran, cada persona quedaría pintada con la silueta de la otra.
    assert s[9].puntos[0][0] == 0.5
    assert s[7].puntos[0][0] == 0.0


def test_sin_mascaras_no_hay_siluetas():
    # Es el caso normal con el modelo de detección: se dibuja la caja de siempre.
    assert siluetas_de(ResultadoFalso(None), [1, 2]) == {}


def test_un_track_sin_id_se_descarta():
    r = ResultadoFalso([contorno(60), contorno(60)])
    s = siluetas_de(r, [-1, 4])
    assert set(s) == {4}


def test_mas_tracks_que_contornos_no_explota():
    r = ResultadoFalso([contorno(60)])
    s = siluetas_de(r, [1, 2, 3])
    assert set(s) == {1}


def test_un_contorno_de_dos_puntos_se_descarta():
    # Con menos de tres no hay polígono que dibujar; pintarlo sería una línea
    # verde atravesando la imagen.
    r = ResultadoFalso([[(0.1, 0.1), (0.2, 0.2)]])
    assert siluetas_de(r, [1]) == {}


def test_se_redondea_al_serializar():
    s = Silueta(track_id=1, puntos=((0.123456, 0.987654),))
    assert s.como_lista() == [[0.1235, 0.9877]]
