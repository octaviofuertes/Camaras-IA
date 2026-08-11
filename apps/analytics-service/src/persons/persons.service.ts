import {
  BadRequestException,
  ConflictException,
  Injectable,
  Logger,
  NotFoundException,
} from '@nestjs/common';
import { DatabaseService } from '../db/database.service';
import { PersonsRepository, type PersonaDto } from './persons.repository';
import type { AuthContext } from '../auth/auth.types';

export interface AltaConRostro {
  displayName: string;
  consentBasis: string;
  /** Vector facial de 512 dimensiones que venía con la alerta. */
  embedding?: number[];
  notes?: string;
  /** El operador ya vio el aviso de parecido y afirma que es otra persona. */
  forzarNueva?: boolean;
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

/**
 * Parecido a partir del cual esta cara probablemente ya está dada de alta.
 *
 * Deliberadamente MÁS BAJO que el umbral de identificación (0.42). No es una
 * casualidad: si la cara superara ese umbral, el módulo la habría reconocido y
 * no habría preguntado nada. Las altas duplicadas nacen justo en la franja de
 * abajo — la misma persona de perfil contra su plantilla de frente da 0.30— y
 * ahí es donde hay que avisar.
 *
 * Avisar no es bloquear: dos hermanos existen. Se le devuelve al operador quién
 * cree el sistema que es, y decide él.
 */
const PARECIDO_SOSPECHOSO = 0.25;

function coseno(a: number[], b: number[]): number {
  if (a.length !== b.length || a.length === 0) return -1;
  let num = 0;
  let na = 0;
  let nb = 0;
  for (let i = 0; i < a.length; i++) {
    num += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  if (na <= 0 || nb <= 0) return -1;
  return num / (Math.sqrt(na) * Math.sqrt(nb));
}

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
      // Antes de crear a nadie: ¿esta cara ya es de alguien dado de alta?
      //
      // Sin esta comprobación, la misma persona vista de otro ángulo se daba de
      // alta otra vez, y el informe le partía las horas entre dos filas con el
      // mismo nombre. El operador no tiene forma de saberlo mirando un recorte.
      if (datos.embedding?.length && !datos.forzarNueva) {
        const parecida = await this.buscarParecida(c, datos.embedding);
        if (parecida) {
          throw new ConflictException({
            message:
              `Esta cara se parece a ${parecida.displayName}, que ya está dada de alta. ` +
              'Si es la misma persona, sumale esta foto en vez de darla de alta otra vez: ' +
              'con varios ángulos se la reconoce mejor.',
            parecidaA: parecida,
          });
        }
      }

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

  /**
   * La persona dada de alta cuya cara más se parece a esta, si alguna.
   *
   * Se compara contra TODAS las plantillas de cada una y se toma la mejor, igual
   * que hace el módulo: alguien con varias fotos se reconoce en más posiciones.
   */
  private async buscarParecida(
    c: Parameters<Parameters<DatabaseService['withTenant']>[1]>[0],
    embedding: number[],
  ): Promise<{ id: string; displayName: string; parecido: number } | null> {
    const galeria = await this.repo.galeria(c);
    let mejor: { id: string; displayName: string; parecido: number } | null = null;
    for (const p of galeria) {
      for (const v of p.embeddings) {
        const s = coseno(embedding, v);
        if (s >= PARECIDO_SOSPECHOSO && (!mejor || s > mejor.parecido)) {
          mejor = { id: p.id, displayName: p.displayName, parecido: Math.round(s * 1000) / 1000 };
        }
      }
    }
    return mejor;
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
        'pueden haber sido de un visitante. Es la suma de todos los no ' +
        'identificados a la vez, así que puede superar la duración del período: ' +
        'tres personas durante una hora son tres horas.',
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
