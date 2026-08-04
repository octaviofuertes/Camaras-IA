import { ConflictException, Injectable, Logger, NotFoundException, UnprocessableEntityException } from '@nestjs/common';
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
export class EventsService {
  private readonly logger = new Logger(EventsService.name);

  constructor(
    private readonly db: DatabaseService,
    private readonly repo: EventsRepository,
  ) {}

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
    });
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

    // Sólo se conserva el video de lo que resultó ser real: guardar clips de
    // falsos positivos sería almacenar (y filmar) gente por nada.
    if (resolution === 'confirmed') {
      void this.captureEvidence(auth, evento, title).catch((err) =>
        this.logger.warn(`no se pudo guardar la evidencia del evento ${id}: ${err}`),
      );
    }

    return evento;
  }

  /**
   * Pide a media-service que arme el clip (10 s antes / evento / 10 s después)
   * y registra la evidencia.
   *
   * Corre fuera del pedido HTTP: el clip necesita esperar el post-evento, y el
   * operador no debe quedarse mirando una pantalla cargando por eso.
   */
  private async captureEvidence(auth: AuthContext, evento: EventDto, title?: string): Promise<void> {
    const base = process.env.MEDIA_SERVICE_URL ?? 'http://127.0.0.1:3020';
    const centerTs = new Date(evento.occurredAt).getTime() / 1000;

    const res = await fetch(`${base}/cameras/${evento.cameraId}/clip`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ eventId: evento.id, centerTs, wait: true }),
    });
    if (!res.ok) throw new Error(`media-service respondió ${res.status}`);
    const info = (await res.json()) as { path?: string; bytes?: number; sha256?: string; durationMs?: number };
    if (!info?.path) throw new Error('media-service no devolvió la ruta del clip');

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
        title: title || `Caída ${new Date(evento.occurredAt).toLocaleString('es-AR')}`,
        createdBy: auth.userId,
      }),
    );
    this.logger.log(`evidencia guardada para el evento ${evento.id}`);
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
