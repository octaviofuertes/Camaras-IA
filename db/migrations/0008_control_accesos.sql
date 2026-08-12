-- ═══════════════════════════════════════════════════════════════════════════
-- Percepta — Migración 0008: control de accesos.
--
-- Cambia para qué se identifica a una persona. Antes era para medir su
-- actividad en un puesto de trabajo; eso se descartó. Ahora es para saber
-- QUIÉN pasó, A QUÉ HORA, y si esa persona tiene permitido estar ahí.
--
-- La diferencia no es de implementación, es de propósito, y se nota en qué
-- datos existen: ya no hay tiempo de teléfono ni ocupación de escritorios que
-- alguien pueda usar para evaluar el desempeño de un trabajador. Hay entradas y
-- salidas, que es lo que hace falta para controlar un acceso.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Lo que se va ─────────────────────────────────────────────────────────
-- El módulo de actividad por puesto se retira entero. Sus tablas se borran en
-- vez de quedar sin uso: una tabla con datos de conducta que ya nadie mira es
-- un riesgo sin contraparte, y el día que alguien la encuentre no va a saber
-- que estaba discontinuada.
DROP TABLE IF EXISTS person_activity_samples CASCADE;
DROP TABLE IF EXISTS activity_samples CASCADE;
DROP VIEW IF EXISTS activity_hourly CASCADE;

-- ── Quién puede estar acá ────────────────────────────────────────────────
-- Se responde al dar de alta a la persona, junto con su nombre. `NULL` no
-- existe a propósito: si alguien está dado de alta, alguien decidió si tiene
-- acceso o no. Un "no sé" acá se traduciría en alertas que nadie sabe atender.
ALTER TABLE persons ADD COLUMN IF NOT EXISTS has_access boolean NOT NULL DEFAULT true;
-- Quién tomó esa decisión y cuándo. Es la contracara de una alerta de acceso
-- denegado: si mañana hay que justificar por qué sonó, ésta es la persona a la
-- que se le pregunta.
ALTER TABLE persons ADD COLUMN IF NOT EXISTS access_decided_by uuid REFERENCES users(id);
ALTER TABLE persons ADD COLUMN IF NOT EXISTS access_decided_at timestamptz;
ALTER TABLE persons ADD COLUMN IF NOT EXISTS access_note text;

-- ── Quién pasó y a qué hora ──────────────────────────────────────────────
-- Una fila por presencia continua, no una por frame: "Juan estuvo entre las
-- 09:14 y las 09:52". Es lo que hace legible un control de accesos.
--
-- `ended_at` se va corriendo mientras la persona sigue en el cuadro. Cuando
-- deja de vérsela por más que la tolerancia configurada, la fila se cierra y la
-- próxima aparición abre otra.
CREATE TABLE IF NOT EXISTS person_sightings (
    id                 uuid        NOT NULL DEFAULT uuidv7(),
    started_at         timestamptz NOT NULL,
    organization_id    uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    site_id            uuid        NOT NULL,
    camera_id          uuid        NOT NULL,

    person_id          uuid        NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    ended_at           timestamptz NOT NULL,
    -- Mejor parecido facial de todo el paso. Un paso sostenido sólo por
    -- continuidad tiene un número bajo, y el informe lo muestra: no es lo mismo
    -- "se le vio la cara" que "se dedujo que era él".
    best_score         numeric(4,3) NOT NULL DEFAULT 0,
    -- Si en algún momento se le vio la cara de verdad.
    seen_by_face       boolean     NOT NULL DEFAULT false,
    -- Si esta persona tenía acceso EN EL MOMENTO del paso. Se guarda acá y no
    -- se lee de `persons` al hacer el informe: si mañana se le quita el acceso,
    -- el registro de ayer tiene que seguir diciendo lo que pasó ayer.
    had_access         boolean     NOT NULL DEFAULT true,

    created_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT person_sightings_pkey PRIMARY KEY (id, started_at),
    CONSTRAINT person_sightings_orden_chk CHECK (ended_at >= started_at)
);

SELECT create_hypertable('person_sightings', 'started_at', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS person_sightings_persona_idx
    ON person_sightings (person_id, started_at DESC);
CREATE INDEX IF NOT EXISTS person_sightings_org_idx
    ON person_sightings (organization_id, started_at DESC);
-- Para poder cerrar el paso en curso sin recorrer la tabla.
CREATE INDEX IF NOT EXISTS person_sightings_abierto_idx
    ON person_sightings (camera_id, person_id, ended_at DESC);

-- ── RLS ──────────────────────────────────────────────────────────────────
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['person_sightings'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS %I_tenant ON %I', t, t);
        EXECUTE format(
            'CREATE POLICY %I_tenant ON %I USING (organization_id = current_setting(''app.current_org'')::uuid) '
            'WITH CHECK (organization_id = current_setting(''app.current_org'')::uuid)', t, t);
    END LOOP;
END $$;

-- Se puede leer, insertar y actualizar (el paso en curso se extiende), pero no
-- borrar: un registro de accesos que la aplicación puede borrar no sirve como
-- registro de accesos. La retención lo hace por antigüedad, abajo.
GRANT SELECT, INSERT, UPDATE ON person_sightings TO percepta_app;

-- Un año: un control de accesos se consulta hacia atrás cuando pasó algo, y
-- "pasó algo" se descubre tarde.
SELECT add_retention_policy('person_sightings', INTERVAL '365 days', if_not_exists => TRUE);
