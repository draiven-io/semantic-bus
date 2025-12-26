"""
Task Queue Port Interfaces

Abstract base classes defining the task queue contracts for the LIP Semantic Bus.
Enables async task processing for agent requests with configurable backends.

These ports follow the hexagonal architecture pattern:
- Bus code depends only on these interfaces
- Adapters (in-memory, Redis) implement these interfaces
- Queue implementation is injected via dependency inversion

Thread-safety: All implementations must be safe for concurrent async usage.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Awaitable
from uuid import UUID, uuid4


class TaskStatus(str, Enum):
    """Status of a queued task."""
    PENDING = "pending"      # Waiting in queue
    PROCESSING = "processing"  # Being executed by a worker
    COMPLETED = "completed"   # Successfully completed
    FAILED = "failed"        # Failed with error
    CANCELLED = "cancelled"  # Manually cancelled
    EXPIRED = "expired"      # TTL expired before processing


class TaskPriority(int, Enum):
    """Task priority levels (lower number = higher priority)."""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


@dataclass
class TaskPayload:
    """
    A task to be processed by an agent.
    
    Contains all information needed to route and execute
    a request against a target agent.
    """
    # === Identity ===
    task_id: UUID = field(default_factory=uuid4)
    
    # === Routing ===
    tenant_id: str = ""
    target_agent_id: UUID | None = None  # Specific agent, or None for intent-based routing
    target_conn_id: str | None = None    # Connection ID if known
    
    # === Message ===
    message_type: str = ""       # The message type to send
    envelope_json: str = ""      # Serialized MessageEnvelope
    
    # === Session Context ===
    session_id: UUID | None = None  # Associated session if any
    exec_id: UUID | None = None     # Associated execution if any
    
    # === Metadata ===
    priority: TaskPriority = TaskPriority.NORMAL
    created_at: datetime = field(default_factory=datetime.utcnow)
    ttl_seconds: int = 300  # Default 5 minute TTL
    retry_count: int = 0
    max_retries: int = 3
    
    # === Callbacks ===
    # These are set by the worker, not serialized
    correlation_id: UUID | None = None  # For tracking responses
    
    # === Extra Data ===
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def is_expired(self) -> bool:
        """Check if task has exceeded its TTL."""
        elapsed = (datetime.utcnow() - self.created_at).total_seconds()
        return elapsed > self.ttl_seconds
    
    @property
    def attempts(self) -> int:
        """Total attempts including retries."""
        return self.retry_count + 1
    
    def can_retry(self) -> bool:
        """Check if task can be retried."""
        return self.retry_count < self.max_retries


@dataclass
class TaskResult:
    """
    Result of a processed task.
    """
    task_id: UUID
    status: TaskStatus
    result: Any | None = None
    error_code: str | None = None
    error_message: str | None = None
    processed_at: datetime = field(default_factory=datetime.utcnow)
    duration_ms: float = 0.0
    retry_count: int = 0


class TaskQueue(ABC):
    """
    Abstract interface for task queue implementations.
    
    Provides async operations for enqueuing, dequeuing, and managing tasks.
    Implementations must be thread-safe for concurrent async usage.
    """
    
    @abstractmethod
    async def enqueue(
        self,
        task: TaskPayload,
        delay_seconds: float = 0.0
    ) -> TaskPayload:
        """
        Add a task to the queue.
        
        Args:
            task: The task payload to enqueue
            delay_seconds: Optional delay before task becomes visible
            
        Returns:
            The enqueued task (may have updated fields)
            
        Raises:
            TaskQueueError: If enqueue fails
        """
        ...
    
    @abstractmethod
    async def dequeue(
        self,
        tenant_id: str | None = None,
        timeout: float = 0.0
    ) -> TaskPayload | None:
        """
        Get the next task from the queue.
        
        Args:
            tenant_id: Optional filter by tenant
            timeout: Max seconds to wait for a task (0 = non-blocking)
            
        Returns:
            Next task or None if queue is empty/timeout
        """
        ...
    
    @abstractmethod
    async def complete(
        self,
        task_id: UUID,
        result: TaskResult
    ) -> bool:
        """
        Mark a task as completed.
        
        Args:
            task_id: The task to complete
            result: The result of processing
            
        Returns:
            True if updated, False if not found
        """
        ...
    
    @abstractmethod
    async def fail(
        self,
        task_id: UUID,
        error_code: str,
        error_message: str,
        retry: bool = True
    ) -> bool:
        """
        Mark a task as failed.
        
        If retry=True and task can be retried, it will be re-enqueued.
        
        Args:
            task_id: The task that failed
            error_code: Error code
            error_message: Error description
            retry: Whether to retry if possible
            
        Returns:
            True if updated, False if not found
        """
        ...
    
    @abstractmethod
    async def get(self, task_id: UUID) -> TaskPayload | None:
        """
        Get a task by ID.
        
        Args:
            task_id: The task ID
            
        Returns:
            Task payload or None if not found
        """
        ...
    
    @abstractmethod
    async def get_result(self, task_id: UUID) -> TaskResult | None:
        """
        Get the result of a completed task.
        
        Args:
            task_id: The task ID
            
        Returns:
            Task result or None if not found/not completed
        """
        ...
    
    @abstractmethod
    async def cancel(self, task_id: UUID) -> bool:
        """
        Cancel a pending task.
        
        Only tasks in PENDING status can be cancelled.
        
        Args:
            task_id: The task to cancel
            
        Returns:
            True if cancelled, False if not found or not cancellable
        """
        ...
    
    @abstractmethod
    async def size(self, tenant_id: str | None = None) -> int:
        """
        Get the number of pending tasks in the queue.
        
        Args:
            tenant_id: Optional filter by tenant
            
        Returns:
            Number of pending tasks
        """
        ...
    
    @abstractmethod
    async def pending_by_agent(self, agent_id: UUID) -> int:
        """
        Get count of pending tasks for a specific agent.
        
        Useful for load balancing decisions.
        
        Args:
            agent_id: Target agent ID
            
        Returns:
            Number of pending tasks for this agent
        """
        ...
    
    @abstractmethod
    async def clear_expired(self) -> int:
        """
        Remove expired tasks from the queue.
        
        Returns:
            Number of tasks cleared
        """
        ...
    
    @abstractmethod
    async def shutdown(self) -> None:
        """
        Gracefully shutdown the queue.
        
        Stops accepting new tasks and waits for in-flight tasks.
        """
        ...


# =============================================================================
# Exceptions
# =============================================================================

class TaskQueueError(Exception):
    """Base exception for task queue errors."""
    pass


class TaskNotFoundError(TaskQueueError):
    """Task not found in queue."""
    pass


class QueueFullError(TaskQueueError):
    """Queue is at capacity."""
    def __init__(self, queue_size: int, max_size: int):
        self.queue_size = queue_size
        self.max_size = max_size
        super().__init__(f"Queue full: {queue_size}/{max_size}")


class QueueClosedError(TaskQueueError):
    """Queue has been shutdown."""
    pass
