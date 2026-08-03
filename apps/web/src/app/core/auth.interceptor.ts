import { inject } from '@angular/core';
import { HttpErrorResponse, type HttpInterceptorFn } from '@angular/common/http';
import { from, switchMap, throwError } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { AuthService } from './auth.service';

/** Rutas que NO llevan token (el propio login, y el video que sirve media-service). */
const SKIP = ['/identity/api/v1/auth/', '/media/', '/ai/'];

/**
 * Adjunta el token a cada petición y renueva la sesión sola.
 *
 * Antes de cada llamada se asegura de tener un token vigente; si el servidor
 * igual responde 401 (por ejemplo, el token se invalidó del otro lado), vuelve
 * a autenticar y reintenta UNA vez. Así el token deja de ser algo que haya que
 * pegar a mano.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  if (SKIP.some((p) => req.url.startsWith(p))) return next(req);

  const auth = inject(AuthService);

  const withToken = (): ReturnType<typeof next> => {
    const token = auth.token;
    return next(token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req);
  };

  return from(auth.ensureSession()).pipe(
    switchMap(() => withToken()),
    catchError((err: HttpErrorResponse) => {
      if (err.status !== 401) return throwError(() => err);
      // Token rechazado: reautenticar y reintentar una sola vez.
      auth.logout();
      return from(auth.ensureSession()).pipe(switchMap(() => withToken()));
    }),
  );
};
