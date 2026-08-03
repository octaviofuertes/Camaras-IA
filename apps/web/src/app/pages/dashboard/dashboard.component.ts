import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { Subscription, interval, startWith, switchMap } from 'rxjs';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { CameraFeedComponent } from '../../shared/camera-feed.component';
import { ModuleIconComponent } from '../../shared/module-icon.component';
import { AI_MODULES } from '../../core/catalog';
import { EVENTS_BY_HOUR, EVENTS_BY_TYPE, TOP_MODULES } from '../../core/demo-data';
import { CamerasService, type LiveDetection } from '../../core/cameras.service';
import { EventsService } from '../../core/events.service';
import { SEVERITY_CLASS, SEVERITY_LABEL, type Camera, type EventItem } from '../../core/models';

interface DonutSlice {
  label: string;
  value: number;
  color: string;
  pct: number;
  dash: string;
  offset: number;
}

@Component({
  selector: 'px-dashboard',
  standalone: true,
  imports: [CommonModule, RouterLink, PageHeaderComponent, CameraFeedComponent, ModuleIconComponent],
  templateUrl: './dashboard.component.html',
  styleUrls: ['./dashboard.component.scss'],
})
export class DashboardComponent implements OnInit, OnDestroy {
  private readonly camsApi = inject(CamerasService);
  private readonly eventsApi = inject(EventsService);
  private subs = new Subscription();

  cameras: Camera[] = [];
  events: EventItem[] = [];
  detections: Record<string, LiveDetection[]> = {};
  camsDemo = false;
  eventsDemo = false;

  readonly topModules = TOP_MODULES;
  readonly byType = EVENTS_BY_TYPE;
  readonly totalByType = EVENTS_BY_TYPE.reduce((a, b) => a + b.value, 0);
  readonly sevLabel = SEVERITY_LABEL;
  readonly sevClass = SEVERITY_CLASS;

  readonly donut: DonutSlice[] = (() => {
    const C = 2 * Math.PI * 42;
    const total = EVENTS_BY_TYPE.reduce((a, b) => a + b.value, 0);
    let acc = 0;
    return EVENTS_BY_TYPE.map((s) => {
      const frac = s.value / total;
      const len = frac * C;
      const slice: DonutSlice = {
        ...s,
        pct: Math.round(frac * 100),
        dash: `${len - 2} ${C - len + 2}`,
        offset: -acc,
      };
      acc += len;
      return slice;
    });
  })();

  readonly areaPath = this.buildArea(EVENTS_BY_HOUR);
  readonly linePath = this.buildLine(EVENTS_BY_HOUR);

  ngOnInit(): void {
    // Estado de cámaras cada 5 s
    this.subs.add(
      interval(5000)
        .pipe(startWith(0), switchMap(() => this.camsApi.list()))
        .subscribe((res) => {
          this.cameras = res.items;
          this.camsDemo = res.demo;
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
          this.eventsDemo = res.demo;
        }),
    );
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
  }

  get onlineCount(): number {
    return this.cameras.filter((c) => c.status === 'online').length;
  }

  snapshotUrl(cam: Camera): string | null {
    return cam.live ? this.camsApi.snapshotUrl(cam.id) : null;
  }

  detectionsFor(cam: Camera): LiveDetection[] {
    return this.detections[cam.id] ?? [];
  }

  private scaleX(i: number, n: number): number {
    return (i / (n - 1)) * 100;
  }

  private scaleY(v: number, max: number): number {
    return 100 - (v / max) * 92;
  }

  private buildLine(data: number[]): string {
    const max = Math.max(...data);
    return data
      .map((v, i) => `${i === 0 ? 'M' : 'L'}${this.scaleX(i, data.length).toFixed(2)},${this.scaleY(v, max).toFixed(2)}`)
      .join(' ');
  }

  private buildArea(data: number[]): string {
    return `${this.buildLine(data)} L100,100 L0,100 Z`;
  }

  moduleIcon(key: string): { icon: string; color: string } {
    const m = AI_MODULES.find((x) => x.moduleKey === key);
    return { icon: m?.icon ?? 'zone', color: m?.color ?? '#3b82f6' };
  }

  trackEvent(_: number, e: EventItem): string {
    return e.id;
  }

  trackCam(_: number, c: Camera): string {
    return c.id;
  }
}
