"""media-service: captura real de video, vista en vivo y evidencias.

Expone:
  GET /health
  GET /cameras                          -> estado de todas las fuentes
  GET /cameras/{id}/stream.mjpg         -> vista en vivo (MJPEG)
  GET /cameras/{id}/snapshot.jpg        -> último frame
  GET /cameras/{id}/frame               -> último frame + metadatos (para ai-worker)
  POST /cameras/{id}/clip               -> arma el clip pre/post de un evento

MJPEG en lugar de WebRTC para el MVP: se consume desde un <img> sin
señalización ni STUN/TURN, y funciona igual con USB que con RTSP. WebRTC
(WHEP, CONTRACTS §11) queda para cuando importe la latencia y la escala.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse

from .clips import build_clip
from .registry import CameraRegistry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("media-service")

app = FastAPI(title="percepta-media-service")

EVIDENCE_DIR = Path(os.environ.get("EVIDENCE_DIR", "./.data/evidence"))

_registry = CameraRegistry()


@app.on_event("startup")
def _startup() -> None:
    _registry.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    _registry.stop()


def _get(camera_id: str):
    src = _registry.get(camera_id)
    if src is None:
        raise HTTPException(status_code=404, detail=f"cámara {camera_id} no registrada")
    return src


@app.get("/health")
def health() -> dict:
    stats = _registry.stats()
    connected = [s for s in stats if s["connected"]]
    return {
        "ok": True,
        "service": "media-service",
        "cameras": len(stats),
        "connected": len(connected),
        "fuenteConfig": "device-service" if _registry.using_api else "cameras.json",
        "syncError": _registry.last_sync_error,
    }


@app.get("/cameras")
def list_cameras() -> dict:
    return {"items": _registry.stats()}


@app.get("/cameras/{camera_id}/snapshot.jpg")
def snapshot(camera_id: str) -> Response:
    jpeg = _get(camera_id).latest_jpeg()
    if jpeg is None:
        raise HTTPException(status_code=503, detail="sin frames todavía")
    return Response(content=jpeg, media_type="image/jpeg")


@app.get("/cameras/{camera_id}/frame")
def frame_meta(camera_id: str) -> dict:
    """Metadatos del último frame (el ai-worker usa esto para no reprocesar)."""
    src = _get(camera_id)
    got = src.latest_frame()
    if got is None:
        raise HTTPException(status_code=503, detail="sin frames todavía")
    _, ts, seq = got
    return {"cameraId": camera_id, "seq": seq, "capturedAt": ts, "connected": src.connected}


@app.get("/cameras/{camera_id}/stream.mjpg")
def stream(camera_id: str) -> StreamingResponse:
    """Vista en vivo MJPEG: se consume directo desde un <img src=...>."""
    src = _get(camera_id)

    def gen():
        last_seq = -1
        idle = 0.0
        while True:
            with src._lock:  # noqa: SLF001 — lectura interna deliberada
                buf = src._buf[-1] if src._buf else None
            if buf is not None and buf.seq != last_seq:
                last_seq = buf.seq
                idle = 0.0
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(buf.jpeg)).encode()
                    + b"\r\n\r\n"
                    + buf.jpeg
                    + b"\r\n"
                )
            else:
                time.sleep(0.02)
                idle += 0.02
                if idle > 15:  # la cámara dejó de entregar: cerrar el stream
                    break

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.post("/cameras/{camera_id}/clip")
def make_clip(camera_id: str, body: dict) -> dict:
    """Arma el clip de un evento (10 s antes / evento / 10 s después)."""
    src = _get(camera_id)
    event_id = body.get("eventId") or f"ev-{int(time.time())}"
    center = float(body.get("centerTs") or time.time())
    dest = EVIDENCE_DIR / camera_id / f"{event_id}.mp4"

    # El post-roll obliga a esperar: se hace fuera del request.
    def work() -> None:
        build_clip(src, center, dest)

    threading.Thread(target=work, daemon=True).start()
    return {"accepted": True, "cameraId": camera_id, "eventId": event_id, "path": str(dest)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "3020")))
