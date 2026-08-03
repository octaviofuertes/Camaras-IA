import { Injectable } from '@nestjs/common';
import type { PoolClient } from 'pg';

export interface CameraDto {
  id: string;
  organizationId: string;
  siteId: string;
  name: string;
  location: string | null;
  status: string;
  /** Índice USB (`0`) o URL RTSP. Vive en `streams.rtsp_url`. */
  source: string | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  enabled: boolean;
  moduleCount: number;
  createdAt: string;
}

export interface ModuleDto {
  id: string;
  moduleKey: string;
  name: string;
  description: string | null;
  category: string;
  version: string;
  configSchema: Record<string, unknown>;
  status: string;
}

export interface AssignmentDto {
  id: string;
  cameraId: string;
  aiModuleId: string;
  moduleKey: string;
  moduleName: string;
  category: string;
  moduleVersion: string;
  config: Record<string, unknown>;
  enabled: boolean;
  priority: number;
}

const CAM_COLS = `c.id, c.organization_id, c.site_id, c.name, c.location, c.status, c.created_at,
  s.rtsp_url, s.width, s.height, s.fps,
  (SELECT count(*) FROM camera_module_configs m WHERE m.camera_id = c.id AND m.enabled) AS module_count`;

interface CamRow {
  id: string;
  organization_id: string;
  site_id: string;
  name: string;
  location: string | null;
  status: string;
  created_at: Date;
  rtsp_url: string | null;
  width: number | null;
  height: number | null;
  fps: string | null;
  module_count: string;
}

function toCamera(r: CamRow): CameraDto {
  return {
    id: r.id,
    organizationId: r.organization_id,
    siteId: r.site_id,
    name: r.name,
    location: r.location,
    status: r.status,
    // `usb://0` se expone como `0`: la UI habla de índice o de URL, no del esquema interno.
    source: r.rtsp_url?.startsWith('usb://') ? r.rtsp_url.slice(6) : r.rtsp_url,
    width: r.width,
    height: r.height,
    fps: r.fps === null ? null : Number(r.fps),
    enabled: r.status !== 'disabled',
    moduleCount: Number(r.module_count ?? 0),
    createdAt: r.created_at.toISOString(),
  };
}

@Injectable()
export class CamerasRepository {
  async list(client: PoolClient): Promise<CameraDto[]> {
    const { rows } = await client.query<CamRow>(
      `SELECT ${CAM_COLS} FROM cameras c
       LEFT JOIN LATERAL (
         SELECT rtsp_url, width, height, fps FROM streams
         WHERE camera_id = c.id AND kind = 'main' LIMIT 1
       ) s ON true
       ORDER BY c.created_at`,
    );
    return rows.map(toCamera);
  }

  async findById(client: PoolClient, id: string): Promise<CameraDto | null> {
    const { rows } = await client.query<CamRow>(
      `SELECT ${CAM_COLS} FROM cameras c
       LEFT JOIN LATERAL (
         SELECT rtsp_url, width, height, fps FROM streams
         WHERE camera_id = c.id AND kind = 'main' LIMIT 1
       ) s ON true
       WHERE c.id = $1`,
      [id],
    );
    return rows[0] ? toCamera(rows[0]) : null;
  }

  /** Alta de cámara + su stream principal, en la misma transacción. */
  async create(
    client: PoolClient,
    organizationId: string,
    input: {
      siteId: string;
      name: string;
      location?: string;
      source: string;
      width?: number;
      height?: number;
      fps?: number;
    },
  ): Promise<CameraDto> {
    const { rows } = await client.query<{ id: string }>(
      `INSERT INTO cameras (organization_id, site_id, name, location, status)
       VALUES ($1, $2, $3, $4, 'offline') RETURNING id`,
      [organizationId, input.siteId, input.name, input.location ?? null],
    );
    const id = rows[0].id;

    // Un índice numérico se guarda como usb://N para distinguirlo de una URL.
    const stored = /^\d+$/.test(input.source.trim()) ? `usb://${input.source.trim()}` : input.source.trim();

    await client.query(
      `INSERT INTO streams (organization_id, camera_id, kind, rtsp_url, width, height, fps)
       VALUES ($1, $2, 'main', $3, $4, $5, $6)`,
      [organizationId, id, stored, input.width ?? 1280, input.height ?? 720, input.fps ?? 10],
    );

    const created = await this.findById(client, id);
    if (!created) throw new Error('la cámara no se pudo leer tras crearla');
    return created;
  }

  async update(
    client: PoolClient,
    id: string,
    patch: {
      name?: string;
      location?: string;
      status?: string;
      source?: string;
      fps?: number;
      width?: number;
      height?: number;
    },
  ): Promise<CameraDto | null> {
    const sets: string[] = [];
    const params: unknown[] = [];
    const add = (col: string, v: unknown): void => {
      params.push(v);
      sets.push(`${col} = $${params.length}`);
    };
    if (patch.name !== undefined) add('name', patch.name);
    if (patch.location !== undefined) add('location', patch.location);
    if (patch.status !== undefined) add('status', patch.status);

    if (sets.length) {
      params.push(id);
      await client.query(
        `UPDATE cameras SET ${sets.join(', ')}, updated_at = now() WHERE id = $${params.length}`,
        params,
      );
    }

    if (patch.source !== undefined || patch.fps !== undefined || patch.width !== undefined) {
      const stored =
        patch.source === undefined
          ? undefined
          : /^\d+$/.test(patch.source.trim())
            ? `usb://${patch.source.trim()}`
            : patch.source.trim();
      await client.query(
        `UPDATE streams SET
           rtsp_url = COALESCE($1, rtsp_url),
           fps      = COALESCE($2, fps),
           width    = COALESCE($3, width),
           height   = COALESCE($4, height)
         WHERE camera_id = $5 AND kind = 'main'`,
        [stored ?? null, patch.fps ?? null, patch.width ?? null, patch.height ?? null, id],
      );
    }

    return this.findById(client, id);
  }

  async remove(client: PoolClient, id: string): Promise<boolean> {
    const r = await client.query('DELETE FROM cameras WHERE id = $1', [id]);
    return (r.rowCount ?? 0) > 0;
  }

  // ── catálogo de módulos ────────────────────────────────────────────
  async listModules(client: PoolClient): Promise<ModuleDto[]> {
    const { rows } = await client.query(
      `SELECT id, module_key, name, description, category, version, config_schema, status
       FROM ai_modules WHERE status = 'available' ORDER BY category, name`,
    );
    return rows.map((r) => ({
      id: r.id,
      moduleKey: r.module_key,
      name: r.name,
      description: r.description,
      category: r.category,
      version: r.version,
      configSchema: r.config_schema ?? {},
      status: r.status,
    }));
  }

  // ── asignaciones cámara ↔ módulo ───────────────────────────────────
  async listAssignments(client: PoolClient, cameraId?: string): Promise<AssignmentDto[]> {
    const { rows } = await client.query(
      `SELECT cmc.id, cmc.camera_id, cmc.ai_module_id, cmc.module_version, cmc.config,
              cmc.enabled, cmc.priority, m.module_key, m.name AS module_name, m.category
       FROM camera_module_configs cmc
       JOIN ai_modules m ON m.id = cmc.ai_module_id
       ${cameraId ? 'WHERE cmc.camera_id = $1' : ''}
       ORDER BY cmc.priority, m.name`,
      cameraId ? [cameraId] : [],
    );
    return rows.map((r) => ({
      id: r.id,
      cameraId: r.camera_id,
      aiModuleId: r.ai_module_id,
      moduleKey: r.module_key,
      moduleName: r.module_name,
      category: r.category,
      moduleVersion: r.module_version,
      config: r.config ?? {},
      enabled: r.enabled,
      priority: r.priority,
    }));
  }

  /**
   * Asigna un módulo a una cámara. Si ya estaba, actualiza su configuración:
   * la restricción UNIQUE (camera_id, ai_module_id) impide duplicarlo.
   */
  async assign(
    client: PoolClient,
    organizationId: string,
    cameraId: string,
    aiModuleId: string,
    config: Record<string, unknown>,
  ): Promise<AssignmentDto | null> {
    const mod = await client.query<{ version: string; config_schema_version: string }>(
      'SELECT version, config_schema_version FROM ai_modules WHERE id = $1',
      [aiModuleId],
    );
    if (!mod.rows[0]) return null;

    await client.query(
      `INSERT INTO camera_module_configs
         (organization_id, camera_id, ai_module_id, module_version, config_schema_version, config, enabled)
       VALUES ($1, $2, $3, $4, $5, $6::jsonb, true)
       ON CONFLICT (camera_id, ai_module_id)
       DO UPDATE SET config = EXCLUDED.config, enabled = true, updated_at = now()`,
      [
        organizationId, cameraId, aiModuleId,
        mod.rows[0].version, mod.rows[0].config_schema_version,
        JSON.stringify(config ?? {}),
      ],
    );

    const list = await this.listAssignments(client, cameraId);
    return list.find((a) => a.aiModuleId === aiModuleId) ?? null;
  }

  async updateConfig(
    client: PoolClient,
    cameraId: string,
    aiModuleId: string,
    config: Record<string, unknown>,
  ): Promise<boolean> {
    const r = await client.query(
      `UPDATE camera_module_configs SET config = $1::jsonb, updated_at = now()
       WHERE camera_id = $2 AND ai_module_id = $3`,
      [JSON.stringify(config ?? {}), cameraId, aiModuleId],
    );
    return (r.rowCount ?? 0) > 0;
  }

  async unassign(client: PoolClient, cameraId: string, aiModuleId: string): Promise<boolean> {
    const r = await client.query(
      'DELETE FROM camera_module_configs WHERE camera_id = $1 AND ai_module_id = $2',
      [cameraId, aiModuleId],
    );
    return (r.rowCount ?? 0) > 0;
  }

  /** Sucursal por defecto de la organización (para el alta rápida desde la UI). */
  async defaultSiteId(client: PoolClient): Promise<string | null> {
    const { rows } = await client.query<{ id: string }>('SELECT id FROM sites ORDER BY created_at LIMIT 1');
    return rows[0]?.id ?? null;
  }
}
