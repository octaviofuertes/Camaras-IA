import {
  CanActivate,
  ExecutionContext,
  Injectable,
  Logger,
  SetMetadata,
  ConflictException,
} from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import type { Request } from 'express';
import { DatabaseService } from '../db/database.service';

export const MODULO_KEY = 'percepta:modulo';

/**
 * Declara que el endpoint pertenece a un módulo de IA, y por lo tanto sólo
 * existe donde ese módulo esté asignado a alguna cámara.
 */
export const RequiereModulo = (moduleKey: string) => SetMetadata(MODULO_KEY, moduleKey);

/**
 * Exceptúa un endpoint del módulo que declaró su controlador.
 *
 * Existe para que las excepciones se lean, en vez de deducirse de que a un
 * método le falta un decorador. Cada uso tiene que venir con el motivo escrito
 * al lado: sacar algo de la puerta es una decisión, no una omisión.
 */
export const SinModulo = () => SetMetadata(MODULO_KEY, null);

/**
 * Cuánto se recuerda la respuesta antes de volver a preguntarle a la base.
 *
 * La pantalla de bienvenida pregunta cada segundo y medio, y el registro en
 * vivo también: sin memoria, cada persona parada frente a una cámara serían
 * dos consultas por segundo a una tabla que cambia cuando alguien arrastra un
 * módulo en la pantalla de Cámaras, o sea casi nunca.
 *
 * Diez segundos es el precio que se paga por apagar la función: entre que se
 * desasigna el módulo y que los endpoints empiezan a rechazar puede pasar eso.
 * Es aceptable porque es la misma demora que ya tiene el ai-worker en dejar de
 * mirar la cámara, y porque el frontend se entera al instante por su lado.
 */
const MEMORIA_MS = 10_000;

/**
 * Un módulo asignado a una cámara es lo que enciende su funcionalidad.
 *
 * Es la diferencia entre tener algo en el catálogo y estar usándolo. El
 * catálogo dice qué se puede contratar; `camera_module_configs` dice qué está
 * efectivamente mirando una cámara de esta organización.
 *
 * Esto es una capa más de la defensa en profundidad, y va DESPUÉS del permiso,
 * no en su lugar: el permiso responde "¿esta persona puede?", el módulo
 * responde "¿esta organización tiene esta función?". Un administrador con
 * todos los permisos del mundo tampoco puede dar de alta la cara de un
 * empleado si no hay ninguna cámara que vaya a reconocerla — no porque no se
 * confíe en él, sino porque guardar un dato biométrico que nada va a usar es
 * empezar a tratar un dato sensible sin motivo.
 *
 * Se responde 409 y no 403: no falta un permiso, falta una asignación. El 403
 * mandaría a revisar los roles del usuario, que es el lugar equivocado.
 */
@Injectable()
export class ModuloAsignadoGuard implements CanActivate {
  private readonly logger = new Logger(ModuloAsignadoGuard.name);
  /** organización + módulo → qué se contestó y cuándo. */
  private readonly memoria = new Map<string, { asignado: boolean; hasta: number }>();

  constructor(
    private readonly reflector: Reflector,
    private readonly db: DatabaseService,
  ) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const moduleKey = this.reflector.getAllAndOverride<string>(MODULO_KEY, [
      context.getHandler(),
      context.getClass(),
    ]);
    // null = exceptuado a propósito con @SinModulo(); undefined = el
    // controlador no declara módulo y no hay nada que verificar.
    if (!moduleKey) return true;

    const req = context.switchToHttp().getRequest<Request>();
    const org = req.auth?.organizationId;
    // Sin organización no hay nada que consultar. Que decida el JwtGuard, que
    // corre antes: acá dejar pasar no abre nada porque ya no hay contexto.
    if (!org) return true;

    if (await this.asignado(org, moduleKey)) return true;

    throw new ConflictException(
      `El módulo "${moduleKey}" no está asignado a ninguna cámara. ` +
        'Asignalo a una cámara desde la sección Cámaras para usar esta función.',
    );
  }

  /** ¿Hay al menos una cámara de esta organización con el módulo prendido? */
  private async asignado(org: string, moduleKey: string): Promise<boolean> {
    const clave = `${org}:${moduleKey}`;
    const ahora = Date.now();
    const recordado = this.memoria.get(clave);
    if (recordado && recordado.hasta > ahora) return recordado.asignado;

    let asignado = false;
    try {
      asignado = await this.db.withTenant(org, async (client) => {
        const { rows } = await client.query<{ hay: boolean }>(
          `SELECT EXISTS (
             SELECT 1
               FROM camera_module_configs cmc
               JOIN ai_modules m ON m.id = cmc.ai_module_id
              WHERE m.module_key = $1 AND cmc.enabled
           ) AS hay`,
          [moduleKey],
        );
        return rows[0]?.hay === true;
      });
    } catch (err) {
      // Si la base no contesta, no se inventa un permiso ni se corta la
      // función: se deja pasar y se grita. Un error de infraestructura no
      // debería parecerse a "el cliente no contrató esto", que es lo que vería
      // el usuario si acá devolviéramos false.
      this.logger.error(`no se pudo verificar el módulo ${moduleKey}: ${err}`);
      return true;
    }

    this.memoria.set(clave, { asignado, hasta: ahora + MEMORIA_MS });
    return asignado;
  }
}
