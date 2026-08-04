"""Representación de features de pose. COMPARTIDA entre entrenamiento e inferencia.

Este archivo lo importan tanto el script de entrenamiento como el módulo que
corre en producción. Es deliberado: si el entrenamiento calculara las features
de una forma y la inferencia de otra, el modelo funcionaría bien en las pruebas
y mal en la cámara — el error clásico de "train/serve skew", difícil de detectar
porque nada falla, sólo empeora la precisión.

Qué se le da al modelo, por frame:
  - los 17 puntos del esqueleto normalizados respecto de la caja de la persona,
    de modo que da igual si está cerca o lejos, a la izquierda o a la derecha;
  - la visibilidad de cada punto, para que el modelo sepa de cuáles fiarse;
  - unos pocos rasgos derivados que ya sabemos que importan (ángulo del torso,
    proporción de la caja), porque darle el trabajo hecho acelera el aprendizaje
    con pocos datos.
"""
from __future__ import annotations

import math

import numpy as np

# Longitud de la ventana temporal, en frames. A ~10-15 fps son 2-3 segundos:
# suficiente para contener el antes, el durante y el después de una caída.
WINDOW = 30

# 17 puntos × (x, y) + 17 visibilidades + 5 derivados
N_KEYPOINTS = 17
FEATURES_PER_FRAME = N_KEYPOINTS * 2 + N_KEYPOINTS + 5

L_SHOULDER, R_SHOULDER = 5, 6
L_HIP, R_HIP = 11, 12


def frame_features(
    keypoints: np.ndarray,
    bbox: tuple[float, float, float, float],
    prev_hip_y: float | None = None,
    dt: float = 0.1,
) -> np.ndarray:
    """Vector de features de UN frame.

    keypoints: array (17, 3) con x, y normalizados a la imagen (0..1) y score.
    bbox:      (x, y, w, h) normalizado a la imagen.
    prev_hip_y: cadera del frame anterior, para la velocidad. None en el primero.
    """
    x, y, w, h = bbox
    w = max(w, 1e-6)
    h = max(h, 1e-6)
    cx, cy = x + w / 2.0, y + h / 2.0
    # Escala común para x e y: sin esto, una caja alta y angosta deformaría la
    # figura y el mismo gesto se vería distinto según el encuadre.
    escala = max(math.hypot(w, h), 1e-6)

    coords = np.zeros(N_KEYPOINTS * 2, dtype=np.float32)
    vis = np.zeros(N_KEYPOINTS, dtype=np.float32)
    for i in range(min(N_KEYPOINTS, len(keypoints))):
        kx, ky, ks = float(keypoints[i][0]), float(keypoints[i][1]), float(keypoints[i][2])
        coords[i * 2] = (kx - cx) / escala
        coords[i * 2 + 1] = (ky - cy) / escala
        vis[i] = ks

    # ── rasgos derivados ────────────────────────────────────────────
    def punto_medio(a: int, b: int) -> tuple[float, float, float] | None:
        pa, pb = keypoints[a], keypoints[b]
        sa, sb = float(pa[2]), float(pb[2])
        if sa < 0.2 and sb < 0.2:
            return None
        if sa < 0.2:
            return float(pb[0]), float(pb[1]), sb
        if sb < 0.2:
            return float(pa[0]), float(pa[1]), sa
        return (float(pa[0]) + float(pb[0])) / 2, (float(pa[1]) + float(pb[1])) / 2, (sa + sb) / 2

    hombros = punto_medio(L_SHOULDER, R_SHOULDER)
    caderas = punto_medio(L_HIP, R_HIP)

    # Ángulo del torso como seno y coseno en lugar de grados: evita el salto
    # de 359° a 0° que confundiría al modelo.
    sin_a = cos_a = 0.0
    if hombros and caderas:
        dx = caderas[0] - hombros[0]
        dy = caderas[1] - hombros[1]
        norma = math.hypot(dx, dy)
        if norma > 1e-6:
            sin_a = dx / norma   # 0 de pie, ±1 tendido
            cos_a = dy / norma   # 1 de pie, 0 tendido

    proporcion = h / w                      # alto/ancho de la caja
    altura_norm = h                         # tamaño en la imagen: pista de distancia
    velocidad = 0.0
    if caderas and prev_hip_y is not None and dt > 1e-6:
        velocidad = ((caderas[1] - prev_hip_y) / max(h, 1e-6)) / dt

    derivados = np.array(
        [sin_a, cos_a, min(proporcion, 10.0), altura_norm, np.clip(velocidad, -20, 20)],
        dtype=np.float32,
    )

    return np.concatenate([coords, vis, derivados])


def hip_y(keypoints: np.ndarray) -> float | None:
    """Altura de la cadera, para calcular la velocidad entre frames."""
    ok = [i for i in (L_HIP, R_HIP) if float(keypoints[i][2]) >= 0.2]
    if not ok:
        return None
    return float(sum(float(keypoints[i][1]) for i in ok) / len(ok))


def build_sequence(
    frames: list[tuple[np.ndarray, tuple[float, float, float, float], float]],
) -> np.ndarray:
    """Convierte una lista de (keypoints, bbox, timestamp) en una matriz (T, F)."""
    salida = np.zeros((len(frames), FEATURES_PER_FRAME), dtype=np.float32)
    anterior = None
    t_anterior = None
    for i, (kps, bbox, ts) in enumerate(frames):
        dt = (ts - t_anterior) if t_anterior is not None else 0.1
        salida[i] = frame_features(kps, bbox, anterior, max(dt, 1e-3))
        anterior = hip_y(kps)
        t_anterior = ts
    return salida


def pad_or_trim(seq: np.ndarray, largo: int = WINDOW) -> np.ndarray:
    """Ajusta la secuencia a `largo` frames: recorta los viejos o repite el primero.

    Se conserva SIEMPRE el final de la secuencia porque ahí está el desenlace
    (la persona en el suelo); el principio es contexto.
    """
    if len(seq) >= largo:
        return seq[-largo:]
    faltan = largo - len(seq)
    relleno = np.repeat(seq[:1], faltan, axis=0) if len(seq) else np.zeros((faltan, FEATURES_PER_FRAME), np.float32)
    return np.concatenate([relleno, seq], axis=0)
