> Parte de la documentación de arquitectura de **Percepta** — Plataforma SaaS de Análisis Inteligente de Video con IA modular. Ver [índice](README.md).

# Percepta — Visión General, Decisiones de Arquitectura y Guía de Lectura

## Resumen ejecutivo

**Percepta** es una plataforma SaaS multiempresa de análisis inteligente de video en tiempo real cuyo diseño gira alrededor de una tesis única y coherente en las nueve secciones: separar un **núcleo estable** de negocio de un **plano de IA extensible por plugins**, de modo que instalar una capacidad nueva sea publicar un `module.json` (manifest + JSON Schema + pesos), nunca tocar el core. Esa idea rectora se materializa de punta a punta: `module-registry` auto-descubre el módulo, el frontend Angular renderiza su formulario de configuración desde el JSON Schema sin código específico, `inference-orchestrator` lo programa según sus `resources`, `ai-worker` lo carga en un sandbox, y `rules-engine` convierte sus detecciones crudas en eventos aplicando la configuración por cámara. El resultado es una arquitectura donde el rubro del cliente no está cableado en ninguna parte: emerge de datos (`camera_module_configs.config` JSONB).

El segundo pilar transversal es el **human-in-the-loop como propiedad arquitectónica verificable**, no como política. Toda detección viaja con su `confidence` y termina en un `event` que nace en estado inicial y **exige** una transición humana (`reconocido → confirmado/descartado/falso-positivo`). Esta invariante se codifica en múltiples capas: un `CHECK` a nivel de base de datos que impide que un evento abandone el estado inicial sin un revisor identificado, la ausencia deliberada de actuadores (los módulos solo pueden `emit:detections`), y el hecho de que el feedback humano de falsos positivos sea a la vez la fuente de etiquetas de MLOps y un SLI de calidad de primer nivel. La percepción se automatiza; la decisión sobre personas es siempre humana.

El tercer eje es la **economía del sistema**, dominada por GPU-tiempo e I/O de evidencias. El diseño lo aborda con decisiones consistentes: un solo decode por cámara con fan-out zero-copy del mismo frame a N módulos, batching por modelo (no por cámara), modelos compartidos entre cámaras, muestreo adaptativo de FPS, y un modo **híbrido por defecto** donde la inferencia corre en el edge (junto a las cámaras) y solo metadatos y evidencias puntuales cruzan a la nube. Esto satisface simultáneamente coste de egress, latencia de alerta sub-segundo, tolerancia a cortes de WAN (store-and-forward) y privacidad por diseño (los píxeles de personas no abandonan la LAN).

Sobre esas bases, la plataforma despliega una malla de 15 microservicios con propiedad estricta de datos (un servicio = dueño de sus tablas), comunicación síncrona gRPC internа + REST solo en el borde, y un pipeline asíncrono de video desacoplado por RabbitMQ que transporta metadatos —nunca píxeles—. La multitenancy es estricta y defendida en profundidad: `organization_id` propagado por JWT, guards RBAC por servicio y Row-Level Security forzado en PostgreSQL como última barrera estructural. La persistencia combina PostgreSQL 15 con TimescaleDB (hypertables, continuous aggregates, compresión y retención por drop-chunk) para las series de eventos y métricas, MinIO/S3 para evidencias con tiering por retención, y Redis para caché, tracking y pub/sub de tiempo real.

La capa de negocio y operación completa el cuadro: un modelo SaaS híbrido (cuota base + metering de GPU/almacenamiento/eventos) con licenciamiento offline firmado (Ed25519/JWS) para on-premise air-gapped, un roadmap de ~18 meses en cinco fases (MVP demostrable al mes ~6), estimaciones de coste por escala, y un marco MLOps disciplinado (MLflow + DVC, promoción con gates, shadow deployment por defecto, detección de drift alimentada por la señal humana). Todo ello observado por OpenTelemetry con correlación de trazas a través de los saltos asíncronos, desplegado por GitOps (ArgoCD + Argo Rollouts) con una sola imagen OCI por servicio configurada por Helm values según entorno.

En conjunto, las secciones describen un sistema notablemente **cohesivo en sus principios y decisiones estructurales**. Las inconsistencias que existen son casi todas de **contrato de detalle** (nombres de columnas, enums, esquemas de manifest) fruto de que distintos autores materializaron el mismo concepto con firmas ligeramente divergentes; ninguna contradice la arquitectura, pero varias deben unificarse antes de codegen porque el propio diseño apuesta por contratos compartidos como fuente de verdad.

---

## Decisiones de arquitectura clave (ADRs)

1. **Núcleo estable + módulos de IA como plugins declarativos.** El `module.json` (manifest + JSON Schema de config + tipos de evento + recursos) es la única frontera. Justificación: instalar capacidades sin redeploy del core y renderizado dinámico del formulario; coste: validación estricta obligatoria en `module-registry` y disciplina de versionado.

2. **Multitenancy con shared-schema + `organization_id` + RLS forzado como línea base**, con schema-per-tenant (enterprise) y database-per-tenant (on-prem) como escalones superiores. Justificación: densidad y simplicidad operativa; aislamiento estructural imposible de evadir vía `FORCE ROW LEVEL SECURITY` + rol `NOBYPASSRLS`.

3. **Defensa en profundidad de autorización en tres capas** (JWT/scope en gateway → guard RBAC NestJS → RLS en Postgres), ninguna confía en la anterior. Justificación: un bug en una capa no produce fuga cross-tenant; coste: `set_config` por transacción.

4. **gRPC interno + REST/JSON solo en el borde.** El bus RabbitMQ no se expone al navegador. Justificación: contratos fuertes y HTTP/2 de baja latencia entre servicios; REST donde el consumidor es el SPA.

5. **Los frames de video NO pasan por RabbitMQ.** Transporte por gRPC streaming + shared-memory/CUDA IPC zero-copy; el bus transporta solo `detections.raw` (metadatos <2KB). Justificación: GB/s de píxeles matarían el broker.

6. **Un solo decode por cámara + fan-out del mismo frame a N módulos + batching por modelo.** Justificación: es la palanca económica central; N decodes o batch por cámara serían inviables a miles de cámaras.

7. **Inferencia en el edge (híbrido por defecto).** El plano media/IA vive junto a las cámaras; el plano de negocio en cloud. Justificación: egress, latencia, privacidad y tolerancia a particiones de red (store-and-forward vía shovel de RabbitMQ).

8. **Autoescalado por profundidad de cola y utilización de GPU (KEDA), no por CPU.** `ai-worker` con scale-to-zero para módulos ociosos. Justificación: la CPU es mala señal para carga I/O y GPU-bound; coste: cold-start (mitigado con `minReplicaCount:1` en módulos críticos).

9. **`media-service` es el único stateful, con sharding por consistent-hashing de `camera_id`.** El resto es cattle stateless. Justificación: ring-buffer y sesión RTSP son afines a la cámara; minimiza reconexiones al reescalar.

10. **TimescaleDB para eventos y métricas** (hypertables, continuous aggregates para KPIs/heatmaps, compresión columnar tras la ventana de revisión, retención por drop-chunk). Justificación: series de alto volumen con borrado O(1) y agregación incremental.

11. **Human-in-the-loop codificado en el esquema** (`CHECK` que impide salir del estado inicial sin revisor). Justificación: convierte un requisito ético en una invariante estructural imposible de saltar por un servicio automático.

12. **Detección cruda en el módulo, lógica de negocio en `rules-engine`.** El módulo devuelve confianza real sin umbralizar; horarios/zonas/umbrales/cooldown viven en config. Justificación: un mismo módulo sirve infinitas reglas sin recompilar; coste: más volumen en el bus (mitigado con `minConfidenceFloor`).

13. **Una sola imagen OCI por servicio, configurada por Helm values por entorno** (`build once, run anywhere`). Justificación: elimina deriva cloud/edge/on-prem; un módulo probado en cloud se comporta igual en el edge.

14. **Licenciamiento on-prem offline firmado (Ed25519/JWS) con periodo de gracia y binding soft.** Justificación: air-gapped sin dependencia de Stripe en vivo; nunca dejar a ciegas un sistema de seguridad por vencimiento administrativo.

15. **Shadow deployment de modelos por defecto sobre A/B en vivo.** El candidato procesa el mismo stream sin generar alertas. Justificación: cero riesgo para el operador; evaluación con tráfico real. Coste: duplica inferencia (acotado a un subconjunto).

16. **Metering idempotente por bus (`usage.metered`) + enforcement con degradación elegante.** Ninguna palanca comercial apaga silenciosamente módulos de seguridad de personas dentro del periodo de gracia.

---

## Inconsistencias detectadas

Son mayormente inconsistencias de **contrato de detalle** entre autores. Se listan por severidad decreciente.

### Bloqueantes (deben resolverse antes de codegen)

1. **La tabla `events` está definida tres veces con esquemas distintos.**
   - `modelo-de-datos`: columna de tiempo `occurred_at`, PK compuesta `(id, occurred_at)`, hypertable con `add_dimension` por `organization_id`.
   - `modulos-reglas-eventos`: columna `ts`, PK `(id, ts)`, hypertable por `ts`, y `UNIQUE (dedup_key, ts)`.
   - `saas-roadmap-costos-etica`: columna `occurred_at`, **PK simple `(id)`** (ni compuesta ni hypertable), con `CHECK human_review_required`.
   
   El nombre de la columna de particionado (`occurred_at` vs `ts`) y la forma de la PK son incompatibles entre sí. Además `apis-seguridad` y `operacion` usan `occurredAt`/`occurred_at`. **Hay que unificar una única definición canónica** (recomendado: `occurred_at`, PK `(id, occurred_at)`, hypertable, más el `CHECK` de revisión humana de la sección SaaS).

2. **La FK de `evidences → events` no cierra en una de las secciones.** `modelo-de-datos` define correctamente la FK compuesta `(event_id, event_occurred_at) → events(id, occurred_at)` (obligada por TimescaleDB). Pero `modulos-reglas-eventos` define `evidences.event_id UUID NOT NULL` **sin** la columna de tiempo, contra un `events` cuya PK es `(id, ts)`: esa FK es inválida. Debe adoptarse la forma compuesta en todas partes.

3. **Nombre y precisión del campo de confianza divergen.** El brief (P5) y `arquitectura-general` hablan de `confidence_score`. `modelo-de-datos` usa `confidence numeric(5,4)`; `saas` usa `confidence numeric(4,3)`; el resto usa `confidence`. Hay que fijar un único nombre (`confidence`) y una única precisión.

4. **Contrato del plugin Python con dos nombres distintos.** `modulos-reglas-eventos` define la ABC como `PerceptaModule` (`load/warmup/infer/health/release`). `dashboard-frontend-estructura` la llama `AIModule` en `packages/py-contracts` (`load_model/warmup/infer`). Como el plugin es el corazón de "instalar sin tocar el core", su contrato **no puede tener dos nombres ni dos firmas**.

5. **Serialización de `detections.raw`: Protobuf vs Avro.** `pipeline-video-ia` especifica protobuf sobre gRPC/AMQP (con contrato `.proto` como fuente de verdad). `modulos-reglas-eventos` dice "Avro/JSON en `detections.raw`" con Schema Registry. Son stacks de esquema distintos; hay que elegir uno.

### Importantes (contratos que causarán fricción)

6. **Enum de estados del evento: inglés en la DB vs español en UI/brief.** El brief y `dashboard-frontend-estructura` usan `nuevo → reconocido → confirmado/descartado/falso_positivo`. Todos los DDL (`modelo-de-datos`, `modulos-reglas`, `saas`) usan `new/acknowledged/confirmed/dismissed/false_positive`. Es defendible (DB en inglés, UI localizada) pero debe declararse explícitamente como decisión y documentarse el mapeo, porque hoy aparece como contradicción textual.

7. **Verbos/permisos del workflow de evento inconsistentes.** `apis-seguridad` expone `POST /events/{id}/acknowledge` y `/resolve` con permisos `events:acknowledge`/`events:resolve`. `modulos-reglas` y `dashboard` usan el permiso `events:review`; `dashboard` además usa `events:view`. Los códigos de permiso (`events:read` vs `events:view`; `acknowledge/resolve` vs `review`) deben unificarse en un único catálogo.

8. **Endpoint de señalización WebRTC (vista en vivo) definido de cuatro formas.**
   - `arquitectura-general`: `POST /api/v1/live/{camera_id}/offer`.
   - `apis-seguridad`: `POST /api/v1/cameras/{cameraId}/live-session`.
   - `pipeline-video-ia`: `GET /api/v1/cameras/{id}/live` + WHEP.
   - `dashboard`: WebRTC vía gateway (sin ruta fija).
   Debe fijarse una sola ruta y verbo.

9. **Multitenancy de `ai_modules` divergente.** `modelo-de-datos` y `modulos-reglas` lo tratan como catálogo **global** (sin `organization_id`). `catalogo-modulos` añade `organization_id UUID NULL` (global cuando es NULL; privado por tenant para marketplace on-prem) y una `UNIQUE` con `COALESCE`. Es una decisión de producto abierta (¿existe marketplace privado por tenant?) que cambia el esquema y la RLS; hay que zanjarla.

10. **Esquema del `module.json` no es único.** Cada sección lo materializa con claves distintas: `moduleKey/modelBackend/emits/configSchemaRef` (catalogo), `id/pluginApiVersion/model.backend/eventTypes/configSchemaRef` (modulos-reglas), `id/modelBackend/input.requiresRoi/emits` (dashboard), `id/model.registry/config_schema/emits` (operacion). Dado que el manifest es la frontera del sistema de plugins, requiere **un meta-schema canónico** (el propio `packages/contracts/module-manifest.schema.json` que dashboard menciona) y que todas las secciones se ajusten a él.

11. **`camera_module_configs` con columnas distintas por sección.** Coinciden en `organization_id/camera_id/ai_module_id/config/enabled` y en `UNIQUE (camera_id, ai_module_id)`, pero difieren en el resto: `config_schema_version` + `priority` (modelo-de-datos), `module_version` + `updated_by` (modulos-reglas), `schedule` + `priority` (catalogo). Debe consolidarse una única definición (probablemente la unión: `module_version` pin + `config_schema_version` + `priority`).

### Menores (aclaraciones de flujo)

12. **Productor de `notifications.dispatch` ambiguo.** `arquitectura-general` dibuja `notification-service → notifications.dispatch` (el servicio publicando hacia sí mismo), mientras `pipeline` y `dashboard` lo tratan como `event-service → notifications.dispatch → notification-service`. Aclarar quién publica.

13. **Disparo de `evidence-service`: ¿consume `events.created` o recibe solicitud de `event-service`?** `pipeline` y `catalogo` dicen que consume `events.created`; `arquitectura` muestra `event-service → solicita clip → evidence-service`. Ambas conviven pero conviene un único camino canónico.

14. **`ai_modules` con o sin `min_core_version`/`signature`/`status` valores distintos** entre `modulos-reglas` (`pending/available/deprecated/revoked`) y `catalogo` (`active/deprecated/disabled`). Unificar el enum de `status`.

15. **`totalApprox` vía `reltuples`** (apis-seguridad) devuelve un conteo a nivel de tabla que **no respeta el filtro RLS**; como estimación de UI es aceptable, pero conviene documentar que no es un conteo por-tenant (podría insinuar volúmenes de otros tenants).

---

## Huecos / faltantes

1. **Sincronización de configuración hacia el edge.** El modo híbrido corre `rules-engine` y config en el edge, pero ninguna sección especifica **cómo `camera_module_configs` y los cambios de config llegan al edge**, qué pasa con ediciones hechas offline, ni la reconciliación de conflictos al reconectar. Es un hueco operativo central del modelo híbrido.

2. **Distribución de artefactos de modelo a on-prem/air-gapped.** MLOps define MLflow + OCI + compilación TensorRT por familia de GPU, pero no el mecanismo de entrega de pesos/engines a un sitio sin internet (bundle firmado, mirror, verificación offline).

3. **Migración de `camera_module_configs` ante major bump de módulo.** Se menciona el "config migrator" (`vN→vN+1.jsonata`) y las asignaciones "que requieren revisión humana", pero falta el **owner del proceso, el estado de una config pendiente de migración, y qué hace `rules-engine`/`ai-worker` mientras tanto**.

4. **Provisioning end-to-end de un tenant nuevo.** Se cita `tenant.provisioned` y seed de `notification_channels`, pero no hay un flujo completo (crear org → subscription → RLS/seed roles → primer usuario admin → invitación) descrito como contrato.

5. **DSAR / derecho de supresión vs auditoría inmutable + WORM.** Varias secciones prometen borrado verificable de evidencias de personas, y a la vez auditoría hash-encadenada append-only en S3 Object Lock. Falta reconciliar cómo se ejerce el borrado sin romper la cadena de hashes ni el WORM (se apunta a "registrar la solicitud, no el contenido", pero sin diseño concreto).

6. **Números concretos de rate-limit y cuotas por plan** en el gateway. Se describe el mecanismo (token-bucket en Redis, `RateLimit-*`), pero no los límites por plan/endpoint que `billing-service` debe exponer a `QuotaGuard`.

7. **Egress de vista en vivo a escala.** Los costes modelan GPU y almacenamiento, pero N operadores viendo mosaicos WebRTC (especialmente en cloud-puro o vía TURN) es un coste de red y de sesiones SFU no dimensionado.

8. **Búsqueda/consulta de evidencias e investigación forense.** Hay layout S3 content-addressed y metadatos, pero no una capacidad de búsqueda transversal (por persona/objeto/rango/cámara) más allá de los filtros de `events`.

9. **Test de fuga cross-tenant explícito.** Se menciona el guardrail de CI que verifica RLS habilitada, pero falta una **suite e2e que intente activamente leer datos de otro `organization_id`** (el ataque, no solo la config).

10. **Gestión del ciclo de vida de `refresh`/sesiones en el edge/on-prem** y comportamiento de auth cuando el WAN cae (¿el operador local puede seguir revisando alertas si `identity-service` vive en cloud?). No está resuelto para el modo híbrido.

11. **Contrato de `analytics-service` alimentado por eventos `*.sample` de telemetría.** `catalogo` introduce `eventClass: "telemetry"` para que no entren al workflow, pero no hay esquema del canal ni de las hypertables de métricas de negocio que lo reciben (parcialmente cubierto por `metric_samples`).

---

## Riesgos y decisiones abiertas (priorizadas)

| # | Riesgo / decisión abierta | Impacto | Prioridad | Acción recomendada |
|---|---|---|---|---|
| R1 | Esquema de `events` triplicado e incompatible (columna de tiempo, PK, hypertable) | Bloquea persistencia, FKs de evidencias y codegen | **Crítica** | Congelar una definición canónica antes de F1; propagar a todas las secciones |
| R2 | Contrato de plugin (`PerceptaModule`/`AIModule`) y `module.json` sin meta-schema único | Rompe la promesa central "instalar sin tocar el core" | **Crítica** | Definir `packages/contracts/module-manifest.schema.json` + una sola ABC en `py-contracts` como fuente de verdad |
| R3 | Serialización de `detections.raw` (Protobuf vs Avro) sin decidir | Afecta hot-path, Schema Registry y codegen de tres lenguajes | **Alta** | Elegir Protobuf (ya hay `.proto` en pipeline) o Avro y unificar |
| R4 | Supuestos de densidad GPU (~15–20 cám/GPU) posiblemente optimistas | Desvía TCO y pricing; margen SaaS en riesgo | **Alta** | Benchmark de regresión por módulo/GPU en F1; recalibrar planes con datos reales |
| R5 | Shadow inference duplica coste de GPU | Presupuesto de inferencia en evaluación de modelos | **Media** | Acotar shadow a subconjunto representativo y ventanas; medir overhead |
| R6 | Scale-to-zero + cold-start en módulos de seguridad | Latencia de primera alerta tras inactividad | **Media** | `minReplicaCount:1` para módulos críticos; documentar la política por módulo |
| R7 | DSAR/borrado vs auditoría WORM hash-encadenada | Conflicto de cumplimiento (GDPR vs inmutabilidad) | **Alta** | Diseñar borrado tombstone + separar cadena de auditoría del contenido borrable |
| R8 | Sincronización de config edge↔cloud y auth offline en híbrido | Continuidad operativa ante corte de WAN | **Alta** | Definir agente de sync de config y estrategia de tokens/validación local en el edge |
| R9 | Multitenancy de `ai_modules` (global vs marketplace privado) | Cambia esquema, RLS y modelo de negocio (rev-share F4) | **Media** | Decisión de producto explícita; si hay marketplace privado, adoptar `organization_id NULL` |
| R10 | Enum de estados ES/EN y catálogo de permisos divergente | Deriva de contratos front/back, RBAC inconsistente | **Media** | DB en inglés canónico + i18n en UI; un único catálogo `permissions` versionado |
| R11 | Endpoint WebRTC definido de 4 formas | Integración frontend/media rota | **Media** | Fijar una ruta única de signaling (WHEP) en `contracts/openapi` |
| R12 | Multi-region activo-pasivo del plano de negocio sin prueba de failover | RTO/RPO no validados | **Media** | Ensayo de DR programado (ya previsto en caos/LitmusChaos) en F3 |
| R13 | Coste/latencia de egress de live-view a escala no modelado | Sorpresa de OpEx en cloud-puro | **Baja** | Modelar sesiones SFU/TURN y límites por plan |

---

## Cómo leer esta documentación

El documento maestro se compone de nueve secciones. El orden recomendado de lectura, según el rol y el objetivo, es:

**Ruta 1 — Comprensión global (todo lector, primero):**
1. Este **meta-análisis** (resumen ejecutivo + ADRs) para el mapa mental.
2. **`arquitectura-general`** — la columna vertebral: los 15 microservicios, planos, comunicación síncrona/asíncrona, edge/cloud/híbrido, HA y despliegue. Es el marco donde encajan las demás.

**Ruta 2 — El núcleo del producto (por qué Percepta es Percepta):**
3. **`modulos-reglas-eventos` (CORE)** — el contrato de plugins, el motor de reglas, el pipeline de eventos y el marco human-in-the-loop. Es la pieza central; leerla antes que el catálogo.
4. **`catalogo-modulos`** — el catálogo concreto de capacidades y su configuración; consumir después de entender el contrato del CORE.
5. **`pipeline-video-ia`** — cómo un frame RTSP se convierte en `detections.raw`: decode único, fan-out zero-copy, batching, GPU, tracking. Es el subsistema de mayor coste.

**Ruta 3 — Plataforma y contratos (backend/infra):**
6. **`modelo-de-datos`** — persistencia, multitenancy/RLS, TimescaleDB, config flexible por JSONB. **Nota:** contrastar la definición de `events` aquí con las de las secciones CORE y SaaS antes de implementar (ver Inconsistencia #1).
7. **`apis-seguridad`** — contrato REST, JWT/refresh/MFA, RBAC en tres capas, hardening, auditoría, privacidad. Referencia obligada para cualquier integración.

**Ruta 4 — Experiencia y negocio:**
8. **`dashboard-frontend-estructura`** — Angular, tiempo real, formularios dinámicos desde JSON Schema, notificaciones y organización del monorepo.
9. **`saas-roadmap-costos-etica`** — modelo de negocio, licenciamiento, metering, roadmap por fases, costes y marco ético. Buen cierre para stakeholders no técnicos.

**Ruta 5 — Operación (SRE/MLOps, transversal a todo):**
10. **`operacion-observabilidad-mlops`** — SLOs, observabilidad del pipeline asíncrono, MLOps, CI/CD, testing y confiabilidad. Se lee mejor al final porque referencia a todos los demás.

**Convenciones para leer cualquier sección:** DB en snake_case, API en camelCase, servicios en kebab-case, REST `/api/v1`, IDs UUID, timestamps UTC ISO-8601, multitenancy por `organization_id` + RLS. Ante cualquier discrepancia de contrato de detalle entre secciones, prevalece lo indicado en el apartado **Inconsistencias detectadas** de este meta-análisis hasta que se emita la definición canónica unificada.

---

[Índice](README.md) · [Siguiente ➡](01-arquitectura-general-y-microservicios.md)
