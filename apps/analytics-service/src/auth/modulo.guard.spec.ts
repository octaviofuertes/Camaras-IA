import { ConflictException } from '@nestjs/common';
import { ModuloAsignadoGuard, MODULO_KEY } from './modulo.guard';

/**
 * Pruebas de la puerta que hace que "Ingreso de personas" sea un módulo.
 *
 * Lo que se protege acá no es un detalle de implementación: es la diferencia
 * entre una función que el cliente contrató y una que aparece sola. Y dos de
 * los casos son justamente los que uno no escribe si no los piensa: que borrar
 * a una persona siga siendo posible con el módulo apagado, y que una base
 * caída no se parezca a "no lo tenés contratado".
 */

/** Un ExecutionContext con lo mínimo que el guard mira. */
function contexto(org: string | undefined) {
  const req = { auth: org ? { organizationId: org } : undefined };
  return {
    switchToHttp: () => ({ getRequest: () => req }),
    getHandler: () => 'handler',
    getClass: () => 'clase',
  } as never;
}

/** Reflector de mentira: devuelve la clave que se le diga. */
function reflector(valor: unknown) {
  return { getAllAndOverride: () => valor } as never;
}

/** Base de mentira que cuenta cuántas veces se la consultó. */
function base(hay: boolean | Error) {
  const estado = { consultas: 0 };
  const db = {
    withTenant: async (_org: string, fn: (c: unknown) => Promise<unknown>) => {
      estado.consultas++;
      if (hay instanceof Error) throw hay;
      return fn({ query: async () => ({ rows: [{ hay }] }) });
    },
  } as never;
  return { db, estado };
}

const ORG = '00000000-0000-4000-b000-000000000001';

describe('ModuloAsignadoGuard', () => {
  it('deja pasar cuando el módulo está asignado a una cámara', async () => {
    const { db } = base(true);
    const guard = new ModuloAsignadoGuard(reflector('person-entry'), db);
    await expect(guard.canActivate(contexto(ORG))).resolves.toBe(true);
  });

  it('corta con 409 cuando no hay ninguna cámara con el módulo', async () => {
    const { db } = base(false);
    const guard = new ModuloAsignadoGuard(reflector('person-entry'), db);
    await expect(guard.canActivate(contexto(ORG))).rejects.toBeInstanceOf(ConflictException);
  });

  it('el 409 dice qué falta y dónde arreglarlo', async () => {
    const { db } = base(false);
    const guard = new ModuloAsignadoGuard(reflector('person-entry'), db);
    // Un "no se puede" sin instrucción manda a revisar permisos, que es el
    // lugar equivocado: lo que falta es arrastrar el módulo a una cámara.
    await expect(guard.canActivate(contexto(ORG))).rejects.toThrow(/Cámaras/);
  });

  it('un endpoint exceptuado con @SinModulo() pasa sin consultar la base', async () => {
    // Es el caso de DELETE /persons/:id. Si esto se rompe, desasignar el
    // módulo deja la biometría cargada sin forma de borrarla.
    const { db, estado } = base(false);
    const guard = new ModuloAsignadoGuard(reflector(null), db);
    await expect(guard.canActivate(contexto(ORG))).resolves.toBe(true);
    expect(estado.consultas).toBe(0);
  });

  it('un controlador que no declara módulo no se ve afectado', async () => {
    const { db, estado } = base(false);
    const guard = new ModuloAsignadoGuard(reflector(undefined), db);
    await expect(guard.canActivate(contexto(ORG))).resolves.toBe(true);
    expect(estado.consultas).toBe(0);
  });

  it('si la base no contesta, deja pasar en vez de simular que no está contratado', async () => {
    const { db } = base(new Error('sin conexión'));
    const guard = new ModuloAsignadoGuard(reflector('person-entry'), db);
    await expect(guard.canActivate(contexto(ORG))).resolves.toBe(true);
  });

  it('un fallo de la base no se recuerda: la próxima vuelve a preguntar', async () => {
    // Cachear el fail-open dejaría la función abierta diez segundos más de lo
    // necesario cada vez que hipa la base.
    const { db, estado } = base(new Error('sin conexión'));
    const guard = new ModuloAsignadoGuard(reflector('person-entry'), db);
    await guard.canActivate(contexto(ORG));
    await guard.canActivate(contexto(ORG));
    expect(estado.consultas).toBe(2);
  });

  it('no consulta la base en cada pedido: la pantalla pregunta cada 1,5 s', async () => {
    const { db, estado } = base(true);
    const guard = new ModuloAsignadoGuard(reflector('person-entry'), db);
    await guard.canActivate(contexto(ORG));
    await guard.canActivate(contexto(ORG));
    await guard.canActivate(contexto(ORG));
    expect(estado.consultas).toBe(1);
  });

  it('lo recordado es por organización: una no decide por la otra', async () => {
    const otra = '00000000-0000-4000-b000-000000000002';
    const { db, estado } = base(true);
    const guard = new ModuloAsignadoGuard(reflector('person-entry'), db);
    await guard.canActivate(contexto(ORG));
    await guard.canActivate(contexto(otra));
    expect(estado.consultas).toBe(2);
  });

  it('sin organización en el token no inventa una respuesta', async () => {
    // Quien rechaza por falta de sesión es el JwtGuard, que corre antes.
    const { db, estado } = base(false);
    const guard = new ModuloAsignadoGuard(reflector('person-entry'), db);
    await expect(guard.canActivate(contexto(undefined))).resolves.toBe(true);
    expect(estado.consultas).toBe(0);
  });

  it('la clave que se consulta es la que declara el endpoint', async () => {
    let pedido: unknown = null;
    const db = {
      withTenant: async (_org: string, fn: (c: unknown) => Promise<unknown>) =>
        fn({
          query: async (_sql: string, params: unknown[]) => {
            pedido = params[0];
            return { rows: [{ hay: true }] };
          },
        }),
    } as never;
    const guard = new ModuloAsignadoGuard(reflector('otro-modulo'), db);
    await guard.canActivate(contexto(ORG));
    expect(pedido).toBe('otro-modulo');
  });

  it('MODULO_KEY es la misma clave que escriben los decoradores', () => {
    expect(MODULO_KEY).toBe('percepta:modulo');
  });
});
