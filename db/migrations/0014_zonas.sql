-- ═══════════════════════════════════════════════════════════════════════════
-- Percepta — Migración 0014: las zonas del lugar dejan de estar en el código.
--
-- Hasta acá el plano eran ocho rectángulos escritos a mano en un archivo
-- TypeScript del frontend (apps/web/src/app/core/zonas.ts), con los nombres de
-- una oficina que no es la de nadie: "Oficina 1", "Sala de reuniones",
-- "Pasillo izquierdo". Cualquier cliente que instalara esto veía el plano de
-- otro, y para tener el suyo había que editar el código y compilar.
--
-- Ahora cada organización dibuja el suyo y le pone los nombres que usa.
--
-- ── Por qué las coordenadas son fracciones y no píxeles ──────────────────────
--
-- Se guardan como números entre 0 y 1 —fracción del ancho y del alto de la
-- imagen— y no como píxeles sobre un plano de 1536×1024, que es lo que había.
-- El motivo es concreto: la imagen del plano la sube el cliente y puede tener
-- cualquier proporción. Con coordenadas en píxeles de un lienzo fijo, un plano
-- apaisado de 2000×800 se dibujaba encajado con franjas arriba y abajo, y los
-- bloques quedaban corridos respecto de lo que se veía en la foto. Con
-- fracciones, un bloque dice "el 30% de izquierda a derecha" y eso sigue siendo
-- cierto se muestre en una pantalla de kiosco, en un celular o en un PDF.
--
-- ── Por qué `key` y no sólo el id ───────────────────────────────────────────
--
-- `persons.work_zone` guarda texto desde la migración 0010 y ya hay gente
-- asignada. Conservar una clave estable permite sembrar las ocho zonas que
-- estaban en el código con las MISMAS claves: quien tenía 'oficina-3' sigue
-- teniéndola, y ahora además puede renombrarla y moverla.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Por qué NO se llama `zones` ─────────────────────────────────────────────
--
-- Ya hay una tabla `zones` desde la migración 0001 y es otra cosa: son las
-- regiones que se dibujan SOBRE LA IMAGEN de una cámara (polígonos, líneas de
-- cruce, ROI) para que un módulo mire sólo ahí. Éstas son partes del EDIFICIO,
-- existan o no cámaras apuntándoles. Dos conceptos con el mismo nombre en la
-- misma base terminan siempre igual: alguien hace el JOIN equivocado.
CREATE TABLE IF NOT EXISTS floor_zones (
    id              uuid        NOT NULL DEFAULT uuidv7(),
    organization_id uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    -- Clave estable. Es lo que guardan las personas: renombrar la zona no
    -- desasigna a nadie.
    key             text        NOT NULL,
    name            text        NOT NULL,
    -- Qué es el bloque. Cambia cómo se dibuja y cómo se lee un registro:
    -- "pasó por el pasillo" y "estuvo en la oficina" no significan lo mismo.
    kind            text        NOT NULL DEFAULT 'oficina',
    -- Fracciones del plano, esquina superior izquierda + tamaño.
    x               double precision NOT NULL,
    y               double precision NOT NULL,
    w               double precision NOT NULL,
    h               double precision NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT floor_zones_pkey PRIMARY KEY (id),
    CONSTRAINT floor_zones_key_uq UNIQUE (organization_id, key),
    CONSTRAINT floor_zones_kind_chk CHECK (kind IN ('oficina', 'pasillo', 'otro')),
    -- Un bloque tiene que estar adentro del plano y tener superficie. Sin esto
    -- un arrastre con el mouse fuera del lienzo guardaba un bloque invisible
    -- que después nadie podía seleccionar para borrar.
    CONSTRAINT floor_zones_dentro_chk CHECK (
        x >= 0 AND y >= 0 AND w > 0 AND h > 0 AND x + w <= 1.0001 AND y + h <= 1.0001
    )
);

CREATE INDEX IF NOT EXISTS floor_zones_org_idx ON floor_zones (organization_id);

ALTER TABLE floor_zones ENABLE ROW LEVEL SECURITY;
ALTER TABLE floor_zones FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS floor_zones_tenant ON floor_zones;
CREATE POLICY floor_zones_tenant ON floor_zones
    USING (organization_id = current_setting('app.current_org')::uuid)
    WITH CHECK (organization_id = current_setting('app.current_org')::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON floor_zones TO percepta_app;

COMMENT ON TABLE floor_zones IS
  'Los bloques del plano de cada organización: oficinas, pasillos y demás. Las '
  'coordenadas son fracciones (0..1) del plano, no píxeles, para que sirvan con '
  'cualquier imagen de fondo y en cualquier tamaño de pantalla.';

-- ── El tamaño real de la imagen del plano ──────────────────────────────────
--
-- Sin esto no se puede dibujar encima sin deformar: había que suponer una
-- proporción fija (1536×1024) y toda imagen que no fuera 3:2 quedaba encajada
-- con franjas, corriendo los bloques respecto de lo que se ve.
ALTER TABLE floor_plans ADD COLUMN IF NOT EXISTS width  int;
ALTER TABLE floor_plans ADD COLUMN IF NOT EXISTS height int;

COMMENT ON COLUMN floor_plans.width IS
  'Ancho natural de la imagen en píxeles. Con el alto define la proporción con '
  'la que se dibuja el plano; las zonas son fracciones de esa caja.';

-- ── Dónde está parada cada cámara ──────────────────────────────────────────
--
-- Una cámara sin zona registra "pasó alguien"; con zona registra "pasó por
-- Recepción". Es la diferencia entre un registro que hay que interpretar
-- mirando un plano aparte y uno que se lee solo.
--
-- ON DELETE SET NULL y no CASCADE: borrar una zona del plano no puede llevarse
-- una cámara puesta. La cámara sigue existiendo, sólo deja de saber dónde está.
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS floor_zone_id uuid REFERENCES floor_zones(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS cameras_floor_zone_idx ON cameras (floor_zone_id) WHERE floor_zone_id IS NOT NULL;

COMMENT ON COLUMN cameras.floor_zone_id IS
  'En qué bloque del plano está esta cámara. Le da contexto a lo que ve: los '
  'pasos y las alertas pueden decir dónde ocurrieron sin que nadie recuerde a '
  'qué parte del lugar apunta cada cámara.';

-- ── Las ocho zonas que estaban en el código ────────────────────────────────
--
-- Se siembran con las mismas claves y las mismas posiciones (convertidas de
-- píxeles sobre 1536×1024 a fracciones) para toda organización que ya tenga
-- personas con zona asignada. Así nadie pierde lo que tenía y el editor abre
-- con algo dibujado en vez de un lienzo en blanco.
--
-- Sólo para las que ya venían usando esto: una organización nueva empieza con
-- el plano vacío y dibuja el suyo, que es el punto de todo el cambio.
INSERT INTO floor_zones (organization_id, key, name, kind, x, y, w, h)
SELECT o.id, z.key, z.name, z.kind,
       z.px / 1536.0, z.py / 1024.0, z.pw / 1536.0, z.ph / 1024.0
  FROM organizations o
 CROSS JOIN (VALUES
       ('oficina-1',      'Oficina 1',         'oficina',   75.0,   45.0,  390.0,  305.0),
       ('sala-reuniones', 'Sala de reuniones', 'oficina',  490.0,   45.0,  545.0,  300.0),
       ('oficina-2',      'Oficina 2',         'oficina', 1060.0,   45.0,  400.0,  300.0),
       ('pasillo-izq',    'Pasillo izquierdo', 'pasillo',   75.0,  360.0,  390.0,  175.0),
       ('recepcion',      'Recepción',         'otro',     570.0,  355.0,  385.0,  570.0),
       ('pasillo-der',    'Pasillo derecho',   'pasillo',  960.0,  360.0,   90.0,  565.0),
       ('oficina-3',      'Oficina 3',         'oficina',   75.0,  545.0,  390.0,  380.0),
       ('oficina-4',      'Oficina 4',         'oficina', 1060.0,  440.0,  400.0,  485.0)
     ) AS z(key, name, kind, px, py, pw, ph)
 WHERE EXISTS (
       SELECT 1 FROM persons p
        WHERE p.organization_id = o.id AND p.work_zone IS NOT NULL
     )
ON CONFLICT (organization_id, key) DO NOTHING;

-- Nadie puede quedar apuntando a una zona que no existe. Si esto falla, hay
-- una persona con una zona que el editor no va a poder mostrar ni corregir.
DO $$
DECLARE huerfanas int;
BEGIN
  SELECT count(*) INTO huerfanas
    FROM persons p
   WHERE p.work_zone IS NOT NULL
     AND NOT EXISTS (
         SELECT 1 FROM floor_zones z
          WHERE z.organization_id = p.organization_id AND z.key = p.work_zone
       );
  IF huerfanas > 0 THEN
    RAISE EXCEPTION 'Quedaron % personas con una zona que no existe en la tabla floor_zones', huerfanas;
  END IF;
END $$;
