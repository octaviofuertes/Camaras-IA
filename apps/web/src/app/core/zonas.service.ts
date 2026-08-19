import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable, catchError, map, of } from 'rxjs';
import type { Piso, TipoZona, Zona } from './zonas';

interface ZonaApi {
  id: string;
  floorId: string;
  key: string;
  name: string;
  kind: TipoZona;
  x: number;
  y: number;
  w: number;
  h: number;
  personas: number;
}

interface PisoApi {
  id: string;
  name: string;
  orden: number;
  image: string | null;
  ancho: number | null;
  alto: number | null;
  zonas: ZonaApi[];
}

@Injectable({ providedIn: 'root' })
export class ZonasService {
  private readonly http = inject(HttpClient);
  private readonly base = '/analytics/api/v1/persons';

  /** Los pisos del lugar, con sus planos y lo marcado encima. */
  cargar(): Observable<Piso[]> {
    return this.http.get<{ pisos: PisoApi[] }>(`${this.base}/zones`).pipe(
      map((r) =>
        (r.pisos ?? []).map((f) => ({
          id: f.id,
          nombre: f.name,
          orden: f.orden,
          image: f.image,
          ancho: f.ancho,
          alto: f.alto,
          zonas: (f.zonas ?? []).map((z) => ({
            id: z.id,
            pisoId: z.floorId,
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
      ),
      catchError(() => of([] as Piso[])),
    );
  }

  /**
   * Guarda todo lo marcado, de todos los pisos.
   *
   * Devuelve el motivo cuando falla en vez de un booleano: el rechazo típico
   * —"no se puede borrar Oficina 3, hay 1 persona asignada"— es una frase que
   * el usuario tiene que leer para saber qué hacer.
   */
  guardar(pisos: Piso[]): Observable<{ ok: boolean; motivo?: string }> {
    const zonas = pisos.flatMap((p) =>
      p.zonas.map((z) => ({
        floorId: p.id,
        key: z.clave,
        name: z.nombre.trim(),
        kind: z.tipo,
        x: z.x,
        y: z.y,
        w: z.w,
        h: z.h,
      })),
    );
    return this.http.post(`${this.base}/zones`, { zonas }).pipe(
      map(() => ({ ok: true })),
      catchError((err: HttpErrorResponse) =>
        of({ ok: false, motivo: (err?.error?.message as string) ?? 'No se pudo guardar' }),
      ),
    );
  }

  /** Agrega un piso vacío. */
  crearPiso(name: string): Observable<{ ok: boolean; id?: string; motivo?: string }> {
    return this.http.post<{ id: string }>(`${this.base}/floors`, { name }).pipe(
      map((r) => ({ ok: true, id: r.id })),
      catchError((err: HttpErrorResponse) =>
        of({ ok: false, motivo: (err?.error?.message as string) ?? 'No se pudo agregar el piso' }),
      ),
    );
  }

  /** Le cambia el nombre a un piso, o su lugar en la lista. */
  renombrarPiso(id: string, name: string, orden: number): Observable<boolean> {
    return this.http
      .post(`${this.base}/floors/${id}`, { name, orden })
      .pipe(map(() => true), catchError(() => of(false)));
  }

  /** Sube el plano de un piso, con su tamaño real para no deformarlo. */
  subirPlano(
    id: string,
    image: string,
    ancho: number,
    alto: number,
  ): Observable<{ ok: boolean; motivo?: string }> {
    return this.http.post(`${this.base}/floors/${id}/plan`, { image, ancho, alto }).pipe(
      map(() => ({ ok: true })),
      catchError((err: HttpErrorResponse) =>
        of({ ok: false, motivo: (err?.error?.message as string) ?? 'No se pudo subir el plano' }),
      ),
    );
  }

  /** Borra un piso con lo que tenga marcado. */
  borrarPiso(id: string): Observable<{ ok: boolean; motivo?: string }> {
    return this.http.delete(`${this.base}/floors/${id}`).pipe(
      map(() => ({ ok: true })),
      catchError((err: HttpErrorResponse) =>
        of({ ok: false, motivo: (err?.error?.message as string) ?? 'No se pudo borrar el piso' }),
      ),
    );
  }

  /** Dice en qué área del plano está parada una cámara. */
  ponerZonaDeCamara(cameraId: string, floorZoneId: string | null): Observable<boolean> {
    return this.http
      .post(`/device/api/v1/cameras/${cameraId}/floor-zone`, { floorZoneId })
      .pipe(map(() => true), catchError(() => of(false)));
  }
}
