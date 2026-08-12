/**
 * Pruebas de la vista en vivo: quién está en el cuadro AHORA.
 *
 * Lo que se protege es su comportamiento en el tiempo, que es todo lo que
 * importa acá: que alguien aparezca apenas entra, que desaparezca apenas se va,
 * y que no parpadee cuando la identificación falla un cuadro. Las tres cosas
 * dependen de plazos, y un plazo mal puesto no se nota mirando la pantalla un
 * rato: se nota cuando alguien confía en que la lista está vacía.
 */
import { PersonsService, type Presente } from './persons.service';

function servicio(): PersonsService {
  // La vista en vivo vive en memoria y no toca la base ni el repositorio: se
  // instancia con dependencias vacías a propósito, para probar sólo eso.
  return new PersonsService(null as never, null as never);
}

function persona(id: string, nombre: string): Presente {
  return {
    personId: id,
    displayName: nombre,
    hasAccess: true,
    desde: new Date().toISOString(),
    ultimaVez: new Date().toISOString(),
    seenByFace: true,
    cameraId: 'cam-1',
  };
}

/**
 * Los presentes según el estado interno, sin pasar por la base.
 *
 * NO filtra por tiempo: el vencimiento es responsabilidad del servicio y tiene
 * que quedar probado. Un helper que filtrara por su cuenta haría pasar la
 * prueba aunque el plazo real fuera de una hora.
 */
function enVivo(s: PersonsService): string[] {
  const mapa = (s as never as {
    presencia: Map<string, { at: number; personas: Map<string, { at: number; p: Presente }> }>;
  }).presencia;
  const salida: string[] = [];
  for (const r of mapa.values()) {
    for (const v of r.personas.values()) salida.push(v.p.displayName);
  }
  return salida;
}

describe('vista en vivo', () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it('muestra a alguien apenas entra', () => {
    const s = servicio();
    s.reportarPresencia('cam-1', [persona('p1', 'Juan')]);
    expect(enVivo(s)).toEqual(['Juan']);
  });

  it('lo saca apenas deja de vérselo', () => {
    const s = servicio();
    s.reportarPresencia('cam-1', [persona('p1', 'Juan')]);

    // La cámara sigue reportando, pero él ya no está en el cuadro.
    jest.advanceTimersByTime(6000);
    s.reportarPresencia('cam-1', []);
    expect(enVivo(s)).toEqual([]);
  });

  it('no parpadea si la identificación falla un cuadro', () => {
    const s = servicio();
    s.reportarPresencia('cam-1', [persona('p1', 'Juan')]);

    // Un par de cuadros sin reconocerlo: sigue estando.
    jest.advanceTimersByTime(3000);
    s.reportarPresencia('cam-1', []);
    expect(enVivo(s)).toEqual(['Juan']);

    // Y cuando vuelve a vérselo, sigue siendo la misma presencia.
    jest.advanceTimersByTime(1000);
    s.reportarPresencia('cam-1', [persona('p1', 'Juan')]);
    expect(enVivo(s)).toEqual(['Juan']);
  });

  it('conserva desde cuándo está, aunque el reporte traiga otra hora', () => {
    const s = servicio();
    const llegada = persona('p1', 'Juan');
    llegada.desde = '2026-01-01T10:00:00.000Z';
    s.reportarPresencia('cam-1', [llegada]);

    jest.advanceTimersByTime(2000);
    const despues = persona('p1', 'Juan');
    despues.desde = '2026-01-01T10:05:00.000Z';
    s.reportarPresencia('cam-1', [despues]);

    const mapa = (s as never as { presencia: Map<string, { personas: Map<string, { p: Presente }> }> }).presencia;
    expect(mapa.get('cam-1')?.personas.get('p1')?.p.desde).toBe('2026-01-01T10:00:00.000Z');
  });

  it('convierte el "desde" que manda el módulo a una fecha', () => {
    // El módulo lo manda en segundos desde la época. Sin convertirlo, la
    // pantalla lo interpretaba como milisegundos y mostraba "hace 495770 h".
    const s = servicio();
    const p = persona('p1', 'Juan');
    (p as unknown as { desde: number }).desde = 1786500000;
    s.reportarPresencia('cam-1', [p]);

    const mapa = (s as never as {
      presencia: Map<string, { personas: Map<string, { p: Presente }> }>;
    }).presencia;
    const guardado = mapa.get('cam-1')?.personas.get('p1')?.p.desde as string;
    expect(guardado).toBe(new Date(1786500000 * 1000).toISOString());
    expect(Number.isNaN(new Date(guardado).getTime())).toBe(false);
  });

  it('dos personas a la vez son dos', () => {
    const s = servicio();
    s.reportarPresencia('cam-1', [persona('p1', 'Juan'), persona('p2', 'Ana')]);
    expect(enVivo(s).sort()).toEqual(['Ana', 'Juan']);
  });
});
