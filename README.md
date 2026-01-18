# WhisperLocal API

API REST para transcripción de audio usando el modelo Whisper de OpenAI. Soporta múltiples formatos de audio y procesamiento concurrente de solicitudes.

## Características

- 🎯 Transcripción de audio a texto usando faster-whisper
- 🌐 API REST con FastAPI
- 🔄 Procesamiento concurrente con control de recursos
- 📦 Soporta múltiples formatos: opus, mp3, wav, m4a, ogg, flac, aac, wma
- 🚀 Conversión automática a 16kHz mono WAV
- ⚡ Detección de voz (VAD) para omitir silencios

## Configuración por Defecto

| Variable | Valor por Defecto | Descripción |
|----------|-------------------|-------------|
| `WHISPER_MODEL` | `base` | Modelo Whisper (tiny, base, small, medium, large) |
| `WHISPER_DEVICE` | `cpu` | Dispositivo de cómputo (cpu, cuda) |
| `WHISPER_COMPUTE_TYPE` | `int8` | Tipo de cómputo (int8, float16, float32) |
| `MAX_FILE_SIZE_MB` | `25` | Tamaño máximo de archivo en MB |
| `MAX_AUDIO_DURATION_SEC` | `300` | Duración máxima del audio en segundos |
| `AUDIO_SAMPLE_RATE` | `16000` | Frecuencia de muestreo (Hz) |
| `WORKERS_COUNT` | `5` | Número de workers concurrentes |
| `API_HOST` | `0.0.0.0` | Host de la API |
| `API_PORT` | `7050` | Puerto de la API |

## Uso Rápido

### Ejemplo 1: Configuración Básica

Ejecución con configuración por defecto (modelo base, CPU, 5 workers):

```bash
docker run -d \
  -p 7050:7050 \
  -v whisper_models:/app/models \
  whisper-local-api
```

Este ejemplo usa el modelo `base` en CPU con int8, ideal para pruebas rápidas con recursos limitados.

### Ejemplo 2: Modelo Avanzado con GPU

Para mejor precisión usando GPU y el modelo medium:

```bash
docker run -d \
  -p 7050:7050 \
  -v whisper_models:/app/models \
  -e WHISPER_MODEL=medium \
  -e WHISPER_DEVICE=cuda \
  -e WHISPER_COMPUTE_TYPE=float16 \
  --gpus all \
  whisper-local-api
```

Modifica: Modelo a `medium`, dispositivo a `cuda`, y tipo de cómputo a `float16` para aprovechar GPU NVIDIA.

### Ejemplo 3: Alta Capacidad de Procesamiento

Para manejar archivos grandes y múltiples solicitudes concurrentes:

```bash
docker run -d \
  -p 7050:7050 \
  -v whisper_models:/app/models \
  -e MAX_FILE_SIZE_MB=100 \
  -e MAX_AUDIO_DURATION_SEC=1800 \
  -e WORKERS_COUNT=10 \
  whisper-local-api
```

Modifica: Tamaño máximo de archivo a 100MB, duración máxima a 30 minutos, y 10 workers concurrentes.

## Endpoints

### `POST /transcribe`

Transcribe un archivo de audio a texto.

**Parámetros:**
- `file` (form-data, requerido): Archivo de audio
- `language` (form-data, opcional): Código de idioma (ej: 'es', 'en')

**Ejemplo:**
```bash
curl -X POST "http://localhost:7050/transcribe" \
  -F "file=@audio.mp3" \
  -F "language=es"
```

### `GET /health`

Verifica el estado del servicio.

```bash
curl http://localhost:7050/health
```

### `GET /model-info`

Obtiene información del modelo cargado.

```bash
curl http://localhost:7050/model-info
```

## Volúmenes

- `/app/models` - Cache de modelos Whisper (recomendado para evitar descargas repetidas)
- `/tmp/whisper_uploads` - Directorio temporal para archivos de audio

## Puertos

- `7050` - API REST

## Notas

- La primera ejecución descargará el modelo Whisper especificado (puede tardar varios minutos)
- El modelo se mantiene en memoria para respuestas rápidas
- Los archivos se procesan de forma concurrente según `WORKERS_COUNT`
- El uso de GPU requiere NVIDIA Container Toolkit instalado
