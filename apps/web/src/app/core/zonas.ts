/**
 * El lugar: sus pisos y las áreas marcadas sobre el plano de cada uno.
 *
 * Antes esto era una lista de ocho rectángulos escritos acá a mano, con los
 * nombres de una oficina que no era la de nadie. Después pasó a dibujarse sobre
 * un lienzo en blanco, que se rompía con el primer edificio de dos plantas.
 * Ahora el plano es la imagen que sube el cliente —una por piso— y lo único que
 * se marca encima es dónde queda cada área.
 *
 * ── Por qué las coordenadas son fracciones ──────────────────────────────
 *
 * Un área dice "empieza al 30% del ancho y ocupa el 25%", no "empieza en el
 * píxel 460". La imagen la sube el cliente y puede tener cualquier proporción y
 * cualquier tamaño: con fracciones, la marca sigue cayendo sobre la misma
 * habitación en la pantalla del kiosco, en un celular o en un PDF.
 */
export type TipoZona = 'oficina' | 'pasillo' | 'otro';

export interface Zona {
  id?: string;
  /** A qué piso pertenece. */
  pisoId: string;
  /** Clave estable. Es lo que guarda cada persona en `work_zone`. */
  clave: string;
  nombre: string;
  tipo: TipoZona;
  /** Fracciones del plano de su piso, entre 0 y 1. */
  x: number;
  y: number;
  w: number;
  h: number;
  /** Cuánta gente tiene esta área asignada. Lo informa el servidor. */
  personas?: number;
}

/** Un piso del lugar, con su plano y lo que hay marcado encima. */
export interface Piso {
  id: string;
  nombre: string;
  orden: number;
  /** El plano que subió el cliente. Null = todavía no lo subió. */
  image: string | null;
  ancho: number | null;
  alto: number | null;
  zonas: Zona[];
}

/**
 * Proporción con la que se dibuja un piso (alto ÷ ancho).
 *
 * Sale del tamaño real de su imagen. Sin imagen se usa 2:3, que sólo sirve para
 * dibujar el recuadro vacío que invita a subirla.
 */
export function proporcion(piso: Piso | null): number {
  const a = piso?.ancho ?? 0;
  const h = piso?.alto ?? 0;
  return a > 0 && h > 0 ? h / a : 1024 / 1536;
}

/**
 * Ancho del lienzo en unidades de dibujo.
 *
 * Se dibuja en un espacio de mil unidades de ancho y no directamente en
 * fracciones porque un viewBox de 0 a 1 obliga a grosores de línea como 0.002,
 * que se leen mal y se redondean peor.
 */
export const LIENZO = 1000;

export function altoLienzo(piso: Piso | null): number {
  return Math.round(LIENZO * proporcion(piso));
}

/** Busca un área por su clave, mirando todos los pisos. */
export function zonaPorClave(pisos: Piso[], clave?: string | null): Zona | undefined {
  if (!clave) return undefined;
  for (const p of pisos) {
    const z = p.zonas.find((q) => q.clave === clave);
    if (z) return z;
  }
  return undefined;
}

/** El piso donde está un área. */
export function pisoDeZona(pisos: Piso[], clave?: string | null): Piso | undefined {
  if (!clave) return undefined;
  return pisos.find((p) => p.zonas.some((z) => z.clave === clave));
}

/**
 * Convierte un nombre en una clave estable.
 *
 * Sólo se usa al crear un área nueva: renombrarla después no cambia la clave,
 * porque eso desasignaría a la gente que ya está ahí. La clave es única en todo
 * el lugar, no por piso, porque las personas guardan sólo la clave.
 */
export function claveDesde(nombre: string, tomadas: Set<string>): string {
  const base =
    nombre
      .toLowerCase()
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 40) || 'area';
  if (!tomadas.has(base)) return base;
  let n = 2;
  while (tomadas.has(`${base}-${n}`)) n++;
  return `${base}-${n}`;
}

/** Cómo se llama cada tipo en pantalla. */
export const TIPOS: { valor: TipoZona; label: string }[] = [
  { valor: 'oficina', label: 'Oficina' },
  { valor: 'pasillo', label: 'Pasillo' },
  { valor: 'otro', label: 'Otro' },
];
