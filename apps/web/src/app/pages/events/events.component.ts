import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { ModuleIconComponent } from '../../shared/module-icon.component';
import { EventsService, type EvidenceItem, type TrainingStats } from '../../core/events.service';
import { AI_MODULES } from '../../core/catalog';
import {
  SEVERITY_CLASS,
  SEVERITY_LABEL,
  STATUS_LABEL,
  type EventItem,
  type EventStatus,
} from '../../core/models';

type FilterKey = EventStatus | 'all';

const STATUS_FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'all', label: 'Todos' },
  { key: 'new', label: 'Nuevos' },
  { key: 'acknowledged', label: 'Reconocidos' },
  { key: 'confirmed', label: 'Confirmados' },
  { key: 'false_positive', label: 'Falsos positivos' },
];

@Component({
  selector: 'px-events',
  standalone: true,
  imports: [CommonModule, FormsModule, PageHeaderComponent, ModuleIconComponent],
  templateUrl: './events.component.html',
  styleUrls: ['./events.component.scss'],
})
export class EventsComponent implements OnInit, OnDestroy {
  private readonly api = inject(EventsService);

  readonly filters = STATUS_FILTERS;
  readonly sevLabel = SEVERITY_LABEL;
  readonly sevClass = SEVERITY_CLASS;
  readonly statusLabel = STATUS_LABEL;

  events: EventItem[] = [];
  loading = true;
  demoMode = false;
  activeFilter: FilterKey = 'all';
  selected: EventItem | null = null;
  busy: string | null = null;

  // ── notificación de caídas nuevas ──────────────────────────────────
  /** Caídas sin revisar que aparecieron desde la última carga. */
  fallAlert: EventItem | null = null;
  private vistos = new Set<string>();
  private primeraCarga = true;

  // ── confirmación con nombre ────────────────────────────────────────
  confirmando: EventItem | null = null;
  tituloEvidencia = '';
  notaRevision = '';

  evidencias: EvidenceItem[] = [];
  cargandoEvidencias = false;
  stats: TrainingStats | null = null;

  private timer?: ReturnType<typeof setInterval>;

  ngOnInit(): void {
    this.load();
    this.refreshStats();
    // Sondeo: mientras no haya WebSocket, así llegan las alertas nuevas.
    this.timer = setInterval(() => this.load(true), 5000);
  }

  ngOnDestroy(): void {
    if (this.timer) clearInterval(this.timer);
  }

  refreshStats(): void {
    this.api.trainingStats().subscribe((s) => (this.stats = s));
  }

  /** Abre el diálogo para confirmar una caída real y ponerle nombre. */
  abrirConfirmacion(e: EventItem): void {
    this.confirmando = e;
    const hora = e.occurredAt;
    this.tituloEvidencia = `Caída ${e.siteName} ${hora}`.trim();
    this.notaRevision = '';
  }

  cerrarConfirmacion(): void {
    this.confirmando = null;
  }

  /** Confirma la caída: guarda el clip con nombre y etiqueta la muestra. */
  confirmarCaida(): void {
    const e = this.confirmando;
    if (!e) return;
    const titulo = this.tituloEvidencia.trim();
    if (!titulo) return;

    this.busy = e.id;
    this.confirmando = null;
    this.api.resolve(e.id, 'confirmed', this.notaRevision.trim() || undefined, titulo).subscribe(() => {
      this.busy = null;
      this.load();
      this.refreshStats();
    });
  }

  descartarAlerta(): void {
    this.fallAlert = null;
  }

  irAlEvento(e: EventItem): void {
    this.fallAlert = null;
    this.select(e);
  }

  esCaida(e: EventItem): boolean {
    return e.moduleKey === 'fall-detection' || e.eventType === 'person.fall';
  }

  load(silencioso = false): void {
    if (!silencioso) this.loading = true;
    const f = this.activeFilter;
    this.api.list(f === 'all' ? undefined : f).subscribe((res) => {
      // Detectar caídas NUEVAS sin revisar para avisar al operador. En la
      // primera carga no se avisa: serían alertas viejas, no novedades.
      if (!this.primeraCarga) {
        const nueva = res.items.find(
          (e) => e.status === 'new' && this.esCaida(e) && !this.vistos.has(e.id),
        );
        if (nueva) this.fallAlert = nueva;
      }
      res.items.forEach((e) => this.vistos.add(e.id));
      this.primeraCarga = false;

      this.events = res.items;
      this.demoMode = res.demo;
      this.loading = false;
      if (this.selected) {
        const actualizado = res.items.find((e) => e.id === this.selected!.id);
        this.selected = actualizado ?? null;
      }
    });
  }

  setFilter(key: FilterKey): void {
    this.activeFilter = key;
    this.load();
  }

  select(e: EventItem): void {
    this.selected = e;
    this.evidencias = [];
    // Se pide el clip SIEMPRE, no sólo si el evento ya está confirmado. El clip
    // se graba al sonar la alerta justamente para poder mirarlo antes de
    // decidir: no se le puede pedir a nadie que dictamine si hubo una caída sin
    // dejarlo ver lo que pasó.
    this.cargandoEvidencias = true;
    this.api.evidences(e.id).subscribe({
      next: (items) => {
        // Sólo interesa mientras siga siendo el evento seleccionado: si el
        // operador ya pasó a otro, esta respuesta llega tarde.
        if (this.selected?.id !== e.id) return;
        this.evidencias = items;
        this.cargandoEvidencias = false;
      },
      error: () => {
        if (this.selected?.id !== e.id) return;
        this.cargandoEvidencias = false;
      },
    });
  }

  /** El clip todavía no fue confirmado: se conserva sólo si se confirma. */
  esProvisional(ev: EvidenceItem): boolean {
    return ev.status === 'pending';
  }

  urlEvidencia(ev: EvidenceItem): string {
    return this.api.evidenceUrl(this.selected?.cameraId ?? '', ev.storageKey);
  }

  tamano(bytes: number): string {
    return bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
  }

  /** Human-in-the-loop: el operador toma la alerta. */
  acknowledge(e: EventItem): void {
    if (this.demoMode) return;
    this.busy = e.id;
    this.api.acknowledge(e.id).subscribe(() => {
      this.busy = null;
      this.load();
    });
  }

  /** Human-in-the-loop: el operador resuelve la alerta revisada. */
  resolve(e: EventItem, resolution: 'confirmed' | 'dismissed' | 'false_positive'): void {
    if (this.demoMode) return;
    this.busy = e.id;
    this.api.resolve(e.id, resolution).subscribe(() => {
      this.busy = null;
      this.load();
    });
  }

  moduleIcon(key: string): { icon: string; color: string } {
    const m = AI_MODULES.find((x) => x.moduleKey === key);
    return { icon: m?.icon ?? 'zone', color: m?.color ?? '#3b82f6' };
  }

  confidencePct(c: number): number {
    return Math.round(c * 100);
  }

  trackEvent(_: number, e: EventItem): string {
    return e.id;
  }
}
