import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { PageHeaderComponent } from '../../shared/page-header.component';
import {
  RecognitionService,
  type Persona,
  type ResultadoFoto,
  type TipoFoto,
} from '../../core/recognition.service';
import { EventsService } from '../../core/events.service';
import { ZONAS } from '../../core/zonas';
import type { EventItem } from '../../core/models';

type Modo = 'registradas' | 'detectadas' | 'manual' | 'plano';

/** Las tres fotos que se piden, con qué aporta cada una. */
interface RanuraFoto {
  tipo: TipoFoto;
  titulo: string;
  ayuda: string;
  imagen?: string;
  resultado?: ResultadoFoto;
  subiendo?: boolean;
}

@Component({
  selector: 'px-recognition',
  standalone: true,
  imports: [CommonModule, FormsModule, PageHeaderComponent],
  templateUrl: './recognition.component.html',
  styleUrls: ['./recognition.component.scss'],
})
export class RecognitionComponent implements OnInit, OnDestroy {
  private readonly api = inject(RecognitionService);
  private readonly eventos = inject(EventsService);

  modo: Modo = 'registradas';
  readonly zonas = ZONAS;

  // ── automático ─────────────────────────────────────────────────────
  /** Caras que la cámara detectó y el sistema no conoce. */
  pendientes: EventItem[] = [];
  cargandoPendientes = true;
  /** Cuál de las pendientes está desplegada. */
  abierta: string | null = null;

  // ── manual ─────────────────────────────────────────────────────────
  personas: Persona[] = [];
  /** A quién se le están sumando fotos. null = se va a crear una nueva. */
  personaElegida: Persona | null = null;

  nombre = '';
  acceso: boolean | null = null;
  consentimiento = '';
  guardando = false;
  errorAlta: string | null = null;

  ranuras: RanuraFoto[] = [
    {
      tipo: 'frontal',
      titulo: 'De frente',
      ayuda: 'La más importante: con ésta se lo reconoce.',
    },
    {
      tipo: 'perfil',
      titulo: 'De perfil',
      ayuda: 'Cubre cuando gira la cabeza.',
    },
    {
      tipo: 'espalda',
      titulo: 'De espaldas',
      ayuda: 'Queda como referencia. No sirve para reconocer: no hay cara que medir.',
    },
  ];

  /** Persona cuya baja se está confirmando. */
  bajaPedida: Persona | null = null;
  borrando = false;
  descartando = false;

  // ── cámara del navegador ───────────────────────────────────────────
  camaraAbierta: TipoFoto | null = null;
  errorCamara: string | null = null;
  private stream?: MediaStream;

  ngOnInit(): void {
    this.cargarPendientes();
    this.cargarPersonas();
    this.api.plano().subscribe((p) => (this.plano = p));
  }

  /** El plano del lugar: es el fondo de la pantalla de bienvenida. */
  plano: string | null = null;
  subiendoPlano = false;
  errorPlano: string | null = null;

  elegirPlano(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    this.errorPlano = null;

    const lector = new FileReader();
    lector.onload = () => {
      // Se achica antes de guardarla: un plano es una imagen grande y acá sólo
      // se usa como fondo de una pantalla.
      void achicar(String(lector.result), 1536).then((img) => {
        this.subiendoPlano = true;
        this.api.subirPlano(img).subscribe((ok) => {
          this.subiendoPlano = false;
          if (ok) this.plano = img;
          else this.errorPlano = 'No se pudo guardar el plano.';
        });
      });
    };
    lector.readAsDataURL(file);
    input.value = '';
  }

  ngOnDestroy(): void {
    this.cerrarCamara();
  }

  setModo(m: Modo): void {
    this.modo = m;
    this.cerrarCamara();
  }

  // ── automático ─────────────────────────────────────────────────────
  cargarPendientes(): void {
    this.cargandoPendientes = true;
    this.eventos.list().subscribe((r) => {
      // Sólo las preguntas sin responder: una vez que se contestó, la cara ya
      // se borró del evento y no hay nada que decidir.
      this.pendientes = r.items.filter(
        (e) => e.eventType === 'person.unknown' && (e.status === 'new' || e.status === 'acknowledged'),
      );
      this.cargandoPendientes = false;
    });
  }

  desplegar(e: EventItem): void {
    this.abierta = this.abierta === e.id ? null : e.id;
    if (this.abierta) {
      this.nombre = '';
      this.acceso = null;
      this.consentimiento = '';
      this.errorAlta = null;
    }
  }

  urlMiniatura(e: EventItem): string {
    return this.urlFoto(e.faceThumbnail);
  }

  /** Acepta tanto un data URL como base64 pelado: los dos llegan. */
  urlFoto(b64?: string | null): string {
    if (!b64) return '';
    return b64.startsWith('data:') ? b64 : `data:image/jpeg;base64,${b64}`;
  }

  inicial(nombre: string): string {
    return (nombre.trim()[0] ?? '?').toUpperCase();
  }

  // ── fichas registradas ─────────────────────────────────────────────
  /** Lleva al modo manual con esta persona ya elegida. */
  agregarFotosA(p: Persona): void {
    this.elegirPersona(p);
    this.modo = 'manual';
  }

  /** Asigna dónde trabaja: es lo que la pantalla de bienvenida ilumina. */
  asignarZona(p: Persona, ev: Event): void {
    const valor = (ev.target as HTMLSelectElement).value || null;
    this.api.cambiarZona(p.id, valor).subscribe(() => this.cargarPersonas());
  }

  nombreZona(clave: string | null): string {
    return this.zonas.find((z) => z.clave === clave)?.nombre ?? 'Sin zona';
  }

  cambiarAcceso(p: Persona): void {
    this.api.cambiarAcceso(p.id, !p.hasAccess).subscribe(() => this.cargarPersonas());
  }

  pedirBaja(p: Persona): void {
    this.bajaPedida = p;
  }

  confirmarBaja(): void {
    const p = this.bajaPedida;
    if (!p || this.borrando) return;
    this.borrando = true;
    this.api.baja(p.id).subscribe(() => {
      this.borrando = false;
      this.bajaPedida = null;
      if (this.personaElegida?.id === p.id) this.elegirPersona(null);
      this.cargarPersonas();
    });
  }

  /**
   * Descarta todas las caras pendientes de una vez.
   *
   * Sirve para vaciar una cola vieja: las caras que quedaron de antes de que el
   * módulo filtrara por pose son perfiles y nucas que nadie puede contestar, y
   * responderlas de a una no aporta nada. De ninguna se guarda ningún dato.
   */
  descartarTodas(): void {
    if (this.descartando || !this.pendientes.length) return;
    this.descartando = true;
    let quedan = this.pendientes.length;
    for (const e of [...this.pendientes]) {
      this.eventos.resolve(e.id, 'false_positive', 'Descartada en bloque').subscribe(() => {
        quedan -= 1;
        if (quedan <= 0) {
          this.descartando = false;
          this.cargarPendientes();
        }
      });
    }
  }

  /** Da de alta a la persona de una alerta, con la cara que venía en ella. */
  altaDesdeAlerta(e: EventItem): void {
    if (!this.puedeGuardar()) return;
    this.guardando = true;
    this.errorAlta = null;

    this.eventos
      .altaPersona({
        displayName: this.nombre.trim(),
        hasAccess: this.acceso === true,
        consentBasis: this.consentimiento.trim(),
        photo: e.faceThumbnail,
        embedding: desempaquetar(e.faceEmbedding),
      })
      .subscribe((res) => {
        this.guardando = false;
        if (!res) {
          this.errorAlta = 'No se pudo dar de alta a la persona';
          return;
        }
        if (res.yaExiste) {
          this.errorAlta = res.mensaje ?? 'Esa cara ya está dada de alta';
          return;
        }
        const nota = this.acceso
          ? `Dado de alta como ${this.nombre.trim()} (con acceso)`
          : `Dado de alta como ${this.nombre.trim()} — SIN ACCESO`;
        this.eventos.resolve(e.id, 'confirmed', nota).subscribe(() => {
          this.abierta = null;
          this.cargarPendientes();
          this.cargarPersonas();
        });
      });
  }

  /** "No trabaja acá": se descarta sin guardar nada de esa persona. */
  ignorar(e: EventItem): void {
    this.eventos.resolve(e.id, 'false_positive', 'No tiene relación con este lugar').subscribe(() => {
      this.abierta = null;
      this.cargarPendientes();
    });
  }

  // ── manual ─────────────────────────────────────────────────────────
  cargarPersonas(): void {
    this.api.listar().subscribe((p) => (this.personas = p));
  }

  elegirPersona(p: Persona | null): void {
    this.personaElegida = p;
    this.errorAlta = null;
    this.limpiarRanuras();
    if (p) {
      this.nombre = p.displayName;
      this.acceso = p.hasAccess;
      this.consentimiento = p.consentBasis;
    } else {
      this.nombre = '';
      this.acceso = null;
      this.consentimiento = '';
    }
  }

  private limpiarRanuras(): void {
    for (const r of this.ranuras) {
      r.imagen = undefined;
      r.resultado = undefined;
      r.subiendo = false;
    }
  }

  puedeGuardar(): boolean {
    return (
      this.nombre.trim().length >= 2 &&
      this.consentimiento.trim().length >= 3 &&
      this.acceso !== null
    );
  }

  /** Cuántas fotos cargadas produjeron una plantilla utilizable. */
  plantillasLogradas(): number {
    return this.ranuras.filter((r) => r.resultado?.plantilla).length;
  }

  hayFotos(): boolean {
    return this.ranuras.some((r) => r.imagen);
  }

  // ── archivos y cámara ──────────────────────────────────────────────
  elegirArchivo(r: RanuraFoto, ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    const lector = new FileReader();
    lector.onload = () => {
      void achicar(String(lector.result)).then((img) => {
        r.imagen = img;
        r.resultado = undefined;
        this.subirSiHayPersona(r);
      });
    };
    lector.readAsDataURL(file);
    input.value = '';
  }

  async abrirCamara(tipo: TipoFoto): Promise<void> {
    this.errorCamara = null;
    this.cerrarCamara();
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 } });
      this.camaraAbierta = tipo;
      // El elemento existe recién después de que Angular pinte el bloque.
      setTimeout(() => {
        const v = document.getElementById('camara-preview') as HTMLVideoElement | null;
        if (v && this.stream) {
          v.srcObject = this.stream;
          void v.play();
        }
      });
    } catch {
      this.errorCamara =
        'No se pudo abrir la cámara. Revisá que el navegador tenga permiso y que ' +
        'ninguna otra aplicación la esté usando.';
    }
  }

  sacarFoto(): void {
    const v = document.getElementById('camara-preview') as HTMLVideoElement | null;
    const r = this.ranuras.find((x) => x.tipo === this.camaraAbierta);
    if (!v || !r) return;

    const canvas = document.createElement('canvas');
    canvas.width = v.videoWidth || 1280;
    canvas.height = v.videoHeight || 720;
    canvas.getContext('2d')?.drawImage(v, 0, 0, canvas.width, canvas.height);
    void achicar(canvas.toDataURL('image/jpeg', 0.95)).then((img) => {
      r.imagen = img;
      r.resultado = undefined;
      this.subirSiHayPersona(r);
    });
    this.cerrarCamara();
  }

  cerrarCamara(): void {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = undefined;
    this.camaraAbierta = null;
  }

  /**
   * Sube la foto si la ficha ya existe.
   *
   * Si todavía no se creó la persona, la foto queda esperando: no se puede
   * guardar una plantilla sin una ficha con su consentimiento registrado, que
   * es justamente lo que hace legal guardarla.
   */
  private subirSiHayPersona(r: RanuraFoto): void {
    if (!this.personaElegida || !r.imagen) return;
    r.subiendo = true;
    this.api.subirFoto(this.personaElegida.id, r.imagen, r.tipo).subscribe((res) => {
      r.subiendo = false;
      r.resultado = res;
      this.cargarPersonas();
    });
  }

  /** Crea la ficha y sube las fotos que estaban esperando. */
  guardarManual(): void {
    if (!this.puedeGuardar() || this.guardando) return;
    this.guardando = true;
    this.errorAlta = null;

    if (this.personaElegida) {
      // Ya existe: sólo se suben las fotos pendientes.
      this.guardando = false;
      for (const r of this.ranuras) {
        if (r.imagen && !r.resultado) this.subirSiHayPersona(r);
      }
      return;
    }

    this.api
      .alta({
        displayName: this.nombre.trim(),
        hasAccess: this.acceso === true,
        consentBasis: this.consentimiento.trim(),
      })
      .subscribe((res) => {
        this.guardando = false;
        if ('error' in res) {
          this.errorAlta = res.error;
          return;
        }
        // La ficha existe: ahora sí se pueden guardar las plantillas.
        this.personaElegida = {
          id: res.id,
          displayName: this.nombre.trim(),
          active: true,
          hasAccess: this.acceso === true,
          photo: null,
          workZone: null,
          consentBasis: this.consentimiento.trim(),
          consentAt: new Date().toISOString(),
          facesCount: 0,
          createdAt: new Date().toISOString(),
        };
        for (const r of this.ranuras) {
          if (r.imagen) this.subirSiHayPersona(r);
        }
        this.cargarPersonas();
      });
  }


  trackPersona(_: number, p: Persona): string {
    return p.id;
  }

  trackEvento(_: number, e: EventItem): string {
    return e.id;
  }
}

/**
 * Achica la foto antes de mandarla.
 *
 * Una foto de teléfono son varios megabytes y en base64 crece un tercio más.
 * El detector de rostros trabaja a 640 px: más allá de mil y pico de píxeles no
 * gana nada y sólo hace más lenta la subida. Se achica sólo si hace falta, para
 * no degradar una foto que ya era chica.
 */
async function achicar(dataUrl: string, maxLado = 1280): Promise<string> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const lado = Math.max(img.width, img.height);
      if (lado <= maxLado) {
        resolve(dataUrl);
        return;
      }
      const escala = maxLado / lado;
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(img.width * escala);
      canvas.height = Math.round(img.height * escala);
      canvas.getContext('2d')?.drawImage(img, 0, 0, canvas.width, canvas.height);
      resolve(canvas.toDataURL('image/jpeg', 0.9));
    };
    // Si el navegador no puede leerla, se manda tal cual: que falle el servidor
    // con su mensaje es mejor que tragarse la foto acá en silencio.
    img.onerror = () => resolve(dataUrl);
    img.src = dataUrl;
  });
}

/** El vector facial viaja en base64 dentro de la alerta. */
function desempaquetar(b64?: string): number[] | undefined {
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
