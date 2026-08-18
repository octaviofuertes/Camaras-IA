-- ═══════════════════════════════════════════════════════════════════════════
-- Percepta — Migración 0010: la zona donde trabaja cada persona.
--
-- Hasta acá el acceso era del lugar entero: se podía estar o no se podía. Un
-- edificio con oficinas tiene una respuesta más precisa —Juan trabaja en la
-- Oficina 3— y esa es la que sirve para mostrarle a alguien, en un plano, dónde
-- le corresponde estar.
--
-- Es una etiqueta, no una geometría: la zona la define quien administra, no la
-- cámara. Cuando haya una cámara por oficina, esta columna es la que las une.
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE persons ADD COLUMN IF NOT EXISTS work_zone text;

COMMENT ON COLUMN persons.work_zone IS
  'Clave de la zona del plano donde trabaja esta persona (ej: oficina-3). '
  'NULL = no se le asignó ninguna, y el plano se muestra sin resaltar nada.';
