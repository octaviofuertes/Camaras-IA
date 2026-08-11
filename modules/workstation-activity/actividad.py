"""Contabilidad de tiempo por puesto de trabajo. Sin dependencias de YOLO.

QUÉ MIDE, Y QUÉ NO
------------------
Mide el PUESTO, no a la persona. Acumula, por zona:

  ocupado  — hay al menos una persona dentro del polígono del puesto
  teléfono — hay al menos una persona con un teléfono asociado a su cuerpo
  vacío    — no hay nadie

No guarda quién estuvo ahí ni permite reconstruirlo: el identificador de
seguimiento no entra en la contabilidad. Es una decisión de diseño, no una
limitación — el sistema no tiene forma de saber quién es cada persona (los
identificadores del tracker se reasignan constantemente), y un informe por
puesto responde igual las preguntas operativas que importan: cuánto se usa cada
posición, en qué franjas, con qué carga.

CÓMO SE CUENTA EL TIEMPO
------------------------
Con el tiempo real entre frames, no multiplicando frames por un fps supuesto. Si
la cámara se corta o el pipeline se atrasa, los segundos que no se observaron NO
se cuentan: quedan registrados aparte como tiempo sin cobertura. Un informe que
rellena los huecos con suposiciones es peor que uno que dice "de esta hora vi 52
minutos".

HONESTIDAD DE LA MEDICIÓN
-------------------------
El tiempo de "teléfono" es el más débil de los tres y el módulo lo declara: sólo
cuenta cuando el teléfono se VE. Alguien mirando el teléfono apoyado sobre el
escritorio, o de espaldas a la cámara, no se detecta. Por eso el informe reporta
el tiempo de teléfono como una COTA INFERIOR y no como un total.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ConfigActividad:
    """Parámetros de la contabilidad."""

    # Cada cuánto se cierra una ventana y se emite una muestra. Ventanas cortas
    # dan más resolución en el informe y más filas; 60 s es el equilibrio para
    # informes por hora y por turno.
    windowSeconds: float = 60.0

    # Confianza mínima para creer que hay una persona.
    personConfidence: float = 0.45
    # El teléfono es un objeto chico y se detecta peor: exigirle la misma
    # confianza que a una persona lo volvería invisible. Se compensa después
    # tratando su tiempo como cota inferior.
    phoneConfidence: float = 0.30

    # Fracción superior del cuerpo donde tiene sentido que aparezca un teléfono
    # que se está usando. Un teléfono en el piso o sobre el escritorio lejos del
    # cuerpo no cuenta como uso.
    phoneBodyTop: float = 0.65
    # Cuánto puede sobresalir el teléfono del cuerpo y seguir contando: la mano
    # extendida saca el teléfono fuera del recuadro.
    phoneMargin: float = 0.12

    # Observaciones seguidas con teléfono antes de empezar a contarlo.
    #
    # A la distancia a la que trabaja una cámara de oficina, un teléfono son unas
    # decenas de píxeles y la confianza del detector queda cerca del ruido:
    # medido en esta cámara sin ningún teléfono presente, el candidato más alto
    # dio 0.29 contra un umbral de 0.30. Un solo cuadro equivocado le sumaría
    # segundos a una persona por algo que no hizo.
    #
    # Un teléfono real se sostiene varios segundos; un falso positivo parpadea.
    # Exigir que se repita es lo que separa una cosa de la otra, y es el mismo
    # criterio con el que el detector de caídas no alerta por un solo frame.
    phonePersistFrames: int = 2

    # Si entre dos frames pasó más que esto, se considera que hubo un corte y el
    # intervalo no se le atribuye a ningún estado. Ver `sin_cobertura`.
    maxGapSeconds: float = 5.0


@dataclass
class Caja:
    """Recuadro normalizado 0..1."""
    x: float
    y: float
    w: float
    h: float
    confianza: float = 0.0

    @property
    def centro(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.h / 2)

    @property
    def pies(self) -> tuple[float, float]:
        """Punto de apoyo: es lo que define en qué zona del piso está alguien.

        Se usa el borde inferior y no el centro porque las zonas son regiones
        del SUELO. Alguien parado justo afuera del borde de un puesto tiene el
        centro del cuerpo adentro y los pies afuera; lo que corresponde es dónde
        está parado.
        """
        return (self.x + self.w / 2, self.y + self.h)


@dataclass
class Zona:
    """Un puesto de trabajo: un polígono con nombre."""
    id: str
    nombre: str
    poligono: list[tuple[float, float]]

    def contiene(self, punto: tuple[float, float]) -> bool:
        # Sin polígono la zona es toda la imagen: es el caso de una cámara que
        # apunta a un solo puesto y a nadie le hace falta dibujar nada.
        if len(self.poligono) < 3:
            return True
        return _punto_en_poligono(punto, self.poligono)


@dataclass
class VentanaZona:
    """Lo acumulado de una zona en la ventana en curso."""
    zona_id: str
    zona_nombre: str
    ocupado_s: float = 0.0
    telefono_s: float = 0.0
    vacio_s: float = 0.0
    sin_cobertura_s: float = 0.0
    max_personas: int = 0
    # Cuenta de la observación anterior. El pico sólo se acepta si se repite en
    # dos frames seguidos: ver `_actualizar_pico`.
    _ultima_cuenta: int = 0
    # Suma de personas × segundos: dividida por el tiempo observado da la
    # ocupación media, que es más representativa que el pico.
    persona_segundos: float = 0.0
    # Tiempo atribuido a cada persona identificada. La clave None junta a
    # todos los presentes sin identificar: su tiempo se reporta aparte y NO
    # se reparte entre los identificados — repartirlo le sumaría a un
    # empleado minutos que quizá fueron de un visitante.
    por_persona: dict = field(default_factory=dict)

    def _actualizar_pico(self, cuenta: int) -> None:
        """Registra el pico de personas, exigiendo que se sostenga dos frames.

        El detector produce recuadros parciales de la misma persona —medio
        cuerpo, un reflejo— que a veces cruzan el umbral de confianza por un
        instante. Tomando el máximo instantáneo, ese parpadeo queda en el
        informe como si hubiera habido más gente: con tres personas frente a la
        cámara se registró un pico de cinco.

        Pedir que la cuenta se repita en dos observaciones seguidas descarta el
        parpadeo sin perder una entrada real, que dura bastante más que dos
        frames. El tiempo ocupado y la ocupación media no dependían de esto —el
        primero sólo necesita que haya alguien, la segunda promedia— pero el
        pico se lee como un dato duro y tiene que serlo.
        """
        sostenida = min(cuenta, self._ultima_cuenta)
        if sostenida > self.max_personas:
            self.max_personas = sostenida
        self._ultima_cuenta = cuenta

    @property
    def observado_s(self) -> float:
        return self.ocupado_s + self.vacio_s

    def reiniciar(self) -> None:
        self.ocupado_s = self.telefono_s = self.vacio_s = 0.0
        self.sin_cobertura_s = 0.0
        self.max_personas = 0
        self._ultima_cuenta = 0
        self.persona_segundos = 0.0
        self.por_persona = {}


@dataclass
class Observacion:
    """Lo que el módulo ve en un frame, ya sin depender del detector.

    `identidades` empareja por índice con `personas`: identidades[i] es quién es
    personas[i], o None si no se lo pudo identificar. Es lo que permite decir
    "Juan estuvo 1 h con el teléfono" en vez de repartir ese tiempo entre todos
    los que estaban en el puesto.
    """
    ts: float
    personas: list[Caja] = field(default_factory=list)
    telefonos: list[Caja] = field(default_factory=list)
    identidades: list[tuple[str, str] | None] = field(default_factory=list)

    def identidad_de(self, i: int) -> tuple[str, str] | None:
        return self.identidades[i] if i < len(self.identidades) else None


@dataclass
class TiempoPersona:
    """Tiempo atribuido a una persona concreta dentro de una zona."""
    persona_id: str | None      # None = presente pero no identificada
    nombre: str
    presente_s: float = 0.0
    telefono_s: float = 0.0


@dataclass
class MuestraPersona:
    """Lo medido de una persona en una ventana, listo para persistir."""
    zona_id: str
    zona_nombre: str
    persona_id: str | None
    nombre: str
    desde: float
    hasta: float
    presente_s: float
    telefono_s: float


@dataclass
class MuestraZona:
    """Una ventana cerrada, lista para persistir."""
    zona_id: str
    zona_nombre: str
    desde: float
    hasta: float
    ocupado_s: float
    telefono_s: float
    vacio_s: float
    sin_cobertura_s: float
    max_personas: int
    ocupacion_media: float


class ContadorActividad:
    """Acumula tiempo por zona y cierra ventanas periódicamente."""

    def __init__(self, zonas: list[Zona], config: ConfigActividad | None = None) -> None:
        self.cfg = config or ConfigActividad()
        # Sin zonas configuradas se asume que la cámara mira un solo puesto. El
        # módulo tiene que servir sin configurar nada; las zonas lo mejoran.
        self.zonas = zonas or [Zona(id="", nombre="Toda la cámara", poligono=[])]
        self._ventanas = {z.id: VentanaZona(z.id, z.nombre) for z in self.zonas}
        self._ultimo_ts: float | None = None
        self._inicio_ventana: float | None = None
        # Muestras por persona de la última ventana cerrada. Van aparte de
        # las de zona porque son datos de distinta naturaleza: una mide una
        # posición de trabajo, la otra atribuye tiempo a un individuo.
        self.ultimas_personas: list[MuestraPersona] = []
        # (zona, persona) -> cuántas observaciones seguidas se le ve el teléfono.
        # Es lo que impide que un solo cuadro dudoso le sume tiempo a alguien.
        self._racha_telefono: dict[tuple, int] = {}

    # ── medición ────────────────────────────────────────────────────
    def observar(self, obs: Observacion) -> list[MuestraZona]:
        """Registra un frame. Devuelve muestras si se cerró una ventana."""
        cfg = self.cfg

        if self._ultimo_ts is None or self._inicio_ventana is None:
            self._ultimo_ts = obs.ts
            self._inicio_ventana = obs.ts
            return []

        dt = obs.ts - self._ultimo_ts
        self._ultimo_ts = obs.ts

        if dt < 0:
            # El reloj retrocedió (reconexión de cámara). No se puede atribuir
            # tiempo negativo a nada: se reinicia la ventana en curso para no
            # mezclar dos tramos temporales distintos en la misma muestra.
            self._inicio_ventana = obs.ts
            for v in self._ventanas.values():
                v.reiniciar()
            return []

        indexadas = [
            (i, p) for i, p in enumerate(obs.personas) if p.confianza >= cfg.personConfidence
        ]
        personas_validas = [p for _, p in indexadas]
        telefonos_validos = [t for t in obs.telefonos if t.confianza >= cfg.phoneConfidence]

        hubo_corte = dt > cfg.maxGapSeconds
        for zona in self.zonas:
            v = self._ventanas[zona.id]
            if hubo_corte:
                # No se vio nada en ese intervalo: no se le atribuye estado.
                # Rellenarlo con el último estado conocido sería inventar datos
                # en un informe, que es exactamente lo que no puede pasar.
                v.sin_cobertura_s += dt
                continue

            dentro = [(i, p) for i, p in indexadas if zona.contiene(p.pies)]
            v._actualizar_pico(len(dentro))
            if dentro:
                v.ocupado_s += dt
                v.persona_segundos += len(dentro) * dt
                algun_telefono = False
                vistos_ahora: set = set()
                for i, p in dentro:
                    crudo = _telefono_en_uso(p, telefonos_validos, cfg)
                    # La persistencia se cuenta por persona, no por puesto: dos
                    # personas al lado no pueden turnarse el mismo contador.
                    ident_p = obs.identidad_de(i)
                    llave = (zona.id, ident_p[0] if ident_p else f"pos{round(p.x, 2)}")
                    if crudo:
                        vistos_ahora.add(llave)
                        self._racha_telefono[llave] = self._racha_telefono.get(llave, 0) + 1
                    con_tel = crudo and self._racha_telefono.get(llave, 0) >= cfg.phonePersistFrames
                    algun_telefono = algun_telefono or con_tel

                    # A cada quien lo suyo: el teléfono se le atribuye a la
                    # persona sobre cuyo cuerpo se detectó, no a todos los que
                    # estaban en el puesto.
                    ident = ident_p
                    clave = ident[0] if ident else None
                    tp = v.por_persona.get(clave)
                    if tp is None:
                        tp = TiempoPersona(clave, ident[1] if ident else "Sin identificar")
                        v.por_persona[clave] = tp
                    tp.presente_s += dt
                    if con_tel:
                        tp.telefono_s += dt

                if algun_telefono:
                    v.telefono_s += dt

                # Se corta la racha de quien dejó de tener el teléfono a la
                # vista: si no, un falso positivo de hace un minuto seguiría
                # habilitando el conteo del siguiente.
                for llave in [k for k in self._racha_telefono if k[0] == zona.id and k not in vistos_ahora]:
                    del self._racha_telefono[llave]
            else:
                v.vacio_s += dt
                for llave in [k for k in self._racha_telefono if k[0] == zona.id]:
                    del self._racha_telefono[llave]

        if obs.ts - self._inicio_ventana >= cfg.windowSeconds:
            return self._cerrar_ventana(obs.ts)
        return []

    def cerrar_pendiente(self, ts: float) -> list[MuestraZona]:
        """Cierra la ventana en curso aunque no haya llegado a su duración.

        Se usa al apagar el módulo o al soltar la cámara: sin esto, el último
        tramo observado se perdería, y en un informe eso se ve como un hueco que
        nadie puede explicar.
        """
        if self._inicio_ventana is None or ts <= self._inicio_ventana:
            return []
        return self._cerrar_ventana(ts)

    def _cerrar_ventana(self, ts: float) -> list[MuestraZona]:
        desde = self._inicio_ventana or ts
        muestras = []
        self.ultimas_personas = []
        for zona in self.zonas:
            v = self._ventanas[zona.id]
            observado = v.observado_s
            if observado <= 0 and v.sin_cobertura_s <= 0:
                continue
            muestras.append(
                MuestraZona(
                    zona_id=v.zona_id,
                    zona_nombre=v.zona_nombre,
                    desde=desde,
                    hasta=ts,
                    ocupado_s=round(v.ocupado_s, 2),
                    telefono_s=round(v.telefono_s, 2),
                    vacio_s=round(v.vacio_s, 2),
                    sin_cobertura_s=round(v.sin_cobertura_s, 2),
                    max_personas=v.max_personas,
                    ocupacion_media=round(v.persona_segundos / observado, 2) if observado > 0 else 0.0,
                )
            )
            for tp in v.por_persona.values():
                if tp.presente_s <= 0:
                    continue
                self.ultimas_personas.append(
                    MuestraPersona(
                        zona_id=v.zona_id, zona_nombre=v.zona_nombre,
                        persona_id=tp.persona_id, nombre=tp.nombre,
                        desde=desde, hasta=ts,
                        presente_s=round(tp.presente_s, 2),
                        telefono_s=round(tp.telefono_s, 2),
                    )
                )
            v.reiniciar()
        self._inicio_ventana = ts
        return muestras

    # ── diagnóstico ─────────────────────────────────────────────────
    def estado(self) -> dict:
        return {
            "zonas": [
                {
                    "id": z.id,
                    "nombre": z.nombre,
                    "ocupadoS": round(self._ventanas[z.id].ocupado_s, 1),
                    "telefonoS": round(self._ventanas[z.id].telefono_s, 1),
                    "vacioS": round(self._ventanas[z.id].vacio_s, 1),
                    "sinCoberturaS": round(self._ventanas[z.id].sin_cobertura_s, 1),
                }
                for z in self.zonas
            ],
            "ventanaAbiertaS": round((self._ultimo_ts or 0) - (self._inicio_ventana or 0), 1),
        }


def _telefono_en_uso(persona: Caja, telefonos: list[Caja], cfg: ConfigActividad) -> bool:
    """¿Alguno de los teléfonos está asociado al cuerpo de esta persona?

    Se pide que el teléfono esté en la mitad superior del cuerpo, con un margen
    que tolera el brazo estirado. Un teléfono apoyado sobre el escritorio, o el
    de otra persona, no cuenta.

    Esto detecta el teléfono VISIBLE. Si queda tapado por el cuerpo o la persona
    está de espaldas, no se ve — por eso el tiempo que sale de acá es una cota
    inferior y el informe lo dice así.
    """
    m = cfg.phoneMargin
    x0, x1 = persona.x - m, persona.x + persona.w + m
    y0 = persona.y - m
    y1 = persona.y + persona.h * cfg.phoneBodyTop

    for t in telefonos:
        cx, cy = t.centro
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            return True
    return False


def _punto_en_poligono(punto: tuple[float, float], poligono: list[tuple[float, float]]) -> bool:
    """Algoritmo del rayo. Devuelve True si el punto cae dentro del polígono."""
    x, y = punto
    dentro = False
    n = len(poligono)
    j = n - 1
    for i in range(n):
        xi, yi = poligono[i]
        xj, yj = poligono[j]
        if (yi > y) != (yj > y):
            corte_x = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < corte_x:
                dentro = not dentro
        j = i
    return dentro
