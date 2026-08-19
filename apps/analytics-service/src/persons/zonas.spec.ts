import { BadRequestException } from '@nestjs/common';
import { PersonsService } from './persons.service';

/**
 * Pruebas de lo que se puede y no se puede guardar como plano.
 *
 * El caso que justifica el archivo es uno solo: borrar un bloque donde trabaja
 * gente. Alguien que arrastra rectángulos en una pantalla no está pensando en
 * el padrón de empleados, y si el borrado pasara sin decir nada, la zona de esa
 * gente se vaciaría en silencio. Eso no se nota hasta que la pantalla de
 * bienvenida deja de mostrarle a alguien dónde le toca, semanas después.
 */

/** Base de mentira: withTenant sólo corre la función con el cliente falso. */
function baseCon(previas: unknown[], gente: Record<string, number>) {
  const guardado: { zonas?: unknown[] } = {};
  const db = { withTenant: async (_o: string, fn: (c: unknown) => Promise<unknown>) => fn({}) } as never;
  const repo = {
    zonas: async () => previas,
    personasPorZona: async () => gente,
    guardarZonas: async (_c: unknown, _o: string, zonas: unknown[]) => {
      guardado.zonas = zonas;
    },
  } as never;
  return { servicio: new PersonsService(db, repo), guardado };
}

const AUTH = { userId: 'u1', organizationId: 'o1', permissions: [] } as never;

/** Un área válida, para no repetir las coordenadas en cada prueba. */
function bloque(key: string, extra: Record<string, unknown> = {}) {
  return { floorId: 'p1', key, name: key, kind: 'oficina', x: 0.1, y: 0.1, w: 0.2, h: 0.2, ...extra };
}

describe('guardarZonas', () => {
  it('guarda el plano dibujado', async () => {
    const { servicio, guardado } = baseCon([], {});
    await servicio.guardarZonas(AUTH, [bloque('recepcion'), bloque('pasillo')] as never);
    expect(guardado.zonas).toHaveLength(2);
  });

  it('se niega a borrar un bloque donde trabaja gente', async () => {
    const { servicio, guardado } = baseCon([{ key: 'oficina-3', name: 'Oficina 3' }], { 'oficina-3': 2 });
    await expect(servicio.guardarZonas(AUTH, [bloque('recepcion')] as never)).rejects.toBeInstanceOf(
      BadRequestException,
    );
    // Y no guarda NADA: el plano queda como estaba, no a medias.
    expect(guardado.zonas).toBeUndefined();
  });

  it('el rechazo dice qué bloque y cuánta gente', async () => {
    const { servicio } = baseCon([{ key: 'oficina-3', name: 'Oficina 3' }], { 'oficina-3': 2 });
    await expect(servicio.guardarZonas(AUTH, [] as never)).rejects.toThrow(/Oficina 3 \(2\)/);
  });

  it('borrar un bloque vacío sí se puede', async () => {
    const { servicio, guardado } = baseCon([{ key: 'deposito', name: 'Depósito' }], {});
    await servicio.guardarZonas(AUTH, [bloque('recepcion')] as never);
    expect(guardado.zonas).toHaveLength(1);
  });

  it('un bloque que se conserva no cuenta como borrado aunque tenga gente', async () => {
    const { servicio, guardado } = baseCon([{ key: 'oficina-3', name: 'Oficina 3' }], { 'oficina-3': 5 });
    await servicio.guardarZonas(AUTH, [bloque('oficina-3', { name: 'Depósito nuevo' })] as never);
    // Renombrar y mover un bloque con gente adentro tiene que poder hacerse:
    // la clave es la que ata a la persona, no el nombre.
    expect(guardado.zonas).toHaveLength(1);
  });

  it('un bloque sin nombre se rechaza', async () => {
    const { servicio } = baseCon([], {});
    await expect(
      servicio.guardarZonas(AUTH, [bloque('x', { name: '  ' })] as never),
    ).rejects.toThrow(/nombre/);
  });

  it('dos bloques con la misma clave se rechazan', async () => {
    const { servicio } = baseCon([], {});
    await expect(
      servicio.guardarZonas(AUTH, [bloque('recepcion'), bloque('recepcion')] as never),
    ).rejects.toThrow(/dos bloques/);
  });

  it('un tipo inventado se rechaza', async () => {
    const { servicio } = baseCon([], {});
    await expect(
      servicio.guardarZonas(AUTH, [bloque('x', { kind: 'quirofano' })] as never),
    ).rejects.toThrow(/desconocido/);
  });

  it('un bloque que se sale del plano se rechaza', async () => {
    const { servicio } = baseCon([], {});
    await expect(
      servicio.guardarZonas(AUTH, [bloque('x', { x: 0.9, w: 0.5 })] as never),
    ).rejects.toThrow(/fuera del plano/);
  });

  it('un bloque sin superficie se rechaza', async () => {
    // Es lo que deja un clic suelto sobre el plano si el editor no lo filtra:
    // un rectángulo invisible que después nadie puede seleccionar para borrar.
    const { servicio } = baseCon([], {});
    await expect(servicio.guardarZonas(AUTH, [bloque('x', { w: 0 })] as never)).rejects.toThrow(
      /fuera del plano/,
    );
  });

  it('el borde exacto del plano entra', async () => {
    const { servicio, guardado } = baseCon([], {});
    await servicio.guardarZonas(AUTH, [bloque('x', { x: 0, y: 0, w: 1, h: 1 })] as never);
    expect(guardado.zonas).toHaveLength(1);
  });

  it('un plano vacío es válido: se puede borrar todo si no hay nadie asignado', async () => {
    const { servicio, guardado } = baseCon([{ key: 'a', name: 'A' }], {});
    await servicio.guardarZonas(AUTH, [] as never);
    expect(guardado.zonas).toHaveLength(0);
  });

  it('un área sin piso se rechaza', async () => {
    // Un área sin piso no se puede ni dibujar ni encontrar: con varias plantas,
    // "está en el 30% del ancho" no dice nada si no se sabe de qué plano.
    const { servicio } = baseCon([], {});
    await expect(
      servicio.guardarZonas(AUTH, [bloque('x', { floorId: undefined })] as never),
    ).rejects.toThrow(/no tiene piso/);
  });

  it('dos pisos pueden tener cada uno sus áreas', async () => {
    const { servicio, guardado } = baseCon([], {});
    await servicio.guardarZonas(AUTH, [
      bloque('recepcion', { floorId: 'planta-baja' }),
      bloque('archivo', { floorId: 'subsuelo' }),
    ] as never);
    expect(guardado.zonas).toHaveLength(2);
  });
});
