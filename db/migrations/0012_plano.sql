-- ═══════════════════════════════════════════════════════════════════════════
-- Percepta — Migración 0012: el plano del lugar.
--
-- La pantalla de bienvenida dibuja las zonas sobre el plano de la oficina. Ese
-- plano lo sube cada empresa: no hay dos edificios iguales y una imagen fija en
-- el código sólo serviría para el primero.
--
-- Va en una tabla propia y no en `organizations.settings` porque es una imagen
-- de varios cientos de KB, y `settings` se lee entero en cada consulta que toca
-- la organización.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS floor_plans (
    organization_id uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    -- La imagen como data URL. Se sirve tal cual a la pantalla.
    image           text        NOT NULL,
    updated_by      uuid        REFERENCES users(id),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT floor_plans_pkey PRIMARY KEY (organization_id)
);

ALTER TABLE floor_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE floor_plans FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS floor_plans_tenant ON floor_plans;
CREATE POLICY floor_plans_tenant ON floor_plans
    USING (organization_id = current_setting('app.current_org')::uuid)
    WITH CHECK (organization_id = current_setting('app.current_org')::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON floor_plans TO percepta_app;
