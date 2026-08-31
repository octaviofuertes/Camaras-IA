"""Corre el módulo de EPP sobre imágenes y dibuja lo que vería el operador.

  python training/ppe/probar_modulo.py                      # 12 imágenes de prueba
  python training/ppe/probar_modulo.py --cuantas 40
  python training/ppe/probar_modulo.py --imagenes C:/fotos  # las tuyas
  python training/ppe/probar_modulo.py --camara 0           # la webcam, 20 cuadros

Sale un PNG por imagen en `training/ppe/salida/`, con el mismo criterio de
colores que la pantalla: verde lo que la persona lleva puesto, rojo lo que se
ve que le falta, gris lo que no se sabe.

── Para qué sirve esto ─────────────────────────────────────────────────────

Las métricas del entrenamiento dicen cuánto acierta el modelo sobre el dataset,
que es lo que hay que mirar para saber si aprendió. Pero no dicen si el módulo
—el que arma las cajas, decide de quién es cada casco y elige el color— hace lo
que se espera. Eso hay que verlo.

Corre el módulo de verdad, el mismo `module.py` que usa el worker, y no una
copia de su lógica: una prueba que reimplementa lo que quiere probar pasa
siempre, incluso cuando el original está roto.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "modules" / "ppe-detection"))

from percepta_contracts import Frame, ModuleContext  # noqa: E402

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "ppe_module", RAIZ / "modules" / "ppe-detection" / "module.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

SALIDA = Path(__file__).parent / "salida"

#: BGR, que es como los quiere OpenCV.
VERDE = (94, 197, 34)
ROJO = (68, 68, 239)
GRIS = (150, 150, 150)
COLOR = {"tiene": VERDE, "falta": ROJO, "no_se_sabe": GRIS}
MARCA = {"tiene": "OK", "falta": "FALTA", "no_se_sabe": "?"}


def _caja_px(bbox, w: int, h: int) -> tuple[int, int, int, int]:
    x, y, bw, bh = bbox
    return int(x * w), int(y * h), int((x + bw) * w), int((y + bh) * h)


def _texto(img, txt: str, x: int, y: int, color) -> None:
    """Texto con reborde negro: sobre un video claro, sin esto no se lee."""
    for grosor, c in ((4, (0, 0, 0)), (1, color)):
        cv2.putText(img, txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, grosor, cv2.LINE_AA)


def dibujar(img: np.ndarray, vista: dict) -> np.ndarray:
    """Pinta la vista del módulo igual que la pantalla del dashboard."""
    out = img.copy()
    h, w = out.shape[:2]
    exigidos = vista.get("exigidos", [])

    # El cuerpo: rojo si le falta algo obligatorio, verde si se le ve todo.
    for per in vista.get("personas", []):
        estado = per.get("estado", {})
        falta = any(estado.get(c) == "falta" for c in exigidos)
        todo = bool(exigidos) and all(estado.get(c) == "tiene" for c in exigidos)
        color = ROJO if falta else (VERDE if todo else (180, 180, 180))
        x1, y1, x2, y2 = _caja_px(per["bbox"], w, h)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 3)
        for i, clave in enumerate(exigidos):
            e = estado.get(clave, "no_se_sabe")
            _texto(out, f"{MARCA[e]} {clave}", x1 + 5, y1 + 18 + i * 17, COLOR[e])

    # Cada elemento detectado, con su propia caja.
    for el in vista.get("elementos", []):
        color = VERDE if el["tiene"] else ROJO
        if not el["exigido"]:
            color = GRIS
        x1, y1, x2, y2 = _caja_px(el["bbox"], w, h)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        rot = el["nombre"] if el["tiene"] else f"sin {el['nombre']}"
        _texto(out, f"{rot} {el['conf']:.2f}", x1, max(12, y1 - 5), color)
    return out


def cargar_modulo(pesos: str | None, exigidos: list[str], min_conf: float):
    m = _mod.MODULE_CLASS()
    cfg = {"exigidos": exigidos, "minConfianza": min_conf, "framesSeguidos": 1}
    if pesos:
        cfg["pesos"] = pesos
    m.load(ModuleContext(
        ai_module_id="prueba", module_key="ppe-detection", module_version="1.0.0",
        device="cpu", config=cfg, zones={},
    ))
    m.warmup()
    return m


def procesar(m, img: np.ndarray, seq: int) -> dict:
    h, w = img.shape[:2]
    m.infer(Frame(camera_id="prueba", frame_seq=seq, captured_at=time.time(),
                  image=img, width=w, height=h, ring_buffer_key=""))
    return m.en_vivo()


def resumir(vista: dict) -> str:
    exigidos = vista.get("exigidos", [])
    partes = []
    for per in vista.get("personas", []):
        est = per.get("estado", {})
        partes.append("[" + " ".join(f"{c}={est.get(c, '?')}" for c in exigidos) + "]")
    return f"{len(vista.get('personas', []))} persona(s) " + " ".join(partes) if partes else "sin personas"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--imagenes", default=str(Path(__file__).parent / "data" / "test" / "images"))
    ap.add_argument("--cuantas", type=int, default=12)
    ap.add_argument("--camara", type=int, default=None, help="usa la webcam en vez de archivos")
    ap.add_argument("--pesos", default=None)
    ap.add_argument("--exigidos", default="casco,chaleco,guantes")
    ap.add_argument("--min-conf", type=float, default=0.45)
    args = ap.parse_args()

    exigidos = [e.strip() for e in args.exigidos.split(",") if e.strip()]
    print(f"Cargando el módulo (se exige: {', '.join(exigidos)}; confianza minima {args.min_conf})…")
    m = cargar_modulo(args.pesos, exigidos, args.min_conf)
    print(f"  modelo: {m.health()['pesos']}\n")

    SALIDA.mkdir(parents=True, exist_ok=True)
    vistas = 0
    con_falta = 0
    con_todo = 0

    if args.camara is not None:
        cap = cv2.VideoCapture(args.camara)
        if not cap.isOpened():
            print(f"No se pudo abrir la cámara {args.camara}", file=sys.stderr)
            return 1
        for i in range(20):
            ok, img = cap.read()
            if not ok:
                break
            vista = procesar(m, img, i)
            cv2.imwrite(str(SALIDA / f"camara_{i:02d}.png"), dibujar(img, vista))
            print(f"  cuadro {i:02d}: {resumir(vista)}")
            vistas += 1
        cap.release()
    else:
        carpeta = Path(args.imagenes)
        archivos = sorted(
            f for f in carpeta.iterdir()
            if f.suffix.lower() in (".jpg", ".jpeg", ".png")
        )[: args.cuantas]
        if not archivos:
            print(f"No hay imágenes en {carpeta}", file=sys.stderr)
            return 1
        for i, f in enumerate(archivos):
            img = cv2.imread(str(f))
            if img is None:
                continue
            vista = procesar(m, img, i)
            cv2.imwrite(str(SALIDA / f"{f.stem}.png"), dibujar(img, vista))
            est = [p.get("estado", {}) for p in vista.get("personas", [])]
            con_falta += sum(1 for e in est if any(v == "falta" for v in e.values()))
            con_todo += sum(1 for e in est if e and all(v == "tiene" for v in e.values()))
            print(f"  {f.name:<40} {resumir(vista)}")
            vistas += 1

    print(f"\n{vistas} imagen(es) en {SALIDA}")
    if args.camara is None:
        print(f"  personas marcadas en ROJO (les falta algo): {con_falta}")
        print(f"  personas marcadas en VERDE (todo puesto):   {con_todo}")
        if con_falta == 0:
            print("\n  ¡Ojo! Ninguna persona quedó en rojo. Si en las fotos hay gente sin")
            print("  casco, el modelo no está viendo las clases negativas: reentrenalo con")
            print("  python training/ppe/entrenar.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
