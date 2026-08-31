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

/** Un elemento de protección visto en el cuadro. */
export interface ElementoEpp {
  clave: string;
  /** Cómo se llama en pantalla: "casco", "chaleco"… */
  nombre: string;
  /** true = lo tiene puesto; false = se ve que le falta. */
  tiene: boolean;
  /** Si en esta cámara es obligatorio. Lo que no se exige se dibuja apagado. */
  exigido: boolean;
  conf: number;
  bbox: [number, number, number, number];
  /** Índice de la persona a la que pertenece, o null si no se pudo saber.
   *  Es el índice DENTRO del módulo de EPP: no sirve para indexar `personas`,
   *  que las detecta otro modelo. Para eso está `eppPersonas`. */
  persona: number | null;
}

/** Si a una persona se le ve el elemento, se le ve que le falta, o no se sabe. */
export type EstadoEpp = 'tiene' | 'falta' | 'no_se_sabe';

/** Una persona vista por el módulo de EPP, con qué tiene puesto y qué no. */
export interface PersonaEpp {
  trackId: number;
  bbox: [number, number, number, number];
  /** Por cada elemento obligatorio en esta cámara, en qué estado está. */
  estado: Record<string, EstadoEpp>;
}

export interface VistaEnVivo {
  /** false = el módulo de ingreso de personas no está corriendo en esta cámara. */
  modulo: boolean;
  /** false = se está usando el modelo de detección: hay caja, no contorno. */
  siluetas: boolean;
  personas: PersonaEnVivo[];
  /** false = esta cámara no tiene asignado el módulo de EPP. */
  moduloEpp: boolean;
  /** Qué elementos son obligatorios en esta cámara. */
  exigidos: string[];
  epp: ElementoEpp[];
  /** Las personas segun el modulo de EPP, con el estado de cada elemento. */
  eppPersonas: PersonaEpp[];
  /** Lo que se vigila pero todavía no se puede alertar: el modelo no distingue
   *  esa ausencia con precisión suficiente como para acusar a nadie. */
  sinAlertarEpp: string[];
}

const VACIO: VistaEnVivo = {
  modulo: false, siluetas: false, personas: [],
  moduloEpp: false, exigidos: [], epp: [], eppPersonas: [], sinAlertarEpp: [],
};

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
        moduloEpp: !!r?.moduloEpp,
        exigidos: r?.exigidos ?? [],
        epp: r?.epp ?? [],
        eppPersonas: r?.eppPersonas ?? [],
        sinAlertarEpp: r?.sinAlertarEpp ?? [],
      })),
      // Que el worker no conteste no puede tapar el video: se sigue viendo la
      // cámara, sólo que sin nadie marcado.
      catchError(() => of(VACIO)),
    );
  }
}
