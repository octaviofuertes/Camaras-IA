import type { Camera, EventItem } from './models';

/**
 * Datos de demostración de la UI.
 *
 * `event-service` ya sirve eventos reales desde Postgres; la pantalla de
 * Eventos los consume por HTTP y sólo cae acá si la API no está levantada.
 * Cámaras y métricas siguen siendo demo hasta que existan `device-service`
 * y `analytics-service`.
 */

export const DEMO_CAMERAS: Camera[] = [
  {
    id: 'c1', code: '01', name: 'Entrada Principal', siteName: 'Planta Mendoza',
    status: 'online', thumbnail: 'lobby',
    modules: ['abandoned-object', 'people-counting', 'loitering', 'restricted-zone', 'suspicious-activity', 'excessive-dwell'],
  },
  {
    id: 'c2', code: '02', name: 'Producción Norte', siteName: 'Planta Mendoza',
    status: 'online', thumbnail: 'factory',
    modules: ['helmet-detection', 'machine-proximity', 'fall-detection', 'people-counting', 'idle-zone'],
  },
  {
    id: 'c3', code: '03', name: 'Depósito', siteName: 'Planta Mendoza',
    status: 'online', thumbnail: 'warehouse',
    modules: ['restricted-zone', 'pallet-counting', 'vehicle-detection', 'helmet-detection', 'abandoned-object', 'fall-detection', 'suspicious-activity'],
  },
  {
    id: 'c4', code: '04', name: 'Oficina General', siteName: 'Sede Central',
    status: 'online', thumbnail: 'office',
    modules: ['people-counting', 'loitering', 'excessive-dwell', 'idle-zone'],
  },
  {
    id: 'c5', code: '05', name: 'Estacionamiento', siteName: 'Sede Central',
    status: 'online', thumbnail: 'parking',
    modules: ['vehicle-detection', 'restricted-zone', 'loitering'],
  },
  {
    id: 'c6', code: '06', name: 'Área de Cargas', siteName: 'Planta Mendoza',
    status: 'online', thumbnail: 'dock',
    modules: ['vehicle-detection', 'pallet-counting', 'fall-detection', 'helmet-detection', 'restricted-zone', 'people-counting'],
  },
];

export const DEMO_EVENTS: EventItem[] = [
  {
    id: 'e1', occurredAt: '10:24:32', eventType: 'zone.restricted_entry', title: 'Zona restringida',
    moduleKey: 'restricted-zone', cameraName: 'Cámara 03', siteName: 'Depósito',
    severity: 'critical', status: 'new', confidence: 0.94,
  },
  {
    id: 'e2', occurredAt: '10:23:11', eventType: 'ppe.helmet_missing', title: 'Sin uso de casco',
    moduleKey: 'helmet-detection', cameraName: 'Cámara 02', siteName: 'Producción Norte',
    severity: 'high', status: 'new', confidence: 0.91,
  },
  {
    id: 'e3', occurredAt: '10:22:45', eventType: 'object.abandoned', title: 'Objeto abandonado',
    moduleKey: 'abandoned-object', cameraName: 'Cámara 01', siteName: 'Entrada Principal',
    severity: 'medium', status: 'acknowledged', confidence: 0.78,
  },
  {
    id: 'e4', occurredAt: '10:21:33', eventType: 'person.loitering', title: 'Merodeo detectado',
    moduleKey: 'loitering', cameraName: 'Cámara 04', siteName: 'Oficina General',
    severity: 'medium', status: 'new', confidence: 0.72,
  },
  {
    id: 'e5', occurredAt: '10:18:07', eventType: 'person.fall', title: 'Caída detectada',
    moduleKey: 'fall-detection', cameraName: 'Cámara 06', siteName: 'Área de Cargas',
    severity: 'critical', status: 'new', confidence: 0.88,
  },
];

/** Serie de eventos por hora (24 puntos) para el gráfico de área. */
export const EVENTS_BY_HOUR = [
  4, 3, 2, 2, 3, 5, 8, 12, 15, 13, 11, 14, 18, 22, 28, 24, 19, 21, 17, 23, 26, 31, 20, 12,
];

export const EVENTS_BY_TYPE = [
  { label: 'Seguridad', value: 45, color: '#ef4444' },
  { label: 'RRHH', value: 28, color: '#8b5cf6' },
  { label: 'Operaciones', value: 25, color: '#3b82f6' },
  { label: 'Logística', value: 16, color: '#eab308' },
  { label: 'Otros', value: 10, color: '#22c55e' },
];

export const TOP_MODULES = [
  { name: 'Detección de personas', icon: 'people', color: '#3b82f6', pct: 98 },
  { name: 'Uso de casco', icon: 'helmet', color: '#f59e0b', pct: 96 },
  { name: 'Zona restringida', icon: 'zone', color: '#ef4444', pct: 94 },
  { name: 'Conteo de vehículos', icon: 'truck', color: '#22c55e', pct: 93 },
  { name: 'Detección de caídas', icon: 'fall', color: '#8b5cf6', pct: 91 },
];
