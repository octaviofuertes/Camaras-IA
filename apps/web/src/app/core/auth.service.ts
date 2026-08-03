import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, firstValueFrom, of, tap } from 'rxjs';
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
 * Mientras no exista pantalla de login, `ensureSession()` inicia sesión sola
 * con las credenciales de desarrollo. No es una puerta trasera: pasa por el
 * mismo endpoint y las mismas validaciones que usaría una persona.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly api = '/identity/api/v1';

  private pending: Promise<boolean> | null = null;

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
    if (this.hasValidToken()) return Promise.resolve(true);
    if (this.pending) return this.pending;

    this.pending = firstValueFrom(this.autoLogin()).finally(() => (this.pending = null));
    return this.pending;
  }

  login(email: string, password: string): Observable<boolean> {
    return this.http.post<LoginResponse>(`${this.api}/auth/login`, { email, password }).pipe(
      tap((r) => this.store(r)),
      map(() => true),
      catchError(() => of(false)),
    );
  }

  private autoLogin(): Observable<boolean> {
    const email = 'admin@percepta.local';
    const password = 'percepta';
    return this.login(email, password);
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
