import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { CdkDrag, CdkDragDrop, CdkDropList, CdkDropListGroup } from '@angular/cdk/drag-drop';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { CameraFeedComponent } from '../../shared/camera-feed.component';
import { ModuleIconComponent } from '../../shared/module-icon.component';
import { AI_MODULES, CATEGORY_FILTERS } from '../../core/catalog';
import { DEMO_CAMERAS } from '../../core/demo-data';
import { CATEGORY_LABEL, type AiModule, type Camera, type ModuleCategory } from '../../core/models';

type FilterKey = 'all' | ModuleCategory;

@Component({
  selector: 'px-cameras',
  standalone: true,
  imports: [
    CommonModule,
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
export class CamerasComponent {
  readonly filters = CATEGORY_FILTERS;
  readonly catLabel = CATEGORY_LABEL;

  activeFilter: FilterKey = 'all';
  view: 'grid' | 'list' = 'grid';
  cameras: Camera[] = DEMO_CAMERAS.map((c) => ({ ...c, modules: [...c.modules] }));
  visibleModules: AiModule[] = AI_MODULES;

  /** Módulo recién asignado, para resaltarlo un instante tras el drop. */
  private justAssigned: { cameraId: string; moduleKey: string } | null = null;

  setFilter(key: FilterKey): void {
    this.activeFilter = key;
    this.visibleModules = key === 'all' ? AI_MODULES : AI_MODULES.filter((m) => m.category === key);
  }

  setView(v: 'grid' | 'list'): void {
    this.view = v;
  }

  modulesOf(cam: Camera): AiModule[] {
    return cam.modules
      .map((k) => AI_MODULES.find((m) => m.moduleKey === k))
      .filter((m): m is AiModule => !!m);
  }

  /**
   * Suelta un módulo sobre una cámara.
   *
   * En la API real esto crea una fila en `camera_module_configs` y abre el
   * formulario de configuración generado desde el `config.schema.json` del
   * módulo (CONTRACTS §4). Acá se refleja el estado local de inmediato.
   */
  onDrop(event: CdkDragDrop<Camera>, camera: Camera): void {
    const mod = event.item.data as AiModule | undefined;
    if (!mod) return;

    // Una cámara no ejecuta el mismo módulo dos veces:
    // UNIQUE (camera_id, ai_module_id) en la base.
    if (camera.modules.includes(mod.moduleKey)) return;

    camera.modules = [...camera.modules, mod.moduleKey];
    this.justAssigned = { cameraId: camera.id, moduleKey: mod.moduleKey };
    setTimeout(() => (this.justAssigned = null), 1600);
  }

  removeModule(camera: Camera, moduleKey: string): void {
    camera.modules = camera.modules.filter((k) => k !== moduleKey);
  }

  isJustAssigned(cameraId: string, moduleKey: string): boolean {
    const j = this.justAssigned;
    return !!j && j.cameraId === cameraId && j.moduleKey === moduleKey;
  }

  trackCam(_: number, c: Camera): string {
    return c.id;
  }

  trackMod(_: number, m: AiModule): string {
    return m.id;
  }
}
