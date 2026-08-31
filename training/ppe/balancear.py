"""Arma una lista de entrenamiento que no deje a las clases raras en minoría.

Se usa desde `entrenar.py`; también corre solo para ver el reparto:

  python training/ppe/balancear.py

── El problema, medido ─────────────────────────────────────────────────────

El dataset tiene tres o cuatro veces más ejemplos de "casco puesto" que de
"cabeza sin casco":

    Hardhat 3204   vs   NO-Hardhat  983
    Gloves  2794   vs   NO-Gloves  1056
    Goggles 1041   vs   NO-Goggles  830

Entrenando tal cual, el modelo aprende muy bien lo que sobra y casi nada de lo
que falta. Medido sobre el split de prueba: casco 0,81 de mAP contra 0,08 de
"sin casco". Para este módulo eso es lo peor que puede pasar, porque la alerta
se dispara con la AUSENCIA: un modelo que detecta cascos perfecto y no detecta
cabezas descubiertas no avisa nunca, y desde afuera se ve igual que uno roto.

── Qué hace ────────────────────────────────────────────────────────────────

Repite en la lista de entrenamiento las imágenes que contienen clases raras,
tantas veces como haga falta para que cada una se acerque a la más común. No
inventa datos: son las mismas fotos, pero el modelo las ve más seguido, y como
en cada época se les aplica un recorte y un color distintos, las repeticiones
no son idénticas entre sí.

Repetir imágenes tiene un costo y conviene decirlo: el modelo ve más veces las
mismas escenas y puede memorizarlas. Por eso el tope de repeticiones es bajo
(cuatro) y la validación queda SIN tocar, para que el número que se mira al
elegir el mejor modelo no esté inflado por las repeticiones.
"""
from __future__ import annotations

import collections
from pathlib import Path

RAIZ = Path(__file__).parent / "data"

#: Las que deciden si el módulo sirve. Las demás se entrenan igual pero no
#: guían el balanceo: `Boots` o `Person` sobran en todas las fotos.
RARAS = ("NO-Hardhat", "NO-Safety Vest", "NO-Goggles", "NO-Gloves")

#: Cuántas veces, como mucho, se puede repetir una imagen.
TOPE = 4


def clases_del_yaml(yaml: Path) -> list[str]:
    nombres: list[str] = []
    for linea in yaml.read_text(encoding="utf-8").splitlines():
        s = linea.strip()
        if s and s[0].isdigit() and ":" in s:
            nombres.append(s.split(":", 1)[1].strip())
    return nombres


def _clases_por_imagen(split: str, clases: list[str]) -> dict[Path, set[str]]:
    salida: dict[Path, set[str]] = {}
    carpeta = RAIZ / split
    for etiqueta in (carpeta / "labels").glob("*.txt"):
        imagen = next(
            (p for p in (carpeta / "images").glob(etiqueta.stem + ".*")), None
        )
        if imagen is None:
            continue
        vistas: set[str] = set()
        for linea in etiqueta.read_text(encoding="utf-8").splitlines():
            p = linea.split()
            if not p:
                continue
            try:
                i = int(p[0])
            except ValueError:
                continue
            if 0 <= i < len(clases):
                vistas.add(clases[i])
        salida[imagen] = vistas
    return salida


def repeticiones(por_imagen: dict[Path, set[str]]) -> dict[Path, int]:
    """Cuántas veces entra cada imagen en la lista de entrenamiento."""
    cuenta: collections.Counter[str] = collections.Counter()
    for vistas in por_imagen.values():
        cuenta.update(vistas)
    if not cuenta:
        return {}

    # La referencia es la clase más común de las que importan: se busca que las
    # raras se le acerquen, no que todas queden exactamente iguales. Igualarlas
    # del todo exigiría repeticiones altísimas y el modelo terminaría
    # memorizando un puñado de fotos.
    referencia = max(cuenta.values())

    factor: dict[str, int] = {}
    for clase in RARAS:
        n = cuenta.get(clase, 0)
        factor[clase] = min(TOPE, max(1, round(referencia / n))) if n else 1

    salida: dict[Path, int] = {}
    for imagen, vistas in por_imagen.items():
        # Si una imagen tiene varias clases raras, manda la que más necesita.
        salida[imagen] = max((factor.get(c, 1) for c in vistas), default=1)
    return salida


def escribir_lista(destino: Path, veces: dict[Path, int]) -> int:
    lineas: list[str] = []
    for imagen, n in sorted(veces.items()):
        lineas.extend([imagen.resolve().as_posix()] * n)
    destino.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return len(lineas)


def preparar(yaml: Path) -> tuple[Path, dict]:
    """Deja la lista escrita y devuelve (ruta, resumen)."""
    clases = clases_del_yaml(yaml)
    por_imagen = _clases_por_imagen("train", clases)
    veces = repeticiones(por_imagen)
    lista = RAIZ / "train_balanceado.txt"
    total = escribir_lista(lista, veces)

    antes: collections.Counter[str] = collections.Counter()
    despues: collections.Counter[str] = collections.Counter()
    for imagen, vistas in por_imagen.items():
        antes.update(vistas)
        for c in vistas:
            despues[c] += veces[imagen]

    return lista, {
        "imagenes": len(por_imagen),
        "entradas": total,
        "antes": dict(antes),
        "despues": dict(despues),
    }


def main() -> int:
    yaml = RAIZ / "epp.yaml"
    if not yaml.is_file():
        print("Falta el dataset. Corré: python training/ppe/descargar.py")
        return 1
    lista, r = preparar(yaml)
    print(f"{r['imagenes']} imágenes -> {r['entradas']} entradas ({lista.name})\n")
    print(f"{'clase':<18} {'en cuántas fotos':>17} {'después de repetir':>19}")
    for c in sorted(set(r["antes"]) | set(r["despues"])):
        print(f"{c:<18} {r['antes'].get(c, 0):>17} {r['despues'].get(c, 0):>19}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
