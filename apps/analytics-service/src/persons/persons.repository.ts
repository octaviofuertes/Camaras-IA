import { Injectable } from '@nestjs/common';
import type { PoolClient } from 'pg';

export interface AltaPersona {
  organizationId: string;
  displayName: string;
  /** Quién documentó el consentimiento y con qué base legal. Obligatorio. */
  consentRecordedBy: string;
  consentBasis: string;
  notes?: string;
}

export interface PersonaDto {
  id: string;
  displayName: string;
  active: boolean;
  consentBasis: string;
  consentAt: string;
  facesCount: number;
  createdAt: string;
}

@Injectable()
export class PersonsRepository {
  /**
   * Da de alta a una persona.
   *
   * No hay forma de llamar a esto sin consentimiento: los tres campos son NOT
   * NULL en la tabla y la base legal tiene un CHECK de longitud mínima. Si
   * alguien intenta saltearlo, falla en la base — no en esta capa, que podría
   * reescribirse.
   */
  async alta(client: PoolClient, p: AltaPersona): Promise<string> {
    const { rows } = await client.query<{ id: string }>(
      `INSERT INTO persons (organization_id, display_name, consent_recorded_by, consent_basis, notes)
       VALUES ($1, $2, $3, $4, $5)
       RETURNING id`,
      [p.organizationId, p.displayName.trim(), p.consentRecordedBy, p.consentBasis.trim(), p.notes ?? null],
    );
    return rows[0].id;
  }

  async agregarRostro(
    client: PoolClient,
    organizationId: string,
    personId: string,
    embedding: number[],
    quality: number,
  ): Promise<string> {
    const { rows } = await client.query<{ id: string }>(
      `INSERT INTO person_faces (organization_id, person_id, embedding, quality)
       VALUES ($1, $2, $3, $4)
       RETURNING id`,
      [organizationId, personId, embedding, quality],
    );
    return rows[0].id;
  }

  /** Galería que consume el módulo de identificación. */
  async galeria(client: PoolClient): Promise<{ id: string; displayName: string; embeddings: number[][] }[]> {
    const { rows } = await client.query<{
      id: string;
      display_name: string;
      embeddings: number[][] | null;
    }>(
      `SELECT p.id, p.display_name,
              array_agg(f.embedding) FILTER (WHERE f.id IS NOT NULL) AS embeddings
         FROM persons p
         LEFT JOIN person_faces f ON f.person_id = p.id
        WHERE p.active
        GROUP BY p.id, p.display_name`,
    );
    return rows.map((r) => ({
      id: r.id,
      displayName: r.display_name,
      embeddings: (r.embeddings ?? []).map((e) => (e as unknown as string[]).map(Number)),
    }));
  }

  async listar(client: PoolClient): Promise<PersonaDto[]> {
    const { rows } = await client.query(
      `SELECT p.id, p.display_name, p.active, p.consent_basis, p.consent_at, p.created_at,
              count(f.id) AS faces
         FROM persons p
         LEFT JOIN person_faces f ON f.person_id = p.id
        GROUP BY p.id
        ORDER BY p.display_name`,
    );
    return rows.map((r) => ({
      id: String(r.id),
      displayName: String(r.display_name),
      active: Boolean(r.active),
      consentBasis: String(r.consent_basis),
      consentAt: r.consent_at?.toISOString?.() ?? String(r.consent_at),
      facesCount: Number(r.faces ?? 0),
      createdAt: r.created_at?.toISOString?.() ?? String(r.created_at),
    }));
  }

  /**
   * Baja definitiva. Las plantillas faciales y los tiempos medidos se van por
   * cascada: es el derecho de supresión, implementado de forma que nadie pueda
   * olvidarse un paso.
   */
  async baja(client: PoolClient, personId: string): Promise<boolean> {
    const r = await client.query(`DELETE FROM persons WHERE id = $1`, [personId]);
    return (r.rowCount ?? 0) > 0;
  }

  /** Una ventana de actividad atribuida a una persona (o a nadie). */
  async insertarMuestraPersona(
    client: PoolClient,
    m: {
      organizationId: string;
      siteId: string;
      cameraId: string;
      zoneId?: string | null;
      zoneName: string;
      personId?: string | null;
      from: number;
      to: number;
      presentSeconds: number;
      phoneSeconds: number;
    },
  ): Promise<string | null> {
    const { rows } = await client.query<{ id: string }>(
      `INSERT INTO person_activity_samples
         (occurred_at, organization_id, site_id, camera_id, zone_id, zone_name,
          person_id, window_seconds, present_seconds, phone_seconds)
       VALUES (to_timestamp($1), $2, $3, $4, $5, $6, $7, $8, $9, $10)
       RETURNING id`,
      [
        m.to, m.organizationId, m.siteId, m.cameraId, m.zoneId ?? null, m.zoneName,
        m.personId ?? null, Math.max(m.to - m.from, 0), m.presentSeconds, m.phoneSeconds,
      ],
    );
    return rows[0]?.id ?? null;
  }

  /**
   * Informe con nombres.
   *
   * El tiempo sin identificar se devuelve como una fila con `personId` nulo, no
   * repartido entre los identificados: repartirlo le atribuiría a un empleado
   * minutos que quizá fueron de un visitante.
   */
  async informeNominal(
    client: PoolClient,
    desde: string,
    hasta: string,
    cameraId?: string,
  ): Promise<
    { personId: string | null; displayName: string; presentSeconds: number; phoneSeconds: number }[]
  > {
    const params: unknown[] = [desde, hasta];
    let filtro = '';
    if (cameraId) {
      params.push(cameraId);
      filtro = ` AND s.camera_id = $${params.length}`;
    }
    const { rows } = await client.query(
      `SELECT s.person_id,
              COALESCE(p.display_name, 'Sin identificar') AS display_name,
              sum(s.present_seconds) AS present_seconds,
              sum(s.phone_seconds)   AS phone_seconds
         FROM person_activity_samples s
         LEFT JOIN persons p ON p.id = s.person_id
        WHERE s.occurred_at >= $1 AND s.occurred_at < $2 ${filtro}
        GROUP BY s.person_id, p.display_name
        ORDER BY sum(s.present_seconds) DESC`,
      params,
    );
    return rows.map((r) => ({
      personId: (r.person_id as string | null) ?? null,
      displayName: String(r.display_name),
      presentSeconds: Number(r.present_seconds ?? 0),
      phoneSeconds: Number(r.phone_seconds ?? 0),
    }));
  }
}
