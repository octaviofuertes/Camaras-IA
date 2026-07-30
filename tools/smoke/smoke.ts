// Smoke test del slice MVP: RuleEvaluator (rules-engine) + workflow (event-service).
// Se ejecuta con node puro tras compilar (sin jest): `npx tsc -p tmp/tsconfig.smoke.json && node tmp/build/tmp/smoke.js`

import assert from 'node:assert/strict';
import type { DetectionBatch, Detection } from '@percepta/contracts';
import { RuleEvaluator, type RuleConfig } from '../../apps/rules-engine/src/domain/evaluator';
import { isWithinSchedule } from '../../apps/rules-engine/src/domain/schedule';
import {
  acknowledgeEvent,
  resolveEvent,
  WorkflowError,
  type ReviewableEvent,
} from '../../apps/event-service/src/domain/workflow';

let passed = 0;
function ok(name: string, fn: () => void): void {
  fn();
  passed += 1;
  console.log(`  ✓ ${name}`);
}

// ── Fixtures ────────────────────────────────────────────────────────────────
const det = (over: Partial<Detection> = {}): Detection => ({
  classLabel: 'no_helmet',
  classId: 2,
  confidence: 0.87,
  bbox: { x: 0.55, y: 0.3, w: 0.09, h: 0.5 },
  trackId: 42,
  keypoints: [],
  inZones: ['zone-dock-2'],
  attributes: {},
  ...over,
});

const batch = (frameSeq: number, capturedAt: string, detections: Detection[]): DetectionBatch => ({
  schemaVersion: '1.0.0',
  organizationId: 'org-demo',
  siteId: 'site-mendoza',
  cameraId: 'cam-deposito-2',
  aiModuleId: 'mod-helmet',
  moduleKey: 'helmet-detection',
  moduleVersion: '1.2.0',
  frameSeq,
  capturedAt,
  inferenceMs: 12.5,
  detections,
  frameRef: { ringBufferKey: `rb-${frameSeq}`, width: 1920, height: 1080 },
});

// Config equivalente a camera_module_configs.config del módulo helmet-detection
const cfg: RuleConfig = {
  triggerClasses: ['no_helmet'],
  eventType: 'ppe.helmet_missing',
  severity: 'high',
  minConfidence: 0.6,
  minPersistenceFrames: 3,
  cooldownSeconds: 300,
  zones: ['zone-dock-2'],
  schedule: {
    days: ['mon', 'tue', 'wed', 'thu', 'fri'],
    windows: [['06:00', '22:00']],
    tz: 'America/Argentina/Mendoza',
  },
};

// 2026-07-30 es jueves. 18:00Z = 15:00 en Mendoza (UTC-3) → dentro de ventana.
const T0 = Date.parse('2026-07-30T18:00:00.000Z');
const at = (offsetMs: number): string => new Date(T0 + offsetMs).toISOString();

console.log('▸ schedule.ts');
ok('ventana activa (jueves 15:00 Mendoza)', () => {
  assert.equal(isWithinSchedule(cfg.schedule, new Date(T0)), true);
});
ok('fuera de ventana (03:30 Mendoza)', () => {
  assert.equal(isWithinSchedule(cfg.schedule, new Date('2026-07-30T06:30:00Z')), false);
});
ok('día excluido (domingo)', () => {
  assert.equal(isWithinSchedule(cfg.schedule, new Date('2026-08-02T18:00:00Z')), false);
});
ok('ventana que cruza medianoche', () => {
  const nightly = { windows: [['20:00', '06:00']] as [string, string][], tz: 'America/Argentina/Mendoza' };
  assert.equal(isWithinSchedule(nightly, new Date('2026-07-31T02:00:00Z')), true);  // 23:00 jueves
  assert.equal(isWithinSchedule(nightly, new Date('2026-07-30T18:00:00Z')), false); // 15:00
});
ok('schedule vacío = siempre activa', () => {
  assert.equal(isWithinSchedule(undefined, new Date(T0)), true);
});

console.log('▸ evaluator.ts');
ok('persistencia: alerta recién al 3er frame consecutivo', () => {
  const ev = new RuleEvaluator();
  assert.equal(ev.evaluate(batch(1, at(0), [det()]), cfg).length, 0);
  assert.equal(ev.evaluate(batch(2, at(200), [det()]), cfg).length, 0);
  const fired = ev.evaluate(batch(3, at(400), [det()]), cfg);
  assert.equal(fired.length, 1);
  assert.equal(fired[0].eventType, 'ppe.helmet_missing');
  assert.equal(fired[0].eventClass, 'alert');
  assert.equal(fired[0].confidence, 0.87);
  assert.match(fired[0].dedupKey, /^cam-deposito-2:helmet-detection:ppe\.helmet_missing:42:/);
});
ok('cooldown: el mismo track no re-alerta dentro de la ventana', () => {
  const ev = new RuleEvaluator();
  for (let i = 1; i <= 3; i++) ev.evaluate(batch(i, at(i * 200), [det()]), cfg);
  // frames 4..6 siguen cumpliendo pero dentro del cooldown de 300s
  let extra = 0;
  for (let i = 4; i <= 6; i++) extra += ev.evaluate(batch(i, at(i * 200), [det()]), cfg).length;
  assert.equal(extra, 0);
  // pasados 301s, re-alerta
  const later = ev.evaluate(batch(7, at(301_000), [det()]), cfg);
  assert.equal(later.length, 1);
});
ok('oclusión: un frame sin el track reinicia la persistencia', () => {
  const ev = new RuleEvaluator();
  ev.evaluate(batch(1, at(0), [det()]), cfg);
  ev.evaluate(batch(2, at(200), [det()]), cfg);
  ev.evaluate(batch(3, at(400), []), cfg); // el casco se ocluyó / track perdido
  assert.equal(ev.evaluate(batch(4, at(600), [det()]), cfg).length, 0); // consecutivos = 1, no 4
});
ok('confianza bajo umbral no cuenta', () => {
  const ev = new RuleEvaluator();
  for (let i = 1; i <= 5; i++) {
    assert.equal(ev.evaluate(batch(i, at(i * 200), [det({ confidence: 0.4 })]), cfg).length, 0);
  }
});
ok('fuera de las zonas configuradas no cuenta', () => {
  const ev = new RuleEvaluator();
  for (let i = 1; i <= 5; i++) {
    assert.equal(ev.evaluate(batch(i, at(i * 200), [det({ inZones: ['zone-otra'] })]), cfg).length, 0);
  }
});
ok('fuera de horario: se suprime y se reinicia el estado', () => {
  const ev = new RuleEvaluator();
  const night = '2026-07-30T06:30:00.000Z'; // 03:30 Mendoza
  for (let i = 1; i <= 5; i++) {
    assert.equal(ev.evaluate(batch(i, night, [det()]), cfg).length, 0);
  }
});
ok('clase no disparadora (person con casco) no alerta', () => {
  const ev = new RuleEvaluator();
  for (let i = 1; i <= 5; i++) {
    assert.equal(ev.evaluate(batch(i, at(i * 200), [det({ classLabel: 'person', classId: 0 })]), cfg).length, 0);
  }
});

console.log('▸ workflow.ts (human-in-the-loop)');
const newEvent: ReviewableEvent = { id: 'evt-1', status: 'new', eventClass: 'alert' };
ok('flujo feliz: new → acknowledged → confirmed, con revisor registrado', () => {
  const acked = acknowledgeEvent(newEvent, 'user-operador-1');
  assert.equal(acked.status, 'acknowledged');
  assert.equal(acked.reviewedBy, 'user-operador-1');
  const confirmed = resolveEvent(acked, 'confirmed', 'user-operador-1', 'Persona sin casco verificada');
  assert.equal(confirmed.status, 'confirmed');
  assert.equal(confirmed.reviewNote, 'Persona sin casco verificada');
});
ok('falso positivo queda registrado (feedback a MLOps)', () => {
  const acked = acknowledgeEvent(newEvent, 'user-operador-1');
  assert.equal(resolveEvent(acked, 'false_positive', 'user-operador-1').status, 'false_positive');
});
ok('no se puede resolver sin reconocer primero (new → confirmed)', () => {
  assert.throws(
    () => resolveEvent(newEvent, 'confirmed', 'user-operador-1'),
    (e: unknown) => e instanceof WorkflowError && e.code === 'INVALID_TRANSITION',
  );
});
ok('sin revisor humano no hay transición', () => {
  assert.throws(
    () => acknowledgeEvent(newEvent, ''),
    (e: unknown) => e instanceof WorkflowError && e.code === 'REVIEWER_REQUIRED',
  );
});
ok('un evento resuelto es terminal', () => {
  const done = resolveEvent(acknowledgeEvent(newEvent, 'u1'), 'dismissed', 'u1');
  assert.throws(
    () => acknowledgeEvent(done, 'u1'),
    (e: unknown) => e instanceof WorkflowError && e.code === 'INVALID_TRANSITION',
  );
});
ok('la telemetría no entra al workflow de revisión', () => {
  const tele: ReviewableEvent = { id: 'evt-2', status: 'new', eventClass: 'telemetry' };
  assert.throws(
    () => acknowledgeEvent(tele, 'u1'),
    (e: unknown) => e instanceof WorkflowError && e.code === 'NOT_REVIEWABLE',
  );
});

console.log(`\n${passed} escenarios OK — slice MVP verificado.`);
