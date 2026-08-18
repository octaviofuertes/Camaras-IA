import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { catchError, of } from 'rxjs';
import { PLANO, ZONAS, zonaPorClave, type Zona } from '../../core/zonas';

/** A quién reconoció la cámara de esta pantalla. */
interface Reconocido {
  personId: string;
  displayName: string;
  photo: string | null;
  hasAccess: boolean;
  workZone: string | null;
  parecido: number;
}

/**
 * Pantalla de bienvenida.
 *
 * Va colgada de una cámara en la entrada: la persona llega, se la reconoce y se
 * le muestra su nombre, su foto y dónde le corresponde estar. No tiene ningún
 * control porque nadie la opera — quien está enfrente no es un usuario del
 * sistema, es alguien que llega a trabajar.
 *
 * Por eso también no hay forma de escribir nada acá: lo único que puede pasar
 * es que la cámara vea una cara.
 */
@Component({
  selector: 'px-welcome',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './welcome.component.html',
  styleUrls: ['./welcome.component.scss'],
})
export class WelcomeComponent implements OnInit, OnDestroy {
  private readonly http = inject(HttpClient);

  readonly plano = PLANO;
  readonly zonas = ZONAS;

  persona: Reconocido | null = null;
  /** Cuándo se lo reconoció, para saber cuándo dejar de saludarlo. */
  private visto = 0;
  camaraLista = false;
  errorCamara: string | null = null;
  buscando = false;

  private stream?: MediaStream;
  private timer?: ReturnType<typeof setInterval>;
  private reloj?: ReturnType<typeof setInterval>;
  hora = '';

  async ngOnInit(): Promise<void> {
    this.actualizarHora();
    this.reloj = setInterval(() => this.actualizarHora(), 10_000);
    await this.abrirCamara();
    // Un intento por segundo y medio: la persona llega, se para y espera. Más
    // rápido no la reconoce antes y carga al worker sin necesidad.
    this.timer = setInterval(() => this.mirar(), 1500);
  }

  ngOnDestroy(): void {
    if (this.timer) clearInterval(this.timer);
    if (this.reloj) clearInterval(this.reloj);
    this.stream?.getTracks().forEach((t) => t.stop());
  }

  private actualizarHora(): void {
    const d = new Date();
    this.hora = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  private async abrirCamara(): Promise<void> {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 1280, height: 720, facingMode: 'user' },
      });
      const v = document.getElementById('espejo') as HTMLVideoElement | null;
      if (v) {
        v.srcObject = this.stream;
        await v.play();
      }
      this.camaraLista = true;
    } catch {
      this.errorCamara =
        'No se pudo abrir la cámara. Revisá que el navegador tenga permiso para usarla.';
    }
  }

  /** Toma un cuadro y pregunta quién es. */
  private mirar(): void {
    if (!this.camaraLista || this.buscando) return;

    // A los diez segundos sin volver a reconocerlo, deja de saludarlo: se fue.
    if (this.persona && Date.now() - this.visto > 10_000) this.persona = null;

    const v = document.getElementById('espejo') as HTMLVideoElement | null;
    if (!v || !v.videoWidth) return;

    const canvas = document.createElement('canvas');
    // Se manda chico a propósito: alcanza para reconocer una cara cercana y
    // hace que el viaje de ida y vuelta entre en el segundo y medio.
    const escala = 640 / v.videoWidth;
    canvas.width = 640;
    canvas.height = Math.round(v.videoHeight * escala);
    canvas.getContext('2d')?.drawImage(v, 0, 0, canvas.width, canvas.height);

    this.buscando = true;
    this.http
      .post<{ reconocido: Reconocido | null }>('/analytics/api/v1/persons/identify', {
        image: canvas.toDataURL('image/jpeg', 0.85),
      })
      .pipe(catchError(() => of({ reconocido: null })))
      .subscribe((r) => {
        this.buscando = false;
        if (r.reconocido) {
          this.persona = r.reconocido;
          this.visto = Date.now();
        }
      });
  }

  // ── presentación ───────────────────────────────────────────────────
  get zona(): Zona | undefined {
    return zonaPorClave(this.persona?.workZone);
  }

  urlFoto(b64?: string | null): string {
    if (!b64) return '';
    return b64.startsWith('data:') ? b64 : `data:image/jpeg;base64,${b64}`;
  }

  /** El saludo de la pantalla. La hora sólo cambia la despedida del renglón. */
  saludo(): string {
    return 'Bienvenido';
  }

  momento(): string {
    const h = new Date().getHours();
    if (h < 12) return 'Buen día';
    if (h < 20) return 'Buenas tardes';
    return 'Buenas noches';
  }

  /** Sólo el nombre de pila: en una pantalla de bienvenida el apellido sobra. */
  primerNombre(nombre: string): string {
    return nombre.trim().split(/\s+/)[0] ?? nombre;
  }

  esSuZona(z: Zona): boolean {
    return !!this.persona && z.clave === this.persona.workZone;
  }
}
