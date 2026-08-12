"""Control de accesos: quién pasó, a qué hora, y si podía estar acá.

QUÉ RESUELVE
------------
Un cuadro por segundo de "Juan está en el cuadro" no es un control de accesos:
es ruido. Lo que sirve leer es "Juan estuvo entre las 09:14 y las 09:52". Este
módulo convierte una cosa en la otra.

También decide cuándo suena la alerta de alguien sin acceso. Ahí el criterio es
distinto al del registro: una alerta que se repite cada frame es una alerta que
se deja de mirar, y una que suena una sola vez se pierde si el operador no
estaba. Se avisa al entrar y se repite recién si sigue adentro pasado un rato.

POR QUÉ NO SE CIERRA EL PASO AL PRIMER FRAME QUE FALTA
------------------------------------------------------
La gente se tapa entre sí, se agacha, sale del cuadro un segundo. Cerrar el paso
en cuanto deja de vérsela partiría la jornada de una persona en decenas de
entradas y salidas, y el registro sería ilegible. Se espera una tolerancia antes
de dar por terminado el paso.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ConfigAccesos:
    """Parámetros del registro de pasos y de la alerta."""

    # Cuánto puede desaparecer alguien del cuadro sin que se cierre su paso.
    # Cubre que se tape, se agache o salga un momento. Más largo pega dos
    # visitas en una; más corto parte una visita en muchas.
    cierreSegundos: float = 90.0

    # Cuánto tiene que estar presente antes de registrar el paso. Evita que
    # alguien que cruza el cuadro camino a otro lado deje una entrada.
    minimoParaRegistrarSegundos: float = 3.0

    # Cada cuánto se repite la alerta de alguien sin acceso que sigue adentro.
    # Cero la emitiría en cada frame y nadie miraría ninguna.
    repetirAlertaSegundos: float = 300.0

    # Cada cuánto se reporta un paso que TODAVÍA está abierto.
    #
    # Sin esto el paso sólo se guardaría al cerrarse, y a alguien que lleva ocho
    # horas adentro no se lo vería en el registro hasta que se fuera. La primera
    # pregunta que se le hace a un control de accesos es quién está adentro
    # AHORA, así que el paso se persiste mientras ocurre y se va extendiendo.
    reporteSegundos: float = 30.0


@dataclass
class Paso:
    """Una presencia continua de una persona frente a una cámara."""
    persona_id: str
    nombre: str
    desde: float
    hasta: float
    mejor_parecido: float = 0.0
    visto_por_rostro: bool = False
    tenia_acceso: bool = True
    # Cuándo se avisó por última vez que esta persona no tenía acceso.
    ultima_alerta: float = 0.0
    # Cuándo se reportó por última vez, para no mandar uno por frame.
    ultimo_reporte: float = 0.0


@dataclass
class Cierre:
    """Un paso que terminó y hay que persistir."""
    paso: Paso


class RegistroDePasos:
    """Agrupa apariciones sueltas en pasos, y decide cuándo alertar."""

    def __init__(self, cfg: ConfigAccesos | None = None) -> None:
        self.cfg = cfg or ConfigAccesos()
        self._abiertos: dict[str, Paso] = {}

    # ── registro ────────────────────────────────────────────────────
    def ver(
        self,
        persona_id: str,
        nombre: str,
        ahora: float,
        parecido: float = 0.0,
        por_rostro: bool = False,
        tiene_acceso: bool = True,
    ) -> Paso:
        """Registra que se vio a esta persona. Devuelve su paso en curso."""
        p = self._abiertos.get(persona_id)
        if p is None or ahora - p.hasta > self.cfg.cierreSegundos:
            p = Paso(persona_id=persona_id, nombre=nombre, desde=ahora, hasta=ahora)
            self._abiertos[persona_id] = p

        p.nombre = nombre or p.nombre
        p.hasta = max(p.hasta, ahora)
        p.mejor_parecido = max(p.mejor_parecido, parecido)
        p.visto_por_rostro = p.visto_por_rostro or por_rostro
        # El acceso se congela al abrir el paso: si se le quita el permiso
        # mientras está adentro, lo que pasó hasta ahí pasó con permiso.
        if p.desde == p.hasta:
            p.tenia_acceso = tiene_acceso
        return p

    def toca_reportar(self, paso: Paso, ahora: float) -> bool:
        """¿Hay que persistir este paso abierto?

        La primera vez sí: es lo que hace que la persona aparezca en el registro
        apenas entra. Después, cada tanto, para ir corriendo su hora de salida
        sin escribir en la base una vez por frame.
        """
        if paso.ultimo_reporte <= 0.0 or ahora - paso.ultimo_reporte >= self.cfg.reporteSegundos:
            paso.ultimo_reporte = ahora
            return True
        return False

    def cerrar_vencidos(self, ahora: float) -> list[Paso]:
        """Cierra los pasos de quienes ya no están. Devuelve los cerrados."""
        cerrados = []
        for pid, p in list(self._abiertos.items()):
            if ahora - p.hasta > self.cfg.cierreSegundos:
                del self._abiertos[pid]
                if self._vale_la_pena(p):
                    cerrados.append(p)
        return cerrados

    def cerrar_todo(self) -> list[Paso]:
        """Al soltar la cámara: lo que quedó abierto igual pasó."""
        cerrados = [p for p in self._abiertos.values() if self._vale_la_pena(p)]
        self._abiertos.clear()
        return cerrados

    def _vale_la_pena(self, p: Paso) -> bool:
        return p.hasta - p.desde >= self.cfg.minimoParaRegistrarSegundos

    # ── alerta ──────────────────────────────────────────────────────
    def debe_alertar(self, paso: Paso, ahora: float, tiene_acceso: bool) -> bool:
        """¿Hay que avisar que esta persona no tiene acceso?

        Al entrar, sí. Después, sólo cada tanto: repetirla en cada frame la
        vuelve ruido y el operador deja de mirarlas, que es lo contrario de lo
        que se busca con una alerta urgente.

        El acceso que se mira acá es el de AHORA, no el que tenía cuando entró.
        Son dos cosas distintas y confundirlas rompe el caso más importante:
        si a alguien se le quita el acceso mientras está adentro, lo que hay que
        saber es que está adentro AHORA. Lo que se congela es el registro —lo
        que hizo con permiso lo hizo con permiso—, no la alarma.
        """
        if tiene_acceso:
            return False
        if paso.ultima_alerta <= 0.0:
            paso.ultima_alerta = ahora
            return True
        if ahora - paso.ultima_alerta >= self.cfg.repetirAlertaSegundos:
            paso.ultima_alerta = ahora
            return True
        return False

    # ── diagnóstico ─────────────────────────────────────────────────
    @property
    def en_curso(self) -> list[Paso]:
        return list(self._abiertos.values())

    def estado(self) -> dict:
        return {
            "pasosEnCurso": len(self._abiertos),
            "sinAccesoAdentro": sum(1 for p in self._abiertos.values() if not p.tenia_acceso),
        }
