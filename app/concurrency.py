"""
Concurrency Manager - Controls concurrent transcription processing.
Limits simultaneous transcriptions to prevent resource exhaustion.
"""

import asyncio
import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


class ConcurrencyManager:
    """
    Manages concurrent transcription processing with a semaphore.
    Limits the number of simultaneous transcriptions.
    """
    
    _instance: Optional['ConcurrencyManager'] = None
    _semaphore: Optional[asyncio.Semaphore] = None
    
    def __new__(cls):
        """Ensure only one instance exists (Singleton pattern)."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize concurrency manager with semaphore."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(settings.workers_count)
            logger.info(f"Concurrency manager initialized with {settings.workers_count} workers")
    
    async def acquire(self):
        """
        Acquire a processing slot.
        Blocks if all slots are in use.
        """
        await self._semaphore.acquire()
        logger.debug("Processing slot acquired")
    
    def release(self):
        """Release a processing slot."""
        self._semaphore.release()
        logger.debug("Processing slot released")
    
    async def __aenter__(self):
        """Context manager entry - acquire slot."""
        await self.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - release slot."""
        self.release()
        return False
    
    def get_available_slots(self) -> int:
        """
        Get number of available processing slots.
        
        Returns:
            Number of available slots
        """
        return self._semaphore._value if self._semaphore else 0
    
    def get_total_slots(self) -> int:
        """
        Get total number of processing slots.
        
        Returns:
            Total number of slots
        """
        return settings.workers_count


# Global instance
concurrency_manager = ConcurrencyManager()
