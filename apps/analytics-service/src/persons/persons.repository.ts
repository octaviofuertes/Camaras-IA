import { Injectable } from '@nestjs/common';
import type { PoolClient } from 'pg';

export interface AltaPersona {
  organizationId: string;
  displayName: string;
  /** Si tiene permitido estar donde mira la cámara. */
  hasAccess: boolean;
  /** Miniatura JPEG en base64, para poder verificar la ficha a ojo. */
  photo?: string | null;
  /** Quién documentó el consentimiento y con qué base legal. Obligatorio. */
  consentRecordedBy: string;
  consentBasis: string;
  notes?: string;
}

export interface PersonaDto {
  id: string;
  displayName: string;
  active: boolean;
  hasAccess: boolean;
  /** Miniatura para reconocer la ficha de un vistazo. */
  photo: string | null;
  consentBasis: string;
  consentAt: string;
  facesCount: number;
  createdAt: string;
}

/**
 * Cuánto puede faltar entre dos apariciones de la misma persona para que sigan
 * siendo la misma visita.
 *
 * Cinco minutos: alguien que se levanta al baño y vuelve estuvo todo ese rato
 * en el lugar, y contarlo como dos entradas no lo hace más exacto, lo hace más
 * difícil de leer. Una ausencia más larga sí es otra visita.
 */
const TOLERANCIA_UNIR_SEGUNDOS = 300;

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
      `INSERT INTO persons
         (organization_id, display_name, consent_recorded_by, consent_basis, notes,
          has_access, access_decided_by, access_decided_at, photo)
       VALUES ($1, $2, $3, $4, $5, $6, $3, now(), $7)
       RETURNING id`,
      [p.organizationId, p.displayName.trim(), p.consentRecordedBy, p.consentBasis.trim(),
       p.notes ?? null, p.hasAccess, p.photo ?? null],
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
  async galeria(
    client: PoolClient,
  ): Promise<{ id: string; displayName: string; hasAccess: boolean; embeddings: number[][] }[]> {
    const { rows } = await client.query<{
      id: string;
      display_name: string;
      has_access: boolean;
      embeddings: number[][] | null;
    }>(
      `SELECT p.id, p.display_name, p.has_access,
              array_agg(f.embedding) FILTER (WHERE f.id IS NOT NULL) AS embeddings
         FROM persons p
         LEFT JOIN person_faces f ON f.person_id = p.id
        WHERE p.active
        GROUP BY p.id, p.display_name, p.has_access`,
    );
    return rows.map((r) => ({
      id: r.id,
      displayName: r.display_name,
      // Viaja con la galería porque la decisión de alertar se toma en el
      // módulo, en el mismo frame en que se reconoce a la persona. Preguntarlo
      // por HTTP en ese momento pondría una llamada de red en el camino de una
      // alerta urgente.
      hasAccess: r.has_access,
      embeddings: (r.embeddings ?? []).map((e) => (e as unknown as string[]).map(Number)),
    }));
  }

  async listar(client: PoolClient): Promise<PersonaDto[]> {
    const { rows } = await client.query(
      `SELECT p.id, p.display_name, p.active, p.has_access, p.photo,
              p.consent_basis, p.consent_at, p.created_at,
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
      hasAccess: Boolean(r.has_access),
      photo: (r.photo as string) ?? null,
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

  /**
   * Registra un paso: quién estuvo frente a esta cámara y entre qué horas.
   *
   * Si el paso ya venía abierto —la persona sigue ahí— se extiende en vez de
   * crear otra fila. Un control de accesos con una fila por frame es ilegible,
   * y la diferencia entre "entró una vez y se quedó" y "entró cuarenta veces"
   * es justamente el dato que se consulta.
   */
  async registrarPaso(
    client: PoolClient,
    m: {
      organizationId: string;
      siteId: string;
      cameraId: string;
      personId: string;
      from: number;
      to: number;
      bestScore: number;
      seenByFace: boolean;
      hadAccess: boolean;
    },
  ): Promise<{ id: string; nuevo: boolean }> {
    const desde = new Date(m.from * 1000).toISOString();
    const hasta = new Date(m.to * 1000).toISOString();

    // Se extiende el paso anterior de esta persona en esta cámara si el que
    // llega arranca poco después de que aquél terminara: es la misma visita.
    //
    // La tolerancia importa y no es cosmética. Identificar a alguien falla por
    // ratos —se da vuelta, se tapa, sale del cuadro— y sin margen una sola
    // visita quedaba partida en cinco entradas de "menos de 1 min" separadas
    // por dos minutos cada una. Eso no son idas y vueltas: son huecos de
    // detección, y un registro que los muestra como entradas y salidas está
    // afirmando algo que no pasó.
    //
    // La decisión se toma acá y no en el módulo porque acá también cubre el
    // caso de que el worker se reinicie en medio de una visita.
    const { rows: abiertos } = await client.query<{ id: string; started_at: string }>(
      `SELECT id, started_at FROM person_sightings
        WHERE camera_id = $1 AND person_id = $2
          AND ended_at >= $3::timestamptz - ($4 || ' seconds')::interval
        ORDER BY started_at DESC LIMIT 1`,
      [m.cameraId, m.personId, desde, String(TOLERANCIA_UNIR_SEGUNDOS)],
    );

    if (abiertos.length) {
      await client.query(
        `UPDATE person_sightings
            SET ended_at = GREATEST(ended_at, $3::timestamptz),
                best_score = GREATEST(best_score, $4),
                seen_by_face = seen_by_face OR $5
          WHERE id = $1 AND started_at = $2`,
        [abiertos[0].id, abiertos[0].started_at, hasta, m.bestScore, m.seenByFace],
      );
      return { id: abiertos[0].id, nuevo: false };
    }

    const { rows } = await client.query<{ id: string }>(
      `INSERT INTO person_sightings
         (started_at, organization_id, site_id, camera_id, person_id,
          ended_at, best_score, seen_by_face, had_access)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
       RETURNING id`,
      [desde, m.organizationId, m.siteId, m.cameraId, m.personId,
       hasta, m.bestScore, m.seenByFace, m.hadAccess],
    );
    return { id: rows[0].id, nuevo: true };
  }

  /**
   * Registro de accesos: quién pasó, a qué hora, y si podía estar acá.
   *
   * Una fila por paso y no un resumen por persona: lo que se le pregunta a un
   * control de accesos es "¿quién entró a las tres de la tarde?", no "¿cuántas
   * horas estuvo Juan este mes?". Lo segundo sale de lo primero; al revés, no.
   */
  async registroDeAccesos(
    client: PoolClient,
    desde: string,
    hasta: string,
    cameraId?: string,
  ): Promise<
    {
      id: string;
      personId: string;
      displayName: string;
      desde: string;
      hasta: string;
      bestScore: number;
      seenByFace: boolean;
      hadAccess: boolean;
      cameraId: string;
    }[]
  > {
    const params: unknown[] = [desde, hasta];
    let filtro = '';
    if (cameraId) {
      params.push(cameraId);
      filtro = `AND s.camera_id = $${params.length}`;
    }
    const { rows } = await client.query(
      `SELECT s.id, s.person_id, p.display_name, s.started_at, s.ended_at,
              s.best_score, s.seen_by_face, s.had_access, s.camera_id
         FROM person_sightings s
         JOIN persons p ON p.id = s.person_id
        WHERE s.started_at >= $1 AND s.started_at < $2 ${filtro}
        ORDER BY s.started_at DESC
        LIMIT 500`,
      params,
    );
    return rows.map((f: Record<string, unknown>) => ({
      id: String(f.id),
      personId: String(f.person_id),
      displayName: String(f.display_name),
      desde: new Date(f.started_at as string).toISOString(),
      hasta: new Date(f.ended_at as string).toISOString(),
      bestScore: Number(f.best_score ?? 0),
      seenByFace: Boolean(f.seen_by_face),
      hadAccess: Boolean(f.had_access),
      cameraId: String(f.camera_id),
    }));
  }

  /**
   * Guarda la miniatura sólo si todavía no tiene una.
   *
   * No se pisa la existente: la primera foto es la que el administrador miró
   * cuando decidió que esta persona era quien decía ser, y reemplazarla con la
   * última que subió alguien haría que la ficha deje de mostrar lo que se
   * verificó.
   */
  async guardarFotoSiFalta(client: PoolClient, personId: string, photo: string): Promise<void> {
    await client.query(
      `UPDATE persons SET photo = $2 WHERE id = $1 AND photo IS NULL`,
      [personId, photo],
    );
  }

  /** Cambia si una persona tiene acceso, dejando registro de quién lo decidió. */
  async cambiarAcceso(
    client: PoolClient,
    personId: string,
    hasAccess: boolean,
    decidedBy: string,
    note?: string,
  ): Promise<boolean> {
    const r = await client.query(
      `UPDATE persons
          SET has_access = $2, access_decided_by = $3, access_decided_at = now(),
              access_note = COALESCE($4, access_note), updated_at = now()
        WHERE id = $1`,
      [personId, hasAccess, decidedBy, note ?? null],
    );
    return (r.rowCount ?? 0) > 0;
  }
}
