import {
  AfterViewInit,
  Component,
  ElementRef,
  Input,
  NgZone,
  OnDestroy,
  ViewChild,
  inject,
  ChangeDetectorRef,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import type { LiveDetection } from '../core/cameras.service';

/**
 * Marco de video de una cámara.
 *
 * Con `streamUrl` muestra el MJPEG REAL de media-service (USB o RTSP) y dibuja
 * encima las cajas de detección. Sin él, dibuja una escena sintética para no
 * mostrar un placeholder roto.
 */
@Component({
  selector: 'px-camera-feed',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="feed" [class.compact]="compact" [ngClass]="snapshotUrl ? 'is-live' : 'scene-' + scene">
      <!-- Video real -->
      <ng-container *ngIf="snapshotUrl; else synthetic">
        <img #video class="video" alt="Vista en vivo" />

        <!-- Cajas de detección sobre el video (coordenadas normalizadas 0..1) -->
        <div class="boxes" *ngIf="detections?.length">
          <div
            class="box"
            *ngFor="let d of detections"
            [style.left.%]="d.bbox[0] * 100"
            [style.top.%]="d.bbox[1] * 100"
            [style.width.%]="d.bbox[2] * 100"
            [style.height.%]="d.bbox[3] * 100"
          >
            <span class="box-tag">{{ d.classLabel }} {{ (d.confidence * 100).toFixed(0) }}%</span>
          </div>
        </div>

        <span class="err" *ngIf="failed">Sin señal</span>
      </ng-container>

      <!-- Escena sintética (sin cámara real conectada) -->
      <ng-template #synthetic>
        <div class="scene">
          <div class="floor"></div>
          <div class="prop p1"></div>
          <div class="prop p2"></div>
          <div class="prop p3"></div>
          <div class="figure f1"></div>
          <div class="figure f2"></div>
        </div>
        <div class="grain"></div>
      </ng-template>

      <span class="ts" *ngIf="timestamp">{{ timestamp }}</span>
      <span class="live" *ngIf="live"><i></i>LIVE</span>
    </div>
  `,
  styles: [
    `
      .feed {
        position: relative;
        aspect-ratio: 16 / 9;
        border-radius: var(--radius-sm);
        overflow: hidden;
        background: #0a0e14;
      }
      .feed.compact { aspect-ratio: 16 / 10; }
      .video { width: 100%; height: 100%; object-fit: cover; display: block; }

      .boxes { position: absolute; inset: 0; pointer-events: none; }
      .box {
        position: absolute;
        border: 2px solid #3cf;
        border-radius: 2px;
        box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.35);
      }
      .box-tag {
        position: absolute;
        top: -19px;
        left: -2px;
        background: #3cf;
        color: #06202b;
        font-size: 10px;
        font-weight: 700;
        padding: 1px 5px;
        border-radius: 3px;
        white-space: nowrap;
      }
      .err {
        position: absolute;
        inset: 0;
        display: grid;
        place-items: center;
        background: rgba(0, 0, 0, 0.6);
        color: var(--text-mute);
        font-size: 12px;
      }

      .scene { position: absolute; inset: 0; }
      .floor {
        position: absolute;
        inset: 55% 0 0 0;
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.06), rgba(255, 255, 255, 0.02));
      }
      .prop, .figure { position: absolute; border-radius: 2px; }
      .figure { background: rgba(255, 255, 255, 0.28); border-radius: 40% 40% 20% 20%; }
      .f1 { width: 4%; height: 20%; left: 34%; top: 47%; }
      .f2 { width: 3.6%; height: 17%; left: 58%; top: 51%; }
      .grain {
        position: absolute;
        inset: 0;
        background: repeating-linear-gradient(0deg, rgba(255, 255, 255, 0.018) 0 1px, transparent 1px 3px);
        pointer-events: none;
      }

      .scene-lobby   { background: linear-gradient(160deg, #1e293b, #0f172a); }
      .scene-lobby .p1 { width: 22%; height: 46%; left: 8%;  top: 16%; background: rgba(148, 197, 255, 0.13); }
      .scene-lobby .p2 { width: 22%; height: 46%; left: 34%; top: 16%; background: rgba(148, 197, 255, 0.10); }
      .scene-lobby .p3 { width: 22%; height: 46%; left: 60%; top: 16%; background: rgba(148, 197, 255, 0.13); }

      .scene-factory { background: linear-gradient(160deg, #2a2418, #14110b); }
      .scene-factory .p1 { width: 30%; height: 26%; left: 6%;  top: 40%; background: rgba(234, 179, 8, 0.20); }
      .scene-factory .p2 { width: 26%; height: 22%; left: 42%; top: 44%; background: rgba(234, 179, 8, 0.14); }
      .scene-factory .p3 { width: 20%; height: 34%; left: 74%; top: 32%; background: rgba(148, 163, 184, 0.16); }

      .scene-warehouse { background: linear-gradient(160deg, #2b2519, #131009); }
      .scene-warehouse .p1 { width: 16%; height: 52%; left: 4%;  top: 22%; background: rgba(217, 165, 92, 0.24); }
      .scene-warehouse .p2 { width: 16%; height: 44%; left: 24%; top: 30%; background: rgba(217, 165, 92, 0.18); }
      .scene-warehouse .p3 { width: 30%; height: 38%; left: 64%; top: 30%; background: rgba(217, 165, 92, 0.14); }

      .scene-office { background: linear-gradient(160deg, #1b2436, #0d1320); }
      .scene-office .p1 { width: 40%; height: 10%; left: 6%;  top: 58%; background: rgba(148, 163, 184, 0.18); }
      .scene-office .p2 { width: 40%; height: 10%; left: 52%; top: 58%; background: rgba(148, 163, 184, 0.14); }
      .scene-office .p3 { width: 86%; height: 8%;  left: 6%;  top: 74%; background: rgba(148, 163, 184, 0.10); }

      .scene-parking { background: linear-gradient(160deg, #1d2a20, #0c1410); }
      .scene-parking .p1 { width: 18%; height: 16%; left: 8%;  top: 52%; background: rgba(226, 232, 240, 0.20); border-radius: 4px; }
      .scene-parking .p2 { width: 18%; height: 16%; left: 30%; top: 55%; background: rgba(226, 232, 240, 0.15); border-radius: 4px; }
      .scene-parking .p3 { width: 18%; height: 16%; left: 54%; top: 52%; background: rgba(226, 232, 240, 0.12); border-radius: 4px; }

      .scene-dock { background: linear-gradient(160deg, #1a2233, #0b1018); }
      .scene-dock .p1 { width: 34%; height: 34%; left: 6%;  top: 34%; background: rgba(96, 165, 250, 0.22); border-radius: 4px; }
      .scene-dock .p2 { width: 22%; height: 26%; left: 46%; top: 42%; background: rgba(226, 232, 240, 0.14); border-radius: 4px; }
      .scene-dock .p3 { width: 20%; height: 30%; left: 72%; top: 38%; background: rgba(234, 179, 8, 0.18); border-radius: 4px; }

      .ts {
        position: absolute;
        left: 8px;
        bottom: 8px;
        font-size: 11px;
        font-variant-numeric: tabular-nums;
        background: rgba(0, 0, 0, 0.62);
        padding: 3px 7px;
        border-radius: 4px;
        color: #e6edf3;
      }
      .live {
        position: absolute;
        right: 8px;
        bottom: 8px;
        display: inline-flex;
        align-items: center;
        gap: 5px;
        font-size: 10.5px;
        font-weight: 700;
        letter-spacing: 0.4px;
        background: rgba(239, 68, 68, 0.9);
        color: #fff;
        padding: 3px 7px;
        border-radius: 4px;
      }
      .live i {
        width: 5px;
        height: 5px;
        border-radius: 50%;
        background: #fff;
        animation: blink 1.4s ease-in-out infinite;
      }
      @keyframes blink { 50% { opacity: 0.25; } }
    `,
  ],
})
export class CameraFeedComponent implements AfterViewInit, OnDestroy {
  private readonly zone = inject(NgZone);

  @ViewChild('video') videoRef?: ElementRef<HTMLImageElement>;

  @Input() scene = 'lobby';
  @Input() timestamp = '';
  @Input() live = false;
  @Input() compact = false;
  /** Si viene, se muestra el video real de esa cámara en lugar de la escena. */
  @Input() snapshotUrl: string | null = null;
  @Input() detections: LiveDetection[] | null = null;
  /** Cuadros por segundo del refresco. */
  @Input() fps = 6;

  failed = false;

  private timer: ReturnType<typeof setInterval> | null = null;
  private loading = false;

  ngAfterViewInit(): void {
    // No se corta si todavía no hay snapshotUrl: tick() lo valida en cada
    // vuelta, así la tarjeta empieza a mostrar imagen apenas la cámara conecta.
    this.zone.runOutsideAngular(() => {
      this.tick();
      this.timer = setInterval(() => this.tick(), Math.max(1000 / this.fps, 80));
    });
  }

  ngOnDestroy(): void {
    if (this.timer) clearInterval(this.timer);
  }

  /** Pide el siguiente cuadro. No encima pedidos si la red va lenta. */
  private readonly cdr = inject(ChangeDetectorRef);

  private tick(): void {
    const el = this.videoRef?.nativeElement;
    if (!this.snapshotUrl || !el || this.loading) return;
    this.loading = true;

    const url = `${this.snapshotUrl}?t=${Date.now()}`;
    const pre = new Image();
    pre.onload = () => {
      el.src = url;
      this.failed = false;
      this.loading = false;
      this.cdr.detectChanges();
    };
    pre.onerror = (err) => {
      console.error('Error al cargar imagen', url, err);
      this.failed = true;
      this.loading = false;
      this.cdr.detectChanges();
    };
    pre.src = url;
  }
}
