-- ═══════════════════════════════════════════════════════════════════════════
-- Identificación de personas (control de accesos)
--
-- ESTO ES DATO BIOMÉTRICO. En Argentina la Ley 25.326 lo clasifica como dato
-- sensible: exige consentimiento expreso, informado y por escrito del titular,
-- y finalidad declarada. En la UE es el art. 9 del RGPD. Varios estados de EEUU
-- (Illinois BIPA, Texas, Washington) fijan indemnizaciones por infracción.
--
-- Por eso el consentimiento NO es una casilla de la interfaz: es una restricción
-- de esta tabla. No se puede dar de alta a un empleado sin registrar quién
-- documentó su consentimiento, cuándo, y con qué base legal. Una garantía que
-- vive sólo en la aplicación se pierde el día que alguien escriba otro cliente
-- contra esta base; ésta no.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS persons (
    id                 uuid        NOT NULL DEFAULT uuidv7(),
    organization_id    uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

    display_name       text        NOT NULL,
    -- 'employee' es el único tipo con plantilla facial almacenada. No existe un
    -- tipo "ignorado": a quien no trabaja acá no se le guarda nada, y por eso no
    -- tiene fila. Ver la nota sobre reincidencia más abajo.
    kind               text        NOT NULL DEFAULT 'employee',

    -- ── Consentimiento ────────────────────────────────────────────────────
    -- Quién lo documentó, cuándo, y bajo qué base. Los tres obligatorios.
    consent_recorded_by uuid       NOT NULL REFERENCES users(id),
    consent_at          timestamptz NOT NULL DEFAULT now(),
    consent_basis       text       NOT NULL,

    active             boolean     NOT NULL DEFAULT true,
    notes              text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT persons_pkey PRIMARY KEY (id),
    CONSTRAINT persons_kind_chk CHECK (kind IN ('employee')),
    -- Una base legal vacía es lo mismo que no tenerla.
    CONSTRAINT persons_consent_chk CHECK (length(btrim(consent_basis)) >= 3),
    CONSTRAINT persons_nombre_chk CHECK (length(btrim(display_name)) >= 2)
);

CREATE INDEX IF NOT EXISTS persons_org_idx ON persons (organization_id) WHERE active;

-- Plantillas faciales. Sólo de personas dadas de alta con consentimiento: la
-- clave foránea con ON DELETE CASCADE es el derecho de supresión implementado —
-- borrar la persona borra su biometría, sin pasos que alguien pueda olvidar.
CREATE TABLE IF NOT EXISTS person_faces (
    id              uuid        NOT NULL DEFAULT uuidv7(),
    organization_id uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    person_id       uuid        NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    -- Vector de 512 dimensiones, normalizado. NO se guarda la foto: de la
    -- plantilla no se puede reconstruir la cara, y una foto sí es una foto.
    embedding       real[]      NOT NULL,
    quality         real        NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT person_faces_pkey PRIMARY KEY (id),
    CONSTRAINT person_faces_dim_chk CHECK (array_length(embedding, 1) = 512)
);

CREATE INDEX IF NOT EXISTS person_faces_person_idx ON person_faces (person_id);
CREATE INDEX IF NOT EXISTS person_faces_org_idx ON person_faces (organization_id);

-- ── Aislamiento entre empresas ─────────────────────────────────────────────
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['persons', 'person_faces'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS %I_select ON %I', t, t);
        EXECUTE format(
            'CREATE POLICY %I_select ON %I FOR SELECT USING '
            '(organization_id = current_setting(''app.current_org'', true)::uuid)', t, t);
        EXECUTE format('DROP POLICY IF EXISTS %I_insert ON %I', t, t);
        EXECUTE format(
            'CREATE POLICY %I_insert ON %I FOR INSERT WITH CHECK '
            '(organization_id = current_setting(''app.current_org'', true)::uuid)', t, t);
    END LOOP;

    -- Sólo `persons` admite modificación y baja: es lo que sostiene el derecho
    -- de rectificación y de supresión del titular. Las mediciones no se editan.
    EXECUTE 'DROP POLICY IF EXISTS persons_update ON persons';
    EXECUTE 'CREATE POLICY persons_update ON persons FOR UPDATE USING '
            '(organization_id = current_setting(''app.current_org'', true)::uuid) '
            'WITH CHECK (organization_id = current_setting(''app.current_org'', true)::uuid)';
    EXECUTE 'DROP POLICY IF EXISTS persons_delete ON persons';
    EXECUTE 'CREATE POLICY persons_delete ON persons FOR DELETE USING '
            '(organization_id = current_setting(''app.current_org'', true)::uuid)';
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON persons TO percepta_app;
-- Las plantillas se borran por cascada al borrar la persona; no hace falta
-- (ni conviene) poder borrarlas sueltas dejando a la persona sin biometría
-- pero registrada como identificable.
GRANT SELECT, INSERT ON person_faces TO percepta_app;

-- Al dar de baja a una persona se van sus plantillas por cascada. Sus tiempos
-- también: no tiene sentido conservar "cuánto trabajó" de alguien a quien ya no
-- se puede nombrar, y conservarlo sería guardar el dato sin su titular.

-- ── Catálogo ───────────────────────────────────────────────────────────────
INSERT INTO ai_modules (id, organization_id, module_key, name, description, category, version,
                        plugin_api_version, manifest, config_schema, config_schema_version, status)
VALUES (
    '00000000-0000-4000-c000-0000000ac702',
    NULL,
    'person-identification',
    'Identificación de personas',
    'Reconoce a los empleados dados de alta para poder atribuir la actividad a cada uno. Requiere consentimiento expreso registrado. A quien no está dado de alta no se le guarda ningún dato biométrico.',
    'hr',
    '1.0.0',
    '1.0.0',
    '{"schemaVersion":"1.0.0","moduleKey":"person-identification","version":"1.0.0","category":"hr","model":{"backend":"onnx","artifactRef":"buffalo_l","classes":["face"]},"input":{"requiresZones":false,"minFps":1,"maxFps":5,"colorSpace":"bgr"},"eventTypes":[{"type":"person.unknown","defaultSeverity":"low","eventClass":"alert"}],"resources":{"gpu":false,"vramMb":0,"targetFps":2}}',
    '{"type":"object","properties":{"matchThreshold":{"type":"number","default":0.42},"minFaceSize":{"type":"number","default":0.05},"askCooldownMinutes":{"type":"number","default":10}}}',
    '1.0.0',
    'available'
)
ON CONFLICT (id) DO UPDATE
    SET name = EXCLUDED.name,
        description = EXCLUDED.description,
        manifest = EXCLUDED.manifest,
        config_schema = EXCLUDED.config_schema,
        status = EXCLUDED.status;
