"""
RabbitMQ Consumer - Processes transcription jobs from the queue.
Implements retry logic with maximum 2 retries (3 total attempts).
"""

import pika
import json
import logging
import asyncio
import os
from typing import Dict

from app.config import settings
from app.orchestrator import orchestrator
from app.audio_processor import audio_processor
from app.rabbitmq.result_producer import get_result_producer

logger = logging.getLogger(__name__)


class RabbitMQConsumer:
    """
    Consumer for processing transcription jobs from RabbitMQ.
    Implements transactional processing with automatic retry logic.
    """
    
    # Queue and exchange names (must match producer)
    MAIN_QUEUE = "whisper_transcriptions"
    RETRY_EXCHANGE = "whisper_retry_exchange"
    RETRY_ROUTING_KEY = "transcription.retry"
    DLQ_EXCHANGE = "whisper_dlq_exchange"
    DLQ_ROUTING_KEY = "transcription.failed"
    
    MAX_RETRIES = 2  # Maximum 2 retries (3 total attempts)
    
    def __init__(self):
        """Initialize RabbitMQ consumer."""
        self.connection = None
        self.channel = None
    
    def _connect(self):
        """Establish connection to RabbitMQ."""
        try:
            parameters = pika.URLParameters(settings.rabbitmq_url)
            self.connection = pika.BlockingConnection(parameters)
            self.channel = self.connection.channel()
            
            # Set QoS to process one message at a time (for concurrency control)
            self.channel.basic_qos(prefetch_count=1)
            
            logger.info("Consumer connected to RabbitMQ")
            
        except Exception as e:
            logger.error(f"Consumer failed to connect to RabbitMQ: {str(e)}")
            raise RuntimeError(f"Consumer connection failed: {str(e)}")
    
    def _declare_topology(self):
        """
        Declare all required RabbitMQ topology (exchanges, queues, bindings).
        This ensures the consumer can work even if the producer hasn't created them yet.
        """
        try:
            # Declare main exchange (for incoming transcription requests)
            self.channel.exchange_declare(
                exchange='whisper_exchange',
                exchange_type='direct',
                durable=True
            )
            
            # Declare main queue (where we consume from)
            self.channel.queue_declare(
                queue=self.MAIN_QUEUE,
                durable=True
            )
            
            # Bind main queue to exchange
            self.channel.queue_bind(
                exchange='whisper_exchange',
                queue=self.MAIN_QUEUE,
                routing_key='transcription.request'
            )
            
            # Declare retry exchange
            self.channel.exchange_declare(
                exchange=self.RETRY_EXCHANGE,
                exchange_type='direct',
                durable=True
            )
            
            # Declare retry queue with TTL and DLX back to main queue
            self.channel.queue_declare(
                queue='whisper_retry_queue',
                durable=True,
                arguments={
                    'x-message-ttl': 5000,  # 5 seconds delay
                    'x-dead-letter-exchange': 'whisper_exchange',
                    'x-dead-letter-routing-key': 'transcription.request'
                }
            )
            
            # Bind retry queue to retry exchange
            self.channel.queue_bind(
                exchange=self.RETRY_EXCHANGE,
                queue='whisper_retry_queue',
                routing_key=self.RETRY_ROUTING_KEY
            )
            
            # Declare DLQ exchange
            self.channel.exchange_declare(
                exchange=self.DLQ_EXCHANGE,
                exchange_type='direct',
                durable=True
            )
            
            # Declare DLQ queue
            self.channel.queue_declare(
                queue='whisper_dlq',
                durable=True
            )
            
            # Bind DLQ queue to DLQ exchange
            self.channel.queue_bind(
                exchange=self.DLQ_EXCHANGE,
                queue='whisper_dlq',
                routing_key=self.DLQ_ROUTING_KEY
            )
            
            logger.info("RabbitMQ topology declared successfully")
            
        except Exception as e:
            logger.error(f"Failed to declare topology: {str(e)}")
            raise
    
    def _process_message(self, ch, method, properties, body):
        """
        Process a single transcription job and publish result.
        
        Args:
            ch: Channel
            method: Delivery method
            properties: Message properties
            body: Message body (JSON)
        """
        audio_file_path = None
        attachment_id = None
        
        # Get result producer
        result_producer = get_result_producer()
        
        # Get retry count from headers FIRST (before try block)
        retry_count = 0
        if properties.headers and 'x-retry-count' in properties.headers:
            retry_count = properties.headers['x-retry-count']
        
        try:
            # Parse message
            message = json.loads(body)
            
            # Support both snake_case (Python) and camelCase (Java)
            audio_file_path = message.get('audio_file_path') or message.get('audioFilePath')
            language = message.get('language')
            attachment_id = message.get('attachment_id') or message.get('attachmentId')
            
            # Validate attachment_id is present
            if attachment_id is None:
                logger.error(f"Missing attachment_id in message! Received: {message}")
                ch.basic_ack(delivery_tag=method.delivery_tag)  # ACK and discard
                return
            
            # Validate audio_file_path is present
            if not audio_file_path:
                logger.error(f"Missing audio_file_path in message! Received: {message}")
                ch.basic_ack(delivery_tag=method.delivery_tag)  # ACK and discard
                return
            
            logger.info(f"Processing attachment_id: {attachment_id}, file: {audio_file_path} (attempt {retry_count + 1}/{self.MAX_RETRIES + 1})")
            
            # Process transcription (async call in sync context)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                orchestrator.transcribe_audio(
                    audio_file_path=audio_file_path,
                    language=language,
                    cleanup_input=True  # Cleanup on success
                )
            )
            loop.close()
            
            # Publish successful result
            result_producer.publish_transcription_result(
                attachment_id=attachment_id,
                texto=result.get('text', ''),
                duration=result.get('duration', 0.0),
                model=result.get('model', 'unknown'),
                success=True
            )
            
            # Success - ACK the message
            ch.basic_ack(delivery_tag=method.delivery_tag)
            logger.info(f"✅ Transcription successful: {audio_file_path}")
            
        except Exception as e:
            logger.error(f"❌ Transcription failed (attempt {retry_count + 1}/{self.MAX_RETRIES + 1}): {str(e)}")
            
            # retry_count already obtained at the beginning of _process_message
            # Check if we should retry
            if retry_count < self.MAX_RETRIES:
                # NACK with requeue=False to send to DLX (retry queue)
                logger.info(f"Sending to retry queue (attempt {retry_count + 1}/{self.MAX_RETRIES})")
                
                # Update retry count in headers
                new_headers = properties.headers or {}
                new_headers['x-retry-count'] = retry_count + 1
                
                # Publish to retry exchange with updated headers
                ch.basic_publish(
                    exchange=self.RETRY_EXCHANGE,
                    routing_key=self.RETRY_ROUTING_KEY,
                    body=body,
                    properties=pika.BasicProperties(
                        delivery_mode=2,
                        content_type='application/json',
                        headers=new_headers
                    )
                )
                
                # ACK original message (we've manually sent to retry)
                ch.basic_ack(delivery_tag=method.delivery_tag)
                
            else:
                # Max retries exceeded - send to DLQ and cleanup audio
                logger.error(f"Max retries exceeded for: {audio_file_path}. Sending to DLQ.")
                
                # Publish failed result
                if attachment_id is not None:
                    result_producer.publish_transcription_result(
                        attachment_id=attachment_id,
                        texto="",
                        duration=0.0,
                        model="unknown",
                        success=False,
                        error_message=f"Max retries exceeded: {str(e)}"
                    )
                
                # Publish to DLQ
                ch.basic_publish(
                    exchange=self.DLQ_EXCHANGE,
                    routing_key=self.DLQ_ROUTING_KEY,
                    body=body,
                    properties=pika.BasicProperties(
                        delivery_mode=2,
                        content_type='application/json',
                        headers={'x-failure-reason': str(e)}
                    )
                )
                
                # ACK the message
                ch.basic_ack(delivery_tag=method.delivery_tag)
                
                # Cleanup audio files (failed permanently)
                if audio_file_path and os.path.exists(audio_file_path):
                    audio_processor.cleanup(audio_file_path)
                    logger.info(f"Cleaned up failed audio: {audio_file_path}")
    
    def start_consuming(self):
        """
        Start consuming messages from the queue.
        This is a blocking call.
        """
        try:
            self._connect()
            self._declare_topology()
            
            logger.info(f"Starting consumer. Waiting for messages on queue: {self.MAIN_QUEUE}")
            
            # Register callback
            self.channel.basic_consume(
                queue=self.MAIN_QUEUE,
                on_message_callback=self._process_message,
                auto_ack=False  # Manual ACK for transaction control
            )
            
            # Start consuming (blocking)
            self.channel.start_consuming()
            
        except KeyboardInterrupt:
            logger.info("Consumer stopped by user")
            self.stop_consuming()
        except Exception as e:
            logger.error(f"Consumer error: {str(e)}")
            raise
    
    def stop_consuming(self):
        """Stop consuming and close connection."""
        try:
            if self.channel:
                self.channel.stop_consuming()
            if self.connection and not self.connection.is_closed:
                self.connection.close()
            logger.info("Consumer stopped and connection closed")
        except Exception as e:
            logger.error(f"Error stopping consumer: {str(e)}")


def main():
    """Main entry point for running the consumer."""
    consumer = RabbitMQConsumer()
    consumer.start_consuming()


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    main()
