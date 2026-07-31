import { Injectable } from '@nestjs/common';
import type { PoolClient } from 'pg';
import type { EventDto, EventStatus } from '@percepta/contracts';

export interface ListFilters {
  status?: EventStatus;
  cameraId?: string;
  siteId?: string;
  eventType?: string;
  severity?: string;
  from?: string;
  to?: string;
  limit: number;
  offset: number;
}

/** Fila cruda de `events` (snake_case) tal como vuelve de Postgres. */
interface EventRow {
  id: string;
  occurred_at: Date;
  /**
   * `occurred_at` con precisión ÍNTEGRA (microsegundos) tal como la guarda
   * Postgres. `Date` de JS solo llega al milisegundo, así que usar el campo
   * convertido como clave en un WHERE nunca haría match. Este es el valor que
   * se usa para direccionar la fila por su PK compuesta.
   */
  occurred_at_raw: string;
  organization_id: string;
  site_id: string;
  camera_id: string;
  ai_module_id: string;
  module_key: string;
  module_version: string;
  event_type: string;
  event_class: string;
  severity: string;
  confidence: string; // numeric → string en node-postgres
  status: EventStatus;
  zone_ids: string[];
  track_id: string | null; // bigint → string
  detection: Record<string, unknown>;
  metadata: Record<string, unknown>;
  reviewed_by: string | null;
  reviewed_at: Date | null;
  review_note: string | null;
  created_at: Date;
}

function toDto(r: EventRow): EventDto {
  return {
    id: r.id,
    occurredAt: r.occurred_at.toISOString(),
    organizationId: r.organization_id,
    siteId: r.site_id,
    cameraId: r.camera_id,
    aiModuleId: r.ai_module_id,
    moduleKey: r.module_key,
    moduleVersion: r.module_version,
    eventType: r.event_type,
    eventClass: r.event_class as EventDto['eventClass'],
    severity: r.severity as EventDto['severity'],
    // numeric(5,4) llega como string: convertir explícitamente para no exponer
    // "0.9312" (string) donde el contrato declara number.
    confidence: Number(r.confidence),
    status: r.status,
    zoneIds: r.zone_ids ?? [],
    trackId: r.track_id === null ? undefined : Number(r.track_id),
    detection: r.detection ?? {},
    metadata: r.metadata ?? {},
    reviewedBy: r.reviewed_by ?? undefined,
    reviewedAt: r.reviewed_at?.toISOString(),
    reviewNote: r.review_note ?? undefined,
    createdAt: r.created_at.toISOString(),
  };
}

const COLUMNS = `id, occurred_at, occurred_at::text AS occurred_at_raw,
  organization_id, site_id, camera_id, ai_module_id,
  module_key, module_version, event_type, event_class, severity, confidence, status,
  zone_ids, track_id, detection, metadata, reviewed_by, reviewed_at, review_note, created_at`;

/** Evento + la clave de tiempo sin pérdida de precisión (uso interno). */
export interface EventWithKey {
  dto: EventDto;
  occurredAtRaw: string;
}

@Injectable()
export class EventsRepository {
  /** Listado filtrado. Las policies de RLS acotan por tenant automáticamente. */
  async list(
    client: PoolClient,
    f: ListFilters,
  ): Promise<{ items: EventDto[]; total: number }> {
    const where: string[] = [];
    const params: unknown[] = [];
    const add = (sql: string, value: unknown): void => {
      params.push(value);
      where.push(sql.replace('?', `$${params.length}`));
    };

    if (f.status) add('status = ?', f.status);
    if (f.cameraId) add('camera_id = ?', f.cameraId);
    if (f.siteId) add('site_id = ?', f.siteId);
    if (f.eventType) add('event_type = ?', f.eventType);
    if (f.severity) add('severity = ?', f.severity);
    if (f.from) add('occurred_at >= ?', f.from);
    if (f.to) add('occurred_at <= ?', f.to);

    const clause = where.length ? `WHERE ${where.join(' AND ')}` : '';

    const countSql = `SELECT count(*)::bigint AS total FROM events ${clause}`;
    const countRes = await client.query<{ total: string }>(countSql, params);

    const listSql = `SELECT ${COLUMNS} FROM events ${clause}
      ORDER BY occurred_at DESC, id DESC
      LIMIT $${params.length + 1} OFFSET $${params.length + 2}`;
    const rows = await client.query<EventRow>(listSql, [...params, f.limit, f.offset]);

    return { items: rows.rows.map(toDto), total: Number(countRes.rows[0]?.total ?? 0) };
  }

  /**
   * Busca por id. `events` tiene PK compuesta (id, occurred_at) por ser
   * hypertable; buscar solo por id es correcto (UUID v7 es único) aunque
   * recorra chunks. `occurredAt` opcional acota la búsqueda a un chunk.
   */
  async findById(client: PoolClient, id: string, occurredAt?: string): Promise<EventDto | null> {
    const sql = occurredAt
      ? `SELECT ${COLUMNS} FROM events WHERE id = $1 AND occurred_at = $2`
      : `SELECT ${COLUMNS} FROM events WHERE id = $1`;
    const params = occurredAt ? [id, occurredAt] : [id];
    const { rows } = await client.query<EventRow>(sql, params);
    return rows[0] ? toDto(rows[0]) : null;
  }

  /**
   * Bloquea la fila para una transición sin condición de carrera. Devuelve
   * también `occurredAtRaw` para poder direccionar después la PK compuesta sin
   * la pérdida de precisión que introduce `Date` de JS.
   */
  async findByIdForUpdate(client: PoolClient, id: string): Promise<EventWithKey | null> {
    const { rows } = await client.query<EventRow>(
      `SELECT ${COLUMNS} FROM events WHERE id = $1 FOR UPDATE`,
      [id],
    );
    const r = rows[0];
    return r ? { dto: toDto(r), occurredAtRaw: r.occurred_at_raw } : null;
  }

  /**
   * Aplica la transición. Usa la PK compuesta completa y re-verifica el estado
   * de origen (optimistic guard): si otro operador transicionó el evento entre
   * el SELECT y el UPDATE, no se pisa.
   */
  async applyTransition(
    client: PoolClient,
    params: {
      id: string;
      /** Debe ser `occurredAtRaw` (precisión de microsegundos), no el ISO del DTO. */
      occurredAt: string;
      fromStatus: EventStatus;
      toStatus: EventStatus;
      reviewedBy: string;
      reviewNote?: string;
    },
  ): Promise<EventDto | null> {
    const { rows } = await client.query<EventRow>(
      `UPDATE events
         SET status = $1, reviewed_by = $2, reviewed_at = now(),
             review_note = COALESCE($3, review_note)
       WHERE id = $4 AND occurred_at = $5 AND status = $6
       RETURNING ${COLUMNS}`,
      [
        params.toStatus,
        params.reviewedBy,
        params.reviewNote ?? null,
        params.id,
        params.occurredAt,
        params.fromStatus,
      ],
    );
    return rows[0] ? toDto(rows[0]) : null;
  }

  /** Rastro de auditoría append-only, en la MISMA transacción que la transición. */
  async writeAudit(
    client: PoolClient,
    a: {
      organizationId: string;
      actorUserId: string;
      action: string;
      resourceId: string;
      requestId?: string;
      detail: Record<string, unknown>;
    },
  ): Promise<void> {
    await client.query(
      `INSERT INTO audit_logs
         (occurred_at, organization_id, actor_user_id, actor_kind, action,
          resource_type, resource_id, request_id, detail)
       VALUES (now(), $1, $2, 'user', $3, 'event', $4, $5, $6)`,
      [a.organizationId, a.actorUserId, a.action, a.resourceId, a.requestId ?? null, a.detail],
    );
  }
}
