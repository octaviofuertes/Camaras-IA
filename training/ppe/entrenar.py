"""Entrena el detector de elementos de protección personal.

  python training/ppe/entrenar.py                 # entrenamiento completo
  python training/ppe/entrenar.py --epocas 3      # una pasada corta, para probar
  python training/ppe/entrenar.py --adoptar       # corta la corrida y se queda con el mejor
  python training/ppe/entrenar.py --solo-medir    # evalúa lo ya entrenado

Sale un `epp.pt` que carga el módulo `ppe-detection`.

── Por qué yolov8n y no algo más grande ────────────────────────────────────

Porque esto corre en CPU. El benchmark del repositorio da 275 ms por cuadro con
yolov8n segmentando; el modelo `m` cuesta cerca de cinco veces eso y dejaría la
cámara mirando una imagen de hace dos segundos. Un detector que llega tarde no
sirve para avisar que alguien entró sin casco: para cuando avisa, ya pasó.

── Por qué 512 y no 416 ni 640 ─────────────────────────────────────────────

Las antiparras ocupan el 0,8% del área de la imagen: alrededor del 9% de cada
lado. A 416 píxeles eso son 37 píxeles de ancho, al borde de lo que un modelo
chico puede aprender; a 512 son 46, que alcanza. Ir a 640 mejora poco y cuesta
un 55% más de tiempo por época, que en CPU se mide en horas.

Se bajó de 576 a 512 por una razón de presupuesto, no de precisión: en esta
máquina (CPU, ocho núcleos) una época a 576 tarda unos 26 minutos, y hacen
falta decenas de épocas para que aparezcan las clases negativas. A 512 la época
baja a unos 20 y la corrida entera entra en una noche.

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
sys.path.insert(0, str(AQUI))
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
    ap.add_argument("--epocas", type=int, default=30)
    ap.add_argument("--imgsz", type=int, default=512)
    ap.add_argument("--lote", type=int, default=8)
    ap.add_argument("--base", default="yolov8n.pt")
    ap.add_argument("--solo-medir", action="store_true")
    ap.add_argument("--sin-balancear", action="store_true",
                    help="entrena con el reparto original del dataset")
    ap.add_argument("--instalar", action="store_true",
                    help="toma los pesos de la última corrida y los deja como el modelo en uso")
    ap.add_argument("--adoptar", action="store_true",
                    help="toma el best.pt de la corrida en curso, lo pone en models/ y lo mide")
    args = ap.parse_args()

    if not DATOS.is_file():
        print("Falta el dataset. Corré: python training/ppe/descargar.py", file=sys.stderr)
        return 1

    from ultralytics import YOLO

    destino = MODELOS / "epp.pt"

    # Las clases de ausencia (NO-Hardhat y compañía) tienen tres o cuatro veces
    # menos ejemplos que sus positivas, y son JUSTO las que disparan la alerta.
    # Sin balancear, el modelo aprende a ver cascos y no aprende a ver cabezas
    # descubiertas: medido, 0,81 de mAP contra 0,08. Un módulo así no avisa
    # nunca y desde afuera se ve igual que uno roto.
    datos = DATOS
    if not args.solo_medir and not args.sin_balancear:
        from balancear import preparar

        lista, resumen = preparar(DATOS)
        datos = AQUI / "data" / "epp_balanceado.yaml"
        datos.write_text(
            DATOS.read_text(encoding="utf-8").replace(
                "train: train/images", f"train: {lista.resolve().as_posix()}"
            ),
            encoding="utf-8",
        )
        print(f"Balanceado: {resumen['imagenes']} imágenes -> {resumen['entradas']} entradas")
        for c in ("NO-Hardhat", "NO-Safety Vest", "NO-Goggles", "NO-Gloves"):
            print(f"  {c:<16} {resumen['antes'].get(c, 0):>5} -> {resumen['despues'].get(c, 0):>5} fotos")
        print()

    mejor = SALIDA / "epp" / "weights" / "best.pt"

    if args.instalar:
        # En CPU un entrenamiento tarda horas y se corta: se apaga la máquina,
        # se cierra la terminal. Ultralytics guarda `best.pt` en cada época, así
        # que lo entrenado hasta ahí ya sirve — pero quedaba tirado en la
        # carpeta de la corrida y el módulo seguía diciendo que no hay modelo.
        mejor = SALIDA / "epp" / "weights" / "best.pt"
        if not mejor.is_file():
            print(f"No hay ninguna corrida con pesos en {mejor}", file=sys.stderr)
            return 1
        MODELOS.mkdir(parents=True, exist_ok=True)
        shutil.copy2(mejor, destino)
        print(f"Instalado {mejor} -> {destino}")
        modelo = YOLO(str(destino))
    elif args.solo_medir:
        if not destino.is_file():
            print(f"No hay modelo entrenado en {destino}", file=sys.stderr)
            return 1
        modelo = YOLO(str(destino))
    elif args.adoptar:
        # Entrenar acá son horas, y ultralytics deja un best.pt actualizado al
        # final de cada época. Esto permite cortar la corrida cuando las
        # métricas ya alcanzan y quedarse con lo mejor que hubo, en vez de
        # tener que llegar hasta la última época o perderlo todo.
        if not mejor.is_file():
            print(f"No hay corrida en {mejor}", file=sys.stderr)
            return 1
        MODELOS.mkdir(parents=True, exist_ok=True)
        shutil.copy2(mejor, destino)
        print(f"Adoptado {mejor} -> {destino}")
        modelo = YOLO(str(destino))
    else:
        print(f"Entrenando desde {args.base}: {args.epocas} épocas, imgsz {args.imgsz}, lote {args.lote}")
        print("En CPU esto tarda horas. Se puede cortar y retomar con --solo-medir sobre lo que haya.\n")
        modelo = YOLO(args.base)
        t0 = time.time()
        modelo.train(
            data=str(datos),
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
            # Nada de cache="ram": las imágenes pesan poco en disco pero
            # descomprimidas ocupan GB, y esta máquina tiene menos de 2 GB
            # libres. Cachear ahí la manda a swap y cada época pasa a tardar
            # más que leyendo el JPEG, que con ocho lectores es barato.
            cache=False,
            workers=8,
            # Sin corte temprano: la corrida anterior murió en la época 1 y las
            # clases negativas (NO-Hardhat y compañía) son justo las últimas que
            # el modelo aprende. Cortar por paciencia acá es cortar antes de que
            # aparezca lo único que hace útil al módulo.
            patience=args.epocas,
            # El optimizador se fija a mano en vez de dejarlo en "auto".
            # En la corrida anterior "auto" eligió AdamW con lr 0,00167 y tres
            # épocas de calentamiento: la época 1 entrenó a lr 0,0002, es decir
            # casi no entrenó. Como el tronco está congelado y sólo se mueve la
            # cabeza, se puede ir bastante más rápido sin desestabilizar nada.
            optimizer="AdamW",
            lr0=0.002,
            warmup_epochs=1.0,
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

    # Se escribe de cero a propósito: los umbrales que hubiera guardados se
    # midieron sobre el modelo anterior y no valen para éste. Lo que NO puede
    # pasar es quedarse ahí, porque sin umbrales el módulo no alerta de nada.
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

    # Y se calibra acá mismo. Entrenar y calibrar eran dos pasos, y el segundo
    # se olvidaba: el modelo quedaba instalado y mudo, sin nada en pantalla que
    # dijera por qué no avisaba nunca. Cuesta un minuto sobre horas de
    # entrenamiento.
    print("\nEligiendo los umbrales de alerta para el modelo nuevo…")
    sys.path.insert(0, str(AQUI))
    try:
        from evaluar_personas import calibrar_y_guardar

        calibrar_y_guardar(destino, recalcular=True)
    except Exception as exc:  # noqa: BLE001
        # No se pierde el entrenamiento por esto, pero se dice fuerte: el
        # modelo está entrenado y todavía no puede alertar.
        print(f"\nNo se pudo calibrar: {exc!r}", file=sys.stderr)
        print("El modelo quedó entrenado pero SIN umbrales, así que el módulo "
              "no va a alertar. Corré:\n"
              "    python training/ppe/evaluar_personas.py --calibrar",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
