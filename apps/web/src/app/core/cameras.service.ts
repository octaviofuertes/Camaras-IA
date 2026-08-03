import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, catchError, map, of } from 'rxjs';
import type { Camera } from './models';
import { DEMO_CAMERAS } from './demo-data';

interface MediaCamera {
  cameraId: string;
  source: string;
  connected: boolean;
  lastError: string | null;
  framesCaptured: number;
  fps: number;
  bufferedFrames: number;
  bufferSeconds: number;
}

export interface LiveDetection {
  moduleKey: string;
  classLabel: string;
  confidence: number;
  bbox: [number, number, number, number];
}

export interface CamerasResult {
  items: Camera[];
  /** true si media-service no respondió y se muestran cámaras de ejemplo. */
  demo: boolean;
}

/** Nombres legibles por cámara mientras device-service no exista. */
const NAMES: Record<string, { name: string; site: string; scene: string }> = {
  '00000000-0000-4000-b000-00000000ca01': {
    name: 'Webcam Integrada',
    site: 'Puesto de trabajo',
    scene: 'office',
  },
  '00000000-0000-4000-b000-00000000ca02': {
    name: 'Logitech C925e',
    site: 'Vista general oficina',
    scene: 'office',
  },
};

@Injectable({ providedIn: 'root' })
export class CamerasService {
  private readonly http = inject(HttpClient);

  /** Cámaras REALES que media-service está capturando. */
  list(): Observable<CamerasResult> {
    return this.http.get<{ items: MediaCamera[] }>('/media/cameras').pipe(
      map((res) => ({
        items: res.items.map((c, i) => this.toCamera(c, i)),
        demo: false,
      })),
      catchError(() => of({ items: DEMO_CAMERAS, demo: true })),
    );
  }

  /** URL del stream en vivo (MJPEG). Se consume directo desde un <img>. */
  streamUrl(cameraId: string): string {
    return `/media/cameras/${cameraId}/stream.mjpg`;
  }

  snapshotUrl(cameraId: string): string {
    return `/media/cameras/${cameraId}/snapshot.jpg`;
  }

  /** Últimas detecciones por cámara, para dibujar las cajas sobre el video. */
  detections(): Observable<Record<string, LiveDetection[]>> {
    return this.http
      .get<Record<string, LiveDetection[]>>('/ai/detections')
      .pipe(catchError(() => of({})));
  }

  private toCamera(c: MediaCamera, i: number): Camera {
    const meta = NAMES[c.cameraId];
    return {
      id: c.cameraId,
      code: String(i + 1).padStart(2, '0'),
      name: meta?.name ?? `Cámara ${c.cameraId.slice(-4)}`,
      siteName: meta?.site ?? 'Sin sucursal',
      status: c.connected ? 'online' : 'offline',
      thumbnail: meta?.scene ?? 'office',
      modules: [],
      live: true,
      fps: c.fps,
      bufferSeconds: c.bufferSeconds,
    };
  }
}
