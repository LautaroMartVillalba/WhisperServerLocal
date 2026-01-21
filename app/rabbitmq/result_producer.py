"""
RabbitMQ Result Producer - Publishes transcription results back to WhatsApp server.
"""

import pika
import json
import logging
from typing import Optional
from app.config import settings

logger = logging.getLogger(__name__)


class RabbitMQResultProducer:
    """
    Producer for publishing transcription results to whisper_results queue.
    Enables bidirectional communication with WhatsAppMCPServer.
    """
    
    # Queue and exchange configuration
    RESULTS_QUEUE = "whisper_results"
    RESULTS_EXCHANGE = "whisper_results_exchange"
    RESULTS_ROUTING_KEY = "transcription.result"
    
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
            
            logger.info("Result Producer connected to RabbitMQ")
        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ: {str(e)}")
            raise RuntimeError(f"RabbitMQ connection failed: {str(e)}")
    
    def _declare_topology(self):
        """
        Declare exchange, queue and binding for results.
        The WhatsAppMCPServer will consume from this queue.
        """
        try:
            # Declare exchange (direct type)
            self.channel.exchange_declare(
                exchange=self.RESULTS_EXCHANGE,
                exchange_type='direct',
                durable=True
            )
            
            # Declare results queue
            self.channel.queue_declare(
                queue=self.RESULTS_QUEUE,
                durable=True
            )
            
            # Bind queue to exchange
            self.channel.queue_bind(
                exchange=self.RESULTS_EXCHANGE,
                queue=self.RESULTS_QUEUE,
                routing_key=self.RESULTS_ROUTING_KEY
            )
            
            logger.info("RabbitMQ result topology declared successfully")
        except Exception as e:
            logger.error(f"Failed to declare topology: {str(e)}")
            raise
    
    def publish_transcription_result(
        self,
        attachment_id: int,
        texto: str,
        duration: float,
        model: str,
        success: bool,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Publish transcription result to results queue.
        
        Args:
            attachment_id: Database ID from original request
            texto: Transcribed text (empty string if failed)
            duration: Audio duration in seconds
            model: Whisper model used (e.g., "tiny", "base", "small")
            success: True if transcription succeeded, False otherwise
            error_message: Error details if success=False
        
        Returns:
            True if published successfully, False otherwise
        """
        try:
            # Build message payload
            message = {
                "attachment_id": attachment_id,
                "texto": texto,
                "duration": duration,
                "model": model,
                "success": success
            }
            
            # Add error message if provided
            if error_message:
                message["error_message"] = error_message
            
            # Publish with persistent delivery mode
            self.channel.basic_publish(
                exchange=self.RESULTS_EXCHANGE,
                routing_key=self.RESULTS_ROUTING_KEY,
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # Make message persistent
                    content_type='application/json'
                )
            )
            
            # Commit transaction
            self.channel.tx_commit()
            
            logger.info(f"📤 Published result for attachment_id: {attachment_id}, success: {success}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to publish result: {str(e)}")
            
            # Rollback transaction on error
            try:
                self.channel.tx_rollback()
            except Exception as rollback_error:
                logger.error(f"Failed to rollback: {str(rollback_error)}")
                # Reconnect on next publish
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
                logger.info("Result Producer connection closed")
        except Exception as e:
            logger.error(f"Error closing connection: {str(e)}")


# Global producer instance
_producer = None

def get_result_producer() -> RabbitMQResultProducer:
    """Get or create global result producer instance."""
    global _producer
    if _producer is None:
        _producer = RabbitMQResultProducer()
    return _producer
