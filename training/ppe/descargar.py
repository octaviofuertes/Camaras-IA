"""Baja el dataset público de elementos de protección personal (EPP).

  python training/ppe/descargar.py              # todo
  python training/ppe/descargar.py --limite 60  # una muestra para probar

Fuente: https://universe.roboflow.com/himanshu-bharati/ppe_dectection-dtt4q
Copia usada: https://huggingface.co/datasets/HB1204/PPE_Detection
Licencia: CC BY 4.0 (uso libre citando la fuente).

── Por qué este dataset y no SH17 ──────────────────────────────────────────

SH17 es el más citado y tiene 8.099 imágenes, pero la copia pública que se
consigue perdió los nombres de las clases: quedaron numeradas del 0 al 16, y
al verificarlas contra su geometría no coincidían con el orden documentado
—"cara-guardia" ocupaba el 25% de la imagen y los pies aparecían en el tercio
superior—. Entrenar con eso es enseñarle al modelo etiquetas cambiadas, y el
error no se ve hasta que el sistema alerta por lo que no es.

Éste trae las clases nombradas y sus etiquetas pasan la misma verificación
(`verificar.py`): las antiparras caen a 0,17 de altura, el casco a 0,26 justo
encima, el chaleco a 0,42 y las botas a 0,70. Cada cosa donde va.

── Lo que lo hace mejor para alertar ───────────────────────────────────────

Tiene las clases NEGATIVAS anotadas: `NO-Hardhat`, `NO-Safety Vest`,
`NO-Gloves`, `NO-Goggles`. Eso significa que alguien etiquetó a mano cabezas
sin casco y torsos sin chaleco, y el modelo aprende a ver la AUSENCIA en vez de
deducirla. La diferencia importa: deducir "no tiene casco" porque no se detectó
uno confunde "no lo tiene puesto" con "no lo vi", que es el error que llena de
alertas falsas a un operador y termina con el módulo apagado.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import requests

REPO = "HB1204/PPE_Detection"
API = f"https://huggingface.co/api/datasets/{REPO}"
BASE = f"https://huggingface.co/datasets/{REPO}/resolve/main/"
RAIZ = Path(__file__).parent / "data"

#: Cómo se llama cada clase en el dataset, en orden. Es el contrato con las
#: etiquetas: cambiar el orden acá desplaza todas las anotaciones.
CLASES = [
    "Boots", "Gloves", "Goggles", "Hardhat", "NO-Gloves", "NO-Goggles",
    "NO-Hardhat", "NO-Safety Vest", "None", "Person", "Safety Vest",
]


def bajar_con_hub() -> bool:
    """Trae el dataset con la biblioteca oficial de HuggingFace.

    Es el camino preferido y no un adorno: bajando los seis mil archivos a mano
    el servidor empieza a responder 429 ("vas muy rápido") y se perdían casi mil
    imágenes. Peor que perderlas era que se perdían en silencio y el
    entrenamiento arrancaba igual con un dataset incompleto.

    `snapshot_download` usa el CDN, reanuda lo que ya está y respeta los
    límites del servidor. Si la biblioteca no está instalada se cae al método
    HTTP de más abajo, que funciona pero es más frágil.
    """
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("  (huggingface_hub no está instalado: se usa descarga directa)")
        return False

    print("  bajando con huggingface_hub…")
    snapshot_download(
        repo_id=REPO,
        repo_type="dataset",
        local_dir=str(RAIZ),
        allow_patterns=["train/*", "valid/*", "test/*"],
        max_workers=4,
    )
    return True


def listar_archivos() -> list[str]:
    r = requests.get(API, timeout=60)
    r.raise_for_status()
    datos = r.json()
    return [s["rfilename"] for s in datos.get("siblings", [])]


def bajar(archivos: list[str], limite: int | None) -> int:
    """Trae los archivos que falten. Reanudable: lo ya bajado no se vuelve a pedir."""
    # Se ordena para que imágenes y etiquetas del mismo split vayan juntas: con
    # --limite se quiere una muestra usable, no 60 etiquetas sin sus imágenes.
    pares: dict[str, dict[str, str]] = {}
    for f in archivos:
        partes = f.split("/")
        if len(partes) != 3 or partes[1] not in ("images", "labels"):
            continue
        split, tipo, nombre = partes
        clave = f"{split}/{Path(nombre).stem}"
        pares.setdefault(clave, {})[tipo] = f

    completos = [p for p in pares.values() if "images" in p and "labels" in p]
    completos.sort(key=lambda p: p["images"])
    if limite:
        # Se toma de cada split, no los primeros N: quedarse sólo con el
        # principio de `train` dejaría validación y prueba vacías.
        por_split: dict[str, list] = {}
        for p in completos:
            por_split.setdefault(p["images"].split("/")[0], []).append(p)
        completos = [x for lista in por_split.values() for x in lista[: max(1, limite // 3)]]

    pendientes = [f for par in completos for f in (par["images"], par["labels"])]
    total = len(pendientes)
    estado = {"bajados": 0, "hechos": 0}
    candado = Lock()
    # Una sesión compartida reusa la conexión TLS: son miles de archivos chicos
    # y el apretón de manos costaba más que la descarga.
    sesion = requests.Session()

    def traer(f: str) -> None:
        destino = RAIZ / f
        if not (destino.is_file() and destino.stat().st_size > 0):
            destino.parent.mkdir(parents=True, exist_ok=True)
            # HTTP 429 es "vas muy rápido", no "no existe": se espera y se
            # reintenta. Sin esto se perdían mil imágenes en silencio y el
            # dataset quedaba incompleto sin que nada lo dijera.
            for intento in range(5):
                try:
                    r = sesion.get(BASE + f, timeout=90)
                except requests.RequestException as exc:
                    print(f"  ! {f}: {exc}", file=sys.stderr)
                    return
                if r.status_code == 200:
                    destino.write_bytes(r.content)
                    with candado:
                        estado["bajados"] += 1
                    break
                if r.status_code in (429, 503):
                    espera = float(r.headers.get("Retry-After") or (2 ** intento))
                    time.sleep(min(espera, 30.0))
                    continue
                print(f"  ! {f}: HTTP {r.status_code}", file=sys.stderr)
                return
            else:
                print(f"  ! {f}: sigue limitado tras 5 intentos", file=sys.stderr)
        with candado:
            estado["hechos"] += 1
            if estado["hechos"] % 400 == 0 or estado["hechos"] == total:
                print(f"  {estado['hechos']}/{total} archivos ({estado['bajados']} nuevos)", flush=True)

    # Ocho a la vez: son archivos chicos y el cuello de botella es la latencia,
    # no el ancho de banda. Más hilos no aceleran y empiezan a dar 429.
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(traer, pendientes))
    return estado["bajados"]


def escribir_yaml() -> Path:
    """El data.yaml que lee ultralytics. Se escribe acá y no se baja.

    El del repositorio original apunta a rutas de la máquina de quien lo armó
    (`/content/drive/MyDrive/...`), así que hay que reescribirlo igual.
    """
    ruta = RAIZ / "epp.yaml"
    nombres = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASES))
    ruta.write_text(
        f"# Generado por training/ppe/descargar.py — no editar a mano.\n"
        f"path: {RAIZ.resolve().as_posix()}\n"
        f"train: train/images\n"
        f"val: valid/images\n"
        f"test: test/images\n"
        f"nc: {len(CLASES)}\n"
        f"names:\n{nombres}\n",
        encoding="utf-8",
    )
    return ruta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limite", type=int, default=None,
                    help="baja sólo unos pocos pares por split, para probar el pipeline")
    args = ap.parse_args()

    print(f"Bajando {REPO}…")
    if args.limite:
        archivos = listar_archivos()
        print(f"  {len(archivos)} archivos en el repositorio")
        nuevos = bajar(archivos, args.limite)
    elif bajar_con_hub():
        nuevos = -1
    else:
        archivos = listar_archivos()
        nuevos = bajar(archivos, None)
    yaml = escribir_yaml()

    for split in ("train", "valid", "test"):
        imgs = list((RAIZ / split / "images").glob("*")) if (RAIZ / split / "images").is_dir() else []
        print(f"  {split:<6} {len(imgs):>5} imágenes")
    print(f"\n{nuevos} archivos nuevos. Configuración en {yaml}")
    print("Ahora: python training/ppe/verificar.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
