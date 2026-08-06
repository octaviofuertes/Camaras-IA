"""Busca los umbrales del detector sobre el dataset público.

  python training/buscar_umbrales.py
  python training/buscar_umbrales.py --beta 0.5

Prueba combinaciones de parámetros y reporta las mejores. Existe porque elegir
umbrales a ojo es cómo se llega a un detector que anda "más o menos": cada
número parece razonable por separado y el conjunto no se mide nunca.

BETA controla qué error duele más:
  beta > 1  prioriza no perder caídas (recall)
  beta = 1  las trata igual
  beta < 1  prioriza no molestar con falsas alarmas (precisión)

El valor por omisión es 0.7, algo inclinado hacia la precisión, y la razón es
operativa: un detector que alerta cuando alguien se sienta se desactiva a la
semana, y uno desactivado tiene recall cero. Pero la elección es del que opera
el sistema, no del que lo programa, así que el reporte muestra la curva entera.
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).parent.parent
sys.path.insert(0, str(RAIZ / "modules" / "fall-detection"))
from detector import FallConfig, FallDetector, Keypoint, PoseFrame  # noqa: E402

CACHE = Path(__file__).parent / "data" / "poses_cache.npz"


def remuestrear(ts, fps):
    paso, out, prox = 1.0 / fps, [], ts[0]
    for i, t in enumerate(ts):
        if t >= prox - 1e-6:
            out.append(i)
            prox = t + paso
    return out


def preparar(datos, secuencias, fps):
    """Precalcula los PoseFrame de cada secuencia: se reusan en cada combinación."""
    listo = {}
    for s in secuencias:
        kps, bbox = datos[f"{s}__kps"], datos[f"{s}__bbox"]
        sc, ts = datos[f"{s}__score"], datos[f"{s}__ts"]
        frames = []
        for i in remuestrear(ts, fps):
            if sc[i] <= 0:
                continue
            frames.append(
                PoseFrame(
                    track_id=1,
                    ts=float(ts[i]),
                    keypoints=[Keypoint(float(p[0]), float(p[1]), float(p[2])) for p in kps[i]],
                    bbox=tuple(float(v) for v in bbox[i]),
                    det_score=float(sc[i]),
                )
            )
        listo[s] = frames
    return listo


def alerta(frames, cfg) -> bool:
    det = FallDetector(cfg)
    for f in frames:
        if det.update(f).is_fall:
            return True
    return False


def evaluar(preparados, caidas, adls, cfg):
    vp = sum(1 for s in caidas if alerta(preparados[s], cfg))
    fp = sum(1 for s in adls if alerta(preparados[s], cfg))
    recall = vp / max(len(caidas), 1)
    precision = vp / max(vp + fp, 1)
    return vp, fp, recall, precision


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fps", type=float, default=6.0)
    ap.add_argument("--beta", type=float, default=0.7)
    args = ap.parse_args()

    datos = np.load(CACHE)
    secuencias = sorted({k.split("__")[0] for k in datos.files})
    caidas = [s for s in secuencias if s.startswith("fall")]
    adls = [s for s in secuencias if s.startswith("adl")]

    print(f"Preparando {len(secuencias)} secuencias a {args.fps} fps…")
    preparados = preparar(datos, secuencias, args.fps)

    rejilla = {
        "fallVelocity": [0.55, 0.7, 0.85, 1.0, 1.2],
        "trunkDropRatio": [0.38, 0.5, 0.6],
        "trunkDropSure": [0.6, 0.75, 0.9, 1.1],
        "downVerticality": [1.0, 1.4, 2.0],
        "impactConfirmFrames": [2, 3],
    }
    combos = list(itertools.product(*rejilla.values()))
    print(f"{len(combos)} combinaciones\n")

    b2 = args.beta ** 2
    resultados = []
    for n, valores in enumerate(combos, 1):
        cfg = FallConfig()
        for campo, v in zip(rejilla.keys(), valores):
            setattr(cfg, campo, v)
        vp, fp, rec, pre = evaluar(preparados, caidas, adls, cfg)
        f = (1 + b2) * pre * rec / max(b2 * pre + rec, 1e-9)
        resultados.append((f, rec, pre, vp, fp, dict(zip(rejilla.keys(), valores))))
        if n % 30 == 0:
            print(f"  {n}/{len(combos)}…")

    resultados.sort(key=lambda r: -r[0])
    print(f"\nMEJORES COMBINACIONES (F-beta con beta={args.beta})")
    print(f"{'F':>6} {'recall':>7} {'prec':>7} {'det':>6} {'falsas':>7}  parámetros")
    for f, rec, pre, vp, fp, params in resultados[:12]:
        ps = " ".join(f"{k}={v}" for k, v in params.items())
        print(f"{f*100:6.1f} {rec*100:6.1f}% {pre*100:6.1f}% {vp:>3}/30 {fp:>4}/40  {ps}")

    print("\nCURVA: mejor precisión alcanzable para cada nivel de recall")
    for objetivo in (0.9, 0.85, 0.8, 0.75, 0.7, 0.6):
        cands = [r for r in resultados if r[1] >= objetivo]
        if not cands:
            continue
        mejor = max(cands, key=lambda r: r[2])
        ps = " ".join(f"{k}={v}" for k, v in mejor[5].items())
        print(f"  recall >= {objetivo*100:4.0f}%  ->  precisión {mejor[2]*100:5.1f}%  "
              f"({mejor[3]}/30 detectadas, {mejor[4]}/40 falsas)   {ps}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
