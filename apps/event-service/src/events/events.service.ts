import { ConflictException, Injectable, NotFoundException, UnprocessableEntityException } from '@nestjs/common';
import type { EventDto, EventStatus } from '@percepta/contracts';
import { DatabaseService } from '../db/database.service';
import { EventsRepository, type ListFilters } from './events.repository';
import { acknowledgeEvent, resolveEvent, WorkflowError, type ReviewableEvent } from '../domain/workflow';
import type { AuthContext } from '../auth/auth.types';

type Resolution = Extract<EventStatus, 'confirmed' | 'dismissed' | 'false_positive'>;

@Injectable()
export class EventsService {
  constructor(
    private readonly db: DatabaseService,
    private readonly repo: EventsRepository,
  ) {}

  async list(auth: AuthContext, filters: ListFilters): Promise<{ items: EventDto[]; total: number }> {
    return this.db.withTenant(auth.organizationId, (c) => this.repo.list(c, filters));
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

  /** `acknowledged → confirmed | dismissed | false_positive`. */
  async resolve(
    auth: AuthContext,
    id: string,
    resolution: Resolution,
    note?: string,
    requestId?: string,
  ): Promise<EventDto> {
    return this.transition(auth, id, (evt, userId) => resolveEvent(evt, resolution, userId, note), note, requestId);
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
        detail: { from: current.status, to: next.status, note: note ?? null },
      });

      return updated;
    });
  }
}
