"""Cuánta confianza aguanta cada clase de AUSENCIA, mirando caja por caja.

  python training/ppe/umbral.py
  python training/ppe/umbral.py --precision-minima 0.8

── Ojo: esto NO es lo que calibra al módulo ────────────────────────────────

Lo que sale de acá describe al detector: qué tan seguido una caja `NO-Hardhat`
cae donde hay una cabeza descubierta anotada. Sirve para comparar dos modelos y
para saber si un reentrenamiento sirvió de algo.

Los umbrales con los que el módulo decide salen de otro lado:

    python training/ppe/evaluar_personas.py --calibrar

porque el módulo no emite cajas sino veredictos sobre personas, y las dos
métricas no coinciden. Dos cajas mal puestas sobre la misma persona son dos
errores acá y una sola alerta —correcta— en pantalla. Calibrando con esta vara,
el chaleco quedaba silenciado por no llegar a 0,70 de precisión, cuando medido
por persona da 0,83 y ve 3 de cada 4 faltas reales.

── Por qué no se elige a ojo ───────────────────────────────────────────────

Se puso 0,60 razonando que a la ausencia hay que pedirle más evidencia que a la
presencia, porque un falso "sin casco" acusa a alguien. El razonamiento está
bien; el número estaba mal. Medido sobre el split de prueba, ese umbral
descartaba 18 de cada 19 faltas detectadas: el módulo dejaba de avisar
prácticamente siempre.

El problema es que la confianza no significa lo mismo en todas las clases. El
modelo está seguro de un casco y mucho menos seguro de una cabeza descubierta,
así que un mismo número corta cosas distintas en cada una. La única forma de
elegirlo es mirar la curva de precisión de ESE modelo, en ESAS clases.

── Qué busca ───────────────────────────────────────────────────────────────

El umbral más bajo que todavía da la precisión pedida. Bajo es bueno: cuanto
más bajo, más faltas reales se ven. La precisión es el piso que no se negocia,
porque es la proporción de alertas que serían correctas, y una alerta
incorrecta acusa a una persona de algo que no hizo.

Hay que volver a correrlo cada vez que se reentrena: un modelo mejor permite
bajar el umbral y ver más faltas con la misma precisión.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

AQUI = Path(__file__).parent
DATOS = AQUI / "data" / "epp.yaml"
MODELOS = AQUI.parent / "models"

#: Las que disparan alertas. Son las únicas que importan acá.
AUSENCIAS = {
    "NO-Hardhat": "casco",
    "NO-Safety Vest": "chaleco",
    "NO-Goggles": "antiparras",
    "NO-Gloves": "guantes",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pesos", default=str(MODELOS / "epp.pt"))
    ap.add_argument("--precision-minima", type=float, default=0.70,
                    help="proporción mínima de alertas correctas que se acepta")
    ap.add_argument("--imgsz", type=int, default=512)
    args = ap.parse_args()

    pesos = Path(args.pesos)
    if not pesos.is_file():
        print(f"No hay modelo en {pesos}", file=sys.stderr)
        return 1

    from ultralytics import YOLO

    modelo = YOLO(str(pesos))
    nombres = modelo.names
    # Una sola pasada con umbral bajo: ultralytics devuelve la curva completa
    # de precisión y recall contra la confianza, así que no hace falta evaluar
    # una vez por cada umbral candidato.
    r = modelo.val(data=str(DATOS), split="test", device="cpu",
                   imgsz=args.imgsz, conf=0.001, verbose=False)

    ejes = getattr(r.box, "px", None)
    p_curva = getattr(r.box, "p_curve", None)
    r_curva = getattr(r.box, "r_curve", None)
    if ejes is None or p_curva is None:
        print("Esta versión de ultralytics no expone las curvas.", file=sys.stderr)
        return 1

    indices = {nombres[int(i)]: k for k, i in enumerate(r.box.ap_class_index)}

    print(f"Modelo: {pesos.name}   precisión mínima pedida: {args.precision_minima}\n")
    print(f"  {'elemento':<12} {'umbral':>7} {'precisión':>10} {'recall':>8}   qué significa")
    print("  " + "─" * 72)

    elegidos: dict[str, float] = {}
    for clase, etiqueta in AUSENCIAS.items():
        k = indices.get(clase)
        if k is None:
            print(f"  {etiqueta:<12} {'—':>7}   (sin ejemplos en el split de prueba)")
            continue

        p = p_curva[k]
        rc = r_curva[k]
        # El umbral más bajo que alcanza la precisión pedida: más bajo ve más
        # faltas reales, y la precisión es el piso que no se negocia.
        mejor = None
        for j in range(len(ejes)):
            if float(p[j]) >= args.precision_minima and float(rc[j]) > 0:
                mejor = j
                break
        if mejor is None:
            # Ninguna confianza alcanza esa precisión: la clase no está para
            # alertar. Decirlo es más útil que devolver un número que miente.
            j = int(max(range(len(ejes)), key=lambda x: float(p[x])))
            print(f"  {etiqueta:<12} {'—':>7} {float(p[j]):>10.3f} {float(rc[j]):>8.3f}"
                  f"   NO alcanza {args.precision_minima}: no conviene alertar todavía")
            continue

        u = float(ejes[mejor])
        prec = float(p[mejor])
        rec = float(rc[mejor])
        elegidos[etiqueta] = round(u, 2)
        print(f"  {etiqueta:<12} {u:>7.2f} {prec:>10.3f} {rec:>8.3f}"
              f"   ~{round(prec * 10)} de cada 10 alertas correctas, ve el {round(rec * 100)}%")

    print()
    if elegidos:
        sugerido = round(min(elegidos.values()), 2)
        print(f"  Para `minConfianzaFalta` (uno solo para todos): {sugerido}")
        print(f"  Por elemento, en `umbralPorElemento`: {json.dumps(elegidos, ensure_ascii=False)}")
    else:
        print("  Ninguna clase de ausencia está para alertar. Hay que entrenar más.")

    print("  Estos números describen al DETECTOR. Los umbrales con los que el")
    print("  módulo decide salen de medir el veredicto por persona:")
    print("      python training/ppe/evaluar_personas.py --calibrar\n")

    # Se guarda aparte de `umbrales`, que es lo que lee el módulo. Cuando esto
    # escribía ahí, calibraba la cámara con la métrica equivocada: por caja, el
    # chaleco no llegaba a la precisión pedida y quedaba silenciado, mientras
    # que por persona —que es lo que el módulo emite— da 0,83 y ve 3 de cada 4
    # faltas reales.
    ficha = MODELOS / "epp.json"
    datos = {}
    if ficha.is_file():
        try:
            datos = json.loads(ficha.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            datos = {}
    datos["curvaDetector"] = {
        "precisionMinima": args.precision_minima,
        "porElemento": elegidos,
    }
    MODELOS.mkdir(parents=True, exist_ok=True)
    ficha.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
