import { BadRequestException, Body, Controller, Get, Post, Query, Req, UseGuards } from '@nestjs/common';
import type { Request } from 'express';
import { JwtGuard } from '../auth/jwt.guard';
import { PermissionsGuard, RequirePermissions } from '../auth/permissions.guard';
import { ActivityService, type Informe } from './activity.service';
import type { AuthContext } from '../auth/auth.types';

interface CuerpoMuestra {
  siteId?: string;
  cameraId?: string;
  zoneId?: string | null;
  zoneName?: string;
  moduleKey?: string;
  moduleVersion?: string;
  from?: number;
  to?: number;
  occupiedSeconds?: number;
  phoneSeconds?: number;
  emptySeconds?: number;
  uncoveredSeconds?: number;
  maxPeople?: number;
  meanOccupancy?: number;
}

const OBLIGATORIOS: (keyof CuerpoMuestra)[] = ['siteId', 'cameraId', 'moduleKey', 'from', 'to'];

@Controller('analytics')
@UseGuards(JwtGuard, PermissionsGuard)
export class ActivityController {
  constructor(private readonly activity: ActivityService) {}

  /**
   * Alta de una ventana medida, desde el pipeline.
   *
   * Requiere `events:ingest`, el mismo permiso que dar de alta un evento: es
   * una identidad de servicio, no de una persona. Ningún rol humano lo tiene.
   */
  @Post('activity')
  @RequirePermissions('events:ingest')
  async ingest(@Req() req: Request, @Body() body: CuerpoMuestra): Promise<{ id: string | null }> {
    const faltan = OBLIGATORIOS.filter((k) => body?.[k] === undefined || body[k] === null);
    if (faltan.length) {
      throw new BadRequestException(`Faltan campos: ${faltan.join(', ')}`);
    }
    const id = await this.activity.ingest(req.auth as AuthContext, {
      siteId: body.siteId!,
      cameraId: body.cameraId!,
      zoneId: body.zoneId ?? null,
      zoneName: body.zoneName || 'Toda la cámara',
      moduleKey: body.moduleKey!,
      moduleVersion: body.moduleVersion ?? '1.0.0',
      from: Number(body.from),
      to: Number(body.to),
      occupiedSeconds: Number(body.occupiedSeconds ?? 0),
      phoneSeconds: Number(body.phoneSeconds ?? 0),
      emptySeconds: Number(body.emptySeconds ?? 0),
      uncoveredSeconds: Number(body.uncoveredSeconds ?? 0),
      maxPeople: Number(body.maxPeople ?? 0),
      meanOccupancy: Number(body.meanOccupancy ?? 0),
    });
    return { id };
  }

  /**
   * Informe de actividad por puesto.
   *
   * Sin rango explícito devuelve el día en curso, que es lo que alguien quiere
   * ver al entrar.
   */
  @Get('activity')
  @RequirePermissions('events:read')
  async informe(
    @Req() req: Request,
    @Query('desde') desde?: string,
    @Query('hasta') hasta?: string,
    @Query('cameraId') cameraId?: string,
    @Query('zoneId') zoneId?: string,
    @Query('bucket') bucket?: string,
  ): Promise<Informe> {
    const ahora = new Date();
    const inicioDelDia = new Date(ahora);
    inicioDelDia.setHours(0, 0, 0, 0);

    return this.activity.informe(req.auth as AuthContext, {
      desde: desde || inicioDelDia.toISOString(),
      hasta: hasta || ahora.toISOString(),
      cameraId: cameraId || undefined,
      zoneId: zoneId || undefined,
      bucket: bucket === 'day' ? 'day' : 'hour',
    });
  }
}
