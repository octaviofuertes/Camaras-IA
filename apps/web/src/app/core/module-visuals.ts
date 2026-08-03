import type { ModuleCategory } from './models';

/**
 * Identidad visual de los módulos. El catálogo real vive en la base
 * (`ai_modules`); acá sólo se resuelve con qué ícono y color se dibuja cada uno.
 * Si aparece un módulo nuevo sin entrada propia, cae al ícono de su categoría.
 */
export const ICON_BY_KEY: Record<string, { icon: string; color: string }> = {
  'person-detection': { icon: 'people', color: '#3b82f6' },
  'people-counting': { icon: 'people', color: '#3b82f6' },
  'helmet-detection': { icon: 'helmet', color: '#f59e0b' },
  'restricted-zone': { icon: 'zone', color: '#22c55e' },
  'abandoned-object': { icon: 'bag', color: '#f59e0b' },
  'fall-detection': { icon: 'fall', color: '#8b5cf6' },
  'suspicious-activity': { icon: 'run', color: '#ef4444' },
  'smoke-fire': { icon: 'fire', color: '#14b8a6' },
  'vehicle-detection': { icon: 'truck', color: '#8b5cf6' },
  'pallet-counting': { icon: 'box', color: '#3b82f6' },
  'excessive-dwell': { icon: 'clock', color: '#22c55e' },
  loitering: { icon: 'people', color: '#a855f7' },
  'queue-length': { icon: 'people', color: '#0ea5e9' },
  'machine-proximity': { icon: 'zone', color: '#ef4444' },
  'idle-zone': { icon: 'clock', color: '#64748b' },
};

export const ICON_BY_CATEGORY: Record<ModuleCategory, { icon: string; color: string }> = {
  security: { icon: 'zone', color: '#ef4444' },
  hr: { icon: 'people', color: '#3b82f6' },
  productivity: { icon: 'clock', color: '#22c55e' },
  logistics: { icon: 'box', color: '#8b5cf6' },
  retail: { icon: 'people', color: '#0ea5e9' },
  industry: { icon: 'zone', color: '#f59e0b' },
};
