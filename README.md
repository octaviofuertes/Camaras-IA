# Percepta

Plataforma SaaS de **análisis inteligente de video con IA modular**. Cualquier organización conecta cámaras IP y asigna **módulos de IA como plugins** por cámara. Núcleo estable; capacidades instalables sin tocar el core. **Human-in-the-loop**: toda detección es una alerta de asistencia con score de confianza que exige revisión humana.

> 📖 **Diseño y arquitectura**: ver [`docs/`](docs/README.md).
> 🧬 **Contratos canónicos** (fuente única de verdad): ver [`docs/CONTRACTS.md`](docs/CONTRACTS.md).

---

## Estructura del monorepo

```
Camaras+IA/
├─ docs/                    # Documentación de arquitectura (00–09 + CONTRACTS.md)
├─ packages/
│  ├─ contracts/            # Contratos TS + .proto + JSON Schemas (deriva de CONTRACTS.md)
│  └─ py-contracts/         # Contrato Python del plugin: clase base PerceptaModule
├─ apps/
│  ├─ api-gateway/          # BFF: REST + WS/SSE + auth de borde   (NestJS)
│  ├─ identity-service/     # Usuarios, roles, RBAC, JWT           (NestJS)
│  ├─ tenant-service/       # Organizations, sites, zones          (NestJS)
│  ├─ device-service/       # Cámaras y streams                    (NestJS)  ← MVP
│  ├─ event-service/        # Eventos + workflow de revisión       (NestJS)  ← MVP
│  ├─ rules-engine/         # Detecciones → eventos (data-driven)  (NestJS)  ← MVP
│  ├─ ai-worker/            # Ejecuta módulos de IA                 (Python)  ← MVP
│  └─ web/                  # Dashboard                             (Angular 15)
├─ modules/
│  └─ helmet-detection/     # Módulo de IA de ejemplo (plugin PerceptaModule)
├─ db/migrations/           # DDL canónico (0001_init.sql) + seed (0002_seed.sql)
└─ docker-compose.yml       # Infra de dev: Postgres+Timescale, Redis, RabbitMQ, MinIO
```

## Requisitos

- Node.js 18.14+ y [pnpm](https://pnpm.io) 9+ (`npm i -g pnpm@9`)
- Docker Desktop (para la infraestructura: Postgres/Timescale, Redis, RabbitMQ, MinIO)

  ```bash
  winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
  ```

  > Requiere virtualización (WSL2) y posiblemente un reinicio la primera vez.

- Python 3.11+ (para `ai-worker` y `py-contracts`)

  ```bash
  winget install -e --id Python.Python.3.11 --scope user --accept-package-agreements --accept-source-agreements
  ```

## Quickstart (desarrollo)

```bash
# 1. Configurar entorno
cp .env.example .env

# 2. Levantar infraestructura (Postgres+Timescale, Redis, RabbitMQ, MinIO)
pnpm infra:up

# 3. Aplicar el esquema canónico + datos semilla
pnpm db:migrate

# 4. Instalar dependencias del workspace y construir los contratos
pnpm install
pnpm --filter @percepta/contracts build

# 5. Levantar los servicios del MVP en paralelo
pnpm dev
```

### Entorno Python (`ai-worker`)

```bash
# Crear venv e instalar contratos + worker (py-contracts PRIMERO: pip resuelve
# la dependencia local 'percepta-contracts' desde lo ya instalado)
python -m venv .venv
.venv/Scripts/pip install -e packages/py-contracts
.venv/Scripts/pip install -e apps/ai-worker

# Arrancar el worker (descubre los plugins de ./modules y expone /health en :3010)
AI_MODULES_PATH=./modules .venv/Scripts/python -m ai_worker.main
```

### Verificación rápida

```bash
# 18 escenarios del slice MVP (evaluador de reglas + workflow human-in-the-loop)
pnpm smoke
```

## Probar el `event-service`

Es el primer servicio conectado a la base de verdad. Con la infra levantada y el esquema aplicado:

```bash
node apps/event-service/dist/main.js
```

Y en otra terminal, la demo del ciclo completo de una alerta:

```bash
pnpm demo
```

Recorre el workflow human-in-the-loop (nueva → reconocida → resuelta) y comprueba los
controles de seguridad: saltear la revisión humana da 422, un auditor sin permiso 403,
otro tenant 404 y sin token 401.

Para hacer peticiones a mano necesitás un token (aún no hay `identity-service`):

```bash
pnpm token operator
```

| Comando | Para qué |
|---|---|
| `pnpm demo` | Recorre el workflow completo y los controles de seguridad |
| `pnpm demo:seed-event` | Crea una alerta nueva de ejemplo |
| `pnpm token <rol>` | Emite un token de desarrollo (`operator`, `org_admin`, `auditor`, `site_admin`, `platform_superadmin`) |
| `pnpm db:reset` | Recrea la base desde cero y vuelve a migrar |

> ⚠️ `tools/dev-token.js` es **solo para desarrollo**: firma tokens con el secreto del `.env`.
> Los emitirá `identity-service` cuando exista.

Servicios de infra expuestos en dev:

| Servicio | URL |
|----------|-----|
| PostgreSQL | `localhost:5432` (db/usuario `percepta`) |
| Redis | `localhost:6379` |
| RabbitMQ (AMQP) | `localhost:5672` |
| RabbitMQ (consola) | http://localhost:15672 |
| MinIO (API S3) | http://localhost:9000 |
| MinIO (consola) | http://localhost:9001 |

## Estado

Proyecto **design-first** en arranque de **Fase 1 (MVP)**. El scaffold y los contratos ya están alineados con `docs/CONTRACTS.md`. Ver el roadmap en [`docs/08`](docs/08-saas-roadmap-costos-y-etica.md).
