import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable, catchError, map, of } from 'rxjs';

/** Una persona dada de alta, con cuántas plantillas tiene. */
export interface Persona {
  id: string;
  displayName: string;
  active: boolean;
  hasAccess: boolean;
  /** Miniatura de su cara. Null si se cargó sin ninguna foto utilizable. */
  photo: string | null;
  /** Zona del plano donde trabaja. Null si no se le asignó ninguna. */
  workZone: string | null;
  consentBasis: string;
  consentAt: string;
  facesCount: number;
  createdAt: string;
}

export type TipoFoto = 'frontal' | 'perfil' | 'espalda';

/**
 * Qué se pudo hacer con una foto.
 *
 * `plantilla: false` no es un error de la aplicación: una foto de espaldas no
 * tiene cara y no puede producir una plantilla. Lo que importa es que el motivo
 * llegue hasta la pantalla, para que nadie crea que subió algo que sirve.
 */
export interface ResultadoFoto {
  tipo: TipoFoto;
  plantilla: boolean;
  motivo: string;
  score?: number;
  yaEsDeOtro?: { id: string; displayName: string; parecido: number };
}

export interface AltaManual {
  displayName: string;
  hasAccess: boolean;
  consentBasis: string;
  /** Miniatura, cuando el alta viene de una cara ya detectada. */
  photo?: string;
}

@Injectable({ providedIn: 'root' })
export class RecognitionService {
  private readonly http = inject(HttpClient);
  private readonly base = '/analytics/api/v1/persons';

  /** Las personas dadas de alta. */
  listar(): Observable<Persona[]> {
    return this.http
      .get<{ items: Persona[] }>(this.base)
      .pipe(map((r) => r.items ?? []), catchError(() => of([])));
  }

  /** Alta manual: se crea la ficha y después se le suman las fotos. */
  alta(datos: AltaManual): Observable<{ id: string } | { error: string }> {
    return this.http.post<{ id: string }>(this.base, datos).pipe(
      catchError((err: HttpErrorResponse) =>
        of({ error: err?.error?.message ?? 'No se pudo dar de alta a la persona' }),
      ),
    );
  }

  /**
   * Suma una foto. El servidor la convierte en plantilla si tiene una cara
   * utilizable, y si no, dice por qué no.
   */
  subirFoto(personId: string, imagenBase64: string, tipo: TipoFoto): Observable<ResultadoFoto> {
    return this.http
      .post<ResultadoFoto>(`${this.base}/${personId}/photos`, { image: imagenBase64, kind: tipo })
      .pipe(
        catchError((err: HttpErrorResponse) =>
          of({
            tipo,
            plantilla: false,
            motivo: err?.error?.message ?? 'No se pudo procesar la foto',
          } as ResultadoFoto),
        ),
      );
  }

  /** Da o quita el acceso de una persona ya cargada. */
  cambiarAcceso(personId: string, hasAccess: boolean, note?: string): Observable<boolean> {
    return this.http
      .post(`${this.base}/${personId}/access`, { hasAccess, note })
      .pipe(map(() => true), catchError(() => of(false)));
  }

  /** El plano del lugar que subió la empresa. */
  plano(): Observable<string | null> {
    return this.http
      .get<{ image: string | null }>(`${this.base}/floorplan`)
      .pipe(map((r) => r.image), catchError(() => of(null)));
  }

  /** Sube o reemplaza el plano. */
  subirPlano(image: string): Observable<boolean> {
    return this.http
      .post(`${this.base}/floorplan`, { image })
      .pipe(map(() => true), catchError(() => of(false)));
  }

  /** Asigna la zona del plano donde trabaja. */
  cambiarZona(personId: string, workZone: string | null): Observable<boolean> {
    return this.http
      .post(`${this.base}/${personId}/zone`, { workZone })
      .pipe(map(() => true), catchError(() => of(false)));
  }

  /** Baja definitiva: se lleva las plantillas y el historial por cascada. */
  baja(personId: string): Observable<boolean> {
    return this.http
      .delete(`${this.base}/${personId}`)
      .pipe(map(() => true), catchError(() => of(false)));
  }
}
