import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { PageHeaderComponent } from '../../shared/page-header.component';
import {
  ReportsService,
  type Informe,
  type PuntoSerie,
  type ResumenPuesto,
} from '../../core/reports.service';

type Rango = 'hoy' | 'ayer' | 'semana' | 'mes';

interface BarraHora {
  etiqueta: string;
  ocupadoPct: number;
  telefonoPct: number;
  sinDatos: boolean;
}

@Component({
  selector: 'px-reports',
  standalone: true,
  imports: [CommonModule, FormsModule, PageHeaderComponent],
  templateUrl: './reports.component.html',
  styleUrls: ['./reports.component.scss'],
})
export class ReportsComponent implements OnInit {
  private readonly api = inject(ReportsService);

  informe: Informe | null = null;
  cargando = true;
  rango: Rango = 'hoy';

  readonly rangos: { key: Rango; label: string }[] = [
    { key: 'hoy', label: 'Hoy' },
    { key: 'ayer', label: 'Ayer' },
    { key: 'semana', label: 'Últimos 7 días' },
    { key: 'mes', label: 'Últimos 30 días' },
  ];

  ngOnInit(): void {
    this.cargar();
  }

  setRango(r: Rango): void {
    this.rango = r;
    this.cargar();
  }

  cargar(): void {
    this.cargando = true;
    const { desde, hasta, bucket } = this.ventana();
    this.api.actividad({ desde, hasta, bucket }).subscribe((i) => {
      this.informe = i;
      this.cargando = false;
    });
  }

  private ventana(): { desde: string; hasta: string; bucket: 'hour' | 'day' } {
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
        return {
          desde: inicioDe(ayer).toISOString(),
          hasta: inicioDe(ahora).toISOString(),
          bucket: 'hour',
        };
      }
      case 'semana': {
        const desde = new Date(ahora);
        desde.setDate(desde.getDate() - 7);
        return { desde: inicioDe(desde).toISOString(), hasta: ahora.toISOString(), bucket: 'day' };
      }
      case 'mes': {
        const desde = new Date(ahora);
        desde.setDate(desde.getDate() - 30);
        return { desde: inicioDe(desde).toISOString(), hasta: ahora.toISOString(), bucket: 'day' };
      }
      default:
        return { desde: inicioDe(ahora).toISOString(), hasta: ahora.toISOString(), bucket: 'hour' };
    }
  }

  // ── presentación ───────────────────────────────────────────────────
  /** Duración legible. Los informes se leen, no se calculan mentalmente. */
  duracion(segundos: number): string {
    const s = Math.max(Math.round(segundos), 0);
    if (s < 60) return `${s} s`;
    const h = Math.floor(s / 3600);
    const m = Math.round((s % 3600) / 60);
    if (h === 0) return `${m} min`;
    return m === 0 ? `${h} h` : `${h} h ${m} min`;
  }

  /**
   * Desglose por franja horaria del puesto que más se usó.
   *
   * Se muestra uno solo y se dice cuál: superponer varios puestos en un gráfico
   * chico se lee mal y termina sin comunicar nada.
   */
  barras(): BarraHora[] {
    const inf = this.informe;
    if (!inf || !inf.serie.length) return [];

    const principal = inf.puestos[0];
    if (!principal) return [];

    const suyos = inf.serie.filter(
      (p) => p.cameraId === principal.cameraId && (p.zoneId ?? '') === (principal.zoneId ?? ''),
    );
    return suyos.map((p) => this.aBarra(p));
  }

  private aBarra(p: PuntoSerie): BarraHora {
    const observado = p.occupiedSeconds + p.emptySeconds;
    const fecha = new Date(p.periodo);
    const etiqueta =
      this.rango === 'semana' || this.rango === 'mes'
        ? fecha.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' })
        : `${String(fecha.getHours()).padStart(2, '0')}h`;

    return {
      etiqueta,
      ocupadoPct: observado > 0 ? Math.round((p.occupiedSeconds / observado) * 100) : 0,
      telefonoPct: observado > 0 ? Math.round((p.phoneSeconds / observado) * 100) : 0,
      sinDatos: observado <= 0,
    };
  }

  nombrePuestoPrincipal(): string {
    return this.informe?.puestos[0]?.zoneName ?? '';
  }

  /** Una cobertura baja invalida la lectura del número de al lado. */
  coberturaFloja(p: ResumenPuesto): boolean {
    return p.coberturaPct < 80;
  }

  trackPuesto(_: number, p: ResumenPuesto): string {
    return `${p.cameraId}|${p.zoneId ?? ''}`;
  }

  trackBarra(i: number): number {
    return i;
  }
}
