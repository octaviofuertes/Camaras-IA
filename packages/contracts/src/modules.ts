// Tipos del manifest de módulo — canónico: docs/CONTRACTS.md §4.
// El JSON Schema fuente vive en ../schemas/module-manifest.schema.json.

export const MODULE_CATEGORY = ['security', 'hr', 'productivity', 'logistics', 'retail', 'industry'] as const;
export type ModuleCategory = (typeof MODULE_CATEGORY)[number];

export const MODEL_BACKEND = ['yolo', 'pytorch', 'tensorflow', 'onnx', 'tensorrt'] as const;
export type ModelBackend = (typeof MODEL_BACKEND)[number];

export const MODULE_STATUS = ['pending', 'available', 'deprecated', 'revoked'] as const;
export type ModuleStatus = (typeof MODULE_STATUS)[number];

export interface ModuleEventType {
  type: string; // ej. 'ppe.helmet_missing'
  defaultSeverity: 'info' | 'low' | 'medium' | 'high' | 'critical';
  eventClass?: 'alert' | 'telemetry';
}

export interface ModuleManifest {
  schemaVersion: '1.0.0';
  moduleKey: string;
  name: string;
  description?: string;
  version: string;
  pluginApiVersion: string;
  category: ModuleCategory;
  vendor?: string;
  signature?: string;
  model: {
    backend: ModelBackend;
    artifactRef: string;
    sha256?: string;
    classes?: string[];
  };
  input?: {
    requiresRoi?: boolean;
    requiresZones?: boolean;
    requiresLines?: boolean;
    minFps?: number;
    maxFps?: number;
    colorSpace?: 'bgr' | 'rgb' | 'gray';
  };
  configSchemaRef: string;
  configSchemaVersion?: string;
  eventTypes: ModuleEventType[];
  resources: {
    gpu: boolean;
    vramMb?: number;
    targetFps?: number;
  };
}

/**
 * Clave del módulo de ingreso de personas.
 *
 * Está acá y no suelta en cada servicio porque tres lugares distintos tienen
 * que coincidir en el string exacto: analytics-service (que cierra los
 * endpoints de personas), identity-service (que decide si emite la sesión de
 * la pantalla de bienvenida) y el frontend (que muestra u oculta el menú). Si
 * alguno se desfasa, la función queda medio prendida: menú visible con
 * endpoints que rechazan, o al revés.
 *
 * El valor tiene que ser igual al `moduleKey` de modules/person-entry/module.json
 * y a `ai_modules.module_key` en la base (migración 0013).
 */
export const MODULO_INGRESO_DE_PERSONAS = 'person-entry';
