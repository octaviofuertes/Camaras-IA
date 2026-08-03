#!/usr/bin/env node
/**
 * Levanta el stack completo de Percepta en un solo comando.
 *
 *   node tools/start-all.js
 *
 * Arranca, en orden: device-service, event-service, media-service, ai-worker y
 * el dashboard. Emite un token de servicio para el pipeline y muestra al final
 * un token de operador listo para pegar en el navegador.
 *
 * Requiere que la infraestructura esté levantada (pnpm infra:up).
 */
const { spawn, execSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');

const ROOT = path.join(__dirname, '..');
const VENV = path.join(ROOT, '.venv', 'Scripts', 'python.exe');

// Cargar .env
const envPath = path.join(ROOT, '.env');
if (fs.existsSync(envPath)) {
  for (const line of fs.readFileSync(envPath, 'utf8').split(/\r?\n/)) {
    if (line.trim().startsWith('#')) continue;
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/);
    if (!m) continue;
    // Descartar el comentario al final de la línea: sin esto,
    // "AI_WORKER_DEVICE=cpu   # cpu | cuda:0" define el device con el comentario incluido.
    process.env[m[1]] = m[2].replace(/\s+#.*$/, '').trim();
  }
}

function token(role) {
  return execSync(`node "${path.join(__dirname, 'dev-token.js')}" ${role}`, { cwd: ROOT }).toString().trim();
}

/** Espera a que un puerto responda (o se rinde). */
function waitPort(port, pathname, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve) => {
    const probe = () => {
      const req = http.get({ host: '127.0.0.1', port, path: pathname, timeout: 1500 }, (res) => {
        res.resume();
        resolve(true);
      });
      req.on('error', () => (Date.now() > deadline ? resolve(false) : setTimeout(probe, 800)));
      req.on('timeout', () => {
        req.destroy();
        Date.now() > deadline ? resolve(false) : setTimeout(probe, 800);
      });
    };
    probe();
  });
}

const procs = [];
function run(name, cmd, args, extraEnv = {}, cwd = ROOT) {
  const p = spawn(cmd, args, {
    cwd,
    env: { ...process.env, ...extraEnv },
    shell: process.platform === 'win32',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const tag = `[${name}]`;
  const pipe = (stream, isErr) =>
    stream.on('data', (d) => {
      const text = d.toString().trimEnd();
      if (!text) return;
      // Sólo se muestran arranque y errores: el resto es ruido.
      if (isErr || /escuchando|listening|started|Compiled|error|Error|ERROR/.test(text)) {
        console.log(`${tag} ${text.split('\n').slice(-3).join('\n' + tag + ' ')}`);
      }
    });
  pipe(p.stdout, false);
  pipe(p.stderr, true);
  p.on('exit', (code) => console.log(`${tag} terminó (código ${code})`));
  procs.push({ name, p });
  return p;
}

function shutdown() {
  console.log('\nDeteniendo servicios…');
  for (const { p } of procs) {
    try {
      p.kill();
    } catch {
      /* ya terminó */
    }
  }
  process.exit(0);
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

(async () => {
  if (!fs.existsSync(VENV)) {
    console.error(`No encuentro el entorno Python en ${VENV}. Creá el venv primero (ver EJECUTAR.md).`);
    process.exit(1);
  }

  const svcToken = token('service');

  console.log('Levantando Percepta…\n');

  run('device', 'node', ['apps/device-service/dist/main.js']);
  run('event', 'node', ['apps/event-service/dist/main.js']);

  const deviceOk = await waitPort(3003, '/api/v1/health', 25000);
  const eventOk = await waitPort(3004, '/api/v1/health', 25000);
  if (!deviceOk || !eventOk) {
    console.error(
      '\nLas APIs no respondieron. Causa habitual: la base no está levantada.\n' +
        'Abrí Docker Desktop y corré:  pnpm infra:up\n',
    );
  }

  run('media', VENV, ['-m', 'media_service.main'], {
    SERVICE_TOKEN: svcToken,
    DEVICE_SERVICE_URL: 'http://127.0.0.1:3003',
    PORT: '3020',
  });

  // El worker arranca después: necesita que media-service ya esté capturando.
  await waitPort(3020, '/health', 30000);

  run('ai-worker', VENV, ['-m', 'ai_worker.main'], {
    SERVICE_TOKEN: svcToken,
    AI_MODULES_PATH: './modules',
    MEDIA_SERVICE_URL: 'http://127.0.0.1:3020',
    EVENT_SERVICE_URL: 'http://127.0.0.1:3004',
    DEVICE_SERVICE_URL: 'http://127.0.0.1:3003',
    PIPELINE_FPS: process.env.PIPELINE_FPS || '2',
    PORT: '3010',
  });

  // `ng serve` necesita correr DENTRO del proyecto Angular, no en la raíz del monorepo.
  run('web', 'npx', ['ng', 'serve', '--port', '4200'], { NG_CLI_ANALYTICS: 'false' }, path.join(ROOT, 'apps', 'web'));

  const webOk = await waitPort(4200, '/', 120000);

  console.log('\n' + '─'.repeat(66));
  console.log(webOk ? '  Percepta levantado:  http://localhost:4200' : '  El dashboard todavía está compilando…');
  console.log('─'.repeat(66));
  console.log('\n  Pegá esto en la consola del navegador (F12) para iniciar sesión:\n');
  console.log(`localStorage.setItem('px_token','${token('org_admin')}');location.reload()`);
  console.log('\n  (el token dura 8 h)   ·   Ctrl+C para detener todo\n');
})();
