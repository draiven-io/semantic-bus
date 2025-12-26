"""
Kafka Task Queue Implementation

Distributed task queue using Apache Kafka for high-throughput streaming.

Features:
- Kafka topics for reliable task delivery
- Consumer groups for horizontal scaling
- Partition-based ordering (per tenant or agent)
- At-least-once delivery semantics
- Configurable retention and compaction
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, TYPE_CHECKING
from uuid import UUID

KAFKA_AVAILABLE = False
try:
    from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
    from aiokafka.errors import KafkaError
    KAFKA_AVAILABLE = True
except ImportError:
    pass

if TYPE_CHECKING:
    from aiokafka import AIOKafkaProducer, AIOKafkaConsumer

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


class KafkaTaskQueue(TaskQueue):
    """
    Kafka-based distributed task queue.
    
    Uses Kafka topics for high-throughput, durable task delivery.
    Tasks are partitioned by tenant_id for ordering guarantees.
    
    Topic naming:
    - {prefix}.tasks.{priority} - Task topics by priority
    - {prefix}.results - Completed task results
    - {prefix}.dlq - Dead letter queue for failed tasks
    """
    
    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        topic_prefix: str = "lip",
        consumer_group: str = "bus_workers",
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 60.0,
        result_retention_ms: int = 3600000,  # 1 hour
    ):
        """
        Initialize Kafka queue.
        
        Args:
            bootstrap_servers: Kafka broker addresses (comma-separated)
            topic_prefix: Prefix for topic names
            consumer_group: Consumer group for this queue
            max_retries: Default max retry attempts
            retry_base_delay: Base delay for retry backoff
            retry_max_delay: Maximum retry delay
            result_retention_ms: How long to keep results
        """
        if not KAFKA_AVAILABLE:
            raise ImportError(
                "Kafka task queue requires aiokafka. "
                "Install with: pip install semantic-bus[kafka]"
            )
        
        self._bootstrap_servers = bootstrap_servers
        self._prefix = topic_prefix
        self._group = consumer_group
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        self._result_retention_ms = result_retention_ms
        
        self._producer: AIOKafkaProducer | None = None
        self._consumer: AIOKafkaConsumer | None = None
        self._closed = False
        self._lock = asyncio.Lock()
        
        # Local caches for task tracking
        self._tasks: dict[UUID, TaskPayload] = {}
        self._results: dict[UUID, TaskResult] = {}
        self._pending_count = 0
    
    def _topic_name(self, priority: TaskPriority) -> str:
        """Get topic name for a priority level."""
        return f"{self._prefix}.tasks.{priority.name.lower()}"
    
    def _results_topic(self) -> str:
        """Get results topic name."""
        return f"{self._prefix}.results"
    
    def _dlq_topic(self) -> str:
        """Get dead letter queue topic name."""
        return f"{self._prefix}.dlq"
    
    async def _ensure_producer(self) -> AIOKafkaProducer:
        """Ensure Kafka producer is connected."""
        if self._producer is None:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
            )
            await self._producer.start()
            logger.info(f"Kafka producer connected to {self._bootstrap_servers}")
        return self._producer
    
    async def _ensure_consumer(self) -> AIOKafkaConsumer:
        """Ensure Kafka consumer is connected."""
        if self._consumer is None:
            # Subscribe to all priority topics
            topics = [self._topic_name(p) for p in TaskPriority]
            
            self._consumer = AIOKafkaConsumer(
                *topics,
                bootstrap_servers=self._bootstrap_servers,
                group_id=self._group,
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
                key_deserializer=lambda k: k.decode("utf-8") if k else None,
                enable_auto_commit=False,  # Manual commit for at-least-once
                auto_offset_reset="earliest",
            )
            await self._consumer.start()
            logger.info(f"Kafka consumer connected, group={self._group}")
        return self._consumer
    
    def _serialize_task(self, task: TaskPayload) -> dict[str, Any]:
        """Serialize task to JSON-compatible dict."""
        return {
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
    
    def _deserialize_task(self, data: dict[str, Any]) -> TaskPayload:
        """Deserialize task from JSON dict."""
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
        
        producer = await self._ensure_producer()
        
        # Use tenant_id as partition key for ordering
        topic = self._topic_name(task.priority)
        key = task.tenant_id
        value = self._serialize_task(task)
        
        # Add delay info if specified
        if delay_seconds > 0:
            value["_delay_until"] = (
                datetime.utcnow().timestamp() + delay_seconds
            )
        
        try:
            await producer.send_and_wait(topic, value=value, key=key)
            self._tasks[task.task_id] = task
            self._pending_count += 1
            
            logger.debug(f"Task enqueued to Kafka: {task.task_id} -> {topic}")
            return task
            
        except KafkaError as e:
            raise TaskQueueError(f"Failed to enqueue task: {e}") from e
    
    async def dequeue(
        self,
        tenant_id: str | None = None,
        timeout: float = 0.0
    ) -> TaskPayload | None:
        """Get the next task from the queue."""
        if self._closed:
            return None
        
        consumer = await self._ensure_consumer()
        
        timeout_ms = int(timeout * 1000) if timeout > 0 else 100
        
        try:
            # Poll for messages
            records = await consumer.getmany(timeout_ms=timeout_ms, max_records=1)
            
            for tp, messages in records.items():
                for msg in messages:
                    task = self._deserialize_task(msg.value)
                    
                    # Check delay
                    delay_until = msg.value.get("_delay_until")
                    if delay_until and datetime.utcnow().timestamp() < delay_until:
                        # Task not ready yet - in production, use a scheduler
                        # For now, skip and it will be redelivered
                        continue
                    
                    # Check tenant filter
                    if tenant_id and task.tenant_id != tenant_id:
                        continue
                    
                    # Check expiration
                    if task.is_expired():
                        await self._mark_expired(task)
                        await consumer.commit()
                        continue
                    
                    # Store offset info for later commit
                    task.metadata["_kafka_tp"] = (tp.topic, tp.partition)
                    task.metadata["_kafka_offset"] = msg.offset
                    
                    self._tasks[task.task_id] = task
                    
                    logger.debug(f"Task claimed from Kafka: {task.task_id}")
                    return task
            
            return None
            
        except KafkaError as e:
            logger.error(f"Kafka consumer error: {e}")
            return None
    
    async def _mark_expired(self, task: TaskPayload) -> None:
        """Mark a task as expired."""
        self._results[task.task_id] = TaskResult(
            task_id=task.task_id,
            status=TaskStatus.EXPIRED,
            error_code="TASK_EXPIRED",
            error_message=f"Task exceeded TTL of {task.ttl_seconds}s"
        )
        if task.task_id in self._tasks:
            del self._tasks[task.task_id]
        self._pending_count = max(0, self._pending_count - 1)
    
    async def complete(
        self,
        task_id: UUID,
        result: TaskResult
    ) -> bool:
        """Mark a task as completed."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        
        consumer = await self._ensure_consumer()
        
        # Commit the offset
        await consumer.commit()
        
        # Store result
        self._results[task_id] = result
        
        # Optionally publish to results topic
        producer = await self._ensure_producer()
        await producer.send_and_wait(
            self._results_topic(),
            value={
                "task_id": str(task_id),
                "status": result.status.value,
                "result": result.result,
                "processed_at": result.processed_at.isoformat(),
            },
            key=str(task_id),
        )
        
        del self._tasks[task_id]
        self._pending_count = max(0, self._pending_count - 1)
        
        logger.debug(f"Task completed in Kafka: {task_id}")
        return True
    
    async def fail(
        self,
        task_id: UUID,
        error_code: str,
        error_message: str,
        retry: bool = True
    ) -> bool:
        """Mark a task as failed, optionally re-enqueuing for retry."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        
        consumer = await self._ensure_consumer()
        await consumer.commit()
        
        if retry and task.can_retry():
            task.retry_count += 1
            
            # Calculate backoff delay
            delay = min(
                self._retry_base_delay * (2 ** task.retry_count),
                self._retry_max_delay
            )
            
            # Re-enqueue with delay
            del self._tasks[task_id]
            await self.enqueue(task, delay_seconds=delay)
            
            logger.info(f"Task {task_id} scheduled for retry #{task.retry_count}")
            return True
        
        # No retry - send to DLQ
        producer = await self._ensure_producer()
        await producer.send_and_wait(
            self._dlq_topic(),
            value={
                "task": self._serialize_task(task),
                "error_code": error_code,
                "error_message": error_message,
                "failed_at": datetime.utcnow().isoformat(),
            },
            key=str(task_id),
        )
        
        self._results[task_id] = TaskResult(
            task_id=task_id,
            status=TaskStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
            retry_count=task.retry_count,
        )
        
        del self._tasks[task_id]
        self._pending_count = max(0, self._pending_count - 1)
        
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
        task = self._tasks.get(task_id)
        if not task:
            return False
        
        self._results[task_id] = TaskResult(
            task_id=task_id,
            status=TaskStatus.CANCELLED,
            error_code="TASK_CANCELLED",
            error_message="Task cancelled by user",
        )
        
        del self._tasks[task_id]
        self._pending_count = max(0, self._pending_count - 1)
        
        logger.debug(f"Task cancelled: {task_id}")
        return True
    
    async def size(self, tenant_id: str | None = None) -> int:
        """Get the number of pending tasks."""
        # Note: This is approximate - Kafka doesn't provide exact counts easily
        if tenant_id:
            return sum(
                1 for t in self._tasks.values()
                if t.tenant_id == tenant_id
            )
        return self._pending_count
    
    async def pending_by_agent(self, agent_id: UUID) -> int:
        """Get count of pending tasks for a specific agent."""
        return sum(
            1 for t in self._tasks.values()
            if t.target_agent_id == agent_id
        )
    
    async def clear_expired(self) -> int:
        """Remove expired tasks from tracking."""
        count = 0
        for task_id, task in list(self._tasks.items()):
            if task.is_expired():
                await self._mark_expired(task)
                count += 1
        return count
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the queue."""
        self._closed = True
        
        if self._producer:
            await self._producer.stop()
            self._producer = None
        
        if self._consumer:
            await self._consumer.stop()
            self._consumer = None
        
        logger.info("Kafka queue shutdown complete")
