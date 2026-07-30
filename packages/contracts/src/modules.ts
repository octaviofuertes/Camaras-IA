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
