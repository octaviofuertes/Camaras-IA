import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { BehaviorSubject, Observable, catchError, map, of, tap } from 'rxjs';

/**
 * Clave del módulo de ingreso de personas.
 *
 * Tiene que coincidir con `MODULO_INGRESO_DE_PERSONAS` de packages/contracts,
 * con el `moduleKey` de modules/person-entry/module.json y con
 * `ai_modules.module_key` en la base. El frontend no puede importar el paquete
 * de contratos (no está entre sus dependencias), así que la copia se anota acá
 * con su origen a la vista.
 */
export const MODULO_INGRESO = 'person-entry';

/**
 * Qué se sabe sobre un módulo.
 *
 * Los tres estados son distintos y confundirlos es el error clásico: `null`
 * no es `false`. Si el servicio de dispositivos no contesta, la respuesta no es
 * "no lo tenés contratado" —eso haría desaparecer medio menú por un servicio
 * caído— sino "no pude preguntar", y ante eso se muestra la función.
 */
export type EstadoModulo = boolean | null;

/**
 * Qué módulos está usando esta organización.
 *
 * Un módulo en el catálogo es algo que se puede contratar; un módulo asignado a
 * una cámara es algo que está andando. Las funciones de producto se cuelgan de
 * lo segundo: el menú, las rutas y los endpoints tienen que coincidir en qué
 * existe, y para eso tienen que mirar todos el mismo dato.
 *
 * El estado se comparte —y no lo consulta cada pantalla por su cuenta— porque
 * si el menú y el guard preguntan por separado se contradicen: el ítem visible
 * lleva a una ruta que rebota.
 */
@Injectable({ providedIn: 'root' })
export class ModulosService {
  private readonly http = inject(HttpClient);

  private readonly estado$ = new BehaviorSubject<Record<string, EstadoModulo>>({});

  /** Lo último que se supo, sin ir a la red. */
  asignado(moduleKey: string): EstadoModulo {
    return this.estado$.value[moduleKey] ?? null;
  }

  /** Para que el menú se dibuje solo cuando cambia una asignación. */
  cambios(): Observable<Record<string, EstadoModulo>> {
    return this.estado$.asObservable();
  }

  /**
   * Vuelve a preguntar qué módulos están asignados.
   *
   * Se apoya en el endpoint que ya existe para la pantalla de Cámaras en vez
   * de estrenar uno: la pregunta "¿qué módulo está asignado a qué cámara?" ya
   * tiene una respuesta canónica y sumar otra sería tener dos verdades.
   */
  refrescar(): Observable<Record<string, EstadoModulo>> {
    return this.http
      .get<{ items: { moduleKey: string; enabled: boolean }[] }>(
        '/device/api/v1/camera-module-configs',
      )
      .pipe(
        map((r) => {
          const mapa: Record<string, EstadoModulo> = {};
          for (const a of r.items ?? []) {
            // Basta una cámara con el módulo prendido. Si una lo tiene apagado
            // y otra prendido, la función existe.
            mapa[a.moduleKey] = mapa[a.moduleKey] === true || a.enabled;
          }
          // Lo que no vino es que no está asignado, no que no se sabe: la
          // respuesta llegó entera.
          if (!(MODULO_INGRESO in mapa)) mapa[MODULO_INGRESO] = false;
          return mapa;
        }),
        // No se pudo preguntar. Se deja todo en "no sé" para que las funciones
        // sigan a la vista: es preferible una pantalla que después explique un
        // error a un menú que se achica solo y parece que perdió cosas.
        catchError(() => of({} as Record<string, EstadoModulo>)),
        tap((mapa) => this.estado$.next(mapa)),
      );
  }

  /**
   * ¿Se puede usar la función de este módulo?
   *
   * Ante la duda, sí. La puerta que de verdad cierra está en el backend, que
   * contesta 409; esto sólo decide qué mostrar, y esconder una función porque
   * un servicio no respondió sería mentir sobre lo que el cliente tiene.
   */
  disponible(moduleKey: string): Observable<boolean> {
    const sabido = this.asignado(moduleKey);
    if (sabido !== null) return of(sabido);
    return this.refrescar().pipe(map((mapa) => mapa[moduleKey] !== false));
  }

  /** Se olvida lo sabido: la próxima pregunta va a la red. */
  olvidar(): void {
    this.estado$.next({});
  }
}
