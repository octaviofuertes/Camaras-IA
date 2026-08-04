"""Descarga el dataset público UR Fall Detection (URFD).

  python training/download.py            # todo (30 caídas + 40 actividades)
  python training/download.py --limit 10 # una muestra para probar el pipeline

Fuente: Universidad de Rzeszów, Polonia.
  https://fenix.ur.edu.pl/~mkepski/ds/uf.html

Contiene 30 secuencias de caídas y 40 de actividades cotidianas (caminar,
agacharse, sentarse, acostarse a propósito). Esas actividades son tan valiosas
como las caídas: son los NEGATIVOS que enseñan a no alertar de más.

Uso académico/investigación según los términos de sus autores. Si el sistema
se comercializa, revisar la licencia con ellos.
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

import requests

BASE = "https://fenix.ur.edu.pl/~mkepski/ds/data"
RAIZ = Path(__file__).parent / "data" / "urfd"

N_CAIDAS = 30
N_ACTIVIDADES = 40

# Etiquetas por frame publicadas por los autores:
#   columna 3 -> -1 = de pie/en movimiento, 0 = postura transitoria, 1 = en el suelo
CSVS = ["urfall-cam0-falls.csv", "urfall-cam0-adls.csv"]


def descargar(url: str, destino: Path) -> bool:
    """Descarga con reanudación: si el archivo ya está completo, no lo repite."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        cabeza = requests.head(url, timeout=30, allow_redirects=True)
        if cabeza.status_code != 200:
            print(f"    no disponible (HTTP {cabeza.status_code})")
            return False
        total = int(cabeza.headers.get("content-length", 0))
    except requests.RequestException as exc:
        print(f"    error al consultar: {exc}")
        return False

    if destino.exists() and total and destino.stat().st_size == total:
        print(f"    ya estaba ({total // 1024 // 1024} MB)")
        return True

    try:
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            bajado = 0
            ultimo_pct = -10
            with open(destino, "wb") as f:
                for trozo in r.iter_content(chunk_size=1 << 18):
                    f.write(trozo)
                    bajado += len(trozo)
                    # Informar cada 25% y no en cada trozo: si no, el log se
                    # vuelve ilegible y ocupa megabytes.
                    if total:
                        pct = bajado * 100 // total
                        if pct >= ultimo_pct + 25:
                            print(f"    {pct:3d}%  ({bajado // 1024 // 1024} MB)", flush=True)
                            ultimo_pct = pct
    except requests.RequestException as exc:
        print(f"\n    fallo la descarga: {exc}")
        destino.unlink(missing_ok=True)
        return False

    return True


def descomprimir(zip_path: Path, destino: Path) -> bool:
    if destino.exists() and any(destino.iterdir()):
        return True
    destino.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(destino)
        return True
    except zipfile.BadZipFile:
        print(f"    zip corrupto: {zip_path.name}")
        zip_path.unlink(missing_ok=True)
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Descarga el dataset URFD de caídas")
    ap.add_argument("--limit", type=int, default=0, help="cuántas secuencias de cada tipo (0 = todas)")
    ap.add_argument("--keep-zips", action="store_true", help="no borrar los .zip tras descomprimir")
    args = ap.parse_args()

    n_caidas = min(args.limit, N_CAIDAS) if args.limit else N_CAIDAS
    n_adl = min(args.limit, N_ACTIVIDADES) if args.limit else N_ACTIVIDADES

    RAIZ.mkdir(parents=True, exist_ok=True)

    print("Etiquetas por frame")
    for csv in CSVS:
        print(f"  {csv}")
        if not descargar(f"{BASE}/{csv}", RAIZ / csv):
            print("  ERROR: sin las etiquetas no se puede entrenar")
            return 1

    tareas = [("fall", i) for i in range(1, n_caidas + 1)] + [("adl", i) for i in range(1, n_adl + 1)]
    print(f"\nSecuencias de video: {n_caidas} caídas + {n_adl} actividades")

    ok = fallidas = 0
    for tipo, i in tareas:
        nombre = f"{tipo}-{i:02d}-cam0-rgb"
        carpeta = RAIZ / f"{tipo}-{i:02d}"
        if carpeta.exists() and any(carpeta.iterdir()):
            ok += 1
            continue

        print(f"  {nombre}")
        zip_path = RAIZ / f"{nombre}.zip"
        if descargar(f"{BASE}/{nombre}.zip", zip_path) and descomprimir(zip_path, carpeta):
            ok += 1
            if not args.keep_zips:
                zip_path.unlink(missing_ok=True)
        else:
            fallidas += 1

    print(f"\n{ok} secuencias listas en {RAIZ}")
    if fallidas:
        print(f"{fallidas} fallaron — se pueden reintentar corriendo el script de nuevo")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
