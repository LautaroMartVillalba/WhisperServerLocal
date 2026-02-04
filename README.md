# Whisper-Local

Servicio de transcripción de audio usando Whisper con arquitectura híbrida Go + Python y comunicación vía RabbitMQ.

## 🏗️ Arquitectura

```
RabbitMQ → Go Orchestrator → Pool de Procesos Python → Whisper (faster-whisper)
```

- **Go Orchestrator**: Maneja concurrencia, mensajería RabbitMQ y validación de archivos
- **Procesos Python**: Ejecutan procesamiento de audio y transcripción ML (modelo cargado una vez en memoria)
- **Comunicación**: Go ↔ Python via stdin/stdout JSON, servicios externos via RabbitMQ

## ✨ Características

- 🎯 Transcripción usando faster-whisper (optimizado)
- 🔄 Procesamiento concurrente con pool de workers
- 📦 Formatos soportados: opus, mp3, wav, m4a, ogg, flac, aac, wma
- 🚀 Conversión automática a 16kHz mono WAV
- ⚡ Detección de voz (VAD) para omitir silencios
- 🔁 Sistema de reintentos automáticos (máx 2 reintentos con delay de 5s)
- 🐳 Contenerizado con Docker

## 🚀 Inicio Rápido

### Con Docker Compose

```bash
docker-compose up -d
```

Esto inicia:
- RabbitMQ en `localhost:5672` (Management UI en `localhost:15672`)
- Whisper service consumiendo de la cola

### Solo el servicio Whisper

```bash
docker build -t whisper-local .
docker run -d \
  -e RABBITMQ_URL=amqp://admin:admin@rabbitmq:5672/ \
  -e WORKERS_COUNT=4 \
  -e WHISPER_MODEL=base \
  -v whisper_models:/app/models \
  whisper-local
```

## 📨 Formato de Mensajes

### 📥 Mensaje de Entrada (Request)

**Cola**: `whisper_transcriptions`  
**Exchange**: `whisper_exchange`  
**Routing Key**: `transcription.request`

```json
{
  "attachment_id": 12345,
  "audio_file_path": "/tmp/shared_audio/audio.mp3",
  "language": "es"
}
```

| Campo | Tipo | Requerido | Descripción |
|-------|------|-----------|-------------|
| `attachment_id` | int | ✅ | ID único del archivo de audio |
| `audio_file_path` | string | ✅ | Ruta absoluta al archivo de audio |
| `language` | string | ❌ | Código ISO 639-1 (ej: 'es', 'en'). Si se omite, se detecta automáticamente |

**⚙️ Modificar formato**: Editar `TranscriptionRequest` en [`internal/rabbitmq/types.go`](internal/rabbitmq/types.go)

---

### 📤 Mensaje de Salida (Result)

**Cola**: `whisper_results`  
**Exchange**: `whisper_results_exchange`  
**Routing Key**: `transcription.result`

#### ✅ Respuesta Exitosa

```json
{
  "attachment_id": 12345,
  "texto": "Esta es la transcripción del audio.",
  "duration": 45.3,
  "model": "base",
  "success": true
}
```

#### ❌ Respuesta con Error

```json
{
  "attachment_id": 12345,
  "texto": "",
  "duration": 0,
  "model": "base",
  "success": false,
  "error_message": "Audio file not found: /tmp/audio.mp3"
}
```

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `attachment_id` | int | ID del archivo procesado |
| `texto` | string | Texto transcrito (vacío si hay error) |
| `duration` | float | Duración del audio en segundos |
| `model` | string | Modelo Whisper usado (ej: 'base', 'medium') |
| `success` | bool | `true` si transcripción exitosa, `false` si hubo error |
| `error_message` | string | Mensaje de error (solo presente si `success: false`) |

**⚙️ Modificar formato**: Editar `TranscriptionResult` en [`internal/rabbitmq/types.go`](internal/rabbitmq/types.go)

---

### 🔁 Sistema de Reintentos

Si falla una transcripción, el mensaje se reenvía a:

**Cola**: `whisper_retry_queue` (con TTL de 5 segundos)  
**Exchange**: `whisper_retry_exchange`  
**Routing Key**: `transcription.retry`

Después del TTL, el mensaje vuelve a la cola principal. Máximo **2 reintentos** (3 intentos totales).

**⚙️ Modificar reintentos**: Editar constantes en [`internal/rabbitmq/producer.go`](internal/rabbitmq/producer.go):
- `RetryTTLMs`: Delay entre reintentos (5000 = 5 segundos)
- `MaxRetries`: Número máximo de reintentos (2 = 3 intentos totales)

## ⚙️ Configuración

### Variables de Entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| **RabbitMQ** |||
| `RABBITMQ_URL` | `amqp://guest:guest@localhost:5672/` | URL de conexión |
| **Workers** |||
| `WORKERS_COUNT` | `4` | Cantidad de workers concurrentes |
| `PROCESS_IDLE_TIMEOUT_MIN` | `5` | Minutos de inactividad antes de cerrar proceso Python |
| **Whisper** |||
| `WHISPER_MODEL` | `base` | Modelo: tiny, base, small, medium, large |
| `WHISPER_DEVICE` | `cpu` | Dispositivo: cpu, cuda |
| `WHISPER_COMPUTE_TYPE` | `int8` | Precisión: int8, float16, float32 |
| `MODELS_DIR` | `./models` | Directorio para cache de modelos |
| **Audio** |||
| `MAX_FILE_SIZE_MB` | `100` | Tamaño máximo de archivo |
| `MAX_AUDIO_DURATION_SEC` | `3600` | Duración máxima (segundos) |
| `AUDIO_SAMPLE_RATE` | `16000` | Frecuencia de muestreo (Hz) |
| `TMP_DIR` | `/tmp/whisper` | Directorio temporal |
| **Python** |||
| `PYTHON_PATH` | `/usr/bin/python3` | Ruta al ejecutable Python |
| `WORKER_SCRIPT` | `/app/python/worker.py` | Script del worker Python |

**⚙️ Modificar configuración**: Ver [`internal/config/config.go`](internal/config/config.go)

## 📁 Estructura del Proyecto

```
whisper-local/
├── cmd/orchestrator/          # Punto de entrada Go
│   └── main.go
├── internal/
│   ├── config/                # Configuración desde env vars
│   ├── rabbitmq/              # Cliente RabbitMQ (consumer, producer, types)
│   ├── validator/             # Validación de archivos
│   └── worker/                # Pool de workers y procesos Python
├── python/
│   ├── worker.py              # Worker Python (punto de entrada)
│   ├── audio_processor.py     # Validación y conversión de audio
│   ├── whisper_service.py     # Servicio de transcripción
│   └── requirements.txt
├── docker-compose.yml
├── Dockerfile
└── go.mod
```

## 🔧 Desarrollo

### Requisitos Locales

- Go 1.21+
- Python 3.11+
- RabbitMQ
- ffmpeg

### Ejecutar localmente

```bash
# 1. Iniciar RabbitMQ
docker run -d -p 5672:5672 -p 15672:15672 rabbitmq:3.12-management

# 2. Instalar dependencias Python
pip install -r python/requirements.txt

# 3. Compilar y ejecutar Go
go run cmd/orchestrator/main.go
```

### Publicar mensaje de prueba

```python
import pika
import json

connection = pika.BlockingConnection(
    pika.ConnectionParameters('localhost')
)
channel = connection.channel()

message = {
    "attachment_id": 1,
    "audio_file_path": "/path/to/audio.mp3",
    "language": "es"
}

channel.basic_publish(
    exchange='whisper_exchange',
    routing_key='transcription.request',
    body=json.dumps(message)
)

print("Mensaje enviado!")
connection.close()
```

### Consumir resultados

```python
import pika

def callback(ch, method, properties, body):
    print(f"Resultado: {body.decode()}")
    ch.basic_ack(delivery_tag=method.delivery_tag)

connection = pika.BlockingConnection(
    pika.ConnectionParameters('localhost')
)
channel = connection.channel()

channel.basic_consume(
    queue='whisper_results',
    on_message_callback=callback
)

print("Esperando resultados...")
channel.start_consuming()
```

## 🐳 Docker

### Build

```bash
docker build -t whisper-local .
```

### Usar con GPU (NVIDIA)

```bash
docker run -d \
  --gpus all \
  -e WHISPER_DEVICE=cuda \
  -e WHISPER_COMPUTE_TYPE=float16 \
  -e WHISPER_MODEL=medium \
  -v whisper_models:/app/models \
  whisper-local
```

## 📊 Topología de Colas RabbitMQ

```
whisper_exchange (direct)
  └─ [transcription.request] → whisper_transcriptions (queue)
                                      ↓
                                  Orchestrator
                                      ↓
                              ┌───────┴───────┐
                              ↓               ↓
                         [Success]        [Failure]
                              ↓               ↓
whisper_results_exchange  ←──┘      whisper_retry_exchange
  └─ whisper_results (queue)          └─ whisper_retry_queue (TTL: 5s)
                                                ↓
                                    (vuelve a whisper_transcriptions)
```

## 📝 Notas

- Los procesos Python son **persistentes**: el modelo se carga una vez al inicio
- **Graceful shutdown**: Maneja señales SIGINT/SIGTERM correctamente
- Los archivos temporales se limpian automáticamente después de la transcripción
- Validación en **dos capas**: Go valida existencia/extensión, Python valida formato/tamaño/duración

## 📄 Licencia

Este proyecto es de código abierto.
