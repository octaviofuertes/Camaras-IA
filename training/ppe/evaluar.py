"""Mide qué tan bien detecta el modelo de EPP, clase por clase.

  python training/ppe/evaluar.py                    # el modelo en uso
  python training/ppe/evaluar.py --pesos otro.pt    # comparar dos modelos
  python training/ppe/evaluar.py --umbral 0.5

Mide sobre el split de PRUEBA: 254 imágenes que el modelo nunca vio ni durante
el entrenamiento ni para elegir el mejor checkpoint. Es el único número que
significa algo — el del entrenamiento siempre se ve mejor de lo que es.

── Qué mirar, y por qué el mAP solo no alcanza ─────────────────────────────

El mAP es la nota promedio del detector, pero acá las dos formas de
equivocarse no cuestan lo mismo:

  - Decir que a alguien le falta el casco cuando lo tiene puesto es acusarlo
    de algo que no hizo. Eso lo mide la PRECISIÓN de las clases NO-*.
  - No ver que a alguien le falta el casco es dejar pasar un riesgo. Eso lo
    mide el RECALL.

Para un sistema que le muestra alertas a un operador, la precisión de las
clases NO-* es lo que decide si lo van a seguir mirando o lo van a apagar.
Por eso se reportan separadas y en ese orden.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

AQUI = Path(__file__).parent
DATOS = AQUI / "data" / "epp.yaml"
MODELOS = AQUI.parent / "models"

#: Lo que el producto realmente usa, agrupado por elemento.
GRUPOS = [
    ("casco", "Hardhat", "NO-Hardhat"),
    ("chaleco", "Safety Vest", "NO-Safety Vest"),
    ("antiparras", "Goggles", "NO-Goggles"),
    ("guantes", "Gloves", "NO-Gloves"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pesos", default=str(MODELOS / "epp.pt"))
    ap.add_argument("--umbral", type=float, default=0.45,
                    help="confianza mínima, la misma que usa el módulo")
    ap.add_argument("--imgsz", type=int, default=512)
    ap.add_argument("--split", default="test")
    args = ap.parse_args()

    pesos = Path(args.pesos)
    if not pesos.is_file():
        print(f"No hay modelo en {pesos}. Entrenalo con: python training/ppe/entrenar.py",
              file=sys.stderr)
        return 1
    if not DATOS.is_file():
        print("Falta el dataset. Corré: python training/ppe/descargar.py", file=sys.stderr)
        return 1

    from ultralytics import YOLO

    modelo = YOLO(str(pesos))
    nombres = modelo.names
    print(f"Modelo: {pesos.name}   split: {args.split}   umbral: {args.umbral}\n")

    m = modelo.val(data=str(DATOS), split=args.split, device="cpu",
                   conf=args.umbral, imgsz=args.imgsz, verbose=False)

    por: dict[str, dict] = {}
    for i, idx in enumerate(m.box.ap_class_index):
        por[nombres[int(idx)]] = {
            "map50": float(m.box.ap50[i]),
            "precision": float(m.box.p[i]),
            "recall": float(m.box.r[i]),
        }

    def fila(nombre: str) -> str:
        d = por.get(nombre)
        if not d:
            return f"  {nombre:<16} {'—':>8}  (sin ejemplos en este split)"
        return (f"  {nombre:<16} {d['map50']:>8.3f} {d['precision']:>10.3f} "
                f"{d['recall']:>8.3f}")

    print(f"  {'clase':<16} {'mAP50':>8} {'precisión':>10} {'recall':>8}")
    print("  " + "─" * 44)
    for etiqueta, puesto, falta in GRUPOS:
        print(f"  ── {etiqueta} " + "─" * (40 - len(etiqueta)))
        print(fila(puesto))
        print(fila(falta))
    print("  " + "─" * 44)
    print(fila("Person"))

    print(f"\n  mAP50 general: {float(m.box.map50):.3f}   mAP50-95: {float(m.box.map):.3f}")

    # La lectura en castellano de lo que significan esos números para el
    # producto: cuántas de las alertas que emitiría serían correctas, y cuántas
    # faltas reales se le escaparían.
    print("\n  Qué significa para las alertas:")
    for etiqueta, _puesto, falta in GRUPOS:
        d = por.get(falta)
        if not d:
            print(f"    {etiqueta:<12} sin datos en este split")
            continue
        de_cada_10 = round(d["precision"] * 10)
        ve = round(d["recall"] * 100)
        print(f"    {etiqueta:<12} de cada 10 alertas, ~{de_cada_10} serían correctas; "
              f"ve el {ve}% de las faltas reales")

    ficha = MODELOS / "epp.json"
    if ficha.is_file():
        try:
            datos = json.loads(ficha.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            datos = {}
    else:
        datos = {}
    datos["evaluacion"] = {
        "pesos": pesos.name, "split": args.split, "umbral": args.umbral,
        "imgsz": args.imgsz,
        "mapa50": round(float(m.box.map50), 4),
        "mapa50_95": round(float(m.box.map), 4),
        "por_clase": {k: {kk: round(vv, 4) for kk, vv in v.items()} for k, v in por.items()},
    }
    MODELOS.mkdir(parents=True, exist_ok=True)
    ficha.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  Guardado en {ficha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
