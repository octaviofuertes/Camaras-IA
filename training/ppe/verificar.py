"""Verifica que las etiquetas del dataset de EPP digan lo que dicen.

  python training/ppe/verificar.py

── Por qué existe este archivo ─────────────────────────────────────────────

Porque el primer dataset que probé estaba mal y no había forma de notarlo
leyendo su documentación. Era una exportación de SH17, el dataset más citado
del tema: traía las clases numeradas del 0 al 16 y la lista de nombres estaba
en el repositorio original. Todo parecía coincidir. Pero al medir DÓNDE caen
las cajas de cada clase, "cara-guardia" ocupaba el 25% de la imagen y los pies
aparecían en el tercio superior. Los nombres estaban corridos.

Entrenar con eso no falla: entrena perfecto y aprende lo que no es. El modelo
habría alertado por "casco faltante" mirando zapatos, y el error recién se ve
cuando el sistema ya está instalado y nadie entiende por qué avisa cualquier
cosa.

La verificación es simple y no necesita mirar una sola imagen: un casco está
arriba y es chico, un chaleco está en el medio y es grande, las botas están
abajo. Si una clase no cae donde va esa parte del cuerpo, las etiquetas están
cambiadas.
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

RAIZ = Path(__file__).parent / "data"

#: Qué se espera de cada clase: (altura mínima, altura máxima) del centro de la
#: caja, en fracción del alto de la imagen; y el área máxima razonable en %.
#: Los rangos son anchos a propósito — se busca detectar etiquetas CAMBIADAS,
#: no juzgar encuadres. Una foto de medio cuerpo corre todo hacia abajo.
ESPERADO: dict[str, tuple[float, float, float]] = {
    "Goggles":        (0.05, 0.45, 5.0),
    "NO-Goggles":     (0.05, 0.45, 5.0),
    "Hardhat":        (0.05, 0.50, 8.0),
    "NO-Hardhat":     (0.05, 0.50, 8.0),
    "Safety Vest":    (0.20, 0.70, 40.0),
    "NO-Safety Vest": (0.20, 0.70, 40.0),
    "Gloves":         (0.20, 0.85, 8.0),
    "NO-Gloves":      (0.20, 0.85, 8.0),
    "Boots":          (0.45, 1.00, 15.0),
    "Person":         (0.15, 0.90, 100.0),
}


def clases_del_yaml(yaml: Path) -> list[str]:
    nombres: list[str] = []
    for linea in yaml.read_text(encoding="utf-8").splitlines():
        s = linea.strip()
        if s and s[0].isdigit() and ":" in s:
            nombres.append(s.split(":", 1)[1].strip())
    return nombres


def medir(split: str, clases: list[str]) -> dict[str, list[tuple[float, float]]]:
    """Para cada clase: (área en %, altura del centro) de todas sus cajas."""
    por: dict[str, list[tuple[float, float]]] = {}
    carpeta = RAIZ / split / "labels"
    if not carpeta.is_dir():
        return por
    for f in carpeta.glob("*.txt"):
        for linea in f.read_text(encoding="utf-8").splitlines():
            p = linea.split()
            if len(p) < 5:
                continue
            try:
                c, _cx, cy, w, h = int(p[0]), *(float(v) for v in p[1:5])
            except ValueError:
                continue
            if c >= len(clases):
                print(f"  ! {f.name}: clase {c} fuera del rango (hay {len(clases)})")
                continue
            por.setdefault(clases[c], []).append((w * h * 100, cy))
    return por


def main() -> int:
    yaml = RAIZ / "epp.yaml"
    if not yaml.is_file():
        print("Falta el dataset. Corré primero: python training/ppe/descargar.py", file=sys.stderr)
        return 1

    clases = clases_del_yaml(yaml)
    print(f"{len(clases)} clases: {', '.join(clases)}\n")

    total: dict[str, list[tuple[float, float]]] = {}
    for split in ("train", "valid", "test"):
        for k, v in medir(split, clases).items():
            total.setdefault(k, []).extend(v)

    if not total:
        print("No hay etiquetas para verificar.", file=sys.stderr)
        return 1

    print(f"{'clase':<16} {'cajas':>7} {'área%':>8} {'altura':>7}   veredicto")
    problemas = 0
    for nombre in clases:
        cajas = total.get(nombre, [])
        if not cajas:
            print(f"{nombre:<16} {0:>7}                     (sin ejemplos)")
            continue
        area = st.median(c[0] for c in cajas)
        alto = st.median(c[1] for c in cajas)
        rango = ESPERADO.get(nombre)
        if rango is None:
            veredicto = "(sin regla)"
        elif not (rango[0] <= alto <= rango[1]):
            veredicto = f"MAL: se esperaba entre {rango[0]} y {rango[1]} de altura"
            problemas += 1
        elif area > rango[2]:
            veredicto = f"MAL: demasiado grande (máx {rango[2]}%)"
            problemas += 1
        else:
            veredicto = "ok"
        print(f"{nombre:<16} {len(cajas):>7} {area:>7.2f}% {alto:>6.2f}   {veredicto}")

    print()
    if problemas:
        print(f"{problemas} clase(s) fuera de lugar: NO entrenar con esto.", file=sys.stderr)
        return 1
    print("Las etiquetas caen donde corresponde. Se puede entrenar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
