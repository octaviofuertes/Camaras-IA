import type { ModuleCategory } from './models';

/**
 * Identidad visual de los módulos. El catálogo real vive en la base
 * (`ai_modules`); acá sólo se resuelve con qué ícono y color se dibuja cada uno.
 * Si aparece un módulo nuevo sin entrada propia, cae al ícono de su categoría.
 */
export const ICON_BY_KEY: Record<string, { icon: string; color: string }> = {
  // Sin esta entrada el módulo cae al ícono genérico de 'security' (rojo) en
  // la pantalla de Cámaras, mientras que en Eventos y Dashboard sale celeste
  // con gente: el mismo módulo con dos caras según dónde se lo mire.
  'person-entry': { icon: 'people', color: '#0284c7' },
  'person-detection': { icon: 'people', color: '#0b5cf6' },
  'people-counting': { icon: 'people', color: '#0b5cf6' },
  'ppe-detection': { icon: 'helmet', color: '#d97706' },
  // El de casco quedó deprecado y no se puede asignar, pero sus eventos
  // viejos siguen en la pantalla de Eventos y necesitan ícono.
  'helmet-detection': { icon: 'helmet', color: '#d97706' },
  'restricted-zone': { icon: 'zone', color: '#12a05f' },
  'abandoned-object': { icon: 'bag', color: '#d97706' },
  'fall-detection': { icon: 'fall', color: '#7c4ddb' },
  'suspicious-activity': { icon: 'run', color: '#e0323f' },
  'smoke-fire': { icon: 'fire', color: '#0d9488' },
  'vehicle-detection': { icon: 'truck', color: '#7c4ddb' },
  'pallet-counting': { icon: 'box', color: '#0b5cf6' },
  'excessive-dwell': { icon: 'clock', color: '#12a05f' },
  loitering: { icon: 'people', color: '#9333ea' },
  'queue-length': { icon: 'people', color: '#0284c7' },
  'machine-proximity': { icon: 'zone', color: '#e0323f' },
  'idle-zone': { icon: 'clock', color: '#5a6b85' },
};

export const ICON_BY_CATEGORY: Record<ModuleCategory, { icon: string; color: string }> = {
  security: { icon: 'zone', color: '#e0323f' },
  hr: { icon: 'people', color: '#0b5cf6' },
  productivity: { icon: 'clock', color: '#12a05f' },
  logistics: { icon: 'box', color: '#7c4ddb' },
  retail: { icon: 'people', color: '#0284c7' },
  industry: { icon: 'zone', color: '#d97706' },
};
