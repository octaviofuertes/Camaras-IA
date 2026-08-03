"""Ensamblado de evidencias: imagen anotada y clip pre/post evento.

CONTRACTS: el clip cubre 10 s antes del evento, el momento, y 10 s después.
Los 10 s previos salen del ring buffer (ya estaban en memoria cuando la alerta
se disparó); los posteriores se esperan y luego se corta la ventana.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2
import numpy as np

from .source import CameraSource

log = logging.getLogger(__name__)

PRE_ROLL_S = 10.0
POST_ROLL_S = 10.0


def save_snapshot(jpeg: bytes, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(jpeg)
    return dest


def annotate(jpeg: bytes, boxes: list[dict], label_prefix: str = "") -> bytes:
    """Dibuja las cajas de la detección sobre el frame de evidencia."""
    arr = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return jpeg
    h, w = arr.shape[:2]
    for b in boxes:
        x, y, bw, bh = b.get("bbox", (0, 0, 0, 0))
        p1 = (int(x * w), int(y * h))
        p2 = (int((x + bw) * w), int((y + bh) * h))
        cv2.rectangle(arr, p1, p2, (60, 180, 255), 2)
        text = f"{label_prefix}{b.get('classLabel', '')} {b.get('confidence', 0):.2f}"
        cv2.putText(arr, text, (p1[0], max(p1[1] - 7, 14)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60, 180, 255), 1, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", arr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    return buf.tobytes() if ok else jpeg


def build_clip(
    src: CameraSource,
    center_ts: float,
    dest: Path,
    *,
    pre: float = PRE_ROLL_S,
    post: float = POST_ROLL_S,
    fps: float = 12.0,
    wait_for_post: bool = True,
) -> Path | None:
    """Arma el clip del evento desde el ring buffer.

    Si `wait_for_post`, bloquea hasta que hayan transcurrido los segundos
    posteriores al evento (debe llamarse en un hilo aparte, no en el request).
    """
    if wait_for_post:
        remaining = (center_ts + post) - time.time()
        if remaining > 0:
            time.sleep(min(remaining, post + 2))

    frames = src.window(center_ts, pre, post)
    if not frames:
        log.warning("[%s] sin frames en el buffer para el clip", src.camera_id)
        return None

    first = cv2.imdecode(np.frombuffer(frames[0].jpeg, np.uint8), cv2.IMREAD_COLOR)
    if first is None:
        return None
    h, w = first.shape[:2]

    dest.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(dest), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not writer.isOpened():
        log.error("no se pudo abrir el escritor de video en %s", dest)
        return None

    written = 0
    for f in frames:
        arr = cv2.imdecode(np.frombuffer(f.jpeg, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            continue
        if arr.shape[:2] != (h, w):
            arr = cv2.resize(arr, (w, h))
        writer.write(arr)
        written += 1
    writer.release()

    log.info("[%s] clip con %d frames (%.1fs) -> %s", src.camera_id, written, written / fps, dest)
    return dest if written else None
