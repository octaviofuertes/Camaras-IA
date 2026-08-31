import {
  Component,
  EventEmitter,
  Input,
  OnDestroy,
  OnInit,
  Output,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import {
  VivoService,
  type ElementoEpp,
  type EstadoEpp,
  type PersonaEnVivo,
  type PersonaEpp,
} from '../core/vivo.service';

/** Cada cuánto se le pregunta al worker quién está en el cuadro. */
const REFRESCO_MS = 700;

/**
 * La cámara en grande, con la gente marcable.
 *
 * Se ve el video entero —no recortado— y encima, para cada persona, su
 * contorno. Tocando a alguien se lo selecciona: recién ahí se lo cubre con una
 * capa verde o roja según tenga acceso al lugar o no, y aparece su ficha.
 *
 * ── Por qué la marca sólo aparece al seleccionar ────────────────────────
 *
 * Pintar a todo el mundo de verde o rojo todo el tiempo convierte el video en
 * un semáforo: con cinco personas en cuadro no se ve la escena, se ven cinco
 * manchas. Y el rojo deja de significar algo cuando está siempre prendido. La
 * marca es una respuesta a una pregunta —"¿este quién es?"— y por eso aparece
 * cuando alguien la hace.
 *
 * ── Por qué el video va con `contain` y no con `cover` ──────────────────
 *
 * Las coordenadas que manda el worker son fracciones del cuadro completo. Con
 * `cover` el navegador recorta la imagen para llenar la caja, y todo lo que se
 * dibuje encima queda corrido respecto de lo que se ve. Acá la caja toma la
 * proporción real del video, así que la silueta cae sobre el cuerpo.
 */
@Component({
  selector: 'px-camera-live',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './camera-live.component.html',
  styleUrls: ['./camera-live.component.scss'],
})
export class CameraLiveComponent implements OnInit, OnDestroy {
  private readonly api = inject(VivoService);

  @Input() cameraId = '';
  @Input() nombreCamara = '';
  /** En qué parte del lugar está esta cámara. Da contexto a "tiene acceso". */
  @Input() zona: string | null = null;
  @Output() cerrar = new EventEmitter<void>();

  personas: PersonaEnVivo[] = [];
  hayModulo = false;
  haySiluetas = false;
  /** Los elementos de protección que ve la cámara ahora. */
  epp: ElementoEpp[] = [];
  hayEpp = false;
  exigidos: string[] = [];
  /** Las personas que ve el módulo de EPP, con el estado de cada elemento. */
  eppPersonas: PersonaEpp[] = [];
  sinAlertarEpp: string[] = [];
  /** El track seleccionado. Se sigue por track, no por persona: quien no está
   *  identificado igual se puede tocar para ver que el sistema no sabe quién es. */
  seleccion: number | null = null;

  /** Proporción real del video, para que lo dibujado caiga donde corresponde. */
  proporcion = 16 / 9;
  fallo = false;

  private timer?: ReturnType<typeof setInterval>;

  get stream(): string {
    return `/media/cameras/${this.cameraId}/stream.mjpg`;
  }

  ngOnInit(): void {
    this.refrescar();
    this.timer = setInterval(() => this.refrescar(), REFRESCO_MS);
  }

  ngOnDestroy(): void {
    if (this.timer) clearInterval(this.timer);
  }

  private refrescar(): void {
    this.api.enVivo(this.cameraId).subscribe((v) => {
      this.hayModulo = v.modulo;
      this.haySiluetas = v.siluetas;
      this.personas = v.personas;
      this.hayEpp = v.moduloEpp;
      this.exigidos = v.exigidos;
      this.epp = v.epp;
      this.eppPersonas = v.eppPersonas;
      this.sinAlertarEpp = v.sinAlertarEpp;
      // Si el seleccionado se fue del cuadro, se suelta la selección: dejarla
      // pegada mostraría la ficha de alguien que ya no está.
      if (this.seleccion !== null && !v.personas.some((p) => p.trackId === this.seleccion)) {
        this.seleccion = null;
      }
    });
  }

  /** Toma la proporción del video real apenas carga el primer cuadro. */
  alCargar(ev: Event): void {
    const img = ev.target as HTMLImageElement;
    if (img.naturalWidth && img.naturalHeight) {
      this.proporcion = img.naturalWidth / img.naturalHeight;
    }
    this.fallo = false;
  }

  alFallar(): void {
    this.fallo = true;
  }

  // ── selección ──────────────────────────────────────────────────────

  tocar(p: PersonaEnVivo): void {
    this.seleccion = this.seleccion === p.trackId ? null : p.trackId;
  }

  get elegida(): PersonaEnVivo | null {
    return this.personas.find((p) => p.trackId === this.seleccion) ?? null;
  }

  estaElegida(p: PersonaEnVivo): boolean {
    return p.trackId === this.seleccion;
  }

  // ── dibujo ─────────────────────────────────────────────────────────

  /** El contorno como lista de puntos para un <polygon> de SVG. */
  puntos(p: PersonaEnVivo): string {
    if (!p.silueta?.length) return '';
    return p.silueta.map(([x, y]) => `${(x * 1000).toFixed(1)},${(y * 1000).toFixed(1)}`).join(' ');
  }

  /** Color de la capa: verde si puede estar acá, rojo si no. */
  clase(p: PersonaEnVivo): string {
    if (p.tieneAcceso === true) return 'con-acceso';
    if (p.tieneAcceso === false) return 'sin-acceso';
    return 'sin-saber';
  }

  // ── elementos de protección ────────────────────────────────────────

  /**
   * Cómo se pinta cada elemento.
   *
   * Verde lo que está puesto, rojo lo que falta, y gris lo que se ve pero en
   * esta cámara no se exige: mostrarlo igual deja ver que el módulo está
   * mirando, y con un color aparte no se confunde con algo que va a alertar.
   */
  claseEpp(e: ElementoEpp): string {
    if (!e.exigido) return 'no-exigido';
    return e.tiene ? 'puesto' : 'falta';
  }

  /** El rótulo del recuadro: "casco" o "sin casco". */
  rotulo(e: ElementoEpp): string {
    return e.tiene ? e.nombre : `sin ${e.nombre}`;
  }

  /**
   * Qué persona del módulo de EPP es la misma que ésta.
   *
   * Se cruza por geometría y no por índice. Los dos módulos corren su propio
   * modelo y encuentran a la gente en su propio orden, así que la persona 0 de
   * uno no es la persona 0 del otro: cruzarlos por índice le ponía "le falta el
   * casco" a quien estaba al lado. Se compara solapamiento de cajas, que es lo
   * único que las dos listas tienen en común.
   */
  private parDeEpp(p: PersonaEnVivo): PersonaEpp | null {
    let mejor: PersonaEpp | null = null;
    let mejorValor = 0.4; // por debajo de esto no son la misma persona
    for (const e of this.eppPersonas) {
      const v = this.iou(p.bbox, e.bbox);
      if (v > mejorValor) {
        mejor = e;
        mejorValor = v;
      }
    }
    return mejor;
  }

  /** Solapamiento sobre unión de dos cajas [x, y, ancho, alto]. */
  private iou(a: [number, number, number, number], b: [number, number, number, number]): number {
    const ix = Math.max(0, Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]));
    const iy = Math.max(0, Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]));
    const inter = ix * iy;
    const union = a[2] * a[3] + b[2] * b[3] - inter;
    return union > 0 ? inter / union : 0;
  }

  /** Los elementos obligatorios de esta persona, con su estado, para la ficha. */
  estadoDe(p: PersonaEnVivo): { nombre: string; estado: EstadoEpp }[] {
    const par = this.parDeEpp(p);
    if (!par) return [];
    return this.exigidos.map((clave) => ({
      nombre: clave,
      estado: par.estado[clave] ?? 'no_se_sabe',
    }));
  }

  /**
   * Cómo se pinta el recuadro de una persona según su EPP.
   *
   * Rojo si le falta algo, verde si tiene todo lo exigido, y gris si no se
   * sabe. El gris importa: alguien de espaldas no está en falta, y pintarlo de
   * rojo sería acusarlo por no dejarse ver.
   */
  colorDePersona(p: PersonaEpp): string {
    const estados = Object.values(p.estado ?? {});
    if (estados.some((e) => e === 'falta')) return 'falta';
    if (estados.length && estados.every((e) => e === 'tiene')) return 'completo';
    return 'no-se-sabe';
  }

  /** Un renglón corto sobre la cabeza: qué le falta, o que está completo. */
  resumenDePersona(p: PersonaEpp): string {
    const faltan = Object.entries(p.estado ?? {})
      .filter(([, v]) => v === 'falta')
      .map(([k]) => k);
    if (faltan.length) return `sin ${faltan.join(', ')}`;
    const todos = Object.values(p.estado ?? {});
    return todos.length && todos.every((e) => e === 'tiene') ? 'completo' : '';
  }

  /** De qué SÍ se avisa: lo exigido menos lo que todavía no se puede medir. */
  get seAlerta(): string[] {
    return this.exigidos.filter((e) => !this.sinAlertarEpp.includes(e));
  }

  /** Cómo se redacta cada estado en la ficha. */
  textoEstado(e: EstadoEpp): string {
    if (e === 'tiene') return 'lo lleva';
    if (e === 'falta') return 'LE FALTA';
    return 'no se sabe';
  }

  /** Los renglones que se dibujan sobre el cuerpo: un elemento por línea. */
  filas(e: PersonaEpp): { texto: string; estado: EstadoEpp }[] {
    return this.exigidos.map((clave) => {
      const estado = e.estado[clave] ?? 'no_se_sabe';
      // El símbolo va además del color: quien no distingue rojo de verde tiene
      // que poder leer lo mismo, y un tilde o una cruz se ven aunque el
      // recuadro quede sobre una parte clara del video.
      const marca = estado === 'tiene' ? '✓' : estado === 'falta' ? '✗' : '?';
      return { texto: `${marca} ${clave}`, estado };
    });
  }

  /** Le falta algo obligatorio: la caja va en rojo. */
  tieneFalta(e: PersonaEpp): boolean {
    return this.exigidos.some((c) => e.estado[c] === 'falta');
  }

  /**
   * Se le ve TODO lo obligatorio puesto: la caja va en verde.
   *
   * Pide que no quede ninguno en "no se sabe". Pintar de verde a alguien del
   * que sólo se vio el chaleco sería decir que está en regla sin haberle visto
   * la cabeza, y el verde tiene que significar exactamente eso: está en regla.
   */
  cumpleTodo(e: PersonaEpp): boolean {
    return this.exigidos.length > 0 && this.exigidos.every((c) => e.estado[c] === 'tiene');
  }

  // ── textos ─────────────────────────────────────────────────────────

  nombreDe(p: PersonaEnVivo): string {
    return p.nombre || 'Sin identificar';
  }

  /** Hace cuánto está en el lugar, redactado. */
  haceCuanto(p: PersonaEnVivo): string {
    const s = p.haceSegundos;
    if (s === null || s === undefined) return 'todavía no se sabe';
    if (s < 60) return 'recién llegó';
    const min = Math.floor(s / 60);
    if (min < 60) return `hace ${min} min`;
    const h = Math.floor(min / 60);
    const resto = min % 60;
    return resto ? `hace ${h} h ${resto} min` : `hace ${h} h`;
  }

  acceso(p: PersonaEnVivo): string {
    if (p.tieneAcceso === true) return this.zona ? `Sí, tiene acceso a ${this.zona}` : 'Sí, tiene acceso';
    if (p.tieneAcceso === false) return this.zona ? `NO tiene acceso a ${this.zona}` : 'NO tiene acceso';
    // No se afirma nada sobre alguien que el sistema no reconoció: decir "no
    // tiene acceso" de un desconocido sería acusarlo por no estar cargado.
    return 'No se sabe: no está identificado';
  }
}
