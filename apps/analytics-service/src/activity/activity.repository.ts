import { Injectable } from '@nestjs/common';
import type { PoolClient } from 'pg';

/** Una ventana medida por el módulo, tal como llega del pipeline. */
export interface MuestraEntrada {
  organizationId: string;
  siteId: string;
  cameraId: string;
  zoneId?: string | null;
  zoneName: string;
  moduleKey: string;
  moduleVersion: string;
  /** Epoch en segundos: inicio y fin de la ventana medida. */
  from: number;
  to: number;
  occupiedSeconds: number;
  phoneSeconds: number;
  emptySeconds: number;
  uncoveredSeconds: number;
  maxPeople: number;
  meanOccupancy: number;
}

export interface FiltroInforme {
  desde: string;
  hasta: string;
  cameraId?: string;
  zoneId?: string;
  /** Granularidad del desglose temporal. */
  bucket: 'hour' | 'day';
}

export interface FilaInforme {
  periodo: string;
  cameraId: string;
  zoneId: string | null;
  zoneName: string;
  windowSeconds: number;
  occupiedSeconds: number;
  phoneSeconds: number;
  emptySeconds: number;
  uncoveredSeconds: number;
  maxPeople: number;
  meanOccupancy: number;
}

@Injectable()
export class ActivityRepository {
  /**
   * Guarda una ventana medida.
   *
   * `occurred_at` es el FIN de la ventana, y se deriva del timestamp que envió
   * el pipeline en vez de usar `now()`: si el worker se atrasa o reenvía, la
   * medición tiene que quedar en el momento en que se observó, no en el momento
   * en que llegó. Un informe fechado por hora de llegada miente sobre cuándo
   * pasaron las cosas.
   */
  async insertar(client: PoolClient, m: MuestraEntrada): Promise<string | null> {
    const ventana = Math.max(m.to - m.from, 0);
    const { rows } = await client.query<{ id: string }>(
      `INSERT INTO activity_samples
         (occurred_at, organization_id, site_id, camera_id, zone_id, zone_name,
          module_key, module_version, window_seconds, occupied_seconds,
          phone_seconds, empty_seconds, uncovered_seconds, max_people, mean_occupancy)
       VALUES (to_timestamp($1), $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
       RETURNING id`,
      [
        m.to,
        m.organizationId,
        m.siteId,
        m.cameraId,
        m.zoneId ?? null,
        m.zoneName,
        m.moduleKey,
        m.moduleVersion,
        ventana,
        m.occupiedSeconds,
        m.phoneSeconds,
        m.emptySeconds,
        m.uncoveredSeconds,
        m.maxPeople,
        m.meanOccupancy,
      ],
    );
    return rows[0]?.id ?? null;
  }

  /**
   * Desglose temporal del informe.
   *
   * Lee de `activity_hourly`, el agregado continuo que TimescaleDB mantiene al
   * día: un informe de un mes recorre cientos de filas en vez de millones. Para
   * el desglose diario se re-agrupan las horas, que es exacto porque los
   * segundos son aditivos.
   */
  async desglose(client: PoolClient, f: FiltroInforme): Promise<FilaInforme[]> {
    const params: unknown[] = [f.desde, f.hasta];
    let filtro = '';
    if (f.cameraId) {
      params.push(f.cameraId);
      filtro += ` AND camera_id = $${params.length}`;
    }
    if (f.zoneId) {
      params.push(f.zoneId);
      filtro += ` AND zone_id = $${params.length}`;
    }

    const unidad = f.bucket === 'day' ? 'day' : 'hour';
    const { rows } = await client.query(
      `SELECT date_trunc('${unidad}', hora)        AS periodo,
              camera_id,
              zone_id,
              max(zone_name)                        AS zone_name,
              sum(window_seconds)                   AS window_seconds,
              sum(occupied_seconds)                 AS occupied_seconds,
              sum(phone_seconds)                    AS phone_seconds,
              sum(empty_seconds)                    AS empty_seconds,
              sum(uncovered_seconds)                AS uncovered_seconds,
              max(max_people)                       AS max_people,
              CASE WHEN sum(occupied_seconds + empty_seconds) > 0
                   THEN sum(mean_occupancy * (occupied_seconds + empty_seconds))
                        / sum(occupied_seconds + empty_seconds)
                   ELSE 0 END                       AS mean_occupancy
         FROM activity_hourly
        WHERE hora >= $1 AND hora < $2 ${filtro}
        GROUP BY periodo, camera_id, zone_id
        ORDER BY periodo, zone_name`,
      params,
    );
    return rows.map(toFila);
  }

  /** Totales por puesto en todo el rango: es la tabla principal del informe. */
  async porPuesto(client: PoolClient, f: FiltroInforme): Promise<FilaInforme[]> {
    const params: unknown[] = [f.desde, f.hasta];
    let filtro = '';
    if (f.cameraId) {
      params.push(f.cameraId);
      filtro += ` AND camera_id = $${params.length}`;
    }

    const { rows } = await client.query(
      `SELECT NULL::timestamptz                     AS periodo,
              camera_id,
              zone_id,
              max(zone_name)                        AS zone_name,
              sum(window_seconds)                   AS window_seconds,
              sum(occupied_seconds)                 AS occupied_seconds,
              sum(phone_seconds)                    AS phone_seconds,
              sum(empty_seconds)                    AS empty_seconds,
              sum(uncovered_seconds)                AS uncovered_seconds,
              max(max_people)                       AS max_people,
              CASE WHEN sum(occupied_seconds + empty_seconds) > 0
                   THEN sum(mean_occupancy * (occupied_seconds + empty_seconds))
                        / sum(occupied_seconds + empty_seconds)
                   ELSE 0 END                       AS mean_occupancy
         FROM activity_hourly
        WHERE hora >= $1 AND hora < $2 ${filtro}
        GROUP BY camera_id, zone_id
        ORDER BY occupied_seconds DESC`,
      params,
    );
    return rows.map(toFila);
  }

  /**
   * Muestras que todavía no entraron al agregado continuo.
   *
   * El agregado se actualiza cada 10 minutos, así que sin esto el informe se
   * vería congelado en el pasado inmediato y nadie confiaría en él mientras
   * mira la cámara en vivo. Se consulta la tabla cruda sólo para ese tramo
   * reciente, que es chico.
   */
  async recientes(client: PoolClient, desdeIso: string): Promise<FilaInforme[]> {
    const { rows } = await client.query(
      `SELECT NULL::timestamptz                     AS periodo,
              camera_id,
              zone_id,
              max(zone_name)                        AS zone_name,
              sum(window_seconds)                   AS window_seconds,
              sum(occupied_seconds)                 AS occupied_seconds,
              sum(phone_seconds)                    AS phone_seconds,
              sum(empty_seconds)                    AS empty_seconds,
              sum(uncovered_seconds)                AS uncovered_seconds,
              max(max_people)                       AS max_people,
              CASE WHEN sum(occupied_seconds + empty_seconds) > 0
                   THEN sum(mean_occupancy * (occupied_seconds + empty_seconds))
                        / sum(occupied_seconds + empty_seconds)
                   ELSE 0 END                       AS mean_occupancy
         FROM activity_samples
        WHERE occurred_at >= $1
        GROUP BY camera_id, zone_id`,
      [desdeIso],
    );
    return rows.map(toFila);
  }

  /** Hasta cuándo llega el agregado continuo (para saber qué completar). */
  async ultimaHoraAgregada(client: PoolClient): Promise<string | null> {
    const { rows } = await client.query<{ h: Date | null }>(
      `SELECT max(hora) AS h FROM activity_hourly`,
    );
    return rows[0]?.h ? rows[0].h.toISOString() : null;
  }
}

function toFila(r: Record<string, unknown>): FilaInforme {
  const n = (v: unknown): number => Number(v ?? 0);
  return {
    periodo: r.periodo instanceof Date ? r.periodo.toISOString() : String(r.periodo ?? ''),
    cameraId: String(r.camera_id),
    zoneId: (r.zone_id as string | null) ?? null,
    zoneName: String(r.zone_name ?? ''),
    windowSeconds: n(r.window_seconds),
    occupiedSeconds: n(r.occupied_seconds),
    phoneSeconds: n(r.phone_seconds),
    emptySeconds: n(r.empty_seconds),
    uncoveredSeconds: n(r.uncovered_seconds),
    maxPeople: n(r.max_people),
    meanOccupancy: n(r.mean_occupancy),
  };
}
