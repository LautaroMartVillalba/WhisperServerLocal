// Whisper-Local Orchestrator
// Main entry point for the Go orchestrator that manages Python transcription workers.
package main

import (
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/joho/godotenv"

	"whisper-local/internal/config"
	"whisper-local/internal/rabbitmq"
	"whisper-local/internal/worker"
)

func main() {
	log.SetFlags(log.Ltime | log.Lmsgprefix)
	log.Println("🚀 Whisper-Local starting...")

	godotenv.Load() // Ignore error, ENV vars take precedence

	// Load configuration
	cfg, err := config.Load()
	if err != nil {
		log.Fatalf("❌ Config error: %v", err)
	}
	log.Printf("⚙️  Config: %d workers, model=%s (%s)",
		cfg.MaxWorkers, cfg.WhisperModel, cfg.WhisperDevice)

	// Connect to RabbitMQ
	conn, err := rabbitmq.Connect(cfg.RabbitMQURL)
	if err != nil {
		log.Fatalf("❌ RabbitMQ: %v", err)
	}
	defer conn.Close()

	// Create consumer and producer
	consumer, err := rabbitmq.NewConsumer(conn, cfg.MaxWorkers)
	if err != nil {
		log.Fatalf("❌ Consumer: %v", err)
	}
	defer consumer.Close()

	producer, err := rabbitmq.NewProducer(conn, cfg.WhisperModel)
	if err != nil {
		log.Fatalf("❌ Producer: %v", err)
	}
	defer producer.Close()

	// Initialize Python workers
	processPool, err := worker.NewProcessPool(cfg)
	if err != nil {
		log.Fatalf("❌ Python pool: %v", err)
	}
	defer processPool.Shutdown()

	// Start worker pool
	workerPool := worker.NewPool(processPool, producer, cfg.MaxWorkers)
	workerPool.Start()
	defer workerPool.Shutdown()

	// Start consuming
	jobs, err := consumer.Consume()
	if err != nil {
		log.Fatalf("❌ Consume: %v", err)
	}

	// Setup graceful shutdown
	shutdown := make(chan os.Signal, 1)
	signal.Notify(shutdown, syscall.SIGINT, syscall.SIGTERM)

	log.Println("✅ Ready, waiting for jobs...")

	// Main loop
	go func() {
		for job := range jobs {
			workerPool.Submit(job)
		}
	}()

	// Wait for shutdown signal
	<-shutdown
	log.Println("\n🛑 Shutting down...")
}
