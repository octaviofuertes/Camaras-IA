import { inject } from '@angular/core';
import { Router, type CanActivateFn } from '@angular/router';
import { AuthService } from './auth.service';

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
