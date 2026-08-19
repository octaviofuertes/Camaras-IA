import { Component, EventEmitter, Input, OnInit, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { CamerasService, type ApiCamera } from '../../core/cameras.service';
import { ZonasService } from '../../core/zonas.service';
import {
  LIENZO,
  TIPOS,
  altoLienzo,
  claveDesde,
  type Plano,
  type TipoZona,
  type Zona,
} from '../../core/zonas';

/** Qué se está haciendo con el mouse apretado. */
type Gesto =
  | { que: 'dibujar'; x0: number; y0: number }
  | { que: 'mover'; i: number; dx: number; dy: number }
  | { que: 'estirar'; i: number }
  | null;

/** Cuánto tiene que medir un arrastre para contar como bloque, en fracción. */
const MINIMO = 0.02;

/**
 * El editor del plano del lugar.
 *
 * Tres gestos y nada más: arrastrar sobre el vacío dibuja un bloque, arrastrar
 * un bloque lo mueve, arrastrar su esquina lo agranda. Es una pantalla que se
 * usa una vez —cuando se configura el lugar— y después casi nunca, así que no
 * paga tener rotación, formas libres ni deshacer.
 *
 * Se dibuja en SVG y no en canvas porque la pantalla de bienvenida ya dibuja
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
  @Output() guardado = new EventEmitter<Zona[]>();

  readonly tipos = TIPOS;
  readonly ancho = LIENZO;

  plano: Plano = { image: null, ancho: null, alto: null };
  zonas: Zona[] = [];
  camaras: ApiCamera[] = [];
  seleccionada: number | null = null;

  cargando = true;
  guardando = false;
  error: string | null = null;
  aviso: string | null = null;
  /** Hay cambios sin guardar. */
  sucio = false;

  private gesto: Gesto = null;

  ngOnInit(): void {
    this.cargar();
  }

  private cargar(): void {
    this.cargando = true;
    this.api.cargar().subscribe((r) => {
      this.plano = r.plano;
      this.zonas = r.zonas;
      this.cargando = false;
      this.sucio = false;
    });
    this.camarasApi.listCameras().subscribe((c) => (this.camaras = c ?? []));
  }

  get alto(): number {
    return altoLienzo(this.plano);
  }

  /** El bloque que está seleccionado, si hay alguno. */
  get actual(): Zona | null {
    return this.seleccionada === null ? null : this.zonas[this.seleccionada] ?? null;
  }

  // ── dibujar, mover, estirar ────────────────────────────────────────

  /**
   * Dónde cayó el mouse, en fracciones del plano.
   *
   * Se calcula contra el rectángulo del SVG en pantalla y no contra el evento
   * crudo: el SVG se escala para entrar en la caja, así que un píxel de
   * pantalla no es una unidad de dibujo.
   */
  private punto(ev: PointerEvent): { x: number; y: number } {
    const svg = (ev.currentTarget as SVGSVGElement).closest('svg') ?? (ev.currentTarget as SVGSVGElement);
    const caja = svg.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (ev.clientX - caja.left) / caja.width)),
      y: Math.min(1, Math.max(0, (ev.clientY - caja.top) / caja.height)),
    };
  }

  alBajar(ev: PointerEvent, i?: number, esquina = false): void {
    ev.preventDefault();
    (ev.currentTarget as Element).setPointerCapture?.(ev.pointerId);
    const p = this.punto(ev);

    if (i === undefined) {
      this.seleccionada = null;
      this.gesto = { que: 'dibujar', x0: p.x, y0: p.y };
      // El bloque en construcción se dibuja como uno más: así se ve dónde va a
      // quedar mientras se arrastra, en vez de aparecer al soltar.
      this.zonas = [...this.zonas, { clave: '', nombre: '', tipo: 'oficina', x: p.x, y: p.y, w: 0, h: 0 }];
      this.seleccionada = this.zonas.length - 1;
      return;
    }

    this.seleccionada = i;
    const z = this.zonas[i];
    this.gesto = esquina ? { que: 'estirar', i } : { que: 'mover', i, dx: p.x - z.x, dy: p.y - z.y };
  }

  alMover(ev: PointerEvent): void {
    if (!this.gesto) return;
    const p = this.punto(ev);
    const g = this.gesto;

    if (g.que === 'dibujar') {
      const z = this.zonas[this.zonas.length - 1];
      z.x = Math.min(g.x0, p.x);
      z.y = Math.min(g.y0, p.y);
      z.w = Math.abs(p.x - g.x0);
      z.h = Math.abs(p.y - g.y0);
    } else if (g.que === 'mover') {
      const z = this.zonas[g.i];
      // Se frena en el borde en vez de dejarlo salir: la base rechaza un
      // bloque fuera del plano, y enterarse al guardar es tarde.
      z.x = Math.min(1 - z.w, Math.max(0, p.x - g.dx));
      z.y = Math.min(1 - z.h, Math.max(0, p.y - g.dy));
    } else {
      const z = this.zonas[g.i];
      z.w = Math.min(1 - z.x, Math.max(MINIMO, p.x - z.x));
      z.h = Math.min(1 - z.y, Math.max(MINIMO, p.y - z.y));
    }
    this.sucio = true;
  }

  alSoltar(): void {
    const g = this.gesto;
    this.gesto = null;
    if (g?.que !== 'dibujar') return;

    const z = this.zonas[this.zonas.length - 1];
    // Un clic suelto no es un bloque: sin esto, cada vez que alguien toca el
    // plano para deseleccionar quedaba un rectángulo invisible.
    if (z.w < MINIMO || z.h < MINIMO) {
      this.zonas = this.zonas.slice(0, -1);
      this.seleccionada = null;
      return;
    }
    const tomadas = new Set(this.zonas.map((q) => q.clave).filter(Boolean));
    const n = this.zonas.length;
    z.nombre = `Bloque ${n}`;
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
    const z = this.zonas[i];
    if (z.personas) {
      this.error =
        `"${z.nombre}" tiene ${z.personas} persona(s) asignada(s). ` +
        'Cambiales la zona desde Reconocimiento y volvé a intentar.';
      return;
    }
    this.zonas = this.zonas.filter((_, k) => k !== i);
    this.seleccionada = null;
    this.error = null;
    this.sucio = true;
  }

  // ── el fondo ───────────────────────────────────────────────────────

  elegirImagen(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    const lector = new FileReader();
    lector.onload = () => {
      const src = String(lector.result);
      // Se mide la imagen antes de subirla: su proporción es la del plano, y
      // sin ella habría que suponer una y deformar todo lo que se dibuje.
      const img = new Image();
      img.onload = () => {
        this.api.subirPlano(src, img.naturalWidth, img.naturalHeight).subscribe((ok) => {
          if (ok) this.plano = { image: src, ancho: img.naturalWidth, alto: img.naturalHeight };
          else this.error = 'No se pudo guardar la imagen del plano';
        });
      };
      img.src = src;
    };
    lector.readAsDataURL(file);
    input.value = '';
  }

  // ── las cámaras ────────────────────────────────────────────────────

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
      this.aviso = id
        ? `"${c.name}" quedó en ${this.zonas.find((z) => z.id === id)?.nombre ?? 'esa zona'}`
        : `"${c.name}" ya no tiene zona`;
    });
  }

  /** Nombre del bloque donde está una cámara, para el resumen. */
  nombreDeZona(id: string | null): string | null {
    return id ? this.zonas.find((z) => z.id === id)?.nombre ?? null : null;
  }

  // ── guardar ────────────────────────────────────────────────────────

  guardar(): void {
    if (this.guardando) return;
    const sinNombre = this.zonas.find((z) => !z.nombre.trim());
    if (sinNombre) {
      this.error = 'Hay un bloque sin nombre. Todos tienen que llamarse de alguna forma.';
      return;
    }
    this.guardando = true;
    this.error = null;
    this.api.guardar(this.zonas).subscribe((r) => {
      this.guardando = false;
      if (!r.ok) {
        this.error = r.motivo ?? 'No se pudo guardar';
        return;
      }
      this.sucio = false;
      this.aviso = `Plano guardado: ${this.zonas.length} bloque(s)`;
      this.guardado.emit(this.zonas);
      // Se recarga para traer los ids de los bloques nuevos, que es lo que
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
