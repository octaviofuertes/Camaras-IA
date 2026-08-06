"""¿Qué tan bien separan las señales una caída de una actividad cotidiana?

  python training/separacion_senales.py

No busca detectar nada: recorre el dataset midiendo las señales crudas y muestra
cómo se distribuyen en caídas frente a actividades normales. Es el paso previo a
elegir cualquier umbral — sin esto, un umbral es una corazonada con decimales.

La pregunta concreta que responde: al momento en que el cuerpo está más abajo,
¿a qué velocidad llegó ahí? Acostarse a propósito y caerse terminan en la misma
postura; lo único que los separa es cómo se llegó.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).parent.parent
sys.path.insert(0, str(RAIZ / "modules" / "fall-detection"))
from detector import (  # noqa: E402
    FallConfig, Keypoint, altura_del_tronco, person_height, torso_angle_deg, verticalidad,
)

CACHE = Path(__file__).parent / "data" / "poses_cache.npz"
FPS = 6.0


def remuestrear(ts, fps):
    paso, elegidos, proximo = 1.0 / fps, [], ts[0]
    for i, t in enumerate(ts):
        if t >= proximo - 1e-6:
            elegidos.append(i)
            proximo = t + paso
    return elegidos


def medir(datos, nombre, cfg):
    """Señales de la secuencia en su momento de máxima bajada de tronco."""
    kps_all = datos[f"{nombre}__kps"]
    bbox_all = datos[f"{nombre}__bbox"]
    score = datos[f"{nombre}__score"]
    ts_all = datos[f"{nombre}__ts"]

    idxs = [i for i in remuestrear(ts_all, FPS) if score[i] > 0]
    if len(idxs) < 4:
        return None

    serie = []
    for i in idxs:
        pts = [Keypoint(float(p[0]), float(p[1]), float(p[2])) for p in kps_all[i]]
        y_t = altura_del_tronco(pts, cfg.keypointScore)
        if y_t is None:
            continue
        serie.append(
            {
                "ts": float(ts_all[i]),
                "y_tronco": y_t,
                "altura": person_height(pts, tuple(float(v) for v in bbox_all[i]), cfg.keypointScore),
                "vert": verticalidad(pts, cfg.keypointScore),
                "angulo": torso_angle_deg(pts, cfg.keypointScore),
            }
        )
    if len(serie) < 4:
        return None

    # Referencias: envolvente superior sobre TODA la secuencia. Acá se puede
    # mirar el futuro porque es un análisis, no el detector en vivo.
    alturas = sorted(s["altura"] for s in serie)
    estatura = alturas[min(int(len(alturas) * 0.9), len(alturas) - 1)]
    ys = sorted(s["y_tronco"] for s in serie)
    y_de_pie = ys[min(int(len(ys) * 0.1), len(ys) - 1)]

    for s in serie:
        s["caida_tronco"] = (s["y_tronco"] - y_de_pie) / max(estatura, 1e-6)

    # El instante de máxima bajada, y la velocidad con la que se llegó.
    peor = max(serie, key=lambda s: s["caida_tronco"])
    vel = 0.0
    for s in serie:
        dt = peor["ts"] - s["ts"]
        if 0.05 < dt <= 1.2:
            vel = max(vel, ((peor["y_tronco"] - s["y_tronco"]) / max(estatura, 1e-6)) / dt)

    return {
        "caida_tronco": peor["caida_tronco"],
        "velocidad": vel,
        "vert": peor["vert"],
        "angulo": peor["angulo"],
        "frames": len(serie),
    }


def resumen(nombre, valores):
    if not valores:
        print(f"  {nombre:14s} (sin datos)")
        return
    v = sorted(valores)
    def p(q):
        return v[min(int(len(v) * q), len(v) - 1)]
    print(f"  {nombre:14s} min {v[0]:5.2f}   p25 {p(.25):5.2f}   mediana {p(.5):5.2f}   p75 {p(.75):5.2f}   max {v[-1]:5.2f}")


def main():
    datos = np.load(CACHE)
    secuencias = sorted({k.split("__")[0] for k in datos.files})
    cfg = FallConfig()

    med = {}
    for s in secuencias:
        m = medir(datos, s, cfg)
        if m:
            med[s] = m

    caidas = {k: v for k, v in med.items() if k.startswith("fall")}
    adls = {k: v for k, v in med.items() if k.startswith("adl")}
    print(f"{len(caidas)} caídas, {len(adls)} actividades cotidianas\n")

    for campo, etiqueta in (
        ("caida_tronco", "BAJADA DE TRONCO (en estaturas)"),
        ("velocidad", "VELOCIDAD al llegar abajo"),
        ("vert", "VERTICALIDAD (alto/ancho)"),
        ("frames", "FRAMES observados a 6 fps"),
    ):
        print(f"{etiqueta}")
        resumen("caídas", [v[campo] for v in caidas.values() if v[campo] is not None])
        resumen("cotidianas", [v[campo] for v in adls.values() if v[campo] is not None])
        print()

    # Cuánto separa cada señal por sí sola: mejor umbral posible y su acierto.
    print("SEPARACIÓN DE CADA SEÑAL POR SÍ SOLA")
    for campo in ("caida_tronco", "velocidad"):
        pos = [v[campo] for v in caidas.values() if v[campo] is not None]
        neg = [v[campo] for v in adls.values() if v[campo] is not None]
        mejor_u, mejor_acc = None, 0.0
        for u in np.arange(0.0, 3.0, 0.01):
            acc = (sum(1 for x in pos if x >= u) + sum(1 for x in neg if x < u)) / (len(pos) + len(neg))
            if acc > mejor_acc:
                mejor_u, mejor_acc = u, acc
        vp = sum(1 for x in pos if x >= mejor_u)
        fp = sum(1 for x in neg if x >= mejor_u)
        print(f"  {campo:14s} umbral {mejor_u:.2f} -> acierto {mejor_acc*100:.0f}%"
              f"  (detecta {vp}/{len(pos)}, falsas {fp}/{len(neg)})")

    print("\nCotidianas con la mayor bajada de tronco (las que más se parecen a una caída):")
    for k, v in sorted(adls.items(), key=lambda kv: -kv[1]["caida_tronco"])[:8]:
        print(f"  {k}: tronco {v['caida_tronco']:.2f}  vel {v['velocidad']:.2f}  vert {v['vert'] or 0:.1f}")

    print("\nCaídas con la MENOR bajada de tronco (las más difíciles):")
    for k, v in sorted(caidas.items(), key=lambda kv: kv[1]["caida_tronco"])[:8]:
        print(f"  {k}: tronco {v['caida_tronco']:.2f}  vel {v['velocidad']:.2f}  vert {v['vert'] or 0:.1f}  frames {v['frames']}")


if __name__ == "__main__":
    main()
