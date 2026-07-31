import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
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

  /**
   * El token lo emitirá `identity-service`. Mientras tanto se lee de
   * localStorage (`px_token`), que es lo que llena `tools/dev-token.js`.
   */
  private headers(): HttpHeaders {
    const token = localStorage.getItem('px_token') ?? '';
    return new HttpHeaders(token ? { Authorization: `Bearer ${token}` } : {});
  }

  list(status?: EventStatus): Observable<EventsResult> {
    const qs = status ? `?status=${status}&limit=50` : '?limit=50';
    return this.http.get<ApiPage>(`${this.base}/events${qs}`, { headers: this.headers() }).pipe(
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
      .post(`${this.base}/events/${id}/acknowledge`, { note }, { headers: this.headers() })
      .pipe(map(() => true), catchError(() => of(false)));
  }

  resolve(id: string, resolution: 'confirmed' | 'dismissed' | 'false_positive', note?: string): Observable<boolean> {
    return this.http
      .post(`${this.base}/events/${id}/resolve`, { resolution, note }, { headers: this.headers() })
      .pipe(map(() => true), catchError(() => of(false)));
  }

  private toItem(e: ApiEvent): EventItem {
    return {
      id: e.id,
      occurredAt: new Date(e.occurredAt).toLocaleTimeString('es-AR', { hour12: false }),
      eventType: e.eventType,
      title: TITLES[e.eventType] ?? e.eventType,
      moduleKey: e.moduleKey,
      cameraName: `Cámara ${e.cameraId.slice(-4)}`,
      siteName: 'Sucursal',
      severity: e.severity,
      status: e.status,
      confidence: e.confidence,
      reviewedBy: e.reviewedBy,
    };
  }
}
