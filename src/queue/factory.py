"""
Task Queue Factory

Factory function to create the appropriate task queue implementation
based on configuration or environment variables.

Supported backends:
- memory: In-memory queue for development/testing
- redis: Redis Streams for distributed deployments
- kafka: Apache Kafka for high-throughput streaming
- rabbitmq: RabbitMQ for reliable message queuing

Environment Variables:
- TASK_QUEUE_BACKEND: Queue backend type (memory, redis, kafka, rabbitmq)
- REDIS_URL: Redis connection URL (for redis backend)
- KAFKA_BOOTSTRAP_SERVERS: Kafka broker addresses (for kafka backend)
- RABBITMQ_URL: RabbitMQ connection URL (for rabbitmq backend)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from src.queue.ports import TaskQueue
from src.queue.memory import InMemoryTaskQueue

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Valid backend types
VALID_BACKENDS = {"memory", "redis", "kafka", "rabbitmq"}


def create_task_queue(
    backend: str | None = None,
    # Connection URLs (auto-detected from env if not provided)
    redis_url: str | None = None,
    kafka_bootstrap_servers: str | None = None,
    rabbitmq_url: str | None = None,
    # Common options
    max_size: int = 10000,
    max_retries: int = 3,
    retry_base_delay: float = 1.0,
    retry_max_delay: float = 60.0,
    # Backend-specific options
    **kwargs
) -> TaskQueue:
    """
    Create a task queue based on configuration or environment.
    
    Args:
        backend: Queue backend type ("memory", "redis", "kafka", "rabbitmq").
                 Auto-detects from TASK_QUEUE_BACKEND env var if not specified.
        redis_url: Redis connection URL. Auto-detects from REDIS_URL env var.
        kafka_bootstrap_servers: Kafka brokers. Auto-detects from KAFKA_BOOTSTRAP_SERVERS env var.
        rabbitmq_url: RabbitMQ URL. Auto-detects from RABBITMQ_URL env var.
        max_size: Maximum queue size (for memory backend)
        max_retries: Default max retry attempts
        retry_base_delay: Base delay for retry backoff (seconds)
        retry_max_delay: Maximum retry delay (seconds)
        **kwargs: Additional backend-specific options
        
    Returns:
        Configured TaskQueue instance
        
    Raises:
        ValueError: If backend is invalid
        ImportError: If required dependencies are not installed
        
    Environment Variables:
        TASK_QUEUE_BACKEND: Queue backend type
        REDIS_URL: Redis connection URL (default: redis://localhost:6379)
        KAFKA_BOOTSTRAP_SERVERS: Kafka brokers (default: localhost:9092)
        RABBITMQ_URL: RabbitMQ URL (default: amqp://guest:guest@localhost:5672/)
    
    Examples:
        # Auto-detect from environment
        queue = create_task_queue()
        
        # Explicit backend
        queue = create_task_queue(backend="redis", redis_url="redis://myhost:6379")
        
        # Kafka with custom options
        queue = create_task_queue(
            backend="kafka",
            kafka_bootstrap_servers="kafka1:9092,kafka2:9092",
            topic_prefix="myapp",
        )
    """
    # Auto-detect from environment
    if backend is None:
        backend = os.getenv("TASK_QUEUE_BACKEND", "memory").lower()
    
    backend = backend.lower()
    
    if backend not in VALID_BACKENDS:
        raise ValueError(
            f"Unknown task queue backend: {backend}. "
            f"Valid options: {', '.join(sorted(VALID_BACKENDS))}"
        )
    
    # Read connection URLs from environment if not provided
    if redis_url is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    if kafka_bootstrap_servers is None:
        kafka_bootstrap_servers = os.getenv(
            "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
        )
    
    if rabbitmq_url is None:
        rabbitmq_url = os.getenv(
            "RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"
        )
    
    # Create the appropriate queue implementation
    if backend == "memory":
        logger.info(f"Creating in-memory task queue (max_size={max_size})")
        return InMemoryTaskQueue(
            max_size=max_size,
            retry_base_delay=retry_base_delay,
            retry_max_delay=retry_max_delay,
        )
    
    elif backend == "redis":
        try:
            from src.queue.redis_queue import RedisTaskQueue
        except ImportError as e:
            raise ImportError(
                "Redis task queue requires the redis package. "
                "Install with: pip install semantic-bus[redis]"
            ) from e
        
        logger.info(f"Creating Redis task queue (url={redis_url})")
        return RedisTaskQueue(
            redis_url=redis_url,
            key_prefix=kwargs.get("key_prefix", "lip"),
            consumer_group=kwargs.get("consumer_group", "bus_workers"),
            consumer_name=kwargs.get("consumer_name"),
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
            retry_max_delay=retry_max_delay,
            result_ttl_seconds=kwargs.get("result_ttl_seconds", 3600),
        )
    
    elif backend == "kafka":
        try:
            from src.queue.kafka_queue import KafkaTaskQueue
        except ImportError as e:
            raise ImportError(
                "Kafka task queue requires aiokafka. "
                "Install with: pip install semantic-bus[kafka]"
            ) from e
        
        logger.info(f"Creating Kafka task queue (brokers={kafka_bootstrap_servers})")
        return KafkaTaskQueue(
            bootstrap_servers=kafka_bootstrap_servers,
            topic_prefix=kwargs.get("topic_prefix", "lip"),
            consumer_group=kwargs.get("consumer_group", "bus_workers"),
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
            retry_max_delay=retry_max_delay,
            result_retention_ms=kwargs.get("result_retention_ms", 3600000),
        )
    
    elif backend == "rabbitmq":
        try:
            from src.queue.rabbitmq_queue import RabbitMQTaskQueue
        except ImportError as e:
            raise ImportError(
                "RabbitMQ task queue requires aio-pika. "
                "Install with: pip install semantic-bus[rabbitmq]"
            ) from e
        
        logger.info(f"Creating RabbitMQ task queue (url={rabbitmq_url})")
        return RabbitMQTaskQueue(
            rabbitmq_url=rabbitmq_url,
            queue_prefix=kwargs.get("queue_prefix", "lip"),
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
            retry_max_delay=retry_max_delay,
            prefetch_count=kwargs.get("prefetch_count", 10),
            message_ttl_ms=kwargs.get("message_ttl_ms", 300000),
        )
    
    # Should never reach here due to validation above
    raise ValueError(f"Unknown backend: {backend}")


def get_available_backends() -> list[str]:
    """
    Get list of available queue backends.
    
    Returns:
        List of backend names that can be used (dependencies installed)
    """
    available = ["memory"]  # Always available
    
    try:
        import redis.asyncio  # noqa: F401
        available.append("redis")
    except ImportError:
        pass
    
    try:
        import aiokafka  # noqa: F401
        available.append("kafka")
    except ImportError:
        pass
    
    try:
        import aio_pika  # noqa: F401
        available.append("rabbitmq")
    except ImportError:
        pass
    
    return available

