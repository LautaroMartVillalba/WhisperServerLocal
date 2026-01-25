"""
Audio Processor - Validates and converts audio files for Whisper processing.

Standalone module without external app dependencies.
Configuration loaded from environment variables.
"""
import os
import uuid
import logging
from pathlib import Path
from pydub import AudioSegment

logger = logging.getLogger(__name__)

# Configuration from environment variables
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "100"))
MAX_AUDIO_DURATION_SEC = int(os.getenv("MAX_AUDIO_DURATION_SEC", "3600"))
AUDIO_SAMPLE_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
TMP_DIR = os.getenv("TMP_DIR", "/tmp/whisper")


class AudioProcessor:
    """
    Service for audio file validation and preprocessing.
    Converts audio to the format required by Whisper (16kHz, mono WAV).
    """
    
    # Supported audio formats
    SUPPORTED_FORMATS = ['.opus', '.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac', '.wma']
    
    def __init__(self):
        """Initialize audio processor and ensure tmp directory exists."""
        Path(TMP_DIR).mkdir(parents=True, exist_ok=True)
    
    def validate_file(self, file_path: str) -> dict:
        """
        Validate audio file format and size.
        
        Args:
            file_path: Path to the audio file
        
        Returns:
            Dictionary with validation results
        
        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file is invalid
        """
        path = Path(file_path)
        
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {file_path}")
        
        # Check file size
        file_size_bytes = path.stat().st_size
        max_size_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
        
        if file_size_bytes > max_size_bytes:
            raise ValueError(
                f"File size ({file_size_bytes / 1024 / 1024:.2f} MB) exceeds "
                f"maximum allowed ({MAX_FILE_SIZE_MB} MB)"
            )
        
        # Check file extension
        file_extension = path.suffix.lower()
        if file_extension not in self.SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported audio format: {file_extension}. "
                f"Supported: {', '.join(self.SUPPORTED_FORMATS)}"
            )
        
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
            return duration_seconds
        except Exception as e:
            logger.error(f"Failed to get audio duration: {e}")
            raise RuntimeError(f"Could not load audio file: {str(e)}")
    
    def convert_to_wav(self, file_path: str) -> str:
        """
        Convert audio file to 16kHz mono WAV format.
        
        Args:
            file_path: Path to the input audio file
        
        Returns:
            Path to the converted WAV file
        
        Raises:
            RuntimeError: If conversion fails
        """
        try:
            # Load audio
            audio = AudioSegment.from_file(file_path)
            
            # Convert to mono and resample to 16kHz
            audio = audio.set_channels(1)
            audio = audio.set_frame_rate(AUDIO_SAMPLE_RATE)
            
            # Generate output path
            output_filename = f"{uuid.uuid4()}.wav"
            output_path = os.path.join(TMP_DIR, output_filename)
            
            # Export as WAV
            audio.export(output_path, format="wav")
            
            return output_path
            
        except Exception as e:
            logger.error(f"Failed to convert audio: {e}")
            raise RuntimeError(f"Audio conversion failed: {str(e)}")
    
    def process_audio(self, file_path: str) -> str:
        """
        Complete audio processing pipeline: validate, check duration, convert.
        
        Args:
            file_path: Path to the input audio file
        
        Returns:
            Path to the processed WAV file
        
        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If validation fails
            RuntimeError: If processing fails
        """
        # Step 1: Validate file
        self.validate_file(file_path)
        
        # Step 2: Load and check duration
        audio = AudioSegment.from_file(file_path)
        duration_seconds = len(audio) / 1000.0
        
        if duration_seconds > MAX_AUDIO_DURATION_SEC:
            raise ValueError(
                f"Audio duration ({duration_seconds:.2f}s) exceeds "
                f"maximum allowed ({MAX_AUDIO_DURATION_SEC}s)"
            )
        
        # Step 3: Convert to 16kHz mono WAV
        audio = audio.set_channels(1)
        audio = audio.set_frame_rate(AUDIO_SAMPLE_RATE)
        
        output_filename = f"{uuid.uuid4()}.wav"
        output_path = os.path.join(TMP_DIR, output_filename)
        
        audio.export(output_path, format="wav")
        
        return output_path
    
    def cleanup(self, file_path: str) -> bool:
        """
        Delete a temporary file.
        
        Args:
            file_path: Path to the file to delete
        
        Returns:
            True if deleted, False otherwise
        """
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                return True
        except Exception as e:
            logger.warning(f"Cleanup failed: {e}")
        return False
