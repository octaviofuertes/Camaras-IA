import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { Subscription, interval, startWith, switchMap } from 'rxjs';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { CameraFeedComponent } from '../../shared/camera-feed.component';
import { CameraLiveComponent } from '../../shared/camera-live.component';
import { ZonasService } from '../../core/zonas.service';
import { ModuleIconComponent } from '../../shared/module-icon.component';
import { AI_MODULES } from '../../core/catalog';
import { CamerasService, type ApiCamera, type LiveDetection, type MediaStatus } from '../../core/cameras.service';
import { EventsService, TITULOS_EVENTO } from '../../core/events.service';
import {
  StatsService,
  type Almacenamiento,
  type EstadisticasEventos,
} from '../../core/stats.service';
import { SEVERITY_CLASS, SEVERITY_LABEL, type EventItem } from '../../core/models';

interface DonutSlice {
  label: string;
  value: number;
  color: string;
  pct: number;
  dash: string;
  offset: number;
}

interface ModuloConEventos {
  name: string;
  icon: string;
  color: string;
  total: number;
  /** Ancho de la barra respecto del módulo que más eventos generó hoy. */
  pct: number;
}

@Component({
  selector: 'px-dashboard',
  standalone: true,
  imports: [CommonModule, RouterLink, PageHeaderComponent, CameraFeedComponent,
    CameraLiveComponent, ModuleIconComponent],
  templateUrl: './dashboard.component.html',
  styleUrls: ['./dashboard.component.scss'],
})
export class DashboardComponent implements OnInit, OnDestroy {
  private readonly camsApi = inject(CamerasService);
  private readonly eventsApi = inject(EventsService);
  private readonly statsApi = inject(StatsService);
  private readonly zonasApi = inject(ZonasService);
  private subs = new Subscription();

  cameras: (ApiCamera & { media?: MediaStatus })[] = [];
  events: EventItem[] = [];
  detections: Record<string, LiveDetection[]> = {};
  camsDemo = false;
  eventosSinApi = false;

  /**
   * Los agregados del día, medidos sobre la base.
   *
   * `null` mientras no se pudo consultar. La pantalla muestra un guión: no hay
   * ningún valor de ejemplo detrás, y un cero se leería como "hoy no pasó
   * nada", que es una afirmación distinta a "no pude preguntar".
   */
  stats: EstadisticasEventos | null = null;
  almacenamiento: Almacenamiento | null = null;
  /** Personas distintas identificadas hoy. Null sin el módulo de ingreso. */
  personasHoy: number | null = null;

  /** El período que abarcan los números de esta pantalla: el día de hoy. */
  readonly periodo = `Hoy · ${new Date().toLocaleDateString('es-AR')}`;

  readonly sevLabel = SEVERITY_LABEL;
  readonly sevClass = SEVERITY_CLASS;

  // ── lo que se dibuja, derivado de `stats` ──────────────────────────

  get donut(): DonutSlice[] {
    const tipos = this.stats?.porTipo ?? [];
    const total = tipos.reduce((a, b) => a + b.total, 0);
    if (!total) return [];

    const C = 2 * Math.PI * 42;
    let acc = 0;
    return tipos.map((t) => {
      const frac = t.total / total;
      const len = frac * C;
      const slice: DonutSlice = {
        label: TITULOS_EVENTO[t.eventType] ?? t.eventType,
        value: t.total,
        color: this.moduleIcon(t.moduleKey).color,
        pct: Math.round(frac * 100),
        dash: `${Math.max(len - 2, 0)} ${C - len + 2}`,
        offset: -acc,
      };
      acc += len;
      return slice;
    });
  }

  get totalByType(): number {
    return (this.stats?.porTipo ?? []).reduce((a, b) => a + b.total, 0);
  }

  get topModules(): ModuloConEventos[] {
    const mods = this.stats?.porModulo ?? [];
    const max = Math.max(...mods.map((m) => m.total), 1);
    return mods.map((m) => {
      const cat = AI_MODULES.find((x) => x.moduleKey === m.moduleKey);
      return {
        name: cat?.name ?? m.moduleKey,
        icon: cat?.icon ?? 'zone',
        color: cat?.color ?? '#0b5cf6',
        total: m.total,
        pct: Math.round((m.total / max) * 100),
      };
    });
  }

  /** ¿Hubo algún evento hoy? Sin eso, los gráficos no tienen nada que decir. */
  get hayActividad(): boolean {
    return !!this.stats && this.stats.hoy > 0;
  }

  get areaPath(): string {
    return this.buildArea(this.stats?.porHora ?? []);
  }

  get linePath(): string {
    return this.buildLine(this.stats?.porHora ?? []);
  }

  /**
   * Cuánto cambió respecto de ayer, en porcentaje.
   *
   * `null` cuando ayer fue cero: no hay porcentaje que calcular contra cero, y
   * escribir "+100%" o "+∞" sería inventar una comparación que no existe.
   */
  variacion(hoy: number, ayer: number): number | null {
    if (!ayer) return null;
    return Math.round(((hoy - ayer) / ayer) * 100);
  }

  /** El texto de la comparación, ya redactado. */
  textoVariacion(hoy: number, ayer: number): string {
    const v = this.variacion(hoy, ayer);
    if (v === null) return ayer === 0 && hoy > 0 ? 'ayer a esta hora, ninguno' : 'sin comparación';
    const signo = v > 0 ? '↑' : v < 0 ? '↓' : '=';
    return `${signo} ${Math.abs(v)}% vs ayer a esta hora`;
  }

  /** Si la variación es una mala noticia. Más eventos críticos no es un logro. */
  claseVariacion(hoy: number, ayer: number, masEsPeor = false): string {
    const v = this.variacion(hoy, ayer);
    if (v === null || v === 0) return '';
    const sube = v > 0;
    return sube === masEsPeor ? 'down' : 'up';
  }

  // ── almacenamiento ────────────────────────────────────────────────

  /** Bytes en la unidad que corresponda, sin inventar precisión. */
  enTamano(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    const u = ['KB', 'MB', 'GB', 'TB', 'PB'];
    let v = bytes / 1024;
    let i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; }
    return `${v.toFixed(v < 10 ? 2 : 1).replace('.', ',')} ${u[i]}`;
  }

  /** Qué parte del disco está ocupada (por todo, no sólo por las evidencias). */
  get discoUsadoPct(): number | null {
    const a = this.almacenamiento;
    if (!a || !a.discoTotalBytes) return null;
    return Math.round(((a.discoTotalBytes - a.discoLibreBytes) / a.discoTotalBytes) * 100);
  }

  ngOnInit(): void {
    // Los nombres de las áreas, para poder decir "tiene acceso a Recepción" en
    // vez de "tiene acceso" a secas.
    this.zonasApi.cargar().subscribe((pisos) => {
      const mapa: Record<string, string> = {};
      for (const f of pisos) for (const z of f.zonas) if (z.id) mapa[z.id] = z.nombre;
      this.zonasPorId = mapa;
    });
    // Cámaras reales (device-service) cada 8 s
    this.subs.add(
      interval(8000)
        .pipe(startWith(0), switchMap(() => this.camsApi.listCameras()))
        .subscribe((cams) => {
          this.camsDemo = cams === null;
          if (cams === null) return;
          const prev = new Map(this.cameras.map((c) => [c.id, c.media]));
          this.cameras = cams.map((c) => ({ ...c, media: prev.get(c.id) }));
        }),
    );

    // Estado de captura (media-service) cada 4 s
    this.subs.add(
      interval(4000)
        .pipe(startWith(0), switchMap(() => this.camsApi.mediaStatus()))
        .subscribe((ms) => {
          const byId = new Map(ms.map((m) => [m.cameraId, m]));
          this.cameras.forEach((c) => (c.media = byId.get(c.id)));
        }),
    );

    // Detecciones en vivo (cajas sobre el video) cada segundo
    this.subs.add(
      interval(1000)
        .pipe(startWith(0), switchMap(() => this.camsApi.detections()))
        .subscribe((d) => (this.detections = d)),
    );

    // Eventos reales cada 5 s
    this.subs.add(
      interval(5000)
        .pipe(startWith(0), switchMap(() => this.eventsApi.list()))
        .subscribe((res) => {
          this.events = res.items.slice(0, 5);
          this.eventosSinApi = res.sinApi;
        }),
    );

    // Los agregados del día. Cada 30 s y no cada 5: son cuentas sobre toda la
    // tabla del día, y no cambian lo suficiente como para pagarlas más seguido.
    this.subs.add(
      interval(30000)
        .pipe(startWith(0), switchMap(() => this.statsApi.eventos()))
        .subscribe((s) => (this.stats = s)),
    );

    // El disco, cada minuto: recorre el árbol de evidencias.
    this.subs.add(
      interval(60000)
        .pipe(startWith(0), switchMap(() => this.statsApi.almacenamiento()))
        .subscribe((a) => (this.almacenamiento = a)),
    );

    // Personas identificadas hoy. Sólo existe con el módulo de ingreso
    // asignado; sin él contesta 409 y queda en null, y la tarjeta no se dibuja.
    this.subs.add(
      interval(30000)
        .pipe(startWith(0), switchMap(() => this.statsApi.personasIdentificadasHoy()))
        .subscribe((n) => (this.personasHoy = n)),
    );
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
  }

  get onlineCount(): number {
    return this.cameras.filter((c) => c.media?.connected).length;
  }

  snapshotUrl(cam: ApiCamera & { media?: MediaStatus }): string | null {
    return cam.media?.connected ? this.camsApi.snapshotUrl(cam.id) : null;
  }

  detectionsFor(cam: ApiCamera): LiveDetection[] {
    return this.detections[cam.id] ?? [];
  }

  /**
   * La cámara que se está mirando en grande. Null = ninguna.
   *
   * Vive acá y no adentro de la tarjeta porque el visor tapa la pantalla
   * entera: si cada tarjeta abriera el suyo, dos clics rápidos dejarían dos
   * visores superpuestos.
   */
  ampliada: (ApiCamera & { media?: MediaStatus }) | null = null;

  /** id del área del plano → su nombre, para decir dónde está cada cámara. */
  private zonasPorId: Record<string, string> = {};

  ampliar(cam: ApiCamera & { media?: MediaStatus }): void {
    this.ampliada = cam;
  }

  cerrarAmpliada(): void {
    this.ampliada = null;
  }

  /** En qué parte del lugar está la cámara que se está mirando. */
  zonaDe(cam: ApiCamera | null): string | null {
    if (!cam?.floorZoneId) return null;
    return this.zonasPorId[cam.floorZoneId] ?? null;
  }

  isOnline(cam: ApiCamera & { media?: MediaStatus }): boolean {
    return !!cam.media?.connected;
  }

  private scaleX(i: number, n: number): number {
    return (i / (n - 1)) * 100;
  }

  private scaleY(v: number, max: number): number {
    return 100 - (v / max) * 92;
  }

  private buildLine(data: number[]): string {
    if (data.length < 2) return '';
    // Un día sin eventos da todos ceros: dividir por el máximo sería dividir
    // por cero y el gráfico saldría con NaN en cada punto.
    const max = Math.max(...data, 1);
    return data
      .map((v, i) => `${i === 0 ? 'M' : 'L'}${this.scaleX(i, data.length).toFixed(2)},${this.scaleY(v, max).toFixed(2)}`)
      .join(' ');
  }

  private buildArea(data: number[]): string {
    const linea = this.buildLine(data);
    return linea ? `${linea} L100,100 L0,100 Z` : '';
  }

  moduleIcon(key: string): { icon: string; color: string } {
    const m = AI_MODULES.find((x) => x.moduleKey === key);
    return { icon: m?.icon ?? 'zone', color: m?.color ?? '#0b5cf6' };
  }

  trackEvent(_: number, e: EventItem): string {
    return e.id;
  }

  trackCam(_: number, c: ApiCamera): string {
    return c.id;
  }
}
