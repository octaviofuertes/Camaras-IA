// Catálogo canónico de permisos RBAC — docs/CONTRACTS.md §9.
// Se descartan `events:view` (→ events:read) y `events:review` (→ acknowledge/resolve).

export const PERMISSIONS = [
  'organizations:read', 'organizations:write',
  'sites:read', 'sites:write',
  'zones:read', 'zones:write',
  'cameras:read', 'cameras:write', 'cameras:live',
  'modules:read', 'modules:install',
  'camera-module-configs:read', 'camera-module-configs:write',
  'events:read', 'events:acknowledge', 'events:resolve',
  // Alta de eventos: la ejercen los SERVICIOS del pipeline (rules-engine /
  // ai-worker), no las personas. Ningún rol de usuario la incluye.
  'events:ingest',
  'evidences:read',
  'notifications:read', 'notifications:write',
  'users:read', 'users:write',
  'roles:read', 'roles:write',
  'billing:read', 'billing:write',
  'audit:read',
  // Informes con nombre y apellido. Va aparte de `events:read` a propósito:
  // un operador que revisa alertas de seguridad no tiene por qué acceder a
  // cuánto tiempo pasó cada empleado con el teléfono. Cuanta menos gente
  // alcance ese dato, mejor.
  'persons:read', 'persons:write',
  'reports:identified',
] as const;

export type Permission = (typeof PERMISSIONS)[number];

export type SystemRole =
  | 'platform_superadmin'
  | 'org_admin'
  | 'site_admin'
  | 'operator'
  | 'auditor';

/** Scope de aplicación del rol. */
export type RoleScope = 'platform' | 'organization' | 'site';

/** Permisos semilla por rol del sistema (CONTRACTS §9). '*' = todos. */
export const SYSTEM_ROLE_PERMISSIONS: Record<SystemRole, Permission[] | ['*']> = {
  platform_superadmin: ['*'],
  org_admin: [
    'organizations:read',
    'persons:read', 'persons:write',
    'reports:identified',
    'sites:read', 'sites:write',
    'zones:read', 'zones:write',
    'cameras:read', 'cameras:write', 'cameras:live',
    'modules:read',
    'camera-module-configs:read', 'camera-module-configs:write',
    'events:read', 'events:acknowledge', 'events:resolve',
    'evidences:read',
    'notifications:read', 'notifications:write',
    'users:read', 'users:write',
    'roles:read', 'roles:write',
    'billing:read', 'billing:write',
    'audit:read',
  ],
  site_admin: [
    'sites:read',
    'persons:read', 'persons:write',
    'reports:identified',
    'zones:read', 'zones:write',
    'cameras:read', 'cameras:write', 'cameras:live',
    'modules:read',
    'camera-module-configs:read', 'camera-module-configs:write',
    'events:read', 'events:acknowledge', 'events:resolve',
    'evidences:read',
    'notifications:read',
  ],
  operator: [
    'cameras:read', 'cameras:live',
    'events:read', 'events:acknowledge', 'events:resolve',
    'evidences:read',
  ],
  auditor: [
    'organizations:read', 'sites:read', 'zones:read', 'cameras:read',
    'modules:read', 'camera-module-configs:read',
    'events:read', 'evidences:read', 'notifications:read',
    'users:read', 'roles:read', 'billing:read', 'audit:read',
  ],
};

export const ROLE_SCOPE: Record<SystemRole, RoleScope> = {
  platform_superadmin: 'platform',
  org_admin: 'organization',
  site_admin: 'site',
  operator: 'site',
  auditor: 'organization',
};
