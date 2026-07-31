import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

/** Ícono cuadrado de color de un módulo de IA (identidad visual del catálogo). */
@Component({
  selector: 'px-module-icon',
  standalone: true,
  imports: [CommonModule],
  template: `
    <span
      class="icon-box"
      [style.background]="color + '22'"
      [style.color]="color"
      [style.width.px]="size"
      [style.height.px]="size"
      [style.borderRadius.px]="size / 3.2"
    >
      <svg [attr.width]="size * 0.55" [attr.height]="size * 0.55" viewBox="0 0 24 24" fill="none"
           stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <ng-container [ngSwitch]="icon">
          <ng-container *ngSwitchCase="'run'">
            <circle cx="13" cy="4" r="1.8" /><path d="m9 20 2-5-2.5-2.5L7 16" /><path d="M11 15l-2.5-3 2-4 3 2 3 1" />
          </ng-container>
          <ng-container *ngSwitchCase="'fall'">
            <circle cx="6" cy="7" r="1.8" /><path d="m4 12 5-2 6 5h5" /><path d="m9 10-3 6" />
          </ng-container>
          <ng-container *ngSwitchCase="'helmet'">
            <path d="M3 15a9 9 0 0 1 18 0" /><rect x="2" y="15" width="20" height="4" rx="1.5" />
          </ng-container>
          <ng-container *ngSwitchCase="'people'">
            <circle cx="9" cy="8" r="3" /><path d="M3 20a6 6 0 0 1 12 0" /><path d="M17 20a5 5 0 0 0-3-4.6" /><circle cx="17" cy="8" r="2.4" />
          </ng-container>
          <ng-container *ngSwitchCase="'zone'">
            <rect x="3" y="3" width="18" height="18" rx="2" stroke-dasharray="4 3" /><circle cx="12" cy="12" r="3" />
          </ng-container>
          <ng-container *ngSwitchCase="'bag'">
            <path d="M6 8h12l-1 12H7L6 8Z" /><path d="M9 8V6a3 3 0 0 1 6 0v2" />
          </ng-container>
          <ng-container *ngSwitchCase="'fire'">
            <path d="M12 3s5 4.5 5 9a5 5 0 0 1-10 0c0-2 1-3.5 2-4.5 0 2 1 3 2 3 .5-3-1-5-1-7.5Z" />
          </ng-container>
          <ng-container *ngSwitchCase="'truck'">
            <rect x="1" y="7" width="12" height="9" rx="1.5" /><path d="M13 10h4l3 3v3h-7z" /><circle cx="6" cy="18" r="1.8" /><circle cx="17" cy="18" r="1.8" />
          </ng-container>
          <ng-container *ngSwitchCase="'box'">
            <path d="M3 8l9-4 9 4-9 4-9-4Z" /><path d="M3 8v8l9 4 9-4V8" /><path d="M12 12v8" />
          </ng-container>
          <ng-container *ngSwitchCase="'clock'">
            <circle cx="12" cy="12" r="9" /><path d="M12 7v5l3.5 2" />
          </ng-container>
          <ng-container *ngSwitchDefault>
            <circle cx="12" cy="12" r="9" />
          </ng-container>
        </ng-container>
      </svg>
    </span>
  `,
  styles: [
    `
      .icon-box {
        display: inline-grid;
        place-items: center;
        flex-shrink: 0;
      }
    `,
  ],
})
export class ModuleIconComponent {
  @Input() icon = 'zone';
  @Input() color = '#3b82f6';
  @Input() size = 38;
}
