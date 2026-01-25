// Package rabbitmq provides RabbitMQ connection management.
package rabbitmq

import (
	"fmt"
	"log"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

const (
	// Connection retry settings
	maxRetries    = 10
	retryInterval = 5 * time.Second
)

// Connect establishes a connection to RabbitMQ with retry logic.
func Connect(url string) (*amqp.Connection, error) {
	var conn *amqp.Connection
	var err error

	for i := 0; i < maxRetries; i++ {
		conn, err = amqp.Dial(url)
		if err == nil {
			log.Println("📡 RabbitMQ connected")
			return conn, nil
		}

		if i < maxRetries-1 {
			log.Printf("⚠️  RabbitMQ retry %d/%d in %v...", i+1, maxRetries, retryInterval)
			time.Sleep(retryInterval)
		}
	}

	return nil, fmt.Errorf("failed to connect after %d attempts: %w", maxRetries, err)
}
