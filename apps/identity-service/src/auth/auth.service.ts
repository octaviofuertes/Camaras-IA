import { Injectable, Logger, UnauthorizedException } from '@nestjs/common';
import * as bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import type { Permission } from '@percepta/contracts';
import { MODULO_INGRESO_DE_PERSONAS } from '@percepta/contracts';
import { DatabaseService } from '../db/database.service';

export interface LoginResult {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
  user: { id: string; email: string; fullName: string | null; organizationId: string; permissions: Permission[] };
}

interface UserRow {
  id: string;
  organization_id: string;
  email: string;
  password_hash: string;
  full_name: string | null;
  status: string;
}

@Injectable()
export class AuthService {
  private readonly logger = new Logger(AuthService.name);

  constructor(private readonly db: DatabaseService) {}

  /**
   * Autentica con email + contraseña.
   *
   * Los permisos NO vienen del cliente: se resuelven en la base a partir de los
   * roles del usuario. El token es sólo el transporte de lo que la base ya dijo.
   */
  async login(email: string, password: string): Promise<LoginResult> {
    const accessSecret = process.env.JWT_ACCESS_SECRET;
    const refreshSecret = process.env.JWT_REFRESH_SECRET;
    if (!accessSecret || !refreshSecret) {
      throw new Error('Faltan JWT_ACCESS_SECRET / JWT_REFRESH_SECRET');
    }

    // El login corre como superusuario: todavía no hay tenant en contexto, y
    // buscar al usuario por email es previo a saber a qué organización pertenece.
    const user = await this.db.asAdmin(async (client) => {
      const { rows } = await client.query<UserRow>(
        `SELECT id, organization_id, email, password_hash, full_name, status
         FROM users WHERE lower(email) = lower($1) LIMIT 1`,
        [email],
      );
      return rows[0] ?? null;
    });

    // Mismo mensaje para usuario inexistente y contraseña incorrecta: no se
    // filtra qué emails existen.
    const invalid = new UnauthorizedException('Email o contraseña incorrectos');
    if (!user) {
      // Comparación igual de costosa aunque no exista el usuario, para no
      // delatar su existencia por el tiempo de respuesta.
      await bcrypt.compare(password, '$2a$10$invalidinvalidinvalidinvalidinvalidinvalidinvalidinvalid');
      throw invalid;
    }
    if (user.status !== 'active') throw new UnauthorizedException('El usuario está deshabilitado');
    if (!(await bcrypt.compare(password, user.password_hash))) throw invalid;

    const permissions = await this.permissionsOf(user.id);

    const ttl = Number(process.env.JWT_ACCESS_TTL ?? 28800);
    const accessToken = jwt.sign(
      { sub: user.id, org: user.organization_id, perms: permissions },
      accessSecret,
      { algorithm: 'HS256', expiresIn: ttl },
    );
    const refreshToken = jwt.sign({ sub: user.id, typ: 'refresh' }, refreshSecret, {
      algorithm: 'HS256',
      expiresIn: Number(process.env.JWT_REFRESH_TTL ?? 1209600),
    });

    this.logger.log(`login OK: ${user.email} (${permissions.length} permisos)`);

    return {
      accessToken,
      refreshToken,
      expiresIn: ttl,
      user: {
        id: user.id,
        email: user.email,
        fullName: user.full_name,
        organizationId: user.organization_id,
        permissions,
      },
    };
  }

  /**
   * Token para la pantalla de bienvenida.
   *
   * No pide credenciales, y eso es deliberado: esa pantalla cuelga de una
   * cámara en la entrada y nadie inicia sesión en ella. Si necesitara usuario y
   * contraseña, alguien dejaría una sesión de administrador abierta en el hall.
   *
   * Lo que hace seguro no pedirlas es lo que entrega: un token con UN permiso,
   * `kiosk:identify`, que sólo habilita mandar una foto y recibir un saludo. No
   * puede listar personas, ni ver sus fotos, ni consultar el registro de
   * accesos. Si alguien se lo lleva, lo único que puede hacer es preguntarle al
   * sistema si conoce una cara que ya tiene.
   *
   * Dura poco y no trae refresh: la pantalla lo vuelve a pedir sola.
   */
  async kiosco(): Promise<LoginResult> {
    const accessSecret = process.env.JWT_ACCESS_SECRET;
    if (!accessSecret) throw new Error('Falta JWT_ACCESS_SECRET');

    const user = await this.db.asAdmin(async (client) => {
      const { rows } = await client.query<UserRow>(
        `SELECT id, organization_id, email, password_hash, full_name, status
           FROM users WHERE lower(email) = 'kiosco@percepta.local' LIMIT 1`,
      );
      return rows[0] ?? null;
    });
    if (!user || user.status !== 'active') {
      throw new UnauthorizedException('La pantalla de bienvenida no está habilitada');
    }

    // La pantalla de bienvenida es una pieza del módulo "Ingreso de personas".
    // Si la organización no tiene ninguna cámara con ese módulo asignado, la
    // pantalla no existe, y la sesión que la enciende no se emite.
    //
    // El corte va acá y no sólo en el frontend porque este endpoint no pide
    // credenciales: es la llave que abre el kiosco, y una llave que se entrega
    // a cualquiera que la pida tiene que verificar ella misma que haya algo
    // detrás de la puerta. Esconder el botón deja la llave igual de repartida.
    //
    // El filtro por organización va explícito porque `asAdmin` no pasa por la
    // RLS: acá no hay nadie que recorte las filas por nosotros.
    const asignado = await this.db.asAdmin(async (client) => {
      const { rows } = await client.query<{ hay: boolean }>(
        `SELECT EXISTS (
           SELECT 1
             FROM camera_module_configs cmc
             JOIN ai_modules m ON m.id = cmc.ai_module_id
            WHERE m.module_key = $1 AND cmc.enabled
              AND cmc.organization_id = $2
         ) AS hay`,
        [MODULO_INGRESO_DE_PERSONAS, user.organization_id],
      );
      return rows[0]?.hay === true;
    });
    if (!asignado) {
      throw new UnauthorizedException(
        'La pantalla de bienvenida necesita que el módulo "Ingreso de personas" esté ' +
          'asignado a una cámara. Asignalo desde la sección Cámaras.',
      );
    }

    const permissions = await this.permissionsOf(user.id);
    // Cinturón y tirantes: aunque alguien le agregue roles a este usuario por
    // error, el token del kiosco nunca lleva más que su permiso.
    const soloKiosco = permissions.filter((p) => p === 'kiosk:identify');
    if (!soloKiosco.length) {
      throw new UnauthorizedException('La pantalla de bienvenida no tiene permisos asignados');
    }

    const ttl = Number(process.env.JWT_KIOSK_TTL ?? 43200);
    const accessToken = jwt.sign(
      { sub: user.id, org: user.organization_id, perms: soloKiosco },
      accessSecret,
      { algorithm: 'HS256', expiresIn: ttl },
    );

    this.logger.log('token de pantalla de bienvenida emitido');
    return {
      accessToken,
      refreshToken: '',
      expiresIn: ttl,
      user: {
        id: user.id,
        email: user.email,
        fullName: user.full_name,
        organizationId: user.organization_id,
        permissions: soloKiosco,
      },
    };
  }

  /** Permisos efectivos: unión de los de todos sus roles. */
  private async permissionsOf(userId: string): Promise<Permission[]> {
    return this.db.asAdmin(async (client) => {
      const { rows } = await client.query<{ permission_key: string }>(
        `SELECT DISTINCT rp.permission_key
           FROM user_roles ur
           JOIN role_permissions rp ON rp.role_id = ur.role_id
          WHERE ur.user_id = $1`,
        [userId],
      );
      return rows.map((r) => r.permission_key as Permission);
    });
  }

  /** Renueva el access token a partir de un refresh válido. */
  async refresh(refreshToken: string): Promise<LoginResult> {
    const refreshSecret = process.env.JWT_REFRESH_SECRET;
    const accessSecret = process.env.JWT_ACCESS_SECRET;
    if (!refreshSecret || !accessSecret) throw new Error('Faltan los secretos JWT');

    let sub: string;
    try {
      const claims = jwt.verify(refreshToken, refreshSecret, { algorithms: ['HS256'] }) as {
        sub: string;
        typ?: string;
      };
      if (claims.typ !== 'refresh') throw new Error('tipo incorrecto');
      sub = claims.sub;
    } catch {
      throw new UnauthorizedException('Refresh token inválido o expirado');
    }

    const user = await this.db.asAdmin(async (client) => {
      const { rows } = await client.query<UserRow>(
        `SELECT id, organization_id, email, password_hash, full_name, status FROM users WHERE id = $1`,
        [sub],
      );
      return rows[0] ?? null;
    });
    if (!user || user.status !== 'active') throw new UnauthorizedException('Usuario inválido');

    const permissions = await this.permissionsOf(user.id);
    const ttl = Number(process.env.JWT_ACCESS_TTL ?? 28800);
    const accessToken = jwt.sign(
      { sub: user.id, org: user.organization_id, perms: permissions },
      accessSecret,
      { algorithm: 'HS256', expiresIn: ttl },
    );

    return {
      accessToken,
      refreshToken,
      expiresIn: ttl,
      user: {
        id: user.id,
        email: user.email,
        fullName: user.full_name,
        organizationId: user.organization_id,
        permissions,
      },
    };
  }
}
