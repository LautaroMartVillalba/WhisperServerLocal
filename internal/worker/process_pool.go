// Package worker provides Python process pool management.
package worker

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"

	"whisper-local/internal/config"
	"whisper-local/internal/rabbitmq"
)

// PythonProcess represents a persistent Python worker process.
type PythonProcess struct {
	id       int
	cmd      *exec.Cmd
	stdin    io.WriteCloser
	stdout   *bufio.Reader
	stderr   io.ReadCloser
	mu       sync.Mutex
	busy     bool
	alive    bool
	lastUsed time.Time
}

// ProcessPool manages a pool of Python worker processes.
type ProcessPool struct {
	processes   []*PythonProcess
	maxWorkers  int
	idleTimeout time.Duration
	pythonPath  string
	workerScript string
	pythonEnv   []string
	mu          sync.Mutex
	shutdown    chan struct{}
	wg          sync.WaitGroup
}

// NewProcessPool creates a new pool of Python worker processes.
func NewProcessPool(cfg *config.Config) (*ProcessPool, error) {
	pool := &ProcessPool{
		maxWorkers:   cfg.MaxWorkers,
		idleTimeout:  cfg.ProcessIdleTimeout,
		pythonPath:   cfg.PythonPath,
		workerScript: cfg.WorkerScript,
		pythonEnv:    cfg.GetPythonEnv(),
		shutdown:     make(chan struct{}),
	}

	// Spawn initial processes
	for i := 0; i < pool.maxWorkers; i++ {
		proc, err := pool.spawnProcess(i)
		if err != nil {
			// Cleanup already spawned processes
			pool.Shutdown()
			return nil, fmt.Errorf("failed to spawn process %d: %w", i, err)
		}
		pool.processes = append(pool.processes, proc)
	}

	// Start idle cleanup goroutine
	go pool.idleCleanupLoop()

	log.Printf("🐍 %d Python workers loaded", pool.maxWorkers)
	return pool, nil
}

// spawnProcess creates and starts a new Python worker process.
func (p *ProcessPool) spawnProcess(id int) (*PythonProcess, error) {
	cmd := exec.Command(p.pythonPath, p.workerScript)
	
	// Set environment variables for Python
	cmd.Env = append(os.Environ(), p.pythonEnv...)

	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, fmt.Errorf("failed to create stdin pipe: %w", err)
	}

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("failed to create stdout pipe: %w", err)
	}

	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, fmt.Errorf("failed to create stderr pipe: %w", err)
	}

	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("failed to start process: %w", err)
	}

	proc := &PythonProcess{
		id:       id,
		cmd:      cmd,
		stdin:    stdin,
		stdout:   bufio.NewReader(stdout),
		stderr:   stderr,
		alive:    true,
		lastUsed: time.Now(),
	}

	// Start stderr logger
	go p.logStderr(proc)

	// Wait for "READY" signal from Python
	readyLine, err := proc.stdout.ReadString('\n')
	if err != nil {
		cmd.Process.Kill()
		return nil, fmt.Errorf("failed to read ready signal: %w", err)
	}

	if strings.TrimSpace(readyLine) != "READY" {
		cmd.Process.Kill()
		return nil, fmt.Errorf("unexpected ready signal: %s", readyLine)
	}

	return proc, nil
}

// logStderr reads and logs stderr from a Python process.
func (p *ProcessPool) logStderr(proc *PythonProcess) {
	reader := bufio.NewReader(proc.stderr)
	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			return
		}
		log.Printf("[Py%d] %s", proc.id, strings.TrimSpace(line))
	}
}

// Execute sends a request to an available worker and returns the response.
func (p *ProcessPool) Execute(request rabbitmq.TranscriptionRequest) (*rabbitmq.PythonWorkerResponse, error) {
	proc, err := p.acquireProcess()
	if err != nil {
		return nil, fmt.Errorf("failed to acquire process: %w", err)
	}
	defer p.releaseProcess(proc)

	// Build Python request
	pyRequest := rabbitmq.PythonWorkerRequest{
		AudioFilePath: request.AudioFilePath,
		Language:      request.Language,
	}

	// Send request JSON + newline
	requestJSON, err := json.Marshal(pyRequest)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	_, err = fmt.Fprintf(proc.stdin, "%s\n", requestJSON)
	if err != nil {
		// Process may be dead, mark for respawn
		proc.alive = false
		return nil, fmt.Errorf("failed to write to process: %w", err)
	}

	// Read response line
	responseLine, err := proc.stdout.ReadString('\n')
	if err != nil {
		proc.alive = false
		return nil, fmt.Errorf("failed to read from process: %w", err)
	}

	// Parse response
	var response rabbitmq.PythonWorkerResponse
	if err := json.Unmarshal([]byte(responseLine), &response); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w, raw: %s", err, responseLine)
	}

	proc.lastUsed = time.Now()
	return &response, nil
}

// acquireProcess gets an available process from the pool.
func (p *ProcessPool) acquireProcess() (*PythonProcess, error) {
	p.mu.Lock()
	defer p.mu.Unlock()

	// Try to find an available process
	for _, proc := range p.processes {
		proc.mu.Lock()
		if !proc.busy && proc.alive {
			proc.busy = true
			proc.mu.Unlock()
			return proc, nil
		}
		proc.mu.Unlock()
	}

	// Try to respawn dead processes
	for i, proc := range p.processes {
		proc.mu.Lock()
		if !proc.alive {
			proc.mu.Unlock()
			
			log.Printf("🔄 Respawning Py%d", proc.id)
			newProc, err := p.spawnProcess(i)
			if err != nil {
				log.Printf("[Pool] Failed to respawn worker %d: %v", i, err)
				continue
			}
			
			newProc.busy = true
			p.processes[i] = newProc
			return newProc, nil
		}
		proc.mu.Unlock()
	}

	return nil, fmt.Errorf("no available workers")
}

// releaseProcess marks a process as available.
func (p *ProcessPool) releaseProcess(proc *PythonProcess) {
	proc.mu.Lock()
	proc.busy = false
	proc.mu.Unlock()
}

// idleCleanupLoop periodically checks for and kills idle processes.
func (p *ProcessPool) idleCleanupLoop() {
	ticker := time.NewTicker(1 * time.Minute)
	defer ticker.Stop()

	for {
		select {
		case <-p.shutdown:
			return
		case <-ticker.C:
			p.cleanupIdleProcesses()
		}
	}
}

// cleanupIdleProcesses kills processes that have been idle too long.
func (p *ProcessPool) cleanupIdleProcesses() {
	p.mu.Lock()
	defer p.mu.Unlock()

	for _, proc := range p.processes {
		proc.mu.Lock()
		if !proc.busy && proc.alive && time.Since(proc.lastUsed) > p.idleTimeout {
			log.Printf("💤 Killing idle Py%d", proc.id)
			proc.cmd.Process.Kill()
			proc.alive = false
		}
		proc.mu.Unlock()
	}
}

// Shutdown gracefully shuts down all Python processes.
func (p *ProcessPool) Shutdown() {
	close(p.shutdown)

	p.mu.Lock()
	defer p.mu.Unlock()

	for _, proc := range p.processes {
		if proc != nil && proc.cmd != nil && proc.cmd.Process != nil {
			proc.stdin.Close()
			proc.cmd.Process.Kill()
			proc.cmd.Wait()
		}
	}
}

// Stats returns pool statistics.
func (p *ProcessPool) Stats() map[string]interface{} {
	p.mu.Lock()
	defer p.mu.Unlock()

	alive := 0
	busy := 0
	for _, proc := range p.processes {
		proc.mu.Lock()
		if proc.alive {
			alive++
		}
		if proc.busy {
			busy++
		}
		proc.mu.Unlock()
	}

	return map[string]interface{}{
		"total":  len(p.processes),
		"alive":  alive,
		"busy":   busy,
		"idle":   alive - busy,
	}
}
