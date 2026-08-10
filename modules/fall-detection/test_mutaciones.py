"""¿Atraparían las pruebas una regresión real? Se comprueba rompiendo el código.

  python modules/fall-detection/test_mutaciones.py

Una batería de pruebas que pasa siempre no protege nada. La única forma honesta
de saber si protege es introducir errores a propósito —cambiar un umbral,
invertir una comparación, borrar una guarda— y ver si alguna prueba se da cuenta.

Cada mutación que NADIE detecta es un agujero medido, no una opinión: ahí el
código puede romperse en producción sin que la batería diga nada.

No forma parte de la suite normal (es lenta y modifica archivos temporalmente).
Se corre cuando se toca la lógica del detector.
"""
from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).parent

# (descripción, texto original, texto mutado)
MUTACIONES = [
    (
        "no exigir que el cuerpo quede abajo para alertar",
        "if st.down_frames >= cfg.impactConfirmFrames and st.peak_velocity >= cfg.fallVelocity:",
        "if st.peak_velocity >= cfg.fallVelocity:",
    ),
    (
        "no exigir velocidad: cualquier cuerpo abajo alerta",
        "if st.down_frames >= cfg.impactConfirmFrames and st.peak_velocity >= cfg.fallVelocity:",
        "if st.down_frames >= cfg.impactConfirmFrames:",
    ),
    (
        "confirmar el impacto con un solo frame",
        "impactConfirmFrames: int = 2",
        "impactConfirmFrames: int = 1",
    ),
    (
        "bajar el umbral de velocidad a la mitad",
        "fallVelocity: float = 0.70",
        "fallVelocity: float = 0.35",
    ),
    (
        "que sentarse cuente como cuerpo abajo",
        "trunkDropRatio: float = 0.38",
        "trunkDropRatio: float = 0.15",
    ),
    (
        "que una bajada moderada alcance sola, sin postura horizontal",
        "trunkDropSure: float = 1.10",
        "trunkDropSure: float = 0.40",
    ),
    (
        "aceptar cualquier postura como horizontal",
        "downVerticality: float = 1.2",
        "downVerticality: float = 99.0",
    ),
    (
        "invertir la comparación de bajada de tronco",
        "if caida_tronco >= cfg.trunkDropSure:",
        "if caida_tronco <= cfg.trunkDropSure:",
    ),
    (
        "ignorar la calidad de la pose",
        "quality_ok = n_torso >= cfg.minTorsoPoints and referencia >= cfg.minPersonHeight",
        "quality_ok = True",
    ),
    (
        "no olvidar nunca a las personas que se fueron",
        "stale = [t for t, s in self.tracks.items() if now - s.last_ts > self.cfg.trackTimeoutSeconds]",
        "stale = []",
    ),
    (
        "aprender la referencia con la mediana en vez de la envolvente",
        "idx = min(int(len(ordenadas) * 0.9), len(ordenadas) - 1)",
        "idx = len(ordenadas) // 2",
    ),
    (
        "no congelar la referencia durante una caída ya alertada",
        "if quality_ok and st.state != State.ALERTED:",
        "if quality_ok:",
    ),
]


def correr_pruebas(carpeta: Path) -> bool:
    """True si TODAS las pruebas pasan en esa copia del módulo."""
    r = subprocess.run(
        [sys.executable, str(carpeta / "test_detector.py")],
        capture_output=True, text=True, cwd=str(carpeta), timeout=300,
    )
    return r.returncode == 0


def main() -> int:
    fuente = (AQUI / "detector.py").read_text(encoding="utf-8")

    print("Rompiendo el detector a propósito para ver si las pruebas se dan cuenta.\n")
    sobrevivientes = []

    for descripcion, original, mutado in MUTACIONES:
        if original not in fuente:
            print(f"  ?  {descripcion}\n     (el código cambió; esta mutación ya no aplica)")
            sobrevivientes.append((descripcion, "mutación obsoleta"))
            continue

        with tempfile.TemporaryDirectory() as tmp:
            copia = Path(tmp) / "mod"
            shutil.copytree(AQUI, copia, ignore=shutil.ignore_patterns("__pycache__"))
            (copia / "detector.py").write_text(fuente.replace(original, mutado, 1), encoding="utf-8")

            paso = correr_pruebas(copia)

        if paso:
            print(f"  SOBREVIVE  {descripcion}")
            sobrevivientes.append((descripcion, "ninguna prueba falló"))
        else:
            print(f"  detectada  {descripcion}")

    total = len(MUTACIONES)
    muertas = total - len(sobrevivientes)
    print(f"\n{muertas}/{total} mutaciones detectadas por las pruebas")

    if sobrevivientes:
        print("\nAGUJEROS: estos errores pasarían sin que ninguna prueba se queje")
        for d, motivo in sobrevivientes:
            print(f"  - {d}  ({motivo})")
        return 1

    print("Ninguna mutación sobrevivió: la batería cubre lo que decide.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
