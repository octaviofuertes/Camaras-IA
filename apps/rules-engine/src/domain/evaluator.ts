// Núcleo data-driven del rules-engine: convierte DetectionBatch (crudo, del ai-worker)
// en candidatos a evento aplicando la config por cámara/módulo (CONTRACTS §5, §7, §12).
//
// Principios (docs/05):
//  - El módulo detecta SIN umbralizar; aquí viven horarios, zonas, umbrales,
//    persistencia y cooldown — todo desde camera_module_configs.config (JSONB).
//  - La salida es una ALERTA para revisión humana, nunca una acción automática.

import type { DetectionBatch, Detection, Severity } from '@percepta/contracts';
import { isWithinSchedule, type ModuleSchedule } from './schedule';

export interface RuleConfig {
  /** Clases del modelo que disparan la regla (ej. ['no_helmet']). */
  triggerClasses: string[];
  eventType: string;
  severity: Severity;
  minConfidence: number;
  /** Frames consecutivos cumpliendo la condición antes de alertar (anti-parpadeo). */
  minPersistenceFrames: number;
  /** Silencio entre alertas del mismo track (anti-spam). */
  cooldownSeconds: number;
  /** Zonas donde aplica; vacío/ausente = toda la imagen. */
  zones?: string[];
  schedule?: ModuleSchedule;
}

export interface EventCandidate {
  occurredAt: string;
  organizationId: string;
  siteId: string;
  cameraId: string;
  aiModuleId: string;
  moduleKey: string;
  moduleVersion: string;
  eventType: string;
  eventClass: 'alert';
  severity: Severity;
  confidence: number;
  dedupKey: string;
  trackId: number;
  zoneIds: string[];
  detection: Record<string, unknown>;
}

interface TrackState {
  consecutive: number;
  lastFiredMs: number;
}

export class RuleEvaluator {
  /** Estado por track: `${cameraId}:${moduleKey}:${trackId}`. En producción se respalda en Redis. */
  private readonly tracks = new Map<string, TrackState>();

  evaluate(batch: DetectionBatch, cfg: RuleConfig, now: Date = new Date(batch.capturedAt)): EventCandidate[] {
    if (!isWithinSchedule(cfg.schedule, now)) {
      this.resetScope(batch);
      return [];
    }

    const candidates: EventCandidate[] = [];
    const seenKeys = new Set<string>();

    for (const det of batch.detections) {
      if (!this.matches(det, cfg)) continue;

      const key = this.trackKey(batch, det.trackId);
      seenKeys.add(key);
      const state = this.tracks.get(key) ?? { consecutive: 0, lastFiredMs: 0 };
      state.consecutive += 1;
      this.tracks.set(key, state);

      const nowMs = now.getTime();
      const cooledDown = nowMs - state.lastFiredMs >= cfg.cooldownSeconds * 1000;
      if (state.consecutive >= cfg.minPersistenceFrames && cooledDown) {
        state.lastFiredMs = nowMs;
        candidates.push(this.toCandidate(batch, det, cfg, now));
      }
    }

    // La persistencia exige frames CONSECUTIVOS: los tracks de este scope que no
    // aparecieron (o dejaron de cumplir) en este frame se reinician.
    this.resetScope(batch, seenKeys);
    return candidates;
  }

  private matches(det: Detection, cfg: RuleConfig): boolean {
    if (!cfg.triggerClasses.includes(det.classLabel)) return false;
    if (det.confidence < cfg.minConfidence) return false;
    if (cfg.zones?.length) {
      if (!det.inZones.some((z) => cfg.zones!.includes(z))) return false;
    }
    return true;
  }

  private toCandidate(
    batch: DetectionBatch,
    det: Detection,
    cfg: RuleConfig,
    now: Date,
  ): EventCandidate {
    // Ventana de dedup alineada al cooldown (CONTRACTS §5: hash(camera,module,type,track,ventana))
    const windowMs = Math.max(cfg.cooldownSeconds, 1) * 1000;
    const bucket = Math.floor(now.getTime() / windowMs);
    return {
      occurredAt: now.toISOString(),
      organizationId: batch.organizationId,
      siteId: batch.siteId,
      cameraId: batch.cameraId,
      aiModuleId: batch.aiModuleId,
      moduleKey: batch.moduleKey,
      moduleVersion: batch.moduleVersion,
      eventType: cfg.eventType,
      eventClass: 'alert',
      severity: cfg.severity,
      confidence: det.confidence,
      dedupKey: `${batch.cameraId}:${batch.moduleKey}:${cfg.eventType}:${det.trackId}:${bucket}`,
      trackId: det.trackId,
      zoneIds: det.inZones,
      detection: {
        classLabel: det.classLabel,
        bbox: det.bbox,
        attributes: det.attributes,
        frameSeq: batch.frameSeq,
        frameRef: batch.frameRef,
      },
    };
  }

  private trackKey(batch: DetectionBatch, trackId: number): string {
    // trackId -1 = módulo sin tracking → estado único por cámara+módulo
    return `${batch.cameraId}:${batch.moduleKey}:${trackId}`;
  }

  private resetScope(batch: DetectionBatch, except: Set<string> = new Set()): void {
    const prefix = `${batch.cameraId}:${batch.moduleKey}:`;
    for (const [key, state] of this.tracks) {
      if (key.startsWith(prefix) && !except.has(key)) {
        state.consecutive = 0;
      }
    }
  }
}
