"""
Event Stream Infrastructure

Provides pub/sub mechanism for real-time event delivery to requesters.
Supports filtering by event type, session, and severity.

Design:
- EventStream: Core pub/sub infrastructure
- EventSubscription: Per-subscriber queue with filtering
- EventFilter: Configurable event filtering

The stream is session-aware - events are automatically routed to
subscribers watching specific sessions or all sessions.
"""

import asyncio
import logging
from datetime import datetime
from typing import AsyncIterator, Callable, Awaitable, Any
from uuid import UUID
from weakref import WeakSet

from pydantic import BaseModel, Field

from src.events.models import BaseEvent, EventType, EventSeverity

logger = logging.getLogger(__name__)


class EventFilter(BaseModel):
    """
    Filter configuration for event subscriptions.
    
    Events must match ALL specified criteria (AND logic).
    Empty/None values mean "match any".
    """
    # Session filtering
    session_ids: set[UUID] | None = Field(
        default=None,
        description="Only events for these sessions (None = all sessions)"
    )
    
    # Execution filtering
    exec_ids: set[str] | None = Field(
        default=None,
        description="Only events for these execution IDs (None = all)"
    )
    
    # Type filtering
    event_types: set[EventType] | None = Field(
        default=None,
        description="Only these event types (None = all types)"
    )
    event_type_prefixes: list[str] | None = Field(
        default=None,
        description="Event types starting with these prefixes (e.g., 'flow.', 'reasoning.')"
    )
    
    # Severity filtering
    min_severity: EventSeverity = Field(
        default=EventSeverity.DEBUG,
        description="Minimum severity level to include"
    )
    
    # Source filtering
    source_agent_ids: set[UUID] | None = Field(
        default=None,
        description="Only events from these agents (None = all agents)"
    )
    
    # Tenant filtering
    tenant_ids: set[str] | None = Field(
        default=None,
        description="Only events for these tenants (None = all tenants)"
    )
    
    class Config:
        arbitrary_types_allowed = True
    
    def matches(self, event: BaseEvent) -> bool:
        """Check if an event matches this filter."""
        # Session filter
        if self.session_ids is not None:
            if event.session_id not in self.session_ids:
                return False
        
        # Execution filter
        if self.exec_ids is not None:
            if event.exec_id not in self.exec_ids:
                return False
        
        # Event type filter
        if self.event_types is not None:
            if event.event_type not in self.event_types:
                return False
        
        # Event type prefix filter
        if self.event_type_prefixes is not None:
            type_value = event.event_type.value
            if not any(type_value.startswith(prefix) for prefix in self.event_type_prefixes):
                return False
        
        # Severity filter (ordered comparison)
        severity_order = {
            EventSeverity.DEBUG: 0,
            EventSeverity.INFO: 1,
            EventSeverity.WARNING: 2,
            EventSeverity.ERROR: 3,
            EventSeverity.CRITICAL: 4,
        }
        if severity_order.get(event.severity, 0) < severity_order.get(self.min_severity, 0):
            return False
        
        # Source agent filter
        if self.source_agent_ids is not None:
            if event.source_agent_id not in self.source_agent_ids:
                return False
        
        # Tenant filter
        if self.tenant_ids is not None:
            if event.tenant_id not in self.tenant_ids:
                return False
        
        return True


class EventSubscription:
    """
    A single subscription to the event stream.
    
    Maintains a queue of events matching the subscription's filter.
    Supports async iteration for consuming events.
    """
    
    def __init__(
        self,
        subscription_id: str,
        filter: EventFilter | None = None,
        max_queue_size: int = 1000,
    ):
        """
        Initialize subscription.
        
        Args:
            subscription_id: Unique identifier for this subscription
            filter: Event filter (None = receive all events)
            max_queue_size: Maximum events to queue before dropping
        """
        self.subscription_id = subscription_id
        self.filter = filter or EventFilter()
        self._queue: asyncio.Queue[BaseEvent | None] = asyncio.Queue(maxsize=max_queue_size)
        self._closed = False
        self._created_at = datetime.utcnow()
        self._events_received = 0
        self._events_dropped = 0
    
    @property
    def is_closed(self) -> bool:
        """Check if subscription is closed."""
        return self._closed
    
    @property
    def stats(self) -> dict[str, Any]:
        """Get subscription statistics."""
        return {
            "subscription_id": self.subscription_id,
            "created_at": self._created_at.isoformat(),
            "events_received": self._events_received,
            "events_dropped": self._events_dropped,
            "queue_size": self._queue.qsize(),
            "is_closed": self._closed,
        }
    
    def deliver(self, event: BaseEvent) -> bool:
        """
        Deliver an event to this subscription.
        
        Returns:
            True if delivered, False if dropped (queue full or closed)
        """
        if self._closed:
            return False
        
        # Check filter
        if not self.filter.matches(event):
            return False
        
        try:
            self._queue.put_nowait(event)
            self._events_received += 1
            return True
        except asyncio.QueueFull:
            self._events_dropped += 1
            logger.warning(
                f"Event dropped for subscription {self.subscription_id}: queue full"
            )
            return False
    
    async def get(self, timeout: float | None = None) -> BaseEvent | None:
        """
        Get the next event from the subscription.
        
        Args:
            timeout: Max seconds to wait (None = wait forever)
        
        Returns:
            Next event, or None if subscription closed or timeout
        """
        if self._closed:
            return None
        
        try:
            if timeout is not None:
                event = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            else:
                event = await self._queue.get()
            return event
        except asyncio.TimeoutError:
            return None
    
    async def __aiter__(self) -> AsyncIterator[BaseEvent]:
        """Async iterate over events."""
        while not self._closed:
            event = await self.get()
            if event is None:
                break
            yield event
    
    def close(self) -> None:
        """Close the subscription."""
        self._closed = True
        # Signal end of stream
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass


class EventStream:
    """
    Central event stream for publishing and subscribing to events.
    
    Thread-safe for concurrent operations.
    Supports multiple subscribers with individual filters.
    """
    
    def __init__(self, max_subscribers: int = 10000):
        """
        Initialize the event stream.
        
        Args:
            max_subscribers: Maximum concurrent subscriptions
        """
        self._max_subscribers = max_subscribers
        self._subscriptions: dict[str, EventSubscription] = {}
        self._lock = asyncio.Lock()
        
        # Statistics
        self._events_published = 0
        self._events_delivered = 0
    
    @property
    def stats(self) -> dict[str, Any]:
        """Get stream statistics."""
        return {
            "subscriber_count": len(self._subscriptions),
            "events_published": self._events_published,
            "events_delivered": self._events_delivered,
        }
    
    async def subscribe(
        self,
        subscription_id: str,
        filter: EventFilter | None = None,
        max_queue_size: int = 1000,
    ) -> EventSubscription:
        """
        Create a new subscription.
        
        Args:
            subscription_id: Unique identifier for the subscription
            filter: Event filter (None = receive all events)
            max_queue_size: Maximum events to queue
        
        Returns:
            The subscription object
        
        Raises:
            ValueError: If subscription_id already exists or max subscribers reached
        """
        async with self._lock:
            if subscription_id in self._subscriptions:
                raise ValueError(f"Subscription {subscription_id} already exists")
            
            if len(self._subscriptions) >= self._max_subscribers:
                raise ValueError(f"Maximum subscribers ({self._max_subscribers}) reached")
            
            subscription = EventSubscription(
                subscription_id=subscription_id,
                filter=filter,
                max_queue_size=max_queue_size,
            )
            self._subscriptions[subscription_id] = subscription
            logger.debug(f"New subscription: {subscription_id}")
            return subscription
    
    async def unsubscribe(self, subscription_id: str) -> bool:
        """
        Remove a subscription.
        
        Args:
            subscription_id: The subscription to remove
        
        Returns:
            True if removed, False if not found
        """
        async with self._lock:
            subscription = self._subscriptions.pop(subscription_id, None)
            if subscription:
                subscription.close()
                logger.debug(f"Subscription removed: {subscription_id}")
                return True
            return False
    
    async def publish(self, event: BaseEvent) -> int:
        """
        Publish an event to all matching subscribers.
        
        Args:
            event: The event to publish
        
        Returns:
            Number of subscribers that received the event
        """
        self._events_published += 1
        delivered = 0
        
        # Copy subscriptions to avoid holding lock during delivery
        async with self._lock:
            subscriptions = list(self._subscriptions.values())
        
        for subscription in subscriptions:
            if subscription.deliver(event):
                delivered += 1
        
        self._events_delivered += delivered
        return delivered
    
    async def publish_many(self, events: list[BaseEvent]) -> int:
        """
        Publish multiple events.
        
        Args:
            events: List of events to publish
        
        Returns:
            Total number of deliveries
        """
        total_delivered = 0
        for event in events:
            total_delivered += await self.publish(event)
        return total_delivered
    
    def get_subscription(self, subscription_id: str) -> EventSubscription | None:
        """Get a subscription by ID."""
        return self._subscriptions.get(subscription_id)
    
    async def close_all(self) -> None:
        """Close all subscriptions."""
        async with self._lock:
            for subscription in self._subscriptions.values():
                subscription.close()
            self._subscriptions.clear()


# Convenience functions for creating filtered subscriptions

def create_session_filter(session_id: UUID) -> EventFilter:
    """Create a filter for a specific session."""
    return EventFilter(session_ids={session_id})


def create_execution_filter(session_id: UUID, exec_id: str) -> EventFilter:
    """Create a filter for a specific execution."""
    return EventFilter(session_ids={session_id}, exec_ids={exec_id})


def create_flow_filter(session_id: UUID) -> EventFilter:
    """Create a filter for flow events only."""
    return EventFilter(
        session_ids={session_id},
        event_type_prefixes=["flow."],
    )


def create_reasoning_filter(session_id: UUID) -> EventFilter:
    """Create a filter for reasoning events only."""
    return EventFilter(
        session_ids={session_id},
        event_type_prefixes=["reasoning."],
    )


def create_progress_filter(session_id: UUID, min_severity: EventSeverity = EventSeverity.INFO) -> EventFilter:
    """Create a filter for progress-related events."""
    return EventFilter(
        session_ids={session_id},
        event_type_prefixes=["execution.", "flow.", "agent."],
        min_severity=min_severity,
    )
