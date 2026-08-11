import { BadRequestException, Injectable, Logger, NotFoundException } from '@nestjs/common';
import { DatabaseService } from '../db/database.service';
import { PersonsRepository, type PersonaDto } from './persons.repository';
import type { AuthContext } from '../auth/auth.types';

export interface AltaConRostro {
  displayName: string;
  consentBasis: string;
  /** Vector facial de 512 dimensiones que venía con la alerta. */
  embedding?: number[];
  notes?: string;
}

export interface FilaNominal {
  personId: string | null;
  displayName: string;
  presenteSegundos: number;
  telefonoSegundos: number;
  telefonoPct: number;
  identificado: boolean;
}

export interface InformeNominal {
  desde: string;
  hasta: string;
  personas: FilaNominal[];
  sinIdentificarSegundos: number;
  advertencias: string[];
}

const DIM_EMBEDDING = 512;

@Injectable()
export class PersonsService {
  private readonly logger = new Logger(PersonsService.name);

  constructor(
    private readonly db: DatabaseService,
    private readonly repo: PersonsRepository,
  ) {}

  /**
   * Da de alta a una persona a partir de una alerta "¿reconocés a esta persona?".
   *
   * `consentRecordedBy` sale del token de quien responde: queda registrado quién
   * afirmó tener el consentimiento, no un genérico "el sistema". Si mañana hay
   * que justificar por qué se guardó la biometría de alguien, esa es la persona
   * a la que se le pregunta.
   */
  async alta(auth: AuthContext, datos: AltaConRostro): Promise<{ id: string }> {
    const nombre = (datos.displayName ?? '').trim();
    const base = (datos.consentBasis ?? '').trim();
    if (nombre.length < 2) throw new BadRequestException('El nombre es obligatorio');
    if (base.length < 3) {
      throw new BadRequestException(
        'Hay que indicar la base del consentimiento: es lo que hace legal guardar el dato biométrico',
      );
    }
    if (datos.embedding && datos.embedding.length !== DIM_EMBEDDING) {
      throw new BadRequestException(`El vector facial debe tener ${DIM_EMBEDDING} dimensiones`);
    }

    return this.db.withTenant(auth.organizationId, async (c) => {
      const id = await this.repo.alta(c, {
        organizationId: auth.organizationId,
        displayName: nombre,
        consentRecordedBy: auth.userId,
        consentBasis: base,
        notes: datos.notes,
      });
      if (datos.embedding?.length) {
        await this.repo.agregarRostro(c, auth.organizationId, id, datos.embedding, 1.0);
      }
      this.logger.log(`alta de persona ${id} — consentimiento registrado por ${auth.userId}`);
      return { id };
    });
  }

  /** Suma otra foto a una persona ya dada de alta (de perfil, con anteojos…). */
  async agregarRostro(auth: AuthContext, personId: string, embedding: number[]): Promise<void> {
    if (embedding.length !== DIM_EMBEDDING) {
      throw new BadRequestException(`El vector facial debe tener ${DIM_EMBEDDING} dimensiones`);
    }
    await this.db.withTenant(auth.organizationId, (c) =>
      this.repo.agregarRostro(c, auth.organizationId, personId, embedding, 1.0),
    );
  }

  async galeria(auth: AuthContext) {
    return this.db.withTenant(auth.organizationId, (c) => this.repo.galeria(c));
  }

  async listar(auth: AuthContext): Promise<PersonaDto[]> {
    return this.db.withTenant(auth.organizationId, (c) => this.repo.listar(c));
  }

  /** Baja definitiva: se lleva las plantillas y los tiempos por cascada. */
  async baja(auth: AuthContext, personId: string): Promise<void> {
    const ok = await this.db.withTenant(auth.organizationId, (c) => this.repo.baja(c, personId));
    if (!ok) throw new NotFoundException('Persona no encontrada');
    this.logger.log(`baja de persona ${personId} solicitada por ${auth.userId}`);
  }

  async ingestarMuestra(
    auth: AuthContext,
    m: {
      siteId: string; cameraId: string; zoneId?: string | null; zoneName: string;
      personId?: string | null; from: number; to: number;
      presentSeconds: number; phoneSeconds: number;
    },
  ): Promise<string | null> {
    if (!(m.to > m.from)) {
      throw new BadRequestException('la ventana medida debe terminar después de empezar');
    }
    return this.db.withTenant(auth.organizationId, (c) =>
      this.repo.insertarMuestraPersona(c, { ...m, organizationId: auth.organizationId }),
    );
  }

  async informe(
    auth: AuthContext,
    desde: string,
    hasta: string,
    cameraId?: string,
  ): Promise<InformeNominal> {
    const d = new Date(desde);
    const h = new Date(hasta);
    if (Number.isNaN(d.getTime()) || Number.isNaN(h.getTime()) || d >= h) {
      throw new BadRequestException('rango de fechas inválido');
    }

    const filas = await this.db.withTenant(auth.organizationId, (c) =>
      this.repo.informeNominal(c, d.toISOString(), h.toISOString(), cameraId),
    );

    const personas: FilaNominal[] = filas.map((f) => ({
      personId: f.personId,
      displayName: f.displayName,
      presenteSegundos: Math.round(f.presentSeconds),
      telefonoSegundos: Math.round(f.phoneSeconds),
      telefonoPct:
        f.presentSeconds > 0 ? Math.round((f.phoneSeconds / f.presentSeconds) * 1000) / 10 : 0,
      identificado: f.personId !== null,
    }));

    const sinIdentificar = personas
      .filter((p) => !p.identificado)
      .reduce((a, p) => a + p.presenteSegundos, 0);

    return {
      desde: d.toISOString(),
      hasta: h.toISOString(),
      personas,
      sinIdentificarSegundos: sinIdentificar,
      advertencias: advertir(personas, sinIdentificar),
    };
  }
}

/**
 * Las advertencias viajan con el informe.
 *
 * Este informe atribuye conducta a personas con nombre y apellido. Presentarlo
 * sin decir cuánto se puede confiar en cada número es invitar a que alguien tome
 * una decisión sobre un trabajador con un dato que no aguanta ese peso.
 */
function advertir(personas: FilaNominal[], sinIdentificar: number): string[] {
  const avisos: string[] = [];
  const total = personas.reduce((a, p) => a + p.presenteSegundos, 0);

  if (sinIdentificar > 0 && total > 0) {
    const pct = Math.round((sinIdentificar / total) * 1000) / 10;
    avisos.push(
      `${pct}% del tiempo observado no se pudo atribuir a nadie. Ese tiempo NO se ` +
        'reparte entre las personas identificadas: hacerlo les sumaría minutos que ' +
        'pueden haber sido de un visitante.',
    );
  }

  if (personas.some((p) => p.telefonoSegundos > 0)) {
    avisos.push(
      'El tiempo de teléfono es una COTA INFERIOR: sólo se cuenta cuando el teléfono ' +
        'se ve. Tapado por el cuerpo o de espaldas no se detecta, así que el valor ' +
        'real es mayor y no se sabe cuánto. Sirve para comparar, no como medida ' +
        'absoluta ni como prueba.',
    );
  }

  avisos.push(
    'La identidad se sostiene por rostro cuando la persona mira a la cámara y por ' +
      'continuidad el resto del tiempo. Una identificación equivocada le atribuiría ' +
      'a alguien la conducta de otro: ante cualquier decisión sobre una persona, ' +
      'contrastá con el video.',
  );
  return avisos;
}
