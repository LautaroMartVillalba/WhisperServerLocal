"""
Configuration module for WhisperLocal API.
Loads environment variables from .env file.
"""

from pydantic_settings import BaseSettings
from pathlib import Path
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Whisper Configuration
    whisper_model: str
    whisper_device: str
    whisper_compute_type: str
    
    # Audio Configuration
    max_file_size_mb: int
    max_audio_duration_sec: int
    audio_sample_rate: int
    
    # RabbitMQ Configuration
    rabbitmq_url: str
    rabbitmq_queue_name: str
    
    # Server Configuration
    workers_count: int
    tmp_dir: str
    
    # API Configuration
    api_host: str
    api_port: int
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
    
    @property
    def max_file_size_bytes(self) -> int:
        """Convert MB to bytes for file size validation."""
        return self.max_file_size_mb * 1024 * 1024
    
    def ensure_tmp_dir(self):
        """Create tmp directory if it doesn't exist."""
        Path(self.tmp_dir).mkdir(parents=True, exist_ok=True)


# Global settings instance
settings = Settings()
