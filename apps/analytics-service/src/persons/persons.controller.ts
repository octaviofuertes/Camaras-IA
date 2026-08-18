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
import { PersonsService } from './persons.service';
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
    @Body() body: {
      displayName?: string;
      consentBasis?: string;
      embedding?: number[];
      notes?: string;
      forzarNueva?: boolean;
      hasAccess?: boolean;
      photo?: string;
    },
  ) {
    if (!body?.displayName || !body?.consentBasis) {
      throw new BadRequestException('Faltan campos: displayName, consentBasis');
    }
    if (typeof body.hasAccess !== 'boolean') {
      throw new BadRequestException(
        'Falta indicar si esta persona tiene acceso a este lugar: de eso depende que ' +
          'suene una alerta cuando aparezca',
      );
    }
    return this.persons.alta(req.auth as AuthContext, {
      displayName: body.displayName,
      hasAccess: body.hasAccess,
      photo: body.photo,
      consentBasis: body.consentBasis,
      embedding: body.embedding,
      notes: body.notes,
      forzarNueva: body.forzarNueva === true,
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

  /** Alta de un paso, desde el pipeline. */
  @Post('sightings')
  @RequirePermissions('events:ingest')
  async paso(
    @Req() req: Request,
    @Body() body: Record<string, unknown>,
  ): Promise<{ id: string; nuevo: boolean }> {
    for (const k of ['siteId', 'cameraId', 'personId', 'from', 'to']) {
      if (body?.[k] === undefined || body[k] === null) {
        throw new BadRequestException(`Falta el campo ${k}`);
      }
    }
    return this.persons.registrarPaso(req.auth as AuthContext, {
      siteId: String(body.siteId),
      cameraId: String(body.cameraId),
      personId: String(body.personId),
      from: Number(body.from),
      to: Number(body.to),
      bestScore: Number(body.bestScore ?? 0),
      seenByFace: body.seenByFace === true,
      hadAccess: body.hadAccess !== false,
    });
  }

  /**
   * Dar o quitar el acceso a una persona ya dada de alta.
   *
   * Detrás de `persons:write` y no de un permiso de lectura: de esto depende
   * que suene o no una alerta urgente cuando esa persona aparezca.
   */
  @Post(':id/access')
  @RequirePermissions('persons:write')
  async acceso(
    @Req() req: Request,
    @Param('id', new ParseUUIDPipe()) id: string,
    @Body() body: { hasAccess?: boolean; note?: string },
  ) {
    if (typeof body?.hasAccess !== 'boolean') {
      throw new BadRequestException('Falta el campo hasAccess (true o false)');
    }
    await this.persons.cambiarAcceso(req.auth as AuthContext, id, body.hasAccess, body.note);
    return { ok: true };
  }

  /**
   * Quién está siendo detectado ahora mismo.
   *
   * Mismo permiso que el registro: es el mismo dato, sólo que del presente.
   */
  @Get('live')
  @RequirePermissions('reports:identified')
  async live(@Req() req: Request) {
    return this.persons.presentes(req.auth as AuthContext);
  }

  /**
   * Suma una foto a una persona (alta manual).
   *
   * El cuerpo lleva la imagen en base64. Va por JSON y no multipart porque la
   * pantalla la obtiene de la cámara del navegador, donde ya es un data URL.
   */
  @Post(':id/photos')
  @RequirePermissions('persons:write')
  async foto(
    @Req() req: Request,
    @Param('id', new ParseUUIDPipe()) id: string,
    @Body() body: { image?: string; kind?: string },
  ) {
    const tipos = ['frontal', 'perfil', 'espalda'];
    if (!body?.image) throw new BadRequestException('Falta la imagen');
    if (!body?.kind || !tipos.includes(body.kind)) {
      throw new BadRequestException(`El tipo de foto debe ser uno de: ${tipos.join(', ')}`);
    }
    return this.persons.agregarFoto(
      req.auth as AuthContext,
      id,
      body.image,
      body.kind as 'frontal' | 'perfil' | 'espalda',
    );
  }

  /** Asigna la zona del plano donde trabaja una persona. */
  @Post(':id/zone')
  @RequirePermissions('persons:write')
  async zona(
    @Req() req: Request,
    @Param('id', new ParseUUIDPipe()) id: string,
    @Body() body: { workZone?: string | null },
  ) {
    await this.persons.cambiarZona(req.auth as AuthContext, id, body?.workZone ?? null);
    return { ok: true };
  }

  /**
   * Quién es la persona de esta foto. Lo consume la pantalla de bienvenida.
   *
   * Tiene su propio permiso, `kiosk:identify`, y no `persons:read`. La
   * diferencia importa: con `persons:read` el token de una pantalla colgada en
   * la entrada podría además LISTAR a todas las personas con sus fotos. Acá lo
   * único que habilita es preguntar por una cara que ya se tiene.
   */
  @Post('identify')
  @RequirePermissions('kiosk:identify')
  async identificar(@Req() req: Request, @Body() body: { image?: string }) {
    const r = await this.persons.identificar(req.auth as AuthContext, body?.image ?? '');
    return { reconocido: r };
  }

  /**
   * El plano del lugar.
   *
   * Lo lee también la pantalla de bienvenida, así que alcanza con el permiso
   * del kiosco: es el dibujo de la planta, no un dato de nadie.
   */
  @Get('floorplan')
  @RequirePermissions('kiosk:identify')
  async verPlano(@Req() req: Request) {
    return this.persons.plano(req.auth as AuthContext);
  }

  /** Sube o reemplaza el plano. Sólo quien administra. */
  @Post('floorplan')
  @RequirePermissions('persons:write')
  async subirPlano(@Req() req: Request, @Body() body: { image?: string }) {
    if (!body?.image) throw new BadRequestException('Falta la imagen del plano');
    await this.persons.guardarPlano(req.auth as AuthContext, body.image);
    return { ok: true };
  }

  /** Reporte de presencia del pipeline: quién está en el cuadro ahora. */
  @Post('presence')
  @RequirePermissions('events:ingest')
  async presencia(
    @Body() body: { cameraId?: string; presentes?: unknown[] },
  ): Promise<{ ok: true }> {
    if (!body?.cameraId) throw new BadRequestException('Falta el campo cameraId');
    this.persons.reportarPresencia(
      String(body.cameraId),
      (body.presentes ?? []) as never[],
    );
    return { ok: true };
  }

  /**
   * Registro de accesos: quién pasó y a qué hora.
   *
   * Detrás de su propio permiso. Saber a qué hora entró y salió cada persona
   * todos los días es un dato sobre su vida, no sobre la seguridad del lugar,
   * y cuanta menos gente lo alcance, mejor.
   */
  @Get('report/access')
  @RequirePermissions('reports:identified')
  async registro(
    @Req() req: Request,
    @Query('desde') desde?: string,
    @Query('hasta') hasta?: string,
    @Query('cameraId') cameraId?: string,
  ) {
    const ahora = new Date();
    const inicio = new Date(ahora);
    inicio.setHours(0, 0, 0, 0);
    return this.persons.registro(
      req.auth as AuthContext,
      desde || inicio.toISOString(),
      hasta || ahora.toISOString(),
      cameraId,
    );
  }
}
