/**
 * Cuándo saludar y cuándo despejar la pantalla de bienvenida.
 *
 * Está acá afuera del componente porque son las dos reglas que hacen que la
 * pantalla sirva para una fila de gente, y se pueden mirar —y probar— sin
 * cámara, sin red y sin navegador.
 */

/**
 * Cuánto dura el saludo en pantalla.
 *
 * No se va cuando la persona se va: se va sola. Es una pantalla de entrada y
 * atrás hay fila — si esperara a que el primero salga del cuadro, el segundo
 * se quedaría mirando el nombre del primero.
 */
export const SALUDO_MS = 5000;

/**
 * Cuánto espera antes de volver a saludar a la MISMA persona.
 *
 * Sin esto, quien se queda parado frente a la pantalla vuelve a ser reconocido
 * en el intento siguiente y el saludo reaparece en loop. A otra persona se la
 * saluda al instante: la espera es por persona, no de la pantalla.
 */
export const REPETIR_MS = 25_000;

/** Lo último que se saludó, para no repetirlo ni cortarlo antes de tiempo. */
export interface EstadoSaludo {
  personId: string;
  /** Cuándo apareció en pantalla. */
  desde: number;
}

/** ¿Ya cumplió sus cinco segundos y hay que sacarlo? */
export function vencio(estado: EstadoSaludo | null, ahora: number): boolean {
  return !!estado && ahora - estado.desde >= SALUDO_MS;
}

/**
 * ¿Corresponde poner en pantalla a quien se acaba de reconocer?
 *
 * `ultimo` es el último saludo que hubo, esté o no visible: la espera para no
 * repetirse tiene que sobrevivir a que el saludo ya se haya ido de pantalla,
 * que es justamente cuando la persona sigue parada ahí.
 */
export function debeSaludar(
  ultimo: EstadoSaludo | null,
  personId: string,
  ahora: number,
): boolean {
  if (!ultimo) return true;
  // Otra persona pasa adelante: no espera nada, es la razón de que el saludo
  // anterior se haya retirado solo.
  if (ultimo.personId !== personId) return true;
  return ahora - ultimo.desde >= REPETIR_MS;
}
