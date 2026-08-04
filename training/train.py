"""Entrena el clasificador de caídas sobre secuencias de esqueletos.

  python training/train.py

Modelo: GRU de 2 capas sobre la ventana temporal. Se eligió por encima de un
transformer o un ST-GCN por tres razones prácticas: entrena bien con pocos
miles de ejemplos, corre rápido en CPU (que es donde vive el worker) y ocupa
menos de 1 MB.

Dos cuidados que separan una métrica honesta de una inflada:

1. SEPARACIÓN POR SECUENCIA. Las ventanas de una misma caída se solapan y son
   casi idénticas. Si se repartieran al azar, habría ventanas casi iguales en
   entrenamiento y en prueba, y la precisión daría ~99% siendo mentira. Acá el
   corte es por secuencia completa: una caída está entera de un solo lado.

2. DESBALANCE. Hay muchas más ventanas normales que de caída. Se compensa el
   peso de la clase positiva en la función de pérdida, y se reportan precisión
   y exhaustividad —no la exactitud, que con 90% de negativos siempre miente.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

# La consola de Windows usa cp1252 por defecto y revienta con acentos.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAIZ = Path(__file__).parent
DATOS = Path(os.environ.get("PERCEPTA_DATASET", RAIZ / "data" / "sequences.npz"))
SALIDA = RAIZ / "models"


class ClasificadorCaidas(nn.Module):
    """GRU + cabeza lineal. Entrada (lote, tiempo, features) -> logit."""

    def __init__(self, n_features: int, oculto: int = 64, capas: int = 2, dropout: float = 0.3):
        super().__init__()
        self.norm = nn.LayerNorm(n_features)
        self.gru = nn.GRU(
            n_features, oculto, num_layers=capas, batch_first=True,
            dropout=dropout if capas > 1 else 0.0, bidirectional=False,
        )
        self.cabeza = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(oculto, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.norm(x)
        salida, _ = self.gru(x)
        # Se usa el ÚLTIMO instante: la decisión es sobre cómo terminó la
        # ventana, con toda la historia previa ya acumulada en el estado.
        return self.cabeza(salida[:, -1, :]).squeeze(-1)


# Índices de los puntos que se intercambian al reflejar el cuerpo.
# (ojos, orejas, hombros, codos, muñecas, caderas, rodillas, tobillos)
PARES_ESPEJO = [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16)]
N_KP = 17


def espejar(lote: np.ndarray) -> np.ndarray:
    """Refleja horizontalmente las poses: una caída hacia la izquierda y otra
    hacia la derecha son ambas caídas.

    Duplica los ejemplos sin inventar datos y obliga al modelo a fijarse en la
    forma del movimiento y no en hacia qué lado ocurrió. Con sólo 75 ventanas
    positivas, esto es lo que más mueve la aguja.
    """
    out = lote.copy()
    # Coordenadas x normalizadas al centro de la caja: reflejar es negarlas.
    for i in range(N_KP):
        out[:, :, i * 2] *= -1.0
    # Al reflejar, izquierda y derecha se intercambian.
    for a, b in PARES_ESPEJO:
        for c in (0, 1):
            out[:, :, a * 2 + c], out[:, :, b * 2 + c] = (
                out[:, :, b * 2 + c].copy(), out[:, :, a * 2 + c].copy(),
            )
        va, vb = N_KP * 2 + a, N_KP * 2 + b
        out[:, :, va], out[:, :, vb] = out[:, :, vb].copy(), out[:, :, va].copy()
    # El seno del ángulo del torso también cambia de signo (el coseno no).
    out[:, :, N_KP * 3] *= -1.0
    return out


def separar_por_grupo(groups: np.ndarray, y: np.ndarray, frac_test=0.2, frac_val=0.15, semilla=42):
    """Reparte SECUENCIAS completas (no ventanas) en train/val/test."""
    rng = np.random.default_rng(semilla)
    secuencias = np.unique(groups)

    # Estratificar por tipo: que caídas y actividades queden repartidas parejo
    # en los tres conjuntos.
    caidas = np.array([s for s in secuencias if str(s).startswith("fall")])
    otras = np.array([s for s in secuencias if not str(s).startswith("fall")])

    def cortar(arr):
        rng.shuffle(arr)
        n_test = max(1, int(len(arr) * frac_test))
        n_val = max(1, int(len(arr) * frac_val))
        return arr[:n_test], arr[n_test : n_test + n_val], arr[n_test + n_val :]

    t1, v1, e1 = cortar(caidas.copy())
    t2, v2, e2 = cortar(otras.copy())

    test, val, train = np.concatenate([t1, t2]), np.concatenate([v1, v2]), np.concatenate([e1, e2])
    return (
        np.isin(groups, train),
        np.isin(groups, val),
        np.isin(groups, test),
        {"train": sorted(map(str, train)), "val": sorted(map(str, val)), "test": sorted(map(str, test))},
    )


def metricas(y_true: np.ndarray, y_prob: np.ndarray, umbral: float) -> dict:
    pred = (y_prob >= umbral).astype(int)
    vp = int(((pred == 1) & (y_true == 1)).sum())
    fp = int(((pred == 1) & (y_true == 0)).sum())
    fn = int(((pred == 0) & (y_true == 1)).sum())
    vn = int(((pred == 0) & (y_true == 0)).sum())
    precision = vp / (vp + fp) if vp + fp else 0.0
    exhaustividad = vp / (vp + fn) if vp + fn else 0.0
    f1 = 2 * precision * exhaustividad / (precision + exhaustividad) if precision + exhaustividad else 0.0
    # F2 pondera la exhaustividad al doble que la precisión. Es la métrica
    # correcta acá: no avisar de una persona caída es mucho peor que molestar
    # a un operador con una alerta que resulta no serlo.
    f2 = (
        5 * precision * exhaustividad / (4 * precision + exhaustividad)
        if (4 * precision + exhaustividad)
        else 0.0
    )
    return {
        "umbral": round(umbral, 2), "precision": round(precision, 4),
        "exhaustividad": round(exhaustividad, 4), "f1": round(f1, 4), "f2": round(f2, 4),
        "vp": vp, "fp": fp, "fn": fn, "vn": vn,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Entrena el clasificador de caídas")
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=4e-4)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if not DATOS.is_file():
        print(f"No encuentro {DATOS}. Corré primero: python training/extract.py")
        return 1

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    d = np.load(DATOS, allow_pickle=True)
    X, y, groups = d["X"].astype(np.float32), d["y"].astype(np.float32), d["groups"]
    print(f"{len(X)} ventanas | {int(y.sum())} caídas | {int((y==0).sum())} normales")

    m_tr, m_val, m_test, reparto = separar_por_grupo(groups, y, semilla=args.seed)
    print(f"Reparto por secuencia: {len(reparto['train'])} train | {len(reparto['val'])} val | {len(reparto['test'])} test")
    print(f"  test = {', '.join(reparto['test'])}\n")

    # Aumentación por espejo SÓLO en entrenamiento: validación y test quedan
    # intactos para que las métricas midan el mundo real, no el aumentado.
    Xtr_np, ytr_np = X[m_tr], y[m_tr]
    Xtr_np = np.concatenate([Xtr_np, espejar(Xtr_np)])
    ytr_np = np.concatenate([ytr_np, ytr_np])
    print(f"entrenamiento aumentado con espejo: {len(Xtr_np)} ventanas")

    Xtr, ytr = torch.tensor(Xtr_np), torch.tensor(ytr_np)
    Xv, yv = torch.tensor(X[m_val]), torch.tensor(y[m_val])
    Xte, yte = torch.tensor(X[m_test]), torch.tensor(y[m_test])

    # Modelo deliberadamente chico: con ~800 ventanas, una red grande memoriza
    # en vez de generalizar (se veía convergiendo en la época 1 y empeorando).
    modelo = ClasificadorCaidas(X.shape[2], oculto=48, capas=2, dropout=0.45)
    # Compensa el desbalance: si hay 9 negativos por positivo, cada positivo
    # pesa 9 veces más. Sin esto el modelo aprende a decir "nunca es caída".
    n_pos = max(float(ytr.sum()), 1.0)
    pos_weight = torch.tensor([(len(ytr) - n_pos) / n_pos])
    print(f"peso de la clase positiva: {pos_weight.item():.2f}")

    criterio = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    opt = torch.optim.AdamW(modelo.parameters(), lr=args.lr, weight_decay=3e-4)
    sched = torch.optim.lr_scheduler.ReduceLROnPlateau(opt, mode="max", factor=0.5, patience=6)

    mejor_f1, mejor_estado, sin_mejora = -1.0, None, 0
    for epoca in range(1, args.epochs + 1):
        modelo.train()
        perm = torch.randperm(len(Xtr))
        perdida_total = 0.0
        for i in range(0, len(perm), args.batch):
            idx = perm[i : i + args.batch]
            opt.zero_grad()
            perdida = criterio(modelo(Xtr[idx]), ytr[idx])
            perdida.backward()
            nn.utils.clip_grad_norm_(modelo.parameters(), 1.0)
            opt.step()
            perdida_total += perdida.item() * len(idx)

        modelo.eval()
        with torch.no_grad():
            prob_val = torch.sigmoid(modelo(Xv)).numpy()
        f1_val = metricas(yv.numpy(), prob_val, 0.5)["f2"]
        sched.step(f1_val)

        if f1_val > mejor_f1:
            mejor_f1, sin_mejora = f1_val, 0
            mejor_estado = {k: v.clone() for k, v in modelo.state_dict().items()}
        else:
            sin_mejora += 1

        if epoca % 10 == 0 or epoca == 1:
            print(f"  época {epoca:3d}  pérdida {perdida_total/len(Xtr):.4f}  F1 val {f1_val:.4f}")

        if sin_mejora >= 25:
            print(f"  sin mejora en 25 épocas, se corta en la {epoca}")
            break

    if mejor_estado:
        modelo.load_state_dict(mejor_estado)

    # ── evaluación final sobre secuencias NUNCA vistas ──────────────
    modelo.eval()
    with torch.no_grad():
        prob_val = torch.sigmoid(modelo(Xv)).numpy()
        prob_test = torch.sigmoid(modelo(Xte)).numpy()

    # Elección del umbral, en VALIDACIÓN y nunca mirando el test.
    #
    # No se maximiza F1 ni F2 a secas. El criterio es: el umbral MÁS ALTO que
    # todavía atrape al menos el 90% de las caídas. La razón es que este modelo
    # NO decide solo: en producción corre después de las reglas geométricas,
    # que ya descartaron casi todo el movimiento normal. La precisión la aporta
    # esa compuerta; al modelo se le pide sobre todo que no deje pasar caídas.
    RECALL_MINIMO = 0.9
    candidatos = []
    for u in np.arange(0.05, 0.96, 0.05):
        m = metricas(yv.numpy(), prob_val, float(u))
        if m["exhaustividad"] >= RECALL_MINIMO:
            candidatos.append((float(u), m["precision"]))

    if candidatos:
        # Entre los que cumplen el mínimo, el de mayor precisión.
        mejor_u = max(candidatos, key=lambda c: (c[1], c[0]))[0]
    else:
        # Ninguno llega al 90%: se cae a F2, que ya prioriza la exhaustividad.
        mejor_u, mejor_f2 = 0.5, -1.0
        for u in np.arange(0.05, 0.96, 0.05):
            f2 = metricas(yv.numpy(), prob_val, float(u))["f2"]
            if f2 > mejor_f2:
                mejor_f2, mejor_u = f2, float(u)

    # Curva completa sobre el test: deja ver el intercambio real y permite
    # mover el umbral después sin volver a entrenar.
    print("\nIntercambio precisión / exhaustividad (test):")
    print("  umbral  precisión  exhaustividad  falsas alarmas  caídas perdidas")
    curva = []
    for u in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
        m = metricas(yte.numpy(), prob_test, u)
        curva.append(m)
        marca = "  <- elegido" if abs(u - mejor_u) < 0.03 else ""
        print(f"   {u:.2f}     {m['precision']:.3f}        {m['exhaustividad']:.3f}"
              f"            {m['fp']:2d}              {m['fn']:2d}{marca}")

    m_test_res = metricas(yte.numpy(), prob_test, mejor_u)
    print(f"\n{'─'*58}\nRESULTADO en secuencias nunca vistas (umbral {mejor_u:.2f})")
    print(f"  precisión      {m_test_res['precision']:.3f}   (de las alertas, cuántas eran caídas)")
    print(f"  exhaustividad  {m_test_res['exhaustividad']:.3f}   (de las caídas, cuántas detectó)")
    print(f"  F1             {m_test_res['f1']:.3f}")
    print(f"  aciertos: {m_test_res['vp']} caídas, {m_test_res['vn']} normales")
    print(f"  errores:  {m_test_res['fp']} falsas alarmas, {m_test_res['fn']} caídas no vistas")
    print("-" * 58)

    SALIDA.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": modelo.state_dict(), "n_features": X.shape[2]}, SALIDA / "fall_classifier.pt")

    # ONNX: el worker lo corre sin depender de PyTorch.
    modelo.eval()
    torch.onnx.export(
        modelo, torch.zeros(1, X.shape[1], X.shape[2]), str(SALIDA / "fall_classifier.onnx"),
        input_names=["sequence"], output_names=["logit"],
        dynamic_axes={"sequence": {0: "batch"}, "logit": {0: "batch"}}, opset_version=17,
    )

    (SALIDA / "metadata.json").write_text(
        json.dumps(
            {
                "modelo": "GRU(64)x2 sobre secuencias de pose",
                "dataset": "UR Fall Detection (Universidad de Rzeszów)",
                "ventana": X.shape[1], "features": X.shape[2],
                "umbralRecomendado": round(mejor_u, 2),
                "test": m_test_res, "curva": curva, "reparto": reparto,
            },
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nModelo guardado en {SALIDA}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
