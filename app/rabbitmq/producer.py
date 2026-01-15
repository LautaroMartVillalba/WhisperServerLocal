"""
RabbitMQ Producer - Publishes transcription jobs to the queue.
"""

import pika
import json
import logging
from typing import Dict, Optional

from app.config import settings

logger = logging.getLogger(__name__)


class RabbitMQProducer:
    """
    Producer for publishing transcription jobs to RabbitMQ.
    Uses direct exchange with persistent messages and transactional processing.
    """
    
    # Queue names
    MAIN_QUEUE = "whisper_transcriptions"
    RETRY_QUEUE = "whisper_transcriptions.retry"
    DLQ = "whisper_transcriptions.dlq"
    
    # Exchange names
    MAIN_EXCHANGE = "whisper_exchange"
    RETRY_EXCHANGE = "whisper_retry_exchange"
    DLQ_EXCHANGE = "whisper_dlq_exchange"
    
    # Routing keys
    MAIN_ROUTING_KEY = "transcription.process"
    RETRY_ROUTING_KEY = "transcription.retry"
    DLQ_ROUTING_KEY = "transcription.failed"
    
    # Retry configuration
    MAX_RETRIES = 2  # Maximum 2 retries (3 total attempts)
    RETRY_TTL_MS = 5000  # 5 seconds delay before retry
    
    def __init__(self):
        """Initialize RabbitMQ connection and declare topology."""
        self.connection = None
        self.channel = None
        self._connect()
        self._declare_topology()
    
    def _connect(self):
        """Establish connection to RabbitMQ."""
        try:
            parameters = pika.URLParameters(settings.rabbitmq_url)
            self.connection = pika.BlockingConnection(parameters)
            self.channel = self.connection.channel()
            
            # Enable transaction mode for reliability
            self.channel.tx_select()
            
            logger.info("Connected to RabbitMQ successfully")
            
        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ: {str(e)}")
            raise RuntimeError(f"RabbitMQ connection failed: {str(e)}")
    
    def _declare_topology(self):
        """
        Declare exchanges, queues and bindings.
        Sets up main queue, retry queue with TTL, and dead letter queue.
        """
        try:
            # Declare exchanges (all direct type)
            self.channel.exchange_declare(
                exchange=self.MAIN_EXCHANGE,
                exchange_type='direct',
                durable=True
            )
            
            self.channel.exchange_declare(
                exchange=self.RETRY_EXCHANGE,
                exchange_type='direct',
                durable=True
            )
            
            self.channel.exchange_declare(
                exchange=self.DLQ_EXCHANGE,
                exchange_type='direct',
                durable=True
            )
            
            # Main queue - with DLX to retry queue
            self.channel.queue_declare(
                queue=self.MAIN_QUEUE,
                durable=True,
                arguments={
                    'x-dead-letter-exchange': self.RETRY_EXCHANGE,
                    'x-dead-letter-routing-key': self.RETRY_ROUTING_KEY
                }
            )
            
            # Retry queue - with TTL and DLX back to main queue
            self.channel.queue_declare(
                queue=self.RETRY_QUEUE,
                durable=True,
                arguments={
                    'x-message-ttl': self.RETRY_TTL_MS,  # Messages expire after TTL
                    'x-dead-letter-exchange': self.MAIN_EXCHANGE,
                    'x-dead-letter-routing-key': self.MAIN_ROUTING_KEY
                }
            )
            
            # Dead Letter Queue - final destination for failed messages
            self.channel.queue_declare(
                queue=self.DLQ,
                durable=True
            )
            
            # Bind queues to exchanges
            self.channel.queue_bind(
                exchange=self.MAIN_EXCHANGE,
                queue=self.MAIN_QUEUE,
                routing_key=self.MAIN_ROUTING_KEY
            )
            
            self.channel.queue_bind(
                exchange=self.RETRY_EXCHANGE,
                queue=self.RETRY_QUEUE,
                routing_key=self.RETRY_ROUTING_KEY
            )
            
            self.channel.queue_bind(
                exchange=self.DLQ_EXCHANGE,
                queue=self.DLQ,
                routing_key=self.DLQ_ROUTING_KEY
            )
            
            logger.info("RabbitMQ topology declared successfully")
            
        except Exception as e:
            logger.error(f"Failed to declare RabbitMQ topology: {str(e)}")
            raise RuntimeError(f"Topology declaration failed: {str(e)}")
    
    def publish_transcription_job(
        self,
        audio_file_path: str,
        language: Optional[str] = None
    ) -> bool:
        """
        Publish a transcription job to the main queue.
        
        Args:
            audio_file_path: Path to the audio file to transcribe
            language: Optional language code for transcription
        
        Returns:
            True if published successfully, False otherwise
        """
        try:
            # Prepare message payload
            message = {
                "audio_file_path": audio_file_path,
                "language": language,
                "retry_count": 0  # Initial retry count
            }
            
            # Publish with persistent delivery mode (delivery_mode=2)
            self.channel.basic_publish(
                exchange=self.MAIN_EXCHANGE,
                routing_key=self.MAIN_ROUTING_KEY,
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # Make message persistent
                    content_type='application/json',
                    headers={
                        'x-retry-count': 0
                    }
                )
            )
            
            # Commit transaction
            self.channel.tx_commit()
            
            logger.info(f"Published transcription job: {audio_file_path}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to publish message: {str(e)}")
            
            # Rollback transaction on error
            try:
                self.channel.tx_rollback()
                logger.info("Transaction rolled back successfully")
            except Exception as rollback_error:
                logger.error(f"Failed to rollback transaction: {str(rollback_error)}")
                # Connection might be broken, try to reconnect on next publish
                try:
                    self.connection.close()
                except:
                    pass
                self.connection = None
                self.channel = None
            
            return False
    
    def close(self):
        """Close RabbitMQ connection."""
        try:
            if self.connection and not self.connection.is_closed:
                self.connection.close()
                logger.info("RabbitMQ connection closed")
        except Exception as e:
            logger.error(f"Error closing RabbitMQ connection: {str(e)}")


# Global producer instance (will be initialized when needed)
_producer = None

def get_producer() -> RabbitMQProducer:
    """Get or create global producer instance."""
    global _producer
    if _producer is None:
        _producer = RabbitMQProducer()
    return _producer
