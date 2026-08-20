#!/usr/bin/env node
/**
 * Arranca uno de los servicios de Python para `pnpm dev`.
 *
 *   node tools/dev-python.js media
 *   node tools/dev-python.js ai-worker
 *
 * ── Por qué existe ─────────────────────────────────────────────────────────
 *
 * `pnpm dev` corre `turbo run dev`, y turbo sólo conoce los paquetes que
 * tienen `package.json`. media-service y ai-worker son Python, así que
 * quedaban afuera: el dashboard arrancaba, pedía `/media/cameras` y `/ai/…`,
 * y el proxy contestaba ECONNREFUSED. Desde la pantalla se veía como "las
 * cámaras no andan", que es un síntoma muy lejos de su causa.
 *
 * Se resuelve dándoles un `package.json` mínimo cuyo `dev` llama acá, para que
 * turbo los levante y los apague junto con el resto.
 *
 * ── Por qué no alcanza con llamar a python y listo ─────────────────────────
 *
 * Estos dos servicios necesitan un token de servicio para preguntarle a
 * device-service qué cámaras hay. Sin él, media-service se cae al archivo de
 * respaldo `cameras.json`, cuyas cámaras tienen otros identificadores: el
 * dashboard pide la cámara real y recibe 404. O sea que arrancaría, pero
 * mostrando cámaras que no existen.
 */
const { spawn, execSync } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');
const VENV = path.join(ROOT, '.venv', 'Scripts', 'python.exe');

// Mismo .env que usa start-all.js, con el mismo cuidado: descartar el
// comentario al final de la línea, si no `AI_WORKER_DEVICE=cpu  # cpu | cuda:0`
// define el device con el comentario adentro.
const envPath = path.join(ROOT, '.env');
if (fs.existsSync(envPath)) {
  for (const line of fs.readFileSync(envPath, 'utf8').split(/\r?\n/)) {
    if (line.trim().startsWith('#')) continue;
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/);
    if (m) process.env[m[1]] = m[2].replace(/\s+#.*$/, '').trim();
  }
}

const cual = process.argv[2];

const SERVICIOS = {
  media: {
    modulo: 'media_service.main',
    // Espera a device-service porque de ahí saca qué cámaras hay. Si arranca
    // antes, no puede preguntar y se cae al archivo de respaldo, cuyas cámaras
    // tienen OTROS identificadores: se queda con el dispositivo USB bajo un id
    // que no existe, y cuando device-service aparece hay que soltarlo y
    // reabrirlo. Ese manoteo del USB es lo que dejaba la cámara sin imagen.
    espera: { puerto: 3003, ruta: '/api/v1/health', nombre: 'device-service' },
    env: () => ({
      SERVICE_TOKEN: token(),
      DEVICE_SERVICE_URL: 'http://127.0.0.1:3003',
      PORT: process.env.MEDIA_SERVICE_PORT || '3020',
    }),
  },
  'ai-worker': {
    modulo: 'ai_worker.main',
    // El worker pide cuadros a media-service: sin él arranca, no encuentra
    // nada y se queda reintentando mientras ensucia el log.
    espera: { puerto: 3020, ruta: '/health', nombre: 'media-service' },
    env: () => ({
      SERVICE_TOKEN: token(),
      AI_MODULES_PATH: './modules',
      MEDIA_SERVICE_URL: 'http://127.0.0.1:3020',
      EVENT_SERVICE_URL: 'http://127.0.0.1:3004',
      ANALYTICS_SERVICE_URL: 'http://127.0.0.1:3005',
      DEVICE_SERVICE_URL: 'http://127.0.0.1:3003',
      // Una caída dura entre medio segundo y uno: a 2 fps entran 1-2 frames y
      // no hay con qué medir la velocidad de descenso.
      PIPELINE_FPS: process.env.PIPELINE_FPS || '6',
      PORT: process.env.AI_WORKER_PORT || '3010',
    }),
  },
};

function token() {
  return execSync(`node "${path.join(__dirname, 'dev-token.js')}" service`, {
    cwd: ROOT,
  }).toString().trim();
}

/** Contesta ese puerto? */
function responde(puerto, ruta) {
  return new Promise((listo) => {
    const req = http.get(
      { host: '127.0.0.1', port: puerto, path: ruta, timeout: 1500 },
      (res) => { res.resume(); listo(res.statusCode < 500); },
    );
    req.on('error', () => listo(false));
    req.on('timeout', () => { req.destroy(); listo(false); });
  });
}

async function esperar(espera, cual) {
  const limite = Date.now() + 90_000;
  if (await responde(espera.puerto, espera.ruta)) return;
  console.log(`[${cual}] esperando a ${espera.nombre} (:${espera.puerto})...`);
  while (Date.now() < limite) {
    await new Promise((r) => setTimeout(r, 1000));
    if (await responde(espera.puerto, espera.ruta)) return;
  }
  // Se arranca igual: mejor un servicio que anda a medias y lo dice, que uno
  // que no arranca y deja media pantalla en blanco sin explicar por que.
  console.warn(
    `[${cual}] ${espera.nombre} no respondio en 90 s. Arranco igual, pero puede ` +
      'levantar la configuracion de respaldo en vez de la real.',
  );
}

async function main() {
  const cfg = SERVICIOS[cual];
  if (!cfg) {
    console.error(`Uso: node tools/dev-python.js <${Object.keys(SERVICIOS).join('|')}>`);
    process.exit(2);
  }
  if (!fs.existsSync(VENV)) {
    console.error(
      `[${cual}] No está el entorno de Python en ${VENV}.\n` +
        '        Crealo con:  python -m venv .venv && .venv\\Scripts\\pip install -e apps/media-service -e apps/ai-worker',
    );
    // Se sale con 0 y no con error: que falte Python no tiene por qué tumbar
    // `pnpm dev` entero para quien está tocando sólo el frontend. El mensaje
    // queda arriba, que es lo que hace falta para darse cuenta.
    process.exit(0);
  }

  if (cfg.espera) await esperar(cfg.espera, cual);

  const hijo = spawn(VENV, ['-m', cfg.modulo], {
    cwd: ROOT,
    env: { ...process.env, ...cfg.env(), PYTHONUNBUFFERED: '1' },
    stdio: 'inherit',
  });

  const cortar = () => hijo.kill();
  process.on('SIGINT', cortar);
  process.on('SIGTERM', cortar);
  hijo.on('exit', (code) => process.exit(code ?? 0));
}

void main();
