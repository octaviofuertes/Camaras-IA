// Espejo TypeScript del contrato Protobuf de detections.raw (CONTRACTS §2).
// Los tipos generados por ts-proto irán a src/generated/; estos son el modelo de dominio
// que consume rules-engine tras deserializar.

export interface BoundingBox {
  x: number;
  y: number;
  w: number;
  h: number;
} // normalizado 0..1

export interface Keypoint {
  name: string;
  x: number;
  y: number;
  score: number;
}

export interface Detection {
  classLabel: string;
  classId: number;
  confidence: number; // 0..1 crudo
  bbox: BoundingBox;
  trackId: number; // -1 si no hay tracking
  keypoints: Keypoint[];
  inZones: string[];
  attributes: Record<string, string>;
}

export interface FrameRef {
  ringBufferKey: string;
  width: number;
  height: number;
}

export interface DetectionBatch {
  schemaVersion: string;
  organizationId: string;
  siteId: string;
  cameraId: string;
  aiModuleId: string;
  moduleKey: string;
  moduleVersion: string;
  frameSeq: number;
  capturedAt: string; // ISO-8601 UTC
  inferenceMs: number;
  detections: Detection[];
  frameRef?: FrameRef;
}

export const DETECTIONS_EXCHANGE = 'detections.raw';
export const EVENTS_CREATED_EXCHANGE = 'events.created';
export const EVIDENCE_READY_EXCHANGE = 'evidence.ready';
export const NOTIFICATIONS_DISPATCH_EXCHANGE = 'notifications.dispatch';
export const USAGE_METERED_EXCHANGE = 'usage.metered';
export const AUDIT_LOG_EXCHANGE = 'audit.log';
