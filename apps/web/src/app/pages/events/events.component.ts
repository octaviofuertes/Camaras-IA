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

/** Convierte el vector facial que viajó en base64 de vuelta a números. */
function desempaquetar(b64?: string): number[] | undefined {
  if (!b64) return undefined;
  try {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return Array.from(new Float32Array(bytes.buffer));
  } catch {
    return undefined;
  }
}

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

  // ── reconocimiento de personas ─────────────────────────────────────
  /** Alerta "¿reconocés a esta persona?" que se está respondiendo. */
  reconociendo: EventItem | null = null;
  nombrePersona = '';
  baseConsentimiento = '';
  guardandoPersona = false;

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

  esReconocimiento(e: EventItem): boolean {
    return e.eventType === 'person.unknown';
  }

  /** Abre el diálogo para ponerle nombre a la persona de la alerta. */
  abrirReconocimiento(e: EventItem): void {
    this.reconociendo = e;
    this.nombrePersona = '';
    this.baseConsentimiento = '';
  }

  cerrarReconocimiento(): void {
    this.reconociendo = null;
  }

  /**
   * "Sí, trabaja acá": da de alta a la persona con su consentimiento registrado.
   *
   * El vector facial de la alerta se convierte en su primera plantilla. A partir
   * de acá el sistema la reconoce y su tiempo aparece con nombre en Informes.
   */
  confirmarPersona(): void {
    const e = this.reconociendo;
    if (!e) return;
    const nombre = this.nombrePersona.trim();
    const base = this.baseConsentimiento.trim();
    if (nombre.length < 2 || base.length < 3) return;

    this.guardandoPersona = true;
    this.api
      .altaPersona({
        displayName: nombre,
        consentBasis: base,
        embedding: desempaquetar(e.faceEmbedding),
      })
      .subscribe((res) => {
        this.guardandoPersona = false;
        this.reconociendo = null;
        if (!res) return;
        // La alerta se resuelve como confirmada: la pregunta fue respondida.
        this.busy = e.id;
        this.api.resolve(e.id, 'confirmed', `Dado de alta como ${nombre}`).subscribe(() => {
          this.busy = null;
          this.load();
        });
      });
  }

  /**
   * "No trabaja acá": se descarta sin guardar NADA.
   *
   * Ni plantilla ni foto. El sistema va a volver a preguntar si esa persona
   * reaparece dentro de un rato, y es el precio elegido a cambio de no armar un
   * fichero biométrico de gente que nunca dio su consentimiento.
   */
  ignorarPersona(e: EventItem): void {
    if (this.demoMode) return;
    this.busy = e.id;
    this.api.resolve(e.id, 'false_positive', 'No trabaja en este entorno').subscribe(() => {
      this.busy = null;
      this.load();
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

  /** Cuánto cubre el clip, tomado de lo que se grabó y no de un texto fijo. */
  ventanaClip(ev: EvidenceItem): string {
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

  /** URL lista para un <img> con la miniatura que vino en la alerta. */
  urlMiniatura(e: EventItem): string {
    const b64 = e.faceThumbnail;
    if (!b64) return '';
    // El módulo manda el base64 pelado. Si alguna vez llegara ya con prefijo, el
    // `img` quedaría roto sin decir nada, y acá lo único que se le pide al
    // operador es mirar una cara: se acepta cualquiera de las dos formas.
    return b64.startsWith('data:') ? b64 : `data:image/jpeg;base64,${b64}`;
  }

  trackEvent(_: number, e: EventItem): string {
    return e.id;
  }
}
