import {
  BadRequestException,
  Body,
  Controller,
  Get,
  Param,
  ParseUUIDPipe,
  Post,
  Query,
  Req,
  UseGuards,
} from '@nestjs/common';
import type { Request } from 'express';
import { EVENT_STATUS, SEVERITY, type EventDto, type EventStatus } from '@percepta/contracts';
import { JwtGuard } from '../auth/jwt.guard';
import { PermissionsGuard, RequirePermissions } from '../auth/permissions.guard';
import { EventsService } from './events.service';
import type { AuthContext } from '../auth/auth.types';

const MAX_LIMIT = 200;
const RESOLUTIONS = ['confirmed', 'dismissed', 'false_positive'] as const;
type Resolution = (typeof RESOLUTIONS)[number];

interface Paged<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

@Controller('events')
@UseGuards(JwtGuard, PermissionsGuard)
export class EventsController {
  constructor(private readonly events: EventsService) {}

  /** Cuántas muestras de entrenamiento se acumularon con el feedback humano. */
  @Get('training/stats')
  @RequirePermissions('events:read')
  async trainingStats(@Req() req: Request): Promise<Record<string, number>> {
    return this.events.trainingStats(req.auth as AuthContext);
  }

  @Get()
  @RequirePermissions('events:read')
  async list(@Req() req: Request, @Query() q: Record<string, string>): Promise<Paged<EventDto>> {
    const auth = req.auth as AuthContext;

    if (q.status && !EVENT_STATUS.includes(q.status as EventStatus)) {
      throw new BadRequestException(`status inválido. Válidos: ${EVENT_STATUS.join(', ')}`);
    }
    if (q.severity && !SEVERITY.includes(q.severity as (typeof SEVERITY)[number])) {
      throw new BadRequestException(`severity inválida. Válidas: ${SEVERITY.join(', ')}`);
    }

    const limit = Math.min(Math.max(Number(q.limit ?? 50) || 50, 1), MAX_LIMIT);
    const offset = Math.max(Number(q.offset ?? 0) || 0, 0);

    const { items, total } = await this.events.list(auth, {
      status: q.status as EventStatus | undefined,
      cameraId: q.cameraId,
      siteId: q.siteId,
      eventType: q.eventType,
      severity: q.severity,
      from: q.from,
      to: q.to,
      limit,
      offset,
    });
    return { items, total, limit, offset };
  }

  /**
   * Alta desde el pipeline (rules-engine / ai-worker). Requiere `events:ingest`,
   * que ningún rol de usuario tiene: es una llamada entre servicios.
   */
  @Post()
  @RequirePermissions('events:ingest')
  async ingest(@Req() req: Request, @Body() body: Record<string, unknown>): Promise<{ created: boolean; event: EventDto | null }> {
    const required = ['siteId', 'cameraId', 'aiModuleId', 'moduleKey', 'moduleVersion', 'eventType', 'severity', 'confidence', 'dedupKey'];
    const missing = required.filter((k) => body?.[k] === undefined || body?.[k] === null);
    if (missing.length) {
      throw new BadRequestException(`Faltan campos: ${missing.join(', ')}`);
    }
    const confidence = Number(body['confidence']);
    if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
      throw new BadRequestException('confidence debe estar entre 0 y 1');
    }
    const event = await this.events.ingest(req.auth as AuthContext, {
      siteId: String(body['siteId']),
      cameraId: String(body['cameraId']),
      aiModuleId: String(body['aiModuleId']),
      moduleKey: String(body['moduleKey']),
      moduleVersion: String(body['moduleVersion']),
      eventType: String(body['eventType']),
      eventClass: body['eventClass'] ? String(body['eventClass']) : undefined,
      severity: String(body['severity']),
      confidence,
      dedupKey: String(body['dedupKey']),
      zoneIds: Array.isArray(body['zoneIds']) ? (body['zoneIds'] as string[]) : undefined,
      trackId: body['trackId'] !== undefined ? Number(body['trackId']) : undefined,
      detection: (body['detection'] as Record<string, unknown>) ?? undefined,
      metadata: (body['metadata'] as Record<string, unknown>) ?? undefined,
      trainingSequence: Array.isArray(body['trainingSequence'])
        ? (body['trainingSequence'] as number[][])
        : undefined,
    });
    // event === null => la deduplicación lo descartó. No es error.
    return { created: !!event, event };
  }

  /** Evidencias (clips) de un evento confirmado. */
  @Get(':id/evidences')
  @RequirePermissions('evidences:read')
  async evidences(
    @Req() req: Request,
    @Param('id', new ParseUUIDPipe()) id: string,
  ): Promise<{ items: Record<string, unknown>[] }> {
    return { items: await this.events.listEvidences(req.auth as AuthContext, id) };
  }

  @Get(':id')
  @RequirePermissions('events:read')
  async findOne(
    @Req() req: Request,
    @Param('id', new ParseUUIDPipe()) id: string,
    @Query('occurredAt') occurredAt?: string,
  ): Promise<EventDto> {
    return this.events.findOne(req.auth as AuthContext, id, occurredAt);
  }

  /** Human-in-the-loop: un operador toma la alerta. */
  @Post(':id/acknowledge')
  @RequirePermissions('events:acknowledge')
  async acknowledge(
    @Req() req: Request,
    @Param('id', new ParseUUIDPipe()) id: string,
    @Body() body: { note?: string } = {},
  ): Promise<EventDto> {
    return this.events.acknowledge(
      req.auth as AuthContext,
      id,
      body?.note,
      req.headers['x-request-id'] as string | undefined,
    );
  }

  /** Human-in-the-loop: el operador resuelve la alerta revisada. */
  @Post(':id/resolve')
  @RequirePermissions('events:resolve')
  async resolve(
    @Req() req: Request,
    @Param('id', new ParseUUIDPipe()) id: string,
    @Body() body: { resolution?: string; note?: string; title?: string },
  ): Promise<EventDto> {
    const resolution = body?.resolution;
    if (!resolution || !RESOLUTIONS.includes(resolution as Resolution)) {
      throw new BadRequestException(`resolution requerida. Válidas: ${RESOLUTIONS.join(', ')}`);
    }
    return this.events.resolve(
      req.auth as AuthContext,
      id,
      resolution as Resolution,
      body?.note,
      req.headers['x-request-id'] as string | undefined,
      typeof body?.title === 'string' ? body.title.trim() || undefined : undefined,
    );
  }
}
