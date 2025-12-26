"""
In-Memory Task Queue Implementation

Thread-safe async implementation using asyncio primitives.
Suitable for development, testing, and single-node deployments.

Features:
- Priority queue ordering
- TTL expiration handling
- Retry logic with backoff
- Per-tenant and per-agent task tracking
"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from heapq import heappush, heappop
from typing import Any
from uuid import UUID

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

logger = logging.getLogger(__name__)


@dataclass(order=True)
class PriorityItem:
    """
    Wrapper for heap queue ordering.
    
    Ordered by: priority, then created_at (FIFO within priority).
    """
    priority: int
    created_at: datetime
    task: TaskPayload = field(compare=False)
    visible_at: datetime = field(compare=False, default_factory=datetime.utcnow)


class InMemoryTaskQueue(TaskQueue):
    """
    In-memory task queue implementation.
    
    Uses a priority heap for ordering and asyncio primitives for concurrency.
    
    Features:
    - Priority-based ordering
    - Delayed task visibility
    - Automatic TTL expiration
    - Retry with exponential backoff
    - Thread-safe async operations
    """
    
    def __init__(
        self,
        max_size: int = 10000,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 60.0
    ):
        """
        Initialize the in-memory queue.
        
        Args:
            max_size: Maximum queue capacity
            retry_base_delay: Base delay for retry backoff (seconds)
            retry_max_delay: Maximum retry delay (seconds)
        """
        self._max_size = max_size
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        
        # Task storage
        self._heap: list[PriorityItem] = []
        self._tasks: dict[UUID, TaskPayload] = {}
        self._status: dict[UUID, TaskStatus] = {}
        self._results: dict[UUID, TaskResult] = {}
        
        # Indexes for efficient lookups
        self._by_tenant: dict[str, set[UUID]] = defaultdict(set)
        self._by_agent: dict[UUID, set[UUID]] = defaultdict(set)
        
        # Concurrency control
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Condition(self._lock)
        self._closed = False
        
        # Statistics
        self._enqueued_count = 0
        self._completed_count = 0
        self._failed_count = 0
    
    async def enqueue(
        self,
        task: TaskPayload,
        delay_seconds: float = 0.0
    ) -> TaskPayload:
        """Add a task to the queue."""
        async with self._lock:
            if self._closed:
                raise QueueClosedError("Queue is shutdown")
            
            if len(self._tasks) >= self._max_size:
                raise QueueFullError(len(self._tasks), self._max_size)
            
            # Set visibility time for delayed tasks
            visible_at = datetime.utcnow()
            if delay_seconds > 0:
                visible_at += timedelta(seconds=delay_seconds)
            
            # Create priority item and add to heap
            item = PriorityItem(
                priority=task.priority.value,
                created_at=task.created_at,
                task=task,
                visible_at=visible_at
            )
            heappush(self._heap, item)
            
            # Store task and update indexes
            self._tasks[task.task_id] = task
            self._status[task.task_id] = TaskStatus.PENDING
            self._by_tenant[task.tenant_id].add(task.task_id)
            if task.target_agent_id:
                self._by_agent[task.target_agent_id].add(task.task_id)
            
            self._enqueued_count += 1
            
            # Signal waiters
            self._not_empty.notify()
            
            logger.debug(
                f"Task enqueued: {task.task_id} "
                f"(priority={task.priority.name}, tenant={task.tenant_id})"
            )
            
            return task
    
    async def dequeue(
        self,
        tenant_id: str | None = None,
        timeout: float = 0.0
    ) -> TaskPayload | None:
        """Get the next visible task from the queue."""
        deadline = None
        if timeout > 0:
            deadline = datetime.utcnow() + timedelta(seconds=timeout)
        
        async with self._not_empty:
            while True:
                if self._closed:
                    return None
                
                # Find next visible, non-expired task
                task = self._find_next_visible(tenant_id)
                if task:
                    return task
                
                # Wait for new tasks or timeout
                if deadline:
                    remaining = (deadline - datetime.utcnow()).total_seconds()
                    if remaining <= 0:
                        return None
                    try:
                        await asyncio.wait_for(
                            self._not_empty.wait(),
                            timeout=remaining
                        )
                    except asyncio.TimeoutError:
                        return None
                else:
                    # Non-blocking mode
                    return None
    
    def _find_next_visible(self, tenant_id: str | None) -> TaskPayload | None:
        """
        Find and claim the next visible task.
        
        Must be called with lock held.
        """
        now = datetime.utcnow()
        checked_items: list[PriorityItem] = []
        result: TaskPayload | None = None
        
        while self._heap:
            item = heappop(self._heap)
            task_id = item.task.task_id
            
            # Skip if task no longer exists (cancelled/completed)
            if task_id not in self._tasks:
                continue
            
            # Skip if not visible yet
            if item.visible_at > now:
                checked_items.append(item)
                continue
            
            # Skip if expired
            if item.task.is_expired():
                self._mark_expired(task_id)
                continue
            
            # Skip if wrong tenant
            if tenant_id and item.task.tenant_id != tenant_id:
                checked_items.append(item)
                continue
            
            # Skip if already being processed
            if self._status.get(task_id) != TaskStatus.PENDING:
                continue
            
            # Claim this task
            self._status[task_id] = TaskStatus.PROCESSING
            result = item.task
            
            logger.debug(f"Task claimed: {task_id}")
            break
        
        # Put back items we checked but didn't take
        for item in checked_items:
            heappush(self._heap, item)
        
        return result
    
    def _mark_expired(self, task_id: UUID) -> None:
        """Mark a task as expired and clean up."""
        self._status[task_id] = TaskStatus.EXPIRED
        task = self._tasks.get(task_id)
        if task:
            self._by_tenant[task.tenant_id].discard(task_id)
            if task.target_agent_id:
                self._by_agent[task.target_agent_id].discard(task_id)
            
            self._results[task_id] = TaskResult(
                task_id=task_id,
                status=TaskStatus.EXPIRED,
                error_code="TASK_EXPIRED",
                error_message=f"Task exceeded TTL of {task.ttl_seconds}s"
            )
        
        logger.debug(f"Task expired: {task_id}")
    
    async def complete(
        self,
        task_id: UUID,
        result: TaskResult
    ) -> bool:
        """Mark a task as completed."""
        async with self._lock:
            if task_id not in self._tasks:
                return False
            
            task = self._tasks[task_id]
            self._status[task_id] = TaskStatus.COMPLETED
            self._results[task_id] = result
            
            # Update indexes
            self._by_tenant[task.tenant_id].discard(task_id)
            if task.target_agent_id:
                self._by_agent[task.target_agent_id].discard(task_id)
            
            self._completed_count += 1
            
            logger.debug(f"Task completed: {task_id}")
            return True
    
    async def fail(
        self,
        task_id: UUID,
        error_code: str,
        error_message: str,
        retry: bool = True
    ) -> bool:
        """Mark a task as failed, optionally re-enqueuing for retry."""
        async with self._lock:
            if task_id not in self._tasks:
                return False
            
            task = self._tasks[task_id]
            
            # Check if we should retry
            if retry and task.can_retry():
                # Increment retry count
                task.retry_count += 1
                
                # Calculate backoff delay
                delay = min(
                    self._retry_base_delay * (2 ** task.retry_count),
                    self._retry_max_delay
                )
                
                # Re-enqueue with delay
                self._status[task_id] = TaskStatus.PENDING
                visible_at = datetime.utcnow() + timedelta(seconds=delay)
                
                item = PriorityItem(
                    priority=task.priority.value,
                    created_at=task.created_at,
                    task=task,
                    visible_at=visible_at
                )
                heappush(self._heap, item)
                
                logger.info(
                    f"Task {task_id} scheduled for retry #{task.retry_count} "
                    f"in {delay:.1f}s: {error_message}"
                )
                
                self._not_empty.notify()
                return True
            
            # No retry - mark as failed
            self._status[task_id] = TaskStatus.FAILED
            self._results[task_id] = TaskResult(
                task_id=task_id,
                status=TaskStatus.FAILED,
                error_code=error_code,
                error_message=error_message,
                retry_count=task.retry_count
            )
            
            # Update indexes
            self._by_tenant[task.tenant_id].discard(task_id)
            if task.target_agent_id:
                self._by_agent[task.target_agent_id].discard(task_id)
            
            self._failed_count += 1
            
            logger.warning(f"Task failed permanently: {task_id} - {error_message}")
            return True
    
    async def get(self, task_id: UUID) -> TaskPayload | None:
        """Get a task by ID."""
        async with self._lock:
            return self._tasks.get(task_id)
    
    async def get_result(self, task_id: UUID) -> TaskResult | None:
        """Get the result of a completed task."""
        async with self._lock:
            return self._results.get(task_id)
    
    async def cancel(self, task_id: UUID) -> bool:
        """Cancel a pending task."""
        async with self._lock:
            if task_id not in self._tasks:
                return False
            
            if self._status.get(task_id) != TaskStatus.PENDING:
                return False
            
            task = self._tasks[task_id]
            self._status[task_id] = TaskStatus.CANCELLED
            
            # Update indexes
            self._by_tenant[task.tenant_id].discard(task_id)
            if task.target_agent_id:
                self._by_agent[task.target_agent_id].discard(task_id)
            
            self._results[task_id] = TaskResult(
                task_id=task_id,
                status=TaskStatus.CANCELLED,
                error_code="TASK_CANCELLED",
                error_message="Task cancelled by user"
            )
            
            logger.debug(f"Task cancelled: {task_id}")
            return True
    
    async def size(self, tenant_id: str | None = None) -> int:
        """Get the number of pending tasks."""
        async with self._lock:
            if tenant_id:
                return sum(
                    1 for tid in self._by_tenant[tenant_id]
                    if self._status.get(tid) == TaskStatus.PENDING
                )
            return sum(
                1 for status in self._status.values()
                if status == TaskStatus.PENDING
            )
    
    async def pending_by_agent(self, agent_id: UUID) -> int:
        """Get count of pending tasks for a specific agent."""
        async with self._lock:
            return sum(
                1 for tid in self._by_agent[agent_id]
                if self._status.get(tid) == TaskStatus.PENDING
            )
    
    async def clear_expired(self) -> int:
        """Remove expired tasks from the queue."""
        async with self._lock:
            count = 0
            now = datetime.utcnow()
            
            for task_id, task in list(self._tasks.items()):
                if self._status.get(task_id) == TaskStatus.PENDING:
                    if task.is_expired():
                        self._mark_expired(task_id)
                        count += 1
            
            return count
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the queue."""
        async with self._lock:
            self._closed = True
            self._not_empty.notify_all()
        
        logger.info(
            f"Queue shutdown: enqueued={self._enqueued_count}, "
            f"completed={self._completed_count}, failed={self._failed_count}"
        )
    
    # =========================================================================
    # Utility Methods
    # =========================================================================
    
    async def stats(self) -> dict[str, Any]:
        """Get queue statistics."""
        async with self._lock:
            pending = sum(1 for s in self._status.values() if s == TaskStatus.PENDING)
            processing = sum(1 for s in self._status.values() if s == TaskStatus.PROCESSING)
            
            return {
                "total_tasks": len(self._tasks),
                "pending": pending,
                "processing": processing,
                "completed": self._completed_count,
                "failed": self._failed_count,
                "max_size": self._max_size,
                "closed": self._closed,
            }
