"""
Event Manager

High-level interface for event streaming in the Semantic Bus.
Integrates with sessions, provides convenient APIs for publishing
and subscribing, and handles event routing to WebSocket connections.

Features:
- Session-aware event publishing
- Automatic subscriber management per connection
- WebSocket delivery with backpressure handling
- Event history for late-joining subscribers
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Awaitable, TYPE_CHECKING
from uuid import UUID, uuid4

from src.events.models import (
    BaseEvent,
    EventType,
    EventSeverity,
    ExecutionEvent,
    FlowEvent,
    AgentEvent,
    ReasoningEvent,
    create_execution_started,
    create_execution_progress,
    create_execution_completed,
    create_execution_failed,
    create_flow_started,
    create_flow_agent_started,
    create_flow_agent_completed,
    create_flow_completed,
    create_reasoning_thought,
    create_reasoning_action,
    create_agent_status,
)
from src.events.stream import (
    EventStream,
    EventSubscription,
    EventFilter,
    create_session_filter,
    create_execution_filter,
)

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = logging.getLogger(__name__)


class EventManager:
    """
    Central manager for event streaming in the Semantic Bus.
    
    Responsibilities:
    - Maintain the event stream
    - Manage per-connection subscriptions
    - Route events to WebSocket connections
    - Provide convenient publishing APIs
    - Keep event history for replay
    """
    
    def __init__(
        self,
        max_history_per_session: int = 1000,
        max_subscribers: int = 10000,
    ):
        """
        Initialize the event manager.
        
        Args:
            max_history_per_session: Max events to keep in history per session
            max_subscribers: Maximum concurrent subscriptions
        """
        self._stream = EventStream(max_subscribers=max_subscribers)
        self._max_history = max_history_per_session
        
        # Event history per session (for late-joining subscribers)
        self._history: dict[UUID, list[BaseEvent]] = {}
        
        # Connection subscriptions: conn_id -> list of subscription_ids
        self._conn_subscriptions: dict[str, list[str]] = {}
        
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()
    
    @property
    def stream(self) -> EventStream:
        """Get the underlying event stream."""
        return self._stream
    
    @property
    def stats(self) -> dict[str, Any]:
        """Get manager statistics."""
        return {
            **self._stream.stats,
            "sessions_with_history": len(self._history),
            "connections_with_subscriptions": len(self._conn_subscriptions),
        }
    
    # =========================================================================
    # Publishing APIs
    # =========================================================================
    
    async def publish(self, event: BaseEvent) -> int:
        """
        Publish an event.
        
        Also stores in history for the session.
        
        Args:
            event: The event to publish
        
        Returns:
            Number of subscribers that received the event
        """
        # Store in history
        await self._add_to_history(event)
        
        # Publish to stream
        return await self._stream.publish(event)
    
    async def _add_to_history(self, event: BaseEvent) -> None:
        """Add event to session history."""
        async with self._lock:
            session_id = event.session_id
            if session_id not in self._history:
                self._history[session_id] = []
            
            history = self._history[session_id]
            history.append(event)
            
            # Trim if needed
            if len(history) > self._max_history:
                self._history[session_id] = history[-self._max_history:]
    
    async def publish_execution_started(
        self,
        session_id: UUID,
        tenant_id: str,
        exec_id: str,
        action: str,
        source_agent_id: UUID | None = None,
        source_name: str | None = None,
        total_steps: int | None = None,
    ) -> int:
        """Publish an execution.started event."""
        event = create_execution_started(
            session_id=session_id,
            tenant_id=tenant_id,
            exec_id=exec_id,
            action=action,
            source_agent_id=source_agent_id,
            source_name=source_name,
            total_steps=total_steps,
        )
        return await self.publish(event)
    
    async def publish_execution_progress(
        self,
        session_id: UUID,
        tenant_id: str,
        exec_id: str,
        message: str,
        progress_percent: float | None = None,
        step_index: int | None = None,
        total_steps: int | None = None,
        source_agent_id: UUID | None = None,
        source_name: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> int:
        """Publish an execution.progress event."""
        event = create_execution_progress(
            session_id=session_id,
            tenant_id=tenant_id,
            exec_id=exec_id,
            message=message,
            progress_percent=progress_percent,
            step_index=step_index,
            total_steps=total_steps,
            source_agent_id=source_agent_id,
            source_name=source_name,
            data=data,
        )
        return await self.publish(event)
    
    async def publish_execution_completed(
        self,
        session_id: UUID,
        tenant_id: str,
        exec_id: str,
        message: str = "Execution completed successfully",
        elapsed_ms: int | None = None,
        source_agent_id: UUID | None = None,
        source_name: str | None = None,
    ) -> int:
        """Publish an execution.completed event."""
        event = create_execution_completed(
            session_id=session_id,
            tenant_id=tenant_id,
            exec_id=exec_id,
            message=message,
            elapsed_ms=elapsed_ms,
            source_agent_id=source_agent_id,
            source_name=source_name,
        )
        return await self.publish(event)
    
    async def publish_execution_failed(
        self,
        session_id: UUID,
        tenant_id: str,
        exec_id: str,
        error_message: str,
        error_code: str | None = None,
        source_agent_id: UUID | None = None,
        source_name: str | None = None,
    ) -> int:
        """Publish an execution.failed event."""
        event = create_execution_failed(
            session_id=session_id,
            tenant_id=tenant_id,
            exec_id=exec_id,
            error_message=error_message,
            error_code=error_code,
            source_agent_id=source_agent_id,
            source_name=source_name,
        )
        return await self.publish(event)
    
    async def publish_flow_started(
        self,
        session_id: UUID,
        tenant_id: str,
        flow_id: str,
        flow_name: str,
        agent_order: list[str],
        exec_id: str | None = None,
    ) -> int:
        """Publish a flow.started event."""
        event = create_flow_started(
            session_id=session_id,
            tenant_id=tenant_id,
            flow_id=flow_id,
            flow_name=flow_name,
            agent_order=agent_order,
            exec_id=exec_id,
        )
        return await self.publish(event)
    
    async def publish_flow_agent_started(
        self,
        session_id: UUID,
        tenant_id: str,
        flow_id: str,
        agent_id: UUID,
        agent_name: str,
        agent_index: int,
        agents_total: int,
        exec_id: str | None = None,
    ) -> int:
        """Publish a flow.agent.started event."""
        event = create_flow_agent_started(
            session_id=session_id,
            tenant_id=tenant_id,
            flow_id=flow_id,
            agent_id=agent_id,
            agent_name=agent_name,
            agent_index=agent_index,
            agents_total=agents_total,
            exec_id=exec_id,
        )
        return await self.publish(event)
    
    async def publish_flow_agent_completed(
        self,
        session_id: UUID,
        tenant_id: str,
        flow_id: str,
        agent_id: UUID,
        agent_name: str,
        agent_index: int,
        agents_total: int,
        next_agent_name: str | None = None,
        exec_id: str | None = None,
    ) -> int:
        """Publish a flow.agent.completed event."""
        event = create_flow_agent_completed(
            session_id=session_id,
            tenant_id=tenant_id,
            flow_id=flow_id,
            agent_id=agent_id,
            agent_name=agent_name,
            agent_index=agent_index,
            agents_total=agents_total,
            next_agent_name=next_agent_name,
            exec_id=exec_id,
        )
        return await self.publish(event)
    
    async def publish_flow_completed(
        self,
        session_id: UUID,
        tenant_id: str,
        flow_id: str,
        flow_name: str,
        agents_total: int,
        elapsed_ms: int | None = None,
        exec_id: str | None = None,
    ) -> int:
        """Publish a flow.completed event."""
        event = create_flow_completed(
            session_id=session_id,
            tenant_id=tenant_id,
            flow_id=flow_id,
            flow_name=flow_name,
            agents_total=agents_total,
            elapsed_ms=elapsed_ms,
            exec_id=exec_id,
        )
        return await self.publish(event)
    
    async def publish_reasoning_thought(
        self,
        session_id: UUID,
        tenant_id: str,
        thought: str,
        step_number: int | None = None,
        source_agent_id: UUID | None = None,
        source_name: str | None = None,
        exec_id: str | None = None,
    ) -> int:
        """Publish a reasoning.thought event."""
        event = create_reasoning_thought(
            session_id=session_id,
            tenant_id=tenant_id,
            thought=thought,
            step_number=step_number,
            source_agent_id=source_agent_id,
            source_name=source_name,
            exec_id=exec_id,
        )
        return await self.publish(event)
    
    async def publish_reasoning_action(
        self,
        session_id: UUID,
        tenant_id: str,
        action: str,
        tool_name: str | None = None,
        tool_input: dict[str, Any] | None = None,
        step_number: int | None = None,
        source_agent_id: UUID | None = None,
        source_name: str | None = None,
        exec_id: str | None = None,
    ) -> int:
        """Publish a reasoning.action event."""
        event = create_reasoning_action(
            session_id=session_id,
            tenant_id=tenant_id,
            action=action,
            tool_name=tool_name,
            tool_input=tool_input,
            step_number=step_number,
            source_agent_id=source_agent_id,
            source_name=source_name,
            exec_id=exec_id,
        )
        return await self.publish(event)
    
    async def publish_agent_status(
        self,
        session_id: UUID,
        tenant_id: str,
        agent_id: UUID,
        agent_name: str,
        status: str,
        message: str,
        progress_percent: float | None = None,
        exec_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> int:
        """Publish an agent.status event."""
        event = create_agent_status(
            session_id=session_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            agent_name=agent_name,
            status=status,
            message=message,
            progress_percent=progress_percent,
            exec_id=exec_id,
            data=data,
        )
        return await self.publish(event)
    
    async def publish_custom_event(
        self,
        session_id: UUID,
        tenant_id: str,
        event_type: EventType,
        message: str,
        exec_id: str | None = None,
        source_agent_id: UUID | None = None,
        source_name: str | None = None,
        severity: EventSeverity = EventSeverity.INFO,
        progress_percent: float | None = None,
        data: dict[str, Any] | None = None,
    ) -> int:
        """Publish a custom event."""
        event = BaseEvent(
            event_type=event_type,
            session_id=session_id,
            tenant_id=tenant_id,
            exec_id=exec_id,
            source_agent_id=source_agent_id,
            source_name=source_name,
            severity=severity,
            message=message,
            progress_percent=progress_percent,
            data=data or {},
        )
        return await self.publish(event)
    
    # =========================================================================
    # Subscription APIs
    # =========================================================================
    
    async def subscribe_session(
        self,
        conn_id: str,
        session_id: UUID,
        include_history: bool = True,
    ) -> EventSubscription:
        """
        Subscribe to all events for a session.
        
        Args:
            conn_id: Connection identifier
            session_id: Session to subscribe to
            include_history: Whether to replay past events
        
        Returns:
            EventSubscription for consuming events
        """
        subscription_id = f"{conn_id}:{session_id}"
        filter = create_session_filter(session_id)
        
        subscription = await self._stream.subscribe(
            subscription_id=subscription_id,
            filter=filter,
        )
        
        # Track subscription for connection
        async with self._lock:
            if conn_id not in self._conn_subscriptions:
                self._conn_subscriptions[conn_id] = []
            self._conn_subscriptions[conn_id].append(subscription_id)
        
        # Replay history if requested
        if include_history:
            history = await self.get_session_history(session_id)
            for event in history:
                subscription.deliver(event)
        
        return subscription
    
    async def subscribe_execution(
        self,
        conn_id: str,
        session_id: UUID,
        exec_id: str,
    ) -> EventSubscription:
        """
        Subscribe to events for a specific execution.
        
        Args:
            conn_id: Connection identifier
            session_id: Session the execution belongs to
            exec_id: Execution to subscribe to
        
        Returns:
            EventSubscription for consuming events
        """
        subscription_id = f"{conn_id}:{session_id}:{exec_id}"
        filter = create_execution_filter(session_id, exec_id)
        
        subscription = await self._stream.subscribe(
            subscription_id=subscription_id,
            filter=filter,
        )
        
        # Track subscription for connection
        async with self._lock:
            if conn_id not in self._conn_subscriptions:
                self._conn_subscriptions[conn_id] = []
            self._conn_subscriptions[conn_id].append(subscription_id)
        
        return subscription
    
    async def subscribe_custom(
        self,
        conn_id: str,
        filter: EventFilter,
        subscription_suffix: str = "",
    ) -> EventSubscription:
        """
        Create a custom subscription.
        
        Args:
            conn_id: Connection identifier
            filter: Custom event filter
            subscription_suffix: Optional suffix for subscription ID
        
        Returns:
            EventSubscription for consuming events
        """
        suffix = f":{subscription_suffix}" if subscription_suffix else f":{uuid4()}"
        subscription_id = f"{conn_id}{suffix}"
        
        subscription = await self._stream.subscribe(
            subscription_id=subscription_id,
            filter=filter,
        )
        
        # Track subscription for connection
        async with self._lock:
            if conn_id not in self._conn_subscriptions:
                self._conn_subscriptions[conn_id] = []
            self._conn_subscriptions[conn_id].append(subscription_id)
        
        return subscription
    
    async def unsubscribe(self, subscription_id: str) -> bool:
        """Unsubscribe by subscription ID."""
        return await self._stream.unsubscribe(subscription_id)
    
    async def unsubscribe_connection(self, conn_id: str) -> int:
        """
        Unsubscribe all subscriptions for a connection.
        
        Called when a connection is closed.
        
        Args:
            conn_id: Connection identifier
        
        Returns:
            Number of subscriptions removed
        """
        async with self._lock:
            subscription_ids = self._conn_subscriptions.pop(conn_id, [])
        
        removed = 0
        for subscription_id in subscription_ids:
            if await self._stream.unsubscribe(subscription_id):
                removed += 1
        
        if removed > 0:
            logger.debug(f"Removed {removed} subscriptions for connection {conn_id}")
        
        return removed
    
    # =========================================================================
    # History APIs
    # =========================================================================
    
    async def get_session_history(
        self,
        session_id: UUID,
        since: datetime | None = None,
        event_types: list[EventType] | None = None,
    ) -> list[BaseEvent]:
        """
        Get event history for a session.
        
        Args:
            session_id: Session to get history for
            since: Only events after this time
            event_types: Filter by event types
        
        Returns:
            List of events (oldest first)
        """
        async with self._lock:
            history = self._history.get(session_id, []).copy()
        
        if since:
            history = [e for e in history if e.timestamp > since]
        
        if event_types:
            type_set = set(event_types)
            history = [e for e in history if e.event_type in type_set]
        
        return history
    
    async def clear_session_history(self, session_id: UUID) -> int:
        """
        Clear event history for a session.
        
        Returns:
            Number of events cleared
        """
        async with self._lock:
            history = self._history.pop(session_id, [])
            return len(history)
    
    # =========================================================================
    # Cleanup
    # =========================================================================
    
    async def close(self) -> None:
        """Close the event manager and all subscriptions."""
        await self._stream.close_all()
        async with self._lock:
            self._history.clear()
            self._conn_subscriptions.clear()
