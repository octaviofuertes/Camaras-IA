"""Convierte los videos del dataset en secuencias de esqueletos etiquetadas.

  python training/extract.py

Pasa YOLOv8-pose por cada frame, se queda con la persona principal y arma un
tensor (ventanas, WINDOW, features) junto a su etiqueta.

Dos decisiones que definen la calidad del entrenamiento:

1. QUÉ ES POSITIVO. Una ventana es "caída" sólo si viene de una secuencia de
   caída Y termina con la persona en el suelo. Las ventanas de esa misma
   secuencia anteriores al golpe son NEGATIVAS: la persona todavía caminaba.

2. ACOSTARSE NO ES CAERSE. Las secuencias de actividades cotidianas incluyen
   gente que se acuesta o se sienta en el piso a propósito. Esas ventanas son
   NEGATIVAS aunque la persona termine en el suelo. Es justamente lo que hace
   difícil el problema, y lo que enseña al modelo a mirar CÓMO llegó ahí.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

RAIZ_PROYECTO = Path(__file__).parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO / "modules" / "fall-detection"))
from features import FEATURES_PER_FRAME, WINDOW, build_sequence, pad_or_trim  # noqa: E402

DATOS = Path(__file__).parent / "data" / "urfd"
SALIDA = Path(__file__).parent / "data" / "sequences.npz"

# El dataset se grabó a 30 fps. Se toma 1 de cada 3 frames para trabajar a
# ~10 fps, que es el ritmo al que corren nuestras cámaras: entrenar a un ritmo
# y ejecutar a otro cambiaría todas las velocidades.
SALTO = 3
FPS_EFECTIVO = 30.0 / SALTO


def cargar_etiquetas() -> dict[str, dict[int, int]]:
    """{secuencia: {n_frame: etiqueta}} con -1 de pie, 0 transición, 1 en el suelo."""
    etiquetas: dict[str, dict[int, int]] = defaultdict(dict)
    for archivo in ("urfall-cam0-falls.csv", "urfall-cam0-adls.csv"):
        ruta = DATOS / archivo
        if not ruta.is_file():
            continue
        with open(ruta, newline="", encoding="utf-8") as f:
            for fila in csv.reader(f):
                if len(fila) < 3:
                    continue
                try:
                    etiquetas[fila[0]][int(fila[1])] = int(fila[2])
                except ValueError:
                    continue
    return etiquetas


def persona_principal(resultado) -> tuple[np.ndarray, tuple[float, float, float, float]] | None:
    """La persona más grande del frame: en este dataset sólo hay un sujeto."""
    cajas = getattr(resultado, "boxes", None)
    kps = getattr(resultado, "keypoints", None)
    if cajas is None or kps is None or kps.data is None or len(cajas) == 0:
        return None

    mejor, mejor_area = -1, 0.0
    for i, b in enumerate(cajas):
        x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
        area = (x2 - x1) * (y2 - y1)
        if area > mejor_area:
            mejor, mejor_area = i, area
    if mejor < 0 or mejor >= len(kps.data):
        return None

    x1, y1, x2, y2 = (float(v) for v in cajas[mejor].xyxy[0].tolist())
    h_img, w_img = resultado.orig_shape
    bbox = (x1 / w_img, y1 / h_img, (x2 - x1) / w_img, (y2 - y1) / h_img)

    kp = np.array(kps.data[mejor].tolist(), dtype=np.float32)  # (17, 3) en píxeles
    kp[:, 0] /= w_img
    kp[:, 1] /= h_img
    return kp, bbox


def procesar_secuencia(modelo, carpeta: Path, etiquetas_sec: dict[int, int]):
    """Devuelve (frames_procesados, etiquetas_alineadas) de una secuencia."""
    imagenes = sorted(carpeta.rglob("*.png"))
    if not imagenes:
        return [], []

    frames, etqs = [], []
    for idx in range(0, len(imagenes), SALTO):
        img = imagenes[idx]
        # El nombre termina en -NNN.png y ese número es el que usa el CSV.
        try:
            n_frame = int(img.stem.split("-")[-1])
        except ValueError:
            continue
        if n_frame not in etiquetas_sec:
            continue

        res = modelo.predict(str(img), imgsz=640, classes=[0], conf=0.25, verbose=False, device="cpu")
        if not res:
            continue
        datos = persona_principal(res[0])
        if datos is None:
            continue  # sin persona visible no hay nada que aprender de este frame

        kp, bbox = datos
        frames.append((kp, bbox, len(frames) / FPS_EFECTIVO))
        etqs.append(etiquetas_sec[n_frame])

    return frames, etqs


# Paso entre ventanas. Con 2 se obtienen bastantes más ejemplos que con 5, y no
# hay riesgo de inflar las métricas porque el reparto train/test es por
# SECUENCIA: las ventanas parecidas caen siempre del mismo lado.
PASO = 2


def ventanas_de(frames, etqs, es_caida: bool):
    """Corta la secuencia en ventanas solapadas y decide la etiqueta de cada una."""
    X, y = [], []
    if len(frames) < 8:
        return X, y  # demasiado corta para tener siquiera contexto

    secuencia = build_sequence(frames)

    # Secuencias más cortas que la ventana: se rellenan en lugar de tirarlas.
    # Sin esto se perdían 17 de las 30 caídas del dataset, porque muchas duran
    # menos de 90 frames y al muestrear 1 de cada 3 quedaban por debajo de 30.
    if len(secuencia) < WINDOW:
        X.append(pad_or_trim(secuencia))
        y.append(1 if (es_caida and etqs[-1] == 1) else 0)
        return X, y

    for fin in range(WINDOW, len(secuencia) + 1, PASO):
        ventana = secuencia[fin - WINDOW : fin]
        etq_final = etqs[fin - 1]
        # Positivo sólo si es una secuencia de caída Y la ventana termina en el
        # suelo. En actividades cotidianas, terminar en el suelo es acostarse.
        X.append(ventana)
        y.append(1 if (es_caida and etq_final == 1) else 0)
    return X, y


def main() -> int:
    ap = argparse.ArgumentParser(description="Extrae secuencias de esqueletos del dataset")
    ap.add_argument("--limit", type=int, default=0, help="máximo de secuencias a procesar")
    args = ap.parse_args()

    from ultralytics import YOLO

    if not DATOS.is_dir():
        print(f"No encuentro {DATOS}. Corré primero: python training/download.py")
        return 1

    etiquetas = cargar_etiquetas()
    if not etiquetas:
        print("No hay etiquetas. Falta descargar los CSV.")
        return 1

    carpetas = sorted([d for d in DATOS.iterdir() if d.is_dir()])
    if args.limit:
        caidas = [d for d in carpetas if d.name.startswith("fall")][: args.limit]
        adls = [d for d in carpetas if d.name.startswith("adl")][: args.limit]
        carpetas = caidas + adls

    print(f"Cargando YOLOv8-pose y procesando {len(carpetas)} secuencias…\n")
    modelo = YOLO("yolov8n-pose.pt")

    todas_X, todas_y, grupos = [], [], []
    for i, carpeta in enumerate(carpetas, 1):
        nombre = carpeta.name
        if nombre not in etiquetas:
            print(f"  [{i}/{len(carpetas)}] {nombre}: sin etiquetas, se omite")
            continue

        frames, etqs = procesar_secuencia(modelo, carpeta, etiquetas[nombre])
        X, y = ventanas_de(frames, etqs, es_caida=nombre.startswith("fall"))
        todas_X.extend(X)
        todas_y.extend(y)
        # El grupo es la SECUENCIA: se usa para separar train/test sin que
        # frames de la misma caída caigan de los dos lados.
        grupos.extend([nombre] * len(X))
        print(f"  [{i}/{len(carpetas)}] {nombre}: {len(frames)} frames -> {len(X)} ventanas ({sum(y)} positivas)")

    if not todas_X:
        print("\nNo se extrajo ninguna ventana.")
        return 1

    X = np.stack(todas_X).astype(np.float32)
    y = np.array(todas_y, dtype=np.int64)
    g = np.array(grupos)

    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(SALIDA, X=X, y=y, groups=g)

    print(f"\n{len(X)} ventanas de {WINDOW} frames × {FEATURES_PER_FRAME} features")
    print(f"  caídas:     {int(y.sum())}")
    print(f"  no caídas:  {int((y == 0).sum())}")
    print(f"  guardado en {SALIDA}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
