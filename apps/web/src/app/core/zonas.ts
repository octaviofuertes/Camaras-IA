/**
 * El plano del lugar, con sus zonas.
 *
 * Está acá y no en la base porque es la planta del edificio, no un dato de
 * negocio: cambia cuando se muda la empresa, no cuando entra alguien. Las
 * personas guardan la CLAVE de su zona (`persons.work_zone`), así que renombrar
 * una etiqueta acá no rompe ninguna asignación.
 *
 * Las coordenadas son las del plano de referencia (1536 × 1024) y se dibujan
 * como SVG, que escala sin pixelarse y permite iluminar una zona sin recortar
 * imágenes. Si más adelante se quiere el render fotográfico de fondo, va como
 * una capa debajo con este mismo sistema de coordenadas.
 */
export interface Zona {
  clave: string;
  nombre: string;
  /** x, y, ancho, alto en coordenadas del plano. */
  x: number;
  y: number;
  w: number;
  h: number;
}

export const PLANO = { ancho: 1536, alto: 1024 } as const;

export const ZONAS: Zona[] = [
  { clave: 'oficina-1', nombre: 'Oficina 1', x: 75, y: 45, w: 390, h: 305 },
  { clave: 'sala-reuniones', nombre: 'Sala de reuniones', x: 490, y: 45, w: 545, h: 300 },
  { clave: 'oficina-2', nombre: 'Oficina 2', x: 1060, y: 45, w: 400, h: 300 },
  { clave: 'pasillo-izq', nombre: 'Pasillo izquierdo', x: 75, y: 360, w: 390, h: 175 },
  { clave: 'recepcion', nombre: 'Recepción', x: 570, y: 355, w: 385, h: 570 },
  { clave: 'pasillo-der', nombre: 'Pasillo derecho', x: 960, y: 360, w: 90, h: 565 },
  { clave: 'oficina-3', nombre: 'Oficina 3', x: 75, y: 545, w: 390, h: 380 },
  { clave: 'oficina-4', nombre: 'Oficina 4', x: 1060, y: 440, w: 400, h: 485 },
];

export function zonaPorClave(clave?: string | null): Zona | undefined {
  return clave ? ZONAS.find((z) => z.clave === clave) : undefined;
}
