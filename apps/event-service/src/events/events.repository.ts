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
  review_title: string | null;
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
    reviewTitle: r.review_title ?? undefined,
    createdAt: r.created_at.toISOString(),
  };
}

const COLUMNS = `id, occurred_at, occurred_at::text AS occurred_at_raw,
  organization_id, site_id, camera_id, ai_module_id,
  module_key, module_version, event_type, event_class, severity, confidence, status,
  zone_ids, track_id, detection, metadata, reviewed_by, reviewed_at, review_note, review_title, created_at`;

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
      reviewTitle?: string;
    },
  ): Promise<EventDto | null> {
    const { rows } = await client.query<EventRow>(
      `UPDATE events
         SET status = $1, reviewed_by = $2, reviewed_at = now(),
             review_note = COALESCE($3, review_note),
             review_title = COALESCE($7, review_title)
       WHERE id = $4 AND occurred_at = $5 AND status = $6
       RETURNING ${COLUMNS}`,
      [
        params.toStatus,
        params.reviewedBy,
        params.reviewNote ?? null,
        params.id,
        params.occurredAt,
        params.fromStatus,
        params.reviewTitle ?? null,
      ],
    );
    return rows[0] ? toDto(rows[0]) : null;
  }

  /**
   * Alta de un evento desde el pipeline (rules-engine / ai-worker).
   *
   * `dedup_key` es la defensa contra el spam de alertas: si el mismo motivo ya
   * generó un evento en la ventana de deduplicación, el INSERT no crea otro.
   */
  async insert(
    client: PoolClient,
    e: {
      organizationId: string;
      siteId: string;
      cameraId: string;
      aiModuleId: string;
      moduleKey: string;
      moduleVersion: string;
      eventType: string;
      eventClass?: string;
      severity: string;
      confidence: number;
      dedupKey: string;
      zoneIds?: string[];
      trackId?: number;
      detection?: Record<string, unknown>;
      metadata?: Record<string, unknown>;
    },
  ): Promise<EventDto | null> {
    const { rows } = await client.query<EventRow>(
      `INSERT INTO events (occurred_at, organization_id, site_id, camera_id, ai_module_id,
                           module_key, module_version, event_type, event_class, severity,
                           confidence, dedup_key, zone_ids, track_id, detection, metadata)
       -- Casts explícitos: sin ellos Postgres infiere text para los parámetros
       -- dentro de COALESCE y falla contra uuid[]/jsonb.
       VALUES (now(), $1, $2, $3, $4, $5, $6, $7, COALESCE($8,'alert'), $9, $10, $11,
               COALESCE($12::uuid[], '{}'::uuid[]), $13,
               COALESCE($14::jsonb, '{}'::jsonb), COALESCE($15::jsonb, '{}'::jsonb))
       ON CONFLICT (dedup_key, occurred_at) DO NOTHING
       RETURNING ${COLUMNS}`,
      [
        e.organizationId, e.siteId, e.cameraId, e.aiModuleId,
        e.moduleKey, e.moduleVersion, e.eventType, e.eventClass ?? 'alert',
        e.severity, e.confidence, e.dedupKey, e.zoneIds ?? [],
        e.trackId ?? null,
        JSON.stringify(e.detection ?? {}), JSON.stringify(e.metadata ?? {}),
      ],
    );
    return rows[0] ? toDto(rows[0]) : null;
  }

  /**
   * Guarda la secuencia de esqueletos que produjo una alerta.
   *
   * Queda sin etiqueta hasta que un operador la revise: su veredicto es lo que
   * convierte esta fila en un ejemplo de entrenamiento.
   */
  async saveTrainingSample(
    client: PoolClient,
    s: {
      organizationId: string;
      cameraId: string;
      eventId: string;
      eventOccurredAt: string;
      sequence: number[][];
      predicted?: number;
      ruleConfidence?: number;
    },
  ): Promise<void> {
    if (!s.sequence?.length) return;
    await client.query(
      `INSERT INTO fall_training_samples
         (organization_id, camera_id, event_id, event_occurred_at, sequence,
          window_frames, n_features, predicted, rule_confidence)
       VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)`,
      [
        s.organizationId, s.cameraId, s.eventId, s.eventOccurredAt,
        JSON.stringify(s.sequence), s.sequence.length, s.sequence[0]?.length ?? 0,
        s.predicted ?? null, s.ruleConfidence ?? null,
      ],
    );
  }

  /** El veredicto humano se convierte en la etiqueta de la muestra. */
  async labelTrainingSample(
    client: PoolClient,
    eventId: string,
    label: 0 | 1,
    userId: string,
  ): Promise<number> {
    const r = await client.query(
      `UPDATE fall_training_samples
          SET label = $1, label_source = 'human', labeled_by = $2, labeled_at = now()
        WHERE event_id = $3 AND label IS NULL`,
      [label, userId, eventId],
    );
    return r.rowCount ?? 0;
  }

  /** Evidencia (imagen o clip) asociada a un evento. */
  async saveEvidence(
    client: PoolClient,
    e: {
      organizationId: string;
      eventId: string;
      eventOccurredAt: string;
      kind: 'image' | 'clip';
      storageKey: string;
      contentType: string;
      bytes: number;
      sha256: string;
      durationMs?: number;
      /** Lo que media-service grabó de verdad, no un valor supuesto. */
      preRollMs?: number;
      postRollMs?: number;
      title?: string;
      createdBy?: string;
      /**
       * `pending` = clip provisional, grabado al detectarse la alerta para que
       * el operador PUEDA VERLO antes de decidir. `ready` = evidencia
       * confirmada, se conserva.
       */
      status?: 'pending' | 'ready';
    },
  ): Promise<string> {
    // `event_occurred_at` se toma de la fila del evento, no del parámetro.
    //
    // `events` tiene clave primaria compuesta (id, occurred_at) y la guarda con
    // precisión de microsegundos, pero un timestamp que pasó por JavaScript
    // viene truncado a milisegundos. Insertar ese valor rompía la clave foránea
    // —"violates foreign key constraint evidences_event_fk"— y el clip, ya
    // grabado en disco, se perdía sin quedar registrado en ningún lado.
    //
    // Leyéndolo de la propia fila el desajuste no puede existir. El rango de un
    // segundo alrededor del valor aproximado está sólo para que el motor pueda
    // podar chunks de la hypertable en vez de recorrerlos todos.
    const { rows } = await client.query<{ id: string }>(
      `INSERT INTO evidences
         (organization_id, event_id, event_occurred_at, kind, storage_key, content_type,
          bytes, duration_ms, pre_roll_ms, post_roll_ms, sha256, status, title, created_by)
       SELECT $1, ev.id, ev.occurred_at, $4, $5, $6, $7, $8, $13, $14, $9, $10, $11, $12
         FROM events ev
        WHERE ev.id = $2
          AND ev.occurred_at BETWEEN $3::timestamptz - interval '1 second'
                                 AND $3::timestamptz + interval '1 second'
        LIMIT 1
       RETURNING id`,
      [
        e.organizationId, e.eventId, e.eventOccurredAt, e.kind, e.storageKey,
        e.contentType, e.bytes, e.durationMs ?? null, e.sha256, e.status ?? 'ready',
        e.title ?? null, e.createdBy ?? null,
        // Cuánto se grabó antes y después. Estaba fijo en 10 s cada uno, así
        // que al cambiar la duración del clip la base seguía afirmando diez y
        // el dato quedaba mintiendo sin que nada lo delatara.
        e.preRollMs ?? null, e.postRollMs ?? null,
      ],
    );
    if (!rows[0]) {
      throw new Error(`no existe el evento ${e.eventId} cerca de ${e.eventOccurredAt}`);
    }
    return rows[0].id;
  }

  /**
   * Promueve el clip provisional a evidencia conservada y le pone el nombre que
   * eligió el operador.
   */
  async confirmEvidence(
    client: PoolClient,
    eventId: string,
    title: string | undefined,
    userId: string,
  ): Promise<number> {
    const r = await client.query(
      `UPDATE evidences
          SET status = 'ready',
              title = COALESCE($1, title),
              created_by = COALESCE(created_by, $2)
        WHERE event_id = $3 AND status = 'pending'`,
      [title ?? null, userId, eventId],
    );
    return r.rowCount ?? 0;
  }

  /**
   * Organizaciones que tienen clips a purgar.
   *
   * Se consulta con el contexto de tenant de cada una ya puesto, así que en la
   * práctica devuelve 0 o 1 fila. Existe para que la purga no tenga que
   * recorrer organizaciones que no tienen nada pendiente.
   */
  async orgsConEvidenciaPurgable(client: PoolClient): Promise<string[]> {
    const { rows } = await client.query<{ organization_id: string }>(
      `SELECT DISTINCT organization_id FROM evidences WHERE status IN ('pending','expired')`,
    );
    return rows.map((r) => r.organization_id);
  }

  /** Clips provisionales de un evento, para decidir qué hacer con ellos. */
  async pendingEvidence(client: PoolClient, eventId: string): Promise<{ id: string; storageKey: string }[]> {
    const { rows } = await client.query<{ id: string; storage_key: string }>(
      `SELECT id, storage_key FROM evidences WHERE event_id = $1 AND status = 'pending'`,
      [eventId],
    );
    return rows.map((r) => ({ id: r.id, storageKey: r.storage_key }));
  }

  /**
   * Borra de la detección el rostro y el vector facial que acompañaban a la
   * pregunta "¿reconocés a esta persona?".
   *
   * Se hace sobre la fila, no sobre una copia: es un dato biométrico, y la
   * única forma de que deje de existir es que deje de estar en la tabla.
   */
  async stripBiometrics(client: PoolClient, id: string): Promise<number> {
    const r = await client.query(
      `UPDATE events
          SET detection = (detection - 'faceThumbnail' - 'faceEmbedding' - 'embedding')
        WHERE id = $1
          AND detection ?| ARRAY['faceThumbnail','faceEmbedding','embedding']`,
      [id],
    );
    return r.rowCount ?? 0;
  }

  /** Igual que `stripBiometrics`, para las alertas que nadie contestó nunca. */
  async stripStaleBiometrics(client: PoolClient, dias: number): Promise<number> {
    const r = await client.query(
      `UPDATE events
          SET detection = (detection - 'faceThumbnail' - 'faceEmbedding' - 'embedding')
        WHERE occurred_at < now() - ($1 || ' days')::interval
          AND detection ?| ARRAY['faceThumbnail','faceEmbedding','embedding']`,
      [String(Math.max(dias, 1))],
    );
    return r.rowCount ?? 0;
  }

  async deleteEvidence(client: PoolClient, ids: string[]): Promise<number> {
    if (!ids.length) return 0;
    const r = await client.query(`DELETE FROM evidences WHERE id = ANY($1::uuid[])`, [ids]);
    return r.rowCount ?? 0;
  }

  /**
   * Marca clips cuyo archivo no se pudo borrar todavía (en Windows, un archivo
   * que se está reproduciendo no se puede eliminar).
   *
   * NO se borra la fila: sin ella el archivo queda en disco sin que nada lo
   * referencie —invisible para la UI, para el borrado y para la retención— y
   * eso es un video de una persona que ya nadie va a poder encontrar para
   * eliminar. `expired` lo oculta de la revisión pero lo deja anotado para que
   * la purga lo reintente.
   */
  async markEvidenceExpired(client: PoolClient, ids: string[]): Promise<number> {
    if (!ids.length) return 0;
    const r = await client.query(
      `UPDATE evidences SET status = 'expired' WHERE id = ANY($1::uuid[])`,
      [ids],
    );
    return r.rowCount ?? 0;
  }

  /**
   * Clips a eliminar: los provisionales que nadie revisó dentro del plazo, más
   * los que quedaron marcados porque su archivo estaba en uso.
   */
  async evidenceToPurge(client: PoolClient, days: number): Promise<{ id: string; storageKey: string }[]> {
    const { rows } = await client.query<{ id: string; storage_key: string }>(
      `SELECT id, storage_key FROM evidences
        WHERE status = 'expired'
           OR (status = 'pending' AND created_at < now() - ($1 || ' days')::interval)`,
      [String(days)],
    );
    return rows.map((r) => ({ id: r.id, storageKey: r.storage_key }));
  }

  async listEvidences(client: PoolClient, eventId: string): Promise<Record<string, unknown>[]> {
    const { rows } = await client.query(
      `SELECT id, kind, storage_key, content_type, bytes, duration_ms,
              pre_roll_ms, post_roll_ms, title, status, created_at
         FROM evidences WHERE event_id = $1 ORDER BY created_at`,
      [eventId],
    );
    return rows.map((r) => ({
      id: r.id,
      kind: r.kind,
      storageKey: r.storage_key,
      contentType: r.content_type,
      bytes: Number(r.bytes),
      durationMs: r.duration_ms,
      preRollMs: r.pre_roll_ms,
      postRollMs: r.post_roll_ms,
      title: r.title,
      status: r.status,
      createdAt: r.created_at?.toISOString?.() ?? r.created_at,
    }));
  }

  /** Estado del aprendizaje: cuántas muestras hay y cuántas ya se revisaron. */
  async trainingStats(client: PoolClient): Promise<Record<string, number>> {
    const { rows } = await client.query<{ total: string; caidas: string; falsas: string; pendientes: string }>(
      `SELECT count(*)                                    AS total,
              count(*) FILTER (WHERE label = 1)           AS caidas,
              count(*) FILTER (WHERE label = 0)           AS falsas,
              count(*) FILTER (WHERE label IS NULL)       AS pendientes
         FROM fall_training_samples`,
    );
    const r = rows[0];
    return {
      total: Number(r?.total ?? 0),
      confirmadas: Number(r?.caidas ?? 0),
      falsosPositivos: Number(r?.falsas ?? 0),
      pendientes: Number(r?.pendientes ?? 0),
    };
  }

  /**
   * Los números del panel: qué pasó hoy, comparado con ayer.
   *
   * ── Por qué "ayer" se corta a esta misma hora ───────────────────────
   *
   * Comparar el día en curso contra el día entero de ayer da siempre una
   * caída: a las diez de la mañana no se lleva ni medio día contra veinticuatro
   * horas. La comparación se hace contra el MISMO tramo de ayer —de las 00:00 a
   * esta hora—, que es la única que responde "¿hoy viene más movido que ayer?".
   *
   * Todo sale de `events` con la conexión del inquilino, así que las políticas
   * de fila ya acotan a su organización.
   */
  async estadisticas(client: PoolClient): Promise<{
    hoy: number;
    ayer: number;
    criticosHoy: number;
    criticosAyer: number;
    porHora: number[];
    porTipo: { eventType: string; moduleKey: string; total: number }[];
    porModulo: { moduleKey: string; total: number }[];
  }> {
    const HOY = "occurred_at >= date_trunc('day', now())";
    const AYER =
      "occurred_at >= date_trunc('day', now()) - interval '1 day' " +
      "AND occurred_at < now() - interval '1 day'";
    const CRITICO = "severity IN ('critical','high')";

    const totales = await client.query<{
      hoy: string; ayer: string; criticos_hoy: string; criticos_ayer: string;
    }>(
      `SELECT count(*) FILTER (WHERE ${HOY})                      AS hoy,
              count(*) FILTER (WHERE ${AYER})                     AS ayer,
              count(*) FILTER (WHERE ${HOY} AND ${CRITICO})       AS criticos_hoy,
              count(*) FILTER (WHERE ${AYER} AND ${CRITICO})      AS criticos_ayer
         FROM events
        WHERE occurred_at >= date_trunc('day', now()) - interval '1 day'`,
    );

    const horas = await client.query<{ hora: string; total: string }>(
      `SELECT date_part('hour', occurred_at)::int AS hora, count(*) AS total
         FROM events
        WHERE ${HOY}
        GROUP BY 1`,
    );
    // Las 24 horas siempre, con cero donde no hubo nada: un gráfico al que le
    // faltan las horas vacías miente sobre la forma del día.
    const porHora = Array<number>(24).fill(0);
    for (const r of horas.rows) porHora[Number(r.hora)] = Number(r.total);

    // El módulo viaja con el tipo para que la pantalla lo pinte con el color
    // que ese módulo ya tiene en el catálogo, en vez de estrenar una paleta.
    const tipos = await client.query<{ event_type: string; module_key: string; total: string }>(
      `SELECT event_type, module_key, count(*) AS total
         FROM events WHERE ${HOY}
        GROUP BY 1, 2 ORDER BY 3 DESC`,
    );

    const modulos = await client.query<{ module_key: string; total: string }>(
      `SELECT module_key, count(*) AS total
         FROM events WHERE ${HOY}
        GROUP BY 1 ORDER BY 2 DESC LIMIT 6`,
    );

    const t = totales.rows[0];
    return {
      hoy: Number(t?.hoy ?? 0),
      ayer: Number(t?.ayer ?? 0),
      criticosHoy: Number(t?.criticos_hoy ?? 0),
      criticosAyer: Number(t?.criticos_ayer ?? 0),
      porHora,
      porTipo: tipos.rows.map((r) => ({
        eventType: r.event_type, moduleKey: r.module_key, total: Number(r.total),
      })),
      porModulo: modulos.rows.map((r) => ({ moduleKey: r.module_key, total: Number(r.total) })),
    };
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
