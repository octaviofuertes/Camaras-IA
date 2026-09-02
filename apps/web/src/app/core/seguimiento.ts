/**
 * Dónde está AHORA lo que el worker vio hace un rato.
 *
 * ── El problema ─────────────────────────────────────────────────────────
 *
 * El video llega casi en vivo: media-service entrega el último cuadro y el
 * navegador lo muestra enseguida. Los recuadros, no. Entre que se captura el
 * cuadro que se analiza, el modelo termina y la pantalla pregunta, pasan unos
 * 200-400 ms. En ese rato la persona se movió, y el recuadro queda dibujado
 * donde estaba: "se queda en el aire".
 *
 * Refrescar más seguido acorta esa diferencia pero no la elimina —el modelo
 * tarda lo que tarda—, y además deja el recuadro saltando de posición en
 * posición en vez de acompañar el movimiento.
 *
 * ── Qué hace esto ───────────────────────────────────────────────────────
 *
 * Guarda las dos últimas posiciones de cada cuerpo con SU instante, saca la
 * velocidad, y devuelve dónde estaría en el instante que se le pida. La
 * pantalla le pide "dónde está ahora" sesenta veces por segundo, así que el
 * recuadro se mueve parejo y llega a donde la persona está, no a donde estuvo.
 *
 * ── Por qué está acotado ────────────────────────────────────────────────
 *
 * Adelantar es adivinar. Alguien que camina y se frena de golpe hace que la
 * predicción se pase, y un recuadro que se pasa y vuelve se ve peor que uno
 * que va atrasado. Por eso hay tres frenos: no se adelanta más de medio
 * segundo, no se corre más de tres cuartos del tamaño del propio recuadro, y
 * la velocidad se promedia con la anterior en vez de tomarse cruda —un cuadro
 * con la caja mal ajustada no manda al recuadro a la otra punta—.
 */

/** x, y, ancho, alto en fracciones del cuadro. */
export type Caja = [number, number, number, number];

/** Hasta cuándo se adelanta. Más que esto es inventar. */
const MAX_ADELANTO_S = 0.5;

/** Cuánto se puede correr un recuadro, en fracciones de su propio tamaño. */
const MAX_CORRIMIENTO = 0.75;

/**
 * Hueco a partir del cual se considera que el cuerpo se perdió y volvió.
 * Sacar velocidad de dos posiciones separadas por más que esto daría un número
 * sin sentido: la persona pudo haber ido y vuelto en el medio.
 */
const OLVIDO_S = 1.2;

/** Cuánto pesa la velocidad nueva contra la que se venía midiendo. */
const SUAVIZADO = 0.5;

interface Rastro {
  caja: Caja;
  ts: number;
  /** Fracciones de cuadro por segundo. */
  vel: Caja;
  /** Última vez que se lo vio, para poder soltarlo. */
  visto: number;
}

export class Seguimiento {
  private readonly rastros = new Map<string, Rastro>();

  /** Una posición nueva, con el instante EN QUE SE CAPTURÓ (no cuando llegó). */
  observar(clave: string, caja: Caja, ts: number): void {
    const previo = this.rastros.get(clave);
    const dt = previo ? ts - previo.ts : 0;

    // Sin dos muestras separadas por un rato razonable no hay velocidad que
    // medir: se arranca quieto, que es el error más chico posible.
    let vel: Caja = [0, 0, 0, 0];
    if (previo && dt > 0.01 && dt <= OLVIDO_S) {
      vel = previo.vel.map((v, i) =>
        v * (1 - SUAVIZADO) + ((caja[i] - previo.caja[i]) / dt) * SUAVIZADO,
      ) as Caja;
    }
    this.rastros.set(clave, { caja, ts, vel, visto: ts });
  }

  /**
   * Dónde estaría en `instante` (reloj del worker). Si nunca se lo vio,
   * devuelve la caja tal cual vino.
   */
  donde(clave: string, instante: number, siNoSe: Caja): Caja {
    const r = this.rastros.get(clave);
    if (!r) return siNoSe;

    const adelanto = Math.min(Math.max(instante - r.ts, 0), MAX_ADELANTO_S);
    if (adelanto === 0) return r.caja;

    const an = r.caja[2];
    const al = r.caja[3];
    // El tope se mide contra el lado que corresponde: el corrimiento en x y el
    // ancho contra el ancho, el de y y el alto contra el alto.
    const tope: Caja = [an, al, an, al].map((lado) => lado * MAX_CORRIMIENTO) as Caja;
    const corrido = r.caja.map((v, i) =>
      v + Math.max(-tope[i], Math.min(tope[i], r.vel[i] * adelanto)),
    ) as Caja;

    // El ancho y el alto no pueden desaparecer: una predicción que los lleva a
    // cero deja el recuadro invisible justo cuando más se lo está mirando.
    corrido[2] = Math.max(corrido[2], an * 0.25);
    corrido[3] = Math.max(corrido[3], al * 0.25);
    return corrido;
  }

  /** Suelta lo que ya no está en el cuadro. Sin esto la memoria crece sola. */
  olvidarSalvo(vigentes: Set<string>): void {
    for (const clave of [...this.rastros.keys()]) {
      if (!vigentes.has(clave)) this.rastros.delete(clave);
    }
  }
}

/**
 * Actualiza una lista EN EL LUGAR: los objetos que ya estaban se modifican en
 * vez de reemplazarse.
 *
 * Importa para lo que se dibuja sesenta veces por segundo. Si en cada vuelta se
 * arma una lista de objetos nuevos, `*ngFor` los ve como otros y rehace el DOM
 * entero: el recuadro parpadea y se pierde el clic a medio camino. Conservando
 * el objeto, Angular sólo cambia los números que cambiaron.
 */
export function fusionar<T extends object>(
  destino: T[],
  nuevos: T[],
  clave: (x: T) => string,
): void {
  const previos = new Map(destino.map((d) => [clave(d), d]));
  const salida: T[] = [];
  for (const n of nuevos) {
    const viejo = previos.get(clave(n));
    if (viejo) {
      Object.assign(viejo, n);
      salida.push(viejo);
    } else {
      salida.push(n);
    }
  }
  destino.length = 0;
  destino.push(...salida);
}
