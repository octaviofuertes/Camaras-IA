#!/usr/bin/env node
/**
 * Levanta el stack completo de Percepta en un solo comando.
 *
 *   node tools/start-all.js
 *
 * Libera los puertos, arranca identity, device, event, media, ai-worker y el
 * dashboard en orden, esperando a que cada uno responda. El dashboard inicia
 * sesión solo contra identity-service: no hay que pegar ningún token.
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

/** Puertos que ocupa el stack. Se liberan antes de arrancar. */
const PORTS = { identity: 3001, device: 3003, event: 3004, analytics: 3005, 'ai-worker': 3010, media: 3020, web: 4200 };

/**
 * Mata lo que esté escuchando en un puerto del stack.
 *
 * Sin esto, una corrida anterior a medio cerrar (o un servicio levantado a mano)
 * hace que TODO falle con EADDRINUSE, y el mensaje real queda enterrado entre
 * decenas de líneas de log.
 */
function freePort(port) {
  try {
    // Sin `-p tcp`: ese filtro deja fuera los sockets IPv6, y `ng serve` escucha
    // en [::1]:4200. Con él, el dev-server quedaba vivo entre reinicios y seguía
    // sirviendo una configuración de proxy vieja.
    const out =
      process.platform === 'win32'
        ? execSync(`netstat -ano | findstr LISTENING | findstr :${port}`, { stdio: ['ignore', 'pipe', 'ignore'] })
            .toString()
        : execSync(`lsof -ti tcp:${port}`, { stdio: ['ignore', 'pipe', 'ignore'] }).toString();

    const pids = new Set(
      out
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter(Boolean)
        .map((l) => (process.platform === 'win32' ? l.split(/\s+/).pop() : l))
        .filter((pid) => pid && pid !== '0' && Number(pid) !== process.pid),
    );

    for (const pid of pids) {
      try {
        execSync(process.platform === 'win32' ? `taskkill /F /PID ${pid}` : `kill -9 ${pid}`, { stdio: 'ignore' });
        console.log(`  puerto ${port}: liberado (proceso ${pid})`);
      } catch {
        console.log(`  puerto ${port}: NO se pudo liberar el proceso ${pid} — cerralo a mano`);
      }
    }
    return pids.size > 0;
  } catch {
    return false; // nada escuchando: el caso normal
  }
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

  console.log('Liberando puertos…');
  let freed = false;
  for (const port of Object.values(PORTS)) freed = freePort(port) || freed;
  if (!freed) console.log('  (todos libres)');
  else await new Promise((r) => setTimeout(r, 1500));

  const svcToken = token('service');

  console.log('\nLevantando Percepta…\n');

  run('identity', 'node', ['apps/identity-service/dist/main.js']);
  run('device', 'node', ['apps/device-service/dist/main.js']);
  run('event', 'node', ['apps/event-service/dist/main.js']);
  // Informes: serie de tiempo de actividad por puesto. Va aparte de eventos
  // a propósito — una medición no es una alerta y no comparte su cola.
  run('analytics', 'node', ['apps/analytics-service/dist/main.js'], { PORT: '3005' });

  const identityOk = await waitPort(3001, '/api/v1/health', 25000);
  const deviceOk = await waitPort(3003, '/api/v1/health', 25000);
  const eventOk = await waitPort(3004, '/api/v1/health', 25000);
  const analyticsOk = await waitPort(3005, '/api/v1/health', 25000);
  if (!identityOk || !deviceOk || !eventOk || !analyticsOk) {
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
    ANALYTICS_SERVICE_URL: 'http://127.0.0.1:3005',
    DEVICE_SERVICE_URL: 'http://127.0.0.1:3003',
    // Una caída dura entre medio segundo y uno: a 2 fps entran 1-2 frames y no
    // hay con qué medir la velocidad de descenso. 6 fps da ~5 frames de caída,
    // que es el mínimo para distinguirla de agacharse.
    PIPELINE_FPS: process.env.PIPELINE_FPS || '6',
    PORT: '3010',
  });

  // `ng serve` necesita correr DENTRO del proyecto Angular, no en la raíz del monorepo.
  run('web', 'npx', ['ng', 'serve', '--port', '4200'], { NG_CLI_ANALYTICS: 'false' }, path.join(ROOT, 'apps', 'web'));

  const webOk = await waitPort(4200, '/', 120000);

  console.log('\n' + '─'.repeat(66));
  console.log(webOk ? '  Percepta levantado:  http://localhost:4200' : '  El dashboard todavía está compilando…');
  console.log('─'.repeat(66));
  console.log('\n  La sesión se inicia sola con admin@percepta.local.');
  console.log('  Ctrl+C detiene todo.\n');
})();
