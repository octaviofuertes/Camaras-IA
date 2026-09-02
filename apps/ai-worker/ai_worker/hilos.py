"""Cuántos hilos usa cada modelo. Se importa ANTES que torch, a propósito.

`ultralytics` pone `OMP_NUM_THREADS=1` al importarse. Tiene sentido para
entrenar en varias GPU, donde cada proceso ya tiene la suya; acá, corriendo en
CPU, deja los modelos en un hilo de los ocho que hay. Medido en esta máquina,
con un cuadro de 1280×720 a imgsz 512:

    hilos   yolov8n-seg   yolov8n
      1        524 ms      467 ms
      4        325 ms      212 ms
      8        291 ms      275 ms

De 1 a 4 se cae casi a la mitad. De 4 a 8 ya no: son 8 núcleos lógicos sobre 4
físicos, y además hay un pipeline por cámara corriendo a la vez. Pedir los 8
para cada uno los hace pelearse por los mismos núcleos y termina peor. Por eso
el reparto por omisión es la mitad de los núcleos: dos cámaras entran justas.

`AI_WORKER_HILOS` lo pisa para una máquina con otra cantidad de núcleos o de
cámaras.
"""
from __future__ import annotations

import os


def elegir() -> int:
    """Cuántos hilos le toca a cada modelo."""
    pedido = os.environ.get("AI_WORKER_HILOS", "").strip()
    if pedido.isdigit() and int(pedido) > 0:
        return int(pedido)
    return max(1, (os.cpu_count() or 2) // 2)


def fijar() -> int:
    """Deja los hilos puestos y devuelve cuántos.

    Se llama dos veces: una acá, antes de importar torch —porque OpenMP lee la
    variable al arrancar y después ya no la mira—, y otra desde `reafirmar()`
    cuando los módulos ya cargaron, para deshacer el pisotón de ultralytics.
    """
    n = str(elegir())
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[var] = n
    return int(n)


def reafirmar() -> int:
    """Repone los hilos si algo los movió. Si ya están bien, no toca nada.

    Ultralytics los sube a `cpu_count()-1` la primera vez que corre cada camino
    de inferencia —`predict` y `track` son dos—, así que no alcanza con
    ponerlos una vez al arrancar: hay que poder revisarlo seguido.

    Por eso lo primero es LEER. `set_num_threads` rearma el pool de hilos de
    torch, y llamarlo desde los dos pipelines en cada cuadro lo rearmaba en
    medio de la inferencia del otro: el cuadro se fue de medio segundo a
    veinticinco. Leer, en cambio, no cuesta nada, y en régimen el número ya
    está bien y no se escribe nunca.
    """
    global ESCRITURAS
    n = elegir()
    try:
        import torch

        if torch.get_num_threads() != n:
            ESCRITURAS += 1
            for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS"):
                os.environ[var] = str(n)
            torch.set_num_threads(n)
    except Exception:  # noqa: BLE001
        # Sin torch no hay nada que ajustar y tampoco nada que romper.
        pass
    return n


#: Cuántas veces hubo que REESCRIBIR el número, no sólo leerlo. En régimen
#: tiene que quedarse quieto: si sube sin parar, algo lo repone en cada cuadro
#: y cada reposición rearma el pool de hilos de torch.
ESCRITURAS = 0

HILOS = fijar()
