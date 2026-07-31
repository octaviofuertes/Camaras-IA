#!/usr/bin/env node
/**
 * Demo del workflow human-in-the-loop contra event-service.
 *
 * Recorre el ciclo completo de una alerta (nueva -> reconocida -> resuelta) y
 * de paso prueba los controles de seguridad. Requiere:
 *   1. infra levantada  (pnpm infra:up)
 *   2. esquema aplicado (pnpm db:migrate)
 *   3. event-service corriendo en :3004
 *
 * Uso: node tools/demo-events.js
 */
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const ROOT = path.join(__dirname, '..');
const BASE = process.env.EVENT_SERVICE_URL || 'http://localhost:3004/api/v1';
const ORG = '00000000-0000-4000-b000-000000000001';

for (const line of fs.readFileSync(path.join(ROOT, '.env'), 'utf8').split(/\r?\n/)) {
  const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/);
  if (m && !process.env[m[1]]) process.env[m[1]] = m[2];
}

const token = (role, org = ORG) =>
  execFileSync('node', [path.join(__dirname, 'dev-token.js'), role, org], { encoding: 'utf8' }).trim();

async function call(method, url, tok, body) {
  const res = await fetch(`${BASE}${url}`, {
    method,
    headers: { Authorization: `Bearer ${tok}`, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  let payload;
  try {
    payload = await res.json();
  } catch {
    payload = null;
  }
  return { status: res.status, body: payload };
}

const ok = (c) => `\x1b[32m${c}\x1b[0m`;
const bad = (c) => `\x1b[31m${c}\x1b[0m`;
const dim = (s) => `\x1b[90m${s}\x1b[0m`;
const check = (cond, txt) => console.log(`  ${cond ? ok('✓') : bad('✗')} ${txt}`);

async function main() {
  const operator = token('operator');
  const auditor = token('auditor');
  const otroTenant = token('operator', '00000000-0000-4000-b000-00000000dead');

  console.log('\n\x1b[1mDemo — workflow human-in-the-loop de Percepta\x1b[0m');
  console.log(dim(`${BASE}\n`));

  // ── 1. Listado ────────────────────────────────────────────────────────────
  console.log('\x1b[1m1. Alertas visibles para el operador\x1b[0m');
  const list = await call('GET', '/events?limit=5', operator);
  if (list.status !== 200) {
    console.log(bad(`  No se pudo listar (HTTP ${list.status}). ¿Está corriendo event-service?`));
    console.log(dim(`  ${JSON.stringify(list.body)}`));
    process.exit(1);
  }
  console.log(`  ${list.body.total} evento(s) en total`);
  for (const e of list.body.items) {
    console.log(
      dim(`  · ${e.eventType}  ${(e.confidence * 100).toFixed(1)}%  [${e.status}]  ${e.occurredAt}`),
    );
  }
  const target = list.body.items.find((e) => e.status === 'new');
  if (!target) {
    console.log(
      `\n  ${dim('No hay alertas en estado "new" para recorrer el workflow.')}\n` +
        `  ${dim('Creá una con:')} pnpm demo:seed-event\n`,
    );
    return;
  }

  // ── 2. Controles de seguridad ─────────────────────────────────────────────
  console.log('\n\x1b[1m2. Controles de seguridad\x1b[0m');

  const salteo = await call('POST', `/events/${target.id}/resolve`, operator, { resolution: 'confirmed' });
  check(salteo.status === 422, `Saltear la revisión humana → ${salteo.status} (esperado 422)`);

  const sinPermiso = await call('POST', `/events/${target.id}/acknowledge`, auditor);
  check(sinPermiso.status === 403, `Auditor (solo lectura) intenta reconocer → ${sinPermiso.status} (esperado 403)`);

  const ajeno = await call('GET', `/events/${target.id}`, otroTenant);
  check(ajeno.status === 404, `Otro tenant pide el evento por ID → ${ajeno.status} (esperado 404, no 403)`);

  const sinToken = await fetch(`${BASE}/events`).then((r) => r.status);
  check(sinToken === 401, `Sin token → ${sinToken} (esperado 401)`);

  // ── 3. Workflow ───────────────────────────────────────────────────────────
  console.log('\n\x1b[1m3. Ciclo de revisión humana\x1b[0m');
  console.log(dim(`  evento ${target.id}`));

  const ack = await call('POST', `/events/${target.id}/acknowledge`, operator, {
    note: 'Revisado por el operador de turno',
  });
  check(ack.status === 201 || ack.status === 200, `nuevo → reconocido  (revisor: ${ack.body?.reviewedBy ?? '—'})`);

  const res = await call('POST', `/events/${target.id}/resolve`, operator, {
    resolution: 'false_positive',
    note: 'Casco presente, oclusión parcial de la cámara',
  });
  check(
    res.body?.status === 'false_positive',
    `reconocido → ${res.body?.status ?? 'error'}  (alimenta el reentrenamiento del modelo)`,
  );

  console.log(
    `\n${dim('La alerta nunca cambió de estado sin un humano identificado: es una')} ` +
      `${dim('invariante del esquema (events_human_review_chk), no una convención.')}\n`,
  );
}

main().catch((e) => {
  console.error(bad(`\nError: ${e.message}`));
  console.error(dim('¿Está corriendo event-service en :3004 y la infra arriba?\n'));
  process.exit(1);
});
