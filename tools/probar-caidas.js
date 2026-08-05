#!/usr/bin/env node
/**
 * Monitor en vivo del detector de caídas.
 *
 *   node tools/probar-caidas.js
 *
 * Muestra, para cada persona que la cámara está siguiendo, en qué estado la ve
 * el detector y cuántos segundos lleva en el suelo. Sirve para probar el módulo
 * sin adivinar: ponete frente a la cámara y mirá cómo cambia.
 *
 * Cómo probarlo:
 *   1. Parate frente a la cámara       -> "de pie"
 *   2. Agachate y volvé a levantarte   -> pasa por "cayendo" pero NO alerta
 *   3. Acostate en el piso y quedate   -> "en el suelo", y a los 3 s ALERTA
 */
const http = require('node:http');

const AI = { host: '127.0.0.1', port: 3010 };
const ETIQUETAS = {
  upright: 'de pie',
  falling: 'DESCENDIENDO',
  impact: 'IMPACTO (confirmando)',
  alerted: '*** CAIDA DETECTADA ***',
  recovered: 'se levanto',
};

function get(path) {
  return new Promise((resolve) => {
    const req = http.get({ ...AI, path, timeout: 2500 }, (res) => {
      let body = '';
      res.on('data', (d) => (body += d));
      res.on('end', () => {
        try {
          resolve(JSON.parse(body));
        } catch {
          resolve(null);
        }
      });
    });
    req.on('error', () => resolve(null));
    req.on('timeout', () => {
      req.destroy();
      resolve(null);
    });
  });
}

let ultimo = '';
let alertasVistas = 0;

async function tick() {
  const st = await get('/modules/fall-detection/state');
  if (!st) {
    process.stdout.write('\r  ai-worker no responde en :3010                    ');
    return;
  }
  if (st.error) {
    process.stdout.write(`\r  ${st.error}                    `);
    return;
  }

  const gente = st.people || {};
  const ids = Object.keys(gente);
  let linea;
  if (!ids.length) {
    linea = 'no hay nadie a la vista';
  } else {
    linea = ids
      .map((id) => {
        const p = gente[id];
        const etiqueta = ETIQUETAS[p.state] || p.state;
        // Las tres señales que deciden. Si una caída no se detecta, acá se ve
        // cuál no llegó al umbral.
        const base = p.alturaBase == null ? 'aprendiendo altura' : `altura ${Math.round((p.colapso ?? 1) * 100)}%`;
        const vel = `desc ${p.velocidad ?? 0}`;
        const seg = p.segundosAbajo > 0 ? ` ${p.segundosAbajo}s abajo` : '';
        return `p${id}: ${etiqueta} [${base}, ${vel}]${seg}`;
      })
      .join('  |  ');
  }

  // Sólo se imprime cuando algo cambia: así el historial queda legible.
  if (linea !== ultimo) {
    const hora = new Date().toLocaleTimeString('es-AR', { hour12: false });
    console.log(`[${hora}] ${linea}`);
    ultimo = linea;
  }

  const enAlerta = ids.filter((id) => gente[id].state === 'alerted').length;
  if (enAlerta > alertasVistas) {
    console.log('\n  ╔══════════════════════════════════════════╗');
    console.log('  ║  CAIDA DETECTADA — revisá el dashboard   ║');
    console.log('  ╚══════════════════════════════════════════╝\n');
  }
  alertasVistas = enAlerta;
}

console.log('Monitor del detector de caídas — Ctrl+C para salir\n');
console.log('  1. Parate frente a la cámara       -> "de pie"');
console.log('  2. Agachate y levantate rápido     -> NO debe alertar');
console.log('  3. Acostate y quedate quieto 3 s   -> debe ALERTAR\n');
console.log('─'.repeat(60));

setInterval(tick, 500);
tick();
