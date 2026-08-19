import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { AuthService } from '../../core/auth.service';

/**
 * Puerta de entrada.
 *
 * Dos caminos que no son dos formas de lo mismo: por uno entra una persona que
 * va a administrar el sistema, y por el otro se enciende una pantalla que
 * cuelga de una cámara en la entrada y que nadie opera.
 *
 * El segundo no pide contraseña a propósito. Lo que lo hace aceptable es lo que
 * entrega: una sesión con un solo permiso, que sólo sirve para preguntar si el
 * sistema conoce una cara. No puede ver el padrón, ni las fotos, ni a qué hora
 * entró nadie.
 */
@Component({
  selector: 'px-login',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './login.component.html',
  styleUrls: ['./login.component.scss'],
})
export class LoginComponent {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  email = '';
  password = '';
  entrando = false;
  error: string | null = null;

  entrar(): void {
    if (this.entrando || !this.email.trim() || !this.password) return;
    this.entrando = true;
    this.error = null;

    this.auth.login(this.email.trim(), this.password).subscribe((ok) => {
      this.entrando = false;
      if (!ok) {
        this.error = 'Email o contraseña incorrectos.';
        return;
      }
      void this.router.navigateByUrl('/dashboard');
    });
  }

  abrirPantallaDeCamara(): void {
    if (this.entrando) return;
    this.entrando = true;
    this.error = null;

    this.auth.entrarComoKiosco().subscribe((r) => {
      this.entrando = false;
      if (!r.ok) {
        this.error =
          r.motivo ??
          'No se pudo abrir la pantalla de bienvenida. Revisá que el servicio de identidad ' +
            'esté corriendo.';
        return;
      }
      void this.router.navigateByUrl('/bienvenida');
    });
  }
}
