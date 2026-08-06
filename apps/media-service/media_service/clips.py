"""Ensamblado de evidencias: imagen anotada y clip pre/post evento.

El clip cubre unos segundos antes del evento, el momento, y otros tantos
después. Los previos salen del ring buffer (ya estaban en memoria cuando la
alerta se disparó); los posteriores se esperan y luego se corta la ventana.

Por qué 3 y no 10: el clip existe para que una persona decida si hubo una caída,
y esa decisión se toma mirando el segundo del impacto. Veinte segundos obligan a
buscar el momento dentro del video, pesan cuatro veces más en disco, y demoran
la alerta veinte segundos —porque hay que esperar el post-roll antes de poder
guardar nada—. Menos video, revisado antes, es mejor evidencia.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import cv2
import numpy as np

from .source import CameraSource

log = logging.getLogger(__name__)

# Datos del último clip armado por ruta, para poder informar su duración REAL
# en vez de la que se supone por los segundos pedidos.
_ULTIMO_CLIP: dict[str, dict] = {}


def info_clip(ruta) -> dict:
    return _ULTIMO_CLIP.get(str(ruta), {})

PRE_ROLL_S = float(os.environ.get("CLIP_PRE_ROLL_S", "3"))
POST_ROLL_S = float(os.environ.get("CLIP_POST_ROLL_S", "3"))


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


def _abrir_escritor(dest, fps: float, w: int, h: int):
    """Abre el escritor de video prefiriendo H.264.

    El clip existe para que una persona lo MIRE en el navegador y decida si
    hubo una caída. Se escribía con `mp4v` (MPEG-4 Parte 2), que ningún
    navegador reproduce: el archivo se generaba bien, se descargaba bien, y el
    reproductor tiraba "formato no soportado". Una evidencia que no se puede
    ver no es evidencia.

    H.264 es lo que entienden Chrome, Firefox y Safari. Si este equipo no tiene
    el encoder se cae a `mp4v` antes que quedarse sin clip, pero lo dice fuerte:
    el video va a existir y no se va a poder mirar desde la web.
    """
    for fourcc in ("avc1", "H264"):
        writer = cv2.VideoWriter(str(dest), cv2.VideoWriter_fourcc(*fourcc), fps, (w, h))
        if writer.isOpened():
            return writer
        writer.release()

    log.warning(
        "sin encoder H.264 disponible: el clip se escribe en mp4v y NO se va a "
        "poder reproducir en el navegador (sí descargar). Instalá ffmpeg con "
        "soporte libx264 o la librería openh264 para resolverlo."
    )
    writer = cv2.VideoWriter(str(dest), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if writer.isOpened():
        return writer
    log.error("no se pudo abrir ningún escritor de video en %s", dest)
    return None


def _fps_real(frames) -> float:
    """Ritmo con el que se capturaron ESTOS frames, según sus tiempos.

    El escritor tenía el fps fijo en 12 mientras la cámara entregaba ~19: el
    clip quedaba en cámara lenta y su duración no coincidía con la ventana
    pedida. Medido: 6 segundos de captura reproduciéndose en 9,7. Además del
    efecto raro al mirarlo, hacía que la duración informada fuera falsa.
    """
    if len(frames) < 2:
        return 12.0
    span = frames[-1].ts - frames[0].ts
    if span <= 1e-3:
        return 12.0
    fps = (len(frames) - 1) / span
    # Acotado a lo que puede entregar una cámara real: un tiempo corrupto no
    # debe producir un clip de 200 fps ni de 0.5.
    return max(1.0, min(fps, 60.0))


def build_clip(
    src: CameraSource,
    center_ts: float,
    dest: Path,
    *,
    pre: float = PRE_ROLL_S,
    post: float = POST_ROLL_S,
    fps: float | None = None,
    wait_for_post: bool = True,
) -> Path | None:
    """Arma el clip del evento desde el ring buffer.

    Si `wait_for_post`, bloquea hasta que hayan transcurrido los segundos
    posteriores al evento (debe llamarse en un hilo aparte, no en el request).

    `fps` se deduce de los propios frames salvo que se lo fuerce.
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

    fps_clip = fps if fps else _fps_real(frames)

    dest.parent.mkdir(parents=True, exist_ok=True)
    writer = _abrir_escritor(dest, fps_clip, w, h)
    if writer is None:
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

    # Se deja constancia del ritmo real: si un clip sale raro, este número dice
    # si fue la cámara la que entregó a otro ritmo.
    log.info(
        "[%s] clip con %d frames a %.1f fps (%.1fs) -> %s",
        src.camera_id, written, fps_clip, written / fps_clip, dest,
    )
    _ULTIMO_CLIP[str(dest)] = {"frames": written, "fps": fps_clip, "segundos": written / fps_clip}
    return dest if written else None
