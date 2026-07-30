# Percepta — Plataforma SaaS de Análisis Inteligente de Video con IA Modular

> Documentación de arquitectura de nivel empresarial. **Percepta** es una plataforma SaaS multiempresa que permite a **cualquier organización** conectar cámaras IP y asignar a cada cámara una o varias **capacidades de IA** (módulos) de forma independiente y configurable. El núcleo es estable; las capacidades de IA se instalan como **plugins** (`module.json` + JSON Schema + pesos) sin tocar el código base. Toda detección es una **alerta de asistencia con score de confianza** que entra en un flujo de **revisión humana** — nunca una decisión automática sobre personas.

---

## 📐 Decisiones troncales (una mirada de 30 segundos)

| Eje | Decisión |
|-----|----------|
| **Extensibilidad** | Núcleo estable + **módulos de IA como plugins declarativos**. Instalar una capacidad = publicar un `module.json`; el frontend renderiza su formulario de config desde el JSON Schema, sin código. |
| **Multitenancy** | PostgreSQL shared-schema + `organization_id` + **Row-Level Security forzado**. Defensa en profundidad: JWT/scope → guard RBAC (NestJS) → RLS (Postgres). |
| **Human-in-the-loop** | Codificado en el esquema: un `CHECK` impide que un evento salga del estado inicial sin revisor humano. Los módulos solo pueden `emit:detections` — no hay actuadores. |
| **Economía / rendimiento** | Un solo decode por cámara + fan-out zero-copy del mismo frame a N módulos + batching por modelo. Los píxeles **no** pasan por el bus; RabbitMQ transporta solo metadatos. |
| **Topología** | **Híbrido por defecto**: inferencia en el *edge* (junto a las cámaras), plano de negocio en *cloud*. Una sola imagen OCI por servicio, configurada por Helm values según entorno. |

## 🧱 Stack tecnológico

| Capa | Tecnologías |
|------|-------------|
| **Frontend** | Angular 15 · TypeScript · Angular Material · RxJS · SCSS |
| **Backend** | Node.js 18 · NestJS (TypeScript) · microservicios |
| **IA / CV** | Python · FastAPI + gRPC · OpenCV · YOLO (Ultralytics) · PyTorch / TensorFlow · ONNX/TensorRT |
| **Datos** | PostgreSQL 15 + TimescaleDB (RLS) · Redis · MinIO / Amazon S3 |
| **Mensajería** | RabbitMQ (topic exchanges) |
| **Streaming** | RTSP · WebRTC (go2rtc / mediamtx) · FFmpeg |
| **Auth** | JWT + Refresh Tokens · RBAC · MFA |
| **Infra** | Docker · Docker Compose · Kubernetes · Helm · ArgoCD (GitOps) · OpenTelemetry |

---

## 📚 Documentos

Lee primero el documento **00** (visión global + decisiones), luego sigue la ruta según tu rol.

| # | Documento | Qué contiene |
|---|-----------|--------------|
| **★** | [**CONTRACTS.md — Contratos Canónicos**](CONTRACTS.md) | **Fuente única de verdad.** Resuelve las inconsistencias entre secciones y congela el *cómo exacto* (Protobuf de `detections.raw`, ABC `PerceptaModule`, meta-schema de `module.json`, DDL canónico de `events`/`evidences`/`ai_modules`, permisos RBAC, enum de estados, endpoint WHEP, topología de colas). **Ante cualquier conflicto de detalle, este archivo gana.** |
| **00** | [Visión General y Decisiones](00-vision-general-y-decisiones.md) | Resumen ejecutivo, 16 ADRs, **inconsistencias detectadas**, huecos, riesgos priorizados y guía de lectura. **Empieza aquí.** |
| **01** | [Arquitectura General y Microservicios](01-arquitectura-general-y-microservicios.md) | Diagramas C4, los 15 microservicios (responsabilidad/datos/API/eventos/escalado), síncrono vs asíncrono, edge/cloud/híbrido, autoscaling, balanceo, HA/DR, despliegue. |
| **02** | [Modelo de Datos y ER](02-modelo-de-datos.md) | Estrategia multitenant + RLS, diagrama ER completo, DDL de todas las tablas, TimescaleDB (hypertables, continuous aggregates, retención), config flexible por JSONB. |
| **03** | [APIs REST, Seguridad y Auditoría](03-apis-seguridad-y-auditoria.md) | Contrato REST `/api/v1` por recurso, ejemplos request/response, JWT+refresh+MFA, RBAC en 3 capas, hardening OWASP, vault de credenciales RTSP, auditoría, privacidad/GDPR. |
| **04** | [Pipeline de Video e IA](04-pipeline-de-video-e-ia.md) | Ingesta RTSP → FFmpeg → frames, WebRTC en vivo, ring-buffer para clips, orquestación GPU, decode único compartido por N módulos, batching, tracking, contrato `detections.raw`. |
| **05** | [Módulos de IA, Motor de Reglas y Eventos](05-modulos-ia-motor-de-reglas-y-eventos.md) | **La pieza central.** Contrato del plugin, manifest `module.json`, descubrimiento/registro automático, motor de reglas data-driven, ciclo de vida del evento, evidencias, human-in-the-loop. |
| **06** | [Catálogo de Módulos de IA](06-catalogo-de-modulos-ia.md) | Todas las capacidades por categoría (Seguridad, RR.HH., Productividad, Logística, Comercio, Industria) con técnica de CV, modelo sugerido, parámetros y tipo de evento; esquemas de config. |
| **07** | [Dashboard, Frontend y Estructura](07-dashboard-frontend-y-estructura.md) | Arquitectura Angular, tiempo real (WS/SSE), formularios dinámicos desde JSON Schema, mapa de pantallas, notificaciones multicanal, y estructura de carpetas del monorepo. |
| **08** | [SaaS, Roadmap, Costos y Ética](08-saas-roadmap-costos-y-etica.md) | Planes/suscripciones, metering, licencias on-prem firmadas, Stripe, roadmap por fases (~18 meses), costos de infra por escala y **marco ético / human-in-the-loop**. |
| **09** | [Operación, Observabilidad y MLOps](09-operacion-observabilidad-y-mlops.md) | SLOs, logging/métricas/tracing (OpenTelemetry), MLOps (MLflow+DVC, shadow, drift), CI/CD (GitOps), testing (contract/e2e/carga), gestión de config y secretos. |

---

## ⚠️ Estado del diseño

Esta documentación describe una arquitectura **cohesiva y completa** en sus principios y decisiones estructurales. El documento **00** listó las **inconsistencias de contrato de detalle** entre secciones (nombres de columnas, enums, firma del manifest) que surgieron porque distintos dominios materializaron el mismo concepto con firmas ligeramente divergentes. **Ninguna contradice la arquitectura**, y **ya están resueltas** en [**`CONTRACTS.md`**](CONTRACTS.md), que es la fuente única de verdad para implementación. El scaffolding del monorepo (`packages/contracts`, `packages/py-contracts`) deriva directamente de ese archivo.

## 🗺️ Rutas de lectura sugeridas

- **Comprensión global** → 00 → 01
- **Núcleo del producto** → 05 (CORE) → 06 → 04
- **Plataforma / backend** → 02 → 03
- **Experiencia y negocio** → 07 → 08
- **Operación (SRE/MLOps)** → 09 (transversal, al final)

---

*Convenciones globales: DB `snake_case` · API JSON `camelCase` · servicios `kebab-case` · REST `/api/v1` · IDs UUID · timestamps UTC ISO-8601 · multitenancy por `organization_id` + RLS.*
