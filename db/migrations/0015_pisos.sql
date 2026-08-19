-- ═══════════════════════════════════════════════════════════════════════════
-- Percepta — Migración 0015: el lugar puede tener varios pisos.
--
-- La 0014 asumía un lugar de una sola planta: una imagen por organización y
-- todos los bloques encima. Eso se rompe con el primer edificio real, que tiene
-- subsuelo, planta baja y pisos: no hay forma de dibujar dos plantas distintas
-- sobre un mismo lienzo sin mentir sobre dónde está cada cosa.
--
-- Ahora cada piso es una fila con su propia imagen, y cada bloque pertenece a
-- un piso. "Oficina 3" del primer piso y "Oficina 3" del subsuelo son dos
-- lugares distintos y el sistema los puede distinguir.
--
-- ── Los nombres los pone el cliente ─────────────────────────────────────────
--
-- No hay lista fija de niveles ni numeración. Un edificio tiene "Entrepiso",
-- otro tiene "Depósito externo", otro tiene "Planta 2 - Producción". Una lista
-- cerrada obliga a que alguien elija el que menos miente, y después el plano
-- dice una cosa y la gente dice otra.
--
-- ── Se reemplaza floor_plans ────────────────────────────────────────────────
--
-- `floor_plans` guardaba una imagen por organización. Ahora la imagen es del
-- piso, así que esa tabla no tiene nada que guardar: lo que tenía se convierte
-- en el primer piso y la tabla se va. Dejarla vacía al lado de `floors` sería
-- dejar dos lugares donde buscar el plano.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS floors (
    id              uuid        NOT NULL DEFAULT uuidv7(),
    organization_id uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            text        NOT NULL,
    -- En qué orden se listan. Se usa el número y no el nombre porque
    -- "Subsuelo" va antes que "Planta baja" y ningún orden alfabético lo sabe.
    orden           smallint    NOT NULL DEFAULT 0,
    -- El plano del piso, como data URL. NULL mientras no se subió: un piso
    -- puede existir antes de que alguien consiga su plano.
    image           text,
    -- Tamaño natural de la imagen. Con él se dibuja con su proporción real, y
    -- los bloques (que son fracciones) caen donde corresponde.
    width           int,
    height          int,
    updated_by      uuid        REFERENCES users(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT floors_pkey PRIMARY KEY (id),
    CONSTRAINT floors_name_uq UNIQUE (organization_id, name)
);

CREATE INDEX IF NOT EXISTS floors_org_idx ON floors (organization_id, orden);

ALTER TABLE floors ENABLE ROW LEVEL SECURITY;
ALTER TABLE floors FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS floors_tenant ON floors;
CREATE POLICY floors_tenant ON floors
    USING (organization_id = current_setting('app.current_org')::uuid)
    WITH CHECK (organization_id = current_setting('app.current_org')::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON floors TO percepta_app;

COMMENT ON TABLE floors IS
  'Los pisos del lugar, cada uno con el plano que subió el cliente. El nombre '
  'lo pone él: no hay lista fija de niveles porque cada edificio nombra los '
  'suyos distinto.';

-- ── Cada bloque pertenece a un piso ────────────────────────────────────────
ALTER TABLE floor_zones ADD COLUMN IF NOT EXISTS floor_id uuid REFERENCES floors(id) ON DELETE CASCADE;

-- Lo que ya existía era todo de una sola planta. Se le crea su piso y se le
-- cuelgan los bloques, así nadie pierde lo que tenía dibujado.
INSERT INTO floors (organization_id, name, orden, image, width, height)
SELECT o.id, 'Planta baja', 0, f.image, f.width, f.height
  FROM organizations o
  LEFT JOIN floor_plans f ON f.organization_id = o.id
 WHERE EXISTS (SELECT 1 FROM floor_zones z WHERE z.organization_id = o.id)
    OR f.organization_id IS NOT NULL
ON CONFLICT (organization_id, name) DO NOTHING;

UPDATE floor_zones z
   SET floor_id = f.id
  FROM floors f
 WHERE f.organization_id = z.organization_id
   AND z.floor_id IS NULL;

-- Un bloque sin piso no se puede ni dibujar ni encontrar. A partir de acá es
-- obligatorio: si algo lo dejara nulo, es un error del código, no un dato.
ALTER TABLE floor_zones ALTER COLUMN floor_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS floor_zones_floor_idx ON floor_zones (floor_id);

-- La clave del bloque era única por organización. Ahora lo es por piso: dos
-- plantas pueden tener cada una su "Oficina 3" sin pisarse.
--
-- OJO: `persons.work_zone` guarda sólo la clave. Mientras haya un solo piso da
-- igual, pero con dos plantas que repitan clave, la persona quedaría apuntando
-- a las dos. Por eso la unicidad se mantiene también por organización hasta que
-- la persona guarde el id del bloque en vez de su clave.
ALTER TABLE floor_zones DROP CONSTRAINT IF EXISTS floor_zones_key_uq;
ALTER TABLE floor_zones ADD CONSTRAINT floor_zones_key_uq UNIQUE (organization_id, key);

COMMENT ON COLUMN floor_zones.floor_id IS
  'En qué piso está este bloque. Un mismo nombre puede repetirse entre pisos; '
  'la clave no, porque es lo que guarda cada persona.';

-- ── Se va floor_plans ──────────────────────────────────────────────────────
DROP TABLE IF EXISTS floor_plans;

-- Nadie puede quedar colgado. Si esto falla, hay bloques sin piso o gente
-- apuntando a un bloque que no existe.
DO $$
DECLARE sueltos int;
BEGIN
  SELECT count(*) INTO sueltos FROM floor_zones WHERE floor_id IS NULL;
  IF sueltos > 0 THEN
    RAISE EXCEPTION 'Quedaron % bloques sin piso', sueltos;
  END IF;

  SELECT count(*) INTO sueltos
    FROM persons p
   WHERE p.work_zone IS NOT NULL
     AND NOT EXISTS (
         SELECT 1 FROM floor_zones z
          WHERE z.organization_id = p.organization_id AND z.key = p.work_zone
       );
  IF sueltos > 0 THEN
    RAISE EXCEPTION 'Quedaron % personas apuntando a un bloque que no existe', sueltos;
  END IF;
END $$;
