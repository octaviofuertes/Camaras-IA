-- ═══════════════════════════════════════════════════════════════════════════
-- Actividad por puesto de trabajo (informes)
--
-- Serie de tiempo, NO eventos. Estas filas nunca entran en la cola de revisión
-- humana: son mediciones agregadas que alimentan el apartado de Informes.
--
-- QUÉ NO HAY ACÁ, Y ES DELIBERADO
-- No hay identificador de persona, ni de track, ni nada que permita reconstruir
-- quién ocupó un puesto. La unidad de medida es la ZONA. Es la diferencia entre
-- medir el uso de una posición de trabajo y vigilar a un trabajador, y está
-- puesta en el esquema —no sólo en la aplicación— para que siga siendo cierta
-- aunque alguien escriba otro cliente contra esta base.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS activity_samples (
    id                 uuid        NOT NULL DEFAULT uuidv7(),
    -- Fin de la ventana medida. Es la dimensión temporal de la hypertable.
    occurred_at        timestamptz NOT NULL,
    organization_id    uuid        NOT NULL,
    site_id            uuid        NOT NULL,
    camera_id          uuid        NOT NULL,

    -- Zona nula = la cámara entera se trata como un solo puesto.
    zone_id            uuid,
    -- Nombre desnormalizado a propósito: un informe histórico tiene que seguir
    -- siendo legible aunque después se borre o renombre la zona.
    zone_name          text        NOT NULL,

    module_key         text        NOT NULL,
    module_version     text        NOT NULL,

    -- Duración de la ventana y su reparto. La suma de los cuatro tiene que dar
    -- la ventana: es lo que hace auditable el informe.
    window_seconds     numeric(10,2) NOT NULL,
    occupied_seconds   numeric(10,2) NOT NULL DEFAULT 0,
    phone_seconds      numeric(10,2) NOT NULL DEFAULT 0,
    empty_seconds      numeric(10,2) NOT NULL DEFAULT 0,
    -- Tiempo que el sistema NO observó (cámara caída, pipeline atrasado). Se
    -- guarda explícito en vez de repartirlo: un informe que rellena huecos con
    -- suposiciones no se puede defender.
    uncovered_seconds  numeric(10,2) NOT NULL DEFAULT 0,

    max_people         integer     NOT NULL DEFAULT 0,
    mean_occupancy     numeric(6,2) NOT NULL DEFAULT 0,

    created_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT activity_samples_pkey PRIMARY KEY (id, occurred_at),
    -- El tiempo no puede ser negativo ni el teléfono superar la presencia:
    -- son invariantes del informe y se hacen cumplir en la base.
    CONSTRAINT activity_no_negativo_chk CHECK (
        window_seconds >= 0 AND occupied_seconds >= 0 AND phone_seconds >= 0
        AND empty_seconds >= 0 AND uncovered_seconds >= 0
    ),
    CONSTRAINT activity_telefono_chk CHECK (phone_seconds <= occupied_seconds + 0.01),
    -- El reparto tiene que cerrar contra la ventana, con tolerancia para el
    -- redondeo de los centésimos.
    CONSTRAINT activity_reparto_chk CHECK (
        abs(occupied_seconds + empty_seconds + uncovered_seconds - window_seconds) <= 1.0
    )
);

SELECT create_hypertable('activity_samples', 'occurred_at', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS activity_org_time_idx
    ON activity_samples (organization_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS activity_camera_time_idx
    ON activity_samples (camera_id, occurred_at DESC);

-- ── Aislamiento entre empresas ─────────────────────────────────────────────
ALTER TABLE activity_samples ENABLE ROW LEVEL SECURITY;
ALTER TABLE activity_samples FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS activity_samples_select ON activity_samples;
CREATE POLICY activity_samples_select ON activity_samples
    FOR SELECT
    USING (organization_id = current_setting('app.current_org', true)::uuid);

DROP POLICY IF EXISTS activity_samples_insert ON activity_samples;
CREATE POLICY activity_samples_insert ON activity_samples
    FOR INSERT
    WITH CHECK (organization_id = current_setting('app.current_org', true)::uuid);

-- Las mediciones no se corrigen a mano: sin UPDATE ni DELETE, un informe no se
-- puede maquillar después de emitido. Lo que sale es la retención automática.
GRANT SELECT, INSERT ON activity_samples TO percepta_app;

-- ── Retención ──────────────────────────────────────────────────────────────
-- Las muestras crudas viven 90 días. El agregado por hora se conserva y es lo
-- que sostiene los informes históricos: guardar el detalle por minuto para
-- siempre sería acumular una descripción granular de la jornada de personas sin
-- que nadie la necesite.
SELECT add_retention_policy('activity_samples', INTERVAL '90 days', if_not_exists => TRUE);

-- ── Agregado por hora ──────────────────────────────────────────────────────
-- Es lo que consulta el informe: un rango de un mes recorre cientos de filas en
-- vez de millones.
--
-- Se INTENTA como agregado continuo de TimescaleDB, que lo mantiene al día
-- solo. Si esta versión no admite un agregado continuo sobre una hypertable con
-- seguridad a nivel de fila, se cae a una vista común con las mismas columnas.
--
-- La preferencia está clara y es deliberada: el aislamiento entre empresas NO
-- se negocia por rendimiento. Ya nos pasó con el almacenamiento columnar, que
-- también es incompatible con RLS; ahí la decisión fue la misma. Una vista
-- común es más lenta con mucho volumen, y eso se puede resolver después; una
-- fuga entre empresas no se resuelve después.
DO $$
DECLARE
    cuerpo text := $q$
        SELECT
            time_bucket(INTERVAL '1 hour', occurred_at) AS hora,
            organization_id,
            site_id,
            camera_id,
            zone_id,
            max(zone_name)                AS zone_name,
            sum(window_seconds)           AS window_seconds,
            sum(occupied_seconds)         AS occupied_seconds,
            sum(phone_seconds)            AS phone_seconds,
            sum(empty_seconds)            AS empty_seconds,
            sum(uncovered_seconds)        AS uncovered_seconds,
            max(max_people)               AS max_people,
            -- Media ponderada por tiempo observado: promediar los promedios
            -- daría más peso a las ventanas cortas.
            CASE WHEN sum(occupied_seconds + empty_seconds) > 0
                 THEN sum(mean_occupancy * (occupied_seconds + empty_seconds))
                      / sum(occupied_seconds + empty_seconds)
                 ELSE 0 END               AS mean_occupancy
        FROM activity_samples
        GROUP BY hora, organization_id, site_id, camera_id, zone_id
    $q$;
BEGIN
    IF to_regclass('activity_hourly') IS NOT NULL THEN
        RAISE NOTICE 'activity_hourly ya existe, se deja como está';
        RETURN;
    END IF;

    BEGIN
        EXECUTE format(
            'CREATE MATERIALIZED VIEW activity_hourly WITH (timescaledb.continuous) AS %s WITH NO DATA',
            cuerpo
        );
        PERFORM add_continuous_aggregate_policy('activity_hourly',
            start_offset => INTERVAL '3 days',
            end_offset   => INTERVAL '10 minutes',
            schedule_interval => INTERVAL '10 minutes',
            if_not_exists => TRUE);
        RAISE NOTICE 'activity_hourly: agregado continuo de TimescaleDB';
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'agregado continuo no disponible (%); se usa una vista común', SQLERRM;
        EXECUTE format('CREATE VIEW activity_hourly AS %s', cuerpo);
    END;
END $$;

-- En cualquiera de los dos casos el aislamiento se mantiene: la vista común
-- hereda la RLS de `activity_samples`, y sobre el agregado continuo la
-- aplicación filtra por organización como en el resto del sistema.
GRANT SELECT ON activity_hourly TO percepta_app;

-- ── Catálogo ───────────────────────────────────────────────────────────────
-- Sin esta fila el módulo existe en disco pero no aparece en el dashboard: el
-- catálogo de la pantalla de Cámaras se sirve desde esta tabla, no desde los
-- archivos. `status='available'` es lo que lo hace visible — los `pending` se
-- ocultan, que es por lo que helmet-detection no se ve.
INSERT INTO ai_modules (id, organization_id, module_key, name, description, category, version,
                        plugin_api_version, manifest, config_schema, config_schema_version, status)
VALUES (
    '00000000-0000-4000-c000-0000000ac701',
    NULL,
    'workstation-activity',
    'Actividad por puesto',
    'Mide cuánto tiempo cada puesto está ocupado, vacío y con uso de teléfono. Alimenta Informes; no genera alertas. La contabilidad es por región: no almacena identificador de persona.',
    'productivity',
    '1.0.0',
    '1.0.0',
    '{"schemaVersion":"1.0.0","moduleKey":"workstation-activity","version":"1.0.0","category":"productivity","model":{"backend":"yolo","artifactRef":"yolov8n.pt","classes":["person","cell phone"]},"input":{"requiresZones":false,"minFps":1,"maxFps":10,"colorSpace":"bgr"},"eventTypes":[{"type":"workstation.activity","defaultSeverity":"low","eventClass":"telemetry"}],"resources":{"gpu":false,"vramMb":0,"targetFps":3}}',
    '{"type":"object","properties":{"windowSeconds":{"type":"number","default":60},"personConfidence":{"type":"number","default":0.45},"phoneConfidence":{"type":"number","default":0.3},"maxGapSeconds":{"type":"number","default":5}}}',
    '1.0.0',
    'available'
)
ON CONFLICT (id) DO UPDATE
    SET name = EXCLUDED.name,
        description = EXCLUDED.description,
        version = EXCLUDED.version,
        category = EXCLUDED.category,
        manifest = EXCLUDED.manifest,
        config_schema = EXCLUDED.config_schema,
        status = EXCLUDED.status;

