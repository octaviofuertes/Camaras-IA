"""Mide el detector de caídas contra el dataset público. Sin adivinar.

  python training/evaluar_detector.py
  python training/evaluar_detector.py --fps 6 --detalle

Reproduce las poses cacheadas por `cachear_poses.py` a través del detector real
y reporta dos números que importan de verdad:

  RECALL      — de 30 caídas reales, cuántas se detectan.
  PRECISIÓN   — de 40 secuencias de actividad cotidiana (sentarse, agacharse,
                acostarse a propósito), en cuántas alerta cuando no debería.

Las secuencias `adl-*` son la parte valiosa: contienen exactamente los casos que
en producción generan falsas alarmas. Una versión del detector que suba el
recall a costa de alertar cuando alguien se sienta no es mejor, es peor: un
sistema que grita seguido se ignora, y entonces no detecta nada.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).parent.parent
sys.path.insert(0, str(RAIZ / "modules" / "fall-detection"))
from detector import FallConfig, FallDetector, Keypoint, PoseFrame  # noqa: E402

CACHE = Path(__file__).parent / "data" / "poses_cache.npz"


def cargar():
    if not CACHE.is_file():
        print(f"No está {CACHE}. Corré primero:  python training/cachear_poses.py")
        raise SystemExit(1)
    datos = np.load(CACHE)
    secuencias = sorted({k.split("__")[0] for k in datos.files})
    return datos, secuencias


def remuestrear(ts: np.ndarray, fps: float) -> list[int]:
    """Índices que aproximan una captura a `fps`, como haría la cámara real."""
    if len(ts) == 0:
        return []
    paso = 1.0 / fps
    elegidos, proximo = [], ts[0]
    for i, t in enumerate(ts):
        if t >= proximo - 1e-6:
            elegidos.append(i)
            proximo = t + paso
    return elegidos


def correr_secuencia(datos, nombre: str, cfg: FallConfig, fps: float):
    """Devuelve (alertas, frames_totales, etiquetas) de una secuencia."""
    kps = datos[f"{nombre}__kps"]
    bbox = datos[f"{nombre}__bbox"]
    score = datos[f"{nombre}__score"]
    etq = datos[f"{nombre}__etq"]
    ts = datos[f"{nombre}__ts"]

    det = FallDetector(cfg)
    alertas = []
    idxs = remuestrear(ts, fps)

    for i in idxs:
        if score[i] <= 0.0:
            continue  # no se detectó a nadie en ese frame
        puntos = [Keypoint(x=float(p[0]), y=float(p[1]), score=float(p[2])) for p in kps[i]]
        res = det.update(
            PoseFrame(
                track_id=1,
                ts=float(ts[i]),
                keypoints=puntos,
                bbox=tuple(float(v) for v in bbox[i]),
                det_score=float(score[i]),
            )
        )
        if res.is_fall:
            alertas.append(
                {
                    "ts": float(ts[i]),
                    "etiqueta": int(etq[i]),
                    "confianza": res.confidence,
                    "motivo": res.reason,
                    "colapso": res.collapse_ratio,
                    "velocidad": res.velocity,
                }
            )

    return alertas, len(idxs), etq


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fps", type=float, default=6.0, help="ritmo al que corre el pipeline")
    ap.add_argument("--detalle", action="store_true", help="listar cada secuencia")
    args = ap.parse_args()

    datos, secuencias = cargar()
    cfg = FallConfig()

    caidas = [s for s in secuencias if s.startswith("fall")]
    adls = [s for s in secuencias if s.startswith("adl")]

    print(f"Evaluando a {args.fps} fps  —  {len(caidas)} caídas, {len(adls)} actividades cotidianas\n")

    detectadas, perdidas = [], []
    for s in caidas:
        alertas, n, _ = correr_secuencia(datos, s, cfg, args.fps)
        (detectadas if alertas else perdidas).append((s, alertas, n))

    limpias, falsas = [], []
    for s in adls:
        alertas, n, _ = correr_secuencia(datos, s, cfg, args.fps)
        (falsas if alertas else limpias).append((s, alertas, n))

    recall = len(detectadas) / max(len(caidas), 1)
    tasa_falsas = len(falsas) / max(len(adls), 1)
    # Precisión a nivel secuencia: de todas las que alertaron, cuántas eran caída.
    precision = len(detectadas) / max(len(detectadas) + len(falsas), 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-9)

    print("=" * 66)
    print(f"  CAÍDAS DETECTADAS   {len(detectadas):>2}/{len(caidas):<2}   recall    {recall*100:5.1f}%")
    print(f"  FALSAS ALARMAS      {len(falsas):>2}/{len(adls):<2}   precisión {precision*100:5.1f}%")
    print(f"  F1                                    {f1*100:5.1f}%")
    print("=" * 66)

    if perdidas:
        print(f"\nCaídas NO detectadas ({len(perdidas)}):")
        for s, _, n in perdidas:
            print(f"  {s}  ({n} frames)")

    if falsas:
        print(f"\nFalsas alarmas ({len(falsas)}):")
        for s, alertas, _ in falsas:
            a = alertas[0]
            col = f"{a['colapso']:.2f}" if a["colapso"] is not None else "?"
            print(f"  {s}: {a['motivo']}  [altura={col} vel={a['velocidad']:.2f} conf={a['confianza']:.2f}]")

    if args.detalle:
        print("\nDetalle de las caídas detectadas:")
        for s, alertas, _ in detectadas:
            a = alertas[0]
            col = f"{a['colapso']:.2f}" if a["colapso"] is not None else "?"
            print(f"  {s}: {a['motivo']}  [altura={col} vel={a['velocidad']:.2f}]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
