// Workflow de revisión humana de eventos (CONTRACTS §10).
// Refleja en código la misma invariante que el CHECK events_human_review_chk:
// una alerta no sale de 'new' sin un revisor humano identificado.

import { canTransition, type EventClass, type EventStatus } from '@percepta/contracts';

export interface ReviewableEvent {
  id: string;
  status: EventStatus;
  eventClass: EventClass;
  reviewedBy?: string | null;
  reviewedAt?: string | null;
  reviewNote?: string | null;
}

export class WorkflowError extends Error {
  constructor(
    readonly code: 'NOT_REVIEWABLE' | 'INVALID_TRANSITION' | 'REVIEWER_REQUIRED',
    message: string,
  ) {
    super(message);
  }
}

function assertReviewable(evt: ReviewableEvent): void {
  if (evt.eventClass !== 'alert') {
    throw new WorkflowError('NOT_REVIEWABLE', `Los eventos de telemetría no entran al workflow de revisión`);
  }
}

function transition(
  evt: ReviewableEvent,
  to: EventStatus,
  userId: string,
  note?: string,
): ReviewableEvent {
  assertReviewable(evt);
  if (!userId) {
    // Human-in-the-loop: sin humano identificado no hay transición.
    throw new WorkflowError('REVIEWER_REQUIRED', `La transición a '${to}' requiere un revisor humano`);
  }
  if (!canTransition(evt.status, to)) {
    throw new WorkflowError('INVALID_TRANSITION', `Transición inválida: ${evt.status} → ${to}`);
  }
  return {
    ...evt,
    status: to,
    reviewedBy: userId,
    reviewedAt: new Date().toISOString(),
    reviewNote: note ?? evt.reviewNote ?? null,
  };
}

/** `new → acknowledged` — permiso requerido: events:acknowledge (CONTRACTS §9). */
export function acknowledgeEvent(evt: ReviewableEvent, userId: string, note?: string): ReviewableEvent {
  return transition(evt, 'acknowledged', userId, note);
}

/** `acknowledged → confirmed|dismissed|false_positive` — permiso: events:resolve. */
export function resolveEvent(
  evt: ReviewableEvent,
  resolution: Extract<EventStatus, 'confirmed' | 'dismissed' | 'false_positive'>,
  userId: string,
  note?: string,
): ReviewableEvent {
  return transition(evt, resolution, userId, note);
}
