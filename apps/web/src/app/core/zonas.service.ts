import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable, catchError, map, of } from 'rxjs';
import type { Plano, TipoZona, Zona } from './zonas';

interface ZonaApi {
  id: string;
  key: string;
  name: string;
  kind: TipoZona;
  x: number;
  y: number;
  w: number;
  h: number;
  personas: number;
}

/** El plano guardado, o el motivo por el que no se pudo traer. */
export interface PlanoConZonas {
  plano: Plano;
  zonas: Zona[];
}

@Injectable({ providedIn: 'root' })
export class ZonasService {
  private readonly http = inject(HttpClient);
  private readonly base = '/analytics/api/v1/persons';

  /** El plano y sus bloques. */
  cargar(): Observable<PlanoConZonas> {
    return this.http.get<{ plano: Plano; zonas: ZonaApi[] }>(`${this.base}/zones`).pipe(
      map((r) => ({
        plano: r.plano ?? { image: null, ancho: null, alto: null },
        zonas: (r.zonas ?? []).map((z) => ({
          id: z.id,
          clave: z.key,
          nombre: z.name,
          tipo: z.kind,
          x: z.x,
          y: z.y,
          w: z.w,
          h: z.h,
          personas: z.personas,
        })),
      })),
      catchError(() => of({ plano: { image: null, ancho: null, alto: null }, zonas: [] })),
    );
  }

  /**
   * Guarda el plano entero.
   *
   * Devuelve el motivo cuando falla en vez de un booleano: el rechazo típico
   * —"no se puede borrar Oficina 3, hay 1 persona asignada"— es una frase que
   * el usuario tiene que leer para saber qué hacer.
   */
  guardar(zonas: Zona[]): Observable<{ ok: boolean; motivo?: string }> {
    const cuerpo = {
      zonas: zonas.map((z) => ({
        key: z.clave,
        name: z.nombre.trim(),
        kind: z.tipo,
        x: z.x,
        y: z.y,
        w: z.w,
        h: z.h,
      })),
    };
    return this.http.post(`${this.base}/zones`, cuerpo).pipe(
      map(() => ({ ok: true })),
      catchError((err: HttpErrorResponse) =>
        of({ ok: false, motivo: (err?.error?.message as string) ?? 'No se pudo guardar el plano' }),
      ),
    );
  }

  /** Sube la imagen de fondo, con su tamaño real para no deformarla. */
  subirPlano(image: string, ancho: number, alto: number): Observable<boolean> {
    return this.http
      .post(`${this.base}/floorplan`, { image, ancho, alto })
      .pipe(map(() => true), catchError(() => of(false)));
  }

  /** Dice en qué bloque del plano está parada una cámara. */
  ponerZonaDeCamara(cameraId: string, floorZoneId: string | null): Observable<boolean> {
    return this.http
      .post(`/device/api/v1/cameras/${cameraId}/floor-zone`, { floorZoneId })
      .pipe(map(() => true), catchError(() => of(false)));
  }
}
