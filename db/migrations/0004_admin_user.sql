-- ═══════════════════════════════════════════════════════════════════════════
-- Percepta — Migración 0004: usuario administrador de desarrollo.
--
-- Permite iniciar sesión de verdad (identity-service valida la contraseña con
-- bcrypt y arma el token con los permisos que el usuario tiene EN LA BASE).
-- Reemplaza al token manual que había que pegar en la consola.
--
-- En producción: crear los usuarios reales y ELIMINAR este.
-- ═══════════════════════════════════════════════════════════════════════════

-- `user_roles.site_id` se declaró como "scoping opcional por sucursal", pero
-- estaba DENTRO de la clave primaria, lo que la obliga a no ser nula: un rol de
-- alcance 'organization' (como org_admin) no podía concederse.
-- Se reemplaza la PK por un índice único que trata NULL como "toda la
-- organización", conservando que un mismo rol pueda darse por sucursal.
ALTER TABLE user_roles DROP CONSTRAINT IF EXISTS user_roles_pkey;
ALTER TABLE user_roles ALTER COLUMN site_id DROP NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS user_roles_uq
  ON user_roles (user_id, role_id, COALESCE(site_id, '00000000-0000-0000-0000-000000000000'::uuid));

-- Rol de administrador de la organización demo, con sus permisos.
INSERT INTO roles (id, organization_id, key, name, scope, is_system)
VALUES ('00000000-0000-4000-a000-0000000000a1', '00000000-0000-4000-b000-000000000001',
        'org_admin', 'Administrador de la empresa', 'organization', true)
ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_id, permission_key)
SELECT '00000000-0000-4000-a000-0000000000a1', key FROM permissions
WHERE key <> 'events:ingest'   -- alta de eventos: identidad de servicio, no humana
ON CONFLICT DO NOTHING;

-- Usuario de desarrollo. Contraseña: percepta
INSERT INTO users (id, organization_id, email, password_hash, full_name, status)
VALUES ('00000000-0000-4000-e000-000000000001', '00000000-0000-4000-b000-000000000001',
        'admin@percepta.local', '$2a$10$kFb0xGMD4knma2hTBN1s5.vWrW11DLgPC/5zHpmGQHBJFlNeLU7ke', 'Administrador', 'active')
ON CONFLICT (id) DO UPDATE SET password_hash = EXCLUDED.password_hash, status = 'active';

INSERT INTO user_roles (user_id, role_id)
SELECT '00000000-0000-4000-e000-000000000001', '00000000-0000-4000-a000-0000000000a1'
WHERE NOT EXISTS (
  SELECT 1 FROM user_roles
  WHERE user_id = '00000000-0000-4000-e000-000000000001'
    AND role_id = '00000000-0000-4000-a000-0000000000a1'
);
