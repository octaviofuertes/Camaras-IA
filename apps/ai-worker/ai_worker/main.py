"""ai-worker: descubre módulos de IA y ejecuta el pipeline real por cámara.

Lee las asignaciones cámara↔módulo desde la base (camera_module_configs) a
través de la API, carga los plugins correspondientes y arranca un pipeline por
cámara. Expone /health con el estado real de cada uno.
"""
from __future__ import annotations

import json
import logging
import os
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
SERVICE_TOKEN = os.environ.get("SERVICE_TOKEN", "")
DEVICE = os.environ.get("AI_WORKER_DEVICE", "cpu")
ASSIGNMENTS_FILE = Path(os.environ.get("ASSIGNMENTS_FILE", "./assignments.json"))
PIPELINE_FPS = float(os.environ.get("PIPELINE_FPS", "3"))

_discovery = discover(MODULES_PATH)
_instances: dict[str, PerceptaModule] = {}
_pipelines: list[CameraPipeline] = []


def _load_assignments() -> list[CameraAssignment]:
    """Asignaciones cámara↔módulo.

    En producción vienen de `camera_module_configs`; acá se leen de un archivo
    para poder correr el pipeline sin depender de que device-service exista.
    """
    if not ASSIGNMENTS_FILE.is_file():
        log.warning("%s no existe: no hay cámaras que procesar", ASSIGNMENTS_FILE)
        return []
    raw = json.loads(ASSIGNMENTS_FILE.read_text(encoding="utf-8"))
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


@app.on_event("startup")
def _startup() -> None:
    assignments = _load_assignments()
    needed = {m["moduleKey"] for a in assignments for m in a.modules}

    for disc in _discovery.loaded:
        if disc.module_key not in needed:
            continue
        cfg: dict = {}
        for a in assignments:
            for m in a.modules:
                if m["moduleKey"] == disc.module_key:
                    cfg = m.get("config", {})
                    break
        inst = disc.module_class()
        ctx = ModuleContext(
            ai_module_id=disc.manifest.get("moduleKey", disc.module_key),
            module_key=disc.module_key,
            module_version=disc.version,
            device=DEVICE,
            config=cfg,
            zones={},
        )
        try:
            inst.load(ctx)
            inst.warmup()
            _instances[disc.module_key] = inst
            log.info("módulo listo: %s v%s", disc.module_key, disc.version)
        except Exception:
            log.exception("no se pudo cargar el módulo %s", disc.module_key)

    for a in assignments:
        if not any(m["moduleKey"] in _instances for m in a.modules):
            log.warning("[%s] ningún módulo asignado está disponible", a.camera_id)
            continue
        p = CameraPipeline(
            a, _instances, media_url=MEDIA_URL, event_url=EVENT_URL,
            token=SERVICE_TOKEN, fps=PIPELINE_FPS,
        )
        p.start()
        _pipelines.append(p)


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
            {"moduleKey": m.module_key, "version": m.version, "loaded": m.module_key in _instances}
            for m in _discovery.loaded
        ],
        "failedModules": [{"name": f.name, "reason": f.reason} for f in _discovery.failed],
        "pipelines": [p.stats() for p in _pipelines],
    }


@app.get("/detections")
def detections() -> dict:
    """Últimas detecciones por cámara — alimenta el overlay en vivo del dashboard."""
    return {p.a.camera_id: p.last_detections for p in _pipelines}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "3010")))
