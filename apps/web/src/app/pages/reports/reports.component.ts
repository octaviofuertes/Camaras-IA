import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { PageHeaderComponent } from '../../shared/page-header.component';
import {
  ReportsService,
  type Paso,
  type Presente,
  type RegistroAccesos,
} from '../../core/reports.service';

type Rango = 'hoy' | 'ayer' | 'semana' | 'mes';

@Component({
  selector: 'px-reports',
  standalone: true,
  imports: [CommonModule, FormsModule, PageHeaderComponent],
  templateUrl: './reports.component.html',
  styleUrls: ['./reports.component.scss'],
})
export class ReportsComponent implements OnInit, OnDestroy {
  private readonly api = inject(ReportsService);

  registro: RegistroAccesos | null = null;
  /** Quién está en el cuadro ahora mismo. */
  presentes: Presente[] = [];
  private timer?: ReturnType<typeof setInterval>;
  cargando = true;
  rango: Rango = 'hoy';
  /** Mostrar sólo los pasos de gente sin acceso. */
  soloSinAcceso = false;

  readonly rangos: { key: Rango; label: string }[] = [
    { key: 'hoy', label: 'Hoy' },
    { key: 'ayer', label: 'Ayer' },
    { key: 'semana', label: 'Últimos 7 días' },
    { key: 'mes', label: 'Últimos 30 días' },
  ];

  ngOnInit(): void {
    this.cargar();
    this.refrescarVivo();
    // El registro del día cambia poco; quién está adentro, todo el tiempo.
    this.timer = setInterval(() => this.refrescarVivo(), 4000);
  }

  ngOnDestroy(): void {
    if (this.timer) clearInterval(this.timer);
  }

  refrescarVivo(): void {
    this.api.enVivo().subscribe((p) => (this.presentes = p));
  }

  /** Hace cuánto que está. */
  desdeHace(p: Presente): string {
    const min = (Date.now() - new Date(p.desde).getTime()) / 60000;
    if (min < 1) return 'recién';
    return `hace ${this.duracion(min)}`;
  }

  setRango(r: Rango): void {
    this.rango = r;
    this.cargar();
  }

  cargar(): void {
    this.cargando = true;
    const { desde, hasta } = this.ventana();
    this.api.accesos({ desde, hasta }).subscribe((r) => {
      this.registro = r;
      this.cargando = false;
    });
  }

  private ventana(): { desde: string; hasta: string } {
    const ahora = new Date();
    const inicioDe = (d: Date): Date => {
      const x = new Date(d);
      x.setHours(0, 0, 0, 0);
      return x;
    };

    switch (this.rango) {
      case 'ayer': {
        const ayer = new Date(ahora);
        ayer.setDate(ayer.getDate() - 1);
        return { desde: inicioDe(ayer).toISOString(), hasta: inicioDe(ahora).toISOString() };
      }
      case 'semana': {
        const desde = new Date(ahora);
        desde.setDate(desde.getDate() - 7);
        return { desde: inicioDe(desde).toISOString(), hasta: ahora.toISOString() };
      }
      case 'mes': {
        const desde = new Date(ahora);
        desde.setDate(desde.getDate() - 30);
        return { desde: inicioDe(desde).toISOString(), hasta: ahora.toISOString() };
      }
      default:
        return { desde: inicioDe(ahora).toISOString(), hasta: ahora.toISOString() };
    }
  }

  // ── presentación ───────────────────────────────────────────────────
  pasos(): Paso[] {
    const todos = this.registro?.pasos ?? [];
    return this.soloSinAcceso ? todos.filter((p) => !p.hadAccess) : todos;
  }

  hora(iso: string): string {
    const d = new Date(iso);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  /** El día sólo se muestra cuando el rango abarca más de uno. */
  dia(iso: string): string {
    if (this.rango === 'hoy' || this.rango === 'ayer') return '';
    return new Date(iso).toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' });
  }

  duracion(minutos: number): string {
    if (minutos < 1) return 'menos de 1 min';
    if (minutos < 60) return `${Math.round(minutos)} min`;
    const h = Math.floor(minutos / 60);
    const m = Math.round(minutos % 60);
    return m === 0 ? `${h} h` : `${h} h ${m} min`;
  }

  /**
   * Cómo se supo quién era.
   *
   * Se muestra en cada fila a propósito: no es lo mismo haberle visto la cara
   * que haber deducido que era él porque seguía en el mismo lugar. Quien lee
   * este registro para tomar una decisión sobre alguien tiene que ver la
   * diferencia sin buscarla.
   */
  comoSeSupo(p: Paso): string {
    if (p.seenByFace) return `Se le vio la cara (${Math.round(p.bestScore * 100)}%)`;
    return 'Deducido por continuidad';
  }

  trackPaso(_: number, p: Paso): string {
    return p.id;
  }
}
