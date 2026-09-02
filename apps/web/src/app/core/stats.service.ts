import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, catchError, map, of } from 'rxjs';

/**
 * Los números del panel, medidos.
 *
 * Todo lo que devuelve este servicio sale de la base o del disco. Cuando algo
 * no se puede consultar devuelve `null` y NO un valor de ejemplo: la pantalla
 * muestra un guión y dice que no pudo. Un panel que inventa cifras plausibles
 * es peor que uno vacío — nadie puede distinguir el dato del relleno, y las
 * decisiones se toman igual.
 */

/** Cuántos eventos hubo hoy, con qué forma y comparados con ayer. */
export interface EstadisticasEventos {
  hoy: number;
  /** Ayer HASTA ESTA MISMA HORA. Comparar contra el día entero daría siempre
   *  una caída: a las diez de la mañana no se lleva medio día contra 24 h. */
  ayer: number;
  criticosHoy: number;
  criticosAyer: number;
  /** 24 valores, uno por hora del día de hoy. Las horas sin eventos van en 0. */
  porHora: number[];
  /** El módulo viene con el tipo para pintarlo con el color que ese módulo
   *  ya tiene en el catálogo, en vez de estrenar una paleta aparte. */
  porTipo: { eventType: string; moduleKey: string; total: number }[];
  porModulo: { moduleKey: string; total: number }[];
}

/** Lo que ocupan las evidencias, y cuánto queda en el disco. */
export interface Almacenamiento {
  evidenciasBytes: number;
  evidenciasArchivos: number;
  discoTotalBytes: number;
  discoLibreBytes: number;
  ruta: string;
}

@Injectable({ providedIn: 'root' })
export class StatsService {
  private readonly http = inject(HttpClient);

  /** Agregados de eventos del día. Null si event-service no contestó. */
  eventos(): Observable<EstadisticasEventos | null> {
    return this.http
      .get<EstadisticasEventos>('/api/v1/events/stats')
      .pipe(catchError(() => of(null)));
  }

  /** Uso de disco real de las evidencias. Null si media-service no contestó. */
  almacenamiento(): Observable<Almacenamiento | null> {
    return this.http.get<Almacenamiento>('/media/storage').pipe(catchError(() => of(null)));
  }

  /**
   * Cuántas personas distintas se identificaron hoy.
   *
   * Sale del registro de accesos, así que existe sólo donde el módulo de
   * ingreso de personas está asignado a alguna cámara. Sin él, el endpoint
   * contesta 409 y acá se devuelve null: la tarjeta no se muestra, en vez de
   * mostrar un cero que se leería como "hoy no entró nadie".
   */
  personasIdentificadasHoy(): Observable<number | null> {
    const inicio = new Date();
    inicio.setHours(0, 0, 0, 0);
    const qs = `?desde=${encodeURIComponent(inicio.toISOString())}&hasta=${encodeURIComponent(
      new Date().toISOString(),
    )}`;
    return this.http
      .get<{ personasDistintas: number }>(`/analytics/api/v1/persons/report/access${qs}`)
      .pipe(
        map((r) => r?.personasDistintas ?? null),
        catchError(() => of(null)),
      );
  }
}
