import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { AuthService } from './core/auth.service';

@Component({
  selector: 'px-root',
  standalone: true,
  imports: [CommonModule, RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <!-- La pantalla de bienvenida va sola: quien está enfrente no opera nada y
         un menú lateral sólo invitaría a tocarlo. -->
    <router-outlet *ngIf="soloKiosco" />

    <div class="layout" *ngIf="!soloKiosco">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-mark">
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 2 4 6v6c0 5 3.4 9.4 8 10 4.6-.6 8-5 8-10V6l-8-4Z" />
              <circle cx="12" cy="11" r="2.5" fill="currentColor" stroke="none" />
            </svg>
          </div>
          <div class="brand-text">
            <div class="brand-name">VisionAI</div>
            <div class="brand-sub">Análisis Inteligente de Video</div>
          </div>
        </div>

        <nav class="nav">
          <a class="nav-item" routerLink="/dashboard" routerLinkActive="active">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="3" width="7" height="7" rx="1.5" />
              <rect x="14" y="3" width="7" height="7" rx="1.5" />
              <rect x="3" y="14" width="7" height="7" rx="1.5" />
              <rect x="14" y="14" width="7" height="7" rx="1.5" />
            </svg>
            <span>Dashboard</span>
          </a>
          <a class="nav-item" routerLink="/camaras" routerLinkActive="active">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="2" y="6" width="14" height="12" rx="2" />
              <path d="m16 11 6-3v8l-6-3z" />
            </svg>
            <span>Cámaras</span>
          </a>
          <a class="nav-item" routerLink="/eventos" routerLinkActive="active">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
              <path d="M13.7 21a2 2 0 0 1-3.4 0" />
            </svg>
            <span>Eventos</span>
          </a>
          <a class="nav-item" routerLink="/reconocimiento" routerLinkActive="active">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2" />
              <circle cx="12" cy="10" r="2.5" />
              <path d="M7.5 17a4.5 4.5 0 0 1 9 0" />
            </svg>
            <span>Reconocimiento</span>
          </a>
          <a class="nav-item" routerLink="/accesos" routerLinkActive="active">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M3 3v18h18" />
              <path d="M7 15l3.5-4 3 3L20 7" />
            </svg>
            <span>Accesos</span>
          </a>
          <a class="nav-item" routerLink="/usuarios" routerLinkActive="active">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
            </svg>
            <span>Usuarios</span>
          </a>
        </nav>

        <button class="user" (click)="salir()" title="Cerrar sesión">
          <div class="avatar">{{ inicial() }}</div>
          <div class="user-text">
            <div class="user-name">{{ nombreUsuario() }}</div>
            <div class="user-role">Cerrar sesión</div>
          </div>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
            <path d="m16 17 5-5-5-5M21 12H9" />
          </svg>
        </button>
      </aside>

      <main class="content">
        <router-outlet />
      </main>
    </div>
  `,
  styles: [
    `
      .layout {
        display: grid;
        grid-template-columns: 232px 1fr;
        height: 100vh;
        overflow: hidden;
      }
      .sidebar {
        background: var(--panel);
        border-right: 1px solid var(--border);
        display: flex;
        flex-direction: column;
        padding: 18px 14px;
      }
      .brand {
        display: flex;
        align-items: center;
        gap: 11px;
        padding: 4px 6px 22px;
      }
      .brand-mark {
        width: 36px;
        height: 36px;
        border-radius: 10px;
        background: linear-gradient(135deg, var(--accent), #1e40af);
        display: grid;
        place-items: center;
        color: #fff;
        flex-shrink: 0;
      }
      .brand-name {
        font-size: 16px;
        font-weight: 700;
        letter-spacing: -0.2px;
      }
      .brand-sub {
        font-size: 10.5px;
        color: var(--text-mute);
        margin-top: 1px;
      }
      .nav {
        display: flex;
        flex-direction: column;
        gap: 4px;
        flex: 1;
      }
      .nav-item {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 11px 13px;
        border-radius: var(--radius-sm);
        color: var(--text-dim);
        text-decoration: none;
        font-size: 14px;
        font-weight: 500;
        transition: background 0.15s, color 0.15s;
      }
      .nav-item:hover {
        background: var(--panel-2);
        color: var(--text);
      }
      .nav-item.active {
        background: var(--accent);
        color: #fff;
      }
      .user {
        width: 100%;
        border: 1px solid transparent;
        font: inherit;
        color: inherit;
        cursor: pointer;
        text-align: left;
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 11px;
        border-radius: var(--radius-sm);
        background: var(--panel-2);
        border: 1px solid var(--border);
        cursor: pointer;
        color: var(--text-dim);
      }
      .user:hover {
        background: var(--panel-hover);
      }
      .avatar {
        width: 34px;
        height: 34px;
        border-radius: 50%;
        background: linear-gradient(135deg, #475569, #334155);
        display: grid;
        place-items: center;
        font-weight: 600;
        color: #fff;
        font-size: 13px;
        flex-shrink: 0;
      }
      .user-text {
        flex: 1;
        min-width: 0;
      }
      .user-name {
        font-size: 13px;
        font-weight: 600;
        color: var(--text);
      }
      .user-role {
        font-size: 11px;
        color: var(--text-mute);
      }
      .content {
        overflow-y: auto;
      }
    `,
  ],
})
export class AppComponent {
  /** La ruta actual es la pantalla de kiosco: se dibuja sin ningún marco. */
  soloKiosco = false;

  private readonly auth = inject(AuthService);

  nombreUsuario(): string {
    const u = this.auth.user;
    return u?.fullName || u?.email?.split('@')[0] || 'Sesión';
  }

  inicial(): string {
    return (this.nombreUsuario()[0] ?? 'A').toUpperCase();
  }

  /** Cierra la sesión y vuelve al login. */
  salir(): void {
    this.auth.logout();
    void this.router.navigateByUrl('/login');
  }

  constructor(private readonly router: Router) {
    // La bienvenida y el login se dibujan solos: uno porque nadie lo opera, el
    // otro porque todavía no hay sesión que enmarcar.
    const sinMarco = (url: string): boolean =>
      url.startsWith('/bienvenida') || url.startsWith('/login');

    this.soloKiosco = sinMarco(this.router.url);
    this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe((e) => (this.soloKiosco = sinMarco(e.urlAfterRedirects)));
  }
}
