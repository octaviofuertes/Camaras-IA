#!/usr/bin/env node
/**
 * Monitor en vivo del módulo de actividad por puesto.
 *
 *   node tools/probar-informes.js
 *
 * Muestra lo que el módulo está acumulando AHORA en la ventana abierta, y las
 * muestras que ya cerró. Sirve para comprobar que mide bien sin esperar a que
 * el informe se llene: la primera muestra tarda un minuto entero, y sin esto
 * ese minuto se pasa mirando una pantalla vacía sin saber si funciona.
 */
const http = require('node:http');

function get(port, path) {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path, timeout: 3000 }, (res) => {
      let b = '';
      res.on('data', (d) => (b += d));
      res.on('end', () => {
        try {
          resolve(JSON.parse(b));
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

const seg = (s) => (s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${s.toFixed(1)}s`);

let ultimo = '';

async function tick() {
  const st = await get(3010, '/modules/workstation-activity/state');
  if (!st || st.error) {
    process.stdout.write(`\r  ${st ? st.error : 'ai-worker no responde en :3010'}          `);
    return;
  }

  const enCurso = st.enCurso || {};
  const zonas = enCurso.zonas || [];
  if (!zonas.length) {
    process.stdout.write('\r  el módulo todavía no observó ningún frame          ');
    return;
  }

  const linea = zonas
    .map(
      (z) =>
        `${z.nombre}: ocupado ${seg(z.ocupadoS)} | vacío ${seg(z.vacioS)} | teléfono ${seg(z.telefonoS)}` +
        (z.sinCoberturaS > 0 ? ` | SIN VER ${seg(z.sinCoberturaS)}` : ''),
    )
    .join('\n  ');

  const cabecera =
    `ventana abierta hace ${enCurso.ventanaAbiertaS}s  ·  ` +
    `${st.muestrasEmitidas} muestra(s) ya guardada(s)`;

  const texto = `${cabecera}\n  ${linea}`;
  if (texto !== ultimo) {
    console.clear();
    console.log('Actividad por puesto — Ctrl+C para salir\n');
    console.log('  ' + cabecera + '\n');
    console.log('  ' + linea);
    console.log(
      '\n  La ventana se cierra al minuto y ahí aparece en Informes.' +
        '\n  Ponete frente a la cámara y salí del cuadro: los contadores tienen que moverse.',
    );
    ultimo = texto;
  }
}

console.log('Conectando con el módulo…');
setInterval(tick, 1000);
tick();
