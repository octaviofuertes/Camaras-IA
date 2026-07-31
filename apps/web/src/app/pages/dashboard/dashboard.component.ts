import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { PageHeaderComponent } from '../../shared/page-header.component';
import { CameraFeedComponent } from '../../shared/camera-feed.component';
import { ModuleIconComponent } from '../../shared/module-icon.component';
import { AI_MODULES } from '../../core/catalog';
import { DEMO_CAMERAS, DEMO_EVENTS, EVENTS_BY_HOUR, EVENTS_BY_TYPE, TOP_MODULES } from '../../core/demo-data';
import { SEVERITY_CLASS, SEVERITY_LABEL, type EventItem } from '../../core/models';

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
export class DashboardComponent {
  readonly cameras = DEMO_CAMERAS;
  readonly events = DEMO_EVENTS;
  readonly topModules = TOP_MODULES;
  readonly byType = EVENTS_BY_TYPE;
  readonly totalByType = EVENTS_BY_TYPE.reduce((a, b) => a + b.value, 0);

  readonly sevLabel = SEVERITY_LABEL;
  readonly sevClass = SEVERITY_CLASS;

  /** Segmentos del donut, calculados como stroke-dasharray sobre un círculo. */
  readonly donut: DonutSlice[] = (() => {
    const C = 2 * Math.PI * 42; // circunferencia (r=42)
    let acc = 0;
    return EVENTS_BY_TYPE.map((s) => {
      const total = EVENTS_BY_TYPE.reduce((a, b) => a + b.value, 0);
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

  /** Path del área de "eventos por hora". */
  readonly areaPath = this.buildArea(EVENTS_BY_HOUR);
  readonly linePath = this.buildLine(EVENTS_BY_HOUR);

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
}
