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
  /**
   * Por qué vía se supo quién es: rostro, seguimiento, apariencia o puesto.
   * "ninguna" cuando no se sabe. Se muestra en la ficha: decir "Juan" cuando
   * en realidad se dedujo por el escritorio en el que está sentado, y no por
   * haberle visto la cara, es esconder de dónde sale el nombre.
   */
  via: string;
  /**
   * Cuándo empezó su visita, en epoch (segundos). Es el paso del registro de
   * accesos: tolera que se tape o salga del cuadro un momento sin reiniciarse.
   * Null mientras no se sepa quién es —ahí no hay visita que registrar—.
   *
   * Viene como instante y no como "hace tantos segundos" para que el
   * cronómetro corra con el reloj del navegador. Un número calculado en el
   * worker llega tan seguido como cuadros procese la cámara —dos por segundo—,
   * y un contador que salta de a medio segundo no es un contador.
   */
  desdeTs: number | null;
  /**
   * Desde cuándo está ESTE cuerpo en el cuadro, en epoch. Siempre viene, se
   * sepa o no quién es: es lo único con que se puede cronometrar a alguien sin
   * identificar, y es exacto desde el primer cuadro en que aparece.
   */
  enCuadroDesdeTs: number;
  /** El cuerpo entero: x, y, ancho, alto en fracciones del cuadro. */
  bbox: [number, number, number, number];
  /** La cara, en fracciones del cuadro. Es lo que se dibuja. */
  rostro: [number, number, number, number];
  /**
   * true = no se le ve la cara en este frame y el recuadro marca dónde está la
   * cabeza, deducida del cuerpo. Se dibuja punteado: un recuadro lleno sobre
   * una nuca diría que se le está viendo la cara.
   */
  rostroEstimado: boolean;
/**
   * true = hay una cara utilizable guardada y se le puede poner un nombre ya.
   * false = todavía no se le agarró una: está de espaldas, de perfil o lejos.
   */
  hayFoto: boolean;
  /**
   * Recorte de su cara, en base64. Llega SÓLO cuando se lo pide para nombrar a
   * esta persona en particular (`enVivo(camara, trackId)`), y nunca para las
   * demás: la imagen de alguien que no está dado de alta no tiene por qué salir
   * del worker varias veces por segundo para todo el que tenga la pantalla
   * abierta. Null el resto del tiempo.
   *
   * No se guarda en ninguna parte: existe mientras esa persona está en el
   * cuadro y desaparece cuando se va.
   */
  foto?: string | null;
  /** Su vector facial en base64, para que el alta arranque con una plantilla. */
  vector?: string | null;
  /**
   * Por qué el sistema no lo reconoció, en las palabras del módulo: "de perfil
   * (48°): irreconocible", "cara demasiado chica", "ambiguo entre dos
   * personas"… Vacío cuando sí se lo reconoció o cuando no se le ve la cara.
   *
   * Se muestra tal cual. "Sin identificar" a secas deja al operador sin saber
   * si el módulo está roto, si la persona está de espaldas o si está tan lejos
   * que no hay nada que arreglar desde el software.
   */
  motivo: string;
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
  /** Cuándo se capturó el cuadro que se está describiendo, en epoch. */
  ts: number;
  /**
   * El reloj del worker en el momento de contestar, en epoch.
   *
   * Es lo que hace exacto al cronómetro. La cuenta `ahora - desdeTs` se
   * resuelve entera en el worker, con un solo reloj, así que no importa si el
   * navegador está en otra máquina con la hora corrida —en una instalación
   * real nunca está en la misma—. La pantalla sólo sigue contando desde ahí.
   *
   * Con `ts` no alcanza: entre que se captura un cuadro y se termina de
   * analizarlo pasan segundos, y contar desde ahí atrasa el cronómetro por ese
   * tanto.
   */
  ahora: number;
  /** false = el módulo de ingreso de personas no está corriendo en esta cámara. */
  modulo: boolean;
  personas: PersonaEnVivo[];
  /** false = esta cámara no tiene asignado el módulo de EPP. */
  moduloEpp: boolean;
  /**
   * De cuándo son los recuadros del EPP, en el reloj del worker.
   *
   * Va aparte de `ts` porque son dos modelos con dos ritmos: el de ingreso de
   * personas y el de EPP no analizan el mismo cuadro ni tardan lo mismo, y
   * adelantar los recuadros de uno con la antigüedad del otro los dejaría
   * corridos justo cuando la persona se mueve.
   */
  tsEpp: number;
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
  ts: 0, ahora: 0, modulo: false, personas: [],
  moduloEpp: false, tsEpp: 0, exigidos: [], epp: [], eppPersonas: [], sinAlertarEpp: [],
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

  /**
   * `nombrar` es el seguimiento de una persona sin identificar a la que se le
   * quiere poner un nombre: sólo para ésa viene el recorte de su cara.
   */
  enVivo(cameraId: string, nombrar?: number | null): Observable<VistaEnVivo> {
    const qs = nombrar === null || nombrar === undefined ? '' : `?nombrar=${nombrar}`;
    return this.http.get<VistaEnVivo>(`/ai/live/${cameraId}${qs}`).pipe(
      map((r) => ({
        ts: r?.ts ?? 0,
        ahora: r?.ahora ?? 0,
        modulo: !!r?.modulo,
        personas: r?.personas ?? [],
        moduloEpp: !!r?.moduloEpp,
        tsEpp: r?.tsEpp ?? 0,
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
