/** Modelos de la UI. Los nombres siguen el contrato de la API (camelCase). */

export type ModuleCategory = 'security' | 'hr' | 'productivity' | 'logistics' | 'retail' | 'industry';
export type Severity = 'info' | 'low' | 'medium' | 'high' | 'critical';
export type EventStatus = 'new' | 'acknowledged' | 'confirmed' | 'dismissed' | 'false_positive';

export interface AiModule {
  id: string;
  moduleKey: string;
  name: string;
  description: string;
  category: ModuleCategory;
  /** Color del ícono en el catálogo (identidad visual del módulo). */
  color: string;
  icon: string;
}

export interface Camera {
  id: string;
  code: string;
  name: string;
  siteName: string;
  status: 'online' | 'offline' | 'degraded';
  thumbnail: string;
  /** moduleKeys asignados a esta cámara (= filas en camera_module_configs). */
  modules: string[];
}

export interface EventItem {
  id: string;
  occurredAt: string;
  eventType: string;
  title: string;
  moduleKey: string;
  cameraName: string;
  siteName: string;
  severity: Severity;
  status: EventStatus;
  confidence: number;
  reviewedBy?: string;
}

/** Etiquetas en español de las categorías (la DB guarda el enum en inglés). */
export const CATEGORY_LABEL: Record<ModuleCategory, string> = {
  security: 'Seguridad',
  hr: 'Personas',
  productivity: 'Operaciones',
  logistics: 'Logística',
  retail: 'Comercio',
  industry: 'Industria',
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: 'Crítico',
  high: 'Alto',
  medium: 'Medio',
  low: 'Bajo',
  info: 'Info',
};

export const SEVERITY_CLASS: Record<Severity, string> = {
  critical: 'badge-critico',
  high: 'badge-alto',
  medium: 'badge-medio',
  low: 'badge-bajo',
  info: 'badge-bajo',
};

export const STATUS_LABEL: Record<EventStatus, string> = {
  new: 'Nuevo',
  acknowledged: 'Reconocido',
  confirmed: 'Confirmado',
  dismissed: 'Descartado',
  false_positive: 'Falso positivo',
};
