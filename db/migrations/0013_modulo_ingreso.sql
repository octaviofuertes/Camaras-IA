-- ═══════════════════════════════════════════════════════════════════════════
-- Percepta — Migración 0013: "Ingreso de personas" pasa a ser un módulo.
--
-- Hasta acá el reconocimiento de gente era una función suelta de la
-- plataforma: las pantallas de Reconocimiento, Accesos y Bienvenida estaban
-- disponibles para cualquier organización, tuviera o no una cámara mirando una
-- puerta. Eso está mal por dos motivos que no son de estilo:
--
--   1. Se le pedían datos biométricos a un cliente que quizás nunca los iba a
--      usar. Dar de alta la cara de un empleado no es configurar una función:
--      es empezar a tratar un dato sensible, y no corresponde ofrecerlo si no
--      hay una cámara que lo justifique.
--   2. El resto de la plataforma ya funciona así. Las caídas, el casco y la
--      detección de personas sólo existen donde se las asignó. Esta era la
--      excepción, y la excepción confundía: aparecían pantallas de control de
--      accesos en instalaciones que no controlaban ningún acceso.
--
-- No se crea una fila nueva: se renombra la que ya está. La clave es que
-- `camera_module_configs` apunta a `ai_modules.id`, no al `module_key`, así que
-- la cámara que ya tenía el módulo asignado lo conserva. Crear una fila nueva
-- habría dejado el módulo viejo huérfano en el catálogo y la asignación del
-- cliente apuntando a algo que ya no se usa.
--
-- La categoría también se corrige: estaba en 'hr' mientras el manifiesto decía
-- 'security'. Un control de accesos es seguridad, no recursos humanos, y la
-- diferencia se ve en la interfaz, que agrupa por categoría.
-- ═══════════════════════════════════════════════════════════════════════════

UPDATE ai_modules
   SET module_key  = 'person-entry',
       name        = 'Ingreso de personas',
       category    = 'security',
       description = 'Reconoce a las personas dadas de alta cuando entran, registra a qué '
                     'hora pasó cada una y avisa cuando entra alguien sin acceso. Incluye la '
                     'pantalla de bienvenida que saluda a quien llega y le muestra su zona en '
                     'el plano. Requiere consentimiento expreso registrado por persona.',
       -- El manifiesto y el schema se REEMPLAZAN enteros en vez de parchearse.
       -- Lo que había guardado era la foto del módulo de hace cinco commits:
       -- declaraba un solo tipo de evento (le faltaba access.denied, que es la
       -- alerta urgente) y tres parámetros de configuración de los diecisiete
       -- que tiene hoy. La pantalla de Cámaras arma el formulario de
       -- configuración con esta copia, así que un schema viejo son perillas
       -- que no aparecen.
       manifest      = '{"schemaVersion":"1.0.0","moduleKey":"person-entry","name":"Ingreso de personas","description":"Reconoce a las personas dadas de alta cuando entran, registra a qué hora pasó cada una y avisa cuando entra alguien sin acceso. Incluye la pantalla de bienvenida que saluda a quien llega y le muestra su zona en el plano. Requiere consentimiento expreso registrado por persona. A quien no está dado de alta no se le guarda ningún dato biométrico.","version":"1.0.0","pluginApiVersion":"1.0.0","category":"security","vendor":"percepta-core","model":{"backend":"onnx","artifactRef":"buffalo_l","classes":["face"]},"input":{"requiresZones":false,"minFps":1,"maxFps":5,"colorSpace":"bgr"},"configSchemaRef":"./config.schema.json","configSchemaVersion":"1.0.0","eventTypes":[{"type":"person.unknown","defaultSeverity":"low","eventClass":"alert"},{"type":"access.denied","defaultSeverity":"high","eventClass":"alert"}],"resources":{"gpu":false,"vramMb":0,"targetFps":2}}'::jsonb,
       config_schema = '{"$schema":"https://json-schema.org/draft/2020-12/schema","title":"Configuración de Ingreso de personas","description":"Reconoce a los empleados dados de alta con consentimiento registrado. De quien no está dado de alta no se guarda nada persistente.","type":"object","properties":{"matchThreshold":{"type":"number","title":"Parecido mínimo para afirmar una identidad","description":"Subirlo hace que se identifique menos pero que casi nunca se confunda a una persona con otra. Bajarlo identifica más y arriesga atribuirle a alguien el tiempo de otro, que es el error que nadie detecta.","minimum":0.2,"maximum":0.8,"default":0.42},"matchMargin":{"type":"number","title":"Ventaja mínima sobre el segundo candidato","description":"Si dos empleados se parecen, sin este margen se turnarían el nombre según el ruido de cada frame. Con él, ante la duda no se identifica a ninguno.","minimum":0,"maximum":0.4,"default":0.06},"minFaceSize":{"type":"number","title":"Tamaño mínimo de la cara","description":"Fracción del alto de la imagen. Más chica que esto no alcanza ni para identificar ni para preguntarle al operador.","minimum":0.01,"maximum":0.5,"default":0.05},"askCooldownMinutes":{"type":"number","title":"No volver a preguntar durante (min)","description":"Se cuenta desde la última vez que se vio a esa persona. Vive sólo en memoria del proceso: al reiniciar se olvida, porque de quien no está dado de alta no se guarda nada.","minimum":1,"maximum":240,"default":10},"repeatThreshold":{"type":"number","title":"Parecido para considerar que ya se preguntó","description":"Más bajo que el de identidad, a propósito: acá equivocarse cuesta al revés. Con el umbral de identidad, la misma persona girando la cabeza parecía alguien nuevo y se preguntaba una y otra vez.","minimum":0.1,"maximum":0.6,"default":0.25},"askMinFaceSize":{"type":"number","title":"Tamaño mínimo de la cara para preguntar","description":"Más exigente que el de identificar: este recorte se lo tiene que poder mirar una persona y decir quién es.","minimum":0.02,"maximum":0.5,"default":0.07},"askMinScore":{"type":"number","title":"Nitidez mínima de la cara para preguntar","minimum":0.1,"maximum":1,"default":0.65},"askMaxYaw":{"type":"number","title":"Giro lateral máximo de la cabeza para preguntar (grados)","description":"Más allá de esto se ve un perfil o una nuca. Preguntar por eso es una alerta que nadie puede contestar, y su vector sería una mala primera plantilla de esa persona.","minimum":10,"maximum":90,"default":30},"askMaxPitch":{"type":"number","title":"Cabeceo máximo para preguntar (grados)","description":"Excluye la coronilla de quien mira su escritorio. Ojo: una cámara alta le da a todo el mundo un cabeceo negativo constante, así que un valor chico no deja pasar a nadie.","minimum":10,"maximum":90,"default":45},"maxDesconocidos":{"type":"integer","title":"Cuántos desconocidos recordar a la vez","minimum":10,"maximum":2000,"default":200},"detSize":{"type":"integer","title":"Resolución de detección de rostros","enum":[320,480,640],"default":640},"classes":{"type":"array","items":{"type":"string"},"title":"Qué detecciones llegan a Eventos","description":"Las dos alertas del módulo: la pregunta por un desconocido y el aviso de que entró alguien sin acceso. Las identificaciones logradas y los pasos registrados no son alertas y no llegan a Eventos.","default":["person.unknown","access.denied"]},"eventType":{"type":"string","title":"Tipo de evento","default":"person.unknown"},"severity":{"type":"string","enum":["info","low","medium","high","critical"],"title":"Severidad","description":"Baja: es una pregunta administrativa, no una emergencia. No debe competir con una caída por la atención del operador.","default":"low"},"minConfidence":{"type":"number","title":"Calidad mínima de la cara para preguntar","description":"Por debajo de esto el recorte se ve mal y la pregunta es incontestable.","minimum":0.1,"maximum":1,"default":0.45},"minPersistenceFrames":{"type":"integer","title":"Frames seguidos antes de preguntar","description":"Uno: el módulo pregunta por cada cara desconocida UNA sola vez y después la recuerda para no repetir. Exigirle dos frames haría que no preguntara nunca.","minimum":1,"maximum":10,"default":1},"cooldownSeconds":{"type":"number","title":"Espera entre preguntas (s)","description":"Corto a propósito. Que no se repita la pregunta por la MISMA persona ya lo garantiza el módulo; este enfriamiento sólo evita duplicar el mismo instante, y si entran tres desconocidos seguidos las tres preguntas tienen que llegar.","minimum":1,"maximum":600,"default":3}},"additionalProperties":false}'::jsonb,
       config_schema_version = '1.0.0',
       updated_at  = now()
 WHERE module_key IN ('person-identification', 'person-entry');

-- Los eventos ya emitidos guardan la clave del módulo copiada (events.module_key),
-- no una referencia. Si no se actualizan, las alertas viejas de reconocimiento
-- quedan huérfanas en la pantalla de Eventos: sin ícono, sin color y sin nombre,
-- porque el frontend los resuelve buscando la clave en el catálogo.
UPDATE events SET module_key = 'person-entry' WHERE module_key = 'person-identification';

-- Que no queden las dos. Si alguna instalación ya tenía las dos filas por
-- haber corrido una versión intermedia, esto lo hace explotar acá y no en
-- producción con dos módulos que hacen lo mismo.
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM ai_modules WHERE module_key IN ('person-entry', 'person-identification');
  IF n <> 1 THEN
    RAISE EXCEPTION 'Se esperaba exactamente un módulo de ingreso de personas, hay %', n;
  END IF;
END $$;

COMMENT ON TABLE ai_modules IS
  'Catálogo de módulos de IA. organization_id NULL = módulo de plataforma, visible '
  'para todas. Una función de producto que dependa de un módulo se habilita cuando '
  'hay al menos una fila en camera_module_configs (enabled) que lo referencie: '
  'tener el módulo en el catálogo no alcanza, hay que asignarlo a una cámara.';
