import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { switchMap } from 'rxjs';
import { FormsModule } from '@angular/forms';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { ModuleIconComponent } from '../../shared/module-icon.component';
import {
  EventsService,
  type EvidenceItem,
  type Reconocimiento,
  type TrainingStats,
} from '../../core/events.service';
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

  // ── reconocer a la persona de la foto (a pedido) ───────────────────
  reconocimiento: Reconocimiento | null = null;
  reconociendo = false;
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

  /** Alguien a quien se le negó el acceso está adentro. */
  esAccesoDenegado(e: EventItem): boolean {
    return e.eventType === 'access.denied';
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

      // La pregunta "¿reconocés a esta persona?" NO va acá. Esta pantalla es
      // la cola de lo que hay que atender —una caída, alguien sin acceso— y
      // ponerle nombre a una cara no es eso: es completar una ficha, y tiene su
      // propia pantalla. Mezcladas, lo urgente quedaba enterrado entre trámites.
      this.events = res.items.filter((e) => e.eventType !== 'person.unknown');
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
    // El resultado del reconocimiento pertenece al evento que se estaba
    // mirando: dejarlo puesto mostraría el nombre de una persona sobre la foto
    // de otra, que es el peor error que puede cometer esta pantalla.
    this.reconocimiento = null;
    this.reconociendo = false;
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

  /** Esta evidencia es una foto (EPP, cara desconocida) y no un video. */
  esFoto(ev: EvidenceItem): boolean {
    return ev.kind === 'image';
  }

  /** La foto del evento, si tiene. Es sobre la que corre el reconocimiento. */
  get fotoDelEvento(): EvidenceItem | null {
    return this.evidencias.find((ev) => ev.kind === 'image') ?? null;
  }

  /**
   * Pregunta quién es la persona de la foto. Recién ahora corre el modelo.
   *
   * Nada de esto pasó cuando sonó la alerta: reconocer caras en cada detección
   * de EPP sería gastar el modelo de rostros todo el día para que nadie mire el
   * resultado, y además dejaría escrito quién anduvo sin casco sin que nadie lo
   * haya preguntado. Acá corre una vez, porque alguien quiso saber.
   */
  reconocerPersona(): void {
    const foto = this.fotoDelEvento;
    const evento = this.selected;
    if (!foto || !evento || this.reconociendo) return;

    this.reconociendo = true;
    this.reconocimiento = null;
    this.api
      .fotoEvidencia(this.urlEvidencia(foto))
      .pipe(switchMap((b64) => this.api.reconocerPersona(b64)))
      .subscribe({
        next: (r) => {
          // Si el operador ya pasó a otra alerta, esta respuesta llega tarde y
          // no corresponde mostrarla sobre otra foto.
          if (this.selected?.id !== evento.id) return;
          this.reconocimiento = r;
          this.reconociendo = false;
        },
        error: () => {
          if (this.selected?.id !== evento.id) return;
          this.reconocimiento = {
            reconocido: null,
            caras: 0,
            motivo: 'No se pudo leer la foto de la evidencia para analizarla.',
          };
          this.reconociendo = false;
        },
      });
  }

  parecidoPct(p: number): number {
    return Math.round(p * 100);
  }

  /** Cuánto cubre el clip, tomado de lo que se grabó y no de un texto fijo. */
  ventanaClip(ev: EvidenceItem): string {
    if (ev.kind === 'image') return 'foto del momento de la alerta';
    const antes = ev.preRollMs ? Math.round(ev.preRollMs / 1000) : null;
    const despues = ev.postRollMs ? Math.round(ev.postRollMs / 1000) : null;
    if (antes === null || despues === null) {
      return ev.durationMs ? `${Math.round(ev.durationMs / 1000)} s` : 'clip';
    }
    return `${antes} s antes y ${despues} s después`;
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
