import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { CdkDrag, CdkDragDrop, CdkDropList, CdkDropListGroup } from '@angular/cdk/drag-drop';
import { Subscription, forkJoin, interval, startWith, switchMap } from 'rxjs';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { CameraFeedComponent } from '../../shared/camera-feed.component';
import { ModuleIconComponent } from '../../shared/module-icon.component';
import {
  CamerasService,
  type ApiAssignment,
  type ApiCamera,
  type ApiModule,
  type LiveDetection,
  type MediaStatus,
} from '../../core/cameras.service';
import { CATEGORY_LABEL, type ModuleCategory } from '../../core/models';
import { ICON_BY_CATEGORY, ICON_BY_KEY } from '../../core/module-visuals';
import { ModulosService } from '../../core/modulos.service';
import { ActivatedRoute } from '@angular/router';
import { AI_MODULES } from '../../core/catalog';

type FilterKey = 'all' | ModuleCategory;

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'all', label: 'Todos' },
  { key: 'security', label: 'Seguridad' },
  { key: 'hr', label: 'Personas' },
  { key: 'productivity', label: 'Operaciones' },
  { key: 'logistics', label: 'Logística' },
];

/** Vista compuesta: cámara + su estado de captura + sus módulos. */
export interface CameraView extends ApiCamera {
  media?: MediaStatus;
  assignments: ApiAssignment[];
}

@Component({
  selector: 'px-cameras',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    CdkDrag,
    CdkDropList,
    CdkDropListGroup,
    PageHeaderComponent,
    CameraFeedComponent,
    ModuleIconComponent,
  ],
  templateUrl: './cameras.component.html',
  styleUrls: ['./cameras.component.scss'],
})
export class CamerasComponent implements OnInit, OnDestroy {
  private readonly api = inject(CamerasService);
  private readonly modulos = inject(ModulosService);
  private readonly ruta = inject(ActivatedRoute);

  /** Nombre del módulo que hace falta asignar para la pantalla que se pidió. */
  faltaModulo: string | null = null;
  private subs = new Subscription();

  readonly filters = FILTERS;
  readonly catLabel = CATEGORY_LABEL;

  cameras: CameraView[] = [];
  modules: ApiModule[] = [];
  visibleModules: ApiModule[] = [];
  detections: Record<string, LiveDetection[]> = {};

  activeFilter: FilterKey = 'all';
  view: 'grid' | 'list' = 'grid';
  loading = true;
  /** null = la API no respondió (distinto de "no hay cámaras"). */
  apiDown = false;
  busy: string | null = null;
  notice: { text: string; kind: 'ok' | 'error' } | null = null;

  // Formulario de alta
  showForm = false;
  form = { name: '', location: '', sourceKind: 'usb' as 'usb' | 'rtsp', usbIndex: '0', rtspUrl: '', fps: 10 };
  formError: string | null = null;
  saving = false;

  ngOnInit(): void {
    this.reload();

    // Alguien intentó entrar a una pantalla cuyo módulo no está asignado y el
    // guard lo trajo acá. Sin este cartel el rebote es mudo: aterrizás en
    // Cámaras sin saber por qué, y la conclusión razonable es que la
    // aplicación se rompió.
    const falta = this.ruta.snapshot.queryParamMap.get('falta');
    if (falta) {
      const nombre = AI_MODULES.find((m) => m.moduleKey === falta)?.name ?? falta;
      this.faltaModulo = nombre;
    }
    // Estado de captura y detecciones en vivo
    this.subs.add(
      interval(4000).pipe(startWith(0), switchMap(() => this.api.mediaStatus())).subscribe((ms) => {
        const byId = new Map(ms.map((m) => [m.cameraId, m]));
        this.cameras.forEach((c) => (c.media = byId.get(c.id)));
      }),
    );
    this.subs.add(
      interval(1000).pipe(startWith(0), switchMap(() => this.api.detections())).subscribe((d) => (this.detections = d)),
    );
    // Refresco periódico de la lista (una cámara nueva aparece sola)
    this.subs.add(interval(15000).subscribe(() => this.reload(true)));
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
  }

  reload(silent = false): void {
    if (!silent) this.loading = true;
    forkJoin({
      cams: this.api.listCameras(),
      mods: this.api.listModules(),
      assigns: this.api.listAssignments(),
    }).subscribe(({ cams, mods, assigns }) => {
      this.loading = false;
      this.apiDown = cams === null;
      if (cams === null) {
        this.cameras = [];
        return;
      }
      const prevMedia = new Map(this.cameras.map((c) => [c.id, c.media]));
      this.cameras = cams.map((c) => ({
        ...c,
        media: prevMedia.get(c.id),
        assignments: assigns.filter((a) => a.cameraId === c.id),
      }));
      this.modules = mods;
      this.applyFilter();
    });
  }

  // ── catálogo ───────────────────────────────────────────────────────
  setFilter(key: FilterKey): void {
    this.activeFilter = key;
    this.applyFilter();
  }

  private applyFilter(): void {
    this.visibleModules =
      this.activeFilter === 'all' ? this.modules : this.modules.filter((m) => m.category === this.activeFilter);
  }

  setView(v: 'grid' | 'list'): void {
    this.view = v;
  }

  iconOf(m: { moduleKey: string; category: ModuleCategory }): { icon: string; color: string } {
    return ICON_BY_KEY[m.moduleKey] ?? ICON_BY_CATEGORY[m.category] ?? { icon: 'zone', color: '#3b82f6' };
  }

  // ── asignación (drag & drop) ───────────────────────────────────────
  /**
   * Soltar un módulo sobre una cámara PERSISTE la asignación: crea la fila en
   * camera_module_configs y el ai-worker la levanta en su próximo ciclo.
   */
  onDrop(event: CdkDragDrop<CameraView>, camera: CameraView): void {
    const mod = event.item.data as ApiModule | undefined;
    if (!mod) {
      // Si el drop llega sin módulo hay un problema de configuración del
      // arrastre: se avisa en lugar de no hacer nada en silencio.
      this.flash('No se pudo leer el módulo arrastrado', 'error');
      return;
    }
    this.assign(camera, mod);
  }

  /** Punto único de asignación: lo usan tanto el arrastre como el selector. */
  private assign(camera: CameraView, mod: ApiModule): void {
    if (camera.assignments.some((a) => a.aiModuleId === mod.id)) {
      this.flash(`"${mod.name}" ya está asignado a ${camera.name}`, 'error');
      return;
    }

    this.busy = camera.id;
    const config = this.defaultConfigFor(mod);
    this.api.assignModule(camera.id, mod.id, config).subscribe({
      next: (ok) => {
        this.busy = null;
        if (ok) {
          this.flash(`"${mod.name}" asignado a ${camera.name}. Reiniciá el ai-worker para que lo tome.`, 'ok');
          this.reload(true);
          this.avisarQueCambioElMenu();
        } else {
          this.flash('No se pudo asignar el módulo', 'error');
        }
      },
      error: (e) => {
        this.busy = null;
        this.flash(`No se pudo asignar: ${e?.message ?? e}`, 'error');
      },
    });
  }

  /**
   * Asignar o quitar un módulo enciende y apaga funciones del producto.
   *
   * Esta pantalla es la única desde donde eso cambia, así que es la única que
   * puede avisar. Sin esto, alguien asigna "Ingreso de personas" y el menú
   * sigue igual hasta que recarga la página — y lo razonable es concluir que
   * no funcionó y volver a arrastrarlo.
   */
  private avisarQueCambioElMenu(): void {
    this.modulos.refrescar().subscribe();
  }

  /** Valores por defecto tomados del JSON Schema del módulo (CONTRACTS §4). */
  private defaultConfigFor(m: ApiModule): Record<string, unknown> {
    const props = (m.configSchema?.['properties'] ?? {}) as Record<string, { default?: unknown }>;
    const cfg: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) {
      if (v?.default !== undefined) cfg[k] = v.default;
    }
    return cfg;
  }

  removeModule(camera: CameraView, a: ApiAssignment): void {
    this.busy = camera.id;
    this.api.unassignModule(camera.id, a.aiModuleId).subscribe((ok) => {
      this.busy = null;
      if (ok) {
        this.flash(`"${a.moduleName}" quitado de ${camera.name}`, 'ok');
        this.reload(true);
        this.avisarQueCambioElMenu();
      } else {
        this.flash('No se pudo quitar el módulo', 'error');
      }
    });
  }

  // ── alta de cámara ─────────────────────────────────────────────────
  openForm(): void {
    this.showForm = true;
    this.formError = null;
    this.form = { name: '', location: '', sourceKind: 'usb', usbIndex: '0', rtspUrl: '', fps: 10 };
  }

  closeForm(): void {
    this.showForm = false;
  }

  saveCamera(): void {
    // `String(...)` y no `.trim()` directo: un <input type="number"> con ngModel
    // entrega un NÚMERO, y llamar .trim() sobre él lanza una excepción que muere
    // en el manejador del clic — el botón no hacía nada y sin ningún mensaje.
    const txt = (v: unknown): string => String(v ?? '').trim();

    const name = txt(this.form.name);
    const source = this.form.sourceKind === 'usb' ? txt(this.form.usbIndex) : txt(this.form.rtspUrl);

    if (!name) {
      this.formError = 'Poné un nombre para la cámara.';
      return;
    }
    if (!source) {
      this.formError = this.form.sourceKind === 'usb' ? 'Indicá el índice USB.' : 'Pegá la URL RTSP.';
      return;
    }
    if (this.form.sourceKind === 'usb' && !/^\d+$/.test(source)) {
      this.formError = 'El índice debe ser un número entero (0, 1, 2…).';
      return;
    }

    const fps = Number(this.form.fps);
    if (!Number.isFinite(fps) || fps < 1 || fps > 30) {
      this.formError = 'Los cuadros por segundo deben estar entre 1 y 30.';
      return;
    }

    this.saving = true;
    this.formError = null;
    this.api
      .createCamera({ name, location: txt(this.form.location) || undefined, source, fps })
      .subscribe({
        next: (res) => {
          this.saving = false;
          if ('error' in res) {
            this.formError = res.error;
            return;
          }
          this.showForm = false;
          this.flash(`Cámara "${res.name}" creada. La captura arranca en unos segundos.`, 'ok');
          this.reload(true);
        },
        // Ningún fallo puede quedar mudo: si algo revienta, se dice en el formulario.
        error: (e) => {
          this.saving = false;
          this.formError = `No se pudo crear la cámara: ${e?.message ?? e}`;
        },
      });
  }

  deleteCamera(c: CameraView): void {
    if (!confirm(`¿Eliminar la cámara "${c.name}"? Se borran también sus módulos asignados.`)) return;
    this.busy = c.id;
    this.api.deleteCamera(c.id).subscribe((ok) => {
      this.busy = null;
      this.flash(ok ? `Cámara "${c.name}" eliminada` : 'No se pudo eliminar', ok ? 'ok' : 'error');
      this.reload(true);
    });
  }

  // ── ayudas de vista ────────────────────────────────────────────────
  snapshotUrl(c: CameraView): string | null {
    return c.media?.connected ? this.api.snapshotUrl(c.id) : null;
  }

  detectionsFor(c: CameraView): LiveDetection[] {
    return this.detections[c.id] ?? [];
  }

  sourceLabel(c: CameraView): string {
    if (!c.source) return 'sin origen';
    return /^\d+$/.test(c.source) ? `USB ${c.source}` : c.source.replace(/\/\/[^@]*@/, '//•••@');
  }

  statusOf(c: CameraView): { text: string; ok: boolean } {
    if (c.media?.connected) return { text: 'En línea', ok: true };
    if (c.media) return { text: c.media.lastError ? 'Sin señal' : 'Conectando…', ok: false };
    return { text: 'Sin captura', ok: false };
  }

  get connectedCount(): number {
    return this.cameras.filter((c) => c.media?.connected).length;
  }

  get totalAssignments(): number {
    return this.cameras.reduce((a, c) => a + c.assignments.length, 0);
  }

  private flash(text: string, kind: 'ok' | 'error'): void {
    this.notice = { text, kind };
    setTimeout(() => (this.notice = null), 3800);
  }

  trackCam(_: number, c: CameraView): string {
    return c.id;
  }

  trackMod(_: number, m: ApiModule): string {
    return m.id;
  }
}
