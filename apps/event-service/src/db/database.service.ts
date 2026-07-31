import { Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { Pool, type PoolClient } from 'pg';

/** UUID v1-v8, para validar el organization_id antes de tocar la base. */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

@Injectable()
export class DatabaseService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(DatabaseService.name);
  private pool!: Pool;

  onModuleInit(): void {
    const connectionString = process.env.DATABASE_URL;
    if (!connectionString) {
      throw new Error('DATABASE_URL no está definida');
    }
    // Debe ser el rol de aplicación (NOBYPASSRLS). Si alguien apunta esto al
    // superusuario, la RLS deja de aplicar y el aislamiento multi-tenant se cae.
    if (/:\/\/percepta:/.test(connectionString)) {
      this.logger.warn(
        'DATABASE_URL usa el superusuario "percepta": la RLS NO se aplicará. ' +
          'Usá percepta_app para los servicios.',
      );
    }
    this.pool = new Pool({ connectionString, max: 10 });
  }

  async onModuleDestroy(): Promise<void> {
    await this.pool?.end();
  }

  /**
   * Ejecuta `fn` dentro de una transacción con el contexto de tenant seteado,
   * que es lo que activa las policies de Row-Level Security.
   *
   * Detalles que importan:
   * - `set_config(..., true)` (no `SET LOCAL ... = $1`): `SET` no admite
   *   parámetros, así que interpolar el id sería una vía de inyección SQL.
   * - El scope es la transacción: al terminar, el contexto se descarta y la
   *   conexión vuelve limpia al pool (nunca hereda el tenant anterior).
   */
  async withTenant<T>(organizationId: string, fn: (client: PoolClient) => Promise<T>): Promise<T> {
    if (!UUID_RE.test(organizationId)) {
      throw new Error('organizationId inválido');
    }
    const client = await this.pool.connect();
    try {
      await client.query('BEGIN');
      await client.query('SELECT set_config($1, $2, true)', ['app.current_org', organizationId]);
      const out = await fn(client);
      await client.query('COMMIT');
      return out;
    } catch (err) {
      await client.query('ROLLBACK').catch(() => undefined);
      throw err;
    } finally {
      client.release();
    }
  }

  async ping(): Promise<boolean> {
    const { rows } = await this.pool.query('SELECT 1 AS ok');
    return rows[0]?.ok === 1;
  }
}
