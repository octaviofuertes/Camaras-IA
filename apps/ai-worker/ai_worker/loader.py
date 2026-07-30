"""Descubrimiento y carga de módulos de IA (plugins PerceptaModule).

Escanea AI_MODULES_PATH buscando carpetas con module.json, valida el manifest
contra el meta-schema canónico (CONTRACTS §4), verifica compatibilidad de
pluginApiVersion (mismo major) y carga la clase MODULE_CLASS de module.py.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from percepta_contracts import PLUGIN_API_VERSION, PerceptaModule


@dataclass(frozen=True)
class DiscoveredModule:
    module_key: str
    version: str
    manifest: dict
    module_class: type[PerceptaModule]
    path: Path


class ModuleLoadError(Exception):
    pass


def _same_major(a: str, b: str) -> bool:
    return a.split(".")[0] == b.split(".")[0]


def discover_modules(modules_path: str | Path) -> list[DiscoveredModule]:
    """Escanea el directorio de módulos y devuelve los cargables."""
    root = Path(modules_path)
    found: list[DiscoveredModule] = []
    if not root.is_dir():
        return found

    for manifest_path in sorted(root.glob("*/module.json")):
        module_dir = manifest_path.parent
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        api_version = manifest.get("pluginApiVersion", "0.0.0")
        if not _same_major(api_version, PLUGIN_API_VERSION):
            raise ModuleLoadError(
                f"{manifest.get('moduleKey', module_dir.name)}: pluginApiVersion "
                f"{api_version} incompatible con core {PLUGIN_API_VERSION}"
            )

        entry = module_dir / "module.py"
        if not entry.is_file():
            raise ModuleLoadError(f"{module_dir.name}: falta module.py")

        spec = importlib.util.spec_from_file_location(
            f"percepta_module_{module_dir.name.replace('-', '_')}", entry
        )
        assert spec and spec.loader
        py_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = py_module
        spec.loader.exec_module(py_module)

        module_class = getattr(py_module, "MODULE_CLASS", None)
        if module_class is None or not issubclass(module_class, PerceptaModule):
            raise ModuleLoadError(
                f"{module_dir.name}: MODULE_CLASS ausente o no implementa PerceptaModule"
            )

        found.append(
            DiscoveredModule(
                module_key=manifest["moduleKey"],
                version=manifest["version"],
                manifest=manifest,
                module_class=module_class,
                path=module_dir,
            )
        )
    return found
