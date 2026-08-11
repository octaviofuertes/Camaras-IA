import {
  BadRequestException,
  Body,
  Controller,
  Delete,
  Get,
  Param,
  ParseUUIDPipe,
  Post,
  Query,
  Req,
  UseGuards,
} from '@nestjs/common';
import type { Request } from 'express';
import { JwtGuard } from '../auth/jwt.guard';
import { PermissionsGuard, RequirePermissions } from '../auth/permissions.guard';
import { PersonsService, type InformeNominal } from './persons.service';
import type { AuthContext } from '../auth/auth.types';

@Controller('persons')
@UseGuards(JwtGuard, PermissionsGuard)
export class PersonsController {
  constructor(private readonly persons: PersonsService) {}

  /**
   * Galería de plantillas para el módulo de identificación.
   *
   * Requiere `persons:read`, que tiene el token de servicio. NO devuelve fotos:
   * sólo los vectores, de los que no se puede reconstruir una cara.
   */
  @Get('faces')
  @RequirePermissions('persons:read')
  async galeria(@Req() req: Request) {
    const items = await this.persons.galeria(req.auth as AuthContext);
    return { items };
  }

  @Get()
  @RequirePermissions('persons:read')
  async listar(@Req() req: Request) {
    return { items: await this.persons.listar(req.auth as AuthContext) };
  }

  /**
   * Alta de una persona. Es el "sí, trabaja acá" del flujo de reconocimiento.
   *
   * `persons:write` sólo lo tienen los administradores: dar de alta implica
   * afirmar que existe el consentimiento de esa persona, y eso no es una
   * decisión operativa.
   */
  @Post()
  @RequirePermissions('persons:write')
  async alta(
    @Req() req: Request,
    @Body() body: { displayName?: string; consentBasis?: string; embedding?: number[]; notes?: string },
  ) {
    if (!body?.displayName || !body?.consentBasis) {
      throw new BadRequestException('Faltan campos: displayName, consentBasis');
    }
    return this.persons.alta(req.auth as AuthContext, {
      displayName: body.displayName,
      consentBasis: body.consentBasis,
      embedding: body.embedding,
      notes: body.notes,
    });
  }

  /** Suma otra foto a alguien ya dado de alta: mejora el reconocimiento. */
  @Post(':id/faces')
  @RequirePermissions('persons:write')
  async agregarRostro(
    @Req() req: Request,
    @Param('id', new ParseUUIDPipe()) id: string,
    @Body() body: { embedding?: number[] },
  ) {
    if (!body?.embedding?.length) throw new BadRequestException('Falta el vector facial');
    await this.persons.agregarRostro(req.auth as AuthContext, id, body.embedding);
    return { ok: true };
  }

  /**
   * Baja definitiva: derecho de supresión.
   *
   * Se lleva las plantillas faciales y los tiempos medidos por cascada. No hay
   * baja lógica que deje la biometría dando vueltas.
   */
  @Delete(':id')
  @RequirePermissions('persons:write')
  async baja(@Req() req: Request, @Param('id', new ParseUUIDPipe()) id: string) {
    await this.persons.baja(req.auth as AuthContext, id);
    return { ok: true };
  }

  /** Alta de una ventana de actividad atribuida (desde el pipeline). */
  @Post('activity')
  @RequirePermissions('events:ingest')
  async ingestar(
    @Req() req: Request,
    @Body() body: Record<string, unknown>,
  ): Promise<{ id: string | null }> {
    for (const k of ['siteId', 'cameraId', 'from', 'to']) {
      if (body?.[k] === undefined || body[k] === null) {
        throw new BadRequestException(`Falta el campo ${k}`);
      }
    }
    const id = await this.persons.ingestarMuestra(req.auth as AuthContext, {
      siteId: String(body.siteId),
      cameraId: String(body.cameraId),
      zoneId: (body.zoneId as string) || null,
      zoneName: String(body.zoneName || 'Toda la cámara'),
      personId: (body.personId as string) || null,
      from: Number(body.from),
      to: Number(body.to),
      presentSeconds: Number(body.presentSeconds ?? 0),
      phoneSeconds: Number(body.phoneSeconds ?? 0),
    });
    return { id };
  }

  /**
   * Informe con nombre y apellido.
   *
   * Detrás de su propio permiso: un operador que revisa alertas de seguridad no
   * necesita saber cuánto tiempo pasó cada empleado con el teléfono, y cuanta
   * menos gente alcance ese dato, mejor.
   */
  @Get('report/activity')
  @RequirePermissions('reports:identified')
  async informe(
    @Req() req: Request,
    @Query('desde') desde?: string,
    @Query('hasta') hasta?: string,
    @Query('cameraId') cameraId?: string,
  ): Promise<InformeNominal> {
    const ahora = new Date();
    const inicio = new Date(ahora);
    inicio.setHours(0, 0, 0, 0);
    return this.persons.informe(
      req.auth as AuthContext,
      desde || inicio.toISOString(),
      hasta || ahora.toISOString(),
      cameraId || undefined,
    );
  }
}
