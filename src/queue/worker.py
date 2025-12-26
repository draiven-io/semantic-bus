"""
Task Worker

Consumes tasks from the queue and executes them against target agents.
Provides configurable concurrency and graceful shutdown.

Features:
- Configurable number of concurrent workers
- Per-tenant isolation
- Graceful shutdown with task completion
- Metrics and health reporting
- Automatic retry handling
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Awaitable, Any, TYPE_CHECKING
from uuid import UUID

from src.queue.ports import (
    TaskPayload,
    TaskStatus,
    TaskResult,
    TaskQueue,
)

if TYPE_CHECKING:
    from src.registry import AgentRegistry
    from src.protocol.envelope import MessageEnvelope

logger = logging.getLogger(__name__)


@dataclass
class WorkerStats:
    """Statistics for task worker."""
    tasks_processed: int = 0
    tasks_succeeded: int = 0
    tasks_failed: int = 0
    tasks_retried: int = 0
    avg_duration_ms: float = 0.0
    last_task_at: datetime | None = None
    
    def record_success(self, duration_ms: float) -> None:
        """Record a successful task."""
        self.tasks_processed += 1
        self.tasks_succeeded += 1
        self._update_avg_duration(duration_ms)
        self.last_task_at = datetime.utcnow()
    
    def record_failure(self, retried: bool) -> None:
        """Record a failed task."""
        self.tasks_processed += 1
        self.tasks_failed += 1
        if retried:
            self.tasks_retried += 1
        self.last_task_at = datetime.utcnow()
    
    def _update_avg_duration(self, duration_ms: float) -> None:
        """Update rolling average duration."""
        if self.tasks_processed == 1:
            self.avg_duration_ms = duration_ms
        else:
            # Exponential moving average
            alpha = 0.1
            self.avg_duration_ms = alpha * duration_ms + (1 - alpha) * self.avg_duration_ms


# Type for task executor function
TaskExecutor = Callable[[TaskPayload], Awaitable[TaskResult]]


class TaskWorker:
    """
    Worker pool that processes tasks from a queue.
    
    Manages a configurable number of concurrent worker coroutines
    that consume tasks and execute them.
    """
    
    def __init__(
        self,
        queue: TaskQueue,
        executor: TaskExecutor,
        num_workers: int = 4,
        poll_interval: float = 0.5,
        tenant_id: str | None = None,
    ):
        """
        Initialize the task worker.
        
        Args:
            queue: Task queue to consume from
            executor: Function that processes each task
            num_workers: Number of concurrent workers
            poll_interval: Seconds between queue polls when idle
            tenant_id: Optional tenant filter (only process tasks for this tenant)
        """
        self._queue = queue
        self._executor = executor
        self._num_workers = num_workers
        self._poll_interval = poll_interval
        self._tenant_id = tenant_id
        
        self._workers: list[asyncio.Task] = []
        self._shutdown_event = asyncio.Event()
        self._stats = WorkerStats()
        self._lock = asyncio.Lock()
    
    async def start(self) -> None:
        """Start the worker pool."""
        async with self._lock:
            if self._workers:
                logger.warning("Workers already started")
                return
            
            self._shutdown_event.clear()
            
            for i in range(self._num_workers):
                worker = asyncio.create_task(
                    self._worker_loop(i),
                    name=f"task_worker_{i}"
                )
                self._workers.append(worker)
            
            logger.info(f"Started {self._num_workers} task workers")
    
    async def stop(self, timeout: float = 30.0) -> None:
        """
        Stop the worker pool gracefully.
        
        Args:
            timeout: Max seconds to wait for in-flight tasks
        """
        async with self._lock:
            if not self._workers:
                return
            
            logger.info("Shutting down task workers...")
            self._shutdown_event.set()
            
            # Wait for workers to finish with timeout
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._workers, return_exceptions=True),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                logger.warning("Worker shutdown timed out, cancelling...")
                for worker in self._workers:
                    worker.cancel()
                await asyncio.gather(*self._workers, return_exceptions=True)
            
            self._workers.clear()
            logger.info(
                f"Task workers stopped. Stats: "
                f"processed={self._stats.tasks_processed}, "
                f"succeeded={self._stats.tasks_succeeded}, "
                f"failed={self._stats.tasks_failed}"
            )
    
    async def _worker_loop(self, worker_id: int) -> None:
        """
        Main worker loop that processes tasks.
        
        Args:
            worker_id: Worker identifier for logging
        """
        logger.debug(f"Worker {worker_id} started")
        
        while not self._shutdown_event.is_set():
            try:
                # Try to get a task
                task = await self._queue.dequeue(
                    tenant_id=self._tenant_id,
                    timeout=self._poll_interval
                )
                
                if task is None:
                    # No task available, loop will check shutdown flag
                    continue
                
                # Process the task
                await self._process_task(worker_id, task)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}", exc_info=True)
                # Brief pause on error to avoid tight loop
                await asyncio.sleep(0.1)
        
        logger.debug(f"Worker {worker_id} stopped")
    
    async def _process_task(self, worker_id: int, task: TaskPayload) -> None:
        """
        Process a single task.
        
        Args:
            worker_id: Worker identifier
            task: Task to process
        """
        start_time = time.perf_counter()
        
        logger.debug(
            f"Worker {worker_id} processing task {task.task_id} "
            f"(type={task.message_type}, agent={task.target_agent_id})"
        )
        
        try:
            # Execute the task
            result = await self._executor(task)
            
            duration_ms = (time.perf_counter() - start_time) * 1000
            result.duration_ms = duration_ms
            
            # Complete the task
            await self._queue.complete(task.task_id, result)
            self._stats.record_success(duration_ms)
            
            logger.debug(
                f"Worker {worker_id} completed task {task.task_id} "
                f"in {duration_ms:.1f}ms"
            )
            
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            
            logger.error(
                f"Worker {worker_id} failed task {task.task_id}: {e}",
                exc_info=True
            )
            
            # Mark as failed (will retry if possible)
            retried = await self._queue.fail(
                task.task_id,
                error_code="EXECUTION_ERROR",
                error_message=str(e),
                retry=True
            )
            
            self._stats.record_failure(retried=task.can_retry())
    
    @property
    def stats(self) -> WorkerStats:
        """Get worker statistics."""
        return self._stats
    
    @property
    def is_running(self) -> bool:
        """Check if workers are running."""
        return len(self._workers) > 0 and not self._shutdown_event.is_set()
    
    async def health_check(self) -> dict[str, Any]:
        """Get health status of the worker pool."""
        queue_size = await self._queue.size(self._tenant_id)
        
        return {
            "running": self.is_running,
            "num_workers": self._num_workers,
            "active_workers": len([w for w in self._workers if not w.done()]),
            "queue_size": queue_size,
            "stats": {
                "tasks_processed": self._stats.tasks_processed,
                "tasks_succeeded": self._stats.tasks_succeeded,
                "tasks_failed": self._stats.tasks_failed,
                "tasks_retried": self._stats.tasks_retried,
                "avg_duration_ms": self._stats.avg_duration_ms,
                "last_task_at": (
                    self._stats.last_task_at.isoformat()
                    if self._stats.last_task_at
                    else None
                ),
            }
        }


class AgentTaskExecutor:
    """
    Task executor that routes tasks to agents via WebSocket.
    
    This is the bridge between the task queue and the agent registry.
    """
    
    def __init__(
        self,
        registry: "AgentRegistry",
    ):
        """
        Initialize the executor.
        
        Args:
            registry: Agent registry for WebSocket lookups
        """
        self._registry = registry
    
    async def execute(self, task: TaskPayload) -> TaskResult:
        """
        Execute a task by sending it to the target agent.
        
        Args:
            task: Task to execute
            
        Returns:
            Task result
            
        Raises:
            Exception: If routing fails
        """
        from src.protocol.envelope import MessageEnvelope
        
        # Get the target WebSocket
        if task.target_conn_id:
            websocket = await self._registry.get_websocket(task.target_conn_id)
        elif task.target_agent_id:
            agent = await self._registry.get_by_agent_id(task.target_agent_id)
            if agent:
                websocket = await self._registry.get_websocket(agent.conn_id)
            else:
                websocket = None
        else:
            raise ValueError("Task has no target agent or connection")
        
        if websocket is None:
            raise ConnectionError(
                f"No websocket for agent {task.target_agent_id} "
                f"(conn_id={task.target_conn_id})"
            )
        
        # Send the envelope
        try:
            await websocket.send_text(task.envelope_json)
            
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.COMPLETED,
                result={"delivered": True}
            )
        except Exception as e:
            raise ConnectionError(f"Failed to send to agent: {e}") from e


def create_agent_executor(registry: "AgentRegistry") -> TaskExecutor:
    """
    Create a task executor for agent routing.
    
    Args:
        registry: Agent registry
        
    Returns:
        Executor function
    """
    executor = AgentTaskExecutor(registry)
    return executor.execute
