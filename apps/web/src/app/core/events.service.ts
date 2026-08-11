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
  detection?: Record<string, unknown>;
}

export interface EvidenceItem {
  id: string;
  kind: 'image' | 'clip';
  storageKey: string;
  contentType: string;
  bytes: number;
  durationMs: number | null;
  preRollMs: number | null;
  postRollMs: number | null;
  title: string | null;
  status: string;
  createdAt: string;
}

/** Alta de una persona a partir de una alerta de reconocimiento. */
export interface AltaPersona {
  displayName: string;
  consentBasis: string;
  embedding?: number[];
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
  'person.unknown': '¿Reconocés a esta persona?',
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

  /**
   * Da de alta a la persona de una alerta de reconocimiento.
   *
   * `consentBasis` es obligatorio y la base lo hace cumplir: sin él no se puede
   * guardar el dato biométrico de nadie.
   */
  altaPersona(p: AltaPersona): Observable<{ id: string } | null> {
    return this.http
      .post<{ id: string }>('/analytics/api/v1/persons', p)
      .pipe(catchError(() => of(null)));
  }

  /** URL de descarga del clip. El archivo lo sirve media-service. */
  evidenceUrl(cameraId: string, storageKey: string): string {
    // Se corta por AMBOS separadores: media-service corre sobre Windows y sus
    // claves vienen con `\`. Partiendo sólo por `/` el nombre salía entero
    // —ruta incluida—, media-service lo rechazaba por contener `\` y el video
    // no cargaba nunca. Un servidor Linux no habría mostrado el problema.
    const nombre = storageKey.split(/[\\/]/).pop() ?? '';
    return `/media/evidence/${cameraId}/${encodeURIComponent(nombre)}`;
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
      // Sólo presentes en las alertas de reconocimiento: la miniatura para
      // que el operador vea de quién se habla, y el vector para poder dar
      // de alta si la respuesta es que sí.
      faceThumbnail: (e.detection?.['faceThumbnail'] as string) ?? undefined,
      faceEmbedding: (e.detection?.['faceEmbedding'] as string) ?? undefined,
    };
  }
}
