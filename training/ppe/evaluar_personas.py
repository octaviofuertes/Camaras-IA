"""Mide lo que el operador realmente ve: el veredicto sobre CADA PERSONA.

  python training/ppe/evaluar_personas.py
  python training/ppe/evaluar_personas.py --calibrar
  python training/ppe/evaluar_personas.py --imgsz 640 --recalcular

── Por qué no alcanza con `evaluar.py` ni con `umbral.py` ──────────────────

Los dos miden al DETECTOR: qué tan bien encuentra cajas de la clase
`NO-Hardhat`, comparándolas una por una contra las anotadas. Es lo que hay que
mirar para saber si el modelo aprendió, pero NO es lo que hace el módulo.

El módulo no muestra cajas: dice "a esta persona le falta el casco". Entre una
cosa y la otra hay pasos que la métrica del detector no ve:

  - encontrar a la persona (si no se la ve, no hay alerta posible),
  - decidir de quién es cada casco cuando hay varios juntos,
  - resolver qué pasa cuando el mismo cuerpo tiene evidencia contradictoria,
  - mirar de cerca a cada persona, que recupera a los del fondo.

La diferencia no es teórica: medido sobre el mismo modelo y el mismo split, el
chaleco da 0,41 de precisión como detector y 0,83 como veredicto por persona.
Calibrar con el primero lo dejaba mudo; con el segundo avisa 3 de cada 4 faltas
reales. Dos cajas mal puestas sobre la misma persona son dos errores para el
detector y una sola alerta —correcta— para quien mira la pantalla.

Por eso los umbrales con los que el módulo decide salen de acá.

── Cómo se arma la verdad ──────────────────────────────────────────────────

El dataset anota cabezas y cuerpos por separado, sin decir cuál es de quién.
La pertenencia se resuelve con las MISMAS funciones que usa el módulo
(`de_quien_es`, `en_su_lugar`) pero sobre las cajas anotadas, que son exactas.
Así lo que se mide es la decisión, no el emparejamiento.

Una persona sin ninguna anotación de ese elemento queda en "no se sabe" y no
cuenta ni a favor ni en contra: en esa foto nadie anotó si tenía casco, y
contarla como "lo tenía" sería inventar una equivocación del módulo.

Una persona anotada que el detector no encontró cuenta como falta no vista
—recall— y nunca como acierto: no avisar porque no se vio a nadie es no avisar
igual.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

AQUI = Path(__file__).parent
RAIZ = AQUI.parent.parent
MODELOS = AQUI.parent / "models"
sys.path.insert(0, str(RAIZ / "modules" / "ppe-detection"))

# La consola de Windows sale en cp1252, que no tiene los caracteres de las
# tablas ni de la ayuda: sin esto, `--help` termina en UnicodeEncodeError.
for _salida in (sys.stdout, sys.stderr):
    if hasattr(_salida, "reconfigure"):
        _salida.reconfigure(encoding="utf-8", errors="replace")

from reglas import (  # noqa: E402
    ELEMENTOS,
    IMGSZ,
    IMGSZ_RECORTE,
    PISO_DEL_DETECTOR,
    ConfigEpp,
    decidir,
    evaluar_cuadro,
    evidencia_por_persona,
    huella_de_pesos,
)

#: Orden de las clases en las etiquetas del dataset (`data/epp.yaml`).
CLASES = [
    "Boots", "Gloves", "Goggles", "Hardhat", "NO-Gloves", "NO-Goggles",
    "NO-Hardhat", "NO-Safety Vest", "None", "Person", "Safety Vest",
]

#: Dónde se guardan las detecciones crudas para no repetir la inferencia en
#: cada barrido de umbrales. Es una caché: se puede borrar sin perder nada.
CACHE = AQUI / "salida" / "detecciones_test.json"

#: Config con la que se junta la evidencia: sin piso de confianza, para que el
#: barrido pueda probar cualquier umbral sobre las mismas detecciones.
TODO_PASA = ConfigEpp(
    exigidos=tuple(e.clave for e in ELEMENTOS),
    minConfianza=0.0,
    minConfianzaFalta=0.0,
)


# ── la verdad, sacada de las anotaciones ─────────────────────────────────
def _leer_etiquetas(archivo: Path) -> tuple[list, list]:
    """Personas y elementos anotados en una imagen, en cajas normalizadas."""
    personas: list[tuple[float, float, float, float]] = []
    elementos: list[tuple[str, tuple[float, float, float, float], float]] = []
    if not archivo.is_file():
        return personas, elementos
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        partes = linea.split()
        if len(partes) < 5:
            continue
        clase = CLASES[int(partes[0])]
        cx, cy, an, al = (float(v) for v in partes[1:5])
        caja = (cx - an / 2, cy - al / 2, an, al)
        if clase == "Person":
            personas.append(caja)
        else:
            # Confianza 1: es una anotación, no una predicción.
            elementos.append((clase, caja, 1.0))
    return personas, elementos


def _iou(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _emparejar(detectadas: list, anotadas: list, minimo: float = 0.5) -> dict[int, int]:
    """Qué persona detectada es cuál de las anotadas. {índice anotada: índice detectada}.

    Se emparejan de a una y por mayor solape, para que dos detecciones sobre el
    mismo cuerpo no cuenten como dos personas distintas.
    """
    pares: list[tuple[float, int, int]] = []
    for i, a in enumerate(anotadas):
        for j, d in enumerate(detectadas):
            v = _iou(a, d)
            if v >= minimo:
                pares.append((v, i, j))
    pares.sort(reverse=True)
    salida: dict[int, int] = {}
    usadas: set[int] = set()
    for _v, i, j in pares:
        if i in salida or j in usadas:
            continue
        salida[i] = j
        usadas.add(j)
    return salida


# ── las detecciones del modelo ───────────────────────────────────────────
def _detectar(modelo, imagen, imgsz: int, conf: float) -> tuple[list, list]:
    """Lo mismo que junta `module.infer`: cuadro entero + un recorte por persona.

    El recorte por persona existe porque en una escena real el del fondo mide
    cincuenta píxeles y el modelo se lo pierde. En video se hace por turnos —una
    persona por cuadro— pero al cabo de un segundo todos recibieron su mirada de
    cerca, así que acá se hacen todos: es el estado sostenido, que es el que
    decide la alerta.
    """
    h, w = imagen.shape[:2]
    nombres = modelo.names
    personas: list = []
    elementos: list = []

    r = modelo.predict(imagen, verbose=False, device="cpu", imgsz=imgsz, conf=conf)[0]
    for b in getattr(r, "boxes", []) or []:
        clase = nombres[int(b.cls.item())]
        x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
        caja = (x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h)
        if clase == "Person":
            personas.append((caja, float(b.conf.item())))
        else:
            elementos.append((clase, caja, float(b.conf.item())))

    for caja, _c in personas:
        px, py, pw, ph = caja
        margen = 0.12 * pw
        x1 = max(0, int((px - margen) * w))
        y1 = max(0, int((py - margen) * h))
        x2 = min(w, int((px + pw + margen) * w))
        y2 = min(h, int((py + ph + margen) * h))
        if x2 - x1 < 32 or y2 - y1 < 32:
            continue
        rr = modelo.predict(imagen[y1:y2, x1:x2], verbose=False, device="cpu",
                            imgsz=IMGSZ_RECORTE, conf=conf)[0]
        for b in getattr(rr, "boxes", []) or []:
            clase = nombres[int(b.cls.item())]
            if clase == "Person":
                continue
            bx1, by1, bx2, by2 = (float(v) for v in b.xyxy[0].tolist())
            elementos.append((clase, ((x1 + bx1) / w, (y1 + by1) / h,
                                      (bx2 - bx1) / w, (by2 - by1) / h),
                              float(b.conf.item())))
    return personas, elementos


def _cache(pesos: Path, imgsz: int, conf: float, recalcular: bool) -> list[dict]:
    """Corre el detector sobre todo el split una vez y guarda lo que salga."""
    firma = {"pesos": pesos.name, "huella": huella_de_pesos(pesos),
             "imgsz": imgsz, "conf": conf}
    if CACHE.is_file() and not recalcular:
        try:
            datos = json.loads(CACHE.read_text(encoding="utf-8"))
            if datos.get("firma") == firma:
                return datos["imagenes"]
        except (OSError, json.JSONDecodeError):
            pass

    import cv2
    from ultralytics import YOLO

    modelo = YOLO(str(pesos))
    carpeta = AQUI / "data" / "test" / "images"
    archivos = sorted(f for f in carpeta.iterdir()
                      if f.suffix.lower() in (".jpg", ".jpeg", ".png"))
    print(f"Detectando sobre {len(archivos)} imágenes de prueba (imgsz {imgsz})…")
    t0 = time.time()
    imagenes: list[dict] = []
    for i, f in enumerate(archivos):
        img = cv2.imread(str(f))
        if img is None:
            continue
        personas, elementos = _detectar(modelo, img, imgsz, conf)
        imagenes.append({
            "archivo": f.name,
            "personas": [[list(c), p] for c, p in personas],
            "elementos": [[c, list(b), p] for c, b, p in elementos],
        })
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(archivos)}  ({time.time() - t0:.0f}s)")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"firma": firma, "imagenes": imagenes}),
                     encoding="utf-8")
    print(f"  listo en {time.time() - t0:.0f}s\n")
    return imagenes


# ── un caso por persona y elemento ───────────────────────────────────────
def casos(imagenes: list[dict]) -> dict[str, list[tuple[bool | None, float | None, float | None]]]:
    """Aplana el split en casos sueltos: (lo_tenía_de_verdad, evidencia a favor, en contra).

    Se arma una sola vez y después el barrido prueba miles de umbrales sobre
    esta lista, sin volver a mirar una imagen. Lo que decide sigue siendo
    `reglas.decidir`, la misma función que corre en la cámara: una prueba que
    reimplementa lo que quiere probar pasa siempre, incluso rota.
    """
    salida: dict[str, list] = {e.clave: [] for e in ELEMENTOS}
    etiquetas = AQUI / "data" / "test" / "labels"

    for img in imagenes:
        gt_personas, gt_elementos = _leer_etiquetas(
            etiquetas / (Path(img["archivo"]).stem + ".txt"))
        if not gt_personas:
            continue
        verdad = evaluar_cuadro(gt_personas, gt_elementos, TODO_PASA, solo_exigidos=False)

        det_personas = [tuple(c) for c, _p in img["personas"]]
        det_elementos = [(c, tuple(b), p) for c, b, p in img["elementos"]]
        visto = evidencia_por_persona(det_personas, det_elementos, TODO_PASA,
                                      solo_exigidos=False)
        pares = _emparejar(det_personas, gt_personas)

        for i in range(len(gt_personas)):
            real = verdad.get(i, {})
            j = pares.get(i)
            evidencia = visto.get(j, {}) if j is not None else {}
            for clave in salida:
                anotado = real.get(clave)
                ev = evidencia.get(clave, {})
                salida[clave].append((
                    anotado[0] if anotado is not None else None,
                    ev.get("tiene"),
                    ev.get("falta"),
                ))
    return salida


def medir(casos_de: list, clave: str, cfg: ConfigEpp) -> dict[str, int]:
    """Aciertos y errores del veredicto sobre un elemento, con esta configuración."""
    c = {"tp": 0, "fp": 0, "fn": 0, "reales": 0}
    for lo_tenia, tiene, falta in casos_de:
        if lo_tenia is False:
            c["reales"] += 1
        veredicto = decidir(clave, tiene, falta, cfg)
        dice_que_falta = veredicto is not None and veredicto[0] is False
        if dice_que_falta:
            if lo_tenia is False:
                c["tp"] += 1
            elif lo_tenia is True:
                c["fp"] += 1
            # Si no se anotó nada de ese elemento no se cuenta: no hay con qué
            # decir si acertó o se equivocó.
        elif lo_tenia is False:
            c["fn"] += 1
    return c


def tasas(c: dict[str, int]) -> tuple[float, float]:
    prec = c["tp"] / (c["tp"] + c["fp"]) if (c["tp"] + c["fp"]) else 0.0
    rec = c["tp"] / (c["tp"] + c["fn"]) if (c["tp"] + c["fn"]) else 0.0
    return prec, rec


def _imprimir(titulo: str, todos: dict[str, list], cfg: ConfigEpp) -> None:
    print(f"  {titulo}")
    print(f"    {'elemento':<12} {'precisión':>10} {'recall':>8} {'avisa':>7} "
          f"{'acierta':>8} {'falla':>6} {'reales':>7}")
    print("    " + "─" * 64)
    for e in ELEMENTOS:
        c = medir(todos[e.clave], e.clave, cfg)
        prec, rec = tasas(c)
        print(f"    {e.clave:<12} {prec:>10.3f} {rec:>8.3f} {c['tp'] + c['fp']:>7} "
              f"{c['tp']:>8} {c['fp']:>6} {c['reales']:>7}")
    print()


def calibrar(
    todos: dict[str, list],
    precision_minima: float,
) -> tuple[dict[str, float], dict[str, float]]:
    """Elige, por elemento, los dos umbrales con los que el módulo va a decidir.

    Se barren las dos perillas juntas porque no son independientes: el umbral
    corroborado sólo actúa cuando el directo no alcanzó, así que bajar uno
    cambia lo que hace el otro.

    Entre todas las combinaciones que cumplen el piso de precisión se elige la
    que ve más faltas reales. La precisión es el piso y no el objetivo: por
    debajo, el operador empieza a recibir acusaciones falsas y apaga el módulo;
    por encima, lo que sobra no sirve de nada si no ve las faltas.
    """
    candidatos = [round(0.25 + 0.05 * k, 2) for k in range(0, 14)]
    directos: dict[str, float] = {}
    corroborados: dict[str, float] = {}

    print(f"  {'elemento':<12} {'directo':>8} {'corrob.':>8} {'precisión':>10} "
          f"{'recall':>8}   qué significa")
    print("  " + "─" * 78)
    for e in ELEMENTOS:
        mejor = None
        for d in candidatos:
            for c in [None] + [u for u in candidatos if u <= d]:
                cfg = ConfigEpp(
                    exigidos=(e.clave,),
                    umbralPorElemento={e.clave: d},
                    umbralCorroborado={e.clave: c} if c is not None else {},
                )
                cuenta = medir(todos[e.clave], e.clave, cfg)
                prec, rec = tasas(cuenta)
                if prec < precision_minima or rec <= 0:
                    continue
                # A igual recall se prefiere la más precisa, y a igual
                # precisión el umbral más alto: la combinación más conservadora
                # de las que empatan.
                marca = (rec, prec, -(c if c is not None else d))
                if mejor is None or marca > mejor[0]:
                    mejor = (marca, d, c, prec, rec)
        if mejor is None:
            print(f"  {e.clave:<12} {'—':>8} {'—':>8}   ninguna combinación llega a "
                  f"{precision_minima}: se queda callado")
            continue
        _marca, d, c, prec, rec = mejor
        directos[e.clave] = d
        if c is not None:
            corroborados[e.clave] = c
        print(f"  {e.clave:<12} {d:>8.2f} {(f'{c:.2f}' if c is not None else '—'):>8} "
              f"{prec:>10.3f} {rec:>8.3f}   ~{round(prec * 10)} de cada 10 alertas "
              f"correctas, ve el {round(rec * 100)}%")
    print()
    return directos, corroborados


def calibrar_y_guardar(
    pesos: Path,
    *,
    precision_minima: float = 0.70,
    imgsz: int = IMGSZ,
    conf_cruda: float = PISO_DEL_DETECTOR,
    recalcular: bool = False,
    calibrar_de_nuevo: bool = True,
) -> int:
    """Mide, elige los umbrales y los deja escritos al lado del modelo.

    La usa `entrenar.py` al terminar, para que un modelo recién entrenado nunca
    quede sin calibrar. Sin eso el módulo arranca mudo —los umbrales de un
    modelo no valen para otro— y no hay nada en pantalla que explique por qué.
    """
    imagenes = _cache(pesos, imgsz, conf_cruda, recalcular)
    todos = casos(imagenes)
    personas = len(next(iter(todos.values())))

    ficha = MODELOS / "epp.json"
    datos = {}
    if ficha.is_file():
        try:
            datos = json.loads(ficha.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            datos = {}
    guardados = datos.get("umbrales") or {}

    print(f"Veredicto por persona — {len(imagenes)} imágenes, {personas} personas "
          f"anotadas, precisión mínima pedida: {precision_minima}\n")

    if guardados.get("medidoSobre") == huella_de_pesos(pesos):
        actual = ConfigEpp(
            exigidos=tuple(e.clave for e in ELEMENTOS),
            umbralPorElemento=dict(guardados.get("porElemento") or {}),
            umbralCorroborado=dict(guardados.get("corroborado") or {}),
        )
        _imprimir("Con los umbrales que están guardados hoy:", todos, actual)
    else:
        # La huella no coincide: lo guardado se midió sobre otro modelo y el
        # módulo lo va a ignorar, así que mostrarlo como "lo de hoy" mentiría.
        print("  Este modelo todavía no tiene umbrales medidos propios: el "
              "módulo no alertaría de nada.\n")

    if not calibrar_de_nuevo:
        print("  Con --calibrar se barren los umbrales y se guardan los que sirven.")
        return 0

    directos, corroborados = calibrar(todos, precision_minima)
    elegida = ConfigEpp(
        exigidos=tuple(e.clave for e in ELEMENTOS),
        umbralPorElemento=dict(directos),
        umbralCorroborado=dict(corroborados),
    )
    _imprimir("Con los umbrales elegidos:", todos, elegida)

    datos["umbrales"] = {
        "precisionMinima": precision_minima,
        # Ata la medición a ESTE archivo de pesos: el módulo la ignora si no
        # coincide, así que un reentrenamiento no puede dejar corriendo los
        # números de un modelo que ya no existe.
        "medidoSobre": huella_de_pesos(pesos),
        "medidoCon": "evaluar_personas.py",
        "imgsz": imgsz,
        "confCruda": conf_cruda,
        "porElemento": directos,
        "corroborado": corroborados,
    }
    ficha.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Guardado en {ficha}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pesos", default=str(MODELOS / "epp.pt"))
    ap.add_argument("--imgsz", type=int, default=IMGSZ,
                    help="el mismo tamaño con el que mira el módulo")
    ap.add_argument("--conf-cruda", type=float, default=PISO_DEL_DETECTOR,
                    help="el mismo piso que usa el módulo; los umbrales se aplican después")
    ap.add_argument("--precision-minima", type=float, default=0.70)
    ap.add_argument("--recalcular", action="store_true", help="ignora la caché")
    ap.add_argument("--calibrar", action="store_true",
                    help="elige los umbrales y los guarda en models/epp.json")
    args = ap.parse_args()

    pesos = Path(args.pesos)
    if not pesos.is_file():
        print(f"No hay modelo en {pesos}. Entrenalo con: python training/ppe/entrenar.py",
              file=sys.stderr)
        return 1

    return calibrar_y_guardar(
        pesos,
        precision_minima=args.precision_minima,
        imgsz=args.imgsz,
        conf_cruda=args.conf_cruda,
        recalcular=args.recalcular,
        calibrar_de_nuevo=args.calibrar,
    )


if __name__ == "__main__":
    raise SystemExit(main())
