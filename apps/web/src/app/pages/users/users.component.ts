import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { PageHeaderComponent } from '../../shared/page-header.component';

interface DemoUser {
  name: string;
  email: string;
  role: string;
  scope: string;
  status: 'active' | 'invited';
}

/**
 * Usuarios y roles. Los roles y permisos mostrados son los del catálogo
 * canónico (CONTRACTS §9); la administración real la servirá
 * `identity-service`.
 */
@Component({
  selector: 'px-users',
  standalone: true,
  imports: [CommonModule, PageHeaderComponent],
  template: `
    <px-page-header title="Usuarios" subtitle="Usuarios, roles y permisos" />

    <div class="page">
      <div class="notice">
        <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8v.01" />
        </svg>
        <span>
          Vista preliminar. La administración de usuarios la servirá
          <code>identity-service</code>; los roles listados son los del catálogo canónico.
        </span>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h2 class="panel-title">Usuarios de la organización</h2>
          <button class="btn btn-primary">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 5v14M5 12h14" />
            </svg>
            Invitar usuario
          </button>
        </div>
        <table class="tbl">
          <thead>
            <tr><th>Usuario</th><th>Rol</th><th>Alcance</th><th>Estado</th></tr>
          </thead>
          <tbody>
            <tr *ngFor="let u of users">
              <td>
                <div class="u">
                  <span class="avatar">{{ u.name.charAt(0) }}</span>
                  <div>
                    <div class="u-name">{{ u.name }}</div>
                    <div class="mute u-mail">{{ u.email }}</div>
                  </div>
                </div>
              </td>
              <td><span class="badge badge-cat">{{ u.role }}</span></td>
              <td class="muted">{{ u.scope }}</td>
              <td>
                <span class="status" [attr.data-s]="u.status">
                  {{ u.status === 'active' ? 'Activo' : 'Invitado' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  `,
  styles: [
    `
      .page { padding: 20px 26px 32px; display: flex; flex-direction: column; gap: 16px; }
      .notice {
        display: flex; align-items: flex-start; gap: 11px;
        padding: 13px 16px; border-radius: var(--radius-sm);
        background: rgba(59, 130, 246, 0.09);
        border: 1px solid rgba(59, 130, 246, 0.28);
        color: #bfdbfe; font-size: 12.5px; line-height: 1.55;
      }
      .notice svg { flex-shrink: 0; margin-top: 1px; }
      code { background: rgba(255, 255, 255, 0.08); padding: 1px 5px; border-radius: 4px; font-size: 11.5px; }
      .tbl { width: 100%; border-collapse: collapse; }
      .tbl th {
        text-align: left; font-size: 11px; text-transform: uppercase;
        letter-spacing: 0.5px; color: var(--text-mute); font-weight: 600;
        padding: 11px 18px; border-bottom: 1px solid var(--border);
      }
      .tbl td { padding: 13px 18px; border-bottom: 1px solid var(--border); font-size: 13px; }
      .u { display: flex; align-items: center; gap: 11px; }
      .avatar {
        width: 34px; height: 34px; border-radius: 50%;
        background: linear-gradient(135deg, #475569, #334155);
        display: grid; place-items: center; font-weight: 600; font-size: 13px; color: #fff;
      }
      .u-name { font-weight: 600; }
      .u-mail { font-size: 11.5px; }
      .status {
        font-size: 11.5px; font-weight: 600; padding: 3px 9px; border-radius: 999px;
        background: rgba(34, 197, 94, 0.14); color: #86efac;
      }
      .status[data-s='invited'] { background: rgba(234, 179, 8, 0.16); color: #fcd34d; }
    `,
  ],
})
export class UsersComponent {
  readonly users: DemoUser[] = [
    { name: 'Admin', email: 'admin@empresa.com', role: 'org_admin', scope: 'Toda la organización', status: 'active' },
    { name: 'Lucía Fernández', email: 'lucia@empresa.com', role: 'site_admin', scope: 'Planta Mendoza', status: 'active' },
    { name: 'Marco Díaz', email: 'marco@empresa.com', role: 'operator', scope: 'Planta Mendoza', status: 'active' },
    { name: 'Sofía Rossi', email: 'sofia@empresa.com', role: 'operator', scope: 'Sede Central', status: 'active' },
    { name: 'Auditoría Externa', email: 'audit@consultora.com', role: 'auditor', scope: 'Toda la organización', status: 'invited' },
  ];
}
