import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable, catchError, map, of } from 'rxjs';
import type { ModuleCategory } from './models';

/** Cámara tal como la devuelve device-service. */
export interface ApiCamera {
  id: string;
  siteId: string;
  name: string;
  location: string | null;
  status: string;
  /** Índice USB ("0") o URL RTSP. */
  source: string | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  moduleCount: number;
  /** En qué bloque del plano está parada. Null = todavía no se dijo. */
  floorZoneId: string | null;
}

/** Módulo del catálogo (tabla ai_modules). */
export interface ApiModule {
  id: string;
  moduleKey: string;
  name: string;
  description: string | null;
  category: ModuleCategory;
  version: string;
  configSchema: Record<string, unknown>;
}

/** Asignación módulo ↔ cámara (fila de camera_module_configs). */
export interface ApiAssignment {
  id: string;
  cameraId: string;
  aiModuleId: string;
  moduleKey: string;
  moduleName: string;
  category: ModuleCategory;
  config: Record<string, unknown>;
  enabled: boolean;
}

/** Estado de captura en vivo que reporta media-service. */
export interface MediaStatus {
  cameraId: string;
  name: string;
  connected: boolean;
  lastError: string | null;
  fps: number;
  bufferSeconds: number;
  framesCaptured: number;
}

export interface LiveDetection {
  moduleKey: string;
  classLabel: string;
  confidence: number;
  bbox: [number, number, number, number];
}

export interface CreateCameraInput {
  name: string;
  location?: string;
  source: string;
  fps?: number;
  width?: number;
  height?: number;
}

@Injectable({ providedIn: 'root' })
export class CamerasService {
  private readonly http = inject(HttpClient);
  private readonly api = '/device/api/v1';

  /** Último motivo por el que falló la consulta, para explicarlo en pantalla. */
  lastError: { status: number; message: string } | null = null;

  // ── cámaras ────────────────────────────────────────────────────────
  listCameras(): Observable<ApiCamera[] | null> {
    return this.http
      .get<{ items: ApiCamera[] }>(`${this.api}/cameras`)
      .pipe(
        map((r) => {
          this.lastError = null;
          return r.items;
        }),
        // null distingue "no pude consultar" de "no hay cámaras": la UI
        // muestra mensajes distintos y nunca inventa datos.
        catchError((e: HttpErrorResponse) => {
          // Guardar el motivo real: un 500 (base caída) no es lo mismo que un
          // 401 (token vencido) ni que un 0 (servicio apagado).
          this.lastError = { status: e.status, message: e?.error?.message ?? e.message ?? '' };
          return of(null);
        }),
      );
  }

  /** Explicación accionable del fallo, según lo que respondió el servidor. */
  errorHint(): string {
    const e = this.lastError;
    if (!e) return '';
    if (e.status === 0) {
      return 'device-service no responde. Levantalo con: node apps/device-service/dist/main.js';
    }
    if (e.status === 401) return 'Tu token venció o es inválido. Generá uno nuevo y volvé a guardarlo.';
    if (e.status === 403) return 'Tu usuario no tiene permiso para ver cámaras (cameras:read).';
    if (e.status === 500) {
      return 'device-service respondió, pero falló contra la base de datos. Verificá que Docker esté abierto y la infraestructura levantada (pnpm infra:up).';
    }
    return `Error ${e.status}: ${e.message}`;
  }

  createCamera(input: CreateCameraInput): Observable<ApiCamera | { error: string }> {
    return this.http
      .post<ApiCamera>(`${this.api}/cameras`, input)
      .pipe(catchError((e) => of({ error: e?.error?.message ?? 'No se pudo crear la cámara' })));
  }

  updateCamera(id: string, patch: Partial<CreateCameraInput> & { status?: string }): Observable<boolean> {
    return this.http
      .patch(`${this.api}/cameras/${id}`, patch)
      .pipe(map(() => true), catchError(() => of(false)));
  }

  deleteCamera(id: string): Observable<boolean> {
    return this.http
      .delete(`${this.api}/cameras/${id}`)
      .pipe(map(() => true), catchError(() => of(false)));
  }

  // ── catálogo y asignaciones ────────────────────────────────────────
  listModules(): Observable<ApiModule[]> {
    return this.http
      .get<{ items: ApiModule[] }>(`${this.api}/modules`)
      .pipe(map((r) => r.items), catchError(() => of([])));
  }

  listAssignments(): Observable<ApiAssignment[]> {
    return this.http
      .get<{ items: ApiAssignment[] }>(`${this.api}/camera-module-configs`)
      .pipe(map((r) => r.items), catchError(() => of([])));
  }

  /** Persiste el drop de un módulo sobre una cámara. */
  assignModule(cameraId: string, aiModuleId: string, config: Record<string, unknown> = {}): Observable<boolean> {
    return this.http
      .post(`${this.api}/cameras/${cameraId}/modules`, { aiModuleId, config })
      .pipe(map(() => true), catchError(() => of(false)));
  }

  updateModuleConfig(cameraId: string, aiModuleId: string, config: Record<string, unknown>): Observable<boolean> {
    return this.http
      .patch(`${this.api}/cameras/${cameraId}/modules/${aiModuleId}`, { config })
      .pipe(map(() => true), catchError(() => of(false)));
  }

  unassignModule(cameraId: string, aiModuleId: string): Observable<boolean> {
    return this.http
      .delete(`${this.api}/cameras/${cameraId}/modules/${aiModuleId}`)
      .pipe(map(() => true), catchError(() => of(false)));
  }

  // ── video en vivo ──────────────────────────────────────────────────
  mediaStatus(): Observable<MediaStatus[]> {
    return this.http
      .get<{ items: MediaStatus[] }>('/media/cameras')
      .pipe(map((r) => r.items), catchError(() => of([])));
  }

  snapshotUrl(cameraId: string): string {
    return `/media/cameras/${cameraId}/snapshot.jpg`;
  }

  streamUrl(cameraId: string): string {
    return `/media/cameras/${cameraId}/stream.mjpg`;
  }

  detections(): Observable<Record<string, LiveDetection[]>> {
    return this.http
      .get<Record<string, LiveDetection[]>>('/ai/detections')
      .pipe(catchError(() => of({})));
  }
}
