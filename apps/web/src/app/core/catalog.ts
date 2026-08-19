import type { AiModule } from './models';

/**
 * Catálogo de módulos de IA para la UI.
 *
 * En producción esto lo sirve `module-registry` desde la tabla `ai_modules`
 * (cada entrada nace de un `module.json`). Acá está embebido para poder
 * construir y validar la experiencia de asignación antes de que el registry
 * exponga su endpoint.
 */
export const AI_MODULES: AiModule[] = [
  {
    id: 'm-suspicious',
    moduleKey: 'suspicious-activity',
    name: 'Posible robo / Actividad sospechosa',
    description: 'Señala comportamientos atípicos para revisión humana',
    category: 'security',
    color: '#ef4444',
    icon: 'run',
  },
  {
    id: 'm-access',
    moduleKey: 'person-entry',
    name: 'Ingreso de personas',
    description: 'Reconoce quién entra, registra a qué hora y alerta si no tiene acceso',
    category: 'security',
    color: '#0ea5e9',
    icon: 'people',
  },
  {
    id: 'm-fall',
    moduleKey: 'fall-detection',
    name: 'Registro de caídas',
    description: 'Detecta posibles caídas de personas',
    category: 'security',
    color: '#8b5cf6',
    icon: 'fall',
  },
  {
    id: 'm-ppe',
    moduleKey: 'helmet-detection',
    name: 'Uso de protección personal (EPP)',
    description: 'Casco, chaleco, guantes y otros elementos',
    category: 'hr',
    color: '#f59e0b',
    icon: 'helmet',
  },
  {
    id: 'm-people-count',
    moduleKey: 'people-counting',
    name: 'Conteo de personas',
    description: 'Cuenta y analiza el flujo de personas',
    category: 'hr',
    color: '#3b82f6',
    icon: 'people',
  },
  {
    id: 'm-zone',
    moduleKey: 'restricted-zone',
    name: 'Zona restringida',
    description: 'Ingreso o permanencia en zonas delimitadas',
    category: 'security',
    color: '#22c55e',
    icon: 'zone',
  },
  {
    id: 'm-abandoned',
    moduleKey: 'abandoned-object',
    name: 'Objetos abandonados',
    description: 'Objetos que quedan sin supervisión',
    category: 'security',
    color: '#f59e0b',
    icon: 'bag',
  },
  {
    id: 'm-smoke',
    moduleKey: 'smoke-fire',
    name: 'Detección de humo / fuego',
    description: 'Indicios de humo o fuego en el área',
    category: 'security',
    color: '#14b8a6',
    icon: 'fire',
  },
  {
    id: 'm-vehicle',
    moduleKey: 'vehicle-detection',
    name: 'Detección de vehículos',
    description: 'Detecta y clasifica vehículos',
    category: 'logistics',
    color: '#8b5cf6',
    icon: 'truck',
  },
  {
    id: 'm-pallets',
    moduleKey: 'pallet-counting',
    name: 'Conteo de mercancías / pallets',
    description: 'Cuenta pallets, cajas u otros objetos',
    category: 'logistics',
    color: '#3b82f6',
    icon: 'box',
  },
  {
    id: 'm-dwell',
    moduleKey: 'excessive-dwell',
    name: 'Permanencia excesiva',
    description: 'Permanencia mayor a la esperada en un sector',
    category: 'hr',
    color: '#22c55e',
    icon: 'clock',
  },
  {
    id: 'm-loitering',
    moduleKey: 'loitering',
    name: 'Merodeo',
    description: 'Permanencia prolongada sin actividad clara',
    category: 'security',
    color: '#a855f7',
    icon: 'people',
  },
  {
    id: 'm-queue',
    moduleKey: 'queue-length',
    name: 'Longitud de filas',
    description: 'Mide filas y tiempos de espera',
    category: 'retail',
    color: '#0ea5e9',
    icon: 'people',
  },
  {
    id: 'm-machine',
    moduleKey: 'machine-proximity',
    name: 'Cercanía a maquinaria',
    description: 'Personas próximas a equipos en operación',
    category: 'industry',
    color: '#ef4444',
    icon: 'zone',
  },
  {
    id: 'm-idle',
    moduleKey: 'idle-zone',
    name: 'Zonas sin actividad',
    description: 'Sectores sin movimiento durante el turno',
    category: 'productivity',
    color: '#64748b',
    icon: 'clock',
  },
];

/** Filtros de categoría de la pantalla de Cámaras (mockup §3c). */
export const CATEGORY_FILTERS: { key: 'all' | AiModule['category']; label: string }[] = [
  { key: 'all', label: 'Todos' },
  { key: 'security', label: 'Seguridad' },
  { key: 'hr', label: 'Personas' },
  { key: 'productivity', label: 'Operaciones' },
  { key: 'logistics', label: 'Logística' },
];
