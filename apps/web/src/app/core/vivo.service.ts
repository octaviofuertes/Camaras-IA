import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, catchError, map, of } from 'rxjs';

/** Alguien que la cámara está viendo ahora mismo. */
export interface PersonaEnVivo {
  /** Identificador del seguimiento. Es lo único que hay si no se sabe quién es. */
  trackId: number;
  personId: string;
  nombre: string;
  /**
   * null = no se sabe quién es todavía.
   *
   * No es lo mismo que `false`: false es "sé quién es y no tendría que estar
   * acá", y eso es una alerta. Confundirlos pintaría de rojo a cualquier
   * desconocido, que es la mitad de la gente cuando alguien recién se instala.
   */
  tieneAcceso: boolean | null;
  /** Hace cuánto está en el lugar, en segundos. Null si no se lo identificó. */
  haceSegundos: number | null;
  /** x, y, ancho, alto en fracciones del cuadro. */
  bbox: [number, number, number, number];
  /** Contorno del cuerpo en fracciones. Null si el modelo no segmenta. */
  silueta: [number, number][] | null;
}

export interface VistaEnVivo {
  /** false = el módulo de ingreso de personas no está corriendo en esta cámara. */
  modulo: boolean;
  /** false = se está usando el modelo de detección: hay caja, no contorno. */
  siluetas: boolean;
  personas: PersonaEnVivo[];
}

const VACIO: VistaEnVivo = { modulo: false, siluetas: false, personas: [] };

/**
 * Quién se ve en una cámara, en este momento.
 *
 * Va directo al ai-worker y no a analytics porque es el presente puro: son las
 * coordenadas del frame que se está mostrando, y pasarlas por la base las
 * dejaría siempre un poco atrás de lo que se ve.
 */
@Injectable({ providedIn: 'root' })
export class VivoService {
  private readonly http = inject(HttpClient);

  enVivo(cameraId: string): Observable<VistaEnVivo> {
    return this.http.get<VistaEnVivo>(`/ai/live/${cameraId}`).pipe(
      map((r) => ({
        modulo: !!r?.modulo,
        siluetas: !!r?.siluetas,
        personas: r?.personas ?? [],
      })),
      // Que el worker no conteste no puede tapar el video: se sigue viendo la
      // cámara, sólo que sin nadie marcado.
      catchError(() => of(VACIO)),
    );
  }
}
