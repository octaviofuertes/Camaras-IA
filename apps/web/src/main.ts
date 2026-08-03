import { bootstrapApplication } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideAnimations } from '@angular/platform-browser/animations';
import { AppComponent } from './app/app.component';
import { APP_ROUTES } from './app/app.routes';
import { authInterceptor } from './app/core/auth.interceptor';

bootstrapApplication(AppComponent, {
  providers: [
    provideRouter(APP_ROUTES),
    // El interceptor adjunta el token y renueva la sesión solo: ningún servicio
    // necesita ocuparse de la autenticación.
    provideHttpClient(withInterceptors([authInterceptor])),
    provideAnimations(),
  ],
}).catch((err) => console.error(err));
