#!/usr/bin/env python3
"""
Task Queue Test Script

Demonstrates the task queue system for scaling agent request handling.

Usage:
    python scripts/test_task_queue.py

This script:
1. Creates an in-memory task queue
2. Starts worker pool
3. Enqueues sample tasks
4. Verifies tasks are processed
"""

import asyncio
import logging
import sys
from uuid import uuid4

# Add src to path
sys.path.insert(0, ".")

from src.queue import (
    TaskPayload,
    TaskStatus,
    TaskPriority,
    TaskResult,
    TaskQueue,
    TaskWorker,
    InMemoryTaskQueue,
    create_task_queue,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def mock_executor(task: TaskPayload) -> TaskResult:
    """
    Mock executor that simulates processing a task.
    """
    # Simulate some work
    await asyncio.sleep(0.1)
    
    logger.info(
        f"Executed task {task.task_id} "
        f"(type={task.message_type}, agent={task.target_agent_id})"
    )
    
    return TaskResult(
        task_id=task.task_id,
        status=TaskStatus.COMPLETED,
        result={"processed": True, "message_type": task.message_type}
    )


async def test_basic_queue():
    """Test basic queue operations."""
    logger.info("=" * 60)
    logger.info("Test: Basic Queue Operations")
    logger.info("=" * 60)
    
    queue = InMemoryTaskQueue(max_size=100)
    
    # Enqueue a task
    task = TaskPayload(
        tenant_id="test-tenant",
        target_agent_id=uuid4(),
        message_type="test.message",
        envelope_json='{"test": true}',
        priority=TaskPriority.HIGH,
    )
    
    enqueued = await queue.enqueue(task)
    logger.info(f"Enqueued task: {enqueued.task_id}")
    
    # Check size
    size = await queue.size()
    logger.info(f"Queue size: {size}")
    assert size == 1, f"Expected size 1, got {size}"
    
    # Dequeue
    dequeued = await queue.dequeue(timeout=1.0)
    assert dequeued is not None, "Expected to dequeue a task"
    logger.info(f"Dequeued task: {dequeued.task_id}")
    assert dequeued.task_id == task.task_id
    
    # Complete the task
    result = TaskResult(
        task_id=task.task_id,
        status=TaskStatus.COMPLETED,
        result={"success": True}
    )
    completed = await queue.complete(task.task_id, result)
    assert completed, "Expected task to be marked completed"
    
    # Verify result is stored
    stored_result = await queue.get_result(task.task_id)
    assert stored_result is not None
    assert stored_result.status == TaskStatus.COMPLETED
    
    await queue.shutdown()
    logger.info("✓ Basic queue operations passed")


async def test_priority_ordering():
    """Test that high priority tasks are processed first."""
    logger.info("=" * 60)
    logger.info("Test: Priority Ordering")
    logger.info("=" * 60)
    
    queue = InMemoryTaskQueue()
    
    # Enqueue tasks with different priorities
    low = TaskPayload(
        tenant_id="test",
        message_type="low",
        priority=TaskPriority.LOW,
    )
    high = TaskPayload(
        tenant_id="test",
        message_type="high",
        priority=TaskPriority.HIGH,
    )
    critical = TaskPayload(
        tenant_id="test",
        message_type="critical",
        priority=TaskPriority.CRITICAL,
    )
    
    # Enqueue in reverse order
    await queue.enqueue(low)
    await queue.enqueue(high)
    await queue.enqueue(critical)
    
    # Dequeue should return in priority order
    first = await queue.dequeue(timeout=0.1)
    assert first is not None and first.message_type == "critical", \
        f"Expected critical, got {first.message_type if first else None}"
    
    second = await queue.dequeue(timeout=0.1)
    assert second is not None and second.message_type == "high"
    
    third = await queue.dequeue(timeout=0.1)
    assert third is not None and third.message_type == "low"
    
    await queue.shutdown()
    logger.info("✓ Priority ordering passed")


async def test_worker_pool():
    """Test worker pool processing."""
    logger.info("=" * 60)
    logger.info("Test: Worker Pool")
    logger.info("=" * 60)
    
    queue = InMemoryTaskQueue()
    processed_tasks: list[str] = []
    
    async def tracking_executor(task: TaskPayload) -> TaskResult:
        await asyncio.sleep(0.05)  # Simulate work
        processed_tasks.append(str(task.task_id))
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
        )
    
    # Create worker pool with 4 workers
    worker = TaskWorker(
        queue=queue,
        executor=tracking_executor,
        num_workers=4,
        poll_interval=0.1,
    )
    
    # Enqueue 20 tasks
    task_ids = []
    for i in range(20):
        task = TaskPayload(
            tenant_id="test",
            message_type=f"task_{i}",
        )
        await queue.enqueue(task)
        task_ids.append(str(task.task_id))
    
    logger.info(f"Enqueued {len(task_ids)} tasks")
    
    # Start workers
    await worker.start()
    
    # Wait for all tasks to be processed
    await asyncio.sleep(1.5)  # 20 tasks * 0.05s / 4 workers = ~0.25s + buffer
    
    # Stop workers
    await worker.stop()
    
    # Verify all tasks were processed
    logger.info(f"Processed {len(processed_tasks)} tasks")
    assert len(processed_tasks) == 20, \
        f"Expected 20 processed, got {len(processed_tasks)}"
    
    # Check stats
    health = await worker.health_check()
    logger.info(f"Worker stats: {health['stats']}")
    assert health["stats"]["tasks_succeeded"] == 20
    
    await queue.shutdown()
    logger.info("✓ Worker pool passed")


async def test_retry_logic():
    """Test task retry on failure."""
    logger.info("=" * 60)
    logger.info("Test: Retry Logic")
    logger.info("=" * 60)
    
    queue = InMemoryTaskQueue(retry_base_delay=0.1, retry_max_delay=0.5)
    attempt_count = 0
    
    async def failing_executor(task: TaskPayload) -> TaskResult:
        nonlocal attempt_count
        attempt_count += 1
        
        if attempt_count < 3:
            raise Exception(f"Simulated failure (attempt {attempt_count})")
        
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
        )
    
    worker = TaskWorker(
        queue=queue,
        executor=failing_executor,
        num_workers=1,
        poll_interval=0.05,
    )
    
    # Enqueue task with max_retries=3
    task = TaskPayload(
        tenant_id="test",
        message_type="will_fail",
        max_retries=3,
    )
    await queue.enqueue(task)
    
    await worker.start()
    
    # Wait for retries
    await asyncio.sleep(2.0)
    
    await worker.stop()
    
    logger.info(f"Total attempts: {attempt_count}")
    assert attempt_count == 3, f"Expected 3 attempts, got {attempt_count}"
    
    # Task should be completed after retries
    result = await queue.get_result(task.task_id)
    assert result is not None
    assert result.status == TaskStatus.COMPLETED
    
    await queue.shutdown()
    logger.info("✓ Retry logic passed")


async def test_factory():
    """Test queue factory."""
    logger.info("=" * 60)
    logger.info("Test: Queue Factory")
    logger.info("=" * 60)
    
    # Create memory queue via factory
    queue = create_task_queue(backend="memory", max_size=500)
    assert isinstance(queue, InMemoryTaskQueue)
    
    task = TaskPayload(tenant_id="test", message_type="factory_test")
    await queue.enqueue(task)
    assert await queue.size() == 1
    
    await queue.shutdown()
    logger.info("✓ Queue factory passed")


async def test_tenant_isolation():
    """Test tenant-based task filtering."""
    logger.info("=" * 60)
    logger.info("Test: Tenant Isolation")
    logger.info("=" * 60)
    
    queue = InMemoryTaskQueue()
    
    # Enqueue tasks for different tenants
    for tenant in ["tenant-a", "tenant-b", "tenant-c"]:
        for i in range(3):
            await queue.enqueue(TaskPayload(
                tenant_id=tenant,
                message_type=f"{tenant}_task_{i}",
            ))
    
    total = await queue.size()
    assert total == 9, f"Expected 9 total, got {total}"
    
    tenant_a_size = await queue.size(tenant_id="tenant-a")
    assert tenant_a_size == 3, f"Expected 3 for tenant-a, got {tenant_a_size}"
    
    # Dequeue only from tenant-a
    dequeued = await queue.dequeue(tenant_id="tenant-a", timeout=0.1)
    assert dequeued is not None
    assert dequeued.tenant_id == "tenant-a"
    
    await queue.shutdown()
    logger.info("✓ Tenant isolation passed")


async def main():
    """Run all tests."""
    logger.info("Task Queue Test Suite")
    logger.info("=" * 60)
    
    try:
        await test_basic_queue()
        await test_priority_ordering()
        await test_worker_pool()
        await test_retry_logic()
        await test_factory()
        await test_tenant_isolation()
        
        logger.info("")
        logger.info("=" * 60)
        logger.info("All tests passed! ✓")
        logger.info("=" * 60)
        
    except AssertionError as e:
        logger.error(f"Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
