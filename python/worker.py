#!/usr/bin/env python3
"""
Whisper-Local Python Worker

Persistent worker process that:
1. Loads the Whisper model ONCE at startup
2. Reads JSON requests from stdin
3. Processes audio files
4. Writes JSON responses to stdout

Communication protocol:
- Startup: prints "READY" to stdout when initialized
- Request: JSON line on stdin {"audio_file_path": "...", "language": "..."}
- Response: JSON line on stdout {"success": true/false, ...}
"""
import sys
import json
import logging
import signal
import select
import os

# Configure logging to stderr (stdout is for communication with Go)
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Import local modules
from audio_processor import AudioProcessor
from whisper_service import WhisperService

# Idle timeout in seconds (also controlled by Go)
IDLE_TIMEOUT = int(os.getenv("PROCESS_IDLE_TIMEOUT_SEC", "300"))  # 5 minutes

# Global services (initialized once)
audio_processor = None
whisper_service = None


def init_services():
    """Initialize services and load Whisper model."""
    global audio_processor, whisper_service
    
    logger.info("🔧 Initializing...")
    audio_processor = AudioProcessor()
    whisper_service = WhisperService()
    logger.info("✅ Model loaded")


def process_request(request: dict) -> dict:
    """
    Process a single transcription request.
    
    Args:
        request: Dict with 'audio_file_path' and optional 'language'
    
    Returns:
        Dict with 'success', 'texto', 'duration', 'model' or 'error_message'
    """
    processed_wav_path = None
    
    try:
        audio_file_path = request["audio_file_path"]
        language = request.get("language")
        
        # Step 1: Validate and convert audio to 16kHz WAV
        processed_wav_path = audio_processor.process_audio(audio_file_path)
        
        # Step 2: Transcribe with Whisper
        result = whisper_service.transcribe(
            audio_path=processed_wav_path,
            language=language
        )
        
        # Step 3: Cleanup temporary files
        audio_processor.cleanup(processed_wav_path)
        audio_processor.cleanup(audio_file_path)
        
        return {
            "success": True,
            "texto": result["text"],
            "duration": result["duration"],
            "model": result["model"]
        }
        
    except FileNotFoundError as e:
        return {
            "success": False,
            "error_message": f"File not found: {str(e)}"
        }
        
    except ValueError as e:
        # Cleanup on validation error
        if processed_wav_path:
            audio_processor.cleanup(processed_wav_path)
        return {
            "success": False,
            "error_message": f"Validation error: {str(e)}"
        }
        
    except Exception as e:
        logger.error(f"❌ {type(e).__name__}: {str(e)}")
        return {
            "success": False,
            "error_message": f"Processing error: {str(e)}"
        }


def main_loop():
    """
    Main processing loop.
    
    Reads JSON requests from stdin, processes them, and writes responses to stdout.
    Uses select() for timeout-based idle detection on Linux.
    """
    while True:
        try:
            # Wait for input with timeout (for idle detection)
            # On Linux, we use select() for timeout on stdin
            if sys.platform != 'win32':
                ready, _, _ = select.select([sys.stdin], [], [], IDLE_TIMEOUT)
                
                if not ready:
                    logger.info("💤 Idle timeout, exiting")
                    break
            
            # Read line from stdin
            line = sys.stdin.readline()
            
            if not line:
                break
            
            line = line.strip()
            if not line:
                continue
            
            # Parse JSON request
            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                response = {
                    "success": False,
                    "error_message": f"Invalid JSON input: {str(e)}"
                }
                print(json.dumps(response), flush=True)
                continue
            
            # Process request
            response = process_request(request)
            
            # Write response (single JSON line)
            print(json.dumps(response), flush=True)
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"❌ Unexpected: {e}")
            response = {
                "success": False,
                "error_message": f"Worker error: {str(e)}"
            }
            print(json.dumps(response), flush=True)


def handle_sigterm(signum, frame):
    """Handle SIGTERM signal for graceful shutdown."""
    sys.exit(0)


def main():
    """Entry point."""
    # Setup signal handlers
    signal.signal(signal.SIGTERM, handle_sigterm)
    if hasattr(signal, 'SIGPIPE'):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    
    try:
        # Initialize services and load model
        init_services()
        
        # Signal to Go that we're ready
        print("READY", flush=True)
        
        # Enter main processing loop
        main_loop()
        
    except Exception as e:
        logger.error(f"❌ Fatal: {e}")
        sys.exit(1)
    
    sys.exit(0)


if __name__ == "__main__":
    main()
