FROM golang:1.21-bookworm AS go-builder

WORKDIR /build

# Copy Go module files
COPY go.mod go.sum* ./

# Download dependencies (cached if go.mod unchanged)
RUN go mod download

# Copy source code
COPY cmd/ ./cmd/
COPY internal/ ./internal/

# Build the orchestrator binary
RUN CGO_ENABLED=0 GOOS=linux go build -o /orchestrator ./cmd/orchestrator

# -----------------------------------------------------------------------------
# Stage 2: Final image with Python + Go binary
# -----------------------------------------------------------------------------
FROM python:3.11-slim-bookworm

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libavformat-dev \
    libavcodec-dev \
    libavutil-dev \
    libswresample-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy Go binary from builder stage
COPY --from=go-builder /orchestrator /usr/local/bin/orchestrator

# Copy Python worker code
COPY python/ ./python/

# Install Python dependencies
RUN pip install --no-cache-dir -r python/requirements.txt

# Create necessary directories
RUN mkdir -p /tmp/whisper /app/models \
    && chmod 777 /tmp/whisper

# Environment variables (defaults, override in docker-compose)
ENV PYTHONUNBUFFERED=1 \
    PYTHON_PATH=/usr/local/bin/python3 \
    WORKER_SCRIPT=/app/python/worker.py \
    WHISPER_MODEL=base \
    WHISPER_DEVICE=cpu \
    WHISPER_COMPUTE_TYPE=int8 \
    MODELS_DIR=/app/models \
    MAX_FILE_SIZE_MB=100 \
    MAX_AUDIO_DURATION_SEC=3600 \
    AUDIO_SAMPLE_RATE=16000 \
    TMP_DIR=/tmp/whisper \
    WORKERS_COUNT=4 \
    PROCESS_IDLE_TIMEOUT_MIN=5 \
    RABBITMQ_URL=amqp://guest:guest@localhost:5672/

# Run the Go orchestrator
CMD ["orchestrator"]
