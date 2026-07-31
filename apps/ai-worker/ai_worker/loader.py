"""Descubrimiento y carga de módulos de IA (plugins PerceptaModule).

Escanea AI_MODULES_PATH buscando carpetas con module.json, valida el manifest
contra el meta-schema canónico (CONTRACTS §4), verifica compatibilidad de
pluginApiVersion (mismo major) y carga la clase MODULE_CLASS de module.py.

Aislamiento de fallos: un módulo defectuoso (manifest inválido, import roto,
contrato incumplido) NO puede tumbar el worker ni impedir la carga del resto.
Se registra como FailedModule y queda expuesto en /health para diagnóstico.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from percepta_contracts import PLUGIN_API_VERSION, PerceptaModule

# Meta-schema canónico: packages/contracts/schemas/module-manifest.schema.json
_META_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "packages" / "contracts" / "schemas" / "module-manifest.schema.json"
)


@dataclass(frozen=True)
class DiscoveredModule:
    module_key: str
    version: str
    manifest: dict
    module_class: type[PerceptaModule]
    path: Path


@dataclass(frozen=True)
class FailedModule:
    """Módulo que no se pudo cargar. Se reporta, no se propaga."""
    name: str
    reason: str
    path: Path


@dataclass(frozen=True)
class DiscoveryResult:
    loaded: list[DiscoveredModule] = field(default_factory=list)
    failed: list[FailedModule] = field(default_factory=list)


class ModuleLoadError(Exception):
    pass


def _same_major(a: str, b: str) -> bool:
    return a.split(".")[0] == b.split(".")[0]


@lru_cache(maxsize=1)
def _meta_schema() -> dict | None:
    """Carga el meta-schema una sola vez. None si no está disponible."""
    try:
        return json.loads(_META_SCHEMA_PATH.read_text(encoding="utf-8"))
    except OSError:
        return None


def validate_manifest(manifest: dict) -> None:
    """Valida el manifest contra el meta-schema canónico. Lanza ModuleLoadError.

    Si jsonschema o el meta-schema no están disponibles se falla en CERRADO:
    un manifest no verificable no se carga (el manifest gobierna qué código
    se ejecuta, así que no validar es un riesgo de seguridad, no una molestia).
    """
    schema = _meta_schema()
    if schema is None:
        raise ModuleLoadError(
            f"meta-schema no encontrado en {_META_SCHEMA_PATH}: no se puede validar"
        )
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover
        raise ModuleLoadError(
            "falta la dependencia 'jsonschema': no se puede validar el manifest"
        ) from exc

    errors = sorted(Draft202012Validator(schema).iter_errors(manifest), key=lambda e: e.path)
    if errors:
        detalle = "; ".join(
            f"{'/'.join(map(str, e.path)) or '(raíz)'}: {e.message}" for e in errors[:5]
        )
        raise ModuleLoadError(f"manifest inválido contra el meta-schema — {detalle}")


def _load_one(manifest_path: Path) -> DiscoveredModule:
    module_dir = manifest_path.parent

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ModuleLoadError(f"module.json ilegible: {exc}") from exc

    # 1. El manifest debe cumplir el contrato ANTES de ejecutar código del módulo.
    validate_manifest(manifest)

    # 2. Compatibilidad de API del plugin (mismo major que el core).
    api_version = manifest["pluginApiVersion"]
    if not _same_major(api_version, PLUGIN_API_VERSION):
        raise ModuleLoadError(
            f"pluginApiVersion {api_version} incompatible con core {PLUGIN_API_VERSION}"
        )

    entry = module_dir / "module.py"
    if not entry.is_file():
        raise ModuleLoadError("falta module.py")

    spec = importlib.util.spec_from_file_location(
        f"percepta_module_{module_dir.name.replace('-', '_')}", entry
    )
    if not spec or not spec.loader:
        raise ModuleLoadError("no se pudo preparar el import de module.py")

    py_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = py_module
    try:
        spec.loader.exec_module(py_module)
    except Exception as exc:
        sys.modules.pop(spec.name, None)
        raise ModuleLoadError(f"error al importar module.py: {exc!r}") from exc

    module_class = getattr(py_module, "MODULE_CLASS", None)
    if module_class is None or not isinstance(module_class, type) \
            or not issubclass(module_class, PerceptaModule):
        raise ModuleLoadError("MODULE_CLASS ausente o no implementa PerceptaModule")

    return DiscoveredModule(
        module_key=manifest["moduleKey"],
        version=manifest["version"],
        manifest=manifest,
        module_class=module_class,
        path=module_dir,
    )


def discover(modules_path: str | Path) -> DiscoveryResult:
    """Escanea el directorio y devuelve módulos cargados y fallidos, sin propagar."""
    root = Path(modules_path)
    result = DiscoveryResult(loaded=[], failed=[])
    if not root.is_dir():
        return result

    for manifest_path in sorted(root.glob("*/module.json")):
        module_dir = manifest_path.parent
        try:
            result.loaded.append(_load_one(manifest_path))
        except ModuleLoadError as exc:
            result.failed.append(FailedModule(module_dir.name, str(exc), module_dir))
        except Exception as exc:  # defensivo: ningún plugin tumba el worker
            result.failed.append(
                FailedModule(module_dir.name, f"error inesperado: {exc!r}", module_dir)
            )
    return result


def discover_modules(modules_path: str | Path) -> list[DiscoveredModule]:
    """Compat: solo los módulos cargables."""
    return discover(modules_path).loaded
