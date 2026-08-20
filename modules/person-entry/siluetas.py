"""Las siluetas de la gente que ve la cámara, para dibujarlas en pantalla.

La diferencia con una caja es la que se ve: un rectángulo alrededor de una
persona incluye pared, escritorio y medio compañero de al lado. Cuando eso se
pinta de verde para decir "esta persona tiene acceso", lo que queda pintado es
un pedazo de la oficina, y con dos personas cerca las cajas se superponen y ya
no se sabe cuál está marcada.

Un polígono que sigue el contorno del cuerpo no tiene ese problema, y es lo que
hace que la marca se lea como "esta persona" y no como "esta zona".

El costo es real y hay que decirlo: el modelo de segmentación tarda alrededor
del doble que el de detección por frame. Por eso es opcional, y sin él el
sistema sigue funcionando con la caja de siempre.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Silueta:
    """El contorno de una persona, en fracciones de la imagen (0..1)."""

    track_id: int
    #: Pares (x, y) en orden, listos para dibujar un polígono.
    puntos: tuple[tuple[float, float], ...]

    def como_lista(self) -> list[list[float]]:
        return [[round(x, 4), round(y, 4)] for x, y in self.puntos]


def simplificar(puntos: list, maximo: int = 48) -> tuple[tuple[float, float], ...]:
    """Deja como mucho `maximo` puntos, repartidos parejo por el contorno.

    El modelo devuelve contornos de varios cientos de puntos. A tamaño de
    pantalla no se distingue de uno de cuarenta, pero la diferencia sí se nota
    en lo que viaja por la red: son varias personas, varias veces por segundo, y
    ese caudal es lo que decide si el dibujo va al ritmo de la cámara o atrasado.

    Se recorre a paso fijo en vez de descartar los puntos "poco importantes"
    (Douglas-Peucker y parecidos) porque acá alcanza: un cuerpo no tiene
    detalles finos que valga la pena conservar a 300 píxeles de alto, y un paso
    fijo no puede deformar la silueta de forma impredecible entre un frame y el
    siguiente, que es lo que haría temblar el dibujo.
    """
    n = len(puntos)
    if n == 0:
        return ()
    if n <= maximo:
        return tuple((float(p[0]), float(p[1])) for p in puntos)

    paso = n / maximo
    salida = []
    for i in range(maximo):
        p = puntos[int(i * paso)]
        salida.append((float(p[0]), float(p[1])))
    return tuple(salida)


def siluetas_de(resultado, track_ids: list[int], maximo: int = 48) -> dict[int, Silueta]:
    """Empareja cada contorno con el track al que pertenece.

    El modelo devuelve las máscaras en el mismo orden que las cajas, así que el
    emparejamiento es posicional. Se hace acá y no en el módulo para que quede a
    la vista que depende de ese orden: si un día dejara de cumplirse, cada
    persona quedaría pintada con el contorno de otra, que es un error que se ve
    raro pero no rompe nada y podría durar meses.
    """
    mascaras = getattr(resultado, "masks", None)
    if mascaras is None:
        return {}
    contornos = getattr(mascaras, "xyn", None)
    if contornos is None:
        return {}

    salida: dict[int, Silueta] = {}
    for i, tid in enumerate(track_ids):
        if tid < 0 or i >= len(contornos):
            continue
        puntos = simplificar(list(contornos[i]), maximo)
        if len(puntos) >= 3:
            salida[tid] = Silueta(track_id=tid, puntos=puntos)
    return salida
