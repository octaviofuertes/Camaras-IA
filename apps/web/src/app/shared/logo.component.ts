import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

/**
 * La marca de e-Sueldos: el archivo original, no un dibujo parecido.
 *
 * El PNG que entregó la marca es blanco sobre transparente, así que se usa de
 * máscara y el color lo pone `currentColor`. Así el mismo archivo sirve en
 * blanco sobre el azul de la barra y en azul sobre una tarjeta clara, sin
 * mantener dos versiones que tarde o temprano quedan distintas, y sin
 * redibujar nada: la silueta es la del original, píxel por píxel.
 *
 * Los dos archivos salen del mismo recorte del original (`logo-e-sueldos.png`
 * de la marca): el lockup completo y la nube sola, para donde no entra el
 * nombre. Las proporciones de abajo son las de esos recortes; si se cambian
 * los archivos hay que actualizarlas.
 */
const PROPORCION_LOCKUP = 936 / 138;  // nube + "e-Sueldos"
const PROPORCION_NUBE = 244 / 137;    // sólo el isotipo

@Component({
  selector: 'px-logo',
  standalone: true,
  imports: [CommonModule],
  template: `
    <span
      class="logo"
      [class.solo-nube]="!conNombre"
      role="img"
      aria-label="e-Sueldos"
      [style.height.px]="alto"
      [style.width.px]="alto * (conNombre ? proporcionLockup : proporcionNube)"
    ></span>
  `,
  styles: [`
    .logo {
      display: inline-block;
      /* El color sale del contexto; la forma, de la máscara. */
      background-color: currentColor;
      -webkit-mask: url('/assets/logo-e-sueldos.png') no-repeat center / contain;
      mask: url('/assets/logo-e-sueldos.png') no-repeat center / contain;
    }
    .solo-nube {
      -webkit-mask-image: url('/assets/isotipo-e-sueldos.png');
      mask-image: url('/assets/isotipo-e-sueldos.png');
    }
  `],
})
export class LogoComponent {
  /** Alto de la marca en píxeles. El ancho sale de la proporción del archivo. */
  @Input() alto = 26;
  /** false para dejar sólo la nube, donde no entra el nombre. */
  @Input() conNombre = true;

  protected readonly proporcionLockup = PROPORCION_LOCKUP;
  protected readonly proporcionNube = PROPORCION_NUBE;
}
