"""
RabbitMQ Task Queue Implementation

Distributed task queue using RabbitMQ for reliable message queuing.

Features:
- Priority queues with message priority support
- Dead letter exchanges for failed tasks
- Message TTL for automatic expiration
- Consumer acknowledgment for at-least-once delivery
- Delayed messages via dead letter routing
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, TYPE_CHECKING
from uuid import UUID

RABBITMQ_AVAILABLE = False
try:
    import aio_pika
    from aio_pika import Message, DeliveryMode, ExchangeType
    from aio_pika.abc import AbstractConnection, AbstractChannel, AbstractQueue
    RABBITMQ_AVAILABLE = True
except ImportError:
    pass

if TYPE_CHECKING:
    from aio_pika.abc import AbstractConnection, AbstractChannel, AbstractQueue

from src.queue.ports import (
    TaskPayload,
    TaskStatus,
    TaskPriority,
    TaskResult,
    TaskQueue,
    TaskQueueError,
    QueueClosedError,
)

logger = logging.getLogger(__name__)


class RabbitMQTaskQueue(TaskQueue):
    """
    RabbitMQ-based distributed task queue.
    
    Uses RabbitMQ for reliable, priority-based message queuing with
    dead letter handling and delayed retry support.
    
    Exchange/Queue naming:
    - {prefix}.tasks - Main task exchange (direct)
    - {prefix}.tasks.{priority} - Priority queues
    - {prefix}.dlx - Dead letter exchange
    - {prefix}.dlq - Dead letter queue
    - {prefix}.delay - Delay exchange for retries
    """
    
    def __init__(
        self,
        rabbitmq_url: str = "amqp://guest:guest@localhost:5672/",
        queue_prefix: str = "lip",
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 60.0,
        prefetch_count: int = 10,
        message_ttl_ms: int = 300000,  # 5 minutes default
    ):
        """
        Initialize RabbitMQ queue.
        
        Args:
            rabbitmq_url: RabbitMQ connection URL (AMQP)
            queue_prefix: Prefix for exchange/queue names
            max_retries: Default max retry attempts
            retry_base_delay: Base delay for retry backoff
            retry_max_delay: Maximum retry delay
            prefetch_count: Number of messages to prefetch per consumer
            message_ttl_ms: Default message TTL in milliseconds
        """
        if not RABBITMQ_AVAILABLE:
            raise ImportError(
                "RabbitMQ task queue requires aio-pika. "
                "Install with: pip install semantic-bus[rabbitmq]"
            )
        
        self._url = rabbitmq_url
        self._prefix = queue_prefix
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        self._prefetch_count = prefetch_count
        self._message_ttl_ms = message_ttl_ms
        
        self._connection: AbstractConnection | None = None
        self._channel: AbstractChannel | None = None
        self._queues: dict[TaskPriority, AbstractQueue] = {}
        self._closed = False
        self._lock = asyncio.Lock()
        
        # Local caches for task tracking
        self._tasks: dict[UUID, TaskPayload] = {}
        self._results: dict[UUID, TaskResult] = {}
        self._pending_messages: dict[UUID, Any] = {}  # Store for ack/nack
    
    def _queue_name(self, priority: TaskPriority) -> str:
        """Get queue name for a priority level."""
        return f"{self._prefix}.tasks.{priority.name.lower()}"
    
    def _exchange_name(self) -> str:
        """Get main exchange name."""
        return f"{self._prefix}.tasks"
    
    def _dlx_name(self) -> str:
        """Get dead letter exchange name."""
        return f"{self._prefix}.dlx"
    
    def _delay_exchange_name(self) -> str:
        """Get delay exchange name."""
        return f"{self._prefix}.delay"
    
    async def _ensure_connected(self) -> tuple[AbstractConnection, AbstractChannel]:
        """Ensure RabbitMQ connection and channel are established."""
        if self._connection is None or self._connection.is_closed:
            self._connection = await aio_pika.connect_robust(self._url)
            logger.info(f"RabbitMQ connected to {self._url}")
        
        if self._channel is None or self._channel.is_closed:
            self._channel = await self._connection.channel()
            await self._channel.set_qos(prefetch_count=self._prefetch_count)
            
            # Declare exchanges
            tasks_exchange = await self._channel.declare_exchange(
                self._exchange_name(),
                ExchangeType.DIRECT,
                durable=True,
            )
            
            dlx = await self._channel.declare_exchange(
                self._dlx_name(),
                ExchangeType.DIRECT,
                durable=True,
            )
            
            delay_exchange = await self._channel.declare_exchange(
                self._delay_exchange_name(),
                ExchangeType.DIRECT,
                durable=True,
            )
            
            # Declare DLQ
            dlq = await self._channel.declare_queue(
                f"{self._prefix}.dlq",
                durable=True,
            )
            await dlq.bind(dlx, routing_key="dead")
            
            # Declare priority queues
            for priority in TaskPriority:
                queue_name = self._queue_name(priority)
                
                queue = await self._channel.declare_queue(
                    queue_name,
                    durable=True,
                    arguments={
                        "x-dead-letter-exchange": self._dlx_name(),
                        "x-dead-letter-routing-key": "dead",
                        "x-message-ttl": self._message_ttl_ms,
                        "x-max-priority": 10,  # Enable priority
                    },
                )
                await queue.bind(tasks_exchange, routing_key=priority.name.lower())
                self._queues[priority] = queue
            
            logger.info("RabbitMQ exchanges and queues declared")
        
        return self._connection, self._channel
    
    def _serialize_task(self, task: TaskPayload) -> bytes:
        """Serialize task to JSON bytes."""
        data = {
            "task_id": str(task.task_id),
            "tenant_id": task.tenant_id,
            "target_agent_id": str(task.target_agent_id) if task.target_agent_id else None,
            "target_conn_id": task.target_conn_id,
            "message_type": task.message_type,
            "envelope_json": task.envelope_json,
            "session_id": str(task.session_id) if task.session_id else None,
            "exec_id": str(task.exec_id) if task.exec_id else None,
            "priority": task.priority.value,
            "created_at": task.created_at.isoformat(),
            "ttl_seconds": task.ttl_seconds,
            "retry_count": task.retry_count,
            "max_retries": task.max_retries,
            "correlation_id": str(task.correlation_id) if task.correlation_id else None,
            "metadata": task.metadata,
        }
        return json.dumps(data).encode("utf-8")
    
    def _deserialize_task(self, body: bytes) -> TaskPayload:
        """Deserialize task from JSON bytes."""
        data = json.loads(body.decode("utf-8"))
        return TaskPayload(
            task_id=UUID(data["task_id"]),
            tenant_id=data["tenant_id"],
            target_agent_id=UUID(data["target_agent_id"]) if data.get("target_agent_id") else None,
            target_conn_id=data.get("target_conn_id"),
            message_type=data["message_type"],
            envelope_json=data["envelope_json"],
            session_id=UUID(data["session_id"]) if data.get("session_id") else None,
            exec_id=UUID(data["exec_id"]) if data.get("exec_id") else None,
            priority=TaskPriority(data["priority"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            ttl_seconds=data["ttl_seconds"],
            retry_count=data["retry_count"],
            max_retries=data["max_retries"],
            correlation_id=UUID(data["correlation_id"]) if data.get("correlation_id") else None,
            metadata=data.get("metadata", {}),
        )
    
    async def enqueue(
        self,
        task: TaskPayload,
        delay_seconds: float = 0.0
    ) -> TaskPayload:
        """Add a task to the queue."""
        if self._closed:
            raise QueueClosedError("Queue is shutdown")
        
        _, channel = await self._ensure_connected()
        
        exchange = await channel.get_exchange(self._exchange_name())
        
        # Calculate message priority (RabbitMQ uses 0-255, we map our enum)
        # Higher RabbitMQ priority = processed first
        rmq_priority = 10 - task.priority.value * 2  # CRITICAL=10, LOW=2
        
        # Calculate expiration
        expiration_ms = task.ttl_seconds * 1000
        
        message = Message(
            body=self._serialize_task(task),
            delivery_mode=DeliveryMode.PERSISTENT,
            priority=rmq_priority,
            expiration=expiration_ms,
            message_id=str(task.task_id),
            correlation_id=str(task.correlation_id) if task.correlation_id else None,
            headers={
                "tenant_id": task.tenant_id,
                "retry_count": task.retry_count,
            },
        )
        
        if delay_seconds > 0:
            # For delayed messages, publish to delay exchange with TTL
            delay_queue_name = f"{self._prefix}.delay.{int(delay_seconds * 1000)}"
            
            # Declare a temporary delay queue that routes to main exchange after TTL
            delay_queue = await channel.declare_queue(
                delay_queue_name,
                durable=True,
                arguments={
                    "x-dead-letter-exchange": self._exchange_name(),
                    "x-dead-letter-routing-key": task.priority.name.lower(),
                    "x-message-ttl": int(delay_seconds * 1000),
                    "x-expires": int(delay_seconds * 1000) + 60000,  # Cleanup after
                },
            )
            
            delay_exchange = await channel.get_exchange(self._delay_exchange_name())
            await delay_queue.bind(delay_exchange, routing_key=delay_queue_name)
            
            await delay_exchange.publish(message, routing_key=delay_queue_name)
        else:
            await exchange.publish(message, routing_key=task.priority.name.lower())
        
        self._tasks[task.task_id] = task
        
        logger.debug(f"Task enqueued to RabbitMQ: {task.task_id}")
        return task
    
    async def dequeue(
        self,
        tenant_id: str | None = None,
        timeout: float = 0.0
    ) -> TaskPayload | None:
        """Get the next task from the queue."""
        if self._closed:
            return None
        
        await self._ensure_connected()
        
        # Try queues in priority order
        for priority in TaskPriority:
            queue = self._queues.get(priority)
            if not queue:
                continue
            
            try:
                message = await asyncio.wait_for(
                    queue.get(timeout=timeout if timeout > 0 else 0.1, fail=False),
                    timeout=timeout + 0.5 if timeout > 0 else 1.0
                )
                
                if message is None:
                    continue
                
                task = self._deserialize_task(message.body)
                
                # Check tenant filter
                if tenant_id and task.tenant_id != tenant_id:
                    # Reject and requeue
                    await message.reject(requeue=True)
                    continue
                
                # Check expiration
                if task.is_expired():
                    await message.reject(requeue=False)  # Send to DLQ
                    self._results[task.task_id] = TaskResult(
                        task_id=task.task_id,
                        status=TaskStatus.EXPIRED,
                        error_code="TASK_EXPIRED",
                        error_message="Task exceeded TTL",
                    )
                    continue
                
                # Store message for later ack
                self._pending_messages[task.task_id] = message
                self._tasks[task.task_id] = task
                
                logger.debug(f"Task claimed from RabbitMQ: {task.task_id}")
                return task
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"RabbitMQ dequeue error: {e}")
                continue
        
        return None
    
    async def complete(
        self,
        task_id: UUID,
        result: TaskResult
    ) -> bool:
        """Mark a task as completed."""
        message = self._pending_messages.pop(task_id, None)
        if message is None:
            return False
        
        await message.ack()
        
        self._results[task_id] = result
        self._tasks.pop(task_id, None)
        
        logger.debug(f"Task completed in RabbitMQ: {task_id}")
        return True
    
    async def fail(
        self,
        task_id: UUID,
        error_code: str,
        error_message: str,
        retry: bool = True
    ) -> bool:
        """Mark a task as failed, optionally re-enqueuing for retry."""
        message = self._pending_messages.pop(task_id, None)
        task = self._tasks.get(task_id)
        
        if message is None or task is None:
            return False
        
        await message.ack()  # Ack original to avoid redelivery
        
        if retry and task.can_retry():
            task.retry_count += 1
            
            # Calculate backoff delay
            delay = min(
                self._retry_base_delay * (2 ** task.retry_count),
                self._retry_max_delay
            )
            
            # Re-enqueue with delay
            self._tasks.pop(task_id, None)
            await self.enqueue(task, delay_seconds=delay)
            
            logger.info(f"Task {task_id} scheduled for retry #{task.retry_count}")
            return True
        
        # No retry - already goes to DLQ via dead letter exchange
        self._results[task_id] = TaskResult(
            task_id=task_id,
            status=TaskStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
            retry_count=task.retry_count if task else 0,
        )
        
        self._tasks.pop(task_id, None)
        
        logger.warning(f"Task failed permanently: {task_id} - {error_message}")
        return True
    
    async def get(self, task_id: UUID) -> TaskPayload | None:
        """Get a task by ID."""
        return self._tasks.get(task_id)
    
    async def get_result(self, task_id: UUID) -> TaskResult | None:
        """Get the result of a completed task."""
        return self._results.get(task_id)
    
    async def cancel(self, task_id: UUID) -> bool:
        """Cancel a pending task."""
        message = self._pending_messages.pop(task_id, None)
        task = self._tasks.pop(task_id, None)
        
        if message is None:
            return False
        
        await message.reject(requeue=False)
        
        self._results[task_id] = TaskResult(
            task_id=task_id,
            status=TaskStatus.CANCELLED,
            error_code="TASK_CANCELLED",
            error_message="Task cancelled by user",
        )
        
        logger.debug(f"Task cancelled: {task_id}")
        return True
    
    async def size(self, tenant_id: str | None = None) -> int:
        """Get the number of pending tasks."""
        if tenant_id:
            return sum(
                1 for t in self._tasks.values()
                if t.tenant_id == tenant_id
            )
        
        # Get queue sizes from RabbitMQ
        total = 0
        for queue in self._queues.values():
            if queue:
                total += queue.declaration_result.message_count
        return total
    
    async def pending_by_agent(self, agent_id: UUID) -> int:
        """Get count of pending tasks for a specific agent."""
        return sum(
            1 for t in self._tasks.values()
            if t.target_agent_id == agent_id
        )
    
    async def clear_expired(self) -> int:
        """Remove expired tasks."""
        # RabbitMQ handles expiration via TTL
        count = 0
        for task_id, task in list(self._tasks.items()):
            if task.is_expired():
                message = self._pending_messages.pop(task_id, None)
                if message:
                    await message.reject(requeue=False)
                self._tasks.pop(task_id, None)
                self._results[task_id] = TaskResult(
                    task_id=task_id,
                    status=TaskStatus.EXPIRED,
                    error_code="TASK_EXPIRED",
                    error_message="Task exceeded TTL",
                )
                count += 1
        return count
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the queue."""
        self._closed = True
        
        # Reject any pending messages to allow redelivery
        for message in self._pending_messages.values():
            try:
                await message.reject(requeue=True)
            except Exception:
                pass
        self._pending_messages.clear()
        
        if self._channel and not self._channel.is_closed:
            await self._channel.close()
            self._channel = None
        
        if self._connection and not self._connection.is_closed:
            await self._connection.close()
            self._connection = None
        
        logger.info("RabbitMQ queue shutdown complete")
