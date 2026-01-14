"""
Audio Processor - Validates and converts audio files for Whisper processing.
Handles format conversion and ensures audio is at 16kHz sample rate.
"""

from pydub import AudioSegment
from pathlib import Path
import logging
import os
import uuid
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


class AudioProcessor:
    """
    Service for audio file validation and preprocessing.
    Converts audio to the format required by Whisper (16kHz, mono).
    """
    
    # Supported audio formats
    SUPPORTED_FORMATS = ['.opus', '.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac', '.wma']
    
    def __init__(self):
        """Initialize audio processor and ensure tmp directory exists."""
        settings.ensure_tmp_dir()
    
    def validate_file(self, file_path: str) -> dict:
        """
        Validate audio file format and basic properties.
        
        Args:
            file_path: Path to the audio file
        
        Returns:
            Dictionary with validation results
        
        Raises:
            ValueError: If file is invalid
            FileNotFoundError: If file doesn't exist
        """
        path = Path(file_path)
        
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {file_path}")
        
        # Check file size
        file_size_bytes = path.stat().st_size
        max_size_bytes = settings.max_file_size_bytes
        
        if file_size_bytes > max_size_bytes:
            raise ValueError(
                f"File size ({file_size_bytes / 1024 / 1024:.2f} MB) exceeds "
                f"maximum allowed ({settings.max_file_size_mb} MB)"
            )
        
        # Check file extension
        file_extension = path.suffix.lower()
        if file_extension not in self.SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported audio format: {file_extension}. "
                f"Supported formats: {', '.join(self.SUPPORTED_FORMATS)}"
            )
        
        logger.info(f"File validation passed: {file_path} ({file_size_bytes / 1024:.2f} KB)")
        
        return {
            "valid": True,
            "size_bytes": file_size_bytes,
            "format": file_extension
        }
    
    def get_audio_duration(self, file_path: str) -> float:
        """
        Get the duration of an audio file in seconds.
        
        Args:
            file_path: Path to the audio file
        
        Returns:
            Duration in seconds
        
        Raises:
            RuntimeError: If audio cannot be loaded
        """
        try:
            audio = AudioSegment.from_file(file_path)
            duration_seconds = len(audio) / 1000.0
            
            logger.info(f"Audio duration: {duration_seconds:.2f} seconds")
            
            return duration_seconds
            
        except Exception as e:
            logger.error(f"Failed to get audio duration: {str(e)}")
            raise RuntimeError(f"Could not load audio file: {str(e)}")
    
    def validate_duration(self, duration_seconds: float) -> None:
        """
        Validate that audio duration is within acceptable limits.
        
        Args:
            duration_seconds: Audio duration in seconds
        
        Raises:
            ValueError: If duration exceeds maximum
        """
        if duration_seconds > settings.max_audio_duration_sec:
            raise ValueError(
                f"Audio duration ({duration_seconds:.2f}s) exceeds "
                f"maximum allowed ({settings.max_audio_duration_sec}s)"
            )
        
        if duration_seconds < 0.1:
            raise ValueError("Audio is too short (minimum 0.1 seconds)")
    
    def convert_to_wav(self, input_path: str, output_path: Optional[str] = None) -> str:
        """
        Convert audio file to WAV format with 16kHz sample rate (required by Whisper).
        Also converts to mono if stereo.
        
        Args:
            input_path: Path to input audio file
            output_path: Optional path for output file. If None, generates unique filename
        
        Returns:
            Path to the converted WAV file
        
        Raises:
            RuntimeError: If conversion fails
        """
        try:
            logger.info(f"Converting audio: {input_path}")
            
            # Load audio file
            audio = AudioSegment.from_file(input_path)
            
            # Convert to mono if stereo
            if audio.channels > 1:
                logger.info("Converting stereo to mono")
                audio = audio.set_channels(1)
            
            # Resample to 16kHz (Whisper's expected sample rate)
            target_sample_rate = settings.audio_sample_rate
            if audio.frame_rate != target_sample_rate:
                logger.info(f"Resampling from {audio.frame_rate}Hz to {target_sample_rate}Hz")
                audio = audio.set_frame_rate(target_sample_rate)
            
            # Generate output path if not provided
            if output_path is None:
                unique_id = uuid.uuid4().hex
                output_path = os.path.join(settings.tmp_dir, f"{unique_id}.wav")
            
            # Export as WAV
            audio.export(output_path, format="wav")
            
            logger.info(f"Audio converted successfully: {output_path}")
            
            return output_path
            
        except Exception as e:
            logger.error(f"Audio conversion failed: {str(e)}")
            raise RuntimeError(f"Failed to convert audio: {str(e)}")
    
    def process_audio(self, input_path: str) -> str:
        """
        Complete audio processing pipeline:
        1. Validate file
        2. Check duration
        3. Convert to 16kHz WAV
        
        Args:
            input_path: Path to input audio file
        
        Returns:
            Path to the processed WAV file ready for Whisper
        
        Raises:
            ValueError: If validation fails
            RuntimeError: If processing fails
        """
        logger.info(f"Starting audio processing pipeline: {input_path}")
        
        # Step 1: Validate file format and size
        validation_result = self.validate_file(input_path)
        logger.info(f"Validation passed: {validation_result}")
        
        # Step 2: Get and validate duration
        duration = self.get_audio_duration(input_path)
        self.validate_duration(duration)
        logger.info(f"Duration validation passed: {duration:.2f}s")
        
        # Step 3: Convert to 16kHz WAV
        output_path = self.convert_to_wav(input_path)
        
        logger.info(f"Audio processing completed: {output_path}")
        
        return output_path
    
    def cleanup(self, file_path: str) -> None:
        """
        Delete temporary audio file.
        
        Args:
            file_path: Path to file to delete
        """
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Cleaned up temporary file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to cleanup file {file_path}: {str(e)}")


# Global instance
audio_processor = AudioProcessor()
