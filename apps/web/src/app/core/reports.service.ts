import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, catchError, map, of } from 'rxjs';

/** Un paso: una persona frente a una cámara entre dos horas. */
export interface Paso {
  id: string;
  personId: string;
  displayName: string;
  desde: string;
  hasta: string;
  minutos: number;
  /** Mejor parecido facial del paso. 0 = nunca se le vio la cara. */
  bestScore: number;
  seenByFace: boolean;
  hadAccess: boolean;
  cameraId: string;
}

export interface RegistroAccesos {
  desde: string;
  hasta: string;
  pasos: Paso[];
  personasDistintas: number;
  sinAcceso: number;
  advertencias: string[];
  /** true si el usuario no tiene permiso para ver el registro. */
  sinPermiso?: boolean;
}

const VACIO: RegistroAccesos = {
  desde: '',
  hasta: '',
  pasos: [],
  personasDistintas: 0,
  sinAcceso: 0,
  advertencias: [],
};

/** Alguien a quien la cámara está viendo en este momento. */
export interface Presente {
  personId: string;
  displayName: string;
  hasAccess: boolean;
  desde: string;
  ultimaVez: string;
  seenByFace: boolean;
  cameraId: string;
}

@Injectable({ providedIn: 'root' })
export class ReportsService {
  private readonly http = inject(HttpClient);

  /**
   * Quién está siendo detectado ahora.
   *
   * Sale de los mismos pasos que el registro, no de una segunda fuente: lo que
   * se ve en vivo y lo que queda escrito no pueden discrepar.
   */
  enVivo(): Observable<{ presentes: Presente[]; enVivo: boolean }> {
    return this.http
      .get<{ presentes: Presente[]; enVivo: boolean }>('/analytics/api/v1/persons/live')
      .pipe(catchError(() => of({ presentes: [], enVivo: false })));
  }

  /**
   * Registro de accesos: quién pasó y a qué hora.
   *
   * Requiere `reports:identified`, que sólo tienen los administradores. Saber a
   * qué hora entra y sale cada persona todos los días es un dato sobre su vida,
   * no sobre la seguridad del lugar. Un 403 no se esconde: se devuelve marcado
   * para que la pantalla explique por qué no se ve.
   */
  accesos(params: { desde: string; hasta: string; cameraId?: string }): Observable<RegistroAccesos> {
    const qs = new URLSearchParams({ desde: params.desde, hasta: params.hasta });
    if (params.cameraId) qs.set('cameraId', params.cameraId);

    return this.http
      .get<RegistroAccesos>(`/analytics/api/v1/persons/report/access?${qs.toString()}`)
      .pipe(
        catchError((err) => {
          const sinPermiso = err?.status === 403;
          if (!sinPermiso) {
            console.warn('[accesos] no se pudo traer el registro:', err?.status);
          }
          return of({ ...VACIO, desde: params.desde, hasta: params.hasta, sinPermiso });
        }),
      );
  }
}
