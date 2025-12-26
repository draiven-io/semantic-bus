"""
Storage Port Interfaces

Abstract base classes defining the storage contracts for the LIP Semantic Bus.
All persistence APIs are async. No sync DB calls allowed.

These ports follow the hexagonal architecture pattern:
- Protocol/bus code depends only on these interfaces
- Adapters (in-memory, SQLAlchemy, Redis) implement these interfaces
- Storage is injected via dependency inversion

Thread-safety: All implementations must be safe for concurrent async usage.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, TypeVar, Generic
from uuid import UUID
from dataclasses import dataclass


# =============================================================================
# Session Store
# =============================================================================

@dataclass
class SessionRecord:
    """
    Stored session data.
    
    This is a storage-level representation, decoupled from the protocol Session model.
    """
    ephemeral_interface_id: UUID
    tenant_id: str
    requester_agent_id: UUID
    requester_conn_id: str
    provider_agent_id: UUID | None
    provider_conn_id: str | None
    intent_message_id: UUID
    intent_data: dict[str, Any]
    status: str  # "negotiating", "active", "closed", "expired"
    offers: list[dict[str, Any]]  # Serialized offers
    chosen_offer_id: str | None
    chosen_schema_id: str | None
    chosen_view_id: str | None
    created_at: datetime
    activated_at: datetime | None
    closed_at: datetime | None
    negotiation_ttl_seconds: int
    session_ttl_seconds: int
    metadata: dict[str, Any]


class SessionStore(ABC):
    """
    Storage interface for session lifecycle management.
    
    Handles ephemeral interface sessions from negotiation through execution.
    """
    
    @abstractmethod
    async def create(self, record: SessionRecord) -> SessionRecord:
        """
        Create a new session.
        
        Args:
            record: Session data to persist
            
        Returns:
            The created record (may have updated fields like timestamps)
            
        Raises:
            StorageError: If creation fails
        """
        ...
    
    @abstractmethod
    async def get(self, session_id: UUID) -> SessionRecord | None:
        """
        Get a session by ID.
        
        Args:
            session_id: The ephemeral_interface_id
            
        Returns:
            Session record or None if not found
        """
        ...
    
    @abstractmethod
    async def update(self, record: SessionRecord) -> SessionRecord:
        """
        Update an existing session.
        
        Args:
            record: Updated session data
            
        Returns:
            The updated record
            
        Raises:
            StorageError: If session not found or update fails
        """
        ...
    
    @abstractmethod
    async def add_offer(
        self,
        session_id: UUID,
        offer: dict[str, Any]
    ) -> SessionRecord | None:
        """
        Add an offer to a session.
        
        Atomic operation to append an offer to the offers list.
        
        Args:
            session_id: Target session
            offer: Serialized offer data
            
        Returns:
            Updated session record or None if session not found
        """
        ...
    
    @abstractmethod
    async def select_offer(
        self,
        session_id: UUID,
        offer_id: str,
        provider_agent_id: UUID,
        provider_conn_id: str,
        chosen_schema_id: str | None,
        chosen_view_id: str | None
    ) -> SessionRecord | None:
        """
        Accept an offer and activate the session.
        
        Atomic operation to:
        - Set status to "active"
        - Set provider info
        - Set chosen offer/schema/view
        - Set activated_at timestamp
        
        Args:
            session_id: Target session
            offer_id: Accepted offer ID
            provider_agent_id: Provider's agent ID
            provider_conn_id: Provider's connection ID
            chosen_schema_id: Agreed schema
            chosen_view_id: Agreed view
            
        Returns:
            Updated session record or None if session not found
        """
        ...
    
    @abstractmethod
    async def close(
        self,
        session_id: UUID,
        reason: str | None = None
    ) -> SessionRecord | None:
        """
        Close a session.
        
        Args:
            session_id: Target session
            reason: Optional close reason
            
        Returns:
            Updated session record or None if not found
        """
        ...
    
    @abstractmethod
    async def expire_before(self, cutoff: datetime) -> int:
        """
        Expire sessions that should have ended before the cutoff.
        
        Args:
            cutoff: Expiration threshold
            
        Returns:
            Number of sessions expired
        """
        ...
    
    @abstractmethod
    async def list_by_tenant(
        self,
        tenant_id: str,
        status: str | None = None,
        limit: int = 100
    ) -> list[SessionRecord]:
        """
        List sessions for a tenant.
        
        Args:
            tenant_id: Target tenant
            status: Optional status filter
            limit: Max results
            
        Returns:
            List of session records
        """
        ...


# =============================================================================
# Trace Store
# =============================================================================

@dataclass
class TraceRecord:
    """
    Stored trace event.
    """
    event_id: UUID
    event_type: str
    timestamp: datetime
    tenant_id: str
    ephemeral_interface_id: UUID | None
    exec_id: str | None
    agent_id: UUID | None
    conn_id: str | None
    data: dict[str, Any]


class TraceStore(ABC):
    """
    Storage interface for semantic trace events.
    
    Append-only store for observability and debugging.
    """
    
    @abstractmethod
    async def append(self, record: TraceRecord) -> None:
        """
        Append a trace event.
        
        Args:
            record: Event to store
        """
        ...
    
    @abstractmethod
    async def tail(
        self,
        session_id: UUID | None = None,
        exec_id: str | None = None,
        limit: int = 100
    ) -> tuple[list[TraceRecord], bool]:
        """
        Get the most recent trace events.
        
        Args:
            session_id: Optional filter by session
            exec_id: Optional filter by execution
            limit: Max events to return
            
        Returns:
            Tuple of (events, truncated)
        """
        ...
    
    @abstractmethod
    async def query(
        self,
        tenant_id: str | None = None,
        session_id: UUID | None = None,
        exec_id: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100
    ) -> list[TraceRecord]:
        """
        Query trace events with filters.
        
        Args:
            tenant_id: Filter by tenant
            session_id: Filter by session
            exec_id: Filter by execution
            event_type: Filter by event type
            since: Events after this time
            until: Events before this time
            limit: Max results
            
        Returns:
            Matching trace records
        """
        ...


# =============================================================================
# Exec Store
# =============================================================================

@dataclass
class ExecRecord:
    """
    Stored execution record for idempotency and status tracking.
    """
    exec_id: UUID
    session_id: UUID
    correlation_id: UUID
    idempotency_key: str | None
    status: str  # "pending", "accepted", "in_progress", "completed", "failed", "rejected"
    requester_conn_id: str
    action: str | None
    result_view_id: str | None
    result_valid: bool | None
    policy_allowed: bool
    policy_reason: str | None
    created_at: datetime
    accepted_at: datetime | None
    completed_at: datetime | None
    metadata: dict[str, Any]


class ExecStore(ABC):
    """
    Storage interface for execution records.
    
    Provides idempotency via put_if_absent and status tracking.
    """
    
    @abstractmethod
    async def put_if_absent(
        self,
        record: ExecRecord
    ) -> tuple[ExecRecord, bool]:
        """
        Create an execution record if it doesn't exist.
        
        Idempotency check:
        1. If idempotency_key is set, check for existing record with same key
        2. If exec_id already exists, return existing record
        3. Otherwise, create new record
        
        Args:
            record: Execution record to create
            
        Returns:
            Tuple of (record, created)
            - created=True if new record was created
            - created=False if existing record was returned
        """
        ...
    
    @abstractmethod
    async def get(
        self,
        exec_id: UUID | None = None,
        idempotency_key: str | None = None
    ) -> ExecRecord | None:
        """
        Get an execution record.
        
        Args:
            exec_id: Lookup by exec_id
            idempotency_key: Or lookup by idempotency key
            
        Returns:
            Execution record or None
        """
        ...
    
    @abstractmethod
    async def set_status(
        self,
        exec_id: UUID,
        status: str,
        **kwargs: Any
    ) -> ExecRecord | None:
        """
        Update execution status and optional fields.
        
        Args:
            exec_id: Target execution
            status: New status
            **kwargs: Additional fields to update (accepted_at, completed_at, etc.)
            
        Returns:
            Updated record or None if not found
        """
        ...
    
    @abstractmethod
    async def list_by_session(
        self,
        session_id: UUID,
        status: str | None = None
    ) -> list[ExecRecord]:
        """
        List executions for a session.
        
        Args:
            session_id: Target session
            status: Optional status filter
            
        Returns:
            List of execution records
        """
        ...


# =============================================================================
# Registry Store (Optional - for distributed presence)
# =============================================================================

@dataclass
class PresenceRecord:
    """
    Stored agent presence information.
    """
    agent_id: UUID
    tenant_id: str
    conn_id: str
    status: str  # "online", "offline", "draining"
    capabilities: list[str]
    tags: list[str]
    load: float
    last_seen: datetime
    registered_at: datetime
    metadata: dict[str, Any]


class RegistryStore(ABC):
    """
    Storage interface for agent presence (optional).
    
    Used for distributed deployments where presence needs to be shared.
    """
    
    @abstractmethod
    async def upsert_presence(self, record: PresenceRecord) -> PresenceRecord:
        """
        Create or update agent presence.
        
        Args:
            record: Presence data
            
        Returns:
            Updated record
        """
        ...
    
    @abstractmethod
    async def heartbeat(
        self,
        conn_id: str,
        load: float | None = None
    ) -> bool:
        """
        Update heartbeat timestamp.
        
        Args:
            conn_id: Connection to update
            load: Optional updated load
            
        Returns:
            True if updated, False if not found
        """
        ...
    
    @abstractmethod
    async def remove(self, conn_id: str) -> bool:
        """
        Remove agent presence.
        
        Args:
            conn_id: Connection to remove
            
        Returns:
            True if removed, False if not found
        """
        ...
    
    @abstractmethod
    async def list_by_tenant(
        self,
        tenant_id: str,
        status: str | None = None,
        capability: str | None = None,
        tag: str | None = None
    ) -> list[PresenceRecord]:
        """
        List agents for a tenant with optional filters.
        
        Args:
            tenant_id: Target tenant
            status: Filter by status
            capability: Filter by capability
            tag: Filter by tag
            
        Returns:
            List of presence records
        """
        ...
    
    @abstractmethod
    async def expire_stale(self, timeout_seconds: float) -> int:
        """
        Mark agents as offline if they haven't sent heartbeat.
        
        Args:
            timeout_seconds: Staleness threshold
            
        Returns:
            Number of agents marked offline
        """
        ...


# =============================================================================
# Storage Bundle
# =============================================================================

@dataclass
class StorageBundle:
    """
    Container for all storage adapters.
    
    Injected into bus components via dependency inversion.
    """
    sessions: SessionStore
    trace: TraceStore
    execs: ExecStore
    registry: RegistryStore | None = None  # Optional for single-node deployments
    
    async def close(self) -> None:
        """
        Close all storage connections.
        
        Called during shutdown.
        """
        # Implementations should override to close DB connections, etc.
        pass


# =============================================================================
# Exceptions
# =============================================================================

class StorageError(Exception):
    """Base exception for storage errors."""
    pass


class NotFoundError(StorageError):
    """Record not found."""
    pass


class ConflictError(StorageError):
    """Conflict during write (e.g., duplicate key)."""
    pass
