"""media-service: captura real de video, vista en vivo y evidencias.

Expone:
  GET /health
  GET /cameras                          -> estado de todas las fuentes
  GET /cameras/{id}/stream.mjpg         -> vista en vivo (MJPEG)
  GET /cameras/{id}/snapshot.jpg        -> último frame
  GET /cameras/{id}/frame               -> último frame + metadatos (para ai-worker)
  POST /cameras/{id}/clip               -> arma el clip pre/post de un evento
  POST /cameras/{id}/snapshot           -> guarda la foto del instante de un evento

MJPEG en lugar de WebRTC para el MVP: se consume desde un <img> sin
señalización ni STUN/TURN, y funciona igual con USB que con RTSP. WebRTC
(WHEP, CONTRACTS §11) queda para cuando importe la latencia y la escala.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from .clips import PRE_ROLL_S, POST_ROLL_S, build_clip, build_snapshot, info_clip
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


@app.get("/storage")
def storage() -> dict:
    """Cuánto ocupan las evidencias y cuánto queda en el disco. Todo medido.

    El panel mostraba un "2,45 TB de 10 TB" inventado. Un número de
    almacenamiento que no se mide es peor que no mostrarlo: el día que el disco
    se llene de verdad, la pantalla va a seguir diciendo que sobra lugar.

    Se recorre el árbol de evidencias en vez de guardar un contador porque los
    clips también se borran desde afuera —al resolver un evento, o a mano— y un
    contador que no ve esos borrados se va despegando de la realidad.
    """
    usados = 0
    archivos = 0
    if EVIDENCE_DIR.exists():
        for f in EVIDENCE_DIR.rglob("*"):
            try:
                if f.is_file():
                    usados += f.stat().st_size
                    archivos += 1
            except OSError:
                # Un archivo que desaparece mientras se lo recorre no es un
                # error: lo acaba de borrar quien resolvió el evento.
                continue

    try:
        du = shutil.disk_usage(EVIDENCE_DIR if EVIDENCE_DIR.exists() else Path("."))
        total, libre = du.total, du.free
    except OSError as exc:
        log.warning("no se pudo medir el disco: %s", exc)
        total, libre = 0, 0

    return {
        "evidenciasBytes": usados,
        "evidenciasArchivos": archivos,
        "discoTotalBytes": total,
        "discoLibreBytes": libre,
        "ruta": str(EVIDENCE_DIR.resolve()) if EVIDENCE_DIR.exists() else str(EVIDENCE_DIR),
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
    """Arma el clip de un evento (unos segundos antes / el momento / después).

    Con `wait: true` responde recién cuando el archivo está listo, e informa su
    tamaño y hash. Es lo que necesita event-service para registrar la evidencia
    con datos verificables en vez de una promesa.
    """
    src = _get(camera_id)
    event_id = body.get("eventId") or f"ev-{int(time.time())}"
    center = float(body.get("centerTs") or time.time())
    esperar = bool(body.get("wait"))
    dest = EVIDENCE_DIR / camera_id / f"{event_id}.mp4"

    if not esperar:
        threading.Thread(target=lambda: build_clip(src, center, dest), daemon=True).start()
        return {"accepted": True, "cameraId": camera_id, "eventId": event_id, "path": str(dest)}

    ruta = build_clip(src, center, dest)
    if ruta is None or not ruta.is_file():
        raise HTTPException(status_code=503, detail="no se pudo armar el clip (¿buffer vacío?)")

    datos = ruta.read_bytes()
    info = info_clip(ruta)
    return {
        "accepted": True,
        "cameraId": camera_id,
        "eventId": event_id,
        "path": str(ruta),
        "bytes": len(datos),
        "sha256": hashlib.sha256(datos).hexdigest(),
        # Duración REAL del archivo, no la ventana pedida. Si el buffer no
        # tenía todos los frames —cámara recién arrancada, o corte— el clip sale
        # más corto, y decir lo contrario haría que la evidencia declare algo
        # que su propio video no muestra.
        "durationMs": int(info.get("segundos", PRE_ROLL_S + POST_ROLL_S) * 1000),
        # Se informan los valores REALES con los que se armó este clip. Antes
        # event-service los guardaba fijos en 10 s cada uno: al cambiar la
        # duración, la base seguía diciendo diez y nadie se enteraba de que el
        # dato estaba mal.
        "preRollMs": int(PRE_ROLL_S * 1000),
        "postRollMs": int(POST_ROLL_S * 1000),
    }


@app.post("/cameras/{camera_id}/snapshot")
def make_snapshot(camera_id: str, body: dict) -> dict:
    """Guarda la FOTO de un evento: el frame del instante en que sonó la alerta.

    Es la evidencia de lo que se contesta mirando un instante —alguien sin
    casco, una cara desconocida—. A diferencia del clip, responde enseguida: no
    hay post-roll que esperar, así que el operador ve la foto apenas aparece la
    alerta en vez de tres segundos después.

    `boxes` es opcional y son las cajas de la detección: si vienen, se dibujan
    sobre la foto, para que quien la mire sepa de QUIÉN de los que están en el
    cuadro habla la alerta.
    """
    src = _get(camera_id)
    event_id = body.get("eventId") or f"ev-{int(time.time())}"
    center = float(body.get("centerTs") or time.time())
    cajas = body.get("boxes") or None
    dest = EVIDENCE_DIR / camera_id / f"{event_id}.jpg"

    ruta = build_snapshot(src, center, dest, boxes=cajas)
    if ruta is None or not ruta.is_file():
        raise HTTPException(status_code=503, detail="no se pudo sacar la foto (¿buffer vacío?)")

    datos = ruta.read_bytes()
    return {
        "accepted": True,
        "cameraId": camera_id,
        "eventId": event_id,
        "path": str(ruta),
        "bytes": len(datos),
        "sha256": hashlib.sha256(datos).hexdigest(),
        # Qué tan lejos quedó la foto del instante pedido. Con la cámara
        # entregando frames es de milésimas; si sale de segundos, la cámara
        # estaba cortada y la foto es del último momento que sí se vio.
        "desfasajeSegundos": round(float(info_clip(ruta).get("desfasajeSegundos", 0.0)), 3),
    }


@app.delete("/evidence")
def delete_evidence(body: dict) -> dict:
    """Borra un clip provisional que resultó no ser nada.

    Se usa cuando el operador marca la alerta como falso positivo: el video se
    grabó por las dudas, y si no hubo nada que documentar no hay motivo para
    conservar imágenes de una persona.

    La ruta se valida contra el directorio de evidencias: un `storageKey` que
    apunte fuera de ahí no borra nada, venga de donde venga.
    """
    clave = str(body.get("storageKey") or "")
    if not clave:
        raise HTTPException(status_code=400, detail="falta storageKey")

    base = EVIDENCE_DIR.resolve()
    ruta = Path(clave)
    if not ruta.is_absolute():
        # Las claves se guardan relativas a la raíz del servicio.
        ruta = (Path.cwd() / ruta).resolve()
    else:
        ruta = ruta.resolve()

    if not str(ruta).startswith(str(base)):
        raise HTTPException(status_code=400, detail="la clave no pertenece al directorio de evidencias")

    if not ruta.is_file():
        # Ya no está: el resultado deseado igual se cumple.
        return {"deleted": True, "reason": "no existe"}

    try:
        ruta.unlink()
    except PermissionError:
        # En Windows no se puede borrar un archivo que alguien está leyendo, y
        # el caso normal es justamente ése: el operador mira el clip y desde el
        # mismo reproductor lo marca como falso positivo. Se responde 409 para
        # que quien llama sepa que hay que reintentar, en vez de un 500 que
        # parece un error del servidor y hace perder el rastro del archivo.
        log.warning("clip en uso, no se pudo borrar todavía: %s", ruta.name)
        raise HTTPException(status_code=409, detail="el archivo está en uso")

    log.info("clip descartado: %s", ruta.name)
    return {"deleted": True}


@app.get("/evidence/{camera_id}/{nombre}")
def get_evidence(camera_id: str, nombre: str, request: Request) -> Response:
    """Descarga o reproducción de un clip guardado, con soporte de rangos.

    NO usa FileResponse. Un `<video>` pide rangos y abandona conexiones cada vez
    que el usuario mueve la barra de tiempo; con FileResponse esos descriptores
    quedaban abiertos y en Windows un archivo abierto no se puede borrar. El
    efecto: marcar una alerta como falso positivo no lograba eliminar el video,
    justo lo contrario de lo que promete esa acción.

    Acá el descriptor se cierra siempre, incluso si el cliente corta a la mitad:
    el `finally` del generador corre igual cuando se lo cierra por GeneratorExit.

    El nombre se sanea contra path traversal: sólo se sirve lo que está dentro
    del directorio de evidencias de esa cámara.
    """
    if "/" in nombre or "\\" in nombre or ".." in nombre:
        raise HTTPException(status_code=400, detail="nombre inválido")
    ruta = (EVIDENCE_DIR / camera_id / nombre).resolve()
    base = EVIDENCE_DIR.resolve()
    if not str(ruta).startswith(str(base)) or not ruta.is_file():
        raise HTTPException(status_code=404, detail="evidencia no encontrada")

    total = ruta.stat().st_size
    inicio, fin = 0, total - 1
    estado = 200
    rango = request.headers.get("range") or request.headers.get("Range")
    if rango and rango.startswith("bytes="):
        crudo = rango[6:].split(",")[0].strip()
        desde, _, hasta = crudo.partition("-")
        try:
            if desde:
                inicio = int(desde)
                fin = int(hasta) if hasta else total - 1
            elif hasta:  # sufijo: los últimos N bytes
                inicio = max(total - int(hasta), 0)
        except ValueError:
            raise HTTPException(status_code=416, detail="rango inválido")
        if inicio >= total or fin < inicio:
            raise HTTPException(status_code=416, detail="rango fuera del archivo")
        fin = min(fin, total - 1)
        estado = 206

    largo = fin - inicio + 1

    def leer():
        # El archivo se abre y se cierra en CADA trozo, en vez de mantenerlo
        # abierto durante toda la respuesta.
        #
        # Parece derrochador y es deliberado. Un generador de streaming queda
        # suspendido en el `yield`, y cuando el reproductor abandona la
        # conexión —cosa que hace cada vez que se mueve la barra de tiempo—
        # nadie lo reanuda ni lo cierra: un `try/finally` alrededor del bucle
        # nunca llega a ejecutarse. Medido: el archivo seguía tomado 6 segundos
        # después de cortar, y en Windows eso significa que marcar la alerta
        # como falso positivo no puede borrar el video.
        #
        # Cerrando entre trozos, el descriptor sólo vive durante la lectura en
        # sí. Son unos pocos microsegundos cada 512 KB, y descargar evidencia no
        # es un camino caliente.
        pos, restante = inicio, largo
        while restante > 0:
            with ruta.open("rb") as f:
                f.seek(pos)
                trozo = f.read(min(512 * 1024, restante))
            if not trozo:
                break
            pos += len(trozo)
            restante -= len(trozo)
            yield trozo

    cabeceras = {
        "Content-Length": str(largo),
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'inline; filename="{nombre}"',
    }
    if estado == 206:
        cabeceras["Content-Range"] = f"bytes {inicio}-{fin}/{total}"

    # El tipo sale de la extensión y no está fijo en video/mp4: desde que las
    # alertas de EPP y de cara desconocida guardan una FOTO, servirla como
    # video hacía que el navegador no la mostrara —y la evidencia existía en
    # disco pero no se podía mirar—.
    tipo = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(
        ruta.suffix.lower().lstrip("."), "video/mp4"
    )

    return StreamingResponse(leer(), status_code=estado, media_type=tipo, headers=cabeceras)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "3020")))
