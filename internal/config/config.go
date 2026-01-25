// Package config handles application configuration from environment variables.
package config

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

// Config holds all application configuration.
type Config struct {
	// RabbitMQ
	RabbitMQURL string

	// Worker Pool
	MaxWorkers         int
	ProcessIdleTimeout time.Duration

	// Python
	PythonPath   string
	WorkerScript string

	// Whisper (passed to Python via env)
	WhisperModel       string
	WhisperDevice      string
	WhisperComputeType string
	ModelsDir          string

	// Audio (passed to Python via env)
	MaxFileSizeMB      int
	MaxAudioDurationSec int
	AudioSampleRate    int
	TmpDir             string
}

// Load reads configuration from environment variables.
func Load() (*Config, error) {
	cfg := &Config{}

	// RabbitMQ
	cfg.RabbitMQURL = getEnv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")

	// Worker Pool
	maxWorkers, err := strconv.Atoi(getEnv("WORKERS_COUNT", "4"))
	if err != nil {
		return nil, fmt.Errorf("invalid WORKERS_COUNT: %w", err)
	}
	cfg.MaxWorkers = maxWorkers

	idleTimeoutMin, err := strconv.Atoi(getEnv("PROCESS_IDLE_TIMEOUT_MIN", "5"))
	if err != nil {
		return nil, fmt.Errorf("invalid PROCESS_IDLE_TIMEOUT_MIN: %w", err)
	}
	cfg.ProcessIdleTimeout = time.Duration(idleTimeoutMin) * time.Minute

	// Python
	cfg.PythonPath = getEnv("PYTHON_PATH", "/usr/bin/python3")
	cfg.WorkerScript = getEnv("WORKER_SCRIPT", "/app/python/worker.py")

	// Whisper
	cfg.WhisperModel = getEnv("WHISPER_MODEL", "base")
	cfg.WhisperDevice = getEnv("WHISPER_DEVICE", "cpu")
	cfg.WhisperComputeType = getEnv("WHISPER_COMPUTE_TYPE", "int8")
	cfg.ModelsDir = getEnv("MODELS_DIR", "./models")

	// Audio
	maxFileSizeMB, err := strconv.Atoi(getEnv("MAX_FILE_SIZE_MB", "100"))
	if err != nil {
		return nil, fmt.Errorf("invalid MAX_FILE_SIZE_MB: %w", err)
	}
	cfg.MaxFileSizeMB = maxFileSizeMB

	maxDuration, err := strconv.Atoi(getEnv("MAX_AUDIO_DURATION_SEC", "3600"))
	if err != nil {
		return nil, fmt.Errorf("invalid MAX_AUDIO_DURATION_SEC: %w", err)
	}
	cfg.MaxAudioDurationSec = maxDuration

	sampleRate, err := strconv.Atoi(getEnv("AUDIO_SAMPLE_RATE", "16000"))
	if err != nil {
		return nil, fmt.Errorf("invalid AUDIO_SAMPLE_RATE: %w", err)
	}
	cfg.AudioSampleRate = sampleRate

	cfg.TmpDir = getEnv("TMP_DIR", "/tmp/whisper")

	return cfg, nil
}

// getEnv returns the value of an environment variable or a default value.
func getEnv(key, defaultValue string) string {
	if value, exists := os.LookupEnv(key); exists {
		return value
	}
	return defaultValue
}

// GetPythonEnv returns environment variables to pass to Python processes.
func (c *Config) GetPythonEnv() []string {
	return []string{
		fmt.Sprintf("WHISPER_MODEL=%s", c.WhisperModel),
		fmt.Sprintf("WHISPER_DEVICE=%s", c.WhisperDevice),
		fmt.Sprintf("WHISPER_COMPUTE_TYPE=%s", c.WhisperComputeType),
		fmt.Sprintf("MODELS_DIR=%s", c.ModelsDir),
		fmt.Sprintf("MAX_FILE_SIZE_MB=%d", c.MaxFileSizeMB),
		fmt.Sprintf("MAX_AUDIO_DURATION_SEC=%d", c.MaxAudioDurationSec),
		fmt.Sprintf("AUDIO_SAMPLE_RATE=%d", c.AudioSampleRate),
		fmt.Sprintf("TMP_DIR=%s", c.TmpDir),
	}
}
