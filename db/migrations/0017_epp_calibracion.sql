-- ═══════════════════════════════════════════════════════════════════════════
-- Percepta — Migración 0017: el EPP vuelve a avisar.
--
-- El módulo estaba mirando y decidiendo bien, y no llegaba una sola alerta a
-- Eventos. Dos motivos, los dos en la configuración y no en el modelo.
--
-- ── 1. El filtro del pipeline tapaba al módulo ──────────────────────────────
--
-- La cámara tenía guardado `minConfidence: 0.45`, escrito el día que se asignó
-- el módulo. El pipeline descarta con ese piso lo que el módulo ya confirmó.
--
-- Pero el módulo no entrega confianza cruda: entrega faltas que YA pasaron un
-- umbral medido sobre este modelo, y ese umbral es distinto en cada elemento
-- —0,25 para el casco, 0,40 para los guantes—, porque la confianza no
-- significa lo mismo en todas las clases. Un piso fijo por encima de eso vuelve
-- a decidir con un número que nadie midió, y descarta alertas correctas:
-- medido sobre el split de prueba, ese 0,45 se comía 4 de cada 5 faltas de
-- antiparras, 1 de cada 3 de guantes y casi 1 de cada 5 de casco.
--
-- Se saca la clave en vez de ponerle otro número: así vale el valor por
-- omisión del `config.schema.json` del módulo, que es donde está explicado y
-- donde se va a corregir si mañana cambia. La misma razón por la que
-- `minPersistenceFrames` ya estaba en 1.
--
-- ── 2. El formulario mostraba un esquema viejo ─────────────────────────────
--
-- `ai_modules.config_schema` es la copia que lee el dashboard, y quedó como
-- estaba en la migración 0016: sin `verificarPosicion`, sin `umbralPorElemento`
-- ni `umbralCorroborado`, sin `sinAlertar` ni `segundaMirada`. El worker leía
-- el archivo del módulo y el dashboard esta copia, así que mostraban módulos
-- distintos. Se refrescan las dos copias desde los archivos.
-- ═══════════════════════════════════════════════════════════════════════════

UPDATE ai_modules
   SET manifest = '{"schemaVersion":"1.0.0","moduleKey":"ppe-detection","name":"Elementos de protección (EPP)","description":"Avisa cuando a una persona le falta un elemento de protección obligatorio en esa cámara: casco, chaleco, antiparras o guantes. Qué se exige se configura por cámara. Es una ayuda para que alguien mire, no un veredicto sobre nadie.","version":"1.0.0","pluginApiVersion":"1.0.0","category":"security","vendor":"percepta-core","model":{"backend":"yolo","artifactRef":"training/models/epp.pt","classes":["Boots","Gloves","Goggles","Hardhat","NO-Gloves","NO-Goggles","NO-Hardhat","NO-Safety Vest","None","Person","Safety Vest"]},"input":{"requiresZones":false,"minFps":1,"maxFps":6,"colorSpace":"bgr"},"configSchemaRef":"./config.schema.json","configSchemaVersion":"1.0.0","eventTypes":[{"type":"ppe.helmet_missing","defaultSeverity":"high","eventClass":"alert"},{"type":"ppe.vest_missing","defaultSeverity":"medium","eventClass":"alert"},{"type":"ppe.goggles_missing","defaultSeverity":"medium","eventClass":"alert"},{"type":"ppe.gloves_missing","defaultSeverity":"low","eventClass":"alert"}],"resources":{"gpu":false,"vramMb":0,"targetFps":3}}'::jsonb,
       config_schema = '{"$schema":"https://json-schema.org/draft/2020-12/schema","title":"Configuración de Elementos de protección (EPP)","description":"Qué elementos son obligatorios en el lugar que mira esta cámara, y con cuánta evidencia se avisa que faltan.","type":"object","properties":{"exigidos":{"type":"array","items":{"type":"string","enum":["casco","chaleco","antiparras","guantes"]},"title":"Qué es obligatorio acá","description":"Se configura por cámara porque depende del lugar: en un obrador se exige casco y chaleco, en un laboratorio antiparras y guantes, y en una oficina nada. Lo que no está en esta lista no se mira: no genera alertas ni consume tiempo de proceso.","default":["casco","chaleco","guantes"]},"minConfianza":{"type":"number","title":"Confianza mínima para creerle al modelo","description":"Subirlo hace que avise menos y casi nunca se equivoque; bajarlo avisa más y empieza a marcar a gente que sí tiene el elemento puesto. Ese error es el caro: acusa a alguien de algo que no hizo.","minimum":0.1,"maximum":0.95,"default":0.45},"solapeMinimo":{"type":"number","title":"Cuánto del elemento tiene que caer sobre la persona","description":"Es lo que decide de quién es cada casco. Bajarlo mucho hace que con dos personas juntas el casco de una cuente para la otra — y entonces una queda tapada y la otra acusada.","minimum":0.1,"maximum":1.0,"default":0.55},"framesSeguidos":{"type":"integer","title":"Cuadros seguidos viendo la falta antes de avisar","description":"Un detector se equivoca en cuadros sueltos. Avisar por uno solo es avisar por un parpadeo. Cuatro, a tres cuadros por segundo, es algo más de un segundo de evidencia sostenida.","minimum":1,"maximum":30,"default":4},"repetirSegundos":{"type":"number","title":"Espera antes de volver a avisar por la misma persona","description":"Quien está sin casco ahora sigue sin casco el minuto que viene. Avisarlo sesenta veces por minuto es la forma más rápida de que alguien apague el módulo.","minimum":5,"maximum":3600,"default":120},"pesos":{"type":"string","title":"Ruta del modelo entrenado","description":"Sale de `python training/ppe/entrenar.py`. Se puede apuntar a otro archivo para probar un modelo reentrenado sin tocar el que está en uso.","default":"training/models/epp.pt"},"classes":{"type":"array","items":{"type":"string"},"title":"Qué detecciones llegan a Eventos","description":"Las cuatro faltas que el módulo puede confirmar. Sacar una de la lista la silencia sin dejar de vigilarla.","default":["NO-Hardhat","NO-Safety Vest","NO-Goggles","NO-Gloves"]},"minConfidence":{"type":"number","title":"Filtro de confianza del pipeline","description":"Segundo filtro, aparte del del módulo y aplicado ya fuera de él. Va en el mínimo a propósito: el módulo NO entrega confianza cruda, entrega faltas que ya pasaron un umbral medido sobre este modelo, y ese umbral es distinto en cada elemento (0,25 para el casco, 0,40 para los guantes). Un piso fijo acá vuelve a decidir con un número que nadie midió, y descarta alertas correctas: medido sobre el split de prueba, el 0,45 que había se comía 4 de cada 5 faltas de antiparras, 1 de cada 3 de guantes y casi 1 de cada 5 de casco, todas ya confirmadas por el módulo. La persistencia, por la misma razón, ya estaba en 1.","minimum":0.1,"maximum":1.0,"default":0.1},"minPersistenceFrames":{"type":"integer","title":"Frames seguidos que pide el pipeline","description":"Uno: la persistencia ya la aplicó el módulo con `framesSeguidos`, que sabe de qué persona se trata. Pedirla dos veces sólo retrasa el aviso.","minimum":1,"maximum":10,"default":1},"cooldownSeconds":{"type":"number","title":"Espera entre alertas del mismo tipo (s)","description":"Corta a propósito: que no se repita por la MISMA persona ya lo garantiza el módulo. Esto sólo evita duplicar el mismo instante, y si tres personas entran sin casco las tres alertas tienen que llegar.","minimum":1,"maximum":600,"default":5},"minConfianzaFalta":{"type":"number","title":"Confianza mínima para afirmar que FALTA","description":"Los dos errores no cuestan lo mismo: pasar por alto un casco puesto no le hace nada a nadie, y decir que alguien no lo tiene cuando sí lo tiene es acusarlo delante de su jefe. Pero el número NO se elige a ojo, y tampoco se usa: los umbrales con los que el módulo decide salen de medir el veredicto por persona con `python training/ppe/evaluar_personas.py --calibrar`, y sin esa medición el módulo no alerta. Esto es sólo el valor de referencia.","minimum":0.1,"maximum":0.95,"default":0.45},"verificarPosicion":{"type":"boolean","title":"Exigir que el elemento esté donde va en el cuerpo","description":"Un casco tiene que estar en la cabeza y unos guantes en las manos. Si el modelo pone un \"sin casco\" a la altura de los pies, se equivocó, y sin esto esa equivocación se convierte en una alerta contra alguien. Apagalo sólo si la cámara tiene un encuadre tan raro que el filtro estorba.","default":true},"umbralPorElemento":{"type":"object","additionalProperties":{"type":"number"},"title":"Umbral propio de cada elemento","description":"El modelo está seguro de un chaleco y mucho menos de unas antiparras. Un solo número para los cuatro obliga a elegir entre no avisar de lo que ve bien, o avisar de más con lo que ve mal. Normalmente NO se toca: lo mide `training/ppe/evaluar_personas.py --calibrar` y viaja con el modelo, no con la cámara. Ponerlo acá lo pisa, y es para el caso raro de un encuadre propio.","default":{}},"umbralCorroborado":{"type":"object","additionalProperties":{"type":"number"},"title":"Umbral cuando nada contradice a la ausencia","description":"Umbral más bajo, que se aplica sólo cuando el detector no le vio ese elemento a esa persona por ningún lado. Una caja floja de \"sin casco\" sobre alguien a quien TAMBIÉN se le encontró un casco es casi siempre un error; la misma caja sobre alguien a quien no se le encontró ninguno es otra cosa, porque el modelo acierta el elemento puesto mucho mejor que su ausencia. Sigue haciendo falta que el modelo vea la ausencia: sin caja no se avisa nada. Lo mide `training/ppe/evaluar_personas.py --calibrar`.","default":{}},"sinAlertar":{"type":"array","items":{"type":"string","enum":["casco","chaleco","antiparras","guantes"]},"title":"Vigilar pero no alertar","description":"Lo que está acá se sigue detectando y dibujando en la cámara, pero no manda alertas a Eventos. Es para el elemento cuyo modelo todavía no distingue la ausencia con precisión suficiente: mejor mostrarlo sin acusar a nadie que llenar Eventos de alertas falsas.","default":[]},"segundaMirada":{"type":"boolean","title":"Mirar de cerca a cada persona, por turnos","description":"En una escena real la gente está a distintas distancias: el de adelante ocupa media pantalla y el del fondo cincuenta píxeles. Mirando sólo el cuadro entero, el modelo resuelve al de adelante y se pierde a los de atrás — se ve como que detecta a uno solo de tres. Recortando a cada persona y ampliándola, todos reciben la misma atención. Cuesta alrededor de un 77% más de CPU por cuadro; apagalo si la máquina no llega.","default":true}},"additionalProperties":false}'::jsonb,
       config_schema_version = '1.0.0',
       updated_at = now()
 WHERE module_key = 'ppe-detection';

-- El piso de confianza guardado en cada cámara. Se borra la clave: el valor
-- pasa a salir del esquema del módulo.
UPDATE camera_module_configs cmc
   SET config = cmc.config - 'minConfidence',
       updated_at = now()
  FROM ai_modules m
 WHERE m.id = cmc.ai_module_id
   AND m.module_key = 'ppe-detection'
   AND cmc.config ? 'minConfidence';

-- Que no quede ninguna cámara de EPP con un piso propio por encima del umbral
-- más bajo que el módulo puede llegar a usar. Si esto falla, el módulo va a
-- confirmar faltas que nunca llegan a Eventos, que es justo lo que se arregla.
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n
    FROM camera_module_configs cmc
    JOIN ai_modules m ON m.id = cmc.ai_module_id
   WHERE m.module_key = 'ppe-detection'
     AND (cmc.config ->> 'minConfidence')::numeric > 0.25;
  IF n > 0 THEN
    RAISE EXCEPTION 'Quedan % cámaras de EPP con minConfidence por encima del umbral del módulo', n;
  END IF;
END $$;
