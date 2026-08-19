import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable, of, tap } from 'rxjs';
import { catchError, map } from 'rxjs/operators';

interface LoginResponse {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
  user: { id: string; email: string; fullName: string | null; organizationId: string; permissions: string[] };
}

export interface SessionUser {
  id: string;
  email: string;
  fullName: string | null;
  permissions: string[];
}

const TOKEN_KEY = 'px_token';
const REFRESH_KEY = 'px_refresh';
const USER_KEY = 'px_user';

/**
 * Sesión del dashboard.
 *
 * La autenticación es real: `identity-service` valida la contraseña con bcrypt
 * y arma el token con los permisos que el usuario tiene EN LA BASE.
 *
 * Ya no inicia sesión sola: hay pantalla de login y entrar es una decisión de
 * una persona. Un auto-login con credenciales fijas convertía cualquier
 * navegador que abriera la aplicación en un administrador, y con eso la
 * pantalla de login sería decoración.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly api = '/identity/api/v1';

  get token(): string | null {
    return localStorage.getItem(TOKEN_KEY);
  }

  get user(): SessionUser | null {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as SessionUser) : null;
  }

  /** ¿Hay token y le queda vida? */
  hasValidToken(): boolean {
    const t = this.token;
    if (!t) return false;
    try {
      const payload = JSON.parse(atob(t.split('.')[1])) as { exp?: number };
      // Un minuto de margen: evita usar un token que vence en el camino.
      return !!payload.exp && payload.exp * 1000 > Date.now() + 60_000;
    } catch {
      return false;
    }
  }

  /**
   * Garantiza una sesión utilizable. Si el token falta o venció, inicia sesión.
   * Las llamadas concurrentes comparten el mismo intento (no repite el login).
   */
  ensureSession(): Promise<boolean> {
    return Promise.resolve(this.hasValidToken());
  }

  login(email: string, password: string): Observable<boolean> {
    return this.http.post<LoginResponse>(`${this.api}/auth/login`, { email, password }).pipe(
      tap((r) => this.store(r)),
      map(() => true),
      catchError(() => of(false)),
    );
  }

  /**
   * Sesión de la pantalla de bienvenida.
   *
   * No pide credenciales porque nadie inicia sesión en un kiosco. El token que
   * devuelve tiene un solo permiso —mandar una foto y recibir un saludo— así
   * que dejarlo en ese dispositivo no expone nada más.
   */
  entrarComoKiosco(): Observable<{ ok: boolean; motivo?: string }> {
    return this.http.post<LoginResponse>(`${this.api}/auth/kiosk`, {}).pipe(
      tap((r) => this.store(r)),
      map(() => ({ ok: true })),
      // El motivo viaja hasta la pantalla en vez de perderse. El caso normal
      // no es "falló": es que el módulo de ingreso de personas no está
      // asignado a ninguna cámara, y eso el servidor lo dice con todas las
      // letras. Tragárselo dejaría al usuario probando el botón sin saber que
      // le falta asignar un módulo.
      catchError((err: HttpErrorResponse) =>
        of({ ok: false, motivo: err?.error?.message as string | undefined }),
      ),
    );
  }

  /** True si la sesión actual es la de la pantalla, no la de una persona. */
  esKiosco(): boolean {
    return this.user?.permissions?.every((p) => p === 'kiosk:identify') ?? false;
  }

  logout(): void {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
    localStorage.removeItem(USER_KEY);
  }

  private store(r: LoginResponse): void {
    localStorage.setItem(TOKEN_KEY, r.accessToken);
    localStorage.setItem(REFRESH_KEY, r.refreshToken);
    localStorage.setItem(
      USER_KEY,
      JSON.stringify({
        id: r.user.id,
        email: r.user.email,
        fullName: r.user.fullName,
        permissions: r.user.permissions,
      } satisfies SessionUser),
    );
  }
}
