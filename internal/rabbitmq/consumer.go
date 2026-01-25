// Package rabbitmq provides RabbitMQ consumer functionality.
package rabbitmq

import (
	"encoding/json"
	"fmt"
	"log"

	amqp "github.com/rabbitmq/amqp091-go"
)

const (
	// Queue names
	MainQueue   = "whisper_transcriptions"
	MainExchange = "whisper_exchange"
	MainRoutingKey = "transcription.request"
)

// Consumer handles consuming messages from RabbitMQ.
type Consumer struct {
	conn    *amqp.Connection
	channel *amqp.Channel
	queue   string
}

// Job represents a transcription job with its delivery for ACK/NACK.
type Job struct {
	Request  TranscriptionRequest
	Delivery amqp.Delivery
}

// NewConsumer creates a new RabbitMQ consumer.
func NewConsumer(conn *amqp.Connection, prefetchCount int) (*Consumer, error) {
	channel, err := conn.Channel()
	if err != nil {
		return nil, fmt.Errorf("failed to open channel: %w", err)
	}

	// Declare topology
	if err := declareConsumerTopology(channel); err != nil {
		channel.Close()
		return nil, err
	}

	// Set QoS - prefetch count equals number of workers
	if err := channel.Qos(prefetchCount, 0, false); err != nil {
		channel.Close()
		return nil, fmt.Errorf("failed to set QoS: %w", err)
	}

	return &Consumer{
		conn:    conn,
		channel: channel,
		queue:   MainQueue,
	}, nil
}

// declareConsumerTopology declares exchanges and queues for consuming.
func declareConsumerTopology(ch *amqp.Channel) error {
	// Declare main exchange
	if err := ch.ExchangeDeclare(
		MainExchange, // name
		"direct",     // type
		true,         // durable
		false,        // auto-deleted
		false,        // internal
		false,        // no-wait
		nil,          // arguments
	); err != nil {
		return fmt.Errorf("failed to declare exchange: %w", err)
	}

	// Declare main queue
	if _, err := ch.QueueDeclare(
		MainQueue, // name
		true,      // durable
		false,     // delete when unused
		false,     // exclusive
		false,     // no-wait
		nil,       // arguments
	); err != nil {
		return fmt.Errorf("failed to declare queue: %w", err)
	}

	// Bind queue to exchange
	if err := ch.QueueBind(
		MainQueue,       // queue name
		MainRoutingKey,  // routing key
		MainExchange,    // exchange
		false,           // no-wait
		nil,             // arguments
	); err != nil {
		return fmt.Errorf("failed to bind queue: %w", err)
	}

	return nil
}

// Consume starts consuming messages and returns a channel of Jobs.
func (c *Consumer) Consume() (<-chan Job, error) {
	msgs, err := c.channel.Consume(
		c.queue,          // queue
		"go-orchestrator", // consumer tag
		false,            // auto-ack (we'll manually ACK)
		false,            // exclusive
		false,            // no-local
		false,            // no-wait
		nil,              // args
	)
	if err != nil {
		return nil, fmt.Errorf("failed to start consuming: %w", err)
	}

	jobs := make(chan Job)

	go func() {
		defer close(jobs)

		for msg := range msgs {
			var request TranscriptionRequest

			if err := json.Unmarshal(msg.Body, &request); err != nil {
				log.Printf("⚠️  Invalid message: %v", err)
				msg.Nack(false, false)
				continue
			}

			// Extract retry count from header if present
			if retryCount, ok := msg.Headers["x-retry-count"].(int32); ok {
				request.RetryCount = int(retryCount)
			} else if retryCount, ok := msg.Headers["x-retry-count"].(int64); ok {
				request.RetryCount = int(retryCount)
			}

			jobs <- Job{
				Request:  request,
				Delivery: msg,
			}
		}
	}()

	log.Printf("[Consumer] Started consuming from queue: %s", c.queue)
	return jobs, nil
}

// Close closes the consumer channel.
func (c *Consumer) Close() error {
	if c.channel != nil {
		return c.channel.Close()
	}
	return nil
}
