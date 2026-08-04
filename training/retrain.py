"""Reentrena el modelo sumando el feedback de los operadores.

  python training/retrain.py

Toma las muestras que los operadores etiquetaron desde el dashboard —cada
"fue una caída real" o "falso positivo" es una etiqueta— y las combina con el
dataset público para entrenar una versión mejorada.

Por qué se combinan y no se usa sólo lo propio: las caídas reales son rarísimas.
En meses de operación podés juntar 3 caídas y 200 falsos positivos; entrenar
sólo con eso produciría un modelo que aprende a decir "nunca es una caída" y
acierta el 98% siendo inútil. El dataset público aporta los positivos; tus
datos aportan los negativos de TU entorno, que son los que causan las falsas
alarmas que te molestan.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).parent
BASE = RAIZ / "data" / "sequences.npz"
CAMPO = RAIZ / "data" / "feedback.npz"
COMBINADO = RAIZ / "data" / "sequences_combined.npz"


def exportar_feedback() -> tuple[int, int]:
    """Trae del dashboard las muestras ya etiquetadas por operadores."""
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        print("Falta psycopg2: pip install psycopg2-binary")
        return 0, 0

    import psycopg2

    dsn = os.environ.get("DATABASE_URL_ADMIN") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("Falta DATABASE_URL en el entorno")
        return 0, 0

    with psycopg2.connect(dsn) as con, con.cursor() as cur:
        cur.execute(
            """SELECT sequence, label, camera_id
                 FROM fall_training_samples
                WHERE label IS NOT NULL
                ORDER BY created_at"""
        )
        filas = cur.fetchall()

    if not filas:
        print("Todavía no hay muestras etiquetadas por operadores.")
        return 0, 0

    X, y, grupos = [], [], []
    for secuencia, label, camera_id in filas:
        arr = np.array(secuencia, dtype=np.float32)
        if arr.ndim != 2:
            continue
        X.append(arr)
        y.append(int(label))
        # Se agrupa por cámara: así el reparto train/test no mezcla la misma
        # cámara de los dos lados y las métricas siguen siendo honestas.
        grupos.append(f"campo-{camera_id}")

    if not X:
        return 0, 0

    # Todas las secuencias deben tener la misma forma para apilarse.
    largo = max(len(a) for a in X)
    n_feat = X[0].shape[1]
    Xp = np.zeros((len(X), largo, n_feat), dtype=np.float32)
    for i, a in enumerate(X):
        Xp[i, -len(a):] = a[-largo:]

    CAMPO.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(CAMPO, X=Xp, y=np.array(y, dtype=np.int64), groups=np.array(grupos))
    return int(sum(y)), int(len(y) - sum(y))


def combinar() -> bool:
    if not BASE.is_file():
        print(f"Falta {BASE}. Corré primero: python training/extract.py")
        return False
    base = np.load(BASE, allow_pickle=True)
    Xs, ys, gs = [base["X"]], [base["y"]], [base["groups"]]

    if CAMPO.is_file():
        campo = np.load(CAMPO, allow_pickle=True)
        Xc = campo["X"]
        # Ajustar la ventana temporal si difiere de la del dataset base.
        if Xc.shape[1] != base["X"].shape[1]:
            t = base["X"].shape[1]
            Xc = Xc[:, -t:] if Xc.shape[1] > t else np.pad(Xc, ((0, 0), (t - Xc.shape[1], 0), (0, 0)))
        if Xc.shape[2] == base["X"].shape[2]:
            # Se repiten las muestras de campo para que pesen más: son pocas
            # pero describen el entorno real donde el modelo va a trabajar.
            for _ in range(3):
                Xs.append(Xc)
                ys.append(campo["y"])
                gs.append(campo["groups"])
        else:
            print("  las features de campo no coinciden con las del dataset: se omiten")

    np.savez_compressed(
        COMBINADO,
        X=np.concatenate(Xs), y=np.concatenate(ys), groups=np.concatenate(gs),
    )
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Reentrena sumando el feedback de operadores")
    ap.add_argument("--epochs", type=int, default=60)
    args = ap.parse_args()

    print("Exportando el feedback de los operadores…")
    caidas, falsas = exportar_feedback()
    print(f"  {caidas} caídas confirmadas, {falsas} falsos positivos\n")

    if not combinar():
        return 1
    print(f"Dataset combinado en {COMBINADO}\n")

    # Se reutiliza el mismo entrenamiento, apuntándolo al dataset combinado.
    entorno = dict(os.environ, PERCEPTA_DATASET=str(COMBINADO))
    return subprocess.call(
        [sys.executable, str(RAIZ / "train.py"), "--epochs", str(args.epochs)], env=entorno
    )


if __name__ == "__main__":
    sys.exit(main())
