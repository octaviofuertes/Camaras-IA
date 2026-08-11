-- ═══════════════════════════════════════════════════════════════════════════
-- Percepta — Migración 0002: datos semilla de desarrollo.
-- Catálogo de permisos (CONTRACTS §9), roles de sistema, planes,
-- una organización demo con sitio/cámara, y el módulo helmet-detection.
-- ═══════════════════════════════════════════════════════════════════════════

-- Catálogo canónico de permisos
INSERT INTO permissions (key, description) VALUES
  ('organizations:read',          'Ver organizaciones'),
  ('organizations:write',         'Administrar organizaciones'),
  ('sites:read',                  'Ver sucursales'),
  ('sites:write',                 'Administrar sucursales'),
  ('zones:read',                  'Ver sectores/zonas'),
  ('zones:write',                 'Administrar sectores/zonas'),
  ('cameras:read',                'Ver cámaras y streams'),
  ('cameras:write',               'Administrar cámaras y streams'),
  ('cameras:live',                'Ver video en vivo (WHEP)'),
  ('modules:read',                'Ver catálogo de módulos'),
  ('modules:install',             'Publicar/instalar módulos'),
  ('camera-module-configs:read',  'Ver configuración de módulos por cámara'),
  ('camera-module-configs:write', 'Asignar y configurar módulos por cámara'),
  ('events:read',                 'Listar/ver eventos'),
  ('events:acknowledge',          'Transición new → acknowledged'),
  ('events:resolve',              'Transición acknowledged → confirmed|dismissed|false_positive'),
  ('evidences:read',              'Ver/descargar evidencias'),
  ('notifications:read',          'Ver canales y notificaciones'),
  ('notifications:write',         'Administrar canales/plantillas'),
  ('users:read',                  'Ver usuarios'),
  ('users:write',                 'Administrar usuarios'),
  ('roles:read',                  'Ver roles'),
  ('roles:write',                 'Administrar roles y permisos'),
  ('billing:read',                'Ver planes y uso'),
  ('billing:write',               'Administrar suscripción'),
  ('audit:read',                  'Ver auditoría'),
  -- Personas identificadas. Separados del resto a propósito: dan acceso a
  -- datos biométricos y a informes con nombre y apellido, y los tiene sólo
  -- quien administra, no quien opera.
  ('persons:read',                'Ver las personas dadas de alta'),
  ('persons:write',               'Dar de alta o de baja personas (registra el consentimiento)'),
  ('reports:identified',          'Ver informes de actividad con nombre y apellido')
ON CONFLICT (key) DO NOTHING;

-- Roles de sistema (organization_id NULL, is_system)
INSERT INTO roles (id, organization_id, key, name, scope, is_system) VALUES
  ('00000000-0000-4000-a000-000000000001', NULL, 'platform_superadmin', 'SuperAdmin de plataforma', 'platform', true),
  ('00000000-0000-4000-a000-000000000002', NULL, 'org_admin',           'Administrador de empresa',  'organization', true),
  ('00000000-0000-4000-a000-000000000003', NULL, 'site_admin',          'Administrador de sucursal', 'site', true),
  ('00000000-0000-4000-a000-000000000004', NULL, 'operator',            'Operador',                  'site', true),
  ('00000000-0000-4000-a000-000000000005', NULL, 'auditor',             'Auditor',                   'organization', true)
ON CONFLICT DO NOTHING;

-- platform_superadmin: todos los permisos
INSERT INTO role_permissions (role_id, permission_key)
SELECT '00000000-0000-4000-a000-000000000001', key FROM permissions
ON CONFLICT DO NOTHING;

-- org_admin: todo menos organizations:write y modules:install
INSERT INTO role_permissions (role_id, permission_key)
SELECT '00000000-0000-4000-a000-000000000002', key FROM permissions
WHERE key NOT IN ('organizations:write','modules:install')
ON CONFLICT DO NOTHING;

-- site_admin
INSERT INTO role_permissions (role_id, permission_key)
SELECT '00000000-0000-4000-a000-000000000003', unnest(ARRAY[
  'sites:read','zones:read','zones:write','cameras:read','cameras:write','cameras:live',
  'modules:read','camera-module-configs:read','camera-module-configs:write',
  'events:read','events:acknowledge','events:resolve','evidences:read','notifications:read',
  'persons:read','persons:write','reports:identified'])
ON CONFLICT DO NOTHING;

-- operator
INSERT INTO role_permissions (role_id, permission_key)
SELECT '00000000-0000-4000-a000-000000000004', unnest(ARRAY[
  'cameras:read','cameras:live','events:read','events:acknowledge','events:resolve','evidences:read'])
ON CONFLICT DO NOTHING;

-- auditor
INSERT INTO role_permissions (role_id, permission_key)
SELECT '00000000-0000-4000-a000-000000000005', unnest(ARRAY[
  'organizations:read','sites:read','zones:read','cameras:read','modules:read',
  'camera-module-configs:read','events:read','evidences:read','notifications:read',
  'users:read','roles:read','billing:read','audit:read'])
ON CONFLICT DO NOTHING;

-- Planes
INSERT INTO plans (key, name, limits, price_usd) VALUES
  ('starter',    'Starter',    '{"maxCameras": 10,  "maxModulesPerCamera": 3,  "retentionDays": 14}',  199),
  ('business',   'Business',   '{"maxCameras": 100, "maxModulesPerCamera": 6,  "retentionDays": 30}',  899),
  ('enterprise', 'Enterprise', '{"maxCameras": 1000,"maxModulesPerCamera": 12, "retentionDays": 90}',  NULL),
  ('onprem',     'On-Premise', '{"maxCameras": -1,  "maxModulesPerCamera": -1, "retentionDays": -1}',  NULL)
ON CONFLICT (key) DO NOTHING;

-- Organización demo (dev)
INSERT INTO organizations (id, name, slug) VALUES
  ('00000000-0000-4000-b000-000000000001', 'Demo Corp', 'demo-corp')
ON CONFLICT DO NOTHING;

INSERT INTO sites (id, organization_id, name, timezone) VALUES
  ('00000000-0000-4000-b000-000000000002', '00000000-0000-4000-b000-000000000001',
   'Sucursal Mendoza', 'America/Argentina/Mendoza')
ON CONFLICT DO NOTHING;

INSERT INTO cameras (id, organization_id, site_id, name, location) VALUES
  ('00000000-0000-4000-b000-000000000003', '00000000-0000-4000-b000-000000000001',
   '00000000-0000-4000-b000-000000000002', 'Depósito 2', 'Nave sur, dock 2')
ON CONFLICT DO NOTHING;

-- Módulo global helmet-detection (manifest resumido; el completo lo publica module-registry)
INSERT INTO ai_modules (id, organization_id, module_key, name, description, category, version,
                        plugin_api_version, manifest, config_schema, config_schema_version, status)
VALUES (
  '00000000-0000-4000-c000-000000000001', NULL, 'helmet-detection', 'Uso de casco (EPP)',
  'Asistencia: señala personas cuyo casco de seguridad no es visible, para revisión humana.',
  'hr', '1.2.0', '1.0.0',
  '{"schemaVersion":"1.0.0","moduleKey":"helmet-detection","version":"1.2.0","category":"hr","model":{"backend":"yolo","artifactRef":"models://ppe/helmet-yolov8m@1.2.0"},"eventTypes":[{"type":"ppe.helmet_missing","defaultSeverity":"high","eventClass":"alert"}],"resources":{"gpu":true,"vramMb":1400,"targetFps":6}}',
  '{"type":"object","required":["zones"],"properties":{"zones":{"type":"array","items":{"type":"string"}},"minConfidence":{"type":"number","default":0.6},"minPersistenceFrames":{"type":"integer","default":5},"cooldownSeconds":{"type":"integer","default":300}}}',
  '1.0.0', 'available'
)
ON CONFLICT DO NOTHING;

-- Zona de trabajo de la cámara demo (polígono normalizado 0..1).
-- helmet-detection declara requiresZones=true y su config exige "zones".
INSERT INTO zones (id, organization_id, site_id, camera_id, name, kind, geometry) VALUES
  ('00000000-0000-4000-d000-000000000001', '00000000-0000-4000-b000-000000000001',
   '00000000-0000-4000-b000-000000000002', '00000000-0000-4000-b000-000000000003',
   'Área de carga', 'polygon',
   '{"points": [[0.10,0.35],[0.90,0.35],[0.90,0.95],[0.10,0.95]]}')
ON CONFLICT DO NOTHING;

-- Asignación módulo ↔ cámara: la relación central del producto (una cámara ejecuta
-- N módulos, cada uno con su propia configuración). Config validada contra el
-- config_schema del módulo en config_schema_version.
INSERT INTO camera_module_configs (organization_id, camera_id, ai_module_id, module_version,
                                   config_schema_version, config, priority, enabled, schedule)
VALUES (
  '00000000-0000-4000-b000-000000000001',
  '00000000-0000-4000-b000-000000000003',
  '00000000-0000-4000-c000-000000000001',
  '1.2.0', '1.0.0',
  '{"zones": ["00000000-0000-4000-d000-000000000001"], "minConfidence": 0.6,
    "minPersistenceFrames": 5, "cooldownSeconds": 300}',
  100, true,
  '{"days": ["mon","tue","wed","thu","fri"], "windows": [["06:00","22:00"]],
    "tz": "America/Argentina/Mendoza"}'
)
ON CONFLICT (camera_id, ai_module_id) DO NOTHING;
