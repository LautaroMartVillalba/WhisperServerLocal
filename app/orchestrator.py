"""
Orchestrator - Coordinates the complete audio transcription workflow.
This is the main service that ties together audio processing and transcription.
"""

import logging
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional
from pathlib import Path
from functools import partial

from app.audio_processor import audio_processor
from app.whisper_service import whisper_service
from app.config import settings

logger = logging.getLogger(__name__)

# Thread pool for blocking operations (increased to handle concurrent requests)
_executor = ThreadPoolExecutor(max_workers=settings.workers_count, thread_name_prefix="whisper-worker")


class TranscriptionOrchestrator:
    """
    Orchestrates the complete audio transcription pipeline.
    Handles coordination between audio processing and Whisper transcription.
    """
    
    def __init__(self):
        """Initialize orchestrator with audio processor and whisper service."""
        self.audio_processor = audio_processor
        self.whisper_service = whisper_service
        settings.ensure_tmp_dir()
    
    def _sync_transcribe(
        self,
        audio_file_path: str,
        language: Optional[str] = None
    ) -> tuple[Dict[str, any], str]:
        """
        Synchronous transcription logic to run in thread pool.
        
        Returns:
            Tuple of (transcription_result, processed_wav_path)
        """
        import threading
        thread_name = threading.current_thread().name
        logger.info(f"[{thread_name}] Starting processing in worker thread")
        
        # Step 1 & 2 & 3: Process audio (validate, check duration, convert to 16kHz WAV)
        logger.info(f"[{thread_name}] Step 1-3: Processing audio file...")
        processed_wav_path = self.audio_processor.process_audio(audio_file_path)
        
        # Step 4: Transcribe with Whisper
        logger.info(f"[{thread_name}] Step 4: Transcribing audio with Whisper...")
        transcription_result = self.whisper_service.transcribe(
            audio_path=processed_wav_path,
            language=language
        )
        
        logger.info(f"[{thread_name}] Processing completed in worker thread")
        
        return transcription_result, processed_wav_path
    
    async def transcribe_audio(
        self,
        audio_file_path: str,
        language: Optional[str] = None,
        cleanup_input: bool = True
    ) -> Dict[str, any]:
        """
        Complete transcription workflow from audio file to text.
        Runs blocking operations in a thread pool to allow concurrent processing.
        
        Workflow:
        1. Validate audio file (format, size)
        2. Check audio duration
        3. Convert to 16kHz WAV format
        4. Transcribe with Whisper (in thread pool)
        5. Cleanup temporary files
        6. Return transcription result
        
        Args:
            audio_file_path: Path to the input audio file
            language: Optional language code (e.g., 'es', 'en'). If None, auto-detect
            cleanup_input: Whether to delete the input file after processing
        
        Returns:
            Dictionary containing:
                - text: Transcribed text
                - duration: Audio duration in seconds
                - model: Model used for transcription
                - success: Boolean indicating success
        
        Raises:
            ValueError: If validation fails
            RuntimeError: If processing or transcription fails
        """
        processed_wav_path = None
        
        try:
            logger.info(f"Starting transcription workflow for: {audio_file_path}")
            
            # Run blocking operations in thread pool
            loop = asyncio.get_event_loop()
            transcription_result, processed_wav_path = await loop.run_in_executor(
                _executor,
                self._sync_transcribe,
                audio_file_path,
                language
            )
            
            # Add success flag
            transcription_result["success"] = True
            
            logger.info("Transcription workflow completed successfully")
            
            # Step 5: Cleanup temporary files (only on success)
            logger.info("Step 5: Cleaning up temporary files...")
            
            # Cleanup the processed WAV file
            if processed_wav_path and os.path.exists(processed_wav_path):
                self.audio_processor.cleanup(processed_wav_path)
            
            # Optionally cleanup the input file (only on success)
            if cleanup_input and os.path.exists(audio_file_path):
                self.audio_processor.cleanup(audio_file_path)
            
            return transcription_result
        
        except ValueError as e:
            logger.error(f"Validation error: {str(e)}")
            # Cleanup processed WAV on validation error (won't retry validation errors)
            if processed_wav_path and os.path.exists(processed_wav_path):
                self.audio_processor.cleanup(processed_wav_path)
            raise
        
        except Exception as e:
            logger.error(f"Transcription workflow failed: {str(e)}")
            # Do NOT cleanup files on transcription error - allow retry
            # RabbitMQ will handle retry logic with NACK
            logger.info("Preserving files for potential retry")
            raise RuntimeError(f"Transcription failed: {str(e)}")
    
    def get_service_status(self) -> Dict[str, any]:
        """
        Get the status of all services in the orchestrator.
        
        Returns:
            Dictionary with status information
        """
        try:
            model_info = self.whisper_service.get_model_info()
            
            return {
                "status": "healthy",
                "whisper_service": model_info,
                "tmp_dir": settings.tmp_dir,
                "max_file_size_mb": settings.max_file_size_mb,
                "max_duration_sec": settings.max_audio_duration_sec,
                "supported_formats": self.audio_processor.SUPPORTED_FORMATS
            }
        except Exception as e:
            logger.error(f"Failed to get service status: {str(e)}")
            return {
                "status": "unhealthy",
                "error": str(e)
            }


# Global instance
orchestrator = TranscriptionOrchestrator()
