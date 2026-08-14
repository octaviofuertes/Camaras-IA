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
  /**
   * Si tiene permitido estar donde mira esta cámara.
   *
   * Se responde al dar de alta, junto con el nombre. No hay valor "no sé": si
   * alguien está dado de alta, alguien decidió esto, y de esa decisión depende
   * que suene o no una alerta urgente.
   */
  hasAccess: boolean;
  consentBasis: string;
  /** Vector facial de 512 dimensiones que venía con la alerta. */
  embedding?: number[];
  notes?: string;
  /** El operador ya vio el aviso de parecido y afirma que es otra persona. */
  forzarNueva?: boolean;
}

export interface Paso {
  id: string;
  personId: string;
  displayName: string;
  desde: string;
  hasta: string;
  minutos: number;
  /** Mejor parecido facial del paso: 0 = nunca se le vio la cara. */
  bestScore: number;
  seenByFace: boolean;
  hadAccess: boolean;
  cameraId: string;
}

/**
 * Cuánto vale un reporte de presencia antes de considerarse viejo.
 *
 * El worker manda uno por frame (unos 3 s). Con seis segundos, si el worker se
 * cae o la cámara se corta la lista se vacía sola en vez de mostrar para
 * siempre a gente que no está. Es la diferencia entre "no hay nadie" y "no
 * sabemos", y acá vale más equivocarse por decir de menos.
 */
const PRESENCIA_VIGENCIA_MS = 6000;

/**
 * Cuánto sigue mostrándose una persona después de la última vez que se la
 * identificó en un frame.
 *
 * Identificar falla un cuadro cada tanto —se da vuelta, se tapa, la cara sale
 * borrosa—. Sin este margen la lista parpadearía: la persona desaparecería y
 * volvería cada pocos segundos, y eso se lee como que entró y salió cuarenta
 * veces. Cinco segundos alcanzan para tapar un par de cuadros perdidos y siguen
 * siendo, a ojo de quien mira, "se fue y desapareció".
 */
const PERSONA_VIGENCIA_MS = 5000;

/**
 * Calidad mínima de una foto para que su plantilla sirva.
 *
 * Más exigente que reconocer en vivo, y por la misma razón que al preguntar por
 * un desconocido: esta plantilla va a decidir a quién se reconoce durante meses.
 * Una foto mala no falla ahora, falla dentro de tres semanas identificando a
 * otra persona, que es cuando nadie la va a relacionar con esta pantalla.
 */
const FOTO_SCORE_MINIMO = 0.65;
const FOTO_ALTO_MINIMO = 0.08;

export type TipoFoto = 'frontal' | 'perfil' | 'espalda';

export interface ResultadoFoto {
  tipo: TipoFoto;
  /** Si se pudo crear una plantilla facial con esta foto. */
  plantilla: boolean;
  motivo: string;
  score?: number;
  /** Si esta cara se parece a alguien YA dado de alta que no es esta persona. */
  yaEsDeOtro?: { id: string; displayName: string; parecido: number };
}

export interface Presente {
  personId: string;
  displayName: string;
  hasAccess: boolean;
  desde: string;
  ultimaVez: string;
  seenByFace: boolean;
  cameraId: string;
}

export interface RegistroAccesos {
  desde: string;
  hasta: string;
  pasos: Paso[];
  personasDistintas: number;
  sinAcceso: number;
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
        hasAccess: datos.hasAccess !== false,
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

  /** Alta de un paso que reportó el pipeline. */
  async registrarPaso(
    auth: AuthContext,
    m: {
      siteId: string; cameraId: string; personId: string;
      from: number; to: number; bestScore: number;
      seenByFace: boolean; hadAccess: boolean;
    },
  ): Promise<{ id: string; nuevo: boolean }> {
    if (!(m.to >= m.from)) {
      throw new BadRequestException('El paso termina antes de empezar');
    }
    return this.db.withTenant(auth.organizationId, (c) =>
      this.repo.registrarPaso(c, { ...m, organizationId: auth.organizationId }),
    );
  }

  /**
   * Lo último que reportó cada cámara sobre quién tiene en el cuadro.
   *
   * En memoria y no en la base a propósito: es el presente, llega una vez por
   * frame y se reemplaza entero. Escribirlo sería miles de filas por hora para
   * responder una pregunta que sólo importa durante tres segundos.
   */
  private readonly presencia = new Map<
    string,
    { at: number; personas: Map<string, { at: number; p: Presente }> }
  >();

  /**
   * Alta de un reporte de presencia del pipeline.
   *
   * Se fusiona por persona en vez de reemplazar la lista entera: quien no vino
   * en ESTE cuadro no desaparece al instante, se le empieza a contar el
   * vencimiento. Es lo que evita que la lista parpadee cuando la
   * identificación falla un frame.
   */
  reportarPresencia(cameraId: string, personas: Presente[]): void {
    const ahora = Date.now();
    // El módulo manda `desde` como segundos desde la época; la pantalla espera
    // una fecha. Sin normalizarlo acá, `new Date(1786...)` lo lee como
    // milisegundos y la pantalla mostraba "hace 495770 h". Se convierte una
    // vez, en la frontera, y no en cada lugar que lo consuma.
    for (const p of personas) {
      const n = Number(p.desde);
      if (Number.isFinite(n) && n > 0) p.desde = new Date(n * 1000).toISOString();
    }
    const previo = this.presencia.get(cameraId);
    const mapa = previo?.personas ?? new Map<string, { at: number; p: Presente }>();

    for (const p of personas) {
      const anterior = mapa.get(p.personId);
      mapa.set(p.personId, {
        at: ahora,
        // El "desde" del anterior manda: es cuándo empezó a estar, y el módulo
        // lo recalcula si abre un paso nuevo.
        p: anterior ? { ...p, desde: anterior.p.desde || p.desde } : p,
      });
    }

    for (const [pid, v] of mapa) {
      if (ahora - v.at > PERSONA_VIGENCIA_MS) mapa.delete(pid);
    }

    this.presencia.set(cameraId, { at: ahora, personas: mapa });
  }

  /**
   * Quién está siendo detectado en este momento.
   *
   * La tolerancia es la misma con la que el módulo cierra un paso: si no se lo
   * ve hace más de eso, ya no está. Un número más chico haría parpadear la
   * lista cada vez que alguien se da vuelta.
   */
  async presentes(auth: AuthContext): Promise<{ presentes: Presente[]; enVivo: boolean }> {
    const ahora = Date.now();
    const frescos: Presente[] = [];
    let hayReporte = false;

    for (const [camara, r] of this.presencia) {
      if (ahora - r.at > PRESENCIA_VIGENCIA_MS) {
        // Esa cámara dejó de reportar: se olvida en vez de mostrar su última
        // foto como si fuera el presente.
        this.presencia.delete(camara);
        continue;
      }
      hayReporte = true;
      for (const [pid, v] of r.personas) {
        if (ahora - v.at > PERSONA_VIGENCIA_MS) {
          r.personas.delete(pid);
          continue;
        }
        frescos.push(v.p);
      }
    }

    if (hayReporte) {
      // El acceso se resuelve contra la base, no contra lo que dijo el módulo:
      // si se le revocó hace diez segundos, la pantalla en vivo tiene que
      // mostrarlo ya, y la galería del módulo se refresca cada treinta.
      const personas = await this.db.withTenant(auth.organizationId, (c) =>
        this.repo.listar(c),
      );
      const acceso = new Map(personas.map((p) => [p.id, p.hasAccess]));
      for (const p of frescos) {
        if (acceso.has(p.personId)) p.hasAccess = acceso.get(p.personId) as boolean;
      }
      return { presentes: frescos, enVivo: true };
    }

    // Sin reportes frescos no se inventa: la pantalla dice que no hay señal en
    // vivo en vez de mostrar a alguien que quizá se fue hace una hora.
    return { presentes: [], enVivo: false };
  }

  /**
   * Suma una foto a una persona: la convierte en plantilla si tiene cara.
   *
   * La conversión la hace el worker, que es donde está cargado el modelo. Acá
   * se decide si el resultado sirve y se guarda.
   */
  async agregarFoto(
    auth: AuthContext,
    personId: string,
    imagenBase64: string,
    tipo: TipoFoto,
  ): Promise<ResultadoFoto> {
    if (!imagenBase64) throw new BadRequestException('Falta la imagen');

    const base = process.env.AI_WORKER_URL ?? 'http://127.0.0.1:3010';
    let caras: { embedding: number[]; score: number; alto: number; yaw: number | null }[] = [];
    try {
      const r = await fetch(`${base}/faces/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: imagenBase64 }),
      });
      const cuerpo = (await r.json()) as { ok?: boolean; error?: string; caras?: typeof caras };
      if (!cuerpo.ok) {
        throw new BadRequestException(
          cuerpo.error ?? 'no se pudo analizar la foto',
        );
      }
      caras = cuerpo.caras ?? [];
    } catch (err) {
      if (err instanceof BadRequestException) throw err;
      this.logger.error(`no se pudo hablar con el worker para analizar la foto: ${err}`);
      throw new BadRequestException(
        'El servicio de reconocimiento no responde. Revisá que el módulo de control de ' +
          'accesos esté asignado a una cámara y que el worker esté corriendo.',
      );
    }

    const mejor = caras[0];
    if (!mejor) {
      // El caso de la foto de espaldas, y también el de una foto movida. Se
      // dice qué pasó en vez de guardar algo que no sirve.
      return {
        tipo,
        plantilla: false,
        motivo:
          tipo === 'espalda'
            ? 'No hay ninguna cara en la foto, que es lo esperable de una foto de espaldas. ' +
              'Sirve como referencia visual, pero no se puede reconocer a nadie con ella.'
            : 'No se detectó ninguna cara en la foto.',
      };
    }

    if (mejor.score < FOTO_SCORE_MINIMO || mejor.alto < FOTO_ALTO_MINIMO) {
      return {
        tipo,
        plantilla: false,
        score: mejor.score,
        motivo:
          `La cara se ve poco (nitidez ${mejor.score}, ocupa el ` +
          `${Math.round(mejor.alto * 100)}% de la foto). Con una plantilla así se ` +
          'confundiría a esta persona con otra. Probá más de cerca o con mejor luz.',
      };
    }

    return this.db.withTenant(auth.organizationId, async (c) => {
      // ¿Esta cara ya es de otro? Es el mismo control que en el alta desde una
      // alerta, y acá importa más: al subir fotos a mano es fácil equivocarse
      // de persona, y una plantilla ajena en la ficha de alguien hace que el
      // sistema los confunda a los dos para siempre.
      const parecida = await this.buscarParecida(c, mejor.embedding);
      if (parecida && parecida.id !== personId) {
        return {
          tipo,
          plantilla: false,
          score: mejor.score,
          yaEsDeOtro: parecida,
          motivo:
            `Esta cara se parece a ${parecida.displayName}, que ya está dado de alta. ` +
            'Si son la misma persona, sumale la foto a esa ficha; si no, revisá que la ' +
            'foto sea de quien creés.',
        };
      }

      await this.repo.agregarRostro(c, auth.organizationId, personId, mejor.embedding, mejor.score);
      this.logger.log(`foto ${tipo} agregada a ${personId} (nitidez ${mejor.score})`);
      return {
        tipo,
        plantilla: true,
        score: mejor.score,
        motivo: 'Plantilla creada: a partir de ahora se lo reconoce también desde este ángulo.',
      };
    });
  }

  /** Cambia si una persona tiene acceso. Queda registrado quién lo decidió. */
  async cambiarAcceso(
    auth: AuthContext,
    personId: string,
    hasAccess: boolean,
    note?: string,
  ): Promise<void> {
    const ok = await this.db.withTenant(auth.organizationId, (c) =>
      this.repo.cambiarAcceso(c, personId, hasAccess, auth.userId, note),
    );
    if (!ok) throw new NotFoundException('Persona no encontrada');
    this.logger.log(
      `acceso de ${personId} -> ${hasAccess ? 'permitido' : 'DENEGADO'} por ${auth.userId}`,
    );
  }

  /**
   * Registro de accesos del período: quién pasó y a qué hora.
   */
  async registro(
    auth: AuthContext,
    desde: string,
    hasta: string,
    cameraId?: string,
  ): Promise<RegistroAccesos> {
    const d = new Date(desde);
    const h = new Date(hasta);
    if (Number.isNaN(d.getTime()) || Number.isNaN(h.getTime()) || d >= h) {
      throw new BadRequestException('rango de fechas inválido');
    }

    const filas = await this.db.withTenant(auth.organizationId, (c) =>
      this.repo.registroDeAccesos(c, d.toISOString(), h.toISOString(), cameraId),
    );

    const pasos: Paso[] = filas.map((f) => ({
      ...f,
      minutos: Math.round(((new Date(f.hasta).getTime() - new Date(f.desde).getTime()) / 60000) * 10) / 10,
    }));

    return {
      desde: d.toISOString(),
      hasta: h.toISOString(),
      pasos,
      personasDistintas: new Set(pasos.map((p) => p.personId)).size,
      sinAcceso: pasos.filter((p) => !p.hadAccess).length,
      advertencias: advertir(pasos),
    };
  }
}

/**
 * Las advertencias viajan con el registro.
 *
 * Un control de accesos se lee para decidir sobre personas. Presentarlo sin
 * decir de dónde sale cada identificación es invitar a que alguien tome una
 * decisión con un dato que no aguanta ese peso.
 */
function advertir(pasos: Paso[]): string[] {
  const avisos: string[] = [];
  if (!pasos.length) return avisos;

  const deducidos = pasos.filter((p) => !p.seenByFace).length;
  if (deducidos) {
    const pct = Math.round((deducidos / pasos.length) * 1000) / 10;
    avisos.push(
      `En el ${pct}% de los pasos NO se le vio la cara a la persona: se dedujo quién era ` +
        'por continuidad del seguimiento o por el puesto donde estaba. Están marcados ' +
        'en la columna "cómo se supo". Ante cualquier decisión sobre alguien, contrastá ' +
        'con el video.',
    );
  }

  if (pasos.some((p) => !p.hadAccess)) {
    avisos.push(
      'Hay pasos de personas sin acceso. Cada uno generó su alerta en Eventos ' +
        'cuando ocurrió; acá figuran para poder reconstruir qué pasó después.',
    );
  }

  avisos.push(
    'Sólo aparecen las personas dadas de alta con su consentimiento registrado. ' +
      'De quien no está dado de alta no se guarda ningún dato, así que no deja rastro ' +
      'en este registro: no es una lista completa de quién estuvo.',
  );
  return avisos;
}
