import { Component, EventEmitter, OnInit, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { CamerasService, type ApiCamera } from '../../core/cameras.service';
import { ZonasService } from '../../core/zonas.service';
import {
  LIENZO,
  TIPOS,
  altoLienzo,
  claveDesde,
  type Piso,
  type TipoZona,
  type Zona,
} from '../../core/zonas';

/** Qué se está haciendo con el mouse apretado. */
type Gesto =
  | { que: 'marcar'; x0: number; y0: number }
  | { que: 'mover'; i: number; dx: number; dy: number }
  | { que: 'estirar'; i: number }
  | null;

/** Cuánto tiene que medir un arrastre para contar como área, en fracción. */
const MINIMO = 0.02;

/** Lo más grande que se acepta subir, ya achicada. */
const ANCHO_MAXIMO = 2000;

/**
 * El editor del plano del lugar.
 *
 * El plano NO se dibuja: se sube. Cada piso tiene su imagen —el render, el
 * plano del arquitecto, una foto del plano impreso— y lo único que se hace
 * encima es marcar con un rectángulo dónde queda cada área y cómo se llama.
 *
 * Dibujar el edificio a mano funcionaba mientras el lugar era una sola planta.
 * Con un subsuelo y dos pisos deja de funcionar: no hay forma de dibujar tres
 * plantas distintas sobre un mismo lienzo sin mentir sobre dónde está cada
 * cosa, y nadie va a redibujar a mano un edificio que ya está dibujado en un
 * plano que tiene guardado.
 *
 * Se marca en SVG y no en canvas porque la pantalla de bienvenida ya dibuja
 * exactamente esto en SVG: con canvas habría que escribir un segundo dibujante
 * y resolver a mano qué rectángulo está debajo del mouse, que es justo lo que
 * el navegador ya hace solo.
 */
@Component({
  selector: 'px-plano-editor',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './plano-editor.component.html',
  styleUrls: ['./plano-editor.component.scss'],
})
export class PlanoEditorComponent implements OnInit {
  private readonly api = inject(ZonasService);
  private readonly camarasApi = inject(CamerasService);

  /** Avisa cuando se guardó, para que el resto de la pantalla se entere. */
  @Output() guardado = new EventEmitter<Piso[]>();

  readonly tipos = TIPOS;
  readonly ancho = LIENZO;

  pisos: Piso[] = [];
  camaras: ApiCamera[] = [];
  /** Qué piso se está mirando. */
  pisoActual = 0;
  seleccionada: number | null = null;

  cargando = true;
  guardando = false;
  subiendo = false;
  error: string | null = null;
  aviso: string | null = null;
  /** Hay marcas sin guardar. */
  sucio = false;
  /** Se está pidiendo el nombre de un piso nuevo. */
  agregandoPiso = false;
  nombrePiso = '';

  private gesto: Gesto = null;

  ngOnInit(): void {
    this.cargar();
  }

  private cargar(): void {
    this.cargando = true;
    this.api.cargar().subscribe((pisos) => {
      this.pisos = pisos;
      if (this.pisoActual >= pisos.length) this.pisoActual = Math.max(0, pisos.length - 1);
      this.cargando = false;
      this.sucio = false;
      this.seleccionada = null;
    });
    this.camarasApi.listCameras().subscribe((c) => (this.camaras = c ?? []));
  }

  // ── el piso que se está mirando ────────────────────────────────────
  get piso(): Piso | null {
    return this.pisos[this.pisoActual] ?? null;
  }

  get zonas(): Zona[] {
    return this.piso?.zonas ?? [];
  }

  get alto(): number {
    return altoLienzo(this.piso);
  }

  verPiso(i: number): void {
    this.pisoActual = i;
    this.seleccionada = null;
    this.error = null;
  }

  // ── pisos ──────────────────────────────────────────────────────────

  agregarPiso(): void {
    const nombre = this.nombrePiso.trim();
    if (!nombre) return;
    this.api.crearPiso(nombre).subscribe((r) => {
      if (!r.ok) {
        this.error = r.motivo ?? 'No se pudo agregar el piso';
        return;
      }
      this.agregandoPiso = false;
      this.nombrePiso = '';
      this.error = null;
      this.aviso = `Piso "${nombre}" agregado. Subile su plano.`;
      this.api.cargar().subscribe((pisos) => {
        this.pisos = pisos;
        this.pisoActual = pisos.findIndex((p) => p.id === r.id);
        if (this.pisoActual < 0) this.pisoActual = pisos.length - 1;
      });
    });
  }

  renombrarPiso(i: number, valor: string): void {
    const p = this.pisos[i];
    const nombre = valor.trim();
    if (!nombre || nombre === p.nombre) return;
    this.api.renombrarPiso(p.id, nombre, p.orden).subscribe((ok) => {
      if (ok) p.nombre = nombre;
      else this.error = 'No se pudo renombrar el piso';
    });
  }

  borrarPiso(i: number): void {
    const p = this.pisos[i];
    this.api.borrarPiso(p.id).subscribe((r) => {
      if (!r.ok) {
        this.error = r.motivo ?? 'No se pudo borrar el piso';
        return;
      }
      this.error = null;
      this.aviso = `Piso "${p.nombre}" borrado`;
      this.cargar();
    });
  }

  // ── el plano de fondo ──────────────────────────────────────────────

  elegirImagen(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    const piso = this.piso;
    if (!file || !piso) return;
    this.error = null;

    const lector = new FileReader();
    lector.onload = () => {
      const img = new Image();
      img.onload = () => {
        // Se achica antes de subirla y se mide después: lo que se guarda es el
        // tamaño de lo que se va a dibujar, no el del archivo original. Si no
        // coincidieran, la proporción del lienzo mentiría.
        const escala = Math.min(1, ANCHO_MAXIMO / img.naturalWidth);
        const w = Math.round(img.naturalWidth * escala);
        const h = Math.round(img.naturalHeight * escala);
        const lienzo = document.createElement('canvas');
        lienzo.width = w;
        lienzo.height = h;
        lienzo.getContext('2d')?.drawImage(img, 0, 0, w, h);
        const chica = lienzo.toDataURL('image/jpeg', 0.88);

        this.subiendo = true;
        this.api.subirPlano(piso.id, chica, w, h).subscribe((r) => {
          this.subiendo = false;
          if (!r.ok) {
            this.error = r.motivo ?? 'No se pudo subir el plano';
            return;
          }
          piso.image = chica;
          piso.ancho = w;
          piso.alto = h;
          this.aviso = `Plano de "${piso.nombre}" cargado`;
        });
      };
      img.onerror = () => (this.error = 'No se pudo leer esa imagen');
      img.src = String(lector.result);
    };
    lector.readAsDataURL(file);
    input.value = '';
  }

  // ── marcar, mover, estirar ─────────────────────────────────────────

  /**
   * Dónde cayó el mouse, en fracciones del plano.
   *
   * Se calcula contra el rectángulo del SVG en pantalla y no contra el evento
   * crudo: el SVG se escala para entrar en la caja, así que un píxel de
   * pantalla no es una unidad de dibujo.
   */
  private punto(ev: PointerEvent): { x: number; y: number } {
    const destino = ev.currentTarget as SVGElement;
    const svg = (destino.closest('svg') ?? destino) as SVGSVGElement;
    const caja = svg.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (ev.clientX - caja.left) / caja.width)),
      y: Math.min(1, Math.max(0, (ev.clientY - caja.top) / caja.height)),
    };
  }

  alBajar(ev: PointerEvent, i?: number, esquina = false): void {
    const piso = this.piso;
    // Sin plano no se marca nada: no hay sobre qué. El recuadro vacío es una
    // invitación a subirlo, no un lienzo para dibujar el edificio a mano.
    if (!piso?.image) return;

    ev.preventDefault();
    (ev.currentTarget as Element).setPointerCapture?.(ev.pointerId);
    const p = this.punto(ev);

    if (i === undefined) {
      this.seleccionada = null;
      this.gesto = { que: 'marcar', x0: p.x, y0: p.y };
      // El área en construcción se dibuja como una más: así se ve dónde va a
      // quedar mientras se arrastra, en vez de aparecer al soltar.
      piso.zonas = [
        ...piso.zonas,
        { pisoId: piso.id, clave: '', nombre: '', tipo: 'oficina', x: p.x, y: p.y, w: 0, h: 0 },
      ];
      this.seleccionada = piso.zonas.length - 1;
      return;
    }

    this.seleccionada = i;
    const z = piso.zonas[i];
    this.gesto = esquina ? { que: 'estirar', i } : { que: 'mover', i, dx: p.x - z.x, dy: p.y - z.y };
  }

  alMover(ev: PointerEvent): void {
    const piso = this.piso;
    if (!this.gesto || !piso) return;
    const p = this.punto(ev);
    const g = this.gesto;

    if (g.que === 'marcar') {
      const z = piso.zonas[piso.zonas.length - 1];
      z.x = Math.min(g.x0, p.x);
      z.y = Math.min(g.y0, p.y);
      z.w = Math.abs(p.x - g.x0);
      z.h = Math.abs(p.y - g.y0);
    } else if (g.que === 'mover') {
      const z = piso.zonas[g.i];
      // Se frena en el borde en vez de dejarlo salir: la base rechaza un área
      // fuera del plano, y enterarse al guardar es tarde.
      z.x = Math.min(1 - z.w, Math.max(0, p.x - g.dx));
      z.y = Math.min(1 - z.h, Math.max(0, p.y - g.dy));
    } else {
      const z = piso.zonas[g.i];
      z.w = Math.min(1 - z.x, Math.max(MINIMO, p.x - z.x));
      z.h = Math.min(1 - z.y, Math.max(MINIMO, p.y - z.y));
    }
    this.sucio = true;
  }

  alSoltar(): void {
    const g = this.gesto;
    const piso = this.piso;
    this.gesto = null;
    if (g?.que !== 'marcar' || !piso) return;

    const z = piso.zonas[piso.zonas.length - 1];
    // Un clic suelto no es un área: sin esto, cada vez que alguien toca el
    // plano para deseleccionar quedaba un rectángulo invisible.
    if (z.w < MINIMO || z.h < MINIMO) {
      piso.zonas = piso.zonas.slice(0, -1);
      this.seleccionada = null;
      return;
    }
    // La clave es única en todo el lugar, no por piso: las personas guardan
    // sólo la clave, así que dos pisos con la misma se pisarían.
    const tomadas = new Set(this.pisos.flatMap((f) => f.zonas.map((q) => q.clave)).filter(Boolean));
    z.nombre = `Área ${piso.zonas.length}`;
    z.clave = claveDesde(z.nombre, tomadas);
    this.sucio = true;
  }

  // ── la lista de al lado ────────────────────────────────────────────

  renombrar(i: number, valor: string): void {
    // Se cambia el nombre, NO la clave: la clave es lo que guardan las
    // personas, y regenerarla al renombrar las desasignaría a todas.
    this.zonas[i].nombre = valor;
    this.sucio = true;
  }

  cambiarTipo(i: number, valor: string): void {
    this.zonas[i].tipo = valor as TipoZona;
    this.sucio = true;
  }

  borrar(i: number): void {
    const piso = this.piso;
    if (!piso) return;
    const z = piso.zonas[i];
    if (z.personas) {
      this.error =
        `"${z.nombre}" tiene ${z.personas} persona(s) asignada(s). ` +
        'Cambiales la zona desde Reconocimiento y volvé a intentar.';
      return;
    }
    piso.zonas = piso.zonas.filter((_, k) => k !== i);
    this.seleccionada = null;
    this.error = null;
    this.sucio = true;
  }

  // ── las cámaras ────────────────────────────────────────────────────

  /** Todas las áreas del lugar, con su piso, para el selector de cámaras. */
  get todasLasZonas(): { piso: string; zonas: Zona[] }[] {
    return this.pisos.filter((p) => p.zonas.length).map((p) => ({ piso: p.nombre, zonas: p.zonas }));
  }

  camaraEnZona(c: ApiCamera): string {
    return c.floorZoneId ?? '';
  }

  ponerCamara(c: ApiCamera, ev: Event): void {
    const id = (ev.target as HTMLSelectElement).value || null;
    this.api.ponerZonaDeCamara(c.id, id).subscribe((ok) => {
      if (!ok) {
        this.error = `No se pudo ubicar "${c.name}"`;
        return;
      }
      c.floorZoneId = id;
      const donde = this.pisos.flatMap((p) => p.zonas).find((z) => z.id === id);
      this.aviso = donde ? `"${c.name}" quedó en ${donde.nombre}` : `"${c.name}" ya no tiene zona`;
    });
  }

  // ── guardar ────────────────────────────────────────────────────────

  guardar(): void {
    if (this.guardando) return;
    const sinNombre = this.pisos.flatMap((p) => p.zonas).find((z) => !z.nombre.trim());
    if (sinNombre) {
      this.error = 'Hay un área sin nombre. Todas tienen que llamarse de alguna forma.';
      return;
    }
    this.guardando = true;
    this.error = null;
    this.api.guardar(this.pisos).subscribe((r) => {
      this.guardando = false;
      if (!r.ok) {
        this.error = r.motivo ?? 'No se pudo guardar';
        return;
      }
      this.sucio = false;
      const n = this.pisos.reduce((s, p) => s + p.zonas.length, 0);
      this.aviso = `Guardado: ${n} área(s) en ${this.pisos.length} piso(s)`;
      this.guardado.emit(this.pisos);
      // Se recarga para traer los ids de las áreas nuevas, que es lo que
      // necesita el selector de cámaras para poder apuntarles.
      this.cargar();
    });
  }

  // ── dibujo ─────────────────────────────────────────────────────────

  px(v: number): number {
    return v * LIENZO;
  }

  py(v: number): number {
    return v * this.alto;
  }
}
