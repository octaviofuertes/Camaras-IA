import { inject } from '@angular/core';
import { Router, type CanActivateFn } from '@angular/router';
import { map } from 'rxjs';
import { AuthService } from './auth.service';
import { MODULO_INGRESO, ModulosService } from './modulos.service';

/**
 * Sin sesión no se entra: manda al login.
 *
 * Antes la aplicación iniciaba sesión sola con credenciales fijas, así que
 * cualquiera que abriera la URL era administrador. Este guard es lo que hace
 * que la pantalla de login signifique algo.
 */
export const sesionRequerida: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  return auth.hasValidToken() ? true : router.createUrlTree(['/login']);
};

/**
 * El panel de administración no se abre con la sesión del kiosco.
 *
 * Ese token tiene un solo permiso y no serviría para nada acá, pero además
 * dejarlo pasar mostraría un panel vacío lleno de errores en vez de decir lo
 * que pasa.
 */
export const soloPersonas: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  if (!auth.hasValidToken()) return router.createUrlTree(['/login']);
  return auth.esKiosco() ? router.createUrlTree(['/bienvenida']) : true;
};

/**
 * Las pantallas del módulo de ingreso de personas sólo existen donde el módulo
 * esté asignado a una cámara.
 *
 * Esconder el ítem del menú no alcanza: la URL se escribe a mano y queda
 * guardada en los favoritos de cualquiera que la usó cuando sí estaba. Sin
 * esto, la pantalla se abriría y se llenaría de errores 409 sin explicar de
 * dónde salen.
 *
 * Manda a Cámaras y no al Dashboard porque Cámaras es donde se arregla: el que
 * entró buscando esta función necesita saber que le falta asignar el módulo,
 * no aterrizar en otro lado sin explicación.
 */
export const moduloIngresoAsignado: CanActivateFn = () => {
  const modulos = inject(ModulosService);
  const router = inject(Router);
  return modulos
    .disponible(MODULO_INGRESO)
    .pipe(map((hay) => (hay ? true : router.createUrlTree(['/camaras'], {
      queryParams: { falta: MODULO_INGRESO },
    }))));
};
