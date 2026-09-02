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
import { ModuloAsignadoGuard, RequiereModulo, SinModulo } from '../auth/modulo.guard';
import { MODULO_INGRESO_DE_PERSONAS } from '@percepta/contracts';
import { PersonsService } from './persons.service';
import type { AuthContext } from '../auth/auth.types';

/**
 * Todo lo que sabe el sistema sobre quién entra y a qué hora.
 *
 * El controlador entero pertenece al módulo "Ingreso de personas": sin una
 * cámara que lo tenga asignado, esta función no existe para la organización y
 * los endpoints contestan 409. No es una restricción de permisos —un
 * administrador los tiene todos igual— sino de finalidad: no corresponde
 * guardar la cara de un empleado si no hay ninguna cámara que vaya a
 * reconocerla.
 *
 * Las excepciones están marcadas una por una con @SinModulo() y su motivo.
 */
@Controller('persons')
@UseGuards(JwtGuard, PermissionsGuard, ModuloAsignadoGuard)
@RequiereModulo(MODULO_INGRESO_DE_PERSONAS)
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
  // Queda FUERA del módulo a propósito. Si alguien saca el módulo de todas las
  // cámaras, la gente que ya se dio de alta tiene que poder borrarse igual: si
  // no, desasignar el módulo dejaría la biometría encerrada, sin pantalla ni
  // endpoint que la elimine. Borrar nunca puede depender de tener contratada
  // la función que creó el dato.
  @SinModulo()
  async baja(@Req() req: Request, @Param('id', new ParseUUIDPipe()) id: string) {
    await this.persons.baja(req.auth as AuthContext, id);
    return { ok: true };
  }

  /** Alta de un paso, desde el pipeline. */
  @Post('sightings')
  @RequirePermissions('events:ingest')
  // Fuera del módulo: lo postea el pipeline, que ya sólo corre donde el módulo
  // está asignado. Ponerle la puerta acá agregaría una carrera —el worker se
  // entera de la asignación cada 15 s y el guard recuerda 10 s— que perdería
  // pasos reales justo después de asignar el módulo. La puerta de este
  // endpoint es `events:ingest`, que ningún rol humano tiene.
  @SinModulo()
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
   * ¿Quién es la persona de la foto de esta alerta? A pedido, desde Eventos.
   *
   * Detrás de `reports:identified` y NO de `events:read`, que es lo que tiene
   * un operador. La diferencia es deliberada: ver que alguien anda sin casco es
   * el trabajo del operador; ponerle nombre y apellido a esa persona es un dato
   * sobre ella, y es la misma puerta que la de los informes con nombre.
   *
   * Tampoco usa `kiosk:identify`: ese permiso vive en una pantalla colgada en
   * la entrada, y lo que habilita es saludar a quien está parado enfrente, no
   * identificar a alguien en una foto guardada.
   */
  @Post('recognize')
  @RequirePermissions('reports:identified')
  async reconocer(@Req() req: Request, @Body() body: { image?: string }) {
    if (!body?.image) throw new BadRequestException('Falta la imagen');
    return this.persons.reconocerEnFoto(req.auth as AuthContext, body.image);
  }

  /**
   * El plano del lugar.
   *
   * Lo lee también la pantalla de bienvenida, así que alcanza con el permiso
   * del kiosco: es el dibujo de la planta, no un dato de nadie.
   */

  /** Reporte de presencia del pipeline: quién está en el cuadro ahora. */
  @Post('presence')
  @RequirePermissions('events:ingest')
  // Fuera del módulo, por el mismo motivo que `sightings`: viene del pipeline.
  @SinModulo()
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
  /**
   * El lugar entero: sus pisos, sus planos y los bloques de cada uno.
   *
   * Lo pide tanto el editor como la pantalla de bienvenida, y ésta corre con
   * el token del kiosco: por eso `kiosk:identify` y no `persons:read`. Lo que
   * devuelve —nombres de pisos y de áreas— es el plano del lugar, no datos de
   * nadie en particular.
   */
  @Get('zones')
  @RequirePermissions('kiosk:identify')
  async verZonas(@Req() req: Request) {
    return this.persons.zonas(req.auth as AuthContext);
  }

  /** Guarda los bloques marcados sobre los planos. */
  @Post('zones')
  @RequirePermissions('persons:write')
  async guardarZonas(@Req() req: Request, @Body() body: { zonas?: unknown }) {
    if (!Array.isArray(body?.zonas)) throw new BadRequestException('Faltan las zonas');
    return this.persons.guardarZonas(req.auth as AuthContext, body.zonas as never);
  }

  /** Agrega un piso: subsuelo, planta baja, entrepiso, lo que sea. */
  @Post('floors')
  @RequirePermissions('persons:write')
  async crearPiso(@Req() req: Request, @Body() body: { name?: string }) {
    return this.persons.crearPiso(req.auth as AuthContext, String(body?.name ?? ''));
  }

  /** Le cambia el nombre a un piso, o su lugar en la lista. */
  @Post('floors/:id')
  @RequirePermissions('persons:write')
  async renombrarPiso(
    @Req() req: Request,
    @Param('id', new ParseUUIDPipe()) id: string,
    @Body() body: { name?: string; orden?: number },
  ) {
    await this.persons.renombrarPiso(
      req.auth as AuthContext,
      id,
      String(body?.name ?? ''),
      Number(body?.orden ?? 0),
    );
    return { ok: true };
  }

  /** Sube o reemplaza el plano de un piso. */
  @Post('floors/:id/plan')
  @RequirePermissions('persons:write')
  async subirPlano(
    @Req() req: Request,
    @Param('id', new ParseUUIDPipe()) id: string,
    @Body() body: { image?: string; ancho?: number; alto?: number },
  ) {
    if (!body?.image) throw new BadRequestException('Falta la imagen del plano');
    await this.persons.guardarPlano(req.auth as AuthContext, id, body.image, body.ancho, body.alto);
    return { ok: true };
  }

  /** Borra un piso con todo lo que tenga marcado encima. */
  @Delete('floors/:id')
  @RequirePermissions('persons:write')
  async borrarPiso(@Req() req: Request, @Param('id', new ParseUUIDPipe()) id: string) {
    await this.persons.borrarPiso(req.auth as AuthContext, id);
    return { ok: true };
  }

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
