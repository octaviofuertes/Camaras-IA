import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { ModuleIconComponent } from '../../shared/module-icon.component';
import { CameraFeedComponent } from '../../shared/camera-feed.component';
import { EventsService } from '../../core/events.service';
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
  imports: [CommonModule, PageHeaderComponent, ModuleIconComponent, CameraFeedComponent],
  templateUrl: './events.component.html',
  styleUrls: ['./events.component.scss'],
})
export class EventsComponent implements OnInit {
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

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading = true;
    const f = this.activeFilter;
    this.api.list(f === 'all' ? undefined : f).subscribe((res) => {
      this.events = res.items;
      this.demoMode = res.demo;
      this.loading = false;
      if (this.selected && !res.items.find((e) => e.id === this.selected!.id)) {
        this.selected = null;
      }
    });
  }

  setFilter(key: FilterKey): void {
    this.activeFilter = key;
    this.load();
  }

  select(e: EventItem): void {
    this.selected = e;
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
