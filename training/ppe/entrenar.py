"""Entrena el detector de elementos de protección personal.

  python training/ppe/entrenar.py                 # entrenamiento completo
  python training/ppe/entrenar.py --epocas 3      # una pasada corta, para probar
  python training/ppe/entrenar.py --solo-medir    # evalúa lo ya entrenado

Sale un `epp.pt` que carga el módulo `ppe-detection`.

── Por qué yolov8n y no algo más grande ────────────────────────────────────

Porque esto corre en CPU. El benchmark del repositorio da 275 ms por cuadro con
yolov8n segmentando; el modelo `m` cuesta cerca de cinco veces eso y dejaría la
cámara mirando una imagen de hace dos segundos. Un detector que llega tarde no
sirve para avisar que alguien entró sin casco: para cuando avisa, ya pasó.

── Por qué 576 y no 416 ni 640 ─────────────────────────────────────────────

Las antiparras ocupan el 0,8% del área de la imagen: alrededor del 9% de cada
lado. A 416 píxeles eso son 37 píxeles de ancho, al borde de lo que un modelo
chico puede aprender; a 576 son 52, que alcanza. Ir a 640 mejora poco y cuesta
un 25% más de tiempo por época, que en CPU se mide en horas.

── Qué se congela y qué no ─────────────────────────────────────────────────

Se parte de los pesos de COCO y se congela el tronco (las primeras 10 capas).
Las capas iniciales ya saben ver bordes, texturas y personas; volver a
aprenderlas con 2.700 imágenes las empeora y multiplica el tiempo. Lo que se
entrena es la cabeza, que es donde vive "esto es un casco".
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

AQUI = Path(__file__).parent
DATOS = AQUI / "data" / "epp.yaml"
SALIDA = AQUI / "corridas"
MODELOS = AQUI.parent / "models"

#: Las clases que le importan al producto. El dataset trae once; `None` y
#: `Boots` no se usan —el calzado no se ve desde una cámara de techo— pero se
#: entrenan igual: quitarlas obligaría a reetiquetar y le sacaría al modelo
#: ejemplos negativos que le enseñan a no confundir un zapato con un guante.
DE_INTERES = [
    "Hardhat", "NO-Hardhat",
    "Safety Vest", "NO-Safety Vest",
    "Gloves", "NO-Gloves",
    "Goggles", "NO-Goggles",
]


def _medidas(metricas, nombres: dict[int, str]) -> dict:
    """Saca de ultralytics el mAP por clase, con nombres legibles."""
    salida: dict = {"mapa50": round(float(metricas.box.map50), 4),
                    "mapa50_95": round(float(metricas.box.map), 4),
                    "por_clase": {}}
    try:
        for i, idx in enumerate(metricas.box.ap_class_index):
            salida["por_clase"][nombres[int(idx)]] = {
                "map50": round(float(metricas.box.ap50[i]), 4),
                "precision": round(float(metricas.box.p[i]), 4),
                "recall": round(float(metricas.box.r[i]), 4),
            }
    except Exception as exc:  # noqa: BLE001
        salida["por_clase_error"] = repr(exc)
    return salida


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--epocas", type=int, default=12)
    ap.add_argument("--imgsz", type=int, default=576)
    ap.add_argument("--lote", type=int, default=8)
    ap.add_argument("--base", default="yolov8n.pt")
    ap.add_argument("--solo-medir", action="store_true")
    args = ap.parse_args()

    if not DATOS.is_file():
        print("Falta el dataset. Corré: python training/ppe/descargar.py", file=sys.stderr)
        return 1

    from ultralytics import YOLO

    destino = MODELOS / "epp.pt"

    if args.solo_medir:
        if not destino.is_file():
            print(f"No hay modelo entrenado en {destino}", file=sys.stderr)
            return 1
        modelo = YOLO(str(destino))
    else:
        print(f"Entrenando desde {args.base}: {args.epocas} épocas, imgsz {args.imgsz}, lote {args.lote}")
        print("En CPU esto tarda horas. Se puede cortar y retomar con --solo-medir sobre lo que haya.\n")
        modelo = YOLO(args.base)
        t0 = time.time()
        modelo.train(
            data=str(DATOS),
            epochs=args.epocas,
            imgsz=args.imgsz,
            batch=args.lote,
            device="cpu",
            project=str(SALIDA),
            name="epp",
            exist_ok=True,
            # El tronco de COCO ya sabe ver personas y objetos; lo que hay que
            # enseñar es qué es un casco.
            freeze=10,
            # Las imágenes entran en memoria (2.717 de ~39 KB son unos 105 MB) y
            # ultralytics avisaba que el disco era el cuello de botella: leía a
            # 3 MB/s. Sin esto cada época tardaba 33 minutos y entrenar era
            # cuestión de días.
            cache="ram",
            workers=8,
            # Paciencia amplia: con pocas imágenes el mAP se mueve a los saltos
            # y un corte temprano deja el modelo a mitad de aprender.
            patience=15,
            # Nada de volteo vertical: una persona al revés no existe en una
            # cámara de seguridad, y enseñárselo gasta capacidad en un caso que
            # nunca va a ver.
            flipud=0.0,
            fliplr=0.5,
            # El color importa: un chaleco es naranja o amarillo flúor. Se deja
            # variar poco para que el modelo no aprenda a ignorarlo.
            hsv_h=0.010,
            hsv_s=0.5,
            hsv_v=0.3,
            verbose=True,
        )
        print(f"\nEntrenamiento terminado en {(time.time() - t0) / 60:.0f} min")

        mejor = SALIDA / "epp" / "weights" / "best.pt"
        if not mejor.is_file():
            print(f"No apareció {mejor}", file=sys.stderr)
            return 1
        MODELOS.mkdir(parents=True, exist_ok=True)
        shutil.copy2(mejor, destino)
        modelo = YOLO(str(destino))

    print("\nMidiendo contra el split de prueba (imágenes que el modelo nunca vio)…")
    metricas = modelo.val(data=str(DATOS), split="test", device="cpu", verbose=False)
    nombres = modelo.names
    resultado = _medidas(metricas, nombres)

    print(f"\nmAP50 general: {resultado['mapa50']:.3f}   mAP50-95: {resultado['mapa50_95']:.3f}\n")
    print(f"{'clase':<18} {'mAP50':>7} {'precisión':>10} {'recall':>8}")
    for nombre in DE_INTERES:
        m = resultado["por_clase"].get(nombre)
        if not m:
            print(f"{nombre:<18} {'—':>7}  (sin ejemplos en prueba)")
            continue
        print(f"{nombre:<18} {m['map50']:>7.3f} {m['precision']:>10.3f} {m['recall']:>8.3f}")

    ficha = MODELOS / "epp.json"
    ficha.write_text(json.dumps({
        "modelo": "epp.pt",
        "base": args.base,
        "epocas": args.epocas,
        "imgsz": args.imgsz,
        "dataset": "HB1204/PPE_Detection (CC BY 4.0)",
        "clases": [nombres[i] for i in sorted(nombres)],
        "metricas": resultado,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nModelo en {destino}\nMétricas en {ficha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
