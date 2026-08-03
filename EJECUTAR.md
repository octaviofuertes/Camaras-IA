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

## 2. Todo el stack, un comando

```bash
pnpm start
```

Libera los puertos que hayan quedado ocupados, levanta los seis servicios en orden
esperando a que cada uno responda, y deja el dashboard servido. `Ctrl+C` detiene todo.

| Servicio | Puerto | Qué hace |
|---|---|---|
| identity-service | 3001 | Login y emisión de tokens |
| device-service | 3003 | Cámaras, catálogo de módulos y asignaciones |
| event-service | 3004 | Eventos y workflow de revisión |
| ai-worker | 3010 | Inferencia YOLO por cámara |
| media-service | 3020 | Captura de video, snapshots y clips |
| dashboard | 4200 | Interfaz web |

## 3. Entrar al dashboard

Abrí **http://localhost:4200**. Listo: **la sesión se inicia sola**.

El dashboard se autentica contra `identity-service` con el usuario de desarrollo
`admin@percepta.local` (contraseña `percepta`). Es autenticación real —bcrypt contra la
tabla `users`, y los permisos del token salen de los roles que ese usuario tiene en la
base—, sólo que las credenciales están precargadas. Si el token vence, se renueva sola.

> Para producción: creá los usuarios reales, borrá el de desarrollo (migración `0004`)
> y reemplazá el auto-login de `auth.service.ts` por una pantalla de ingreso.

---

## Configurar cámaras

### Desde el dashboard (recomendado)

En **Cámaras → Agregar cámara**: nombre, tipo (USB o IP/WiFi), origen y fps. La cámara
se guarda en la base y `media-service` la detecta y empieza a capturar en menos de 10 s,
sin reiniciar nada. Para asignarle capacidades, arrastrá un módulo del catálogo sobre
ella: eso escribe `camera_module_configs` y el `ai-worker` lo levanta en su próximo ciclo.

### Las cámaras actuales

Hay **dos webcams configuradas como fuentes independientes**:

| `source` | Dispositivo | Nombre en el sistema |
|---|---|---|
| `0` | Webcam integrada de la notebook | Webcam Integrada |
| `1` | Logitech C925e (externa) | Logitech C925e |

`source` es el índice del dispositivo. Para saber cuál es cuál, mirá el snapshot:
`http://localhost:3020/cameras/<id>/snapshot.jpg`

> **Backend DirectShow (Windows).** El backend por defecto de OpenCV (MSMF) tarda
> ~54 s en abrir una webcam USB y **se cuelga si ya hay otra capturando**. Por eso
> `media-service` usa `CAP_DSHOW` en Windows: abre la misma cámara en 0,7 s y
> permite varias en paralelo. Sin esto, la segunda cámara nunca conecta.

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

Cargala desde **Agregar cámara** eligiendo *Cámara IP / WiFi* y pegando la URL.

---

## Ajustar la sensibilidad

Cada asignación cámara↔módulo tiene su propia configuración en `camera_module_configs.config`.
Las dos cámaras actuales están deliberadamente distintas: la integrada alerta con 1 persona;
la Logitech, sólo con 3 o más (regla de aglomeración).

Parámetros:

| Parámetro | Qué hace |
|---|---|
| `minConfidence` | Confianza mínima para considerar la detección (0–1) |
| `minPersistenceFrames` | Frames seguidos viendo el objeto antes de alertar (evita parpadeos) |
| `cooldownSeconds` | Tiempo mínimo entre alertas repetidas de la misma cámara |
| `minPersons` | Cuántas personas simultáneas disparan la alerta |
| `classes` | Qué detectar: `person`, `car`, `truck`, `backpack`… |

Después de cambiarlos, el `ai-worker` los toma al reiniciarse.

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

**Funciona de verdad:** alta y baja de cámaras desde el dashboard · asignación de módulos por drag & drop que persiste en la base · captura simultánea de varias cámaras USB/RTSP con reconexión · detección YOLO de personas y vehículos · reglas de confianza/persistencia/cooldown · eventos persistidos con aislamiento multi-tenant · workflow de revisión humana con auditoría · video en vivo con cajas de detección.

**Todavía no:** no hay pantalla de login (la sesión se inicia sola con el usuario de desarrollo), los clips se guardan en disco pero no suben a MinIO, no hay login (se usan tokens de desarrollo), y `helmet-detection` sigue siendo un stub — **detectar EPP necesita un modelo entrenado para eso**, que YOLO base no trae.
