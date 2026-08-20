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
import { VivoService, type ElementoEpp, type PersonaEnVivo } from '../core/vivo.service';

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

  /** Qué le falta a la persona seleccionada, para la ficha. */
  faltantesDe(indice: number): string[] {
    return this.epp
      .filter((e) => e.persona === indice && e.exigido && !e.tiene)
      .map((e) => e.nombre);
  }

  /** Qué tiene puesto la persona seleccionada. */
  puestosDe(indice: number): string[] {
    return this.epp
      .filter((e) => e.persona === indice && e.tiene)
      .map((e) => e.nombre);
  }

  indiceDe(p: PersonaEnVivo): number {
    return this.personas.indexOf(p);
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
