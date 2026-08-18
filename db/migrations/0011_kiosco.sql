-- ═══════════════════════════════════════════════════════════════════════════
-- Percepta — Migración 0011: la pantalla de bienvenida tiene su propia llave.
--
-- Esa pantalla cuelga de una cámara en la entrada y nadie inicia sesión en
-- ella: si necesitara un usuario real, alguien dejaría una sesión de
-- administrador abierta en el hall, que es peor que no tener control de accesos.
--
-- Por eso tiene un permiso propio, `kiosk:identify`, que habilita EXACTAMENTE
-- una cosa: mandar una foto y recibir un saludo. No permite listar a las
-- personas ni ver sus fotos ni el registro de accesos. Sin este permiso
-- separado habría que darle `persons:read`, y con eso cualquiera que llegara a
-- la pantalla podría descargarse el padrón entero de la empresa.
-- ═══════════════════════════════════════════════════════════════════════════

INSERT INTO permissions (key, description) VALUES
  ('kiosk:identify', 'Identificar a quien está frente a la pantalla de bienvenida')
ON CONFLICT (key) DO NOTHING;

-- Rol del kiosco: un solo permiso, y nada más.
INSERT INTO roles (id, organization_id, key, name, scope, is_system) VALUES
  ('00000000-0000-4000-a000-00000000000f', NULL, 'kiosk', 'Pantalla de bienvenida', 'organization', true)
ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_id, permission_key)
VALUES ('00000000-0000-4000-a000-00000000000f', 'kiosk:identify')
ON CONFLICT DO NOTHING;

-- Los administradores también lo tienen: son los que prueban la pantalla.
INSERT INTO role_permissions (role_id, permission_key)
SELECT r.id, 'kiosk:identify' FROM roles r
 WHERE r.key IN ('org_admin', 'site_admin', 'platform_superadmin')
ON CONFLICT DO NOTHING;

-- Usuario de la pantalla. No tiene contraseña utilizable: su hash es un valor
-- imposible de producir con bcrypt, así que nadie puede iniciar sesión con él
-- por el formulario. El token se emite por el endpoint del kiosco, que no pide
-- credenciales pero tampoco entrega nada más que este permiso.
INSERT INTO users (id, organization_id, email, password_hash, full_name, status)
VALUES ('00000000-0000-4000-e000-0000000000f0', '00000000-0000-4000-b000-000000000001',
        'kiosco@percepta.local', 'sin-contrasena', 'Pantalla de bienvenida', 'active')
ON CONFLICT (id) DO UPDATE SET status = 'active';

INSERT INTO user_roles (user_id, role_id)
SELECT '00000000-0000-4000-e000-0000000000f0', '00000000-0000-4000-a000-00000000000f'
WHERE NOT EXISTS (
  SELECT 1 FROM user_roles
   WHERE user_id = '00000000-0000-4000-e000-0000000000f0'
     AND role_id = '00000000-0000-4000-a000-00000000000f'
);
