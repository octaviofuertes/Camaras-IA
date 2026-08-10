"""ai-worker: descubre módulos de IA y ejecuta el pipeline real por cámara.

Lee las asignaciones cámara↔módulo desde la base (camera_module_configs) a
través de la API, carga los plugins correspondientes y arranca un pipeline por
cámara. Expone /health con el estado real de cada uno.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

import requests
from fastapi import FastAPI

from percepta_contracts import ModuleContext, PerceptaModule

from ai_worker.loader import discover
from ai_worker.pipeline import CameraAssignment, CameraPipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ai-worker")

app = FastAPI(title="percepta-ai-worker")

MODULES_PATH = os.environ.get("AI_MODULES_PATH", "./modules")
MEDIA_URL = os.environ.get("MEDIA_SERVICE_URL", "http://localhost:3020")
EVENT_URL = os.environ.get("EVENT_SERVICE_URL", "http://localhost:3004")
ANALYTICS_URL = os.environ.get("ANALYTICS_SERVICE_URL", "http://127.0.0.1:3005")
DEVICE_URL = os.environ.get("DEVICE_SERVICE_URL", "http://127.0.0.1:3003")
SERVICE_TOKEN = os.environ.get("SERVICE_TOKEN", "")
DEVICE = os.environ.get("AI_WORKER_DEVICE", "cpu")
ASSIGNMENTS_FILE = Path(os.environ.get("ASSIGNMENTS_FILE", "./assignments.json"))
PIPELINE_FPS = float(os.environ.get("PIPELINE_FPS", "3"))
# Cada cuánto se revisa si cambiaron las asignaciones en el dashboard.
SYNC_SECONDS = float(os.environ.get("ASSIGNMENT_SYNC_SECONDS", "15"))

_discovery = discover(MODULES_PATH)
# Clave: '<camara>:<modulo>'. Una instancia por cámara, no una por módulo.
_instances: dict[str, PerceptaModule] = {}
_pipelines: list[CameraPipeline] = []


def _assignments_from_api() -> list[CameraAssignment] | None:
    """Asignaciones cámara↔módulo desde `camera_module_configs` (device-service).

    Es la fuente de verdad: soltar un módulo sobre una cámara en el dashboard
    escribe esa tabla, y el worker lo levanta de acá.
    """
    if not SERVICE_TOKEN:
        return None
    headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
    try:
        cams = requests.get(f"{DEVICE_URL}/api/v1/cameras", headers=headers, timeout=5)
        assigns = requests.get(f"{DEVICE_URL}/api/v1/camera-module-configs", headers=headers, timeout=5)
        if cams.status_code != 200 or assigns.status_code != 200:
            log.warning("device-service respondió %s/%s", cams.status_code, assigns.status_code)
            return None
    except requests.RequestException as exc:
        log.warning("device-service no disponible: %s", exc)
        return None

    by_camera: dict[str, list[dict]] = {}
    for a in assigns.json().get("items", []):
        if not a.get("enabled", True):
            continue
        cfg = a.get("config") or {}
        by_camera.setdefault(a["cameraId"], []).append(
            {
                "moduleKey": a["moduleKey"],
                "aiModuleId": a["aiModuleId"],
                "moduleVersion": a.get("moduleVersion", "1.0.0"),
                # Tipo y severidad del evento: configurables por cámara, con
                # un valor por defecto derivado del módulo.
                "eventType": cfg.get("eventType", f"{a['moduleKey'].split('-')[0]}.detected"),
                "severity": cfg.get("severity", "medium"),
                "config": cfg,
            }
        )

    out: list[CameraAssignment] = []
    for c in cams.json().get("items", []):
        mods = by_camera.get(c["id"], [])
        if not mods or c.get("status") == "disabled":
            continue
        out.append(
            CameraAssignment(
                camera_id=c["id"],
                site_id=c["siteId"],
                organization_id=c["organizationId"],
                modules=mods,
            )
        )
    return out


def _load_assignments() -> list[CameraAssignment]:
    """Asignaciones desde la API; si no está disponible, desde el archivo."""
    api = _assignments_from_api()
    if api is not None:
        log.info("asignaciones desde device-service: %d cámara(s)", len(api))
        return api

    if not ASSIGNMENTS_FILE.is_file():
        log.warning("sin API y sin %s: no hay cámaras que procesar", ASSIGNMENTS_FILE)
        return []
    raw = json.loads(ASSIGNMENTS_FILE.read_text(encoding="utf-8"))
    log.info("asignaciones desde %s (respaldo)", ASSIGNMENTS_FILE)
    return [
        CameraAssignment(
            camera_id=a["cameraId"],
            site_id=a["siteId"],
            organization_id=a["organizationId"],
            modules=a.get("modules", []),
        )
        for a in raw
        if a.get("enabled", True)
    ]


def _arrancar_pipelines() -> None:
    """(Re)construye los pipelines según las asignaciones vigentes.

    Cada cámara recibe su PROPIA instancia de cada módulo. Antes había una sola
    instancia por módulo compartida entre todas las cámaras, y eso rompía tres
    cosas a la vez:

      1. El estado por persona se mezclaba. Los módulos con memoria temporal
         —la detección de caídas aprende la altura de pie de cada persona— usan
         el id de seguimiento como clave, y ese id lo numera el tracker desde 1
         en CADA cámara. La persona 1 de la cámara A y la persona 1 de la B eran
         la misma entrada. Medido: la referencia de altura saltaba de 0.250 a
         0.600 al intercalarse los frames de la otra cámara, y la persona de la
         primera pasaba a leerse al 42 % de su altura sin haberse movido.
      2. El seguimiento se corrompía. YOLO con `persist=True` mantiene UN tracker
         por modelo; alimentarlo alternando dos cámaras equivale a decirle que
         son un solo video donde la escena cambia por completo cada frame.
      3. No había seguridad entre hilos. Cada cámara corre en su propio hilo y
         todas llamaban a `infer()` sobre el mismo objeto, sin ningún candado.

    El costo es un modelo de pose por cámara. Es el precio correcto: compartirlo
    no ahorraba nada que valiera perder la identidad de las personas.
    """
    assignments = _load_assignments()

    for a in assignments:
        instancias: dict[str, PerceptaModule] = {}

        for m in a.modules:
            disc = next((d for d in _discovery.loaded if d.module_key == m["moduleKey"]), None)
            if disc is None:
                log.warning("[%s] el módulo %s no está disponible", a.camera_id, m["moduleKey"])
                continue

            # La configuración es la de ESTA cámara. Antes se tomaba la de la
            # primera que usara el módulo, así que los ajustes por cámara
            # —la razón de ser de la tabla de asignaciones— se ignoraban.
            ctx = ModuleContext(
                ai_module_id=disc.manifest.get("moduleKey", disc.module_key),
                module_key=disc.module_key,
                module_version=disc.version,
                device=DEVICE,
                config=m.get("config", {}),
                zones={},
            )
            inst = disc.module_class()
            try:
                inst.load(ctx)
                inst.warmup()
                instancias[disc.module_key] = inst
                _instances[f"{a.camera_id}:{disc.module_key}"] = inst
                log.info("[%s] módulo listo: %s v%s", a.camera_id, disc.module_key, disc.version)
            except Exception:
                log.exception("[%s] no se pudo cargar %s", a.camera_id, disc.module_key)

        if not instancias:
            log.warning("[%s] ningún módulo asignado está disponible", a.camera_id)
            continue

        p = CameraPipeline(
            a, instancias, media_url=MEDIA_URL, event_url=EVENT_URL,
            token=SERVICE_TOKEN, fps=PIPELINE_FPS, analytics_url=ANALYTICS_URL,
        )
        p.start()
        _pipelines.append(p)


def _firma(assignments) -> str:
    """Huella de la configuración: cambia si se agregó o quitó una asignación."""
    return "|".join(
        sorted(f"{a.camera_id}:{','.join(sorted(m['moduleKey'] for m in a.modules))}" for a in assignments)
    )


def _vigilar_asignaciones() -> None:
    """Reconstruye los pipelines cuando cambian las asignaciones.

    Sin esto, asignar un módulo desde el dashboard no tenía efecto hasta
    reiniciar el worker a mano — inaceptable en producción.
    """
    actual = _firma(_load_assignments())
    while True:
        time.sleep(SYNC_SECONDS)
        try:
            nuevas = _load_assignments()
            firma = _firma(nuevas)
            if firma == actual:
                continue
            log.info("cambiaron las asignaciones: reconstruyendo pipelines")
            actual = firma
            for p in _pipelines:
                p.stop()
            _pipelines.clear()
            # Soltar las instancias viejas antes de crear las nuevas: cada una
            # tiene su propio modelo de pose cargado, y no liberarlas dejaba uno
            # colgado en memoria por cada cambio de asignación.
            for inst in _instances.values():
                try:
                    inst.release()
                except Exception:  # noqa: BLE001
                    log.exception("fallo al liberar un módulo")
            _instances.clear()
            _arrancar_pipelines()
        except Exception:
            log.exception("fallo al sincronizar asignaciones")


@app.on_event("startup")
def _startup() -> None:
    _arrancar_pipelines()
    threading.Thread(target=_vigilar_asignaciones, name="sync-asignaciones", daemon=True).start()


@app.on_event("shutdown")
def _shutdown() -> None:
    for p in _pipelines:
        p.stop()
    for inst in _instances.values():
        try:
            inst.release()
        except Exception:
            pass


@app.get("/health")
def health() -> dict:
    return {
        "ok": not _discovery.failed and bool(_instances),
        "service": "ai-worker",
        "device": DEVICE,
        "modules": [
            {
                "moduleKey": m.module_key,
                "version": m.version,
                # Cuántas cámaras lo tienen cargado, que ahora puede ser más de una.
                "instancias": sum(1 for k in _instances if k.endswith(f":{m.module_key}")),
                "loaded": any(k.endswith(f":{m.module_key}") for k in _instances),
            }
            for m in _discovery.loaded
        ],
        "failedModules": [{"name": f.name, "reason": f.reason} for f in _discovery.failed],
        "pipelines": [p.stats() for p in _pipelines],
    }


@app.get("/detections")
def detections() -> dict:
    """Últimas detecciones por cámara — alimenta el overlay en vivo del dashboard."""
    return {p.a.camera_id: p.last_detections for p in _pipelines}


@app.get("/modules/{module_key}/state")
def module_state(module_key: str, camera: str | None = None) -> dict:
    """Estado interno de un módulo. Sirve para ver en vivo cómo razona.

    Con varias cámaras hay una instancia por cada una, así que el estado se
    devuelve por cámara. Sin el parámetro `camera` se devuelve el de todas: era
    engañoso mostrar una sola y presentarla como "el módulo".
    """
    coincidencias = {
        k.split(":", 1)[0]: v for k, v in _instances.items() if k.endswith(f":{module_key}")
    }
    if not coincidencias:
        return {"error": f"módulo {module_key} no cargado en ninguna cámara"}

    if camera is not None:
        inst = coincidencias.get(camera)
        if inst is None:
            return {"error": f"la cámara {camera} no tiene cargado {module_key}"}
        try:
            return inst.health()
        except Exception as exc:  # noqa: BLE001
            return {"error": repr(exc)}

    salida: dict = {"camaras": {}}
    for cam, inst in coincidencias.items():
        try:
            salida["camaras"][cam] = inst.health()
        except Exception as exc:  # noqa: BLE001
            salida["camaras"][cam] = {"error": repr(exc)}
    # Se conserva la forma anterior cuando hay una sola cámara, para no romper
    # las herramientas que ya la consumen.
    if len(coincidencias) == 1:
        salida.update(next(iter(salida["camaras"].values())))
    return salida


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "3010")))
