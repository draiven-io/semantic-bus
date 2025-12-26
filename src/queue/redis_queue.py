"""
Redis Task Queue Implementation

Distributed task queue using Redis for scalable multi-node deployments.

Features:
- Redis Streams for reliable task delivery
- Consumer groups for load balancing across workers
- Automatic task expiration with TTL
- Priority queues using multiple streams
- Dead letter queue for failed tasks
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, TYPE_CHECKING
from uuid import UUID

REDIS_AVAILABLE = False
try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    aioredis = None  # type: ignore

if TYPE_CHECKING:
    from redis.asyncio import Redis as RedisType

from src.queue.ports import (
    TaskPayload,
    TaskStatus,
    TaskPriority,
    TaskResult,
    TaskQueue,
    TaskQueueError,
    TaskNotFoundError,
    QueueClosedError,
)

logger = logging.getLogger(__name__)


class RedisTaskQueue(TaskQueue):
    """
    Redis-based distributed task queue.
    
    Uses Redis Streams for reliable, ordered task delivery with
    consumer group support for horizontal scaling.
    
    Key schema:
    - lip:queue:{priority}  - Task streams by priority
    - lip:task:{task_id}    - Task payload hash
    - lip:status:{task_id}  - Task status
    - lip:result:{task_id}  - Task result (after completion)
    - lip:idx:tenant:{tenant_id} - Set of task IDs by tenant
    - lip:idx:agent:{agent_id}   - Set of task IDs by agent
    """
    
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        key_prefix: str = "lip",
        consumer_group: str = "bus_workers",
        consumer_name: str | None = None,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 60.0,
        result_ttl_seconds: int = 3600,  # Keep results for 1 hour
    ):
        """
        Initialize Redis queue.
        
        Args:
            redis_url: Redis connection URL
            key_prefix: Prefix for all Redis keys
            consumer_group: Consumer group name for this queue
            consumer_name: Unique name for this consumer (auto-generated if None)
            max_retries: Default max retry attempts
            retry_base_delay: Base delay for retry backoff
            retry_max_delay: Maximum retry delay
            result_ttl_seconds: How long to keep completed task results
        """
        if not REDIS_AVAILABLE:
            raise ImportError(
                "Redis package not installed. "
                "Install with: pip install semantic-bus[redis]"
            )
        
        self._redis_url = redis_url
        self._prefix = key_prefix
        self._group = consumer_group
        self._consumer = consumer_name or f"worker_{id(self)}"
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        self._result_ttl = result_ttl_seconds
        
        self._redis: RedisType | None = None
        self._closed = False
        self._lock = asyncio.Lock()
    
    async def _ensure_connected(self) -> RedisType:
        """Ensure Redis connection is established."""
        if self._redis is None:
            self._redis = aioredis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True
            )
            # Create consumer groups for each priority stream
            for priority in TaskPriority:
                stream_key = self._stream_key(priority)
                try:
                    await self._redis.xgroup_create(
                        stream_key,
                        self._group,
                        id="0",
                        mkstream=True
                    )
                except Exception as e:
                    if "BUSYGROUP" not in str(e):
                        raise
        return self._redis
    
    def _stream_key(self, priority: TaskPriority) -> str:
        """Get stream key for a priority level."""
        return f"{self._prefix}:queue:{priority.value}"
    
    def _task_key(self, task_id: UUID) -> str:
        """Get key for task payload."""
        return f"{self._prefix}:task:{task_id}"
    
    def _status_key(self, task_id: UUID) -> str:
        """Get key for task status."""
        return f"{self._prefix}:status:{task_id}"
    
    def _result_key(self, task_id: UUID) -> str:
        """Get key for task result."""
        return f"{self._prefix}:result:{task_id}"
    
    def _tenant_index_key(self, tenant_id: str) -> str:
        """Get key for tenant task index."""
        return f"{self._prefix}:idx:tenant:{tenant_id}"
    
    def _agent_index_key(self, agent_id: UUID) -> str:
        """Get key for agent task index."""
        return f"{self._prefix}:idx:agent:{agent_id}"
    
    def _serialize_task(self, task: TaskPayload) -> dict[str, str]:
        """Serialize task to Redis hash format."""
        return {
            "task_id": str(task.task_id),
            "tenant_id": task.tenant_id,
            "target_agent_id": str(task.target_agent_id) if task.target_agent_id else "",
            "target_conn_id": task.target_conn_id or "",
            "message_type": task.message_type,
            "envelope_json": task.envelope_json,
            "session_id": str(task.session_id) if task.session_id else "",
            "exec_id": str(task.exec_id) if task.exec_id else "",
            "priority": str(task.priority.value),
            "created_at": task.created_at.isoformat(),
            "ttl_seconds": str(task.ttl_seconds),
            "retry_count": str(task.retry_count),
            "max_retries": str(task.max_retries),
            "correlation_id": str(task.correlation_id) if task.correlation_id else "",
            "metadata": json.dumps(task.metadata),
        }
    
    def _deserialize_task(self, data: dict[str, str]) -> TaskPayload:
        """Deserialize task from Redis hash format."""
        return TaskPayload(
            task_id=UUID(data["task_id"]),
            tenant_id=data["tenant_id"],
            target_agent_id=UUID(data["target_agent_id"]) if data.get("target_agent_id") else None,
            target_conn_id=data.get("target_conn_id") or None,
            message_type=data["message_type"],
            envelope_json=data["envelope_json"],
            session_id=UUID(data["session_id"]) if data.get("session_id") else None,
            exec_id=UUID(data["exec_id"]) if data.get("exec_id") else None,
            priority=TaskPriority(int(data["priority"])),
            created_at=datetime.fromisoformat(data["created_at"]),
            ttl_seconds=int(data["ttl_seconds"]),
            retry_count=int(data["retry_count"]),
            max_retries=int(data["max_retries"]),
            correlation_id=UUID(data["correlation_id"]) if data.get("correlation_id") else None,
            metadata=json.loads(data.get("metadata", "{}")),
        )
    
    async def enqueue(
        self,
        task: TaskPayload,
        delay_seconds: float = 0.0
    ) -> TaskPayload:
        """Add a task to the queue."""
        if self._closed:
            raise QueueClosedError("Queue is shutdown")
        
        r = await self._ensure_connected()
        
        async with self._lock:
            # Store task payload
            task_data = self._serialize_task(task)
            await r.hset(self._task_key(task.task_id), mapping=task_data)
            
            # Set TTL on task key
            await r.expire(
                self._task_key(task.task_id),
                task.ttl_seconds + 60  # Extra minute buffer
            )
            
            # Set status
            await r.set(
                self._status_key(task.task_id),
                TaskStatus.PENDING.value,
                ex=task.ttl_seconds + 60
            )
            
            # Add to indexes
            await r.sadd(self._tenant_index_key(task.tenant_id), str(task.task_id))
            if task.target_agent_id:
                await r.sadd(self._agent_index_key(task.target_agent_id), str(task.task_id))
            
            # Add to stream (with optional delay using scheduled set)
            if delay_seconds > 0:
                # For delayed tasks, use a sorted set with score = execute time
                delayed_key = f"{self._prefix}:delayed"
                execute_at = datetime.utcnow().timestamp() + delay_seconds
                await r.zadd(delayed_key, {str(task.task_id): execute_at})
            else:
                # Add directly to stream
                stream_key = self._stream_key(task.priority)
                await r.xadd(
                    stream_key,
                    {"task_id": str(task.task_id)},
                    id="*"
                )
        
        logger.debug(f"Task enqueued to Redis: {task.task_id}")
        return task
    
    async def dequeue(
        self,
        tenant_id: str | None = None,
        timeout: float = 0.0
    ) -> TaskPayload | None:
        """Get the next task from the queue."""
        if self._closed:
            return None
        
        r = await self._ensure_connected()
        
        # Process any due delayed tasks first
        await self._process_delayed_tasks()
        
        # Read from streams in priority order
        block_ms = int(timeout * 1000) if timeout > 0 else None
        
        for priority in TaskPriority:
            stream_key = self._stream_key(priority)
            
            try:
                # Read from consumer group
                result = await r.xreadgroup(
                    self._group,
                    self._consumer,
                    {stream_key: ">"},
                    count=1,
                    block=block_ms
                )
                
                if not result:
                    continue
                
                # Parse result: [(stream_name, [(message_id, {fields})])]
                stream_name, messages = result[0]
                if not messages:
                    continue
                
                message_id, fields = messages[0]
                task_id = UUID(fields["task_id"])
                
                # Get task payload
                task_data = await r.hgetall(self._task_key(task_id))
                if not task_data:
                    # Task expired or deleted, ack and continue
                    await r.xack(stream_key, self._group, message_id)
                    continue
                
                task = self._deserialize_task(task_data)
                
                # Check tenant filter
                if tenant_id and task.tenant_id != tenant_id:
                    # Put back (nack would require pending entry handling)
                    # For now, skip - more sophisticated handling would use
                    # separate streams per tenant
                    continue
                
                # Check expiration
                if task.is_expired():
                    await self._mark_expired(task_id, message_id, stream_key)
                    continue
                
                # Update status
                await r.set(self._status_key(task_id), TaskStatus.PROCESSING.value)
                
                # Store message_id for later ack
                task.metadata["_redis_msg_id"] = message_id
                task.metadata["_redis_stream"] = stream_key
                
                logger.debug(f"Task claimed from Redis: {task_id}")
                return task
                
            except Exception as e:
                logger.error(f"Error reading from Redis stream: {e}")
                continue
        
        return None
    
    async def _process_delayed_tasks(self) -> None:
        """Move due delayed tasks to their streams."""
        r = await self._ensure_connected()
        delayed_key = f"{self._prefix}:delayed"
        now = datetime.utcnow().timestamp()
        
        # Get due tasks
        due_tasks = await r.zrangebyscore(delayed_key, 0, now, start=0, num=100)
        
        for task_id_str in due_tasks:
            task_id = UUID(task_id_str)
            task_data = await r.hgetall(self._task_key(task_id))
            
            if task_data:
                task = self._deserialize_task(task_data)
                stream_key = self._stream_key(task.priority)
                await r.xadd(stream_key, {"task_id": str(task_id)})
            
            await r.zrem(delayed_key, task_id_str)
    
    async def _mark_expired(
        self,
        task_id: UUID,
        message_id: str,
        stream_key: str
    ) -> None:
        """Mark a task as expired."""
        r = await self._ensure_connected()
        
        await r.set(self._status_key(task_id), TaskStatus.EXPIRED.value)
        await r.xack(stream_key, self._group, message_id)
        
        # Store result
        result = TaskResult(
            task_id=task_id,
            status=TaskStatus.EXPIRED,
            error_code="TASK_EXPIRED",
            error_message="Task exceeded TTL"
        )
        await r.hset(
            self._result_key(task_id),
            mapping={
                "status": result.status.value,
                "error_code": result.error_code or "",
                "error_message": result.error_message or "",
                "processed_at": result.processed_at.isoformat(),
            }
        )
        await r.expire(self._result_key(task_id), self._result_ttl)
        
        logger.debug(f"Task expired: {task_id}")
    
    async def complete(
        self,
        task_id: UUID,
        result: TaskResult
    ) -> bool:
        """Mark a task as completed."""
        r = await self._ensure_connected()
        
        task_data = await r.hgetall(self._task_key(task_id))
        if not task_data:
            return False
        
        task = self._deserialize_task(task_data)
        
        # Acknowledge message in stream
        msg_id = task.metadata.get("_redis_msg_id")
        stream_key = task.metadata.get("_redis_stream")
        if msg_id and stream_key:
            await r.xack(stream_key, self._group, msg_id)
        
        # Update status
        await r.set(self._status_key(task_id), TaskStatus.COMPLETED.value)
        
        # Store result
        await r.hset(
            self._result_key(task_id),
            mapping={
                "status": result.status.value,
                "result": json.dumps(result.result) if result.result else "",
                "processed_at": result.processed_at.isoformat(),
                "duration_ms": str(result.duration_ms),
            }
        )
        await r.expire(self._result_key(task_id), self._result_ttl)
        
        # Clean up indexes
        await r.srem(self._tenant_index_key(task.tenant_id), str(task_id))
        if task.target_agent_id:
            await r.srem(self._agent_index_key(task.target_agent_id), str(task_id))
        
        logger.debug(f"Task completed in Redis: {task_id}")
        return True
    
    async def fail(
        self,
        task_id: UUID,
        error_code: str,
        error_message: str,
        retry: bool = True
    ) -> bool:
        """Mark a task as failed, optionally re-enqueuing for retry."""
        r = await self._ensure_connected()
        
        task_data = await r.hgetall(self._task_key(task_id))
        if not task_data:
            return False
        
        task = self._deserialize_task(task_data)
        
        # Ack the current message
        msg_id = task.metadata.pop("_redis_msg_id", None)
        stream_key = task.metadata.pop("_redis_stream", None)
        if msg_id and stream_key:
            await r.xack(stream_key, self._group, msg_id)
        
        # Check if we should retry
        if retry and task.can_retry():
            task.retry_count += 1
            
            # Calculate backoff delay
            delay = min(
                self._retry_base_delay * (2 ** task.retry_count),
                self._retry_max_delay
            )
            
            # Update task data
            task_data = self._serialize_task(task)
            await r.hset(self._task_key(task_id), mapping=task_data)
            
            # Re-enqueue with delay
            await r.set(self._status_key(task_id), TaskStatus.PENDING.value)
            delayed_key = f"{self._prefix}:delayed"
            execute_at = datetime.utcnow().timestamp() + delay
            await r.zadd(delayed_key, {str(task_id): execute_at})
            
            logger.info(f"Task {task_id} scheduled for retry #{task.retry_count}")
            return True
        
        # No retry - mark as failed
        await r.set(self._status_key(task_id), TaskStatus.FAILED.value)
        
        # Store result
        await r.hset(
            self._result_key(task_id),
            mapping={
                "status": TaskStatus.FAILED.value,
                "error_code": error_code,
                "error_message": error_message,
                "retry_count": str(task.retry_count),
                "processed_at": datetime.utcnow().isoformat(),
            }
        )
        await r.expire(self._result_key(task_id), self._result_ttl)
        
        # Clean up indexes
        await r.srem(self._tenant_index_key(task.tenant_id), str(task_id))
        if task.target_agent_id:
            await r.srem(self._agent_index_key(task.target_agent_id), str(task_id))
        
        logger.warning(f"Task failed permanently: {task_id} - {error_message}")
        return True
    
    async def get(self, task_id: UUID) -> TaskPayload | None:
        """Get a task by ID."""
        r = await self._ensure_connected()
        task_data = await r.hgetall(self._task_key(task_id))
        if not task_data:
            return None
        return self._deserialize_task(task_data)
    
    async def get_result(self, task_id: UUID) -> TaskResult | None:
        """Get the result of a completed task."""
        r = await self._ensure_connected()
        result_data = await r.hgetall(self._result_key(task_id))
        if not result_data:
            return None
        
        return TaskResult(
            task_id=task_id,
            status=TaskStatus(result_data["status"]),
            result=json.loads(result_data["result"]) if result_data.get("result") else None,
            error_code=result_data.get("error_code") or None,
            error_message=result_data.get("error_message") or None,
            processed_at=datetime.fromisoformat(result_data["processed_at"]),
            duration_ms=float(result_data.get("duration_ms", 0)),
            retry_count=int(result_data.get("retry_count", 0)),
        )
    
    async def cancel(self, task_id: UUID) -> bool:
        """Cancel a pending task."""
        r = await self._ensure_connected()
        
        status = await r.get(self._status_key(task_id))
        if status != TaskStatus.PENDING.value:
            return False
        
        await r.set(self._status_key(task_id), TaskStatus.CANCELLED.value)
        
        # Remove from delayed queue if present
        delayed_key = f"{self._prefix}:delayed"
        await r.zrem(delayed_key, str(task_id))
        
        # Store result
        await r.hset(
            self._result_key(task_id),
            mapping={
                "status": TaskStatus.CANCELLED.value,
                "error_code": "TASK_CANCELLED",
                "error_message": "Task cancelled by user",
                "processed_at": datetime.utcnow().isoformat(),
            }
        )
        await r.expire(self._result_key(task_id), self._result_ttl)
        
        logger.debug(f"Task cancelled: {task_id}")
        return True
    
    async def size(self, tenant_id: str | None = None) -> int:
        """Get the number of pending tasks."""
        r = await self._ensure_connected()
        
        if tenant_id:
            task_ids = await r.smembers(self._tenant_index_key(tenant_id))
            count = 0
            for tid in task_ids:
                status = await r.get(self._status_key(UUID(tid)))
                if status == TaskStatus.PENDING.value:
                    count += 1
            return count
        
        # Count across all priority streams
        total = 0
        for priority in TaskPriority:
            stream_key = self._stream_key(priority)
            info = await r.xinfo_stream(stream_key)
            total += info.get("length", 0)
        return total
    
    async def pending_by_agent(self, agent_id: UUID) -> int:
        """Get count of pending tasks for a specific agent."""
        r = await self._ensure_connected()
        
        task_ids = await r.smembers(self._agent_index_key(agent_id))
        count = 0
        for tid in task_ids:
            status = await r.get(self._status_key(UUID(tid)))
            if status == TaskStatus.PENDING.value:
                count += 1
        return count
    
    async def clear_expired(self) -> int:
        """Remove expired tasks from the queue."""
        # Redis handles expiration via TTL, so this is mostly a no-op
        # But we can clean up any stragglers in the delayed queue
        r = await self._ensure_connected()
        await self._process_delayed_tasks()
        return 0
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the queue."""
        self._closed = True
        if self._redis:
            await self._redis.close()
            self._redis = None
        logger.info("Redis queue shutdown complete")
