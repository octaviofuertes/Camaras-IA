import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { AuthService } from './core/auth.service';
import { MODULO_INGRESO, ModulosService } from './core/modulos.service';
import { LogoComponent } from './shared/logo.component';

@Component({
  selector: 'px-root',
  standalone: true,
  imports: [CommonModule, RouterOutlet, RouterLink, RouterLinkActive, LogoComponent],
  template: `
    <!-- La pantalla de bienvenida va sola: quien está enfrente no opera nada y
         un menú lateral sólo invitaría a tocarlo. -->
    <router-outlet *ngIf="soloKiosco" />

    <div class="layout" *ngIf="!soloKiosco">
      <aside class="sidebar">
        <div class="brand">
          <px-logo [alto]="24" />
          <div class="brand-sub">Análisis Inteligente de Video</div>
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
          <!-- Reconocimiento y Accesos son las dos pantallas del módulo
               "Ingreso de personas": aparecen sólo si hay una cámara con el
               módulo asignado. Si no se pudo averiguar, se muestran igual (ver
               ModulosService): esconderlas por un servicio caído sería peor. -->
          <a class="nav-item" *ngIf="hayIngresoDePersonas"
             routerLink="/reconocimiento" routerLinkActive="active">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2" />
              <circle cx="12" cy="10" r="2.5" />
              <path d="M7.5 17a4.5 4.5 0 0 1 9 0" />
            </svg>
            <span>Reconocimiento</span>
          </a>
          <a class="nav-item" *ngIf="hayIngresoDePersonas"
             routerLink="/accesos" routerLinkActive="active">
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
      /* La barra va en el azul de marca y el contenido en claro. Es el mismo
         reparto que hace el sitio de e-Sueldos: el azul enmarca, y lo que hay
         que leer va sobre blanco. */
      .sidebar {
        background: var(--brand);
        color: var(--on-brand);
        display: flex;
        flex-direction: column;
        padding: 20px 14px 16px;
      }
      .brand {
        padding: 4px 8px 24px;
        color: var(--on-brand);
      }
      .brand-sub {
        font-size: 10.5px;
        color: var(--on-brand-mute);
        margin-top: 7px;
        padding-left: 2px;
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
        border-radius: 999px;
        color: var(--on-brand-dim);
        text-decoration: none;
        font-size: 14px;
        font-weight: 600;
        transition: background 0.15s, color 0.15s;
      }
      .nav-item:hover {
        background: rgba(255, 255, 255, 0.12);
        color: var(--on-brand);
      }
      /* La sección abierta se marca en blanco pleno: sobre el azul es lo único
         que se distingue de pasarle el mouse por encima. */
      .nav-item.active {
        background: var(--on-brand);
        color: var(--brand);
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
        background: rgba(255, 255, 255, 0.1);
        border: 1px solid rgba(255, 255, 255, 0.16);
        cursor: pointer;
        color: var(--on-brand-dim);
      }
      .user:hover {
        background: rgba(255, 255, 255, 0.18);
      }
      .avatar {
        width: 34px;
        height: 34px;
        border-radius: 50%;
        background: var(--on-brand);
        display: grid;
        place-items: center;
        font-weight: 800;
        color: var(--brand);
        font-size: 13px;
        flex-shrink: 0;
      }
      .user-text {
        flex: 1;
        min-width: 0;
      }
      .user-name {
        font-size: 13px;
        font-weight: 700;
        color: var(--on-brand);
      }
      .user-role {
        font-size: 11px;
        color: var(--on-brand-mute);
      }
      .content {
        overflow-y: auto;
        background: var(--bg);
      }
    `,
  ],
})
export class AppComponent {
  /** La ruta actual es la pantalla de kiosco: se dibuja sin ningún marco. */
  soloKiosco = false;

  /**
   * ¿Hay alguna cámara con el módulo de ingreso de personas?
   *
   * Arranca en true a propósito: mientras no se sepa, la función se muestra.
   * Al revés el menú parpadearía —se dibuja completo, se achica— y quien lo
   * mire va a creer que perdió una sección.
   */
  hayIngresoDePersonas = true;

  private readonly auth = inject(AuthService);
  private readonly modulos = inject(ModulosService);

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
    // Lo que tenga esta organización asignado no vale para la próxima.
    this.modulos.olvidar();
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

    // El menú sigue al estado compartido, así no se contradice con el guard.
    this.modulos.cambios().subscribe((mapa) => {
      this.hayIngresoDePersonas = mapa[MODULO_INGRESO] !== false;
    });

    // Se pregunta al entrar al panel, no al arrancar la aplicación: en el
    // login y en el kiosco no hay sesión que pueda consultarlo.
    this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe((e) => {
        if (!sinMarco(e.urlAfterRedirects) && this.modulos.asignado(MODULO_INGRESO) === null) {
          this.modulos.refrescar().subscribe();
        }
      });
  }
}
