import {
  BadRequestException,
  Body,
  Controller,
  Delete,
  Get,
  NotFoundException,
  Param,
  ParseUUIDPipe,
  Patch,
  Post,
  Req,
  UseGuards,
} from '@nestjs/common';
import type { Request } from 'express';
import { JwtGuard } from '../auth/jwt.guard';
import { PermissionsGuard, RequirePermissions } from '../auth/permissions.guard';
import { DatabaseService } from '../db/database.service';
import { CamerasRepository, type AssignmentDto, type CameraDto, type ModuleDto } from './cameras.repository';
import type { AuthContext } from '../auth/auth.types';

/** Índice USB (`0`, `1`…) o URL de cámara IP (`rtsp://…`, `http://…`). */
const SOURCE_RE = /^(\d+|(rtsp|rtsps|http|https):\/\/.+)$/i;

@Controller()
@UseGuards(JwtGuard, PermissionsGuard)
export class CamerasController {
  constructor(
    private readonly db: DatabaseService,
    private readonly repo: CamerasRepository,
  ) {}

  private auth(req: Request): AuthContext {
    return req.auth as AuthContext;
  }

  // ── cámaras ────────────────────────────────────────────────────────
  @Get('cameras')
  @RequirePermissions('cameras:read')
  async list(@Req() req: Request): Promise<{ items: CameraDto[] }> {
    const items = await this.db.withTenant(this.auth(req).organizationId, (c) => this.repo.list(c));
    return { items };
  }

  @Get('cameras/:id')
  @RequirePermissions('cameras:read')
  async findOne(@Req() req: Request, @Param('id', new ParseUUIDPipe()) id: string): Promise<CameraDto> {
    const cam = await this.db.withTenant(this.auth(req).organizationId, (c) => this.repo.findById(c, id));
    if (!cam) throw new NotFoundException('Cámara no encontrada');
    return cam;
  }

  @Post('cameras')
  @RequirePermissions('cameras:write')
  async create(@Req() req: Request, @Body() body: Record<string, unknown>): Promise<CameraDto> {
    const name = String(body?.['name'] ?? '').trim();
    const source = String(body?.['source'] ?? '').trim();
    if (!name) throw new BadRequestException('El nombre es obligatorio');
    if (!SOURCE_RE.test(source)) {
      throw new BadRequestException(
        'Origen inválido. Usá un índice USB (0, 1…) o una URL rtsp://usuario:clave@ip:554/stream',
      );
    }

    const auth = this.auth(req);
    return this.db.withTenant(auth.organizationId, async (client) => {
      const enUso = await this.repo.cameraUsingSource(client, source);
      if (enUso) {
        throw new BadRequestException(
          `Ese origen ya lo usa la cámara "${enUso}". Dos cámaras no pueden capturar el mismo dispositivo.`,
        );
      }

      const siteId = body['siteId'] ? String(body['siteId']) : await this.repo.defaultSiteId(client);
      if (!siteId) throw new BadRequestException('No hay ninguna sucursal donde crear la cámara');
      return this.repo.create(client, auth.organizationId, {
        siteId,
        name,
        location: body['location'] ? String(body['location']) : undefined,
        source,
        width: body['width'] ? Number(body['width']) : undefined,
        height: body['height'] ? Number(body['height']) : undefined,
        fps: body['fps'] ? Number(body['fps']) : undefined,
      });
    });
  }

  @Patch('cameras/:id')
  @RequirePermissions('cameras:write')
  async update(
    @Req() req: Request,
    @Param('id', new ParseUUIDPipe()) id: string,
    @Body() body: Record<string, unknown>,
  ): Promise<CameraDto> {
    if (body['source'] !== undefined && !SOURCE_RE.test(String(body['source']).trim())) {
      throw new BadRequestException('Origen inválido');
    }
    const cam = await this.db.withTenant(this.auth(req).organizationId, (c) =>
      this.repo.update(c, id, {
        name: body['name'] !== undefined ? String(body['name']) : undefined,
        location: body['location'] !== undefined ? String(body['location']) : undefined,
        status: body['status'] !== undefined ? String(body['status']) : undefined,
        source: body['source'] !== undefined ? String(body['source']) : undefined,
        fps: body['fps'] !== undefined ? Number(body['fps']) : undefined,
        width: body['width'] !== undefined ? Number(body['width']) : undefined,
        height: body['height'] !== undefined ? Number(body['height']) : undefined,
      }),
    );
    if (!cam) throw new NotFoundException('Cámara no encontrada');
    return cam;
  }

  @Delete('cameras/:id')
  @RequirePermissions('cameras:write')
  async remove(@Req() req: Request, @Param('id', new ParseUUIDPipe()) id: string): Promise<{ deleted: boolean }> {
    const ok = await this.db.withTenant(this.auth(req).organizationId, (c) => this.repo.remove(c, id));
    if (!ok) throw new NotFoundException('Cámara no encontrada');
    return { deleted: true };
  }

  // ── catálogo de módulos ────────────────────────────────────────────
  @Get('modules')
  @RequirePermissions('modules:read')
  async modules(@Req() req: Request): Promise<{ items: ModuleDto[] }> {
    const items = await this.db.withTenant(this.auth(req).organizationId, (c) => this.repo.listModules(c));
    return { items };
  }

  // ── asignaciones ───────────────────────────────────────────────────
  @Get('camera-module-configs')
  @RequirePermissions('camera-module-configs:read')
  async assignments(@Req() req: Request): Promise<{ items: AssignmentDto[] }> {
    const items = await this.db.withTenant(this.auth(req).organizationId, (c) => this.repo.listAssignments(c));
    return { items };
  }

  /** Asignar un módulo a una cámara (es lo que hace el drag & drop). */
  @Post('cameras/:id/modules')
  @RequirePermissions('camera-module-configs:write')
  async assign(
    @Req() req: Request,
    @Param('id', new ParseUUIDPipe()) cameraId: string,
    @Body() body: Record<string, unknown>,
  ): Promise<AssignmentDto> {
    const aiModuleId = String(body?.['aiModuleId'] ?? '');
    if (!aiModuleId) throw new BadRequestException('aiModuleId es obligatorio');

    const auth = this.auth(req);
    const created = await this.db.withTenant(auth.organizationId, async (client) => {
      const cam = await this.repo.findById(client, cameraId);
      if (!cam) throw new NotFoundException('Cámara no encontrada');
      return this.repo.assign(
        client,
        auth.organizationId,
        cameraId,
        aiModuleId,
        (body['config'] as Record<string, unknown>) ?? {},
      );
    });
    if (!created) throw new NotFoundException('Módulo no encontrado en el catálogo');
    return created;
  }

  @Patch('cameras/:id/modules/:moduleId')
  @RequirePermissions('camera-module-configs:write')
  async updateConfig(
    @Req() req: Request,
    @Param('id', new ParseUUIDPipe()) cameraId: string,
    @Param('moduleId', new ParseUUIDPipe()) moduleId: string,
    @Body() body: Record<string, unknown>,
  ): Promise<{ updated: boolean }> {
    const ok = await this.db.withTenant(this.auth(req).organizationId, (c) =>
      this.repo.updateConfig(c, cameraId, moduleId, (body['config'] as Record<string, unknown>) ?? {}),
    );
    if (!ok) throw new NotFoundException('Asignación no encontrada');
    return { updated: true };
  }

  @Delete('cameras/:id/modules/:moduleId')
  @RequirePermissions('camera-module-configs:write')
  async unassign(
    @Req() req: Request,
    @Param('id', new ParseUUIDPipe()) cameraId: string,
    @Param('moduleId', new ParseUUIDPipe()) moduleId: string,
  ): Promise<{ deleted: boolean }> {
    const ok = await this.db.withTenant(this.auth(req).organizationId, (c) =>
      this.repo.unassign(c, cameraId, moduleId),
    );
    if (!ok) throw new NotFoundException('Asignación no encontrada');
    return { deleted: true };
  }
}
