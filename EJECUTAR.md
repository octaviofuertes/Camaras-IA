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

### Un módulo asignado es lo que enciende una función

Asignar no es sólo "esta cámara además hace esto": para algunos módulos es lo que hace
existir la función en toda la aplicación.

**Ingreso de personas** es el primero que funciona así. Sin ninguna cámara que lo tenga
asignado:

- las secciones **Reconocimiento** y **Accesos** no aparecen en el menú, y entrar por
  la URL a mano rebota a Cámaras con el motivo escrito;
- la **pantalla de bienvenida** no se puede abrir: el botón "Panel de cámara" del login
  contesta que falta asignar el módulo, y ni siquiera se emite la sesión del kiosco;
- los endpoints de `/api/v1/persons` contestan **409** con el mismo motivo.

Se apaga y se prende arrastrando el módulo, sin reiniciar nada: el menú reacciona al
instante y el backend en menos de diez segundos.

La única excepción es **borrar una persona**, que sigue funcionando con el módulo
desasignado. Es a propósito: si no, quitar el módulo dejaría encerrados los datos
biométricos ya cargados, sin pantalla ni endpoint para eliminarlos.

### Elementos de protección (EPP)

Detecta **casco, chaleco, antiparras y guantes** y avisa cuando a alguien le falta uno que
en esa cámara es obligatorio. Qué se exige se configura al asignar el módulo, porque depende
del lugar: en un obrador casco y chaleco, en un laboratorio antiparras y guantes.

El modelo no viene con el repositorio: se entrena acá, con un dataset público
([CC BY 4.0](https://universe.roboflow.com/himanshu-bharati/ppe_dectection-dtt4q)).

```bash
python training/ppe/descargar.py && python training/ppe/verificar.py && python training/ppe/entrenar.py
python training/ppe/evaluar.py     # que tan bien detecta, clase por clase
python training/ppe/umbral.py      # con cuanta confianza conviene alertar
```

`verificar.py` no es opcional. El primer dataset que probé tenía las clases corridas y no
había forma de notarlo leyendo su documentación: entrenaba perfecto y aprendía lo que no era.
La verificación mide dónde cae cada clase —un casco arriba y chico, un chaleco en el medio y
grande, las botas abajo— y se planta si algo no cae donde va.

En CPU el entrenamiento tarda unas 5 horas. Deja `training/models/epp.pt` y las métricas por
clase en `training/models/epp.json`. Ninguno de los dos va al repositorio: son artefactos, se
regeneran con el comando de arriba.

**Antes de confiar en él, medilo.**  da el mAP por clase sobre las 254 imágenes
que el modelo nunca vio, y traduce el número: *"de cada 10 alertas, ~7 serían correctas"*.
 elige con cuánta confianza conviene alertar cada elemento — y se niega a dar un
número para los que todavía no llegan, en vez de inventar uno. Lo que no llega se pone en
: se sigue viendo en la cámara, pero no manda alertas.

**Lo que el módulo NO hace:** avisar porque no vio el elemento. Sólo avisa cuando el modelo
vio la ausencia —el dataset tiene las cabezas sin casco anotadas a mano—. La diferencia es lo
que separa un módulo que se usa de uno que se apaga a la semana por avisar de más.

### Ver una cámara en grande

En **Dashboard**, el botón de las flechas en cada cámara la abre a pantalla completa, con el
video en vivo (MJPEG, sin recortar).

Ahí cada persona que ve la cámara queda marcada con el contorno de su cuerpo. Tocando a una:

- aparece su ficha con **nombre**, **hace cuánto está en el lugar** y **si tiene acceso**;
- se la cubre con una capa **verde** si puede estar ahí y **roja** si no.

La capa aparece sólo al seleccionar. Con todos pintados todo el tiempo el video deja de verse
y el rojo deja de significar algo.

A quien el sistema no reconoció no se lo pinta ni de verde ni de rojo, sino de gris: no se sabe
si puede estar ahí, y un color afirmaría algo que no se sabe.

El contorno sale del modelo de segmentación (`yolov8n-seg.pt`), que cuesta alrededor del doble
de CPU por frame que el de detección. Se puede volver al anterior poniendo `personWeights:
"yolov8n.pt"` en la configuración del módulo: ahí la marca pasa a ser un recuadro.

### El plano del lugar

En **Accesos → Plano y zonas**. El plano no se dibuja: se sube. Cada piso tiene el suyo
—el render, el plano del arquitecto o una foto del plano impreso— y encima se marca con un
rectángulo dónde queda cada área y cómo se llama.

Si el lugar tiene subsuelo o varias plantas, cada una va por separado con **+ Piso** y su
propia imagen. Los nombres los ponés vos: "Subsuelo", "Entrepiso", "Planta 2 - Producción".

Con los bloques dibujados se hacen dos cosas:

- **A cada persona** se le asigna en qué bloque trabaja, desde Reconocimiento. Es lo que la
  pantalla de bienvenida ilumina en verde cuando la reconoce.
- **A cada cámara** se le dice en qué bloque está parada, en el mismo editor. Eso le da
  contexto a lo que ve: el registro puede decir dónde pasó algo sin que nadie recuerde a
  qué parte del lugar apunta cada cámara.

Las marcas se guardan como fracciones del plano, no como píxeles, así que la imagen puede
tener cualquier proporción y las áreas siguen cayendo sobre la misma habitación.

Borrar un área —o un piso entero— donde hay gente asignada se rechaza, con el nombre y
cuántas personas son.
Es a propósito: vaciarle la zona a alguien en silencio no se nota hasta semanas después,
cuando la pantalla de bienvenida deja de decirle dónde le toca.

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

## Probar la detección de caídas

El módulo está asignado a la Logitech. Para verlo razonar en vivo:

```bash
node tools/probar-caidas.js
```

Muestra en qué estado ve a cada persona. Probá esta secuencia:

| Qué hacés | Qué debería pasar |
|---|---|
| Parado frente a la cámara | `de pie` |
| Te agachás y te levantás rápido | pasa por `CAYENDO` pero **no alerta** |
| Te acostás y te quedás quieto 3 s | `EN EL SUELO` → **CAÍDA CONFIRMADA** |

El tercer caso genera un evento real en la base, visible en **Eventos** del dashboard.

> Ajustá la sensibilidad desde la configuración del módulo: `confirmSeconds` es el
> parámetro clave. Más alto = menos falsas alarmas pero alerta más tarde.

## Detalles que ya nos mordieron

- **El orden al configurar la cámara USB cambia el rendimiento 3x.** Hay que fijar
  primero la RESOLUCIÓN y después el formato MJPG; al revés DirectShow ignora el
  pedido y entrega YUY2 sin comprimir, que a 720p satura el bus USB y clava la
  cámara en 10 fps. Y **nunca** setear `CAP_PROP_FPS` después de MJPG: renegocia el
  formato y vuelve a YUY2. Medido: `res→MJPG` = 29,6 fps · `res→MJPG→FPS` = 10,0 fps.
- **`ng serve` escucha en IPv6** (`[::1]:4200`), no en `0.0.0.0`. Si buscás su proceso con
  `netstat -p tcp` no aparece: ese filtro sólo lista IPv4. Por eso `pnpm start` usa
  `netstat -ano` sin filtro para liberar el puerto.
- **`proxy.conf.json` no se recarga en caliente.** Si tocás las rutas del proxy del
  dashboard, hay que reiniciar `ng serve` (o `pnpm start`); si no, seguís con la config
  vieja y las llamadas nuevas dan 404.
- **`localhost` vs `127.0.0.1`.** Node resuelve `localhost` a IPv6 (`::1`) y tanto Docker
  como los servicios Python publican en IPv4: usar `localhost` da `ECONNREFUSED`. Por eso
  todas las cadenas de conexión y el proxy apuntan a `127.0.0.1`.
- **Puerto 5432 ocupado.** Hay un PostgreSQL nativo instalado en Windows; el contenedor
  del proyecto usa el **5433** para no chocar.
- **Docker Desktop no arranca con Windows** y alguna vez murió dejando un socket huérfano
  en `%LOCALAPPDATA%\docker-secrets-engine`. Si no levanta, renombrá esa carpeta y volvé
  a abrirlo.

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

**Funciona de verdad:** detección de caídas por pose con confirmación temporal (11 pruebas automatizadas) · alta y baja de cámaras desde el dashboard · asignación de módulos por drag & drop que persiste en la base · captura simultánea de varias cámaras USB/RTSP con reconexión · detección YOLO de personas y vehículos · reglas de confianza/persistencia/cooldown · eventos persistidos con aislamiento multi-tenant · workflow de revisión humana con auditoría · video en vivo con cajas de detección.

**Todavía no:** no hay pantalla de login (la sesión se inicia sola con el usuario de desarrollo), los clips se guardan en disco pero no suben a MinIO, no hay login (se usan tokens de desarrollo), y `helmet-detection` sigue siendo un stub — **detectar EPP necesita un modelo entrenado para eso**, que YOLO base no trae.
