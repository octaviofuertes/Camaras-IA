> Parte de la documentación de arquitectura de **Percepta** — Plataforma SaaS de Análisis Inteligente de Video con IA modular. Ver [índice](README.md).

## Flujo de Procesamiento de Video y Flujo de Inteligencia Artificial

Esta sección especifica el camino crítico de la plataforma: desde el paquete RTSP que sale de una cámara IP hasta el mensaje `detections.raw` que consume el `rules-engine`. Es el sub-sistema con mayor consumo de recursos (CPU de decode, GPU de inferencia, ancho de banda) y el que define la economía unitaria del SaaS (costo por cámara/hora). Todas las decisiones se justifican con su trade-off y se alinean con los principios compartidos: **un solo decode por cámara**, **frame compartido entre N módulos**, **batching por modelo** y **human-in-the-loop** (toda salida es una alerta con `score`, jamás una acción).

### 0. Decisiones de arquitectura de referencia (resumen ejecutable)

| # | Decisión | Alternativa descartada | Razón |
|---|----------|------------------------|-------|
| D1 | **Decode único por cámara** en `media-service` (NVDEC si hay GPU, libx264 SW si no), frame publicado a un *plano de frames* compartido | Un decode por módulo dentro de cada `ai-worker` | N decodes = N× costo CPU/GPU; una cámara con 4 módulos costaría 4 decodes. Con 1000 cámaras es inviable. |
| D2 | **Fan-out del mismo frame** por referencia (SHM/CUDA IPC co-locado; frame serializado solo si cruza nodo) | Copiar el frame a cada worker | Zero-copy intra-nodo; el copy solo se paga cuando es inevitable (cloud disaggregado). |
| D3 | **Batching por MODELO**, no por cámara | Batch por cámara | La GPU se satura con batches homogéneos del mismo modelo; frames de 8 cámaras que corren YOLOv8n van en un tensor `[8,3,640,640]`. |
| D4 | **Vista en vivo = passthrough WebRTC** (go2rtc/mediamtx) sin recodificar | Transcodificar siempre a WebRTC en `media-service` | El H.264/H.265 de la cámara suele ser compatible con WebRTC; recodificar 1000 cámaras quema CPU sin valor. Solo se transcodifica si el códec no es negociable (p. ej. HEVC en navegador sin soporte). |
| D5 | **Ring buffer segmentado en GOPs** (fMP4) para clips pre/post-evento; ensamblado on-demand por `evidence-service` | Grabar todo a disco 24/7 | El ring buffer mantiene solo N segundos en RAM/NVMe; la evidencia se materializa a MinIO/S3 solo cuando nace un evento. |
| D6 | **Tracking en el `ai-worker`** (ByteTrack por defecto), estado en Redis; agregación temporal (conteo/merodeo/permanencia) en `rules-engine` con ventanas | Tracking en `rules-engine` | El `track_id` debe viajar dentro de `detections.raw`; ByteTrack necesita las cajas crudas frame-a-frame que solo el worker tiene. |
| D7 | **Backpressure con descarte elegante del frame más viejo** (drop-oldest) por cámara/módulo, nunca del evento | Cola infinita / bloqueo | En tiempo real un frame viejo no vale nada; se degrada FPS de análisis, no se cae el pipeline. |

---

### 1. Pipeline de ingesta: RTSP → `media-service` → frames → cola

#### 1.1 Topología del `media-service`

`media-service` es un servicio NestJS que **orquesta** procesos FFmpeg/go2rtc (no decodifica en el event loop de Node). Cada cámara activa se materializa como un `IngestWorker`: un proceso hijo FFmpeg supervisado, más un descriptor en Redis.

```mermaid
flowchart LR
  subgraph CAM[Camara IP]
    R[RTSP H.264/H.265\nmain + sub stream]
  end
  subgraph MS[media-service host / edge node]
    SUP[Ingest Supervisor\nNestJS] -->|spawn/monitor| FF[FFmpeg\nNVDEC decode]
    FF -->|frames BGR/NV12| SHM[(Shared Frame Plane\n/dev/shm o GPU surface pool)]
    FF -->|GOP fMP4| RB[(Ring Buffer\n30-40s por camara)]
    G2R[go2rtc / mediamtx] -->|WebRTC passthrough| LIVE((Vista en vivo))
    SUP --> HEALTH[Health probe\n-> device-service]
  end
  R -->|RTSP/TCP| FF
  R -->|RTSP/TCP| G2R
  SHM -->|frame descriptor| IO[inference-orchestrator]
```

**Nota de eficiencia:** el *sub-stream* de la cámara (baja resolución, p. ej. 640×360) se usa para inferencia cuando el módulo no requiere alta resolución; el *main-stream* (1080p/4K) se reserva para evidencia y para módulos que necesitan detalle (LPR/matrículas, rostros). Esto se decide por módulo en `camera_module_configs.config` (ver §1.4).

#### 1.2 Comando FFmpeg de ingesta (decode con NVDEC + tee)

Un solo proceso FFmpeg cumple tres funciones simultáneas mediante `tee`/salidas múltiples: **(a)** frames crudos al plano compartido, **(b)** segmentos fMP4 al ring buffer, sin recodificar (`-c copy`).

```bash
ffmpeg -hide_banner -loglevel warning \
  -rtsp_transport tcp -stimeout 5000000 \        # TCP + timeout socket 5s
  -fflags nobuffer -flags low_delay \
  -hwaccel cuda -hwaccel_output_format cuda \     # NVDEC, frame queda en GPU
  -i "rtsp://user:pass@10.0.0.10:554/main" \
  \
  -map 0:v -vf "fps=15,scale_cuda=640:360,format=nv12" \  # rama inferencia
  -f rawvideo -pix_fmt nv12 "unix:///run/percepta/frames/cam_{uuid}.sock" \
  \
  -map 0:v -c:v copy -an \                         # rama evidencia: SIN recodificar
  -f segment -segment_time 2 -segment_format mpegts \
  -reset_timestamps 1 "/dev/shm/percepta/rb/cam_{uuid}/%08d.ts"
```

Decisiones:
- **`-rtsp_transport tcp`**: evita pérdida/reordenamiento de UDP en LAN saturada; el trade-off es algo más de latencia, aceptable para analítica.
- **`-hwaccel cuda … scale_cuda`**: decode y resize ocurren en GPU; el frame nunca baja a RAM host si el worker está co-locado (CUDA IPC). En nodos sin GPU se cae a `-hwaccel none` + `scale` SW.
- **Rama evidencia con `-c:v copy`**: se guardan los mismos NAL units de la cámara; **cero recodificación**, cero pérdida de calidad, costo CPU ~0.
- **`fps=15`**: decimación base a nivel de decode. El submuestreo *por módulo* (más fino) ocurre en el `inference-orchestrator` (§4.3), no aquí, para no re-lanzar FFmpeg al cambiar la config de un módulo.

#### 1.3 Reconexión y tolerancia a fallos

Máquina de estados del `IngestWorker`, persistida en `device-service` (`streams.status`) y emitida como salud:

```mermaid
stateDiagram-v2
  [*] --> CONNECTING
  CONNECTING --> STREAMING: primer frame < 10s
  CONNECTING --> BACKOFF: timeout / EOF / 401
  STREAMING --> DEGRADED: FPS < 60% objetivo o gaps PTS
  STREAMING --> BACKOFF: RTSP teardown / EOF
  DEGRADED --> STREAMING: se recupera
  DEGRADED --> BACKOFF: sigue cayendo
  BACKOFF --> CONNECTING: retry (exp backoff + jitter)
  BACKOFF --> DISABLED: max_retries o credencial invalida
  DISABLED --> [*]
```

- **Backoff exponencial con jitter**: `delay = min(30s, 1s * 2^intento) ± rand(0..1s)`. Evita *thundering herd* cuando un NVR reinicia y 200 cámaras caen a la vez.
- **Watchdog de PTS**: si el `pts` no avanza durante `> 3s` (cámara colgada pero socket vivo), se mata FFmpeg y se reconecta. FFmpeg no siempre detecta el freeze; el supervisor sí.
- **Credencial inválida (401/403)** → estado `DISABLED`, evento de salud a `device-service`, **no** se reintenta en loop (protege de bloqueo de cuenta en la cámara). Las credenciales viven cifradas en el vault de `device-service`.
- **Aislamiento de fallos**: un proceso FFmpeg por cámara ⇒ una cámara corrupta no tumba las demás. El supervisor NestJS solo orquesta; si el supervisor cae, K8s lo reinicia y re-adopta procesos por PID file.

#### 1.4 Múltiples resoluciones/FPS y submuestreo por módulo

Cada asignación `camera_module_configs` declara sus requisitos de entrada; el `inference-orchestrator` los resuelve contra los planos disponibles (main/sub) sin exigir un decode extra salvo que una resolución no exista.

```jsonc
// camera_module_configs.config (JSONB) — validado contra el JSON Schema del módulo
{
  "input": {
    "stream": "sub",           // "main" | "sub"  -> reusa decode existente
    "targetFps": 5,            // el orquestador decima 15fps -> 5fps para este módulo
    "resolution": [640, 360],
    "colorSpace": "BGR"
  },
  "roi": {                     // zonas/lineas del módulo (§6)
    "zones": [{ "id": "z_entrada", "polygon": [[0.1,0.2],[0.9,0.2],[0.9,0.95],[0.1,0.95]] }],
    "lines": [{ "id": "l_conteo", "from": [0.0,0.5], "to": [1.0,0.5] }]
  }
}
```

Regla de resolución de streams para minimizar decodes:

| Requisito del módulo | Se resuelve con | Decode adicional |
|---|---|---|
| `sub` @ ≤15fps @ ≤640×360 | rama sub existente | No |
| `main` @ ≤15fps | rama main (si el módulo la pide) | No, si ya hay un módulo main; sí, primera vez |
| FPS mayor al del plano | se eleva el `fps=` del decode y todos los módulos se decimna hacia abajo | Se re-negocia el decode una vez |
| Resolución intermedia (p. ej. 1280×720 para LPR) | `scale` GPU extra en el mismo FFmpeg (`tee`) | No (mismo proceso, otra salida) |

El principio: **el decode se dimensiona al módulo más exigente de la cámara**, y todos los demás módulos se sirven por decimación/escalado del mismo pipeline. Nunca hay un FFmpeg por módulo.

---

### 2. Vista en vivo con WebRTC (passthrough, sin recodificar)

La vista en vivo del dashboard **no pasa por el pipeline de inferencia**. Es una ruta independiente servida por **go2rtc/mediamtx** embebido junto a `media-service`, para no acoplar latencia de UI con carga de GPU.

```mermaid
sequenceDiagram
  participant UI as Angular (operador)
  participant GW as api-gateway
  participant G2R as go2rtc/mediamtx
  participant CAM as Camara RTSP

  UI->>GW: GET /api/v1/cameras/{id}/live (JWT)
  GW->>GW: authz RBAC + tenant (organization_id)
  GW-->>UI: {whepUrl, iceServers, token efimero}
  UI->>G2R: WHEP offer (SDP) + token
  G2R->>CAM: RTSP pull (si no hay sesion activa)
  G2R-->>UI: SDP answer (H.264 passthrough)
  Note over G2R,UI: RTP -> WebRTC sin transcode<br/>si el codec es negociable
  UI-->>G2R: ICE / DTLS-SRTP (media directo)
```

Decisiones:
- **Passthrough primero**: go2rtc negocia el códec nativo de la cámara (H.264 baseline/main es directamente empaquetable a WebRTC). **Costo CPU marginal**: solo repacketización RTP→SRTP, no decode+encode.
- **Transcode solo como fallback**: si la cámara emite H.265/HEVC y el navegador no lo soporta por WebRTC, go2rtc transcodifica *bajo demanda y solo mientras haya un espectador* (`on-demand: true`). Sin espectadores, cero costo.
- **Sesión compartida**: N operadores viendo la misma cámara comparten **un** pull RTSP (fan-out en go2rtc); no se multiplica la carga sobre la cámara.
- **Autorización de borde**: el `api-gateway` valida RBAC + `organization_id` y entrega un token efímero (TTL corto) para el WHEP; go2rtc no expone RTSP al cliente. La media viaja P2P/TURN, la señalización por el gateway.
- **Trade-off vs HLS/LL-HLS**: WebRTC da sub-segundo de latencia (crítico para que un operador reaccione a una alerta), a cambio de mayor complejidad de NAT/TURN. Para *walls* de 30+ cámaras en modo mosaico se ofrece un perfil LL-HLS de menor costo (mayor latencia, sin sesión SRTP por tile).

---

### 3. Ring buffer y ensamblaje de clips (10s antes / evento / 10s después)

#### 3.1 Estrategia

El clip de evidencia exige **frames anteriores al evento**, imposibles de capturar reaccionando al evento. Se resuelve con un **ring buffer continuo por cámara** que retiene los últimos `PRE + POST + margen` segundos de segmentos fMP4/TS **ya codificados por la cámara** (sin recodificar, ver §1.2).

```mermaid
flowchart LR
  FF[FFmpeg -c copy\nsegmentos de 2s] --> RB
  subgraph RB[Ring Buffer por camara - NVMe/tmpfs]
    S1[seg 00042.ts\nt-8s..t-6s]
    S2[seg 00043.ts\nt-6s..t-4s]
    S3[seg 00044.ts\nt-4s..t-2s]
    S4[seg 00045.ts\nt-2s..t0]
    S5[seg 00046.ts\nt0..t+2s]
    Sn[... retencion 30-40s ...]
  end
  EV[event-service\nevents.created] -->|clipRequest t0| ES[evidence-service]
  ES -->|selecciona segmentos\n[t0-10s .. t0+10s]| RB
  ES -->|concat + faststart\nsin recodificar| MP4[clip.mp4]
  ES --> MINIO[(MinIO / S3)]
  ES -->|evidence.ready| BUS((RabbitMQ))
```

#### 3.2 Retención y dimensionamiento

- **Segmentos de 2s** alineados a GOP (keyframe al inicio de cada segmento vía `-force_key_frames` si la cámara no fuerza IDR regular). Segmentos cortos = ensamblaje preciso y menor retención total.
- **Ventana retenida = `PRE(10s) + POST(10s) + margen(10-20s)`** ≈ 30–40s por cámara. En `tmpfs` (RAM) para latencia mínima, con *spillover* a NVMe si la RAM del nodo se presiona.
- **Costo de memoria** (sub-stream 2 Mbps): `40s × 2 Mbps / 8 ≈ 10 MB/cámara`. 200 cámaras/nodo ≈ 2 GB. Con main-stream 8 Mbps ≈ 40 MB/cámara. Se retiene el **sub** para el pre-roll y el **main** solo si la cámara/plan requiere evidencia HD (config por `plan`/módulo).

#### 3.3 Ensamblaje on-demand (sin recodificar)

Al recibir `events.created`, `evidence-service` calcula el rango `[t0-PRE, t0+POST]`, espera a que exista el segmento que cubre `t0+POST` (a lo sumo `POST` segundos de espera), selecciona los segmentos que intersectan el rango y los concatena:

```bash
# 1) Lista de segmentos que cubren [t0-10s, t0+10s]
#    (evidence-service la genera a partir del index del ring buffer)
# 2) Concat sin recodificar:
ffmpeg -f concat -safe 0 -i segments.txt \
  -c copy -movflags +faststart \
  -metadata event_id={uuid} clip.mp4

# 3) Recorte fino a los limites exactos (opcional, requiere keyframe):
#    si se necesita corte al frame, solo el primer GOP se recodifica.
```

- **`-c copy`**: ensamblaje en milisegundos, sin GPU/CPU de encode. El clip conserva el bitrate/códec original.
- **Corte al frame exacto**: los límites caen en fronteras de GOP (2s). Si el requisito legal exige corte exacto, solo el primer GOP se recodifica (`-c:v libx264 -crf 18` en un pre-roll de 2s), el resto va en copy. Trade-off: precisión vs costo, configurable por `plan`.
- **Imagen de portada (thumbnail)**: `evidence-service` extrae el keyframe en `t0` como JPEG para la tarjeta de alerta del dashboard, y guarda ambos (`clip.mp4`, `thumb.jpg`) en `evidences` → MinIO/S3, emitiendo `evidence.ready`.

---

### 4. Arquitectura de inferencia: fan-out de un frame a N módulos + batching

#### 4.1 El problema del "N decodes" y su solución (plano de frames)

Una cámara con N módulos NO debe decodificarse N veces ni copiarse el frame N veces. La solución es un **plano de frames compartido** (*shared frame plane*):

```mermaid
flowchart TB
  subgraph EDGE[Nodo de computo - co-locado]
    FF[FFmpeg NVDEC] -->|1 frame NV12\nen GPU surface| POOL[(GPU Surface Pool\n+ CUDA IPC handle)]
    FF -.->|si CPU-only| SHM[(SHM /dev/shm\nframe BGR)]
    POOL --> DESC[Frame Descriptor\n camera_id, frame_id, pts,\n ipc_handle, w, h, fmt]
    SHM --> DESC
    DESC -->|Redis Stream 'frames:{cam}'| IO[inference-orchestrator]
    IO -->|gRPC: (ipc_handle, roi, model)| W1[ai-worker\nmodulo A: personas]
    IO -->|gRPC: (mismo ipc_handle)| W2[ai-worker\nmodulo B: EPP/casco]
    IO -->|gRPC: (mismo ipc_handle)| W3[ai-worker\nmodulo C: merodeo]
  end
```

- **Zero-copy intra-nodo**: el frame vive una sola vez en memoria GPU (o SHM host). El orquestador reparte **el mismo `ipc_handle`/offset**; los workers mapean la memoria, no la copian. Ganancia: para N=4 módulos, se ahorran 3 copias + 3 decodes por frame.
- **Cross-node (cloud disaggregado)**: si el worker no está co-locado, el frame debe serializarse (JPEG/tensor) y viajar por red — es el único caso donde se paga la copia. Se minimiza con la política de scheduling "afinidad de nodo por cámara" (§8.4).
- **Referencia contada + TTL**: el descriptor de frame se libera cuando todos los módulos suscritos confirmaron consumo o venció el TTL (drop-oldest, §7). Redis Stream con consumer group por módulo da *at-least-once* y visibilidad de lag.

#### 4.2 Batching por MODELO (clave de la eficiencia GPU)

El orquestador **no** agrupa por cámara; agrupa por **(modelo, resolución, precisión)**. Frames de distintas cámaras que corren el mismo YOLOv8n@640 se apilan en un tensor `[B,3,640,640]`.

```mermaid
flowchart LR
  subgraph IO[inference-orchestrator - dynamic batcher]
    Q1[Cola modelo=yolov8n-coco@640\ncam1,cam7,cam3,...]
    Q2[Cola modelo=ppe-yolo@640]
    Q3[Cola modelo=lpr@1280]
  end
  Q1 -->|batch=8 o 4ms timeout| GPU0[(ai-worker GPU0\nTensorRT engine yolov8n)]
  Q2 -->|batch=8| GPU0
  Q3 -->|batch=4| GPU1[(ai-worker GPU1\nengine LPR)]
```

- **Dynamic batching**: se cierra el batch cuando llega a `max_batch` **o** se cumple `max_delay` (p. ej. 4–8 ms). Trade-off latencia↔throughput: batches grandes suben FPS/GPU pero añaden hasta `max_delay` de latencia. Para alertas de seguridad se prioriza latencia baja; para conteo/analítica se permite batch mayor.
- **Un engine cargado, muchas cámaras**: el modelo `yolov8n-coco` se carga **una vez** en la GPU y sirve a todas las cámaras que lo usan (ver §5.3). No hay una copia de pesos por cámara.

#### 4.3 Submuestreo (decimación) y prioridad por módulo

- **Decimación fina por módulo** en el orquestador: cada `camera_module_configs.input.targetFps` define cada cuántos frames se envía a ese módulo. `personas@15fps` corre en todos los frames; `merodeo@2fps` se sirve 1 de cada ~7. Ahorra GPU sin re-lanzar decode.
- **Colas por prioridad**: `class-of-service` por módulo (`realtime` | `standard` | `batch`). Módulos de seguridad crítica (intrusión, caída de persona) → cola `realtime` con `max_delay` bajo y preferencia de scheduling. Analítica (heatmap, conteo) → `batch`. En saturación se degrada primero `batch` (§7).

---

### 5. Gestión de GPU: colocación, memoria, runtimes y colas por dispositivo

#### 5.1 Runtime de inferencia

- **Estándar de exportación: ONNX** como formato de intercambio del módulo (`module.json` declara `backend`), y **TensorRT** como runtime de despliegue en NVIDIA (engine compilado por GPU/driver). Fallback **ONNX Runtime** (CUDA/CPU EP) donde no hay TensorRT (edge heterogéneo, CPU-only, AMD/Intel vía OpenVINO/DirectML).
- **Precisión**: FP16 por defecto en TensorRT (2× throughput, memoria a la mitad, pérdida de precisión despreciable en detección); INT8 con calibración para modelos de alto volumen (conteo) donde el ahorro justifica el trabajo de calibración. La precisión se declara por módulo y se valida con el set de regresión antes de publicar la versión.
- **Compilación de engines cacheada**: el engine TensorRT se compila una vez por (modelo, versión, GPU arch, precisión) y se cachea en MinIO/volumen; los workers lo cargan, no lo recompilan.

#### 5.2 Servidor de inferencia y colas por dispositivo

El `ai-worker` puede envolver **NVIDIA Triton Inference Server** (o un runner propio equivalente) para obtener, out-of-the-box: *model repository*, *dynamic batching*, *concurrent model execution* (varias instancias/model en la misma GPU) y *instance groups* por dispositivo.

```yaml
# config.pbtxt (Triton) para un modulo de deteccion de personas
name: "yolov8n_coco"
platform: "tensorrt_plan"
max_batch_size: 16
input  [{ name: "images", data_type: TYPE_FP16, dims: [3,640,640] }]
output [{ name: "output0", data_type: TYPE_FP16, dims: [84,8400] }]
dynamic_batching {
  preferred_batch_size: [4, 8, 16]
  max_queue_delay_microseconds: 5000        # 5 ms -> latencia acotada
}
instance_group [{ count: 2, kind: KIND_GPU, gpus: [0] }]   # 2 instancias en GPU0
```

- **Colas por dispositivo**: cada GPU tiene su(s) cola(s); el orquestador enruta el batch a la GPU que hospeda el engine requerido. Métricas por cola (`queue_delay`, `gpu_util`, `vram_used`) alimentan el autoscaler.
- **Concurrencia intra-GPU**: 2–4 instancias por modelo para solapar copia H2D/D2H con cómputo y ocultar latencia; el límite lo impone la VRAM.

#### 5.3 Colocación de modelos y presupuesto de memoria (bin-packing)

El orquestador resuelve un problema de **bin-packing**: colocar los modelos requeridos por las cámaras en las GPUs disponibles, respetando VRAM y objetivo de FPS.

```mermaid
flowchart TB
  subgraph GPU0[GPU0 - 24GB - 40 TFLOPs FP16]
    M1[yolov8n-coco FP16\n~120MB pesos\nsirve 40 cam]
    M2[ppe-yolo FP16\n~180MB\nsirve 25 cam]
    RES1[reserva activaciones\ny batch buffers]
  end
  subgraph GPU1[GPU1 - 24GB]
    M3[lpr-crnn INT8\n~90MB]
    M4[pose-fall FP16\n~250MB]
  end
```

- **Modelos compartidos entre cámaras**: la clave económica. Un engine = muchas cámaras. Colocar juntas las cámaras que comparten modelo maximiza reutilización y batch.
- **Presupuesto VRAM** = pesos + activaciones(`~ f(max_batch, resolución)`) + buffers I/O + overhead CUDA (~500MB–1GB/proceso). El planner reserva con headroom del 15%.
- **Anti-fragmentación**: modelos "pesados y raros" (pose, LPR HD) se agrupan en GPUs dedicadas; modelos "ligeros y frecuentes" (detección genérica) se densifican. Evita que un modelo de 250MB desplace 10 cámaras de un modelo de 120MB.
- **MPS/MIG**: en GPUs grandes (A100/H100) se usa **MIG** para aislar tenants o clases de servicio con QoS dura; en GPUs de gama media, **CUDA MPS** para solapamiento sin aislamiento estricto.

---

### 6. Tracking (ByteTrack/DeepSORT) y agregación temporal

El `track_id` es imprescindible para conteo (una persona = un cruce, no uno por frame), merodeo (misma persona > T segundos en zona) y permanencia (dwell time). Se produce en el `ai-worker`; la lógica temporal de negocio vive en `rules-engine`.

#### 6.1 Elección de tracker

| Tracker | Costo | Robustez a oclusión | Cuándo |
|---|---|---|---|
| **ByteTrack** (por defecto) | Bajo (solo IoU + Kalman, sin red de apariencia) | Media-alta (usa cajas de baja confianza) | Conteo/permanencia/merodeo, alta densidad de cámaras. Corre en CPU del worker. |
| **DeepSORT / BoT-SORT** (ReID) | Alto (extractor de apariencia en GPU) | Alta (re-identifica tras oclusión larga) | Módulos que exigen re-ID (seguir a la misma persona entre oclusiones o entre cámaras). Opt-in por módulo. |

Decisión: **ByteTrack por defecto** (mejor relación robustez/costo para el 90% de módulos); ReID solo cuando el `module.json` lo declara, porque añade una GPU-pass de apariencia por objeto.

#### 6.2 Estado de tracking y agregación

```mermaid
flowchart LR
  W[ai-worker\nByteTrack por camara] -->|bbox+track_id\npor frame| DR[detections.raw]
  W <-->|estado de tracks\n(TTL, ultimo pts)| REDIS[(Redis\ntrack:{cam})]
  DR --> RE[rules-engine]
  subgraph RE_AGG[rules-engine - agregacion temporal]
    CNT[Conteo: cruce de linea\npor track_id -> +1]
    LOI[Merodeo: dwell>T en zona\npor track_id]
    OCC[Permanencia: entrada/salida\nde zona, tiempo acumulado]
  end
  RE --> RE_AGG --> EV[event-service\nevents.created]
```

- **Estado del tracker en Redis** (`track:{camera_id}`), no en memoria del worker: si el worker se reprograma a otra GPU o reinicia, el tracking se recupera sin perder IDs (dentro de un TTL corto). Trade-off: latencia de Redis vs continuidad; se usa Redis local al nodo para el hot-path.
- **Conteo por cruce de línea**: `rules-engine` mantiene, por `track_id`, el lado de la línea; un cambio de lado en la dirección configurada = un conteo. Deduplicación natural por `track_id` (no cuenta el mismo objeto dos veces).
- **Merodeo (loitering)**: ventana deslizante por `track_id` dentro de `zone`; si `dwell_time > umbral` (config del módulo) → evento. Cooldown por `track_id` para no re-disparar cada frame.
- **Permanencia/aforo**: conteo de `track_id` activos dentro de una zona → serie temporal a `analytics-service` (TimescaleDB); umbral de aforo → alerta.
- **Deduplicación/cooldown**: se aplica en `rules-engine` con clave `(camera_id, module_id, track_id, event_type)` y ventana de cooldown, para que N frames de la misma persona merodeando generen **una** alerta, no cientos.

---

### 7. Contrato de datos: `detections.raw`

Mensaje que el `ai-worker` publica al exchange topic `detections.raw` y que consume `rules-engine`. Routing key: `detections.raw.<organization_id>.<camera_id>.<module_id>` (permite bindings por tenant/cámara/módulo).

#### 7.1 Esquema (JSON canónico; gRPC/protobuf en el hot-path binario)

```jsonc
{
  "schemaVersion": "1.0",
  "organizationId": "b3f1...uuid",       // multitenancy (RLS aguas abajo)
  "siteId": "a12c...uuid",
  "cameraId": "c9d2...uuid",
  "streamId": "s4e8...uuid",
  "moduleId": "person-detection",         // ai_modules.id (catalogo)
  "moduleVersion": "2.3.1",
  "frame": {
    "frameId": 148213,                    // monotonico por camara
    "pts": 148213000,                     // 90kHz o us, segun clock
    "capturedAt": "2026-07-30T14:22:31.512Z",  // UTC ISO-8601
    "width": 640, "height": 360,
    "inferenceLatencyMs": 11.4
  },
  "detections": [
    {
      "detId": "d1",
      "class": "person",                  // vocabulario del modulo
      "classId": 0,
      "score": 0.91,                       // confianza del modelo (human-in-the-loop)
      "bbox": { "x": 0.42, "y": 0.30, "w": 0.08, "h": 0.22, "norm": true }, // normalizado 0..1
      "trackId": "cam9:trk:5521",         // estable por camara; null si sin tracking
      "trackAgeFrames": 37,
      "velocity": { "vx": -0.004, "vy": 0.001 },  // opcional (por frame, normalizado)
      "zones": ["z_entrada"],             // zonas de camera_module_configs que contienen el centroide
      "lineEvents": [                      // cruces detectados en este frame
        { "lineId": "l_conteo", "direction": "in" }
      ],
      "attributes": {                      // libre por modulo (JSON Schema del modulo)
        "ppe": { "helmet": false, "vest": true },
        "poseState": "standing"
      },
      "embedding": null                    // vector ReID (base64/f16) solo si el modulo hace re-ID
    }
  ],
  "producer": { "workerId": "ai-worker-7", "gpu": "GPU0", "runtime": "tensorrt-fp16" }
}
```

#### 7.2 Reglas del contrato

| Regla | Motivo |
|---|---|
| **bbox normalizado (0..1)** con `norm:true` | Independiente de resolución; `rules-engine` evalúa ROI (también normalizado) sin conocer el tamaño real. |
| **`score` siempre presente** | Principio human-in-the-loop: toda detección lleva confianza; el evento resultante la propaga a la alerta. |
| **`trackId` con prefijo de cámara** | Único global; evita colisión entre cámaras. `null` explícito si el módulo no trackea. |
| **`schemaVersion` + `moduleVersion`** | Evolución sin romper `rules-engine`; consumidores validan compat. |
| **`capturedAt` = tiempo de captura, no de publicación** | La lógica temporal (merodeo, dwell) usa el tiempo del frame, no el de proceso; robusto ante backpressure. |
| **Payload SIN el frame** | `detections.raw` es metadata liviana (<2KB típico). El pixel viaja por el plano de frames/evidencia, nunca por el bus. |
| **Encoding hot-path: protobuf sobre gRPC/AMQP** | JSON para debug/contrato; protobuf en producción por tamaño y velocidad de (de)serialización a miles de msg/s. |

Contrato protobuf equivalente (extracto), fuente de verdad para codegen:

```proto
message Detection {
  string det_id = 1;
  string clazz = 2;
  uint32 class_id = 3;
  float  score = 4;
  BBoxNorm bbox = 5;            // x,y,w,h en 0..1
  string track_id = 6;         // vacio si sin tracking
  uint32 track_age_frames = 7;
  repeated string zones = 8;
  repeated LineEvent line_events = 9;
  google.protobuf.Struct attributes = 10;  // esquema por modulo
  bytes embedding = 11;        // f16 opcional
}
```

---

### 8. Rendimiento, escala y modos de despliegue

#### 8.1 Cifras de referencia (planificación de capacidad)

Valores de diseño para dimensionar planes SaaS y nodos (dependen de modelo/resolución/GPU; se calibran por benchmark de regresión por versión de módulo).

| Modelo (FP16, 640×640) | GPU | FPS agregado (batch) | Cámaras @ ded. FPS |
|---|---|---|---|
| YOLOv8n (nano) | T4 (16GB) | ~450–600 fps | ~40 @ 15fps / ~90 @ 5fps |
| YOLOv8s (small) | T4 | ~200–280 fps | ~18 @ 15fps |
| YOLOv8n | L4 / A10 | ~1100–1500 fps | ~90 @ 15fps |
| Pose/fall (medium) | A10 | ~120–180 fps | ~10 @ 12fps |
| LPR (det+OCR, 1280) | A10 | ~90–140 fps | evento-driven (no continuo) |

**Reglas de dedo:**
- **FPS efectivo por cámara = Σ(targetFps de sus módulos)**, no el FPS de decode. Una cámara con 3 módulos a 15/5/2 fps = 22 fps de inferencia agregada.
- **Cámaras por `ai-worker`** = `capacidad_fps_GPU / FPS_efectivo_medio_por_camara`, dejando 30% de headroom para picos y batch imperfecto.
- **Objetivo económico**: densificar hacia el sub-stream y decimar módulos no críticos; bajar de 15→5 fps donde el fenómeno lo permite (merodeo, aforo) casi triplica la densidad de cámaras.

#### 8.2 Backpressure y descarte elegante

El sistema es *soft-real-time*: perder frames degrada la tasa de muestreo, no la corrección. Política en capas:

```mermaid
flowchart TB
  A[Frame nuevo] --> B{Cola del modulo\nllena?}
  B -- No --> C[Encolar]
  B -- Si --> D{Class of service}
  D -- batch/standard --> E[Drop-oldest\nincrementa metric dropped_frames]
  D -- realtime --> F{VRAM/GPU\ncritico?}
  F -- No --> G[Preempt cola batch\ny encolar]
  F -- Si --> H[Drop-oldest realtime\n+ alerta de salud a device-service]
  C --> Z[Inferencia]
  G --> Z
```

- **Drop-oldest, nunca drop-event**: se descarta el frame más viejo (ya irrelevante), jamás un evento ya generado. La cola de `detections.raw`/`events.created` en RabbitMQ es *durable*; la cola de frames es efímera y descartable.
- **Degradación graciosa priorizada**: bajo presión, primero se decima `batch` (analítica), luego `standard`, y solo en extremo `realtime` (seguridad), emitiendo señal de salud ("cámara en modo degradado") a `device-service` y al dashboard.
- **Métricas de contrato**: `dropped_frames`, `queue_delay_ms`, `effective_fps` por (cámara, módulo) → si `effective_fps < min_fps` del módulo por > X s, se genera un evento operativo (no una alerta de seguridad) para el tenant.
- **Autoscaling**: KEDA/HPA sobre `gpu_util` + `queue_delay` escala el pool de `ai-worker`; el `inference-orchestrator` re-balancea cámaras a los nuevos workers respetando afinidad de modelo.

#### 8.3 Edge vs Cloud vs Híbrido

```mermaid
flowchart LR
  subgraph E[EDGE - on-premise]
    C1[Camaras LAN] --> MSE[media-service edge]
    MSE --> IOE[inference-orchestrator edge]
    IOE --> WE[ai-worker edge GPU]
    WE -->|detections.raw + eventos\n(solo metadata)| WAN
  end
  subgraph CL[CLOUD - control plane]
    WAN --> GW[api-gateway]
    GW --> ES[event-service / analytics / billing]
    ES --> DASH[Dashboard]
  end
```

| Criterio | Edge (on-prem) | Cloud | Híbrido (recomendado por defecto) |
|---|---|---|---|
| **Decode+inferencia** | En sitio | En cloud (requiere subir video) | Decode+inferencia en edge; control/analítica en cloud |
| **Ancho de banda WAN** | Mínimo (solo metadata + evidencia bajo demanda) | Alto (video crudo continuo) | Mínimo |
| **Latencia de alerta** | Sub-100ms local | +RTT WAN | Sub-100ms local |
| **Privacidad** | Video no sale del sitio | Video en cloud | Video se queda en sitio (privacidad por diseño) |
| **Costo GPU** | CAPEX cliente (licencia on-prem via `billing-service`) | OPEX pay-per-use | Mixto |
| **Continuidad si cae WAN** | Sigue detectando y encola eventos | Se detiene | Sigue local; sincroniza al reconectar |

**Recomendación arquitectónica:** el **híbrido** es el modo de referencia. El plano de datos pesado (RTSP→decode→GPU→`detections.raw`) corre en el **edge** (zero-copy, sin exponer video, baja latencia, robusto ante corte WAN con *store-and-forward* de eventos en RabbitMQ local). El plano de control (auth, `event-service`, `analytics-service`, `billing-service`, dashboard) vive en **cloud** y solo recibe **metadata + evidencia bajo demanda**. Cloud-puro se ofrece para clientes sin GPU en sitio (aceptando el costo/latencia de subir video); edge-puro (air-gapped) para clientes con requisito de que ningún dato salga, sincronizando el control plane cuando hay conectividad.

---

### 9. Diagrama de secuencia del pipeline completo (cámara → dashboard)

```mermaid
sequenceDiagram
  autonumber
  participant CAM as Camara RTSP
  participant MS as media-service (FFmpeg NVDEC)
  participant RB as Ring Buffer
  participant IO as inference-orchestrator
  participant AW as ai-worker (modulos + ByteTrack)
  participant RE as rules-engine
  participant EV as event-service
  participant ES as evidence-service
  participant NS as notification-service
  participant GW as api-gateway (WS/SSE)
  participant UI as Dashboard Angular

  CAM->>MS: RTSP H.264 (TCP)
  MS->>MS: Decode unico (NVDEC) + scale
  MS->>RB: Segmentos fMP4 -c copy (retencion 30-40s)
  MS->>IO: Frame descriptor (ipc_handle, pts, cam)
  Note over IO: decimacion por modulo + batching por MODELO
  IO->>AW: gRPC batch (mismo frame -> N modulos, zero-copy)
  AW->>AW: Inferencia TensorRT + ByteTrack (track_id)
  AW-->>IO: (ack / backpressure metrics)
  AW->>RE: publish detections.raw (bbox,class,score,track_id,zones)
  RE->>RE: Evalua config (horarios, zonas, umbrales) + dedup/cooldown + agregacion temporal
  alt Condicion de alerta cumplida
    RE->>EV: events.created (evento = ALERTA, estado NUEVO, score)
    EV->>ES: solicita clip (t0-10s .. t0+10s)
    ES->>RB: selecciona segmentos del rango
    ES->>ES: concat -c copy + faststart + thumbnail
    ES-->>EV: evidence.ready (url MinIO/S3)
    EV->>NS: notifications.dispatch (segun canales del tenant)
    NS-->>UI: Email/WhatsApp/Telegram/Push
    EV->>GW: Redis pub/sub -> WS/SSE
    GW-->>UI: Alerta en tiempo real (tarjeta con score + clip)
    UI->>EV: Operador: reconoce -> confirma / descarta / falso-positivo
    Note over UI,EV: HUMAN-IN-THE-LOOP: ninguna accion automatica sobre personas
  else Sin condicion
    RE->>RE: (opcional) metrica a analytics-service (TimescaleDB)
  end
```

---

### 10. Diagrama: cómo N módulos comparten UN frame (single decode, zero-copy)

```mermaid
flowchart TB
  CAM([Camara IP RTSP]) --> DEC[media-service\nFFmpeg NVDEC\n**1 solo decode**]
  DEC --> FP[(Frame unico en\nGPU surface / SHM\n+ ipc_handle)]

  FP --> ORq[inference-orchestrator\nfan-out por referencia\n(sin copiar el frame)]

  ORq -->|targetFps 15\nROI zonas| MA[Modulo A: personas\nyolov8n compartido]
  ORq -->|targetFps 15\nROI zona obra| MB[Modulo B: EPP/casco\nppe-yolo]
  ORq -->|targetFps 2\nROI zona restringida| MC[Modulo C: merodeo\nyolov8n + ByteTrack]
  ORq -->|targetFps 5\nlinea de conteo| MD[Modulo D: conteo\nyolov8n + linea]

  MA --> DR[(detections.raw)]
  MB --> DR
  MC --> DR
  MD --> DR

  DR --> RE[rules-engine]

  classDef shared fill:#1b5e20,stroke:#0d3311,color:#fff;
  classDef mod fill:#0d47a1,stroke:#062a63,color:#fff;
  class DEC,FP shared;
  class MA,MB,MC,MD mod;
```

Puntos clave del diagrama:
- **Un decode, un frame en memoria**: el mismo `ipc_handle` se reparte a los 4 módulos; ni el decode ni el frame se multiplican.
- **Modelos compartidos**: A, C y D usan el mismo engine `yolov8n` cargado una vez; el orquestador los apila en batches del mismo modelo aunque provengan de módulos distintos y cámaras distintas.
- **Decimación independiente por módulo**: cada módulo consume su cadencia (`targetFps`) del mismo flujo, sin afectar a los demás.
- **ROI/zonas por módulo**: cada módulo aplica su recorte/zonas declarados en `camera_module_configs`, todos sobre el mismo pixel de origen.
- **Salida homogénea**: los cuatro emiten `detections.raw` con el contrato de §7; `rules-engine` es el único punto donde la config por cámara/módulo se convierte en alertas para revisión humana.

---

⬅ [Anterior](03-apis-seguridad-y-auditoria.md) · [Índice](README.md) · [Siguiente ➡](05-modulos-ia-motor-de-reglas-y-eventos.md)
