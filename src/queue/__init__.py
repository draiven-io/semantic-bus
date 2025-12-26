"""
Task Queue Module

Provides async task queuing for scaling agent request processing.

Components:
- TaskQueue: Abstract interface for task queue implementations
- InMemoryTaskQueue: Development/testing implementation
- RedisTaskQueue: Distributed Redis Streams implementation
- KafkaTaskQueue: High-throughput Kafka implementation
- RabbitMQTaskQueue: Reliable RabbitMQ implementation
- TaskWorker: Consumes and processes queued tasks

Environment Variables:
- TASK_QUEUE_BACKEND: Queue backend (memory, redis, kafka, rabbitmq)
- REDIS_URL: Redis connection URL
- KAFKA_BOOTSTRAP_SERVERS: Kafka broker addresses
- RABBITMQ_URL: RabbitMQ connection URL
"""

from src.queue.ports import (
    TaskPayload,
    TaskStatus,
    TaskPriority,
    TaskResult,
    TaskQueue,
    TaskQueueError,
    TaskNotFoundError,
    QueueFullError,
    QueueClosedError,
)
from src.queue.memory import InMemoryTaskQueue
from src.queue.worker import TaskWorker, create_agent_executor, WorkerStats
from src.queue.factory import create_task_queue, get_available_backends

# Conditionally export backend-specific queues
_optional_exports: list[str] = []

try:
    from src.queue.redis_queue import RedisTaskQueue
    _optional_exports.append("RedisTaskQueue")
except ImportError:
    pass

try:
    from src.queue.kafka_queue import KafkaTaskQueue
    _optional_exports.append("KafkaTaskQueue")
except ImportError:
    pass

try:
    from src.queue.rabbitmq_queue import RabbitMQTaskQueue
    _optional_exports.append("RabbitMQTaskQueue")
except ImportError:
    pass

__all__ = [
    # Port interfaces
    "TaskPayload",
    "TaskStatus",
    "TaskPriority",
    "TaskResult",
    "TaskQueue",
    "TaskQueueError",
    "TaskNotFoundError",
    "QueueFullError",
    "QueueClosedError",
    # Implementations (always available)
    "InMemoryTaskQueue",
    # Optional implementations
    *_optional_exports,
    # Worker
    "TaskWorker",
    "WorkerStats",
    "create_agent_executor",
    # Factory
    "create_task_queue",
    "get_available_backends",
]
