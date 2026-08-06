import {
  ConflictException,
  Injectable,
  Logger,
  NotFoundException,
  OnModuleDestroy,
  OnModuleInit,
  UnprocessableEntityException,
} from '@nestjs/common';
import type { EventDto, EventStatus } from '@percepta/contracts';
import { DatabaseService } from '../db/database.service';
import { EventsRepository, type ListFilters } from './events.repository';
import { acknowledgeEvent, resolveEvent, WorkflowError, type ReviewableEvent } from '../domain/workflow';
import type { AuthContext } from '../auth/auth.types';

type Resolution = Extract<EventStatus, 'confirmed' | 'dismissed' | 'false_positive'>;

export interface IngestInput {
  siteId: string;
  cameraId: string;
  aiModuleId: string;
  moduleKey: string;
  moduleVersion: string;
  eventType: string;
  eventClass?: string;
  severity: string;
  confidence: number;
  dedupKey: string;
  zoneIds?: string[];
  trackId?: number;
  detection?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  /** Ventana de features de pose que sostiene la alerta (para reentrenar). */
  trainingSequence?: number[][];
}

@Injectable()
export class EventsService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(EventsService.name);
  private purga?: NodeJS.Timeout;

  constructor(
    private readonly db: DatabaseService,
    private readonly repo: EventsRepository,
  ) {}

  onModuleInit(): void {
    // La retención tiene que correr sola. Un método de purga que hay que
    // acordarse de invocar es, en la práctica, video de gente acumulándose sin
    // límite: cada falsa alarma que nadie miró deja un clip para siempre.
    const horas = Number(process.env.EVIDENCE_PURGE_HOURS ?? 6);
    const dias = Number(process.env.EVIDENCE_RETENTION_DAYS ?? 7);
    const cada = Math.max(horas, 1) * 60 * 60 * 1000;
    this.purga = setInterval(() => {
      void this.purgarTodo(dias).catch((err) => this.logger.warn(`purga de evidencia: ${err}`));
    }, cada);
    this.purga.unref?.();
    // Una pasada al arrancar limpia lo que haya quedado de la sesión anterior.
    void this.purgarTodo(dias).catch((err) => this.logger.warn(`purga inicial de evidencia: ${err}`));
  }

  onModuleDestroy(): void {
    if (this.purga) clearInterval(this.purga);
  }

  private async purgarTodo(dias: number): Promise<void> {
    const orgs = await this.db.withTenant(
      process.env.PURGE_ORG_ID ?? '00000000-0000-4000-b000-000000000001',
      (c) => this.repo.orgsConEvidenciaPurgable(c),
    );
    for (const org of orgs) await this.purgarEvidenciaSinRevisar(org, dias);
  }

  async list(auth: AuthContext, filters: ListFilters): Promise<{ items: EventDto[]; total: number }> {
    return this.db.withTenant(auth.organizationId, (c) => this.repo.list(c, filters));
  }

  /**
   * Alta desde el pipeline. Devuelve null si la deduplicación lo descartó
   * (no es un error: es la protección contra ráfagas de alertas repetidas).
   */
  async ingest(auth: AuthContext, e: IngestInput): Promise<EventDto | null> {
    return this.db.withTenant(auth.organizationId, async (client) => {
      const evento = await this.repo.insert(client, { ...e, organizationId: auth.organizationId });

      // La ventana de esqueletos se guarda junto al evento, SIN etiqueta. La
      // etiqueta llega cuando un operador lo revise: ahí se vuelve un ejemplo
      // de entrenamiento. Si la deduplicación descartó el evento, no hay nada
      // que etiquetar después, así que tampoco se guarda la muestra.
      if (evento && e.trainingSequence?.length) {
        try {
          await this.repo.saveTrainingSample(client, {
            organizationId: auth.organizationId,
            cameraId: e.cameraId,
            eventId: evento.id,
            eventOccurredAt: evento.occurredAt,
            sequence: e.trainingSequence,
            ruleConfidence: e.confidence,
          });
        } catch (err) {
          this.logger.warn(`no se pudo guardar la muestra del evento ${evento.id}: ${err}`);
        }
      }
      return evento;
    }).then((evento) => {
      // El clip se graba AHORA, no al confirmarlo. Dos razones, y la segunda
      // invalidaba la función entera:
      //
      //  1. Nadie puede decidir si algo fue una caída sin ver el video. Pedir
      //     la confirmación primero y mostrar el clip después es el orden al
      //     revés.
      //  2. El buffer en memoria de la cámara dura 25 segundos. Al confirmar
      //     una alerta —aunque sea un minuto más tarde— los frames ya no
      //     existen: el clip NUNCA se armaba. Verificado contra el servicio:
      //     503 "no se pudo armar el clip (¿buffer vacío?)".
      //
      // Queda como `pending`: es un clip provisional, sujeto a revisión. Si el
      // operador dice que no fue una caída, se borra.
      if (evento && this.mereceEvidencia(evento)) {
        void this.capturarClipProvisional(auth, evento).catch((err) =>
          this.logger.warn(`no se pudo grabar el clip provisional de ${evento.id}: ${err}`),
        );
      }
      return evento;
    });
  }

  /**
   * Qué alertas se graban en video mientras esperan revisión.
   *
   * Sólo las graves: un clip por cada detección de persona llenaría el disco y
   * significaría filmar a todo el mundo todo el tiempo sin que nadie lo mire.
   */
  private mereceEvidencia(evento: EventDto): boolean {
    return evento.severity === 'high' || evento.severity === 'critical';
  }

  async findOne(auth: AuthContext, id: string, occurredAt?: string): Promise<EventDto> {
    const evt = await this.db.withTenant(auth.organizationId, (c) =>
      this.repo.findById(c, id, occurredAt),
    );
    // Si la RLS lo filtró por pertenecer a otro tenant, la respuesta es 404:
    // un 403 confirmaría su existencia y filtraría información entre tenants.
    if (!evt) throw new NotFoundException(`Evento ${id} no encontrado`);
    return evt;
  }

  /** `new → acknowledged`. Requiere revisor humano (human-in-the-loop). */
  async acknowledge(auth: AuthContext, id: string, note?: string, requestId?: string): Promise<EventDto> {
    return this.transition(auth, id, (evt, userId) => acknowledgeEvent(evt, userId, note), note, requestId);
  }

  /**
   * `acknowledged → confirmed | dismissed | false_positive`.
   *
   * Además del cambio de estado, el veredicto tiene dos consecuencias:
   *  - etiqueta la secuencia de esqueletos que generó la alerta, que pasa a ser
   *    un ejemplo de entrenamiento;
   *  - si se confirma como caída real, se guarda el clip como evidencia con el
   *    nombre que le puso el operador.
   */
  async resolve(
    auth: AuthContext,
    id: string,
    resolution: Resolution,
    note?: string,
    requestId?: string,
    title?: string,
  ): Promise<EventDto> {
    const evento = await this.transition(
      auth, id, (evt, userId) => resolveEvent(evt, resolution, userId, note), note, requestId, title,
    );

    // El feedback humano es la etiqueta: confirmado = caída, falso positivo = no.
    if (resolution === 'confirmed' || resolution === 'false_positive') {
      const label = resolution === 'confirmed' ? 1 : 0;
      try {
        await this.db.withTenant(auth.organizationId, (c) =>
          this.repo.labelTrainingSample(c, id, label, auth.userId),
        );
      } catch (err) {
        // Que falle el etiquetado no debe romper la revisión del operador.
        this.logger.warn(`no se pudo etiquetar la muestra del evento ${id}: ${err}`);
      }
    }

    // El clip provisional ya existe desde que sonó la alerta. Acá sólo se
    // decide su destino: se conserva con el nombre que eligió el operador, o se
    // borra. Guardar video de gente que no protagonizó nada es exactamente lo
    // que no queremos.
    try {
      if (resolution === 'confirmed') {
        const promovidas = await this.db.withTenant(auth.organizationId, (c) =>
          this.repo.confirmEvidence(c, id, title, auth.userId),
        );
        if (promovidas === 0) {
          // No había clip provisional: el buffer estaba vacío al momento de la
          // alerta, o el evento es anterior a esta función. Se intenta ahora,
          // aunque para una alerta vieja lo más probable es que ya no haya
          // frames — por eso existe la captura al detectar.
          this.logger.warn(`el evento ${id} no tenía clip provisional; se intenta grabarlo ahora`);
          void this.captureEvidence(auth, evento, title, 'ready').catch((err) =>
            this.logger.warn(`tampoco se pudo grabar la evidencia de ${id}: ${err}`),
          );
        }
      } else {
        // Primero el archivo, después la fila. Al revés —que fue como estaba—,
        // si el borrado del archivo falla la fila ya no existe y el video queda
        // en disco sin nada que lo referencie: nadie lo ve y nadie lo va a
        // poder eliminar nunca.
        const pendientes = await this.db.withTenant(auth.organizationId, (c) =>
          this.repo.pendingEvidence(c, id),
        );
        const borrados: string[] = [];
        const trabados: string[] = [];
        for (const p of pendientes) {
          if (await this.borrarClip(p.storageKey)) borrados.push(p.id);
          else trabados.push(p.id);
        }
        await this.db.withTenant(auth.organizationId, async (c) => {
          await this.repo.deleteEvidence(c, borrados);
          await this.repo.markEvidenceExpired(c, trabados);
        });
        if (borrados.length) {
          this.logger.log(`descartado ${borrados.length} clip(s) de ${id}: no fue una caída`);
        }
        if (trabados.length) {
          this.logger.warn(
            `${trabados.length} clip(s) de ${id} no se pudieron borrar ahora (archivo en uso); ` +
              `quedan marcados para la purga`,
          );
        }
      }
    } catch (err) {
      // Que falle el manejo del clip no puede tumbar la revisión del operador.
      this.logger.warn(`no se pudo resolver la evidencia del evento ${id}: ${err}`);
    }

    return evento;
  }

  /** Graba el clip apenas suena la alerta, para que haya QUÉ revisar. */
  private async capturarClipProvisional(auth: AuthContext, evento: EventDto): Promise<void> {
    await this.captureEvidence(auth, evento, undefined, 'pending');
  }

  /** Borra el archivo del clip. Devuelve si el archivo ya no está. */
  private async borrarClip(storageKey: string): Promise<boolean> {
    const base = process.env.MEDIA_SERVICE_URL ?? 'http://127.0.0.1:3020';
    try {
      const res = await fetch(`${base}/evidence`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ storageKey }),
      });
      if (res.ok) return true;
      // 409 = el archivo está siendo leído justo ahora. No es un error: hay que
      // volver a intentarlo más tarde.
      if (res.status !== 409) {
        this.logger.warn(`media-service no pudo borrar ${storageKey}: ${res.status}`);
      }
      return false;
    } catch (err) {
      this.logger.warn(`no se pudo borrar el clip ${storageKey}: ${err}`);
      return false;
    }
  }

  /**
   * Borra los clips provisionales que nadie revisó dentro del plazo.
   *
   * Sin esto, cada falsa alarma que el operador nunca miró deja un video de una
   * persona guardado para siempre. La retención no es una optimización de
   * disco: es la contracara de haber grabado sin que nadie lo pidiera.
   */
  async purgarEvidenciaSinRevisar(organizationId: string, dias = 7): Promise<number> {
    const candidatas = await this.db.withTenant(organizationId, (c) =>
      this.repo.evidenceToPurge(c, dias),
    );
    if (!candidatas.length) return 0;

    const borrados: string[] = [];
    for (const v of candidatas) {
      if (await this.borrarClip(v.storageKey)) borrados.push(v.id);
    }
    // La fila se borra sólo si el archivo ya no está. Si sigue trabado se
    // reintenta en la purga siguiente: mejor una fila de más que un video
    // huérfano que nadie puede encontrar.
    const eliminadas = await this.db.withTenant(organizationId, (c) =>
      this.repo.deleteEvidence(c, borrados),
    );
    const trabados = candidatas.length - borrados.length;
    this.logger.log(
      `purgados ${eliminadas} clip(s) sin revisar` +
        (trabados ? `; ${trabados} siguen en uso, se reintentan luego` : ''),
    );
    return eliminadas;
  }

  /**
   * Pide a media-service que arme el clip (unos segundos antes y después)
   * y registra la evidencia.
   *
   * Corre fuera del pedido HTTP: el clip necesita esperar el post-evento, y el
   * operador no debe quedarse mirando una pantalla cargando por eso.
   */
  private async captureEvidence(
    auth: AuthContext,
    evento: EventDto,
    title?: string,
    status: 'pending' | 'ready' = 'ready',
  ): Promise<void> {
    const base = process.env.MEDIA_SERVICE_URL ?? 'http://127.0.0.1:3020';
    const centerTs = new Date(evento.occurredAt).getTime() / 1000;

    const res = await fetch(`${base}/cameras/${evento.cameraId}/clip`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ eventId: evento.id, centerTs, wait: true }),
    });
    if (!res.ok) throw new Error(`media-service respondió ${res.status}`);
    const info = (await res.json()) as {
      path?: string; bytes?: number; sha256?: string;
      durationMs?: number; preRollMs?: number; postRollMs?: number;
    };
    if (!info?.path) throw new Error('media-service no devolvió la ruta del clip');

    try {
      await this.db.withTenant(auth.organizationId, (c) =>
        this.repo.saveEvidence(c, {
          organizationId: auth.organizationId,
          eventId: evento.id,
          eventOccurredAt: evento.occurredAt,
          kind: 'clip',
          storageKey: info.path!,
          contentType: 'video/mp4',
          bytes: info.bytes ?? 0,
          sha256: info.sha256 ?? '',
          durationMs: info.durationMs,
          preRollMs: info.preRollMs,
          postRollMs: info.postRollMs,
        // El clip provisional no lleva nombre: el nombre lo pone el operador al
        // confirmarlo, y ponerle uno antes sugeriría un veredicto que nadie dio.
          title: status === 'pending' ? undefined : title || `Caída ${new Date(evento.occurredAt).toLocaleString('es-AR')}`,
          createdBy: status === 'pending' ? undefined : auth.userId,
          status,
        }),
      );
    } catch (err) {
      // El clip ya está escrito en disco. Si no se pudo registrar, queda un
      // video de una persona sin ninguna fila que lo respalde: invisible para
      // la UI, para la retención y para el borrado por falso positivo. Se
      // elimina acá o no lo elimina nadie.
      await this.borrarClip(info.path!);
      throw err;
    }
    this.logger.log(
      status === 'pending'
        ? `clip provisional grabado para ${evento.id} (a la espera de revisión)`
        : `evidencia guardada para el evento ${evento.id}`,
    );
  }

  async listEvidences(auth: AuthContext, eventId: string): Promise<Record<string, unknown>[]> {
    return this.db.withTenant(auth.organizationId, (c) => this.repo.listEvidences(c, eventId));
  }

  async trainingStats(auth: AuthContext): Promise<Record<string, number>> {
    return this.db.withTenant(auth.organizationId, (c) => this.repo.trainingStats(c));
  }

  /**
   * Tronco común de las transiciones. Todo ocurre en UNA transacción con el
   * contexto de tenant activo: lectura bloqueante, validación de dominio,
   * escritura condicionada al estado de origen y auditoría.
   */
  private async transition(
    auth: AuthContext,
    id: string,
    apply: (evt: ReviewableEvent, userId: string) => ReviewableEvent,
    note: string | undefined,
    requestId: string | undefined,
    title?: string,
  ): Promise<EventDto> {
    return this.db.withTenant(auth.organizationId, async (client) => {
      const found = await this.repo.findByIdForUpdate(client, id);
      if (!found) throw new NotFoundException(`Evento ${id} no encontrado`);
      const current = found.dto;

      let next: ReviewableEvent;
      try {
        next = apply(
          {
            id: current.id,
            status: current.status,
            eventClass: current.eventClass,
            reviewedBy: current.reviewedBy,
            reviewedAt: current.reviewedAt,
            reviewNote: current.reviewNote,
          },
          auth.userId,
        );
      } catch (err) {
        if (err instanceof WorkflowError) {
          // 422: la petición es sintácticamente válida pero viola una regla del
          // dominio (transición inválida, telemetría no revisable, etc.).
          throw new UnprocessableEntityException({ code: err.code, message: err.message });
        }
        throw err;
      }

      const updated = await this.repo.applyTransition(client, {
        id: current.id,
        // Clave de tiempo con precisión íntegra: el ISO del DTO perdería los
        // microsegundos y el UPDATE no encontraría la fila.
        occurredAt: found.occurredAtRaw,
        fromStatus: current.status,
        toStatus: next.status,
        reviewedBy: auth.userId,
        reviewNote: note,
        reviewTitle: title,
      });

      if (!updated) {
        // El estado cambió entre el SELECT ... FOR UPDATE y el UPDATE.
        throw new ConflictException('El evento fue modificado por otro operador');
      }

      await this.repo.writeAudit(client, {
        organizationId: auth.organizationId,
        actorUserId: auth.userId,
        action: `event.${next.status}`,
        resourceId: current.id,
        requestId,
        detail: { from: current.status, to: next.status, note: note ?? null, title: title ?? null },
      });

      return updated;
    });
  }
}
