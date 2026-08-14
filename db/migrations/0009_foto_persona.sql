-- ═══════════════════════════════════════════════════════════════════════════
-- Percepta — Migración 0009: la foto de cada persona registrada.
--
-- Hasta acá se guardaba la PLANTILLA facial —un vector de 512 números— pero
-- ninguna imagen. Alcanza para reconocer, no para que un administrador mire la
-- lista y sepa de quién está hablando cada fila: un control de accesos que
-- muestra sólo nombres obliga a confiar en que la carga fue correcta, y la
-- carga la hace una persona apurada mirando recortes parecidos entre sí.
--
-- Es una miniatura chica, la misma que ya viajaba en la alerta. No agrega un
-- dato nuevo sobre nadie: agrega poder verificar lo que ya se guardó.
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE persons ADD COLUMN IF NOT EXISTS photo text;

COMMENT ON COLUMN persons.photo IS
  'Miniatura JPEG en base64 para identificar la ficha en la interfaz. Se borra '
  'con la persona, igual que sus plantillas.';
