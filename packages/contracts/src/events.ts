// Contratos de evento — canónico: docs/CONTRACTS.md §5, §10.
// La DB persiste el enum en INGLÉS; la UI localiza. El texto localizado nunca se persiste.

export const EVENT_STATUS = ['new', 'acknowledged', 'confirmed', 'dismissed', 'false_positive'] as const;
export type EventStatus = (typeof EVENT_STATUS)[number];

export const EVENT_CLASS = ['alert', 'telemetry'] as const;
export type EventClass = (typeof EVENT_CLASS)[number];

export const SEVERITY = ['info', 'low', 'medium', 'high', 'critical'] as const;
export type Severity = (typeof SEVERITY)[number];

/** Transiciones válidas de la máquina de estados (CONTRACTS §10). */
export const EVENT_TRANSITIONS: Record<EventStatus, EventStatus[]> = {
  new: ['acknowledged'],
  acknowledged: ['confirmed', 'dismissed', 'false_positive'],
  confirmed: [],
  dismissed: [],
  false_positive: [],
};

export function canTransition(from: EventStatus, to: EventStatus): boolean {
  return EVENT_TRANSITIONS[from]?.includes(to) ?? false;
}

/** Mapa DB (EN) → etiqueta i18n key para la UI. */
export const EVENT_STATUS_I18N: Record<EventStatus, string> = {
  new: 'event.status.new',
  acknowledged: 'event.status.acknowledged',
  confirmed: 'event.status.confirmed',
  dismissed: 'event.status.dismissed',
  false_positive: 'event.status.falsePositive',
};

/** DTO de evento tal como lo expone la API (camelCase). */
export interface EventDto {
  id: string;
  occurredAt: string; // ISO-8601 UTC
  organizationId: string;
  siteId: string;
  cameraId: string;
  aiModuleId: string;
  moduleKey: string;
  moduleVersion: string;
  eventType: string;
  eventClass: EventClass;
  severity: Severity;
  confidence: number; // 0..1
  status: EventStatus;
  zoneIds: string[];
  trackId?: number;
  detection: Record<string, unknown>;
  metadata: Record<string, unknown>;
  reviewedBy?: string;
  reviewedAt?: string;
  reviewNote?: string;
  /** Nombre que le puso el operador al confirmarla. */
  reviewTitle?: string;
  createdAt: string;
}

export interface AcknowledgeEventDto {
  note?: string;
}

export interface ResolveEventDto {
  resolution: Extract<EventStatus, 'confirmed' | 'dismissed' | 'false_positive'>;
  note?: string;
  /** Al confirmar una caída real, el nombre con el que se guarda la evidencia. */
  title?: string;
}
