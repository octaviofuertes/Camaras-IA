-- ═══════════════════════════════════════════════════════════════════════════
-- Percepta — Migración 0016: el módulo de elementos de protección personal.
--
-- Detecta casco, chaleco, antiparras y guantes, y avisa cuando a alguien le
-- falta uno que en esa cámara es obligatorio.
--
-- ── Qué pasa con "helmet-detection" ─────────────────────────────────────────
--
-- Ya había un módulo de casco en el catálogo. Nunca funcionó: su `module.py`
-- era un esqueleto con `self._model = object()` y un `infer()` que no devolvía
-- ninguna detección, y su manifiesto apuntaba a un modelo
-- (`models://ppe/helmet-yolov8m@1.2.0`) que no existe en ningún lado. Por eso
-- quedó en `pending` y nadie pudo asignarlo nunca.
--
-- No se borra, se marca `deprecated`. Borrarlo dejaría huérfanos los 502
-- eventos de demostración que guardan su clave: la pantalla de Eventos resuelve
-- ícono y nombre buscando `module_key` en el catálogo, así que esas filas
-- quedarían sin nombre y sin color. `deprecated` lo saca de la lista de módulos
-- asignables —device-service sólo ofrece los `available`— y conserva lo viejo
-- legible.
-- ═══════════════════════════════════════════════════════════════════════════

UPDATE ai_modules
   SET status = 'deprecated',
       description = 'Reemplazado por "Elementos de protección (EPP)", que además de casco '
                     'detecta chaleco, antiparras y guantes. Este nunca llegó a funcionar: '
                     'su implementación era un esqueleto sin modelo.',
       updated_at = now()
 WHERE module_key = 'helmet-detection';

-- ── El módulo nuevo ────────────────────────────────────────────────────────
--
-- organization_id NULL: es un módulo de plataforma, disponible para todas.
-- El id es fijo para que reaplicar la migración no cree un duplicado.
INSERT INTO ai_modules (
    id, organization_id, module_key, name, description, category, version,
    plugin_api_version, manifest, config_schema, config_schema_version, status
)
VALUES (
    '00000000-0000-4000-c000-0000000ac703',
    NULL,
    'ppe-detection',
    'Elementos de protección (EPP)',
    'Avisa cuando a una persona le falta un elemento de protección obligatorio en esa '
    'cámara: casco, chaleco, antiparras o guantes. Qué se exige se configura por cámara, '
    'porque depende del lugar. Es una ayuda para que alguien mire, no un veredicto.',
    'security',
    '1.0.0',
    '1.0.0',
    '{"schemaVersion":"1.0.0","moduleKey":"ppe-detection","name":"Elementos de protección (EPP)","version":"1.0.0","pluginApiVersion":"1.0.0","category":"security","vendor":"percepta-core","model":{"backend":"yolo","artifactRef":"training/models/epp.pt","classes":["Boots","Gloves","Goggles","Hardhat","NO-Gloves","NO-Goggles","NO-Hardhat","NO-Safety Vest","None","Person","Safety Vest"]},"input":{"requiresZones":false,"minFps":1,"maxFps":6,"colorSpace":"bgr"},"configSchemaRef":"./config.schema.json","configSchemaVersion":"1.0.0","eventTypes":[{"type":"ppe.helmet_missing","defaultSeverity":"high","eventClass":"alert"},{"type":"ppe.vest_missing","defaultSeverity":"medium","eventClass":"alert"},{"type":"ppe.goggles_missing","defaultSeverity":"medium","eventClass":"alert"},{"type":"ppe.gloves_missing","defaultSeverity":"low","eventClass":"alert"}],"resources":{"gpu":false,"vramMb":0,"targetFps":3}}',
    '{"$schema":"https://json-schema.org/draft/2020-12/schema","title":"Configuración de Elementos de protección (EPP)","type":"object","properties":{"exigidos":{"type":"array","items":{"type":"string","enum":["casco","chaleco","antiparras","guantes"]},"title":"Qué es obligatorio acá","description":"Se configura por cámara porque depende del lugar: en un obrador se exige casco y chaleco, en un laboratorio antiparras y guantes, y en una oficina nada.","default":["casco","chaleco"]},"minConfianza":{"type":"number","title":"Confianza mínima para creerle al modelo","description":"Bajarlo avisa más y empieza a marcar a gente que sí tiene el elemento puesto. Ese error es el caro: acusa a alguien de algo que no hizo.","minimum":0.1,"maximum":0.95,"default":0.45},"solapeMinimo":{"type":"number","title":"Cuánto del elemento tiene que caer sobre la persona","description":"Es lo que decide de quién es cada casco. Bajarlo mucho hace que con dos personas juntas el casco de una cuente para la otra.","minimum":0.1,"maximum":1.0,"default":0.55},"framesSeguidos":{"type":"integer","title":"Cuadros seguidos viendo la falta antes de avisar","description":"Avisar por un cuadro solo es avisar por un parpadeo.","minimum":1,"maximum":30,"default":4},"repetirSegundos":{"type":"number","title":"Espera antes de volver a avisar por la misma persona","description":"Quien está sin casco ahora sigue sin casco el minuto que viene.","minimum":5,"maximum":3600,"default":120},"pesos":{"type":"string","title":"Ruta del modelo entrenado","default":"training/models/epp.pt"},"classes":{"type":"array","items":{"type":"string"},"title":"Qué detecciones llegan a Eventos","default":["NO-Hardhat","NO-Safety Vest","NO-Goggles","NO-Gloves"]},"minConfidence":{"type":"number","title":"Calidad mínima para que la alerta llegue a Eventos","minimum":0.1,"maximum":1.0,"default":0.45},"minPersistenceFrames":{"type":"integer","title":"Frames seguidos que pide el pipeline","description":"Uno: la persistencia ya la aplicó el módulo, que sabe de qué persona se trata.","minimum":1,"maximum":10,"default":1},"cooldownSeconds":{"type":"number","title":"Espera entre alertas del mismo tipo (s)","description":"Corta a proposito: si tres personas entran sin casco las tres alertas tienen que llegar.","minimum":1,"maximum":600,"default":5}},"additionalProperties":false}',
    '1.0.0',
    'available'
)
ON CONFLICT (id) DO UPDATE
   SET module_key = EXCLUDED.module_key,
       name = EXCLUDED.name,
       description = EXCLUDED.description,
       category = EXCLUDED.category,
       manifest = EXCLUDED.manifest,
       config_schema = EXCLUDED.config_schema,
       status = EXCLUDED.status,
       updated_at = now();

-- Que no queden dos módulos de EPP asignables. Si esto falla, alguien puede
-- asignar el que no funciona y el sistema no va a avisar de nada.
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n
    FROM ai_modules
   WHERE status = 'available' AND module_key IN ('ppe-detection', 'helmet-detection');
  IF n <> 1 THEN
    RAISE EXCEPTION 'Se esperaba un solo módulo de EPP asignable, hay %', n;
  END IF;
END $$;
