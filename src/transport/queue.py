"""
Connection Queue Manager

Per-connection async outgoing queue with single writer task.
Implements backpressure handling for streaming.

Design:
- Each connection gets a dedicated asyncio.Queue
- Single writer coroutine drains queue and sends to WebSocket
- Backpressure: if queue is full, signal to caller to terminate stream
- Clean shutdown on disconnect
"""

import asyncio
import logging
from typing import Callable, Awaitable
from uuid import UUID

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class QueueFullError(Exception):
    """Raised when the outbound queue is full (backpressure)."""
    def __init__(self, conn_id: str, queue_size: int):
        self.conn_id = conn_id
        self.queue_size = queue_size
        super().__init__(f"Queue full for {conn_id} (size={queue_size})")


class ConnectionQueue:
    """
    Outbound message queue for a single connection.
    
    Features:
    - Async queue with configurable max size
    - Single writer task to serialize WebSocket sends
    - Non-blocking put with backpressure signaling
    """
    
    def __init__(
        self,
        conn_id: str,
        send_fn: Callable[[str], Awaitable[None]],
        max_size: int = 200
    ):
        """
        Initialize connection queue.
        
        Args:
            conn_id: Connection identifier
            send_fn: Async function to send data to WebSocket
            max_size: Max queue depth before backpressure
        """
        self.conn_id = conn_id
        self._send_fn = send_fn
        self._queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=max_size)
        self._writer_task: asyncio.Task | None = None
        self._closed = False
        self._max_size = max_size
    
    async def start(self) -> None:
        """Start the writer task."""
        if self._writer_task is None:
            self._writer_task = asyncio.create_task(
                self._writer_loop(),
                name=f"queue_writer_{self.conn_id}"
            )
    
    async def stop(self) -> None:
        """Stop the writer task gracefully."""
        self._closed = True
        # Signal writer to exit
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        
        if self._writer_task:
            self._writer_task.cancel()
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
            self._writer_task = None
    
    def put_nowait(self, message: str) -> None:
        """
        Put a message on the queue without blocking.
        
        Raises:
            QueueFullError: If queue is full (backpressure condition)
        """
        if self._closed:
            raise RuntimeError(f"Queue closed for {self.conn_id}")
        
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            raise QueueFullError(self.conn_id, self._max_size)
    
    async def put(self, message: str, timeout: float = 1.0) -> bool:
        """
        Put a message on the queue with timeout.
        
        Args:
            message: Message to send
            timeout: Max time to wait for space
            
        Returns:
            True if queued, False if timeout
        """
        if self._closed:
            return False
        
        try:
            await asyncio.wait_for(
                self._queue.put(message),
                timeout=timeout
            )
            return True
        except asyncio.TimeoutError:
            return False
    
    @property
    def qsize(self) -> int:
        """Current queue depth."""
        return self._queue.qsize()
    
    @property
    def is_full(self) -> bool:
        """Check if queue is at max capacity."""
        return self._queue.full()
    
    async def _writer_loop(self) -> None:
        """
        Single writer loop that drains the queue.
        
        This serializes all sends to the WebSocket connection,
        preventing concurrent write issues.
        """
        while not self._closed:
            try:
                message = await self._queue.get()
                
                # None is the shutdown signal
                if message is None:
                    break
                
                try:
                    await self._send_fn(message)
                except Exception as e:
                    logger.warning(f"Send failed for {self.conn_id}: {e}")
                    # Connection likely dead, break out
                    break
                finally:
                    self._queue.task_done()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Writer loop error for {self.conn_id}: {e}")


class ConnectionQueueManager:
    """
    Manages per-connection outbound queues.
    
    Creates queues on demand and cleans up on disconnect.
    """
    
    def __init__(self, max_queue_size: int = 200):
        """
        Initialize queue manager.
        
        Args:
            max_queue_size: Default max queue size per connection
        """
        self._max_queue_size = max_queue_size
        self._queues: dict[str, ConnectionQueue] = {}
        self._lock = asyncio.Lock()
    
    async def get_or_create(
        self,
        conn_id: str,
        send_fn: Callable[[str], Awaitable[None]]
    ) -> ConnectionQueue:
        """
        Get existing queue or create new one.
        
        Args:
            conn_id: Connection identifier
            send_fn: Async send function for the WebSocket
            
        Returns:
            ConnectionQueue instance
        """
        async with self._lock:
            if conn_id not in self._queues:
                queue = ConnectionQueue(conn_id, send_fn, self._max_queue_size)
                await queue.start()
                self._queues[conn_id] = queue
            return self._queues[conn_id]
    
    async def remove(self, conn_id: str) -> None:
        """
        Remove and stop queue for a connection.
        
        Args:
            conn_id: Connection to remove
        """
        async with self._lock:
            queue = self._queues.pop(conn_id, None)
            if queue:
                await queue.stop()
    
    async def send(self, conn_id: str, message: str) -> bool:
        """
        Send a message via the connection's queue.
        
        Args:
            conn_id: Target connection
            message: Message to send
            
        Returns:
            True if queued, False if connection not found
            
        Raises:
            QueueFullError: If queue is full (backpressure)
        """
        async with self._lock:
            queue = self._queues.get(conn_id)
            if queue is None:
                return False
        
        queue.put_nowait(message)
        return True
    
    async def shutdown(self) -> None:
        """Stop all queues."""
        async with self._lock:
            for queue in self._queues.values():
                await queue.stop()
            self._queues.clear()
    
    def queue_size(self, conn_id: str) -> int:
        """Get queue depth for a connection."""
        queue = self._queues.get(conn_id)
        return queue.qsize if queue else 0
    
    def connection_count(self) -> int:
        """Number of active connection queues."""
        return len(self._queues)
