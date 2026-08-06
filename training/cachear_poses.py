"""Extrae y guarda las poses del dataset público, una sola vez.

  python training/cachear_poses.py

Pasar YOLOv8-pose por las 70 secuencias tarda varios minutos. Guardando el
resultado, después se puede reevaluar la lógica del detector en segundos y
tantas veces como haga falta. Sin esto, cada cambio de un umbral costaría otra
corrida completa y en la práctica nadie mide: se termina ajustando a ojo.

Lo que se guarda por secuencia:
  - keypoints (n_frames, 17, 3) normalizados 0..1, con su score
  - bbox (n_frames, 4) normalizado
  - score de detección (n_frames,)
  - etiqueta por frame del dataset: -1 de pie, 0 transición, 1 en el suelo
  - timestamp de cada frame, derivado de los 30 fps originales
"""
from __future__ import annotations

import csv
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

DATOS = Path(__file__).parent / "data" / "urfd"
SALIDA = Path(__file__).parent / "data" / "poses_cache.npz"

FPS_ORIGINAL = 30.0
# Se guarda a 15 fps: suficiente para después remuestrear a los 6 fps a los que
# corre producción, y la mitad de inferencias que procesar todos los frames.
SALTO = 2


def cargar_etiquetas() -> dict[str, dict[int, int]]:
    etiquetas: dict[str, dict[int, int]] = defaultdict(dict)
    for archivo in ("urfall-cam0-falls.csv", "urfall-cam0-adls.csv"):
        ruta = DATOS / archivo
        if not ruta.is_file():
            print(f"  aviso: falta {archivo}, esa parte queda sin etiquetas por frame")
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


def persona_principal(resultado):
    """La persona más grande del frame: en este dataset hay un solo sujeto."""
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
    score = float(cajas[mejor].conf.item())

    kp = np.array(kps.data[mejor].tolist(), dtype=np.float32)  # (17, 3) en píxeles
    kp[:, 0] /= w_img
    kp[:, 1] /= h_img
    return kp, bbox, score


def main() -> int:
    from ultralytics import YOLO

    if not DATOS.is_dir():
        print(f"No está el dataset en {DATOS}. Corré primero training/download.py")
        return 1

    etiquetas = cargar_etiquetas()
    modelo = YOLO("yolov8n-pose.pt")
    modelo.to("cpu")

    carpetas = sorted(d for d in DATOS.iterdir() if d.is_dir())
    print(f"{len(carpetas)} secuencias a procesar\n")

    salida: dict[str, np.ndarray] = {}
    t0 = time.time()

    for n, carpeta in enumerate(carpetas, 1):
        nombre = carpeta.name
        imagenes = sorted(carpeta.rglob("*.png"))
        if not imagenes:
            print(f"  [{n}/{len(carpetas)}] {nombre}: sin imágenes, se omite")
            continue

        kps_all, bboxes, scores, etqs, tss = [], [], [], [], []
        etiquetas_sec = etiquetas.get(nombre, {})

        for idx in range(0, len(imagenes), SALTO):
            img = imagenes[idx]
            try:
                n_frame = int(img.stem.split("-")[-1])
            except ValueError:
                continue

            res = modelo.predict(str(img), imgsz=640, classes=[0], conf=0.15, verbose=False, device="cpu")
            if not res:
                continue
            datos = persona_principal(res[0])
            if datos is None:
                # Sin persona detectada se guarda un frame vacío en vez de
                # saltearlo: el hueco temporal es información — el detector real
                # también se queda sin ver a nadie en esos instantes.
                kps_all.append(np.zeros((17, 3), dtype=np.float32))
                bboxes.append(np.zeros(4, dtype=np.float32))
                scores.append(0.0)
            else:
                kp, bbox, score = datos
                kps_all.append(kp)
                bboxes.append(np.array(bbox, dtype=np.float32))
                scores.append(score)

            etqs.append(etiquetas_sec.get(n_frame, -99))
            tss.append(idx / FPS_ORIGINAL)

        if not kps_all:
            continue

        salida[f"{nombre}__kps"] = np.stack(kps_all)
        salida[f"{nombre}__bbox"] = np.stack(bboxes)
        salida[f"{nombre}__score"] = np.array(scores, dtype=np.float32)
        salida[f"{nombre}__etq"] = np.array(etqs, dtype=np.int16)
        salida[f"{nombre}__ts"] = np.array(tss, dtype=np.float32)

        transcurrido = time.time() - t0
        restante = transcurrido / n * (len(carpetas) - n)
        print(
            f"  [{n}/{len(carpetas)}] {nombre}: {len(kps_all)} frames"
            f"  (quedan ~{restante/60:.1f} min)"
        )

    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(SALIDA, **salida)
    secuencias = len({k.split('__')[0] for k in salida})
    print(f"\nguardado {SALIDA}  —  {secuencias} secuencias, {SALIDA.stat().st_size/1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
