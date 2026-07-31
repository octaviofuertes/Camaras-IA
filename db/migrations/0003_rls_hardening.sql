-- ═══════════════════════════════════════════════════════════════════════════
-- Percepta — Migración 0003: endurecimiento de RLS y restauración de la FK
-- de evidencias. Corrige fugas cross-tenant CONFIRMADAS por auditoría
-- adversarial (2026-07-31) sobre el esquema 0001.
--
-- Hallazgos que corrige:
--   H1. audit_logs, organizations, roles, user_roles quedaron SIN RLS: un tenant
--       podía leer y ESCRIBIR registros de otra organización. Grave en audit_logs,
--       que es un control de seguridad (append-only, no repudio).
--   H2. Las policies de ai_modules y users usaban USING (organization_id IS NULL
--       OR ...) sin WITH CHECK. En una policy FOR ALL, si se omite WITH CHECK
--       Postgres reutiliza USING como check de escritura => cualquier tenant podía
--       MUTAR las filas globales (p.ej. revocar un módulo del catálogo, o editar
--       usuarios de plataforma incluyendo password_hash).
--   H3. La FK evidences -> events se había eliminado asumiendo que TimescaleDB no
--       soporta FKs hacia hypertables. FALSO en 2.28.3: se crea y se hace cumplir
--       (verificado). Se restaura la integridad referencial real.
-- ═══════════════════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────────────────
-- H1a. audit_logs: aislamiento por tenant + APPEND-ONLY.
-- La auditoría no se edita ni se borra desde la aplicación: sin policies de
-- UPDATE/DELETE, esas operaciones quedan denegadas por defecto bajo RLS.
-- ───────────────────────────────────────────────────────────────────────────
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS audit_logs_select ON audit_logs;
CREATE POLICY audit_logs_select ON audit_logs FOR SELECT
    USING (organization_id = current_setting('app.current_org', true)::uuid);

-- Solo se puede insertar auditoría DE LA PROPIA organización.
DROP POLICY IF EXISTS audit_logs_insert ON audit_logs;
CREATE POLICY audit_logs_insert ON audit_logs FOR INSERT
    WITH CHECK (organization_id = current_setting('app.current_org', true)::uuid);

-- Refuerzo a nivel de privilegio: ni siquiera con una policy futura mal escrita
-- se podrá alterar el rastro de auditoría desde el rol de aplicación.
REVOKE UPDATE, DELETE ON audit_logs FROM percepta_app;

-- ───────────────────────────────────────────────────────────────────────────
-- H1b. organizations: cada tenant ve SOLO la suya. Nunca la escribe
-- (alta/baja/modificación es operación de plataforma, no de tenant).
-- ───────────────────────────────────────────────────────────────────────────
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS organizations_self ON organizations;
CREATE POLICY organizations_self ON organizations FOR SELECT
    USING (id = current_setting('app.current_org', true)::uuid);

REVOKE INSERT, UPDATE, DELETE ON organizations FROM percepta_app;

-- ───────────────────────────────────────────────────────────────────────────
-- H1c. roles y user_roles: aislamiento por tenant.
-- roles.organization_id IS NULL = rol de sistema (plantilla global): visible por
-- todos, pero NO modificable por ningún tenant (WITH CHECK excluye NULL).
-- ───────────────────────────────────────────────────────────────────────────
ALTER TABLE roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE roles FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS roles_select ON roles;
CREATE POLICY roles_select ON roles FOR SELECT
    USING (organization_id IS NULL
           OR organization_id = current_setting('app.current_org', true)::uuid);

-- Escritura: solo roles propios del tenant (nunca los de sistema).
DROP POLICY IF EXISTS roles_write ON roles;
CREATE POLICY roles_write ON roles FOR INSERT
    WITH CHECK (organization_id = current_setting('app.current_org', true)::uuid);
DROP POLICY IF EXISTS roles_update ON roles;
CREATE POLICY roles_update ON roles FOR UPDATE
    USING (organization_id = current_setting('app.current_org', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.current_org', true)::uuid);
DROP POLICY IF EXISTS roles_delete ON roles;
CREATE POLICY roles_delete ON roles FOR DELETE
    USING (organization_id = current_setting('app.current_org', true)::uuid);

-- user_roles no tiene organization_id: se acota vía el usuario al que pertenece,
-- que sí está aislado por RLS. La subconsulta a users respeta la RLS de users.
ALTER TABLE user_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_roles FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS user_roles_tenant ON user_roles;
CREATE POLICY user_roles_tenant ON user_roles
    USING (EXISTS (SELECT 1 FROM users u WHERE u.id = user_roles.user_id
                   AND u.organization_id = current_setting('app.current_org', true)::uuid))
    WITH CHECK (EXISTS (SELECT 1 FROM users u WHERE u.id = user_roles.user_id
                   AND u.organization_id = current_setting('app.current_org', true)::uuid));

-- ───────────────────────────────────────────────────────────────────────────
-- H1d. Catálogos globales inmutables para la aplicación: permissions, plans,
-- role_permissions. Se leen, no se escriben (los administra la plataforma).
-- ───────────────────────────────────────────────────────────────────────────
REVOKE INSERT, UPDATE, DELETE ON permissions   FROM percepta_app;
REVOKE INSERT, UPDATE, DELETE ON plans         FROM percepta_app;

-- role_permissions sí es escribible, pero solo para roles del propio tenant.
ALTER TABLE role_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE role_permissions FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS role_permissions_select ON role_permissions;
CREATE POLICY role_permissions_select ON role_permissions FOR SELECT
    USING (EXISTS (SELECT 1 FROM roles r WHERE r.id = role_permissions.role_id));

DROP POLICY IF EXISTS role_permissions_write ON role_permissions;
CREATE POLICY role_permissions_write ON role_permissions
    FOR INSERT WITH CHECK (EXISTS (SELECT 1 FROM roles r WHERE r.id = role_permissions.role_id
        AND r.organization_id = current_setting('app.current_org', true)::uuid));
DROP POLICY IF EXISTS role_permissions_delete ON role_permissions;
CREATE POLICY role_permissions_delete ON role_permissions
    FOR DELETE USING (EXISTS (SELECT 1 FROM roles r WHERE r.id = role_permissions.role_id
        AND r.organization_id = current_setting('app.current_org', true)::uuid));

-- ───────────────────────────────────────────────────────────────────────────
-- H2. ai_modules y users: separar VISIBILIDAD de MUTACIÓN.
-- El patrón roto era una única policy FOR ALL cuyo USING incluía las filas
-- globales; al no declarar WITH CHECK, Postgres lo reusaba como check de
-- escritura y habilitaba mutar el catálogo global desde cualquier tenant.
-- ───────────────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS ai_modules_visibility ON ai_modules;

CREATE POLICY ai_modules_select ON ai_modules FOR SELECT
    USING (organization_id IS NULL
           OR organization_id = current_setting('app.current_org', true)::uuid);

-- Escritura: SOLO módulos privados del propio tenant. Los globales (NULL) son
-- del catálogo de plataforma y ningún tenant puede tocarlos.
CREATE POLICY ai_modules_insert ON ai_modules FOR INSERT
    WITH CHECK (organization_id = current_setting('app.current_org', true)::uuid);
CREATE POLICY ai_modules_update ON ai_modules FOR UPDATE
    USING (organization_id = current_setting('app.current_org', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.current_org', true)::uuid);
CREATE POLICY ai_modules_delete ON ai_modules FOR DELETE
    USING (organization_id = current_setting('app.current_org', true)::uuid);

DROP POLICY IF EXISTS users_tenant_isolation ON users;

CREATE POLICY users_select ON users FOR SELECT
    USING (organization_id = current_setting('app.current_org', true)::uuid);
CREATE POLICY users_insert ON users FOR INSERT
    WITH CHECK (organization_id = current_setting('app.current_org', true)::uuid);
CREATE POLICY users_update ON users FOR UPDATE
    USING (organization_id = current_setting('app.current_org', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.current_org', true)::uuid);
CREATE POLICY users_delete ON users FOR DELETE
    USING (organization_id = current_setting('app.current_org', true)::uuid);

-- ───────────────────────────────────────────────────────────────────────────
-- H3. Restaurar la FK compuesta evidences -> events.
-- Verificado contra TimescaleDB 2.28.3: la FK hacia un hypertable se crea y se
-- hace cumplir. La justificación de v1.0.1 para quitarla era incorrecta.
-- ───────────────────────────────────────────────────────────────────────────
ALTER TABLE evidences
    ADD CONSTRAINT evidences_event_fk
    FOREIGN KEY (event_id, event_occurred_at)
    REFERENCES events (id, occurred_at) ON DELETE CASCADE;
