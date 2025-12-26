"""
Queued Router

Extends the IntentRouter to use a task queue for scalable message routing.
Instead of sending messages directly to agents, it enqueues tasks that
are processed by worker pools.

Benefits:
- Horizontal scaling: Multiple workers can process tasks
- Backpressure handling: Queue absorbs spikes in traffic
- Retry logic: Failed deliveries are automatically retried
- Load balancing: Tasks are distributed across workers
- Durability: Redis backend provides persistence across restarts
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from src.registry import AgentRecord
from src.protocol.envelope import MessageEnvelope
from src.queue.ports import TaskPayload, TaskPriority, TaskQueue

if TYPE_CHECKING:
    from src.registry import AgentRegistry
    from src.routing.router import IntentRouter
    from src.routing.semantic_matcher import SemanticMatcher

logger = logging.getLogger(__name__)


class QueuedRouter:
    """
    Router that uses a task queue for scalable message delivery.
    
    This wraps an IntentRouter and delegates the actual message delivery
    to a task queue, allowing for horizontal scaling and better handling
    of high-volume scenarios.
    """
    
    def __init__(
        self,
        base_router: "IntentRouter",
        queue: TaskQueue,
        default_priority: TaskPriority = TaskPriority.NORMAL,
        default_ttl_seconds: int = 300,
    ):
        """
        Initialize the queued router.
        
        Args:
            base_router: The underlying IntentRouter for semantic matching
            queue: Task queue for message delivery
            default_priority: Default priority for queued tasks
            default_ttl_seconds: Default TTL for tasks
        """
        self._router = base_router
        self._queue = queue
        self._default_priority = default_priority
        self._default_ttl = default_ttl_seconds
    
    async def find_candidates_for_intent(
        self, envelope: MessageEnvelope
    ) -> list[AgentRecord]:
        """
        Find candidate agents for an intent broadcast.
        
        This delegates to the base router's semantic matching.
        
        Args:
            envelope: The intent.broadcast message
            
        Returns:
            List of candidate AgentRecords
        """
        return await self._router.find_candidates_for_intent(envelope)
    
    async def queue_to_agent(
        self,
        agent: AgentRecord,
        envelope: MessageEnvelope,
        priority: TaskPriority | None = None,
        ttl_seconds: int | None = None,
        session_id: UUID | None = None,
        exec_id: UUID | None = None,
    ) -> TaskPayload:
        """
        Queue a message for delivery to a specific agent.
        
        Instead of sending directly, this creates a task that will be
        processed by a worker.
        
        Args:
            agent: Target agent
            envelope: Message to send
            priority: Task priority (uses default if not specified)
            ttl_seconds: Task TTL (uses default if not specified)
            session_id: Associated session ID for tracking
            exec_id: Associated execution ID for tracking
            
        Returns:
            The enqueued task
        """
        task = TaskPayload(
            tenant_id=envelope.tenant_id,
            target_agent_id=agent.agent_id,
            target_conn_id=agent.conn_id,
            message_type=envelope.message_type.value,
            envelope_json=envelope.model_dump_json(),
            session_id=session_id,
            exec_id=exec_id,
            priority=priority or self._default_priority,
            ttl_seconds=ttl_seconds or self._default_ttl,
            correlation_id=envelope.message_id,
            metadata={
                "sender_id": str(envelope.sender_id),
            }
        )
        
        await self._queue.enqueue(task)
        
        logger.debug(
            f"Queued {envelope.message_type} for {agent.agent_id} "
            f"(task_id={task.task_id})"
        )
        
        return task
    
    async def queue_to_candidates(
        self,
        candidates: list[AgentRecord],
        envelope: MessageEnvelope,
        priority: TaskPriority | None = None,
        ttl_seconds: int | None = None,
        session_id: UUID | None = None,
    ) -> list[TaskPayload]:
        """
        Queue a message for delivery to multiple candidates.
        
        Args:
            candidates: List of target agents
            envelope: Message to send
            priority: Task priority
            ttl_seconds: Task TTL
            session_id: Associated session ID
            
        Returns:
            List of enqueued tasks
        """
        tasks = []
        
        for agent in candidates:
            task = await self.queue_to_agent(
                agent=agent,
                envelope=envelope,
                priority=priority,
                ttl_seconds=ttl_seconds,
                session_id=session_id,
            )
            tasks.append(task)
        
        logger.info(
            f"Queued {envelope.message_type} for {len(tasks)} candidates"
        )
        
        return tasks
    
    async def route_to_agent(
        self,
        agent: AgentRecord,
        envelope: MessageEnvelope,
        use_queue: bool = True,
        **queue_kwargs
    ) -> bool | TaskPayload:
        """
        Route a message to an agent, optionally using the queue.
        
        Args:
            agent: Target agent
            envelope: Message to send
            use_queue: If True, queue the message. If False, send directly.
            **queue_kwargs: Additional arguments for queue_to_agent
            
        Returns:
            If use_queue=True: The enqueued TaskPayload
            If use_queue=False: True if sent directly, False otherwise
        """
        if use_queue:
            return await self.queue_to_agent(agent, envelope, **queue_kwargs)
        else:
            return await self._router.route_to_agent(agent, envelope)
    
    async def get_queue_depth(self, agent_id: UUID | None = None) -> int:
        """
        Get the number of pending tasks.
        
        Args:
            agent_id: If specified, get depth for this agent only
            
        Returns:
            Number of pending tasks
        """
        if agent_id:
            return await self._queue.pending_by_agent(agent_id)
        return await self._queue.size()
    
    async def get_agent_load(self, agent_id: UUID) -> float:
        """
        Estimate agent load based on pending tasks.
        
        Args:
            agent_id: Agent to check
            
        Returns:
            Load factor between 0.0 and 1.0
        """
        pending = await self._queue.pending_by_agent(agent_id)
        # Simple heuristic: assume 10 pending tasks = fully loaded
        return min(1.0, pending / 10.0)


def create_queued_router(
    registry: "AgentRegistry",
    queue: TaskQueue,
    max_candidates: int = 3,
    llm_model: str = "gpt-4o-mini",
    **kwargs
) -> QueuedRouter:
    """
    Create a QueuedRouter with an IntentRouter backend.
    
    Args:
        registry: Agent registry
        queue: Task queue for message delivery
        max_candidates: Max agents to route to
        llm_model: LLM model for semantic matching
        **kwargs: Additional QueuedRouter options
        
    Returns:
        Configured QueuedRouter
    """
    from src.routing.router import IntentRouter
    
    base_router = IntentRouter(
        registry=registry,
        max_candidates=max_candidates,
        llm_model=llm_model,
    )
    
    return QueuedRouter(
        base_router=base_router,
        queue=queue,
        **kwargs
    )
