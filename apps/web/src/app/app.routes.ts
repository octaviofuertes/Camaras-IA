import type { Routes } from '@angular/router';

export const APP_ROUTES: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
  {
    path: 'dashboard',
    title: 'Dashboard — VisionAI',
    loadComponent: () => import('./pages/dashboard/dashboard.component').then((m) => m.DashboardComponent),
  },
  {
    path: 'camaras',
    title: 'Cámaras — VisionAI',
    loadComponent: () => import('./pages/cameras/cameras.component').then((m) => m.CamerasComponent),
  },
  {
    path: 'eventos',
    title: 'Eventos — VisionAI',
    loadComponent: () => import('./pages/events/events.component').then((m) => m.EventsComponent),
  },
  {
    path: 'accesos',
    title: 'Registro de accesos — VisionAI',
    loadComponent: () => import('./pages/reports/reports.component').then((m) => m.ReportsComponent),
  },
  {
    path: 'usuarios',
    title: 'Usuarios — VisionAI',
    loadComponent: () => import('./pages/users/users.component').then((m) => m.UsersComponent),
  },
  { path: '**', redirectTo: 'dashboard' },
];
