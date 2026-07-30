-- ═══════════════════════════════════════════════════════════════════════════
-- Percepta — Migración 0001: esquema canónico inicial.
-- Fuente única de verdad: docs/CONTRACTS.md (§5 events, §6 evidences,
-- §7 camera_module_configs, §8 ai_modules) + docs/02-modelo-de-datos.md.
-- Multitenancy: organization_id + RLS FORZADO en todas las tablas de tenant.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- uuidv7(): PG15 no lo trae nativo; shim sobre gen_random_uuid() con prefijo temporal.
-- (En PG18+ reemplazar por el nativo. La propiedad que importa: ordenable por tiempo.)
CREATE OR REPLACE FUNCTION uuidv7() RETURNS uuid AS $$
DECLARE
  unix_ms bigint := floor(extract(epoch FROM clock_timestamp()) * 1000);
  buf bytea := gen_random_bytes(16);
BEGIN
  buf := set_byte(buf, 0, (unix_ms >> 40)::int & 255);
  buf := set_byte(buf, 1, (unix_ms >> 32)::int & 255);
  buf := set_byte(buf, 2, (unix_ms >> 24)::int & 255);
  buf := set_byte(buf, 3, (unix_ms >> 16)::int & 255);
  buf := set_byte(buf, 4, (unix_ms >> 8)::int  & 255);
  buf := set_byte(buf, 5, unix_ms::int & 255);
  buf := set_byte(buf, 6, ((get_byte(buf, 6) & 15) | 112)); -- version 7
  buf := set_byte(buf, 8, ((get_byte(buf, 8) & 63) | 128)); -- variant
  RETURN encode(buf, 'hex')::uuid;
END;
$$ LANGUAGE plpgsql VOLATILE;

-- ───────────────────────────────────────────────────────────────────────────
-- Tenancy
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE organizations (
    id            uuid        NOT NULL DEFAULT uuidv7(),
    name          text        NOT NULL,
    slug          text        NOT NULL UNIQUE,
    status        text        NOT NULL DEFAULT 'active',
    settings      jsonb       NOT NULL DEFAULT '{}',
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT organizations_pkey PRIMARY KEY (id),
    CONSTRAINT organizations_status_chk CHECK (status IN ('active','suspended','archived'))
);

CREATE TABLE sites (
    id              uuid        NOT NULL DEFAULT uuidv7(),
    organization_id uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            text        NOT NULL,
    timezone        text        NOT NULL DEFAULT 'UTC',
    address         text,
    settings        jsonb       NOT NULL DEFAULT '{}',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sites_pkey PRIMARY KEY (id)
);
CREATE INDEX sites_org_idx ON sites (organization_id);

CREATE TABLE zones (
    id              uuid        NOT NULL DEFAULT uuidv7(),
    organization_id uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    site_id         uuid        NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    camera_id       uuid,       -- FK diferida: se agrega tras crear cameras
    name            text        NOT NULL,
    kind            text        NOT NULL DEFAULT 'polygon',  -- polygon | line | roi
    geometry        jsonb       NOT NULL,                    -- puntos normalizados 0..1
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT zones_pkey PRIMARY KEY (id),
    CONSTRAINT zones_kind_chk CHECK (kind IN ('polygon','line','roi'))
);
CREATE INDEX zones_site_idx ON zones (site_id);

-- ───────────────────────────────────────────────────────────────────────────
-- Dispositivos
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE cameras (
    id              uuid        NOT NULL DEFAULT uuidv7(),
    organization_id uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    site_id         uuid        NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    name            text        NOT NULL,
    location        text,
    status          text        NOT NULL DEFAULT 'offline', -- online|offline|degraded|disabled
    settings        jsonb       NOT NULL DEFAULT '{}',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cameras_pkey PRIMARY KEY (id),
    CONSTRAINT cameras_status_chk CHECK (status IN ('online','offline','degraded','disabled'))
);
CREATE INDEX cameras_org_site_idx ON cameras (organization_id, site_id);

ALTER TABLE zones
    ADD CONSTRAINT zones_camera_fk FOREIGN KEY (camera_id)
    REFERENCES cameras(id) ON DELETE CASCADE;

CREATE TABLE streams (
    id              uuid        NOT NULL DEFAULT uuidv7(),
    organization_id uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    camera_id       uuid        NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    kind            text        NOT NULL DEFAULT 'main',  -- main | sub
    -- La URL RTSP NO incluye credenciales: van en el vault, referenciadas por secret_ref.
    rtsp_url        text        NOT NULL,
    secret_ref      text,
    width           integer,
    height          integer,
    fps             numeric(5,2),
    codec           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT streams_pkey PRIMARY KEY (id),
    CONSTRAINT streams_kind_chk CHECK (kind IN ('main','sub'))
);
CREATE INDEX streams_camera_idx ON streams (camera_id);

-- ───────────────────────────────────────────────────────────────────────────
-- Catálogo de módulos de IA (CONTRACTS §8)
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE ai_modules (
    id                    uuid        NOT NULL DEFAULT uuidv7(),
    organization_id       uuid REFERENCES organizations(id) ON DELETE CASCADE, -- NULL = global
    module_key            text        NOT NULL,
    name                  text        NOT NULL,
    description           text,
    category              text        NOT NULL,
    version               text        NOT NULL,
    plugin_api_version    text        NOT NULL,
    manifest              jsonb       NOT NULL,
    config_schema         jsonb       NOT NULL,
    config_schema_version text        NOT NULL,
    min_core_version      text,
    signature             text,
    status                text        NOT NULL DEFAULT 'pending',
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ai_modules_pkey PRIMARY KEY (id),
    CONSTRAINT ai_modules_category_chk CHECK (category IN
        ('security','hr','productivity','logistics','retail','industry')),
    CONSTRAINT ai_modules_status_chk CHECK (status IN
        ('pending','available','deprecated','revoked'))
);
CREATE UNIQUE INDEX ai_modules_key_ver_uq
    ON ai_modules (COALESCE(organization_id, '00000000-0000-0000-0000-000000000000'::uuid),
                   module_key, version);

-- Asignación módulo ↔ cámara (CONTRACTS §7)
CREATE TABLE camera_module_configs (
    id                    uuid        NOT NULL DEFAULT uuidv7(),
    organization_id       uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    camera_id             uuid        NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    ai_module_id          uuid        NOT NULL REFERENCES ai_modules(id),
    module_version        text        NOT NULL,
    config_schema_version text        NOT NULL,
    config                jsonb       NOT NULL DEFAULT '{}',
    priority              smallint    NOT NULL DEFAULT 100,
    enabled               boolean     NOT NULL DEFAULT true,
    schedule              jsonb       NOT NULL DEFAULT '{}',
    migration_status      text        NOT NULL DEFAULT 'current',
    updated_by            uuid,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cmc_pkey PRIMARY KEY (id),
    CONSTRAINT cmc_unique UNIQUE (camera_id, ai_module_id),
    CONSTRAINT cmc_migration_chk CHECK (migration_status IN ('current','pending_review','migrating'))
);
CREATE INDEX cmc_camera_idx ON camera_module_configs (camera_id) WHERE enabled;
CREATE INDEX cmc_config_gin ON camera_module_configs USING gin (config jsonb_path_ops);

-- ───────────────────────────────────────────────────────────────────────────
-- Identidad y RBAC
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE users (
    id              uuid        NOT NULL DEFAULT uuidv7(),
    organization_id uuid REFERENCES organizations(id) ON DELETE CASCADE, -- NULL = superadmin plataforma
    email           text        NOT NULL UNIQUE,
    password_hash   text        NOT NULL,
    full_name       text        NOT NULL,
    status          text        NOT NULL DEFAULT 'active',
    mfa_enabled     boolean     NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT users_pkey PRIMARY KEY (id),
    CONSTRAINT users_status_chk CHECK (status IN ('active','invited','suspended'))
);

CREATE TABLE roles (
    id              uuid        NOT NULL DEFAULT uuidv7(),
    organization_id uuid REFERENCES organizations(id) ON DELETE CASCADE, -- NULL = rol de sistema
    key             text        NOT NULL,
    name            text        NOT NULL,
    scope           text        NOT NULL DEFAULT 'organization', -- platform|organization|site
    is_system       boolean     NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT roles_pkey PRIMARY KEY (id),
    CONSTRAINT roles_scope_chk CHECK (scope IN ('platform','organization','site'))
);
CREATE UNIQUE INDEX roles_key_uq
    ON roles (COALESCE(organization_id, '00000000-0000-0000-0000-000000000000'::uuid), key);

-- Catálogo de permisos (CONTRACTS §9): key = 'recurso:accion'
CREATE TABLE permissions (
    key         text NOT NULL,
    description text,
    CONSTRAINT permissions_pkey PRIMARY KEY (key)
);

CREATE TABLE role_permissions (
    role_id        uuid NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_key text NOT NULL REFERENCES permissions(key) ON DELETE CASCADE,
    CONSTRAINT role_permissions_pkey PRIMARY KEY (role_id, permission_key)
);

CREATE TABLE user_roles (
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id    uuid NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    site_id    uuid REFERENCES sites(id) ON DELETE CASCADE, -- scoping opcional por sucursal
    granted_at timestamptz NOT NULL DEFAULT now(),
    granted_by uuid REFERENCES users(id),
    CONSTRAINT user_roles_pkey PRIMARY KEY (user_id, role_id, site_id)
);

-- ───────────────────────────────────────────────────────────────────────────
-- Eventos (CONTRACTS §5) — hypertable + human-in-the-loop CHECK
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE events (
    id                 uuid          NOT NULL DEFAULT uuidv7(),
    occurred_at        timestamptz   NOT NULL,
    organization_id    uuid          NOT NULL,
    site_id            uuid          NOT NULL,
    camera_id          uuid          NOT NULL,
    ai_module_id       uuid          NOT NULL,
    module_key         text          NOT NULL,
    module_version     text          NOT NULL,
    event_type         text          NOT NULL,
    event_class        text          NOT NULL DEFAULT 'alert',
    severity           text          NOT NULL DEFAULT 'medium',
    confidence         numeric(5,4)  NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    status             text          NOT NULL DEFAULT 'new',
    dedup_key          text          NOT NULL,
    zone_ids           uuid[]        NOT NULL DEFAULT '{}',
    track_id           bigint,
    detection          jsonb         NOT NULL DEFAULT '{}',
    metadata           jsonb         NOT NULL DEFAULT '{}',
    reviewed_by        uuid,
    reviewed_at        timestamptz,
    review_note        text,
    created_at         timestamptz   NOT NULL DEFAULT now(),

    CONSTRAINT events_pkey PRIMARY KEY (id, occurred_at),
    CONSTRAINT events_status_chk CHECK (status IN
        ('new','acknowledged','confirmed','dismissed','false_positive')),
    CONSTRAINT events_severity_chk CHECK (severity IN
        ('info','low','medium','high','critical')),
    CONSTRAINT events_class_chk CHECK (event_class IN ('alert','telemetry')),
    -- HUMAN-IN-THE-LOOP: una alerta no sale de 'new' sin revisor humano identificado
    CONSTRAINT events_human_review_chk CHECK (
        event_class = 'telemetry'
        OR status = 'new'
        OR reviewed_by IS NOT NULL
    ),
    CONSTRAINT events_dedup_uq UNIQUE (dedup_key, occurred_at)
);

-- Sin dimensión de espacio por organization_id: TimescaleDB exigiría incluirla en
-- la PK y en events_dedup_uq (todo índice único debe contener todas las columnas de
-- particionado). El aislamiento por tenant ya lo dan RLS + índices (CONTRACTS v1.0.1).
SELECT create_hypertable('events', 'occurred_at', chunk_time_interval => INTERVAL '1 day');

CREATE INDEX events_org_time_idx  ON events (organization_id, occurred_at DESC);
CREATE INDEX events_camera_idx    ON events (camera_id, occurred_at DESC);
CREATE INDEX events_status_idx    ON events (organization_id, status, occurred_at DESC);
CREATE INDEX events_type_idx      ON events (organization_id, event_type, occurred_at DESC);
CREATE INDEX events_detection_gin ON events USING gin (detection jsonb_path_ops);

-- ───────────────────────────────────────────────────────────────────────────
-- Evidencias (CONTRACTS §6) — FK compuesta hacia la hypertable
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE evidences (
    id                 uuid          NOT NULL DEFAULT uuidv7(),
    organization_id    uuid          NOT NULL,
    event_id           uuid          NOT NULL,
    event_occurred_at  timestamptz   NOT NULL,
    kind               text          NOT NULL,
    storage_key        text          NOT NULL,
    content_type       text          NOT NULL,
    bytes              bigint        NOT NULL,
    duration_ms        integer,
    pre_roll_ms        integer,
    post_roll_ms       integer,
    sha256             text          NOT NULL,
    status             text          NOT NULL DEFAULT 'pending',
    created_at         timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT evidences_pkey PRIMARY KEY (id),
    -- (event_id, event_occurred_at) es una REFERENCIA LÓGICA a events(id, occurred_at):
    -- TimescaleDB no soporta FKs hacia hypertables. Integridad garantizada por
    -- event-service; limpieza alineada a la retención de events (CONTRACTS v1.0.1).
    CONSTRAINT evidences_kind_chk CHECK (kind IN ('image','clip')),
    CONSTRAINT evidences_status_chk CHECK (status IN ('pending','ready','failed','expired'))
);
CREATE INDEX evidences_event_idx ON evidences (event_id, event_occurred_at);
CREATE INDEX evidences_org_idx   ON evidences (organization_id, created_at DESC);

-- ───────────────────────────────────────────────────────────────────────────
-- Notificaciones, planes, auditoría
-- ───────────────────────────────────────────────────────────────────────────

CREATE TABLE notification_channels (
    id              uuid        NOT NULL DEFAULT uuidv7(),
    organization_id uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    kind            text        NOT NULL,  -- email|whatsapp|telegram|push|sms|webhook
    name            text        NOT NULL,
    config          jsonb       NOT NULL DEFAULT '{}',  -- destino/plantilla; secretos via vault ref
    enabled         boolean     NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT notification_channels_pkey PRIMARY KEY (id),
    CONSTRAINT nc_kind_chk CHECK (kind IN ('email','whatsapp','telegram','push','sms','webhook'))
);

CREATE TABLE notifications (
    id                 uuid        NOT NULL DEFAULT uuidv7(),
    organization_id    uuid        NOT NULL,
    channel_id         uuid        NOT NULL REFERENCES notification_channels(id),
    event_id           uuid,
    event_occurred_at  timestamptz,
    status             text        NOT NULL DEFAULT 'pending', -- pending|sent|failed
    payload            jsonb       NOT NULL DEFAULT '{}',
    sent_at            timestamptz,
    error              text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT notifications_pkey PRIMARY KEY (id),
    -- (event_id, event_occurred_at): referencia lógica a events (sin FK — hypertable).
    CONSTRAINT notifications_status_chk CHECK (status IN ('pending','sent','failed'))
);
CREATE INDEX notifications_org_idx ON notifications (organization_id, created_at DESC);

CREATE TABLE plans (
    id          uuid        NOT NULL DEFAULT uuidv7(),
    key         text        NOT NULL UNIQUE,     -- starter|business|enterprise|onprem
    name        text        NOT NULL,
    limits      jsonb       NOT NULL DEFAULT '{}', -- {maxCameras, maxModulesPerCamera, retentionDays,...}
    price_usd   numeric(10,2),
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT plans_pkey PRIMARY KEY (id)
);

CREATE TABLE subscriptions (
    id              uuid        NOT NULL DEFAULT uuidv7(),
    organization_id uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    plan_id         uuid        NOT NULL REFERENCES plans(id),
    status          text        NOT NULL DEFAULT 'active', -- trialing|active|past_due|canceled
    stripe_sub_id   text,
    current_period_end timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT subscriptions_pkey PRIMARY KEY (id),
    CONSTRAINT subscriptions_status_chk CHECK (status IN ('trialing','active','past_due','canceled'))
);

CREATE TABLE licenses (
    id              uuid        NOT NULL DEFAULT uuidv7(),
    organization_id uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    license_key     text        NOT NULL,        -- JWS firmado Ed25519 (on-prem/air-gapped)
    limits          jsonb       NOT NULL DEFAULT '{}',
    issued_at       timestamptz NOT NULL,
    expires_at      timestamptz NOT NULL,
    grace_days      integer     NOT NULL DEFAULT 30,
    revoked         boolean     NOT NULL DEFAULT false,
    CONSTRAINT licenses_pkey PRIMARY KEY (id)
);

CREATE TABLE audit_logs (
    id              uuid        NOT NULL DEFAULT uuidv7(),
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    organization_id uuid,
    actor_user_id   uuid,
    actor_kind      text        NOT NULL DEFAULT 'user',  -- user|service|system
    action          text        NOT NULL,                 -- ej. 'events:resolve'
    resource_type   text        NOT NULL,
    resource_id     text,
    request_id      text,
    detail          jsonb       NOT NULL DEFAULT '{}',
    CONSTRAINT audit_logs_pkey PRIMARY KEY (id, occurred_at)
);
SELECT create_hypertable('audit_logs', 'occurred_at', chunk_time_interval => INTERVAL '7 days');
CREATE INDEX audit_logs_org_idx ON audit_logs (organization_id, occurred_at DESC);

-- ───────────────────────────────────────────────────────────────────────────
-- Row-Level Security (defensa en profundidad, capa 3)
-- app.current_org la setea cada servicio por transacción: SET LOCAL app.current_org = '<uuid>'
-- ───────────────────────────────────────────────────────────────────────────

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'sites','zones','cameras','streams','camera_module_configs',
    'events','evidences','notification_channels','notifications',
    'subscriptions','licenses'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY %I_tenant_isolation ON %I USING (organization_id = current_setting(''app.current_org'', true)::uuid)',
      t, t);
  END LOOP;
END $$;

-- ai_modules: global (organization_id IS NULL) visible para todos; privados solo para su tenant.
ALTER TABLE ai_modules ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_modules FORCE ROW LEVEL SECURITY;
CREATE POLICY ai_modules_visibility ON ai_modules
    USING (organization_id IS NULL
           OR organization_id = current_setting('app.current_org', true)::uuid);

-- users: el propio tenant (o superadmin de plataforma via rol de conexión aparte).
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
CREATE POLICY users_tenant_isolation ON users
    USING (organization_id IS NULL
           OR organization_id = current_setting('app.current_org', true)::uuid);

-- Compresión y retención (TimescaleDB)
ALTER TABLE events SET (timescaledb.compress,
    timescaledb.compress_segmentby = 'organization_id',
    timescaledb.compress_orderby   = 'occurred_at DESC');
SELECT add_compression_policy('events', INTERVAL '7 days');
-- Retención por plan: gestionada por job de la app (drop_chunks selectivo), no política global.
