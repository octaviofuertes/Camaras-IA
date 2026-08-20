"""Sincroniza las cámaras que se capturan con las que hay en la base.

La fuente de verdad es `device-service` (tabla `cameras` + `streams`). Este
módulo consulta la API cada pocos segundos y arranca o detiene hilos de captura
según corresponda: dar de alta una cámara en el dashboard la pone a capturar
sin reiniciar nada.

Si la API no está disponible cae a `cameras.json`, para poder trabajar con el
pipeline sin levantar todo el stack.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

import requests

from .source import CameraSource

log = logging.getLogger("registry")

DEVICE_URL = os.environ.get("DEVICE_SERVICE_URL", "http://127.0.0.1:3003")
SERVICE_TOKEN = os.environ.get("SERVICE_TOKEN", "")
SOURCES_FILE = Path(os.environ.get("CAMERA_SOURCES", "./cameras.json"))
SYNC_SECONDS = float(os.environ.get("CAMERA_SYNC_SECONDS", "10"))


def _parse_source(raw: str | int) -> str | int:
    """De cómo se guarda una cámara a lo que entiende OpenCV.

    `0` y `'0'` son el índice de una webcam USB; `'rtsp://…'` es una cámara IP.

    `usb://0` también es el índice 0: así se guarda en la base para
    distinguir un índice de una URL, y device-service le saca el esquema antes
    de exponerlo. Se acepta igual acá porque si alguna vez llega crudo —un
    archivo de respaldo escrito a mano, una consulta directa— quedaba como el
    texto "usb://0", OpenCV intentaba abrirlo como si fuera una dirección de
    red y la cámara no arrancaba nunca. Fallaba en silencio: el hilo de captura
    se quedaba sin abrir el dispositivo y la pantalla decía "Sin señal", que es
    lo mismo que dice una cámara desenchufada.
    """
    if isinstance(raw, int):
        return raw
    s = str(raw).strip()
    for prefijo in ("usb://", "usb:", "cam://"):
        if s.lower().startswith(prefijo):
            resto = s[len(prefijo):]
            if resto.isdigit():
                return int(resto)
    return int(s) if s.isdigit() else s


def decidir_camaras(
    de_la_api: list[dict] | None,
    ya_contesto_alguna_vez: bool,
    del_archivo: list[dict],
) -> list[dict] | None:
    """Con qué lista de cámaras quedarse.

    `None` significa "no cambiar nada", y es la respuesta importante: si la API
    ya contestó alguna vez, que ahora no conteste es un hueco de red, no un
    cambio de configuración.

    Sin esto pasaba lo siguiente, y es feo. device-service se caía un segundo;
    media-service se pasaba al archivo de respaldo, cuyas cámaras tienen OTROS
    identificadores; la cámara real dejaba de estar en la lista deseada y se la
    daba de baja. A partir de ahí cada pedido de imagen devolvía 404 y el panel
    mostraba "Sin señal" para siempre, aunque device-service volviera: la
    cámara del respaldo ya se había quedado con el mismo dispositivo USB.

    El archivo sólo sirve para arrancar sin API. Una vez que la API habló, ella
    manda, y su ausencia no borra nada.
    """
    if de_la_api is not None:
        return de_la_api
    if ya_contesto_alguna_vez:
        return None
    return del_archivo


class CameraRegistry:
    def __init__(self) -> None:
        self.sources: dict[str, CameraSource] = {}
        self.names: dict[str, str] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_sync_error: str | None = None
        self.using_api = False
        # Si la API contestó alguna vez, su silencio posterior no borra cámaras.
        self._api_respondio = False

    # ── origen de la configuración ───────────────────────────────────
    def _from_api(self) -> list[dict] | None:
        if not SERVICE_TOKEN:
            return None
        try:
            r = requests.get(
                f"{DEVICE_URL}/api/v1/cameras",
                headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
                timeout=5,
            )
            if r.status_code != 200:
                self.last_sync_error = f"device-service {r.status_code}"
                return None
            self.last_sync_error = None
            out = []
            for c in r.json().get("items", []):
                if not c.get("source"):
                    continue  # cámara sin stream configurado
                out.append(
                    {
                        "id": c["id"],
                        "name": c.get("name", ""),
                        "source": c["source"],
                        "width": c.get("width") or 1280,
                        "height": c.get("height") or 720,
                        "fps": c.get("fps") or 10,
                        "enabled": c.get("status") != "disabled",
                    }
                )
            return out
        except requests.RequestException as exc:
            self.last_sync_error = str(exc)
            return None

    def _from_file(self) -> list[dict]:
        if not SOURCES_FILE.is_file():
            return []
        cfg = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
        return [c for c in cfg if c.get("enabled", True) and "id" in c]

    def _desired(self) -> list[dict] | None:
        api = self._from_api()
        if api is not None:
            self.using_api = True
            self._api_respondio = True
            return api
        self.using_api = False
        return decidir_camaras(api, self._api_respondio, self._from_file())

    # ── sincronización ───────────────────────────────────────────────
    def sync_once(self) -> None:
        elegidas = self._desired()
        if elegidas is None:
            # No se sabe qué hay: se deja todo como está. Apagar cámaras porque
            # no se pudo preguntar es peor que seguir mostrando las de recién.
            log.warning(
                "no se pudo consultar device-service (%s): se conservan las %d cámara(s) actuales",
                self.last_sync_error, len(self.sources),
            )
            return
        desired = {c["id"]: c for c in elegidas if c.get("enabled", True)}

        with self._lock:
            # Cámaras nuevas: arrancar captura.
            delay = 0.0
            for cam_id, c in desired.items():
                if cam_id in self.sources:
                    continue
                src = CameraSource(
                    camera_id=cam_id,
                    source=_parse_source(c["source"]),
                    width=int(c.get("width", 1280)),
                    height=int(c.get("height", 720)),
                    target_fps=float(c.get("fps", 10)),
                )
                self.sources[cam_id] = src
                self.names[cam_id] = c.get("name", "")
                src.start(delay=delay)
                log.info("cámara agregada: %s (%s) -> %r", c.get("name"), cam_id[-6:], c["source"])
                # Escalonar aperturas USB: abrir dos a la vez cuelga el driver.
                delay += 4.0 if isinstance(src.source, int) else 0.5

            # Cámaras eliminadas o deshabilitadas: detener captura.
            for cam_id in list(self.sources):
                if cam_id not in desired:
                    log.info("cámara quitada: %s", cam_id[-6:])
                    self.sources.pop(cam_id).stop()
                    self.names.pop(cam_id, None)
                else:
                    self.names[cam_id] = desired[cam_id].get("name", "")

    def start(self) -> None:
        self.sync_once()
        self._thread = threading.Thread(target=self._loop, name="cam-sync", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(SYNC_SECONDS)
            if self._stop.is_set():
                break
            try:
                self.sync_once()
            except Exception:
                log.exception("fallo al sincronizar cámaras")

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for s in self.sources.values():
                s.stop()

    def get(self, camera_id: str) -> CameraSource | None:
        return self.sources.get(camera_id)

    def stats(self) -> list[dict]:
        with self._lock:
            return [{**s.stats(), "name": self.names.get(cid, "")} for cid, s in self.sources.items()]
