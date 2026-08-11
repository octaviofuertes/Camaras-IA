import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, catchError, of } from 'rxjs';

export interface ResumenPuesto {
  cameraId: string;
  zoneId: string | null;
  zoneName: string;
  observadoSegundos: number;
  ocupadoSegundos: number;
  telefonoSegundos: number;
  vacioSegundos: number;
  sinCoberturaSegundos: number;
  ocupacionPct: number;
  telefonoPct: number;
  coberturaPct: number;
  ocupacionMedia: number;
  maxPersonas: number;
}

export interface PuntoSerie {
  periodo: string;
  cameraId: string;
  zoneId: string | null;
  zoneName: string;
  windowSeconds: number;
  occupiedSeconds: number;
  phoneSeconds: number;
  emptySeconds: number;
  uncoveredSeconds: number;
  maxPeople: number;
  meanOccupancy: number;
}

export interface Informe {
  desde: string;
  hasta: string;
  puestos: ResumenPuesto[];
  serie: PuntoSerie[];
  total: ResumenPuesto | null;
  advertencias: string[];
}

/** Informe vacío: la pantalla necesita algo coherente cuando no hay datos. */
const VACIO: Informe = {
  desde: '',
  hasta: '',
  puestos: [],
  serie: [],
  total: null,
  advertencias: [],
};

export interface FilaNominal {
  personId: string | null;
  displayName: string;
  presenteSegundos: number;
  telefonoSegundos: number;
  telefonoPct: number;
  identificado: boolean;
}

export interface InformeNominal {
  desde: string;
  hasta: string;
  personas: FilaNominal[];
  sinIdentificarSegundos: number;
  advertencias: string[];
  /** true si el usuario no tiene permiso para ver nombres. */
  sinPermiso?: boolean;
}

@Injectable({ providedIn: 'root' })
export class ReportsService {
  private readonly http = inject(HttpClient);
  // Va por su propio prefijo: analytics-service es un servicio aparte de
  // eventos, y mezclarlos en /api haría que un informe caído se viera como si
  // los eventos estuvieran caídos.
  private readonly base = '/analytics/api/v1/analytics';

  actividad(params: {
    desde: string;
    hasta: string;
    cameraId?: string;
    bucket?: 'hour' | 'day';
  }): Observable<Informe> {
    const qs = new URLSearchParams({
      desde: params.desde,
      hasta: params.hasta,
      bucket: params.bucket ?? 'hour',
    });
    if (params.cameraId) qs.set('cameraId', params.cameraId);

    return this.http.get<Informe>(`${this.base}/activity?${qs.toString()}`).pipe(
      catchError((err) => {
        console.warn('[informes] analytics-service no respondió:', err.status);
        return of({ ...VACIO, desde: params.desde, hasta: params.hasta });
      }),
    );
  }

  /**
   * Informe con nombre y apellido.
   *
   * Requiere `reports:identified`, que sólo tienen los administradores. Un 403
   * no es un error a esconder: se devuelve marcado para que la pantalla explique
   * por qué no se ve, en vez de mostrar una sección vacía sin motivo.
   */
  porPersona(params: { desde: string; hasta: string; cameraId?: string }): Observable<InformeNominal> {
    const qs = new URLSearchParams({ desde: params.desde, hasta: params.hasta });
    if (params.cameraId) qs.set('cameraId', params.cameraId);

    return this.http
      .get<InformeNominal>(`/analytics/api/v1/persons/report/activity?${qs.toString()}`)
      .pipe(
        catchError((err) => {
          const sinPermiso = err?.status === 403;
          if (!sinPermiso) {
            console.warn('[informes] no se pudo traer el informe por persona:', err?.status);
          }
          return of({
            desde: params.desde,
            hasta: params.hasta,
            personas: [],
            sinIdentificarSegundos: 0,
            advertencias: [],
            sinPermiso,
          });
        }),
      );
  }
}
