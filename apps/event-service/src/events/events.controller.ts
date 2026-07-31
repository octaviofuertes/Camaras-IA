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
    @Body() body: { resolution?: string; note?: string },
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
    );
  }
}
