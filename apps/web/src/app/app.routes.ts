import type { Routes } from '@angular/router';
import { moduloIngresoAsignado, sesionRequerida, soloPersonas } from './core/auth.guard';

export const APP_ROUTES: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'login' },
  {
    path: 'login',
    title: 'Ingresar — e-Sueldos',
    loadComponent: () => import('./pages/login/login.component').then((m) => m.LoginComponent),
  },
  {
    path: 'dashboard',
    canActivate: [soloPersonas],
    title: 'Dashboard — e-Sueldos',
    loadComponent: () => import('./pages/dashboard/dashboard.component').then((m) => m.DashboardComponent),
  },
  {
    path: 'camaras',
    canActivate: [soloPersonas],
    title: 'Cámaras — e-Sueldos',
    loadComponent: () => import('./pages/cameras/cameras.component').then((m) => m.CamerasComponent),
  },
  {
    path: 'eventos',
    canActivate: [soloPersonas],
    title: 'Eventos — e-Sueldos',
    loadComponent: () => import('./pages/events/events.component').then((m) => m.EventsComponent),
  },
  {
    path: 'bienvenida',
    title: 'Bienvenida — e-Sueldos',
    canActivate: [sesionRequerida],
    loadComponent: () =>
      import('./pages/welcome/welcome.component').then((m) => m.WelcomeComponent),
  },
  {
    path: 'reconocimiento',
    canActivate: [soloPersonas, moduloIngresoAsignado],
    title: 'Reconocimiento — e-Sueldos',
    loadComponent: () =>
      import('./pages/recognition/recognition.component').then((m) => m.RecognitionComponent),
  },
  {
    path: 'accesos',
    canActivate: [soloPersonas, moduloIngresoAsignado],
    title: 'Registro de accesos — e-Sueldos',
    loadComponent: () => import('./pages/reports/reports.component').then((m) => m.ReportsComponent),
  },
  {
    path: 'usuarios',
    canActivate: [soloPersonas],
    title: 'Usuarios — e-Sueldos',
    loadComponent: () => import('./pages/users/users.component').then((m) => m.UsersComponent),
  },
  { path: '**', redirectTo: 'login' },
];
