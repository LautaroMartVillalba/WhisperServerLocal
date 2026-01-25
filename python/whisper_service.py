"""
Whisper Service - Handles model loading and audio transcription.

Standalone module without external app dependencies.
Configuration loaded from environment variables.
The model is loaded once when the service starts and kept in memory.
"""
import os
import logging
from pathlib import Path
from typing import Optional

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)

# Configuration from environment variables
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
MODELS_DIR = os.getenv("MODELS_DIR", "./models")

# Global model instance (singleton)
_model: Optional[WhisperModel] = None


class WhisperService:
    """
    Service for audio transcription using faster-whisper.
    Implements singleton pattern to load model only once per process.
    """
    
    def __init__(self):
        """Initialize the service and load model if not already loaded."""
        global _model
        if _model is None:
            self._load_model()
        self.model = _model
    
    def _load_model(self):
        """
        Load the Whisper model into memory.
        Called once when the service is first instantiated.
        """
        global _model
        
        try:
            logger.info(f"Loading {WHISPER_MODEL} on {WHISPER_DEVICE}...")
            
            _model = WhisperModel(
                WHISPER_MODEL,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
                download_root=MODELS_DIR
            )
            
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            raise RuntimeError(f"Could not initialize Whisper model: {str(e)}")
    
    def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        task: str = "transcribe"
    ) -> dict:
        """
        Transcribe an audio file to text.
        
        Args:
            audio_path: Path to the audio file (should be preprocessed to 16kHz WAV)
            language: Optional language code (e.g., 'es', 'en'). If None, auto-detect
            task: Either 'transcribe' or 'translate' (to English)
        
        Returns:
            Dictionary containing:
                - text: Full transcription
                - duration: Audio duration in seconds
                - model: Model name used for transcription
        
        Raises:
            FileNotFoundError: If audio file doesn't exist
            RuntimeError: If transcription fails
        """
        if not Path(audio_path).exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        if self.model is None:
            raise RuntimeError("Whisper model not loaded")
        
        try:
            # Transcribe with faster-whisper
            segments, info = self.model.transcribe(
                audio_path,
                language=language,
                task=task,
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=500
                )
            )
            
            # Concatenate all segments
            full_text = " ".join(segment.text.strip() for segment in segments)
            
            # Clean up text (remove extra spaces)
            full_text = " ".join(full_text.split())
            
            return {
                "text": full_text,
                "duration": info.duration,
                "model": WHISPER_MODEL,
                "language": info.language,
                "language_probability": info.language_probability
            }
            
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            raise RuntimeError(f"Transcription failed: {str(e)}")
    
    def get_model_info(self) -> dict:
        """
        Get information about the loaded model.
        
        Returns:
            Dictionary with model configuration
        """
        return {
            "model": WHISPER_MODEL,
            "device": WHISPER_DEVICE,
            "compute_type": WHISPER_COMPUTE_TYPE,
            "models_dir": MODELS_DIR,
            "loaded": self.model is not None
        }
