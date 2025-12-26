"""
Semantic Trace Store

Append-only in-memory store for recording significant events in the LIP lifecycle.
Used for observability, debugging, and replay analysis.

Design:
- Keyed by ephemeral_interface_id (session-centric view)
- Secondary index by exec_id for execution-focused queries
- Events are immutable once appended
- For streaming chunks, only metadata (length, hash) is stored to limit memory

Event Categories:
- Agent: register, unregister, timeout
- Intent: broadcast, deliver, offer, agree
- Session: created, closed, expired
- Policy: decision (allow/deny with reason)
- Execution: request, accepted, result, error
- Streaming: stream.start, stream.chunk (metadata only), stream.end

Supports optional persistent storage via TraceStore interface.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, TYPE_CHECKING
from uuid import UUID
import hashlib

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.storage import TraceStore as TraceStorePort, TraceRecord

class TraceEventType(str, Enum):
    """Categories of traceable events."""
    # Agent lifecycle
    AGENT_REGISTER = "agent.register"
    AGENT_UNREGISTER = "agent.unregister"
    AGENT_TIMEOUT = "agent.timeout"
    
    # Intent negotiation
    INTENT_BROADCAST = "intent.broadcast"
    INTENT_DELIVER = "intent.deliver"
    INTENT_OFFER = "intent.offer"
    INTENT_OFFERS = "intent.offers"
    INTENT_AGREE = "intent.agree"
    
    # Session lifecycle
    SESSION_CREATED = "session.created"
    SESSION_CLOSED = "session.closed"
    SESSION_EXPIRED = "session.expired"
    
    # Policy
    POLICY_DECISION = "policy.decision"
    
    # Execution
    EXEC_REQUEST = "exec.request"
    EXEC_ACCEPTED = "exec.accepted"
    EXEC_PROGRESS = "exec.progress"
    EXEC_RESULT = "exec.result"
    EXEC_ERROR = "exec.error"
    
    # Streaming
    STREAM_START = "stream.start"
    STREAM_CHUNK = "stream.chunk"  # Metadata only
    STREAM_END = "stream.end"
    STREAM_BACKPRESSURE = "stream.backpressure"
    
    # Trace
    TRACE_GET = "trace.get"


class TraceEvent(BaseModel):
    """
    A single trace event.
    
    Immutable record of something significant that happened in the bus.
    """
    # Identity
    event_id: UUID = Field(
        ...,
        description="Unique event identifier"
    )
    event_type: TraceEventType = Field(
        ...,
        description="Category of event"
    )
    
    # Context
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the event occurred"
    )
    tenant_id: str = Field(
        ...,
        description="Tenant context"
    )
    ephemeral_interface_id: UUID | None = Field(
        default=None,
        description="Session this event belongs to (if any)"
    )
    exec_id: str | None = Field(
        default=None,
        description="Execution this event belongs to (if any)"
    )
    
    # Actors
    agent_id: UUID | None = Field(
        default=None,
        description="Agent that triggered or is subject of the event"
    )
    conn_id: str | None = Field(
        default=None,
        description="Connection ID (for agent events)"
    )
    
    # Payload - event-specific data
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Event-specific metadata"
    )
    
    def to_dict(self) -> dict[str, Any]:
        """Serialize for trace.result payload."""
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "tenant_id": self.tenant_id,
            "ephemeral_interface_id": str(self.ephemeral_interface_id) if self.ephemeral_interface_id else None,
            "exec_id": self.exec_id,
            "agent_id": str(self.agent_id) if self.agent_id else None,
            "conn_id": self.conn_id,
            "data": self.data
        }


class SemanticTraceStore:
    """
    In-memory append-only trace store with optional persistent storage.
    
    Thread-safe for concurrent reads/writes via list append semantics.
    
    If a TraceStore is provided, events are persisted.
    Otherwise, events are stored in-memory only.
    """
    
    def __init__(
        self,
        max_events_per_session: int = 10000,
        storage: "TraceStorePort | None" = None,
    ):
        """
        Initialize the trace store.
        
        Args:
            max_events_per_session: Max events to keep per session (oldest evicted)
            storage: Optional persistent storage adapter
        """
        self._max_events = max_events_per_session
        self._storage = storage
        
        # Primary storage: session_id -> list of events
        self._by_session: dict[str, list[TraceEvent]] = {}
        
        # Secondary index: exec_id -> list of event indices (session_id, idx)
        self._by_exec: dict[str, list[tuple[str, int]]] = {}
        
        # Global events (no session context, e.g., agent.register)
        self._global: list[TraceEvent] = []
    
    async def append_async(self, event: TraceEvent) -> None:
        """
        Append a trace event (async version for persistent storage).
        
        Routes to appropriate storage based on context.
        """
        # Persist if storage configured
        if self._storage:
            from src.storage import TraceRecord
            record = TraceRecord(
                event_id=event.event_id,
                event_type=event.event_type.value,
                timestamp=event.timestamp,
                tenant_id=event.tenant_id,
                ephemeral_interface_id=event.ephemeral_interface_id,
                exec_id=event.exec_id,
                agent_id=event.agent_id,
                conn_id=event.conn_id,
                data=event.data,
            )
            await self._storage.append(record)
        
        # Also keep in-memory for fast access
        self.append(event)
    
    def append(self, event: TraceEvent) -> None:
        """
        Append a trace event (sync, in-memory only).
        
        Routes to appropriate storage based on context.
        """
        session_key = str(event.ephemeral_interface_id) if event.ephemeral_interface_id else None
        
        if session_key:
            # Session-scoped event
            if session_key not in self._by_session:
                self._by_session[session_key] = []
            
            events = self._by_session[session_key]
            idx = len(events)
            events.append(event)
            
            # Secondary index by exec_id
            if event.exec_id:
                if event.exec_id not in self._by_exec:
                    self._by_exec[event.exec_id] = []
                self._by_exec[event.exec_id].append((session_key, idx))
            
            # Evict oldest if over limit
            if len(events) > self._max_events:
                # Remove oldest 10%
                to_remove = self._max_events // 10
                del events[:to_remove]
                # Note: This invalidates exec_id index, but for simplicity we don't rebuild
        else:
            # Global event
            self._global.append(event)
            if len(self._global) > self._max_events:
                del self._global[:self._max_events // 10]
    
    def get_by_session(
        self,
        session_id: str | UUID,
        tail: int = 100,
        exec_id: str | None = None
    ) -> tuple[list[TraceEvent], bool]:
        """
        Get trace events for a session.
        
        Args:
            session_id: Session to query
            tail: Max events to return (most recent)
            exec_id: Optional filter by execution ID
            
        Returns:
            Tuple of (events, truncated)
        """
        key = str(session_id)
        events = self._by_session.get(key, [])
        
        if exec_id:
            # Filter by exec_id
            events = [e for e in events if e.exec_id == exec_id]
        
        truncated = len(events) > tail
        return events[-tail:], truncated
    
    def get_by_exec(
        self,
        exec_id: str,
        tail: int = 100
    ) -> tuple[list[TraceEvent], bool]:
        """
        Get trace events for an execution ID.
        
        Args:
            exec_id: Execution to query
            tail: Max events to return
            
        Returns:
            Tuple of (events, truncated)
        """
        refs = self._by_exec.get(exec_id, [])
        events = []
        
        for session_key, idx in refs:
            session_events = self._by_session.get(session_key, [])
            if idx < len(session_events):
                events.append(session_events[idx])
        
        truncated = len(events) > tail
        return events[-tail:], truncated
    
    def get_global(self, tail: int = 100) -> tuple[list[TraceEvent], bool]:
        """
        Get global events (agent lifecycle, etc).
        
        Returns:
            Tuple of (events, truncated)
        """
        truncated = len(self._global) > tail
        return self._global[-tail:], truncated
    
    def session_count(self) -> int:
        """Number of sessions with traces."""
        return len(self._by_session)
    
    def total_events(self) -> int:
        """Total events across all sessions."""
        return sum(len(e) for e in self._by_session.values()) + len(self._global)


# =============================================================================
# Helper functions to create trace events
# =============================================================================

def _short_hash(data: str | bytes, length: int = 8) -> str:
    """Create a short hash of data for stream chunk metadata."""
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()[:length]


def trace_agent_register(
    tenant_id: str,
    agent_id: UUID,
    conn_id: str,
    capabilities: list[str] | None = None
) -> TraceEvent:
    """Create trace event for agent registration."""
    from uuid import uuid4
    return TraceEvent(
        event_id=uuid4(),
        event_type=TraceEventType.AGENT_REGISTER,
        tenant_id=tenant_id,
        agent_id=agent_id,
        conn_id=conn_id,
        data={"capabilities": capabilities or []}
    )


def trace_agent_unregister(
    tenant_id: str,
    agent_id: UUID,
    conn_id: str,
    reason: str = "explicit"
) -> TraceEvent:
    """Create trace event for agent unregistration."""
    from uuid import uuid4
    return TraceEvent(
        event_id=uuid4(),
        event_type=TraceEventType.AGENT_UNREGISTER,
        tenant_id=tenant_id,
        agent_id=agent_id,
        conn_id=conn_id,
        data={"reason": reason}
    )


def trace_agent_timeout(
    tenant_id: str,
    agent_id: UUID,
    conn_id: str
) -> TraceEvent:
    """Create trace event for agent heartbeat timeout."""
    from uuid import uuid4
    return TraceEvent(
        event_id=uuid4(),
        event_type=TraceEventType.AGENT_TIMEOUT,
        tenant_id=tenant_id,
        agent_id=agent_id,
        conn_id=conn_id,
        data={}
    )


def trace_intent_broadcast(
    tenant_id: str,
    session_id: UUID,
    agent_id: UUID,
    intent_type: str,
    candidate_count: int
) -> TraceEvent:
    """Create trace event for intent broadcast."""
    from uuid import uuid4
    return TraceEvent(
        event_id=uuid4(),
        event_type=TraceEventType.INTENT_BROADCAST,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        agent_id=agent_id,
        data={"intent_type": intent_type, "candidate_count": candidate_count}
    )


def trace_intent_offer(
    tenant_id: str,
    session_id: UUID,
    agent_id: UUID,
    offer_id: str,
    view_ids: list[str] | None = None
) -> TraceEvent:
    """Create trace event for intent offer."""
    from uuid import uuid4
    return TraceEvent(
        event_id=uuid4(),
        event_type=TraceEventType.INTENT_OFFER,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        agent_id=agent_id,
        data={"offer_id": offer_id, "view_ids": view_ids or []}
    )


def trace_intent_agree(
    tenant_id: str,
    session_id: UUID,
    requester_id: UUID,
    provider_id: UUID,
    offer_id: str,
    chosen_view_id: str | None = None
) -> TraceEvent:
    """Create trace event for intent agreement."""
    from uuid import uuid4
    return TraceEvent(
        event_id=uuid4(),
        event_type=TraceEventType.INTENT_AGREE,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        agent_id=requester_id,
        data={
            "offer_id": offer_id,
            "provider_id": str(provider_id),
            "chosen_view_id": chosen_view_id
        }
    )


def trace_session_created(
    tenant_id: str,
    session_id: UUID,
    requester_id: UUID,
    provider_id: UUID
) -> TraceEvent:
    """Create trace event for session creation."""
    from uuid import uuid4
    return TraceEvent(
        event_id=uuid4(),
        event_type=TraceEventType.SESSION_CREATED,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        data={
            "requester_id": str(requester_id),
            "provider_id": str(provider_id)
        }
    )


def trace_session_closed(
    tenant_id: str,
    session_id: UUID,
    reason: str = "explicit"
) -> TraceEvent:
    """Create trace event for session close."""
    from uuid import uuid4
    return TraceEvent(
        event_id=uuid4(),
        event_type=TraceEventType.SESSION_CLOSED,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        data={"reason": reason}
    )


def trace_policy_decision(
    tenant_id: str,
    session_id: UUID,
    exec_id: str,
    agent_id: UUID,
    allowed: bool,
    reason: str,
    purpose: str | None = None
) -> TraceEvent:
    """Create trace event for IBAC policy decision."""
    from uuid import uuid4
    return TraceEvent(
        event_id=uuid4(),
        event_type=TraceEventType.POLICY_DECISION,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        exec_id=exec_id,
        agent_id=agent_id,
        data={
            "allowed": allowed,
            "reason": reason,
            "purpose": purpose
        }
    )


def trace_exec_request(
    tenant_id: str,
    session_id: UUID,
    exec_id: str,
    agent_id: UUID,
    action: str | None = None
) -> TraceEvent:
    """Create trace event for exec.request."""
    from uuid import uuid4
    return TraceEvent(
        event_id=uuid4(),
        event_type=TraceEventType.EXEC_REQUEST,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        exec_id=exec_id,
        agent_id=agent_id,
        data={"action": action}
    )


def trace_exec_result(
    tenant_id: str,
    session_id: UUID,
    exec_id: str,
    agent_id: UUID,
    view_valid: bool | None = None
) -> TraceEvent:
    """Create trace event for exec.result."""
    from uuid import uuid4
    return TraceEvent(
        event_id=uuid4(),
        event_type=TraceEventType.EXEC_RESULT,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        exec_id=exec_id,
        agent_id=agent_id,
        data={"view_valid": view_valid}
    )


def trace_exec_error(
    tenant_id: str,
    session_id: UUID,
    exec_id: str | None,
    agent_id: UUID,
    error_code: str,
    error_message: str
) -> TraceEvent:
    """Create trace event for exec.error."""
    from uuid import uuid4
    return TraceEvent(
        event_id=uuid4(),
        event_type=TraceEventType.EXEC_ERROR,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        exec_id=exec_id,
        agent_id=agent_id,
        data={"error_code": error_code, "error_message": error_message}
    )


def trace_stream_start(
    tenant_id: str,
    session_id: UUID,
    exec_id: str,
    agent_id: UUID,
    content_type: str | None = None
) -> TraceEvent:
    """Create trace event for stream start."""
    from uuid import uuid4
    return TraceEvent(
        event_id=uuid4(),
        event_type=TraceEventType.STREAM_START,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        exec_id=exec_id,
        agent_id=agent_id,
        data={"content_type": content_type}
    )


def trace_stream_chunk(
    tenant_id: str,
    session_id: UUID,
    exec_id: str,
    seq: int,
    chunk_len: int,
    chunk_hash: str | None = None
) -> TraceEvent:
    """
    Create trace event for stream chunk.
    
    NOTE: Does NOT store chunk data, only metadata.
    """
    from uuid import uuid4
    return TraceEvent(
        event_id=uuid4(),
        event_type=TraceEventType.STREAM_CHUNK,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        exec_id=exec_id,
        data={
            "seq": seq,
            "chunk_len": chunk_len,
            "chunk_hash": chunk_hash
        }
    )


def trace_stream_end(
    tenant_id: str,
    session_id: UUID,
    exec_id: str,
    total_chunks: int | None = None,
    total_bytes: int | None = None
) -> TraceEvent:
    """Create trace event for stream end."""
    from uuid import uuid4
    return TraceEvent(
        event_id=uuid4(),
        event_type=TraceEventType.STREAM_END,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        exec_id=exec_id,
        data={
            "total_chunks": total_chunks,
            "total_bytes": total_bytes
        }
    )


def trace_stream_backpressure(
    tenant_id: str,
    session_id: UUID,
    exec_id: str,
    queue_size: int
) -> TraceEvent:
    """Create trace event for backpressure termination."""
    from uuid import uuid4
    return TraceEvent(
        event_id=uuid4(),
        event_type=TraceEventType.STREAM_BACKPRESSURE,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        exec_id=exec_id,
        data={"queue_size": queue_size}
    )
