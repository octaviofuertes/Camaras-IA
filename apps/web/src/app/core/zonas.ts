/**
 * Los bloques del plano del lugar.
 *
 * Antes esto era una lista de ocho rectángulos escritos acá a mano, con los
 * nombres de una oficina que no era la de nadie. Ahora cada empresa dibuja el
 * suyo y lo guarda en la base (`floor_zones`, migración 0014); este archivo
 * quedó con la forma del dato y las cuentas para dibujarlo.
 *
 * ── Por qué las coordenadas son fracciones ──────────────────────────────
 *
 * Un bloque dice "empieza al 30% del ancho y ocupa el 25%", no "empieza en el
 * píxel 460". La imagen del plano la sube el cliente y puede tener cualquier
 * proporción: con píxeles sobre un lienzo fijo, un plano apaisado se dibujaba
 * encajado con franjas y los bloques quedaban corridos respecto de la foto.
 */
export type TipoZona = 'oficina' | 'pasillo' | 'otro';

export interface Zona {
  id?: string;
  /** Clave estable. Es lo que guarda cada persona en `work_zone`. */
  clave: string;
  nombre: string;
  tipo: TipoZona;
  /** Fracciones del plano, entre 0 y 1. */
  x: number;
  y: number;
  w: number;
  h: number;
  /** Cuánta gente tiene este bloque asignado. Lo informa el servidor. */
  personas?: number;
}

/** El fondo sobre el que se dibujan los bloques. */
export interface Plano {
  image: string | null;
  ancho: number | null;
  alto: number | null;
}

/**
 * Proporción con la que se dibuja el plano (alto ÷ ancho).
 *
 * Sale del tamaño real de la imagen subida. Sin imagen se usa 2:3, que es la
 * forma de una planta de oficina común y evita que el lienzo vacío salga
 * cuadrado o como una tira.
 */
export function proporcion(plano: Plano | null): number {
  const a = plano?.ancho ?? 0;
  const h = plano?.alto ?? 0;
  return a > 0 && h > 0 ? h / a : 1024 / 1536;
}

/**
 * Alto del lienzo en unidades de dibujo, para un ancho de `LIENZO`.
 *
 * Se dibuja en un espacio de mil unidades de ancho y no directamente en
 * fracciones porque un viewBox de 0 a 1 obliga a grosores de línea como
 * 0.002, que se leen mal y se redondean peor.
 */
export const LIENZO = 1000;

export function altoLienzo(plano: Plano | null): number {
  return Math.round(LIENZO * proporcion(plano));
}

export function zonaPorClave(zonas: Zona[], clave?: string | null): Zona | undefined {
  return clave ? zonas.find((z) => z.clave === clave) : undefined;
}

/**
 * Convierte un nombre en una clave estable.
 *
 * Sólo se usa al crear un bloque nuevo: renombrar después no cambia la clave,
 * porque eso desasignaría a la gente que ya está en ese bloque.
 */
export function claveDesde(nombre: string, tomadas: Set<string>): string {
  const base =
    nombre
      .toLowerCase()
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 40) || 'bloque';
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
