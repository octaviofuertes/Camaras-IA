import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

/**
 * Cabecera de página: título + subtítulo y los filtros globales.
 *
 * Los selectores de empresa y sucursal son la materialización visual de la
 * multitenancy: la empresa sale del token (no se elige libremente salvo para
 * un superadmin de plataforma) y la sucursal acota el scope de lo que se ve.
 */
@Component({
  selector: 'px-page-header',
  standalone: true,
  imports: [CommonModule],
  template: `
    <header class="head">
      <div>
        <h1>{{ title }}</h1>
        <p class="sub">{{ subtitle }}</p>
      </div>
      <div class="filters">
        <ng-content></ng-content>
        <button class="select">
          <span>Todas las empresas</span>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2">
            <path d="m6 9 6 6 6-6" />
          </svg>
        </button>
        <button class="select">
          <span>Todas las sucursales</span>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2">
            <path d="m6 9 6 6 6-6" />
          </svg>
        </button>
        <button class="select" *ngIf="showDateRange && dateRange">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="3" y="5" width="18" height="16" rx="2" /><path d="M3 10h18M8 3v4M16 3v4" />
          </svg>
          <span>{{ dateRange }}</span>
        </button>
      </div>
    </header>
  `,
  styles: [
    `
      .head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 20px;
        padding: 22px 26px;
        border-bottom: 1px solid var(--border);
        flex-wrap: wrap;
      }
      h1 {
        margin: 0;
        font-size: 23px;
        font-weight: 700;
        letter-spacing: -0.3px;
      }
      .sub {
        margin: 3px 0 0;
        font-size: 13px;
        color: var(--text-dim);
      }
      .filters {
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
      }
      .select {
        display: inline-flex;
        align-items: center;
        gap: 9px;
        background: var(--panel);
        border: 1px solid var(--border);
        color: var(--text-dim);
        border-radius: var(--radius-sm);
        padding: 10px 14px;
        font-size: 13px;
        cursor: pointer;
        font-family: inherit;
        white-space: nowrap;
      }
      .select:hover {
        background: var(--panel-2);
        color: var(--text);
      }
    `,
  ],
})
export class PageHeaderComponent {
  @Input() title = '';
  @Input() subtitle = '';
  @Input() showDateRange = false;
  /**
   * Qué período se está mostrando. Lo pasa cada pantalla.
   *
   * Sin valor por omisión: había uno fijo —"01/05/2024 - 01/05/2024"— que se
   * leía como el rango de lo que había en pantalla y no lo era. Un filtro que
   * dice una fecha que no es la de los datos es peor que no mostrar el filtro.
   */
  @Input() dateRange = '';
}
