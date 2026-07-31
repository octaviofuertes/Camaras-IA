"""Entry point del ai-worker.

MVP: descubre módulos en AI_MODULES_PATH, expone /health (FastAPI) y queda listo
para recibir frames del inference-orchestrator (gRPC, Fase 1) y publicar
DetectionBatch (protobuf) al exchange detections.raw.
"""
from __future__ import annotations

import os

from fastapi import FastAPI

from ai_worker.loader import discover

app = FastAPI(title="percepta-ai-worker")

MODULES_PATH = os.environ.get("AI_MODULES_PATH", "./modules")
_discovery = discover(MODULES_PATH)


@app.get("/health")
def health() -> dict:
    """Estado del worker.

    ok=False si algún módulo falló al cargar: un fallo silencioso significaría
    que una cámara cree tener una capacidad activa que en realidad no corre.
    """
    return {
        "ok": not _discovery.failed,
        "service": "ai-worker",
        "device": os.environ.get("AI_WORKER_DEVICE", "cpu"),
        "modules": [
            {"moduleKey": m.module_key, "version": m.version} for m in _discovery.loaded
        ],
        "failedModules": [
            {"name": f.name, "reason": f.reason} for f in _discovery.failed
        ],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "3010")))
