-- ═══════════════════════════════════════════════════════════════════════════
-- Percepta — Migración 0005: aprendizaje continuo de caídas y evidencias.
--
-- Cierra el círculo: cada alerta guarda la secuencia de esqueletos que la
-- provocó; cuando un operador la revisa, su veredicto se convierte en la
-- etiqueta de esa secuencia. Con eso se reentrena el modelo periódicamente.
-- ═══════════════════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────────────────
-- Muestras de entrenamiento
--
-- Tabla propia y NO dentro de `events` por dos motivos: los datos de
-- entrenamiento deben sobrevivir a la retención de eventos (un evento se borra
-- a los 30 días, la muestra sirve para siempre), y la secuencia es voluminosa
-- comparada con el resto del evento.
-- ───────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS fall_training_samples (
    id                uuid        NOT NULL DEFAULT uuidv7(),
    organization_id   uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    camera_id         uuid        NOT NULL,
    event_id          uuid,                       -- referencia lógica a events
    event_occurred_at timestamptz,

    -- Secuencia de features de pose: (ventana × features) serializada.
    -- Se guarda ya procesada, no el video: ocupa poco, no identifica a nadie
    -- y es exactamente lo que consume el modelo.
    sequence          jsonb       NOT NULL,
    window_frames     integer     NOT NULL,
    n_features        integer     NOT NULL,

    -- Lo que dijo el modelo cuando ocurrió (para medir su evolución).
    predicted         numeric(5,4),
    rule_confidence   numeric(5,4),

    -- NULL = todavía sin revisar. Lo completa el operador desde el dashboard.
    label             smallint,
    label_source      text        NOT NULL DEFAULT 'pending',
    labeled_by        uuid,
    labeled_at        timestamptz,

    created_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT fall_samples_pkey PRIMARY KEY (id),
    CONSTRAINT fall_samples_label_chk CHECK (label IS NULL OR label IN (0, 1)),
    CONSTRAINT fall_samples_source_chk CHECK (label_source IN ('pending', 'human', 'dataset', 'auto'))
);

CREATE INDEX IF NOT EXISTS fall_samples_org_idx ON fall_training_samples (organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS fall_samples_label_idx ON fall_training_samples (organization_id, label) WHERE label IS NOT NULL;
CREATE INDEX IF NOT EXISTS fall_samples_event_idx ON fall_training_samples (event_id);

ALTER TABLE fall_training_samples ENABLE ROW LEVEL SECURITY;
ALTER TABLE fall_training_samples FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS fall_samples_tenant ON fall_training_samples;
CREATE POLICY fall_samples_tenant ON fall_training_samples
    USING (organization_id = current_setting('app.current_org', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.current_org', true)::uuid);

-- ───────────────────────────────────────────────────────────────────────────
-- Evidencias: nombre que le pone el operador al confirmar.
-- ───────────────────────────────────────────────────────────────────────────
ALTER TABLE evidences ADD COLUMN IF NOT EXISTS title text;
ALTER TABLE evidences ADD COLUMN IF NOT EXISTS created_by uuid;

-- Título que el operador le da al evento al confirmarlo ("Caída pasillo B").
ALTER TABLE events ADD COLUMN IF NOT EXISTS review_title text;

GRANT SELECT, INSERT, UPDATE, DELETE ON fall_training_samples TO percepta_app;
