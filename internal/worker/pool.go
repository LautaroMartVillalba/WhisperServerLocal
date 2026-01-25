// Package worker provides job processing functionality.
package worker

import (
	"fmt"
	"log"
	"sync"

	"whisper-local/internal/rabbitmq"
	"whisper-local/internal/validator"
)

// Pool manages concurrent job processing using a Python process pool.
type Pool struct {
	processPool *ProcessPool
	producer    *rabbitmq.Producer
	jobs        chan rabbitmq.Job
	wg          sync.WaitGroup
	shutdown    chan struct{}
	numWorkers  int
}

// NewPool creates a new worker pool.
func NewPool(processPool *ProcessPool, producer *rabbitmq.Producer, numWorkers int) *Pool {
	return &Pool{
		processPool: processPool,
		producer:    producer,
		jobs:        make(chan rabbitmq.Job, numWorkers*2),
		shutdown:    make(chan struct{}),
		numWorkers:  numWorkers,
	}
}

// Start begins processing jobs with the configured number of workers.
func (p *Pool) Start() {
	for i := 0; i < p.numWorkers; i++ {
		p.wg.Add(1)
		go p.worker(i)
	}
	log.Printf("👷 %d workers ready", p.numWorkers)
}

// Submit adds a job to the processing queue.
func (p *Pool) Submit(job rabbitmq.Job) {
	p.jobs <- job
}

// worker processes jobs from the queue.
func (p *Pool) worker(id int) {
	defer p.wg.Done()

	for {
		select {
		case <-p.shutdown:
			log.Printf("[Worker-%d] Shutting down", id)
			return
		case job, ok := <-p.jobs:
			if !ok {
				return
			}
			p.processJob(id, job)
		}
	}
}

// processJob handles a single transcription job.
func (p *Pool) processJob(workerID int, job rabbitmq.Job) {
	request := job.Request
	retryInfo := ""
	if request.RetryCount > 0 {
		retryInfo = fmt.Sprintf(" [retry %d]", request.RetryCount)
	}
	log.Printf("[W%d] Job #%d%s", workerID, request.AttachmentID, retryInfo)

	// 1. Validate file exists
	if !validator.FileExists(request.AudioFilePath) {
		err := p.producer.PublishError(
			request.AttachmentID,
			"Audio file not found: "+request.AudioFilePath,
		)
		if err != nil {
			log.Printf("[W%d] ❌ Publish failed: %v", workerID, err)
			job.Delivery.Nack(false, true) // Requeue
			return
		}
		job.Delivery.Ack(false)
		return
	}

	// 2. Validate file extension
	if !validator.ValidateAudioExtension(request.AudioFilePath) {
		err := p.producer.PublishError(
			request.AttachmentID,
			"Unsupported audio format",
		)
		if err != nil {
			log.Printf("[W%d] ❌ Publish failed: %v", workerID, err)
			job.Delivery.Nack(false, true)
			return
		}
		job.Delivery.Ack(false)
		return
	}

	// 3. Execute Python worker
	response, err := p.processPool.Execute(request)

	// 4. Handle execution error
	if err != nil {
		p.handleFailure(workerID, job, err.Error())
		return
	}

	// 5. Handle Python error response
	if !response.Success {
		p.handleFailure(workerID, job, response.ErrorMessage)
		return
	}

	// 6. Success - publish result
	err = p.producer.PublishSuccess(
		request.AttachmentID,
		response.Texto,
		response.Duration,
	)
	if err != nil {
		log.Printf("[W%d] ❌ Publish failed: %v", workerID, err)
		job.Delivery.Nack(false, true)
		return
	}

	job.Delivery.Ack(false)
	log.Printf("[W%d] ✅ #%d done (%.1fs)", workerID, request.AttachmentID, response.Duration)
}

// handleFailure handles a failed job, either retrying or publishing error.
func (p *Pool) handleFailure(workerID int, job rabbitmq.Job, errorMessage string) {
	request := job.Request

	if rabbitmq.ShouldRetry(request.RetryCount) {
		log.Printf("[W%d] 🔄 #%d retry %d/%d",
			workerID, request.AttachmentID, request.RetryCount+1, rabbitmq.MaxRetries)

		err := p.producer.PublishRetry(request)
		if err != nil {
			log.Printf("[W%d] ❌ Retry failed: %v", workerID, err)
			job.Delivery.Nack(false, true)
			return
		}
		job.Delivery.Ack(false)
		return
	}

	// Max retries exceeded
	log.Printf("[W%d] ❌ #%d failed: %s", workerID, request.AttachmentID, errorMessage)

	err := p.producer.PublishError(request.AttachmentID, errorMessage)
	if err != nil {
		log.Printf("[W%d] ❌ Error publish failed: %v", workerID, err)
		job.Delivery.Nack(false, true) // Requeue
		return
	}
	job.Delivery.Ack(false)
}

// Shutdown gracefully stops all workers.
func (p *Pool) Shutdown() {
	close(p.shutdown)
	close(p.jobs)
	p.wg.Wait()
}
