import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable, catchError, map, of, switchMap } from 'rxjs';
import type { EventItem, EventStatus, Severity } from './models';

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
  /** Si tiene permitido estar donde mira esta cámara. */
  hasAccess: boolean;
  /** La cara que venía en la alerta, para poder verificar la ficha después. */
  photo?: string;
  consentBasis: string;
  embedding?: number[];
  /** El operador ya vio el aviso de parecido y afirma que es otra persona. */
  forzarNueva?: boolean;
}

export interface PersonaCargada {
  id: string;
  displayName: string;
  facesCount: number;
  hasAccess: boolean;
}

/** Resultado del alta: creada, o rechazada porque esa cara ya es de alguien. */
export interface ResultadoAlta {
  id?: string;
  yaExiste?: { id: string; displayName: string; parecido: number };
  mensaje?: string;
}

/** Quién es la persona de una foto, resuelto a pedido desde una alerta. */
export interface Reconocimiento {
  reconocido: {
    personId: string;
    displayName: string;
    photo: string | null;
    hasAccess: boolean;
    workZone: string | null;
    parecido: number;
  } | null;
  /** Por qué no se pudo. Vacío cuando sí se pudo. */
  motivo: string;
  caras: number;
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
  /**
   * true si la API no respondió.
   *
   * Antes acá se devolvían eventos de ejemplo con este mismo aviso. El problema
   * no era el aviso —la pantalla de Eventos lo mostraba— sino que el panel
   * dibujaba esos eventos sin decir nada, y quedaban cinco alertas inventadas
   * con nombre de cámara y hora, indistinguibles de las reales. Ahora no hay
   * nada que mostrar: una lista vacía con el motivo escrito.
   */
  sinApi: boolean;
}

/**
 * Títulos legibles por tipo de evento (los emite el manifest de cada módulo).
 *
 * Exportado porque el panel nombra los mismos tipos en su gráfico: con dos
 * listas, el mismo evento terminaría llamándose distinto en cada pantalla.
 */
export const TITULOS_EVENTO: Record<string, string> = {
  // Se dice "no se le ve" y no "no tiene": el sistema vio una cabeza sin
  // casco, y quien lea la alerta tiene que ir a mirar, no a sancionar.
  'ppe.helmet_missing': 'No se le ve el casco',
  'ppe.vest_missing': 'No se le ve el chaleco',
  'ppe.goggles_missing': 'No se le ven las antiparras',
  'ppe.gloves_missing': 'No se le ven los guantes',
  'zone.restricted_entry': 'Zona restringida',
  'object.abandoned': 'Objeto abandonado',
  'person.loitering': 'Merodeo detectado',
  'person.detected': 'Persona detectada',
  'person.fall': 'Caída detectada',
  'person.unknown': '¿Reconocés a esta persona?',
  'access.denied': 'ACCESO DENEGADO',
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
        sinApi: false,
      })),
      catchError((err: HttpErrorResponse) => {
        console.warn('[events] la API no respondió:', err.status);
        return of({ items: [], total: 0, sinApi: true });
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
  altaPersona(p: AltaPersona): Observable<ResultadoAlta | null> {
    return this.http.post<{ id: string }>('/analytics/api/v1/persons', p).pipe(
      map((r) => ({ id: r.id } as ResultadoAlta)),
      catchError((err: HttpErrorResponse) => {
        // 409: la cara se parece a alguien ya dado de alta. No es un error que
        // haya que esconder — es justamente lo que evita partir a una persona
        // en dos filas del informe.
        if (err.status === 409) {
          const cuerpo = err.error ?? {};
          return of({ yaExiste: cuerpo.parecidaA, mensaje: cuerpo.message } as ResultadoAlta);
        }
        return of(null);
      }),
    );
  }

  /** Las personas ya dadas de alta, para poder sumarle una foto a una existente. */
  personas(): Observable<PersonaCargada[]> {
    return this.http
      .get<{ items: PersonaCargada[] }>('/analytics/api/v1/persons')
      .pipe(map((r) => r.items ?? []), catchError(() => of([])));
  }

  /**
   * Suma este ángulo a alguien ya dado de alta.
   *
   * Es lo que hace que el reconocimiento mejore con el uso: el módulo compara
   * contra TODAS las plantillas de cada persona y se queda con la mejor, así que
   * de frente, de perfil y con anteojos se reconocen todas.
   */
  sumarRostro(personId: string, embedding: number[]): Observable<boolean> {
    return this.http
      .post(`/analytics/api/v1/persons/${personId}/faces`, { embedding })
      .pipe(map(() => true), catchError(() => of(false)));
  }

  /**
   * ¿Quién es la persona de esta foto?
   *
   * Se dispara SÓLO cuando el operador aprieta el botón. El reconocimiento
   * facial no corre en cada alerta: correrlo siempre gastaría el modelo de
   * rostros miles de veces por día para que nadie lea el resultado, y dejaría
   * escrito en la base quién anduvo sin casco cada día sin que nadie lo pidiera.
   */
  reconocerPersona(imagenBase64: string): Observable<Reconocimiento> {
    return this.http
      .post<Reconocimiento>('/analytics/api/v1/persons/recognize', { image: imagenBase64 })
      .pipe(
        catchError((err: HttpErrorResponse) => {
          // Cada causa se dice con su nombre: son cosas que se arreglan en
          // lugares distintos, y un "no se pudo" genérico manda a buscar el
          // problema donde no está.
          const motivo =
            err.status === 403
              ? 'Tu usuario no tiene permiso para ver el nombre de las personas. Lo habilita ' +
                'un administrador (permiso "Ver informes con nombre y apellido").'
              : err.status === 409
                ? (err.error?.message ??
                  'Para reconocer a alguien hace falta el módulo "Ingreso de personas" asignado ' +
                  'a alguna cámara.')
                : 'No se pudo consultar el reconocimiento. Revisá que analytics-service y el ' +
                  'worker estén corriendo.';
          return of({ reconocido: null, motivo, caras: 0 } as Reconocimiento);
        }),
      );
  }

  /**
   * La foto de la evidencia como data URL, para poder mandarla a analizar.
   *
   * Se lee del archivo ya guardado y no de la cámara: la alerta puede tener
   * horas, y lo que hay que reconocer es a quien estaba en ESE momento.
   */
  fotoEvidencia(url: string): Observable<string> {
    return this.http.get(url, { responseType: 'blob' }).pipe(
      switchMap(
        (b) =>
          new Observable<string>((obs) => {
            const fr = new FileReader();
            fr.onload = () => {
              obs.next(String(fr.result));
              obs.complete();
            };
            fr.onerror = () => obs.error(fr.error);
            fr.readAsDataURL(b);
          }),
      ),
    );
  }

  /** URL de descarga de la evidencia. El archivo lo sirve media-service. */
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
      title: TITULOS_EVENTO[e.eventType] ?? e.eventType,
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
      personName: (e.detection?.['personName'] as string) ?? undefined,
      faceEmbedding: (e.detection?.['faceEmbedding'] as string) ?? undefined,
    };
  }
}
