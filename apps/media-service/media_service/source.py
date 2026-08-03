"""Captura de video real desde una fuente: webcam USB o cámara IP (RTSP).

La MISMA ruta de código sirve para ambas: OpenCV abre un índice de dispositivo
(`0`) o una URL (`rtsp://user:pass@ip:554/stream`). Cambiar de la Logitech de
pruebas a cámaras WiFi/IP es cambiar la cadena de conexión, no el código.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque

import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BufferedFrame:
    """Frame en el ring buffer: JPEG comprimido + su instante de captura."""
    ts: float
    seq: int
    jpeg: bytes


class CameraSource:
    """Hilo de captura con reconexión y ring buffer para clips pre/post evento.

    El ring buffer es lo que permite que un clip incluya los 10 s ANTERIORES al
    evento: cuando la alerta se dispara, esos frames ya están en memoria.
    """

    def __init__(
        self,
        camera_id: str,
        source: str | int,
        *,
        width: int = 1280,
        height: int = 720,
        target_fps: float = 12.0,
        buffer_seconds: float = 25.0,
        jpeg_quality: int = 80,
    ) -> None:
        self.camera_id = camera_id
        self.source = source
        self.width = width
        self.height = height
        self.target_fps = target_fps
        self.jpeg_quality = jpeg_quality

        self._buf: Deque[BufferedFrame] = deque(maxlen=int(buffer_seconds * target_fps))
        self._lock = threading.Lock()
        self._latest_raw: np.ndarray | None = None
        self._latest_meta: tuple[float, int] = (0.0, 0)
        self._seq = 0

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self.connected = False
        self.last_error: str | None = None
        self.frames_captured = 0
        self.started_at = time.time()

    # ── ciclo de vida ────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"cap-{self.camera_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.connected = False

    # ── captura ──────────────────────────────────────────────────────
    def _open(self) -> cv2.VideoCapture | None:
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        # Buffer mínimo: preferimos el frame más reciente antes que uno viejo
        # en cola (para análisis en vivo, la latencia importa más que no perder
        # frames).
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:  # no todos los backends lo soportan
            pass
        return cap

    def _run(self) -> None:
        interval = 1.0 / self.target_fps
        backoff = 1.0
        cap: cv2.VideoCapture | None = None

        while not self._stop.is_set():
            if cap is None:
                cap = self._open()
                if cap is None:
                    self.connected = False
                    self.last_error = f"no se pudo abrir la fuente {self.source!r}"
                    log.warning("[%s] %s; reintento en %.0fs", self.camera_id, self.last_error, backoff)
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2, 30.0)  # backoff exponencial acotado
                    continue
                self.connected = True
                self.last_error = None
                backoff = 1.0
                log.info("[%s] fuente abierta: %r", self.camera_id, self.source)

            t0 = time.time()
            ok, frame = cap.read()
            if not ok or frame is None:
                # Cámara desconectada o stream cortado: cerrar y reintentar.
                log.warning("[%s] lectura fallida, reconectando", self.camera_id)
                cap.release()
                cap = None
                self.connected = False
                self.last_error = "lectura fallida"
                continue

            self._seq += 1
            self.frames_captured += 1
            ok_enc, buf = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
            )
            with self._lock:
                self._latest_raw = frame
                self._latest_meta = (t0, self._seq)
                if ok_enc:
                    self._buf.append(BufferedFrame(ts=t0, seq=self._seq, jpeg=buf.tobytes()))

            # Ritmo estable: dormir lo que sobra del intervalo objetivo.
            elapsed = time.time() - t0
            if elapsed < interval:
                self._stop.wait(interval - elapsed)

        if cap is not None:
            cap.release()
        log.info("[%s] captura detenida", self.camera_id)

    # ── lectura ──────────────────────────────────────────────────────
    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._buf[-1].jpeg if self._buf else None

    def latest_frame(self) -> tuple[np.ndarray, float, int] | None:
        """Último frame sin comprimir, para inferencia."""
        with self._lock:
            if self._latest_raw is None:
                return None
            ts, seq = self._latest_meta
            return self._latest_raw.copy(), ts, seq

    def window(self, center_ts: float, pre: float, post: float) -> list[BufferedFrame]:
        """Frames del buffer en [center-pre, center+post] — el clip del evento."""
        with self._lock:
            return [f for f in self._buf if center_ts - pre <= f.ts <= center_ts + post]

    def buffer_span(self) -> float:
        with self._lock:
            if len(self._buf) < 2:
                return 0.0
            return self._buf[-1].ts - self._buf[0].ts

    def stats(self) -> dict:
        uptime = max(time.time() - self.started_at, 1e-6)
        return {
            "cameraId": self.camera_id,
            "source": str(self.source),
            "connected": self.connected,
            "lastError": self.last_error,
            "framesCaptured": self.frames_captured,
            "fps": round(self.frames_captured / uptime, 2),
            "bufferedFrames": len(self._buf),
            "bufferSeconds": round(self.buffer_span(), 1),
        }
