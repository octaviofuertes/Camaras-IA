"""ai-worker: descubre módulos de IA y ejecuta el pipeline real por cámara.

Lee las asignaciones cámara↔módulo desde la base (camera_module_configs) a
través de la API, carga los plugins correspondientes y arranca un pipeline por
cámara. Expone /health con el estado real de cada uno.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

# Antes que nada lo que traiga torch: OpenMP lee la cantidad de hilos cuando
# arranca y después ya no la mira. Ver ai_worker/hilos.py.
from ai_worker import hilos as _hilos

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
# Clave del módulo de ingreso de personas. Igual que en
# packages/contracts/src/modules.ts y en modules/person-entry/module.json.
MODULO_INGRESO = "person-entry"
# Clave del módulo de elementos de protección personal.
MODULO_EPP = "ppe-detection"

# Cada cuánto se revisa si cambiaron las asignaciones en el dashboard.
SYNC_SECONDS = float(os.environ.get("ASSIGNMENT_SYNC_SECONDS", "15"))

_discovery = discover(MODULES_PATH)


def _descubierto(module_key: str):
    return next((d for d in _discovery.loaded if d.module_key == module_key), None)


def _tipos_declarados(module_key: str) -> list[dict]:
    """Todos los tipos de evento que declara el manifiesto del módulo."""
    d = _descubierto(module_key)
    return list((d.manifest.get("eventTypes") if d else None) or [])


def _tipo_declarado(module_key: str, campo: str, si_falta: str) -> str:
    """Lo que el manifiesto dice del primer tipo de evento del módulo."""
    d = _descubierto(module_key)
    tipos = (d.manifest.get("eventTypes") if d else None) or []
    return str(tipos[0].get(campo) or si_falta) if tipos else si_falta


_defaults_cache: dict[str, dict] = {}


def preparar_modulo(a: dict) -> dict:
    """Convierte una asignación de la base en la configuración que corre.

    Tres capas, de menor a mayor prioridad: lo que declara el manifiesto, lo que
    declara el config.schema.json del módulo, y lo que guardó esta cámara.

    Antes no había capas: se usaba la config de la base tal cual y, para lo que
    faltara, un valor adivinado a partir del NOMBRE del módulo —de
    'person-entry' salía el evento 'person.detected', que ese módulo no
    emite—. Una asignación con config vacía quedaba corriendo con reglas que no
    eran de nadie, y la alerta se descartaba sin dejar rastro.
    """
    clave = a["moduleKey"]
    cfg = {**_defaults_del_modulo(clave), **(a.get("config") or {})}
    return {
        "moduleKey": clave,
        "aiModuleId": a["aiModuleId"],
        "moduleVersion": a.get("moduleVersion", "1.0.0"),
        "eventType": cfg.get("eventType") or _tipo_declarado(clave, "type", f"{clave}.detected"),
        "severity": cfg.get("severity") or _tipo_declarado(clave, "defaultSeverity", "medium"),
        # Todos los tipos que este módulo declara: cada detección se emite con
        # el suyo. Un módulo con dos alertas distintas no puede mandarlas a
        # ambas con el mismo tipo de evento.
        "eventTypes": _tipos_declarados(clave),
        "config": cfg,
    }


def _defaults_del_modulo(module_key: str) -> dict:
    """Valores por defecto declarados en el config.schema.json del módulo.

    Son los mismos que el formulario del dashboard escribe al asignar. Leerlos
    acá hace que el pipeline se comporte igual con una asignación hecha desde la
    UI que con una insertada a mano, en vez de depender de que alguien haya
    pasado por la pantalla correcta.
    """
    if module_key in _defaults_cache:
        return _defaults_cache[module_key]

    valores: dict = {}
    d = _descubierto(module_key)
    if d is not None:
        ref = str(d.manifest.get("configSchemaRef") or "./config.schema.json")
        ruta = (d.path / ref).resolve()
        try:
            esquema = json.loads(ruta.read_text(encoding="utf-8"))
            for nombre, prop in (esquema.get("properties") or {}).items():
                if isinstance(prop, dict) and "default" in prop:
                    valores[nombre] = prop["default"]
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("no se pudieron leer los defaults de %s: %s", module_key, exc)

    _defaults_cache[module_key] = valores
    return valores
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
        by_camera.setdefault(a["cameraId"], []).append(preparar_modulo(a))

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

        asignados = {m["moduleKey"] for m in a.modules}

        for m in a.modules:
            disc = next((d for d in _discovery.loaded if d.module_key == m["moduleKey"]), None)
            if disc is None:
                log.warning("[%s] el módulo %s no está disponible", a.camera_id, m["moduleKey"])
                continue

            # Dependencias entre módulos: un manifiesto puede declarar en
            # `requires` que necesita otro módulo asignado a la MISMA cámara.
            # Es el mismo criterio que usa el producto entero —una función
            # existe donde su módulo está asignado— aplicado entre plugins.
            requiere = list(disc.manifest.get("requires") or [])
            faltan = [r for r in requiere if r not in asignados]
            if faltan:
                # Se rechaza en vez de correr degradado. Un módulo a medias que
                # nadie sabe que está a medias produce un informe que parece
                # completo y no lo está — y en este caso lo que faltaría son
                # justamente los nombres.
                log.error(
                    "[%s] %s NO se carga: necesita %s, que no está asignado a esta cámara",
                    a.camera_id, disc.module_key, ", ".join(faltan),
                )
                continue
            m["requires"] = requiere

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
                analytics_url=ANALYTICS_URL,
                service_token=SERVICE_TOKEN,
                camera_id=a.camera_id,
                site_id=a.site_id,
            )
            inst = disc.module_class()
            try:
                inst.load(ctx)
                inst.warmup()
                # El pisotón de ultralytics ocurre en la PRIMERA inferencia
                # —el warmup—, no al importarse: ahí deja los hilos de torch en
                # `cpu_count()-1`. Medido: se repone una sola vez, así que
                # alcanza con corregirlo acá y queda puesto.
                #
                # Y tiene que ser acá y no por cuadro: `set_num_threads`
                # rearma el pool de hilos, y llamarlo desde los dos pipelines a
                # la vez, en cada cuadro, lo rearmaba en medio de la inferencia
                # del otro. Eso llevó el cuadro de 0,5 s a 25 s.
                _hilos.reafirmar()
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
    """Huella de la configuración: cambia si se agregó, se quitó o se RECONFIGURÓ.

    Antes sólo miraba qué módulos corría cada cámara. Con eso, asignar y
    desasignar se detectaba, pero cambiar la configuración —qué EPP se exige,
    cada cuánto puede repetir el aviso, con cuánta evidencia alerta— no movía
    nada: el worker seguía con lo que había leído al arrancar, y el cambio
    hecho en el dashboard sólo tenía efecto si alguien lo reiniciaba a mano.

    Es el mismo error que se venía arrastrando en el módulo de EPP visto desde
    el otro lado: la configuración que corre y la que está guardada eran dos
    cosas distintas sin que nada lo dijera.
    """
    partes = []
    for a in sorted(assignments, key=lambda x: x.camera_id):
        modulos = sorted(a.modules, key=lambda m: m["moduleKey"])
        partes.append(f"{a.camera_id}:{json.dumps(modulos, sort_keys=True, default=str)}")
    return hashlib.sha1("|".join(partes).encode("utf-8")).hexdigest()


#: Si device-service contestó alguna vez. Ver `_asignaciones_para_comparar`.
_hubo_api = False


def _asignaciones_para_comparar() -> list[CameraAssignment] | None:
    """Las asignaciones, o None cuando no hay con qué decidir si algo cambió.

    El archivo de respaldo sirve para ARRANCAR sin device-service, pero no para
    decidir que la configuración cambió. Sus datos son de cuando se lo escribió
    y casi nunca coinciden con los de la base: si device-service se cae un
    segundo y se lee el respaldo, la firma da distinta, y el worker tira abajo
    los dos pipelines y recarga todos los modelos por una caída que ya pasó.

    Visto en el log: a las 14:16:03 se leyó el respaldo, se reconstruyó todo, y
    a las 14:16:07 device-service ya contestaba de nuevo. Los cuadros pasaron de
    150 ms a 25 s mientras los modelos volvían a cargar, y en pantalla eso son
    las cámaras trabadas y los recuadros clavados.

    Es el mismo error que ya se había arreglado en media-service, del otro lado:
    ante la duda no se toca nada. Devolver None es exactamente eso.

    Si device-service NUNCA contestó, el respaldo sí manda: es la única fuente
    que hay, y sin esto no se podría cambiar nada en una instalación que corre
    sólo con el archivo.
    """
    global _hubo_api
    api = _assignments_from_api()
    if api is not None:
        _hubo_api = True
        return api
    if _hubo_api:
        return None
    return _load_assignments()


def _vigilar_asignaciones() -> None:
    """Reconstruye los pipelines cuando cambian las asignaciones.

    Sin esto, asignar un módulo desde el dashboard no tenía efecto hasta
    reiniciar el worker a mano — inaceptable en producción.
    """
    actual = _firma(_load_assignments())
    while True:
        time.sleep(SYNC_SECONDS)
        try:
            nuevas = _asignaciones_para_comparar()
            if nuevas is None:
                continue
            firma = _firma(nuevas)
            if firma == actual:
                continue
            log.info("cambiaron las asignaciones o su configuración: "
                     "reconstruyendo pipelines")
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


def _hilos_de_calculo() -> dict:
    """Con cuántos hilos corre cada modelo, que es lo que fija su velocidad.

    Va en /health porque no es evidente y cambia todo: los modelos corren en
    CPU y con un solo hilo tardan varias veces más. `ultralytics` toca
    `OMP_NUM_THREADS` al importarse y lo deja en 1, que en CPU cuesta casi el
    doble por cuadro. `ai_worker.hilos` lo vuelve a poner; esto es para ver que
    haya quedado puesto de verdad y no confiar en que sí.
    """
    datos: dict = {
        "cpus": os.cpu_count(),
        "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
        "elegidos": _hilos.elegir(),
        "reescrituras": _hilos.ESCRITURAS,
    }
    try:
        import torch

        datos["torch"] = torch.get_num_threads()
    except Exception:  # noqa: BLE001
        datos["torch"] = None
    return datos


@app.get("/health")
def health() -> dict:
    return {
        "ok": not _discovery.failed and bool(_instances),
        "service": "ai-worker",
        "device": DEVICE,
        "hilos": _hilos_de_calculo(),
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


@app.get("/live/{camera_id}")
def live(camera_id: str, nombrar: int | None = None) -> dict:
    """Quién se ve en esta cámara ahora mismo, con su cara y su identidad.

    Lo consume la vista ampliada del dashboard para marcar a una persona sobre
    el video. Es el presente y nada más: no hay historia acá, y si el módulo no
    está corriendo en esa cámara la respuesta es una lista vacía, no un error
    —la pantalla tiene que poder mostrar el video igual.

    `nombrar` es el seguimiento de una persona sin identificar a la que se le
    quiere poner un nombre: sólo para ésa viaja el recorte de su cara. Sin el
    parámetro, la respuesta son recuadros y nombres, ninguna imagen.
    """
    salida: dict = {
        # `ts` es DE CUÁNDO son los recuadros —el instante del cuadro que se
        # analizó— y `ahora` es el reloj del worker al contestar. La pantalla
        # necesita los dos para dibujar bien: entre que se captura un cuadro y
        # llega la respuesta pasan unos 200 ms, y en ese rato la persona se
        # movió. Con la diferencia, la pantalla adelanta el recuadro hasta
        # donde la persona está AHORA en vez de dejarlo donde estaba.
        #
        # `ahora` se pone acá y no en cada módulo: con una cámara que sólo
        # tiene EPP asignado se quedaba en cero, y sin él la pantalla no puede
        # comparar su reloj con el del worker.
        "ts": 0, "ahora": time.time(), "personas": [], "modulo": False,
        "epp": [], "exigidos": [], "moduloEpp": False, "eppPersonas": [],
        "sinAlertarEpp": [], "tsEpp": 0,
    }

    # Los dos módulos son independientes: una cámara puede tener uno, el otro,
    # los dos o ninguno. Cada uno aporta lo suyo y la pantalla dibuja lo que
    # haya, en vez de quedarse en blanco porque falta el que no está asignado.
    inst = _instances.get(f"{camera_id}:{MODULO_INGRESO}")
    if inst is not None and hasattr(inst, "en_vivo"):
        try:
            salida.update({**inst.en_vivo(nombrar), "modulo": True})
        except Exception as exc:  # noqa: BLE001
            log.error("no se pudo leer el ingreso de personas de %s: %r", camera_id, exc)

    epp = _instances.get(f"{camera_id}:{MODULO_EPP}")
    if epp is not None and hasattr(epp, "en_vivo"):
        try:
            v = epp.en_vivo()
            salida.update({
                "epp": v.get("elementos", []),
                "exigidos": v.get("exigidos", []),
                "sinAlertarEpp": v.get("sinAlertar", []),
                # Las personas del EPP van aparte de las del ingreso: son otro
                # modelo y otro orden. Mezclarlas en una sola lista fue el
                # error que le atribuía el casco de uno al de al lado.
                "eppPersonas": v.get("personas", []),
                # De cuándo son ESTOS recuadros. Va aparte del `ts` del ingreso
                # de personas porque son dos modelos con dos ritmos: mezclarlos
                # haría que la pantalla adelante los de uno con la antigüedad
                # del otro.
                "tsEpp": v.get("ts", 0),
                "moduloEpp": True,
            })
        except Exception as exc:  # noqa: BLE001
            log.error("no se pudo leer el EPP de %s: %r", camera_id, exc)

    return salida


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


# Detector propio para analizar fotos sueltas. Se carga una sola vez y sólo si
# hace falta.
_app_fotos = None


def _detector_de_caras():
    """El detector con el que se analizan las fotos que llegan por HTTP.

    Se prefiere la instancia que ya tiene cargada un pipeline: es el mismo
    modelo y evita ocupar memoria dos veces. Pero si no hay ninguna —porque la
    cámara está apagada o todavía no arrancó su pipeline— se carga una propia.

    Antes esto devolvía un error y dejaba sin funcionar el alta de personas y la
    pantalla de bienvenida cada vez que se caía una cámara, que es justamente
    cuando alguien puede estar cargando gente.

    Que exista este respaldo NO abre la función cuando el módulo no está
    asignado: quien decide eso es analytics-service, que contesta 409 antes de
    llegar hasta acá. Este detector sólo cubre el hueco entre "el módulo está
    asignado" y "el pipeline de esa cámara ya está andando".
    """
    global _app_fotos

    inst = next(
        (v for k, v in _instances.items() if k.endswith(f":{MODULO_INGRESO}")), None
    )
    propia = getattr(inst, "_app", None) if inst is not None else None
    if propia is not None:
        return propia

    if _app_fotos is None:
        try:
            from insightface.app import FaceAnalysis

            log.info("cargando el detector de rostros para analizar fotos sueltas")
            _app_fotos = FaceAnalysis(
                name="buffalo_l",
                allowed_modules=["detection", "recognition"],
                providers=["CPUExecutionProvider"],
            )
            _app_fotos.prepare(ctx_id=-1, det_size=(640, 640))
        except Exception:  # noqa: BLE001
            log.exception("no se pudo cargar el detector de rostros")
            return None
    return _app_fotos


@app.post("/faces/analyze")
def analyze_face(payload: dict) -> dict:
    """Analiza una foto y devuelve la plantilla facial que contiene, si hay.

    Lo usa el alta manual: alguien sube o saca una foto y hay que convertirla en
    algo con lo que después se pueda reconocer a esa persona. El modelo está
    cargado acá, así que la conversión ocurre acá.

    Devuelve SIEMPRE qué se pudo y qué no. Una foto de espaldas no tiene cara y
    no produce plantilla: decirlo es la diferencia entre que el operador sepa
    que esa foto no va a servir para reconocer a nadie y que crea que sí.
    """
    import base64 as _b64

    b64 = str(payload.get("image", ""))
    if "," in b64[:64]:                      # data:image/jpeg;base64,....
        b64 = b64.split(",", 1)[1]
    try:
        crudo = _b64.b64decode(b64)
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "la imagen no es base64 válido"}

    import cv2 as _cv2
    import numpy as _np

    img = _cv2.imdecode(_np.frombuffer(crudo, _np.uint8), _cv2.IMREAD_COLOR)
    if img is None:
        return {"ok": False, "error": "no se pudo leer la imagen"}

    app_caras = _detector_de_caras()
    if app_caras is None:
        return {"ok": False, "error": "no se pudo cargar el modelo de rostros"}

    h, w = img.shape[:2]
    caras = []
    for c in app_caras.get(img):
        x1, y1, x2, y2 = (float(v) for v in c.bbox)
        pose = getattr(c, "pose", None)
        emb = _np.asarray(c.normed_embedding, dtype=_np.float32)
        caras.append({
            "embedding": emb.tolist(),
            "score": round(float(getattr(c, "det_score", 0.0)), 3),
            "alto": round((y2 - y1) / h, 4),
            "yaw": round(float(pose[1]), 1) if pose is not None and len(pose) > 1 else None,
            "pitch": round(float(pose[0]), 1) if pose is not None else None,
        })

    # La mejor primero: si hay varias personas en la foto, la más grande y nítida
    # es casi siempre la que se quiso fotografiar.
    caras.sort(key=lambda c: (c["alto"], c["score"]), reverse=True)
    return {"ok": True, "caras": caras}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "3010")))
