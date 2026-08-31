#!/usr/bin/env node
/**
 * Mueve los datos pesados del proyecto a otro disco, sin romper nada.
 *
 *   node tools/mover-a-disco.js            # muestra qué haría, no toca nada
 *   node tools/mover-a-disco.js --hacerlo
 *   node tools/mover-a-disco.js --destino E:\PerceptaData --hacerlo
 *
 * ── Por qué con enlaces y no cambiando rutas en el código ───────────────────
 *
 * Los datasets, las corridas de entrenamiento y los modelos son varios GB y no
 * tienen por qué vivir en el disco del sistema. Pero están referenciados desde
 * scripts, configuraciones y el propio ultralytics, así que cambiar la ruta en
 * cada lugar es una lista larga de la que siempre se olvida uno — y el que se
 * olvida vuelve a escribir en C: sin que nadie se entere.
 *
 * Con un junction de Windows la carpeta sigue existiendo donde estaba: el
 * código la abre igual, pero los bytes están en el otro disco. No hace falta
 * ser administrador (un junction de directorio no lo pide, a diferencia de un
 * symlink) y se deshace borrando el enlace y moviendo la carpeta de vuelta.
 *
 * ── Qué mueve y qué no ──────────────────────────────────────────────────────
 *
 * Sólo datos regenerables y pesados: datasets, corridas, modelos entrenados,
 * salidas de prueba. NO mueve el código, ni `.venv`, ni `node_modules`: esos
 * tienen rutas absolutas adentro y moverlos rompe el entorno.
 */
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const RAIZ = path.join(__dirname, '..');

/** Carpetas que vale la pena mudar, con por qué. */
const CANDIDATAS = [
  ['training/data', 'datasets descargados (caídas)'],
  ['training/ppe/data', 'dataset de EPP'],
  ['training/ppe/corridas', 'corridas de entrenamiento'],
  ['training/ppe/corridas_epoca2', 'corrida vieja de entrenamiento'],
  ['training/ppe/salida', 'imágenes de prueba del módulo'],
  ['training/models', 'modelos entrenados'],
  ['runs', 'evaluaciones de ultralytics'],
  ['evidencia', 'clips y capturas de eventos'],
];

function tamano(dir) {
  let total = 0;
  const pila = [dir];
  while (pila.length) {
    const d = pila.pop();
    let entradas;
    try {
      entradas = fs.readdirSync(d, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const e of entradas) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) pila.push(p);
      else if (e.isFile()) {
        try {
          total += fs.statSync(p).size;
        } catch { /* archivo que desapareció mientras se contaba */ }
      }
    }
  }
  return total;
}

const gb = (b) => (b / 1024 ** 3).toFixed(2);

function esEnlace(p) {
  try {
    return fs.lstatSync(p).isSymbolicLink();
  } catch {
    return false;
  }
}

function mover(origen, destino) {
  fs.mkdirSync(path.dirname(destino), { recursive: true });
  // robocopy /MOVE copia y borra el origen. Sus códigos de salida por debajo
  // de 8 son éxito (1 = copió archivos), así que no se puede confiar en el
  // habitual "cero es que anduvo".
  try {
    execFileSync('robocopy', [origen, destino, '/E', '/MOVE', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:1', '/W:1'], {
      stdio: 'pipe',
    });
  } catch (err) {
    const code = err.status ?? 16;
    if (code >= 8) throw new Error(`robocopy falló con código ${code}`);
  }
  // Queda la carpeta vacía; se borra para poder poner el enlace en su lugar.
  try {
    fs.rmdirSync(origen);
  } catch { /* ya no está */ }
  execFileSync('cmd', ['/c', 'mklink', '/J', origen, destino], { stdio: 'pipe' });
}

function main() {
  const args = process.argv.slice(2);
  const hacerlo = args.includes('--hacerlo');
  const i = args.indexOf('--destino');
  const destinoBase = i >= 0 ? args[i + 1] : 'D:\\PerceptaData';

  const raizDestino = path.parse(destinoBase).root;
  if (!fs.existsSync(raizDestino)) {
    console.error(`No existe el disco ${raizDestino}`);
    return 1;
  }

  console.log(`Destino: ${destinoBase}\n`);
  let total = 0;
  const aMover = [];

  for (const [rel, para] of CANDIDATAS) {
    const origen = path.join(RAIZ, rel);
    if (!fs.existsSync(origen)) continue;
    if (esEnlace(origen)) {
      console.log(`  ya movida   ${rel.padEnd(30)} (${para})`);
      continue;
    }
    const bytes = tamano(origen);
    total += bytes;
    aMover.push([origen, path.join(destinoBase, rel.replace(/\//g, path.sep)), rel, bytes]);
    console.log(`  ${gb(bytes).padStart(7)} GB  ${rel.padEnd(30)} ${para}`);
  }

  if (!aMover.length) {
    console.log('\nNo queda nada por mover.');
    return 0;
  }
  console.log(`\n  ${gb(total)} GB en total`);

  if (!hacerlo) {
    console.log('\nEsto fue una simulación: no se tocó nada.');
    console.log('Para hacerlo de verdad:  node tools/mover-a-disco.js --hacerlo');
    return 0;
  }

  console.log('\nMoviendo…');
  for (const [origen, destino, rel, bytes] of aMover) {
    process.stdout.write(`  ${rel} (${gb(bytes)} GB)… `);
    try {
      mover(origen, destino);
      console.log('listo');
    } catch (err) {
      console.log(`FALLÓ: ${err.message}`);
      console.log('    (la carpeta quedó como estaba o a medio copiar en el destino;'
        + ' revisá antes de volver a intentar)');
      return 1;
    }
  }
  console.log(`\nListo. ${gb(total)} GB liberados en C:.`);
  console.log('Las carpetas siguen existiendo donde estaban: son enlaces al otro disco.');
  return 0;
}

process.exit(main());
