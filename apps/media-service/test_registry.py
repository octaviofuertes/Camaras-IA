"""Pruebas del registro de cámaras de media-service.

Las dos reglas que se protegen acá son las que dejaron la cámara sin funcionar:

  1. De dónde sale el índice de una webcam. Un origen mal interpretado no da
     error: el hilo de captura no abre nada y la pantalla dice "Sin señal", que
     es exactamente lo que diría una cámara desenchufada.

  2. Qué hacer cuando device-service no contesta. Antes se pasaba a un archivo
     de respaldo con OTROS identificadores, así que la cámara real se daba de
     baja y no volvía nunca — el respaldo ya se había quedado con el mismo
     dispositivo USB.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from media_service.registry import _parse_source, decidir_camaras  # noqa: E402


# ── de dónde sale el índice de la webcam ─────────────────────────────

def test_un_indice_suelto_es_un_indice():
    assert _parse_source("0") == 0
    assert _parse_source("2") == 2


def test_un_entero_pasa_tal_cual():
    assert _parse_source(3) == 3


def test_el_esquema_usb_tambien_es_un_indice():
    # Es como se guarda en la base para distinguirlo de una URL.
    assert _parse_source("usb://0") == 0
    assert _parse_source("usb://1") == 1


def test_el_esquema_no_distingue_mayusculas():
    assert _parse_source("USB://2") == 2


def test_una_url_de_camara_ip_queda_como_texto():
    assert _parse_source("rtsp://camara/stream") == "rtsp://camara/stream"


def test_un_dispositivo_de_linux_queda_como_texto():
    # OpenCV lo abre por ruta; convertirlo a número sería abrir otra cámara.
    assert _parse_source("/dev/video0") == "/dev/video0"


def test_los_espacios_no_estorban():
    assert _parse_source("  1  ") == 1


def test_usb_sin_numero_no_se_inventa_un_indice():
    # "usb://" a secas no es la cámara 0: es una configuración incompleta, y
    # abrir la primera webcam que haya sería mostrar imagen de otro lado.
    assert _parse_source("usb://") == "usb://"


# ── con qué lista de cámaras quedarse ────────────────────────────────

DE_LA_API = [{"id": "camara-real"}]
DEL_ARCHIVO = [{"id": "camara-de-respaldo"}]


def test_si_la_api_contesta_manda_la_api():
    assert decidir_camaras(DE_LA_API, False, DEL_ARCHIVO) == DE_LA_API


def test_en_arranque_frio_sirve_el_archivo():
    # Sin API todavía, el archivo es lo único que hay para levantar algo.
    assert decidir_camaras(None, False, DEL_ARCHIVO) == DEL_ARCHIVO


def test_si_la_api_ya_hablo_su_silencio_no_cambia_nada():
    # ESTA es la prueba que importa: None significa "no tocar". Devolver el
    # archivo acá es lo que daba de baja la cámara real.
    assert decidir_camaras(None, True, DEL_ARCHIVO) is None


def test_la_api_manda_aunque_venga_vacia():
    # Una lista vacía es una respuesta: el cliente borró sus cámaras. No es lo
    # mismo que no poder preguntar.
    assert decidir_camaras([], True, DEL_ARCHIVO) == []


def test_sin_archivo_y_sin_api_no_hay_camaras():
    assert decidir_camaras(None, False, []) == []
