import {
  ChangeDetectorRef,
  Component,
  EventEmitter,
  Input,
  NgZone,
  OnDestroy,
  OnInit,
  Output,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AuthService } from '../core/auth.service';
import { RecognitionService } from '../core/recognition.service';
import {
  VivoService,
  type ElementoEpp,
  type EstadoEpp,
  type PersonaEnVivo,
  type PersonaEpp,
  type VistaEnVivo,
} from '../core/vivo.service';
import { Seguimiento, fusionar, type Caja } from '../core/seguimiento';

/**
 * Cada cuánto se le pregunta al worker quién está en el cuadro.
 *
 * Estaba en 700 ms y era la causa principal de que el recuadro quedara colgado
 * atrás de la persona: el worker analiza un cuadro cada ~190 ms, así que la
 * pantalla llegaba a estar tres cuadros atrasada por no preguntar. La consulta
 * devuelve un JSON chico y el worker contesta en 10-20 ms —no vuelve a
 * analizar nada, entrega lo último que tiene—, así que preguntar seguido no le
 * cuesta trabajo a nadie.
 */
const REFRESCO_MS = 150;

/**
 * Cada cuánto se pide, además, el recorte de la cara de quien se está por dar
 * de alta.
 *
 * Va aparte y mucho más espaciado que el refresco: esa imagen es la cara de
 * alguien que NO está dado de alta, y sacarla del worker siete veces por
 * segundo para tener el recuadro al día sería pagar con la privacidad de esa
 * persona una fluidez que no aporta nada —la foto no se mueve—.
 */
const NOMBRAR_CADA_MS = 700;

/**
 * Cuánto tarda el video en aparecer en pantalla, aproximado y por lo bajo.
 *
 * El recuadro tiene que caer sobre el cuadro que el ojo está viendo, no sobre
 * el instante en que se lo dibuja. Quedarse corto con este número atrasa el
 * recuadro un poco; pasarse lo adelanta, que se ve peor.
 */
const RETRASO_VIDEO_S = 0.1;

/**
 * Cómo se reconoce un elemento entre un cuadro y el siguiente.
 *
 * No viene con identificador propio —el modelo detecta cajas, no objetos que
 * persisten— así que se lo identifica por qué es y de quién es. Alcanza:
 * lo único que se hace con esa identidad es moverlo junto con su persona.
 */
function claveElemento(e: ElementoEpp): string {
  return `${e.clave}:${e.persona ?? 'suelto'}`;
}

/**
 * Escribe una caja nueva sólo si se movió de verdad, y avisa si lo hizo.
 *
 * El umbral es medio milésimo del cuadro —menos de un píxel en pantalla—: por
 * debajo de eso no hay nada que ver, y escribirlo igual haría redibujar la
 * pantalla sesenta veces por segundo con alguien parado enfrente.
 */
function cambiar<T>(obj: T, campo: keyof T, caja: Caja): boolean {
  const previa = obj[campo] as unknown as Caja;
  if (previa && previa.every((v, i) => Math.abs(v - caja[i]) < 0.0005)) return false;
  (obj[campo] as unknown as Caja) = caja;
  return true;
}

/**
 * Cada cuánto avanza el cronómetro.
 *
 * Más fino que el segundo que se muestra, a propósito: con un tic de exactamente
 * un segundo, el redondeo hace que de tanto en tanto un número se repita o se
 * saltee, y eso en un cronómetro se ve.
 */
const TIC_MS = 200;

/**
 * La cámara en grande, con cada persona marcada en la cara.
 *
 * Se ve el video entero —no recortado— y encima, sobre la CARA de cada
 * persona, un recuadro con su nombre: verde si tiene acceso a este lugar, rojo
 * si no lo tiene, gris si el sistema no sabe quién es.
 *
 * ── Por qué sobre la cara y no sobre el cuerpo ──────────────────────────
 *
 * Lo que se está afirmando es una identidad, y la identidad está en la cara.
 * Un recuadro alrededor del cuerpo entero incluye pared, escritorio y medio
 * compañero de al lado; pintado de verde, lo que queda marcado es un pedazo de
 * la oficina y no una persona. Sobre la cara no hay ambigüedad posible sobre
 * de quién se está hablando.
 *
 * ── Por qué el gris nunca se convierte en un nombre ─────────────────────
 *
 * A quien el sistema no reconoce se lo marca "Sin identificar" y ahí se queda.
 * Nunca se le pone el nombre del que más se le parece: un nombre equivocado
 * sobre una cara es un error que nadie mirando la pantalla puede detectar, y
 * arrastra el registro de accesos entero. Tocándolo se le puede dar un nombre
 * a mano, que es la forma correcta de resolverlo — la decide una persona.
 *
 * ── Por qué el video va con `contain` y no con `cover` ──────────────────
 *
 * Las coordenadas que manda el worker son fracciones del cuadro completo. Con
 * `cover` el navegador recorta la imagen para llenar la caja, y todo lo que se
 * dibuje encima queda corrido respecto de lo que se ve. Acá la caja toma la
 * proporción real del video, así que el recuadro cae sobre la cara.
 */
@Component({
  selector: 'px-camera-live',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './camera-live.component.html',
  styleUrls: ['./camera-live.component.scss'],
})
export class CameraLiveComponent implements OnInit, OnDestroy {
  private readonly api = inject(VivoService);
  private readonly personasApi = inject(RecognitionService);
  private readonly auth = inject(AuthService);

  @Input() cameraId = '';
  @Input() nombreCamara = '';
  /** En qué parte del lugar está esta cámara. Da contexto a "tiene acceso". */
  @Input() zona: string | null = null;
  @Output() cerrar = new EventEmitter<void>();

  personas: PersonaEnVivo[] = [];
  hayModulo = false;
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

  // ── ponerle un nombre a alguien sin identificar ────────────────────
  nombreNuevo = '';
  /** null = todavía no se eligió. No se asume que sí: de esto depende que
   *  suene una alerta cuando esa persona vuelva a aparecer. */
  accesoNuevo: boolean | null = null;
  consentimiento = '';
  guardando = false;
  errorAlta: string | null = null;
  /** La persona ya cargada a la que se parece esta cara, si el servidor avisó. */
  yaExiste: { id: string; displayName: string; parecido: number } | null = null;
  guardado = false;

  /** Proporción real del video, para que lo dibujado caiga donde corresponde. */
  proporcion = 16 / 9;
  fallo = false;

  /** El reloj del cronómetro. Lo mueve su propio tic, no el refresco. */
  private reloj = Date.now() / 1000;
  /**
   * Los dos relojes en el mismo instante: el del worker cuando contestó, y el
   * de este navegador cuando llegó esa respuesta.
   *
   * Con ese par, el tiempo de cada persona se calcula sin comparar relojes
   * distintos: la resta contra su hora de llegada se hace toda con el reloj
   * del worker, y acá sólo se le suma lo que pasó desde que llegó la
   * respuesta. Da lo mismo que el navegador tenga la hora corrida.
   */
  private anclaServidor = 0;
  private anclaLocal = 0;

  private timer?: ReturnType<typeof setTimeout>;
  private tic?: ReturnType<typeof setInterval>;
  private cuadro = 0;
  private vivo = false;

  /**
   * Dónde está cada cuerpo AHORA, a partir de dónde estaba cuando se lo miró.
   * Es lo que hace que el recuadro acompañe a la persona en vez de ir saltando
   * detrás de ella. Ver `core/seguimiento.ts`.
   */
  private readonly seg = new Seguimiento();
  /** Las cajas tal cual las mandó el worker, para medir el corrimiento. */
  private crudoCara = new Map<number, Caja>();
  private crudoEpp = new Map<number, Caja>();
  private crudoElemento = new Map<string, Caja>();
  /** De cuándo es cada capa. Son dos modelos con dos ritmos, así que cada una
   *  se adelanta con su propia antigüedad y no con la del otro. */
  private tsCara = 0;
  private tsEpp = 0;
  private ultimoNombrar = 0;

  private readonly zone = inject(NgZone);
  private readonly cd = inject(ChangeDetectorRef);

  get stream(): string {
    return `/media/cameras/${this.cameraId}/stream.mjpg`;
  }

  ngOnInit(): void {
    this.vivo = true;
    this.refrescar();
    this.tic = setInterval(() => (this.reloj = Date.now() / 1000), TIC_MS);
    // El dibujo corre fuera de Angular y avisa sólo cuando algo se movió. Si no,
    // cada cuadro de animación dispararía una detección de cambios de la
    // pantalla entera, sesenta veces por segundo, para correr un recuadro dos
    // píxeles.
    this.zone.runOutsideAngular(() => this.animar());
  }

  ngOnDestroy(): void {
    this.vivo = false;
    if (this.timer) clearTimeout(this.timer);
    if (this.tic) clearInterval(this.tic);
    if (this.cuadro) cancelAnimationFrame(this.cuadro);
  }

  private refrescar(): void {
    // Se pide la cara SÓLO de quien está seleccionado y sin identificar, y
    // espaciado: es el único momento en que hace falta y esa imagen no cambia
    // de un cuadro al otro. Ver `VivoService.enVivo` y NOMBRAR_CADA_MS.
    const ahora = Date.now();
    const pedirCara =
      !!this.elegida && !this.elegida.personId && ahora - this.ultimoNombrar >= NOMBRAR_CADA_MS;
    if (pedirCara) this.ultimoNombrar = ahora;

    this.api.enVivo(this.cameraId, pedirCara ? this.seleccion : null).subscribe((v) => {
      this.aplicar(v);
      // La próxima consulta se agenda cuando llegó ésta, y no cada 150 ms pase
      // lo que pase: con un worker ocupado, un intervalo fijo encima las
      // consultas y termina pidiendo más rápido de lo que se puede contestar.
      if (this.vivo) this.timer = setTimeout(() => this.refrescar(), REFRESCO_MS);
    });
  }

  /** Guarda lo que llegó y lo suma al seguimiento, sin romper lo que se dibuja. */
  private aplicar(v: VistaEnVivo): void {
    if (v.ahora > 0) {
      this.anclaServidor = v.ahora;
      this.anclaLocal = Date.now() / 1000;
    }
    this.hayModulo = v.modulo;
    this.hayEpp = v.moduloEpp;
    this.exigidos = v.exigidos;
    this.sinAlertarEpp = v.sinAlertarEpp;

    // El instante del cuadro que se analizó. Si el módulo no lo manda se usa el
    // reloj del worker, que equivale a no adelantar nada: es preferible quedar
    // como antes a adelantar con una antigüedad inventada.
    this.tsCara = v.ts > 0 ? v.ts : v.ahora;
    this.tsEpp = v.tsEpp > 0 ? v.tsEpp : v.ahora;

    const vigentes = new Set<string>();
    for (const p of v.personas) {
      vigentes.add(`cara:${p.trackId}`);
      this.seg.observar(`cara:${p.trackId}`, p.rostro, this.tsCara);
    }
    for (const p of v.eppPersonas) {
      vigentes.add(`epp:${p.trackId}`);
      this.seg.observar(`epp:${p.trackId}`, p.bbox, this.tsEpp);
    }
    this.seg.olvidarSalvo(vigentes);

    this.crudoCara = new Map(v.personas.map((p) => [p.trackId, [...p.rostro] as Caja]));
    this.crudoEpp = new Map(v.eppPersonas.map((p) => [p.trackId, [...p.bbox] as Caja]));
    this.crudoElemento = new Map(v.epp.map((e) => [claveElemento(e), [...e.bbox] as Caja]));

    // En el lugar, sin reemplazar la lista: ver `fusionar`.
    fusionar(this.personas, v.personas, (p) => String(p.trackId));
    fusionar(this.eppPersonas, v.eppPersonas, (p) => String(p.trackId));
    fusionar(this.epp, v.epp, claveElemento);

    // Si el seleccionado se fue del cuadro, se suelta la selección: dejarla
    // pegada mostraría la ficha de alguien que ya no está.
    if (this.seleccion !== null && !v.personas.some((p) => p.trackId === this.seleccion)) {
      this.seleccion = null;
    }
    this.mover();
  }

  /** El lazo de dibujo: adelanta cada recuadro hasta el instante que se ve. */
  private animar(): void {
    if (!this.vivo) return;
    if (this.mover()) this.cd.detectChanges();
    this.cuadro = requestAnimationFrame(() => this.animar());
  }

  /**
   * Corre cada recuadro hasta donde la persona está ahora. Devuelve si algo se
   * movió lo bastante como para valer un redibujo.
   */
  private mover(): boolean {
    if (!this.anclaServidor) return false;
    // El instante que se está VIENDO en el video, medido con el reloj del
    // worker: su hora cuando contestó, más lo que pasó desde entonces acá,
    // menos lo que el video tarda en llegar a la pantalla.
    const visto = this.anclaServidor + (Date.now() / 1000 - this.anclaLocal) - RETRASO_VIDEO_S;

    let cambio = false;
    // Cuánto se corrió cada persona del EPP respecto de donde la vio el worker.
    // Sus elementos —el casco, el chaleco— se mueven con ella: van pegados al
    // cuerpo, y seguirlos por separado sólo les agregaría un temblor propio.
    const corrimiento = new Map<number, [number, number]>();

    for (const p of this.eppPersonas) {
      const cruda = this.crudoEpp.get(p.trackId);
      if (!cruda) continue;
      const caja = this.seg.donde(`epp:${p.trackId}`, visto, cruda);
      corrimiento.set(p.trackId, [caja[0] - cruda[0], caja[1] - cruda[1]]);
      if (cambiar(p, 'bbox', caja)) cambio = true;
    }

    for (const e of this.epp) {
      const cruda = this.crudoElemento.get(claveElemento(e));
      if (!cruda) continue;
      const dueno = e.persona !== null ? this.eppPersonas[e.persona] : undefined;
      const d = dueno ? corrimiento.get(dueno.trackId) : undefined;
      const caja: Caja = d
        ? [cruda[0] + d[0], cruda[1] + d[1], cruda[2], cruda[3]]
        : cruda;
      if (cambiar(e, 'bbox', caja)) cambio = true;
    }

    for (const p of this.personas) {
      const cruda = this.crudoCara.get(p.trackId);
      if (!cruda) continue;
      if (cambiar(p, 'rostro', this.seg.donde(`cara:${p.trackId}`, visto, cruda))) {
        cambio = true;
      }
    }
    return cambio;
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
    const mismo = this.seleccion === p.trackId;
    this.seleccion = mismo ? null : p.trackId;
    // El formulario es de UNA persona. Arrastrar el nombre a medio escribir de
    // la anterior es la forma más fácil de darle de alta la cara equivocada.
    if (mismo) return;
    this.limpiarAlta();
    // Sin esto, la foto de quien se acaba de tocar tarda hasta lo que falte
    // para el próximo refresco: se siente como que la ficha está rota.
    if (!p.personId) this.refrescar();
  }

  private limpiarAlta(): void {
    this.nombreNuevo = '';
    this.accesoNuevo = null;
    this.consentimiento = '';
    this.errorAlta = null;
    this.yaExiste = null;
    this.guardado = false;
    this.guardando = false;
  }

  get elegida(): PersonaEnVivo | null {
    return this.personas.find((p) => p.trackId === this.seleccion) ?? null;
  }

  estaElegida(p: PersonaEnVivo): boolean {
    return p.trackId === this.seleccion;
  }

  // ── dibujo ─────────────────────────────────────────────────────────

  /**
   * Dónde va el recuadro de la cara, en porcentaje de la caja del video.
   *
   * Se posiciona con HTML y no con SVG a propósito. La capa de SVG se estira
   * sin conservar la proporción para cubrir el video, y con ella se estira
   * también el texto: los nombres salían anchos y deformados. En HTML el
   * recuadro se estira y la letra no.
   */
  estiloCara(p: PersonaEnVivo): Record<string, string> {
    const [x, y, w, h] = p.rostro;
    return {
      left: `${x * 100}%`,
      top: `${y * 100}%`,
      width: `${w * 100}%`,
      height: `${h * 100}%`,
    };
  }

  /**
   * El nombre va debajo del recuadro cuando la cara está pegada al borde de
   * arriba. Arriba es donde se lee mejor —no tapa el cuerpo— pero contra el
   * techo del cuadro quedaría cortado, y un nombre a medias no sirve.
   */
  etiquetaAbajo(p: PersonaEnVivo): boolean {
    return p.rostro[1] < 0.06;
  }

  /** Color del recuadro: verde si puede estar acá, rojo si no. */
  clase(p: PersonaEnVivo): string {
    if (p.tieneAcceso === true) return 'con-acceso';
    if (p.tieneAcceso === false) return 'sin-acceso';
    return 'sin-saber';
  }

  // ── darle un nombre a quien el sistema no reconoce ─────────────────

  /**
   * Dar de alta a una persona es afirmar que existe su consentimiento para
   * guardar su dato biométrico. No es una decisión operativa, y por eso está
   * detrás del mismo permiso que la pantalla de personas.
   */
  get puedeNombrar(): boolean {
    return this.auth.user?.permissions?.includes('persons:write') ?? false;
  }

  /**
   * ¿Hay con qué darla de alta?
   *
   * Sin vector facial se puede crear la ficha, pero la cámara no la va a
   * reconocer nunca: quedaría un nombre en una lista y la persona seguiría
   * apareciendo "Sin identificar" para siempre. Se pide esperar a que mire
   * hacia la cámara una vez.
   */
  hayCaraUtilizable(p: PersonaEnVivo): boolean {
    return p.hayFoto;
  }

  /**
   * Por qué no se lo reconoce, dicho como lo dice el módulo.
   *
   * Cuando no se le ve la cara el motivo es ése y no hace falta repetirlo: el
   * recuadro ya está punteado y el texto lo explica.
   */
  porQueNo(p: PersonaEnVivo): string {
    if (p.personId) return '';
    // El motivo del módulo va primero: cuando lo hay dice algo más concreto
    // que "no se le ve la cara" —por ejemplo que a esa persona se le dio de
    // baja mientras estaba en el cuadro—.
    if (p.motivo) return p.motivo;
    if (p.rostroEstimado) return 'no se le ve la cara, está de espaldas o de costado';
    return '';
  }

  puedeGuardar(p: PersonaEnVivo): boolean {
    return (
      !this.guardando &&
      // El vector, no `hayFoto`: sin él la ficha se crearía sin plantilla y la
      // cámara no la reconocería nunca. Llega en el refresco de después de
      // tocarla, así que el botón está apagado ese instante y nada más.
      !!p.vector &&
      this.nombreNuevo.trim().length >= 2 &&
      this.consentimiento.trim().length >= 3 &&
      this.accesoNuevo !== null
    );
  }

  darDeAlta(p: PersonaEnVivo, forzarNueva = false): void {
    if (!this.puedeGuardar(p)) return;
    this.guardando = true;
    this.errorAlta = null;
    this.yaExiste = null;

    this.personasApi
      .alta({
        displayName: this.nombreNuevo.trim(),
        hasAccess: this.accesoNuevo === true,
        consentBasis: this.consentimiento.trim(),
        photo: p.foto ?? undefined,
        embedding: desempaquetar(p.vector ?? null),
        forzarNueva,
      })
      .subscribe((res) => {
        this.guardando = false;
        if (res.yaExiste) {
          // No es un error que haya que esconder: es el servidor evitando que
          // la misma persona quede cargada dos veces y que el informe le parta
          // las horas entre dos filas con el mismo nombre.
          this.yaExiste = res.yaExiste;
          this.errorAlta = res.error ?? null;
          return;
        }
        if (!res.id) {
          this.errorAlta = res.error ?? 'No se pudo dar de alta a la persona';
          return;
        }
        this.guardado = true;
      });
  }

  /** "Es la misma persona": se le suma este ángulo en vez de duplicarla. */
  sumarAExistente(p: PersonaEnVivo): void {
    const otra = this.yaExiste;
    const vector = desempaquetar(p.vector ?? null);
    if (!otra || !vector || this.guardando) return;
    this.guardando = true;
    this.personasApi.sumarRostro(otra.id, vector).subscribe((ok) => {
      this.guardando = false;
      if (!ok) {
        this.errorAlta = 'No se pudo sumarle esta foto';
        return;
      }
      this.yaExiste = null;
      this.errorAlta = null;
      this.guardado = true;
    });
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

  // ── el cronómetro ──────────────────────────────────────────────────

  /**
   * Desde cuándo se le cuenta el tiempo a esta persona.
   *
   * Si se sabe quién es, desde que empezó su visita: ese instante sobrevive a
   * que se tape o salga del cuadro un momento. Si no se sabe, desde que su
   * cuerpo apareció en el cuadro, que es lo único que hay —y alcanza, porque
   * es exacto—.
   */
  private arranqueDe(p: PersonaEnVivo): number {
    return p.desdeTs ?? p.enCuadroDesdeTs;
  }

  /** Segundos exactos que lleva ahí. */
  segundosDe(p: PersonaEnVivo): number {
    const arranque = this.arranqueDe(p);
    if (!arranque || !this.anclaServidor) return 0;
    // Lo que el worker había contado cuando contestó, más lo que pasó desde
    // que llegó su respuesta. Las dos restas son entre instantes del MISMO
    // reloj, así que no hay desfase que corregir.
    return Math.max(0, this.anclaServidor - arranque + (this.reloj - this.anclaLocal));
  }

  /**
   * El cronómetro: `mm:ss`, y `h:mm:ss` cuando pasa la hora.
   *
   * Sin redondear a minutos. "Hace 3 min" puede ser cualquier cosa entre tres
   * y cuatro minutos, y en un control de accesos el minuto es justamente el
   * dato: la diferencia entre pasar por ahí y quedarse.
   */
  cronometro(p: PersonaEnVivo): string {
    const total = Math.floor(this.segundosDe(p));
    const s = total % 60;
    const m = Math.floor(total / 60) % 60;
    const h = Math.floor(total / 3600);
    const dd = (n: number) => String(n).padStart(2, '0');
    return h > 0 ? `${h}:${dd(m)}:${dd(s)}` : `${dd(m)}:${dd(s)}`;
  }

  /** La hora exacta a la que empezó a contar, para que el número se pueda auditar. */
  desdeQueHora(p: PersonaEnVivo): string {
    const arranque = this.arranqueDe(p);
    if (!arranque) return '';
    // Pasada al reloj de quien mira la pantalla: `arranque` está en el del
    // worker, y las dos anclas son ese mismo instante en cada uno.
    const local = arranque + (this.anclaLocal - this.anclaServidor);
    return new Date(local * 1000).toLocaleTimeString('es-AR', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  }

  /**
   * Qué está midiendo el cronómetro. No es lo mismo y hay que decirlo: para
   * alguien identificado es su visita al lugar, y para un desconocido es
   * cuánto hace que se lo ve en el cuadro.
   */
  queMide(p: PersonaEnVivo): string {
    return p.desdeTs ? 'En el lugar' : 'En cuadro';
  }

  /**
   * Cómo se supo quién es. Se dice siempre, no sólo cuando la vía es débil.
   *
   * "Juan" deducido del escritorio en el que está sentado y "Juan" porque se
   * le vio la cara no valen lo mismo, y quien mira la pantalla tiene que poder
   * distinguirlos sin tener que conocer el sistema por dentro.
   */
  comoSeSabe(p: PersonaEnVivo): string {
    if (!p.personId) return '';
    switch (p.via) {
      case 'rostro':
        return 'se le vio la cara';
      case 'seguimiento':
        return 'se lo viene siguiendo desde que se le vio la cara';
      case 'apariencia':
        return 'por su ropa, desde que se le vio la cara';
      case 'puesto':
        return 'por el puesto donde se le vio la cara';
      default:
        return '';
    }
  }

  acceso(p: PersonaEnVivo): string {
    if (p.tieneAcceso === true) return this.zona ? `Sí, tiene acceso a ${this.zona}` : 'Sí, tiene acceso';
    if (p.tieneAcceso === false) return this.zona ? `NO tiene acceso a ${this.zona}` : 'NO tiene acceso';
    // No se afirma nada sobre alguien que el sistema no reconoció: decir "no
    // tiene acceso" de un desconocido sería acusarlo por no estar cargado.
    return 'No se sabe: no está identificado';
  }
}

/** El vector facial viaja en base64 dentro de la vista en vivo. */
function desempaquetar(b64: string | null): number[] | undefined {
  if (!b64) return undefined;
  try {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return Array.from(new Float32Array(bytes.buffer));
  } catch {
    return undefined;
  }
}
