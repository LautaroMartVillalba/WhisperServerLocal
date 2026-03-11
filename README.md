# Whisper-Local

Servicio de transcripción de audio local usando [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Arquitectura híbrida **Go + Python** con mensajería vía **RabbitMQ**.

Go actúa como orquestador: gestiona la concurrencia, la mensajería y la validación superficial. Python ejecuta el procesamiento pesado de audio y la inferencia del modelo Whisper, que se carga **una sola vez en memoria** al arrancar cada worker.

---

## Arquitectura

```
[Productor externo]
        │
        ▼ publica en
┌────────────────────────────────────────┐
│  whisper_exchange (RabbitMQ, direct)   │
│  routing key: transcription.request    │
└──────────────┬─────────────────────────┘
               │
               ▼ consume
┌──────────────────────────────────────────────┐
│             Go Orchestrator                  │
│  ┌───────────────────────────────────────┐   │
│  │  Worker Pool (N goroutines)           │   │
│  │   - Valida existencia del archivo     │   │
│  │   - Valida extensión soportada        │   │
│  │   - Delega al Process Pool            │   │
│  └──────────────┬────────────────────────┘   │
│                 │ stdin/stdout JSON           │
│  ┌──────────────▼────────────────────────┐   │
│  │  Process Pool (N procesos Python)     │   │
│  │   - Convierte audio a WAV 16kHz mono  │   │
│  │   - Transcribe con faster-whisper     │   │
│  └───────────────────────────────────────┘   │
└──────────────┬───────────────────────────────┘
               │
       ┌───────┴────────┐
       ▼                ▼
  [Éxito]          [Fallo / Reintento]
       │                │
       ▼                ▼ TTL 5s → DLX → whisper_transcriptions
whisper_results   whisper_retry_queue  (hasta 2 reintentos)
```

---

## Mensajería RabbitMQ

> Esta sección define el **contrato completo** de comunicación con el servicio. Toda la topología se declara automáticamente al iniciar; no es necesario crearla manualmente.

### Topología declarada

| Recurso | Tipo | Nombre |
|---|---|---|
| Exchange de entrada | `direct`, durable | `whisper_exchange` |
| Cola de entrada | durable | `whisper_transcriptions` |
| Exchange de resultados | `direct`, durable | `whisper_results_exchange` |
| Cola de resultados | durable | `whisper_results` |
| Exchange de reintentos | `direct`, durable | `whisper_retry_exchange` |
| Cola de reintentos | durable, TTL 5s, DLX → `whisper_exchange` | `whisper_retry_queue` |

---

### 📥 Mensaje de Entrada — Request

**Dónde publicar:**
- Exchange: `whisper_exchange`
- Routing Key: `transcription.request`
- Cola destino: `whisper_transcriptions`

> **Requisito del archivo de audio:** la ruta `audio_file_path` debe ser **accesible desde el sistema de archivos del contenedor/host donde corre el servicio**. Con Docker, monta el directorio de audios como volumen compartido entre el servicio productor y `whisper-api`. El `docker-compose.yml` monta `/tmp/shared_audio` por defecto.

```json
{
  "attachment_id": 123,
  "audio_file_path": "/tmp/shared_audio/grabacion.mp3",
  "language": "es",
  "import_batch_id": 7
}
```

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `attachment_id` | `int` | ✅ | Identificador único del trabajo. Se devuelve en el resultado para correlacionar la respuesta. |
| `audio_file_path` | `string` | ✅ | Ruta absoluta al archivo de audio accesible desde el contenedor del servicio. |
| `language` | `string` | ❌ | Código de idioma ISO 639-1 (ej: `"es"`, `"en"`, `"pt"`). Si se omite o es `""`, Whisper lo detecta automáticamente. |
| `import_batch_id` | `int \| null` | ❌ | Ver sección [import_batch_id](#import_batch_id). |

**Formatos de audio soportados:** `.opus`, `.mp3`, `.wav`, `.m4a`, `.ogg`, `.flac`, `.aac`, `.wma`

**Modificar el tipo del mensaje:** `TranscriptionRequest` en [internal/rabbitmq/types.go](internal/rabbitmq/types.go).

---

### 📤 Mensaje de Salida — Result

**Dónde consumir:**
- Exchange: `whisper_results_exchange`
- Routing Key: `transcription.result`
- Cola: `whisper_results`

El servicio **siempre publica exactamente un resultado por cada job recibido**, ya sea exitoso o fallido. No hay jobs que queden sin respuesta (salvo errores de red al publicar, que generan NACK con requeue).

#### Resultado exitoso (`success: true`)

```json
{
  "attachment_id": 123,
  "texto": "Hola, esto es una transcripción de prueba.",
  "duration": 12.45,
  "model": "base",
  "success": true,
  "import_batch_id": 7,
  "processing_time_ms": 3241
}
```

#### Resultado con error (`success: false`)

```json
{
  "attachment_id": 123,
  "texto": "",
  "duration": 0,
  "model": "base",
  "success": false,
  "import_batch_id": 7,
  "error_message": "Audio file not found: /tmp/shared_audio/grabacion.mp3"
}
```

| Campo | Tipo | Siempre presente | Descripción |
|---|---|---|---|
| `attachment_id` | `int` | ✅ | Mismo valor recibido en el request. |
| `texto` | `string` | ✅ | Texto transcrito. Vacío (`""`) si hubo error. |
| `duration` | `float64` | ✅ | Duración del audio en segundos. `0` si hubo error. |
| `model` | `string` | ✅ | Nombre del modelo Whisper usado (ej: `"base"`). |
| `success` | `bool` | ✅ | `true` si la transcripción fue exitosa, `false` en cualquier tipo de error. |
| `import_batch_id` | `int \| null` | ✅ | Mismo valor recibido en el request. |
| `error_message` | `string` | ❌ | Descripción del error. Solo presente cuando `success` es `false`. |
| `processing_time_ms` | `int64` | ❌ | Tiempo total de procesamiento en milisegundos, medido en Go desde antes de invocar Python hasta recibir la respuesta. Solo presente cuando `success` es `true`. |

**Modificar el tipo del mensaje:** `TranscriptionResult` en [internal/rabbitmq/types.go](internal/rabbitmq/types.go).

---

### 🔁 Sistema de Reintentos

Cuando una transcripción falla (error de Python, proceso muerto, fallo de validación de audio), el job entra al mecanismo de reintentos.

**Flujo:**
1. Fallo → el orchestrator publica el request original en `whisper_retry_exchange` con routing key `transcription.retry`, incrementando `retry_count`.
2. `whisper_retry_queue` tiene TTL de **5000ms**. Al expirar, el mensaje es redirigido automáticamente (Dead Letter Exchange) de vuelta a `whisper_exchange` → `whisper_transcriptions`.
3. El campo `retry_count` viaja en el header AMQP `x-retry-count` y en el cuerpo del mensaje.
4. Si `retry_count >= 2` (máximo de reintentos alcanzado), se publica directamente un mensaje de error en `whisper_results` y se hace ACK definitivo.

**Configuración de reintentos** en [internal/rabbitmq/producer.go](internal/rabbitmq/producer.go):
- `MaxRetries = 2` → 3 intentos totales
- `RetryTTLMs = 5000` → 5 segundos de espera entre intentos

> Los errores de validación superficial en Go (archivo no encontrado, extensión no soportada) **no** van al sistema de reintentos: publican directamente un error y hacen ACK, ya que son errores determinísticos que no se resolverán con reintentar.

---

## import_batch_id

`import_batch_id` es un campo **opcional** de tipo `int | null` incluido en el código para facilitar la integración con el servicio original que consume estas transcripciones. Su función es **puramente organizativa**: permite agrupar múltiples trabajos de transcripción bajo un mismo número de lote para que el servicio consumidor pueda rastrearlos en conjunto.

El servicio Whisper-Local **no utiliza este campo en ninguna lógica interna**. Lo recibe en el request y lo devuelve sin modificación en el result.

**Si no necesitás esta funcionalidad**, podés eliminar el campo `ImportBatchID` de `TranscriptionRequest` y `TranscriptionResult` en [internal/rabbitmq/types.go](internal/rabbitmq/types.go) sin ningún impacto en el resto del sistema. También podés extenderlo (cambiarlo a `string`, agregar más campos de agrupación, etc.) según las necesidades del servicio que lo consuma.

---

## Componentes Internos

### Go Orchestrator

**[cmd/orchestrator/main.go](cmd/orchestrator/main.go)**  
Punto de entrada. Levanta todos los subsistemas en orden (config → RabbitMQ → ProcessPool → WorkerPool → Consumer) y bloquea hasta recibir `SIGINT` o `SIGTERM`, luego hace shutdown ordenado.

**[internal/config/config.go](internal/config/config.go)**  
Carga toda la configuración desde variables de entorno con valores por defecto. Expone `GetPythonEnv()` que genera el slice de env vars que se inyectan a cada proceso Python al spawnearlos.

**[internal/rabbitmq/connection.go](internal/rabbitmq/connection.go)**  
Conecta a RabbitMQ con reintentos automáticos (hasta 10 intentos, 5s de espera entre cada uno).

**[internal/rabbitmq/consumer.go](internal/rabbitmq/consumer.go)**  
Declara la topología de entrada (exchange + cola + binding). Configura QoS con prefetch igual a `WORKERS_COUNT` para no saturar el pool. Retorna un canal `<-chan Job` que el orchestrator consume en una goroutine.

**[internal/rabbitmq/producer.go](internal/rabbitmq/producer.go)**  
Declara la topología de salida y reintentos. Expone `PublishSuccess`, `PublishError` y `PublishRetry`. La cola de reintentos usa `x-message-ttl`, `x-dead-letter-exchange` y `x-dead-letter-routing-key` para redirigir automáticamente mensajes expirados de vuelta a la cola principal.

**[internal/rabbitmq/types.go](internal/rabbitmq/types.go)**  
Define los cuatro structs de mensajes: `TranscriptionRequest` (entrada RabbitMQ), `TranscriptionResult` (salida RabbitMQ), `PythonWorkerRequest` (enviado a Python por stdin) y `PythonWorkerResponse` (recibido de Python por stdout).

**[internal/validator/file.go](internal/validator/file.go)**  
Validación rápida en Go antes de involucrar un worker Python: verifica existencia del archivo en disco y extensión soportada. Si falla, publica error inmediatamente y libera el worker.

**[internal/worker/pool.go](internal/worker/pool.go)**  
Pool de N goroutines. Cada goroutine toma jobs del canal interno, aplica validación, llama al `ProcessPool` y publica el resultado. Contiene la lógica de reintentos (`handleFailure`).

**[internal/worker/process_pool.go](internal/worker/process_pool.go)**  
Gestiona N procesos Python persistentes. Al arrancar, spawnea los procesos y espera la señal `READY` de cada uno. La comunicación es por **stdin/stdout JSON** (ver protocolo abajo). Si un proceso muere, se respawnea automáticamente al intentar usarlo. Un goroutine de mantenimiento mata procesos que llevan más de `PROCESS_IDLE_TIMEOUT_MIN` minutos sin uso.

---

### Python Workers

**[python/worker.py](python/worker.py)**  
Punto de entrada del worker Python. Al arrancar inicializa `AudioProcessor` y `WhisperService` (carga el modelo en memoria), luego imprime `READY\n` a stdout. Entra en un loop: lee una línea JSON de stdin, procesa, escribe una línea JSON a stdout. Usa `select()` en Linux para detectar idle timeout y salir limpiamente.

**[python/audio_processor.py](python/audio_processor.py)**  
Pipeline de preprocesamiento de audio:
1. Valida tamaño del archivo (≤ `MAX_FILE_SIZE_MB`).
2. Valida extensión soportada.
3. Carga el audio con `pydub` y verifica duración (≤ `MAX_AUDIO_DURATION_SEC`).
4. Convierte a **WAV 16kHz mono** y guarda en `TMP_DIR` con nombre UUID.
5. Limpia los archivos temporales (WAV generado + original) después de la transcripción.

**[python/whisper_service.py](python/whisper_service.py)**  
Singleton de transcripción. El modelo `faster-whisper` se carga **una sola vez por proceso** y se reutiliza en todas las llamadas. Transcribe con `beam_size=5` y `vad_filter=True` (omite silencios con mínimo de 500ms). Devuelve texto completo, duración e idioma detectado.

---

### Protocolo de comunicación Go ↔ Python

La comunicación entre el orchestrator y cada proceso Python es por **líneas JSON sobre stdin/stdout**. Una mensaje = una línea terminada en `\n`. Los logs de Python van a **stderr** para no interferir con el protocolo.

**Handshake al arrancar el proceso:**
```
Go:     spawns python worker.py
Python: imprime → READY\n
Go:     lee "READY" → proceso disponible en el pool
```

**Por cada job:**
```
Go escribe en stdin:
{"audio_file_path": "/tmp/audio.mp3", "language": "es"}\n

Python escribe en stdout (éxito):
{"success": true, "texto": "...", "duration": 12.5, "model": "base"}\n

Python escribe en stdout (error):
{"success": false, "error_message": "..."}\n
```

---

## Configuración — Variables de Entorno

| Variable | Default | Descripción |
|---|---|---|
| `RABBITMQ_URL` | `amqp://guest:guest@localhost:5672/` | URL de conexión a RabbitMQ |
| `WORKERS_COUNT` | `4` | Cantidad de workers concurrentes (goroutines Go = procesos Python) |
| `PROCESS_IDLE_TIMEOUT_MIN` | `5` | Minutos de inactividad antes de cerrar un proceso Python |
| `WHISPER_MODEL` | `base` | Modelo: `tiny`, `base`, `small`, `medium`, `large-v2`, `large-v3` |
| `WHISPER_DEVICE` | `cpu` | Dispositivo de inferencia: `cpu`, `cuda` |
| `WHISPER_COMPUTE_TYPE` | `int8` | Precisión: `int8` (CPU), `float16` (GPU), `float32` |
| `MODELS_DIR` | `./models` | Directorio de caché de modelos Whisper |
| `MAX_FILE_SIZE_MB` | `100` | Tamaño máximo de archivo de audio (MB) |
| `MAX_AUDIO_DURATION_SEC` | `3600` | Duración máxima del audio (segundos) |
| `AUDIO_SAMPLE_RATE` | `16000` | Frecuencia de muestreo target para conversión (Hz) |
| `TMP_DIR` | `/tmp/whisper` | Directorio para archivos WAV temporales |
| `PYTHON_PATH` | `/usr/bin/python3` | Ruta al ejecutable Python |
| `WORKER_SCRIPT` | `/app/python/worker.py` | Ruta al script del worker Python |

---

## Inicio Rápido

### Docker Compose

```bash
docker-compose up -d
```

Levanta RabbitMQ (`localhost:5672`, Management UI en `localhost:15672`) y el servicio Whisper.

### Desarrollo local

```bash
# 1. RabbitMQ
docker run -d -p 5672:5672 -p 15672:15672 \
  -e RABBITMQ_DEFAULT_USER=admin -e RABBITMQ_DEFAULT_PASS=admin \
  rabbitmq:3.12-management

# 2. Dependencias Python
pip install -r python/requirements.txt

# 3. Compilar y ejecutar
go run cmd/orchestrator/main.go
```

### GPU (NVIDIA)

```bash
docker run -d --gpus all \
  -e WHISPER_DEVICE=cuda \
  -e WHISPER_COMPUTE_TYPE=float16 \
  -e WHISPER_MODEL=large-v3 \
  -v whisper_models:/app/models \
  whisper-local
```

---

## Ejemplos de uso

### Publicar un job

```python
import pika, json

conn = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
ch = conn.channel()

ch.basic_publish(
    exchange='whisper_exchange',
    routing_key='transcription.request',
    properties=pika.BasicProperties(delivery_mode=2),  # persistent
    body=json.dumps({
        "attachment_id": 1,
        "audio_file_path": "/tmp/shared_audio/audio.mp3",
        "language": "es",
        "import_batch_id": None  # opcional
    })
)
conn.close()
```

### Consumir resultados

```python
import pika, json

def on_result(ch, method, props, body):
    result = json.loads(body)
    if result["success"]:
        print(f"[{result['attachment_id']}] {result['texto']}")
    else:
        print(f"[{result['attachment_id']}] ERROR: {result['error_message']}")
    ch.basic_ack(delivery_tag=method.delivery_tag)

conn = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
ch = conn.channel()
ch.basic_qos(prefetch_count=1)
ch.basic_consume(queue='whisper_results', on_message_callback=on_result)
ch.start_consuming()
```

---

## Dependencias

**Go:** `github.com/rabbitmq/amqp091-go`, `github.com/joho/godotenv`  
**Python:** `faster-whisper >= 1.0.0`, `pydub >= 0.25.1`  
**Sistema:** `ffmpeg` (requerido por pydub para decodificar formatos de audio)
