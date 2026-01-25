// Package rabbitmq provides RabbitMQ producer functionality.
package rabbitmq

import (
	"encoding/json"
	"fmt"
	"log"

	amqp "github.com/rabbitmq/amqp091-go"
)

const (
	// Result queue configuration
	ResultsQueue      = "whisper_results"
	ResultsExchange   = "whisper_results_exchange"
	ResultsRoutingKey = "transcription.result"

	// Retry queue configuration
	RetryExchange   = "whisper_retry_exchange"
	RetryRoutingKey = "transcription.retry"
	RetryQueue      = "whisper_retry_queue"
	RetryTTLMs      = 5000 // 5 seconds delay before retry

	// Max retries (2 retries = 3 total attempts)
	MaxRetries = 2
)

// Producer handles publishing messages to RabbitMQ.
type Producer struct {
	conn    *amqp.Connection
	channel *amqp.Channel
	model   string
}

// NewProducer creates a new RabbitMQ producer.
func NewProducer(conn *amqp.Connection, whisperModel string) (*Producer, error) {
	channel, err := conn.Channel()
	if err != nil {
		return nil, fmt.Errorf("failed to open channel: %w", err)
	}

	// Declare topology
	if err := declareProducerTopology(channel); err != nil {
		channel.Close()
		return nil, err
	}

	log.Printf("[Producer] Connected and ready")

	return &Producer{
		conn:    conn,
		channel: channel,
		model:   whisperModel,
	}, nil
}

// declareProducerTopology declares exchanges and queues for producing.
func declareProducerTopology(ch *amqp.Channel) error {
	// === Results topology ===

	// Declare results exchange
	if err := ch.ExchangeDeclare(
		ResultsExchange, // name
		"direct",        // type
		true,            // durable
		false,           // auto-deleted
		false,           // internal
		false,           // no-wait
		nil,             // arguments
	); err != nil {
		return fmt.Errorf("failed to declare results exchange: %w", err)
	}

	// Declare results queue
	if _, err := ch.QueueDeclare(
		ResultsQueue, // name
		true,         // durable
		false,        // delete when unused
		false,        // exclusive
		false,        // no-wait
		nil,          // arguments
	); err != nil {
		return fmt.Errorf("failed to declare results queue: %w", err)
	}

	// Bind results queue
	if err := ch.QueueBind(
		ResultsQueue,      // queue name
		ResultsRoutingKey, // routing key
		ResultsExchange,   // exchange
		false,             // no-wait
		nil,               // arguments
	); err != nil {
		return fmt.Errorf("failed to bind results queue: %w", err)
	}

	// === Retry topology ===

	// Declare retry exchange
	if err := ch.ExchangeDeclare(
		RetryExchange, // name
		"direct",      // type
		true,          // durable
		false,         // auto-deleted
		false,         // internal
		false,         // no-wait
		nil,           // arguments
	); err != nil {
		return fmt.Errorf("failed to declare retry exchange: %w", err)
	}

	// Declare retry queue with TTL and DLX back to main queue
	if _, err := ch.QueueDeclare(
		RetryQueue, // name
		true,       // durable
		false,      // delete when unused
		false,      // exclusive
		false,      // no-wait
		amqp.Table{
			"x-message-ttl":             int32(RetryTTLMs),
			"x-dead-letter-exchange":    MainExchange,
			"x-dead-letter-routing-key": MainRoutingKey,
		},
	); err != nil {
		return fmt.Errorf("failed to declare retry queue: %w", err)
	}

	// Bind retry queue
	if err := ch.QueueBind(
		RetryQueue,      // queue name
		RetryRoutingKey, // routing key
		RetryExchange,   // exchange
		false,           // no-wait
		nil,             // arguments
	); err != nil {
		return fmt.Errorf("failed to bind retry queue: %w", err)
	}

	return nil
}

// PublishResult publishes a transcription result to the results queue.
func (p *Producer) PublishResult(result TranscriptionResult) error {
	body, err := json.Marshal(result)
	if err != nil {
		return fmt.Errorf("failed to marshal result: %w", err)
	}

	err = p.channel.Publish(
		ResultsExchange,   // exchange
		ResultsRoutingKey, // routing key
		false,             // mandatory
		false,             // immediate
		amqp.Publishing{
			ContentType:  "application/json",
			DeliveryMode: amqp.Persistent,
			Body:         body,
		},
	)
	if err != nil {
		return fmt.Errorf("failed to publish result: %w", err)
	}

	return nil
}

// PublishRetry publishes a message to the retry queue.
func (p *Producer) PublishRetry(request TranscriptionRequest) error {
	// Increment retry count
	request.RetryCount++

	body, err := json.Marshal(request)
	if err != nil {
		return fmt.Errorf("failed to marshal retry request: %w", err)
	}

	err = p.channel.Publish(
		RetryExchange,   // exchange
		RetryRoutingKey, // routing key
		false,           // mandatory
		false,           // immediate
		amqp.Publishing{
			ContentType:  "application/json",
			DeliveryMode: amqp.Persistent,
			Headers: amqp.Table{
				"x-retry-count": int32(request.RetryCount),
			},
			Body: body,
		},
	)
	if err != nil {
		return fmt.Errorf("failed to publish retry: %w", err)
	}

	return nil
}

// PublishError publishes an error result when max retries exceeded.
func (p *Producer) PublishError(attachmentID int, errorMessage string) error {
	result := TranscriptionResult{
		AttachmentID: attachmentID,
		Texto:        "",
		Duration:     0,
		Model:        p.model,
		Success:      false,
		ErrorMessage: errorMessage,
	}
	return p.PublishResult(result)
}

// PublishSuccess publishes a successful transcription result.
func (p *Producer) PublishSuccess(attachmentID int, texto string, duration float64) error {
	result := TranscriptionResult{
		AttachmentID: attachmentID,
		Texto:        texto,
		Duration:     duration,
		Model:        p.model,
		Success:      true,
	}
	return p.PublishResult(result)
}

// ShouldRetry checks if a request should be retried based on retry count.
func ShouldRetry(retryCount int) bool {
	return retryCount < MaxRetries
}

// Close closes the producer channel.
func (p *Producer) Close() error {
	if p.channel != nil {
		return p.channel.Close()
	}
	return nil
}
