"""
FastAPI main application - REST API for audio transcription.
"""

from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
import logging
import uuid
import os
from pathlib import Path
from typing import Optional
import threading

from app.config import settings
from app.orchestrator import orchestrator
from app.concurrency import concurrency_manager
from app.rabbitmq.consumer import RabbitMQConsumer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="WhisperLocal API",
    description="Audio transcription API using Whisper model",
    version="0.1.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Custom exception handler for validation errors
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Custom handler for validation errors to provide detailed error messages.
    """
    error_details = []
    for error in exc.errors():
        error_details.append({
            "field": " -> ".join(str(x) for x in error["loc"]),
            "message": error["msg"],
            "type": error["type"]
        })
    
    logger.error(f"Validation error on {request.method} {request.url.path}")
    logger.error(f"Error details: {error_details}")
    
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Validation error",
            "errors": error_details,
            "message": "The request body is not valid. Check the required fields."
        }
    )


# Middleware to log all requests
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """
    Log all incoming requests with details.
    """
    logger.info(f"Incoming request: {request.method} {request.url.path}")
    logger.debug(f"Headers: {dict(request.headers)}")
    
    response = await call_next(request)
    
    logger.info(f"Response status: {response.status_code}")
    
    return response


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup."""
    logger.info("Starting WhisperLocal API...")
    
    # Ensure tmp directory exists
    settings.ensure_tmp_dir()
    
    # Verify Whisper model is loaded
    try:
        model_info = orchestrator.get_service_status()
        logger.info(f"Whisper service status: {model_info}")
    except Exception as e:
        logger.error(f"Failed to initialize Whisper service: {str(e)}")
        raise
    
    # Start RabbitMQ consumer in a separate thread
    def start_consumer():
        """Start RabbitMQ consumer in background thread."""
        try:
            logger.info("Starting RabbitMQ consumer thread...")
            consumer = RabbitMQConsumer()
            consumer.start_consuming()
        except Exception as e:
            logger.error(f"RabbitMQ consumer failed: {str(e)}")
    
    # Start consumer in daemon thread (will stop when main app stops)
    consumer_thread = threading.Thread(target=start_consumer, daemon=True)
    consumer_thread.start()
    logger.info("RabbitMQ consumer thread started")
    
    logger.info("WhisperLocal API started successfully")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info("Shutting down WhisperLocal API...")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "WhisperLocal API",
        "version": "0.1.0",
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """
    Health check endpoint.
    Returns service status and configuration.
    """
    try:
        status = orchestrator.get_service_status()
        
        # Add concurrency information
        status["concurrency"] = {
            "max_workers": concurrency_manager.get_total_slots(),
            "available_slots": concurrency_manager.get_available_slots(),
            "busy_slots": concurrency_manager.get_total_slots() - concurrency_manager.get_available_slots()
        }
        
        return JSONResponse(
            status_code=200,
            content=status
        )
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": str(e)
            }
        )


@app.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(..., description="Audio file to transcribe"),
    language: Optional[str] = Form(None, description="Language code (e.g., 'es', 'en'). Auto-detect if not provided")
):
    """
    Transcribe an audio file to text.
    
    Accepts audio files in formats: .opus, .mp3, .wav, .m4a, .ogg, .flac, .aac, .wma
    
    The audio will be automatically converted to 16kHz mono WAV format for optimal Whisper processing.
    
    Args:
        file: Audio file (multipart/form-data)
        language: Optional language code for transcription
    
    Returns:
        JSON with transcription result:
        - text: Transcribed text
        - duration: Audio duration in seconds
        - model: Model used for transcription
        - success: Boolean indicating success
    
    Raises:
        400: Invalid file or validation error
        500: Transcription processing error
    """
    temp_file_path = None
    
    try:
        # Validate file is provided
        if not file:
            raise HTTPException(status_code=400, detail="No file provided")
        
        # Validate file has content
        if file.size == 0:
            raise HTTPException(status_code=400, detail="Empty file provided")
        
        # Check file size before reading
        if file.size and file.size > settings.max_file_size_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"File size exceeds maximum allowed ({settings.max_file_size_mb} MB)"
            )
        
        # Get file extension
        file_extension = Path(file.filename).suffix.lower() if file.filename else ""
        if not file_extension:
            raise HTTPException(status_code=400, detail="File must have an extension")
        
        # Validate file extension
        from app.audio_processor import audio_processor
        if file_extension not in audio_processor.SUPPORTED_FORMATS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file format: {file_extension}. Supported: {', '.join(audio_processor.SUPPORTED_FORMATS)}"
            )
        
        # Generate unique filename
        unique_id = uuid.uuid4().hex
        temp_file_path = os.path.join(settings.tmp_dir, f"{unique_id}{file_extension}")
        
        logger.info(f"Receiving file: {file.filename} ({file.size} bytes)")
        
        # Save uploaded file to temporary location
        with open(temp_file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
        
        logger.info(f"File saved to: {temp_file_path}")
        
        # Acquire processing slot (waits if all slots busy)
        async with concurrency_manager:
            available = concurrency_manager.get_available_slots()
            total = concurrency_manager.get_total_slots()
            logger.info(f"Processing slot acquired ({total - available}/{total} slots in use)")
            
            # Process transcription (runs in thread pool - non-blocking)
            result = await orchestrator.transcribe_audio(
                audio_file_path=temp_file_path,
                language=language,
                cleanup_input=True  # Cleanup after processing
            )
        
        logger.info(f"Transcription completed successfully for: {file.filename}")
        
        return JSONResponse(
            status_code=200,
            content=result
        )
    
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    
    except ValueError as e:
        # Validation errors
        logger.error(f"Validation error: {str(e)}")
        
        # Cleanup temp file on validation error
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except:
                pass
        
        raise HTTPException(status_code=400, detail=str(e))
    
    except Exception as e:
        # Unexpected errors
        logger.error(f"Transcription failed: {str(e)}")
        
        # Cleanup temp file on error
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except:
                pass
        
        raise HTTPException(
            status_code=500,
            detail=f"Transcription failed: {str(e)}"
        )


@app.get("/model-info")
async def get_model_info():
    """
    Get information about the loaded Whisper model.
    
    Returns:
        Model configuration details
    """
    try:
        from app.whisper_service import whisper_service
        model_info = whisper_service.get_model_info()
        
        return JSONResponse(
            status_code=200,
            content=model_info
        )
    except Exception as e:
        logger.error(f"Failed to get model info: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/debug-upload")
async def debug_upload(request: Request):
    """
    Debug endpoint to see what's being received in the request.
    Temporary endpoint for debugging multipart issues.
    """
    logger.info("=" * 50)
    logger.info("DEBUG UPLOAD - Checking request details")
    logger.info("=" * 50)
    
    # Log headers
    logger.info("Headers:")
    for key, value in request.headers.items():
        logger.info(f"  {key}: {value}")
    
    # Log content type
    content_type = request.headers.get("content-type", "")
    logger.info(f"Content-Type: {content_type}")
    
    # Try to parse form data
    try:
        form = await request.form()
        logger.info(f"Form fields found: {list(form.keys())}")
        
        for key in form.keys():
            value = form[key]
            logger.info(f"Field '{key}': type={type(value).__name__}")
            
            if hasattr(value, 'filename'):
                logger.info(f"  - filename: {value.filename}")
                logger.info(f"  - content_type: {value.content_type}")
                logger.info(f"  - size: {value.size if hasattr(value, 'size') else 'unknown'}")
            else:
                logger.info(f"  - value: {value}")
        
        return JSONResponse({
            "status": "success",
            "fields_received": list(form.keys()),
            "details": {
                key: {
                    "type": type(value).__name__,
                    "filename": value.filename if hasattr(value, 'filename') else None,
                    "content_type": value.content_type if hasattr(value, 'content_type') else None
                }
                for key, value in form.items()
            }
        })
    except Exception as e:
        logger.error(f"Error parsing form: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=400)
    
    finally:
        logger.info("=" * 50)


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True
    )
