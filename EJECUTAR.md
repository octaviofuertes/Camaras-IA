# Cómo levantar Percepta con una cámara real

Guía para correr el sistema completo: **cámara → IA → eventos → dashboard**.

## Requisitos (ya instalados en esta máquina)

- Docker Desktop abierto (no arranca solo con Windows)
- El venv del repo (`.venv`) con las dependencias de Python
- `pnpm install` ya ejecutado

---

## 1. Infraestructura

```bash
pnpm infra:up
```

Levanta PostgreSQL+TimescaleDB (puerto **5433** en el host), Redis, RabbitMQ y MinIO.

Si es la primera vez o querés empezar de cero:

```bash
pnpm db:reset
```

## 2. Los cuatro servicios

Cada uno en su propia terminal.

**a) event-service** (API de eventos, puerto 3004)

```bash
node apps/event-service/dist/main.js
```

**b) media-service** (captura de video, puerto 3020)

```bash
.venv/Scripts/python -m media_service.main
```

**c) ai-worker** (inferencia YOLO, puerto 3010) — necesita un token de servicio:

```bash
SERVICE_TOKEN=$(node tools/dev-token.js service) AI_MODULES_PATH=./modules PIPELINE_FPS=3 .venv/Scripts/python -m ai_worker.main
```

**d) dashboard** (puerto 4200)

```bash
cd apps/web && npx ng serve
```

## 3. Entrar al dashboard

Abrí **http://localhost:4200**. Para ver eventos reales necesitás un token de operador:

```bash
node tools/dev-token.js operator
```

Copialo y pegalo en la consola del navegador (F12):

```js
localStorage.setItem('px_token', 'PEGAR_TOKEN_ACA'); location.reload();
```

> El token dura 15 minutos. Cuando expire, generá otro. Esto desaparece cuando exista `identity-service` con login real.

---

## Configurar cámaras

### Webcam USB (la actual)

`cameras.json` en la raíz:

```json
{ "id": "00000000-0000-4000-b000-00000000ca01", "name": "Webcam Logitech", "source": 0, "fps": 12, "enabled": true }
```

`source` es el índice del dispositivo (`0` = primera cámara).

### Cámara IP / WiFi

**Cambia una sola línea** — el resto del sistema es idéntico:

```json
{ "id": "<uuid>", "name": "Depósito", "source": "rtsp://usuario:clave@192.168.1.50:554/stream1", "fps": 10, "enabled": true }
```

Para encontrar la URL RTSP de tu cámara, buscá el modelo + "rtsp url". Formatos típicos:

| Marca | URL habitual |
|---|---|
| Hikvision | `rtsp://user:pass@IP:554/Streaming/Channels/101` |
| Dahua | `rtsp://user:pass@IP:554/cam/realmonitor?channel=1&subtype=0` |
| TP-Link Tapo | `rtsp://user:pass@IP:554/stream1` |
| Reolink | `rtsp://user:pass@IP:554/h264Preview_01_main` |
| ONVIF genérica | `rtsp://user:pass@IP:554/onvif1` |

Toda cámara nueva necesita además su fila en la tabla `cameras` y su asignación de módulos
(hoy en `assignments.json`; cuando exista `device-service` se hará desde el dashboard).

---

## Ajustar la sensibilidad

En `assignments.json`, dentro de `config`:

| Parámetro | Qué hace |
|---|---|
| `minConfidence` | Confianza mínima para considerar la detección (0–1) |
| `minPersistenceFrames` | Frames seguidos viendo el objeto antes de alertar (evita parpadeos) |
| `cooldownSeconds` | Tiempo mínimo entre alertas repetidas de la misma cámara |
| `minPersons` | Cuántas personas simultáneas disparan la alerta |
| `classes` | Qué detectar: `person`, `car`, `truck`, `backpack`… |

Después de cambiarlo, reiniciá el `ai-worker`.

---

## Verificar que funciona

```bash
curl -s http://localhost:3020/cameras
```

```bash
curl -s http://localhost:3010/health
```

Ver el video crudo en el navegador: `http://localhost:3020/cameras/<id>/stream.mjpg`

Ver los eventos generados:

```bash
docker compose exec -T postgres psql "postgresql://percepta_app:percepta_app_dev_pw@localhost:5432/percepta" -c "SET app.current_org='00000000-0000-4000-b000-000000000001'; SELECT occurred_at, event_type, confidence, status FROM events ORDER BY occurred_at DESC LIMIT 10;"
```

---

## Estado real

**Funciona de verdad:** captura USB/RTSP con reconexión · detección YOLO de personas y vehículos · reglas de confianza/persistencia/cooldown · eventos persistidos con aislamiento multi-tenant · workflow de revisión humana con auditoría · video en vivo con cajas de detección.

**Todavía no:** el alta de cámaras desde el dashboard (falta `device-service`), el drag & drop no persiste en la base, los clips se guardan en disco pero no suben a MinIO, no hay login (se usan tokens de desarrollo), y `helmet-detection` sigue siendo un stub — **detectar EPP necesita un modelo entrenado para eso**, que YOLO base no trae.
