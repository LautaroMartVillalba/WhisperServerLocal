"""
Whisper Service - Handles model loading and audio transcription.
The model is loaded once when the service starts and kept in memory.
"""

from faster_whisper import WhisperModel
from typing import Optional, Dict, List
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


class WhisperService:
    """
    Service for audio transcription using faster-whisper.
    Implements singleton pattern to load model only once.
    """
    
    _instance: Optional['WhisperService'] = None
    _model: Optional[WhisperModel] = None
    
    def __new__(cls):
        """Ensure only one instance exists (Singleton pattern)."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize the service but don't load model yet."""
        if self._model is None:
            self._load_model()
    
    def _load_model(self):
        """
        Load the Whisper model into memory.
        This is called once when the service is first instantiated.
        """
        try:
            logger.info(f"Loading Whisper model: {settings.whisper_model}")
            logger.info(f"Device: {settings.whisper_device}, Compute type: {settings.whisper_compute_type}")
            
            self._model = WhisperModel(
                settings.whisper_model,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
                download_root="./models"  # Store models in local directory
            )
            
            logger.info("Whisper model loaded successfully")
            
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {str(e)}")
            raise RuntimeError(f"Could not initialize Whisper model: {str(e)}")
    
    def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        task: str = "transcribe"
    ) -> Dict[str, any]:
        """
        Transcribe an audio file to text.
        
        Args:
            audio_path: Path to the audio file (should be preprocessed to 16kHz)
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
        
        if self._model is None:
            raise RuntimeError("Whisper model not loaded")
        
        try:
            logger.info(f"Starting transcription: {audio_path}")
            
            # Transcribe with faster-whisper
            segments, info = self._model.transcribe(
                audio_path,
                language=language,
                task=task,
                beam_size=5,  # Balance between speed and accuracy
                vad_filter=True,  # Voice Activity Detection to skip silence
                vad_parameters=dict(
                    min_silence_duration_ms=500  # Skip silence > 500ms
                )
            )
            
            # Process segments to get full text
            full_text = []
            
            for segment in segments:
                full_text.append(segment.text.strip())
            
            result = {
                "text": " ".join(full_text),
                "duration": round(info.duration, 2),
                "model": f"whisper-{settings.whisper_model}"
            }
            
            logger.info(f"Transcription completed. Language: {info.language}, Duration: {info.duration}s")
            
            return result
            
        except Exception as e:
            logger.error(f"Transcription failed: {str(e)}")
            raise RuntimeError(f"Transcription error: {str(e)}")
    
    def get_model_info(self) -> Dict[str, str]:
        """
        Get information about the loaded model.
        
        Returns:
            Dictionary with model configuration details
        """
        return {
            "model": settings.whisper_model,
            "device": settings.whisper_device,
            "compute_type": settings.whisper_compute_type,
            "status": "loaded" if self._model else "not loaded"
        }


# Global instance - will be initialized when imported
whisper_service = WhisperService()
