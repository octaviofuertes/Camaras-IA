import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable, catchError, map, of } from 'rxjs';
import type { EventItem, EventStatus, Severity } from './models';
import { DEMO_EVENTS } from './demo-data';

/** Respuesta paginada de `event-service`. */
interface ApiPage {
  items: ApiEvent[];
  total: number;
  limit: number;
  offset: number;
}

interface ApiEvent {
  id: string;
  occurredAt: string;
  eventType: string;
  moduleKey: string;
  cameraId: string;
  siteId: string;
  severity: Severity;
  status: EventStatus;
  confidence: number;
  reviewedBy?: string;
  reviewNote?: string;
  reviewTitle?: string;
}

export interface EvidenceItem {
  id: string;
  kind: 'image' | 'clip';
  storageKey: string;
  contentType: string;
  bytes: number;
  durationMs: number | null;
  title: string | null;
  status: string;
  createdAt: string;
}

export interface TrainingStats {
  total: number;
  confirmadas: number;
  falsosPositivos: number;
  pendientes: number;
}

export interface EventsResult {
  items: EventItem[];
  total: number;
  /** true si la API no respondió y se están mostrando datos de demostración. */
  demo: boolean;
}

/** Títulos legibles por tipo de evento (los emite el manifest de cada módulo). */
const TITLES: Record<string, string> = {
  'ppe.helmet_missing': 'Sin uso de casco',
  'zone.restricted_entry': 'Zona restringida',
  'object.abandoned': 'Objeto abandonado',
  'person.loitering': 'Merodeo detectado',
  'person.fall': 'Caída detectada',
};

@Injectable({ providedIn: 'root' })
export class EventsService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/v1';

  list(status?: EventStatus): Observable<EventsResult> {
    const qs = status ? `?status=${status}&limit=50` : '?limit=50';
    return this.http.get<ApiPage>(`${this.base}/events${qs}`).pipe(
      map((page) => ({
        items: page.items.map((e) => this.toItem(e)),
        total: page.total,
        demo: false,
      })),
      catchError((err: HttpErrorResponse) => {
        // Sin API disponible se muestra el set de demostración, pero la UI lo
        // dice explícitamente: nunca hacer pasar datos falsos por reales.
        console.warn('[events] API no disponible, usando datos de demo:', err.status);
        const items = status ? DEMO_EVENTS.filter((e) => e.status === status) : DEMO_EVENTS;
        return of({ items, total: items.length, demo: true });
      }),
    );
  }

  acknowledge(id: string, note?: string): Observable<boolean> {
    return this.http
      .post(`${this.base}/events/${id}/acknowledge`, { note })
      .pipe(map(() => true), catchError(() => of(false)));
  }

  /**
   * Resuelve la alerta. Al confirmarla como caída real, `title` es el nombre
   * con el que se guarda el clip de evidencia.
   */
  resolve(
    id: string,
    resolution: 'confirmed' | 'dismissed' | 'false_positive',
    note?: string,
    title?: string,
  ): Observable<boolean> {
    return this.http
      .post(`${this.base}/events/${id}/resolve`, { resolution, note, title })
      .pipe(map(() => true), catchError(() => of(false)));
  }

  /** Evidencias (clips) guardadas para un evento confirmado. */
  evidences(id: string): Observable<EvidenceItem[]> {
    return this.http
      .get<{ items: EvidenceItem[] }>(`${this.base}/events/${id}/evidences`)
      .pipe(map((r) => r.items ?? []), catchError(() => of([])));
  }

  /** Cuántas muestras acumuló el sistema gracias al feedback de los operadores. */
  trainingStats(): Observable<TrainingStats | null> {
    return this.http
      .get<TrainingStats>(`${this.base}/events/training/stats`)
      .pipe(catchError(() => of(null)));
  }

  /** URL de descarga del clip. El archivo lo sirve media-service. */
  evidenceUrl(cameraId: string, storageKey: string): string {
    const nombre = storageKey.split(/[\/]/).pop() ?? '';
    return `/media/evidence/${cameraId}/${nombre}`;
  }

  private toItem(e: ApiEvent): EventItem {
    return {
      id: e.id,
      occurredAt: new Date(e.occurredAt).toLocaleTimeString('es-AR', { hour12: false }),
      eventType: e.eventType,
      title: TITLES[e.eventType] ?? e.eventType,
      moduleKey: e.moduleKey,
      cameraId: e.cameraId,
      cameraName: `Cámara ${e.cameraId.slice(-4)}`,
      siteName: 'Sucursal',
      severity: e.severity,
      status: e.status,
      confidence: e.confidence,
      reviewedBy: e.reviewedBy,
      reviewTitle: e.reviewTitle,
    };
  }
}
