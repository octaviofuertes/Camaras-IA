/**
 * Pruebas de a quién saluda la pantalla de bienvenida.
 *
 * Lo que se protege es lo que puede hacer quedar mal al sistema delante de una
 * persona: saludarla con el nombre de otra, o no saludarla por parecerse a sí
 * misma. Lo segundo pasó de verdad y es lo que motiva estas pruebas.
 */
import { PersonsService } from './persons.service';

type Fila = {
  id: string;
  displayName: string;
  hasAccess: boolean;
  photo: string | null;
  workZone: string | null;
  embeddings: number[][];
};

/** Vector unitario en la dirección `i`, para poder fijar parecidos exactos. */
function eje(i: number, dim = 8): number[] {
  return Array.from({ length: dim }, (_, k) => (k === i ? 1 : 0));
}

/** Un vector cuyo coseno contra `eje(0)` es exactamente `p`. */
function aParecido(p: number): number[] {
  const v = eje(0);
  v[0] = p;
  v[1] = Math.sqrt(Math.max(1 - p * p, 0));
  return v;
}

function servicio(galeria: Fila[], caraDetectada: number[] | null): PersonsService {
  const repo = { galeriaConDatos: async () => galeria } as never;
  const db = { withTenant: async (_o: string, fn: (c: unknown) => unknown) => fn({}) } as never;
  const s = new PersonsService(db, repo);

  // El worker se reemplaza por una respuesta fija: acá se prueba la decisión,
  // no el detector de rostros.
  (globalThis as unknown as { fetch: unknown }).fetch = async () => ({
    json: async () =>
      caraDetectada
        ? { ok: true, caras: [{ embedding: caraDetectada, score: 0.9, alto: 0.4 }] }
        : { ok: true, caras: [] },
  });
  return s;
}

const AUTH = { organizationId: 'org-1', userId: 'u-1' } as never;

function persona(id: string, nombre: string, embeddings: number[][]): Fila {
  return { id, displayName: nombre, hasAccess: true, photo: null, workZone: 'oficina-1', embeddings };
}

describe('a quién saluda la pantalla de bienvenida', () => {
  it('saluda a quien reconoce', async () => {
    const s = servicio([persona('p1', 'Juan Rodríguez', [eje(0)])], eje(0));
    const r = await s.identificar(AUTH, 'foto');
    expect(r?.displayName).toBe('Juan Rodríguez');
    expect(r?.workZone).toBe('oficina-1');
  });

  it('varias fotos de la misma persona no la descalifican', async () => {
    // El bug real: el margen se medía entre plantillas, así que las dos fotos
    // de Juan competían entre sí y lo dejaban sin saludo.
    const s = servicio(
      [persona('p1', 'Juan', [eje(0), aParecido(0.97)]), persona('p2', 'Ana', [eje(3)])],
      eje(0),
    );
    const r = await s.identificar(AUTH, 'foto');
    expect(r?.displayName).toBe('Juan');
  });

  it('no saluda si dos personas se parecen casi igual', async () => {
    const s = servicio(
      [persona('p1', 'Juan', [eje(0)]), persona('p2', 'Ana', [aParecido(0.98)])],
      eje(0),
    );
    expect(await s.identificar(AUTH, 'foto')).toBeNull();
  });

  it('no saluda a quien no alcanza el parecido mínimo', async () => {
    const s = servicio([persona('p1', 'Juan', [aParecido(0.4)])], eje(0));
    expect(await s.identificar(AUTH, 'foto')).toBeNull();
  });

  it('sin cara en el cuadro no saluda a nadie', async () => {
    const s = servicio([persona('p1', 'Juan', [eje(0)])], null);
    expect(await s.identificar(AUTH, 'foto')).toBeNull();
  });

  it('con la galería vacía no inventa a nadie', async () => {
    const s = servicio([], eje(0));
    expect(await s.identificar(AUTH, 'foto')).toBeNull();
  });
});
