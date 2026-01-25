// Package rabbitmq provides types for RabbitMQ message handling.
package rabbitmq

// TranscriptionRequest represents an incoming transcription job from RabbitMQ.
type TranscriptionRequest struct {
	AttachmentID  int    `json:"attachment_id"`
	AudioFilePath string `json:"audio_file_path"`
	Language      string `json:"language,omitempty"`
	RetryCount    int    `json:"retry_count,omitempty"`
}

// TranscriptionResult represents the result sent back to RabbitMQ.
type TranscriptionResult struct {
	AttachmentID int     `json:"attachment_id"`
	Texto        string  `json:"texto"`
	Duration     float64 `json:"duration"`
	Model        string  `json:"model"`
	Success      bool    `json:"success"`
	ErrorMessage string  `json:"error_message,omitempty"`
}

// PythonWorkerRequest is the request sent to Python worker via stdin.
type PythonWorkerRequest struct {
	AudioFilePath string `json:"audio_file_path"`
	Language      string `json:"language,omitempty"`
}

// PythonWorkerResponse is the response received from Python worker via stdout.
type PythonWorkerResponse struct {
	Success      bool    `json:"success"`
	Texto        string  `json:"texto,omitempty"`
	Duration     float64 `json:"duration,omitempty"`
	Model        string  `json:"model,omitempty"`
	ErrorMessage string  `json:"error_message,omitempty"`
}
