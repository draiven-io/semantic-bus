"""
SQLAlchemy Models for LIP Storage

Async-compatible SQLAlchemy 2.0 ORM models for:
- Sessions (ephemeral interfaces)
- Executions (request/response tracking)
- Trace events (observability)

Designed to work with:
- SQLite (via aiosqlite)
- PostgreSQL (via asyncpg)
- MySQL (via aiomysql)

JSON column handling:
- PostgreSQL: Native JSONB
- SQLite/MySQL: TEXT with JSON serialization
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    String,
    Text,
    Float,
    Boolean,
    DateTime,
    Index,
    ForeignKey,
    event,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.types import TypeDecorator
import json


# =============================================================================
# Custom Types
# =============================================================================

class JSONType(TypeDecorator):
    """
    Platform-agnostic JSON column.
    
    Uses JSONB on PostgreSQL, TEXT+JSON on SQLite/MySQL.
    """
    impl = Text
    cache_ok = True
    
    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())
    
    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value  # JSONB handles dict directly
        return json.dumps(value)
    
    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value  # JSONB returns dict directly
        if isinstance(value, str):
            return json.loads(value)
        return value


class UUIDType(TypeDecorator):
    """
    Platform-agnostic UUID column.
    
    Uses native UUID on PostgreSQL, String(36) on SQLite/MySQL.
    """
    impl = String(36)
    cache_ok = True
    
    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))
    
    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return str(value)
    
    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, UUID):
            return value
        return UUID(value)


# =============================================================================
# Base
# =============================================================================

class Base(DeclarativeBase):
    """Base class for all models."""
    pass


# =============================================================================
# Session Model
# =============================================================================

class SessionModel(Base):
    """
    Ephemeral interface session.
    
    Tracks the lifecycle from intent broadcast through execution.
    """
    __tablename__ = "lip_sessions"
    
    # Primary key
    ephemeral_interface_id: Mapped[UUID] = mapped_column(
        UUIDType(),
        primary_key=True,
        default=uuid4
    )
    
    # Tenant isolation
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    
    # Requester
    requester_agent_id: Mapped[UUID] = mapped_column(UUIDType(), nullable=False)
    requester_conn_id: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Provider (set after agreement)
    provider_agent_id: Mapped[UUID | None] = mapped_column(UUIDType(), nullable=True)
    provider_conn_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    # Intent
    intent_message_id: Mapped[UUID] = mapped_column(UUIDType(), nullable=False)
    intent_data: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    
    # Status
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="negotiating",
        index=True
    )
    
    # Offers (stored as JSON array)
    offers: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    
    # Agreement
    chosen_offer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chosen_schema_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chosen_view_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    
    # TTL configuration
    negotiation_ttl_seconds: Mapped[int] = mapped_column(default=30)
    session_ttl_seconds: Mapped[int] = mapped_column(default=300)
    
    # Metadata
    metadata_json: Mapped[dict] = mapped_column(
        JSONType(),
        nullable=False,
        default=dict,
        name="metadata"
    )
    
    # Indexes
    __table_args__ = (
        Index("ix_sessions_tenant_status", "tenant_id", "status"),
        Index("ix_sessions_created_at", "created_at"),
    )


# =============================================================================
# Execution Model
# =============================================================================

class ExecModel(Base):
    """
    Execution record for idempotency and status tracking.
    """
    __tablename__ = "lip_execs"
    
    # Primary key
    exec_id: Mapped[UUID] = mapped_column(
        UUIDType(),
        primary_key=True,
        default=uuid4
    )
    
    # Session reference
    session_id: Mapped[UUID] = mapped_column(
        UUIDType(),
        nullable=False,
        index=True
    )
    
    # Correlation
    correlation_id: Mapped[UUID] = mapped_column(UUIDType(), nullable=False)
    
    # Idempotency
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
        index=True
    )
    
    # Status
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        index=True
    )
    
    # Requester info
    requester_conn_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    # Result validation
    result_view_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    
    # Policy
    policy_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    policy_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    
    # Metadata
    metadata_json: Mapped[dict] = mapped_column(
        JSONType(),
        nullable=False,
        default=dict,
        name="metadata"
    )
    
    # Indexes
    __table_args__ = (
        Index("ix_execs_session_status", "session_id", "status"),
    )


# =============================================================================
# Trace Event Model
# =============================================================================

class TraceEventModel(Base):
    """
    Semantic trace event for observability.
    """
    __tablename__ = "lip_trace_events"
    
    # Primary key
    event_id: Mapped[UUID] = mapped_column(
        UUIDType(),
        primary_key=True,
        default=uuid4
    )
    
    # Event type
    event_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True
    )
    
    # Timestamp
    timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True
    )
    
    # Context
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    ephemeral_interface_id: Mapped[UUID | None] = mapped_column(
        UUIDType(),
        nullable=True,
        index=True
    )
    exec_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    
    # Actor
    agent_id: Mapped[UUID | None] = mapped_column(UUIDType(), nullable=True)
    conn_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    # Event data
    data: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    
    # Indexes
    __table_args__ = (
        Index("ix_trace_session_timestamp", "ephemeral_interface_id", "timestamp"),
        Index("ix_trace_tenant_timestamp", "tenant_id", "timestamp"),
    )


# =============================================================================
# Presence Model (Optional - for distributed registry)
# =============================================================================

class PresenceModel(Base):
    """
    Agent presence for distributed deployments.
    """
    __tablename__ = "lip_presence"
    
    # Primary key is conn_id
    conn_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    
    # Agent identity
    agent_id: Mapped[UUID] = mapped_column(UUIDType(), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    
    # Status
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="online",
        index=True
    )
    
    # Capabilities
    capabilities: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    tags: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    
    # Load
    load: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    
    # Timestamps
    last_seen: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True
    )
    registered_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow
    )
    
    # Metadata
    metadata_json: Mapped[dict] = mapped_column(
        JSONType(),
        nullable=False,
        default=dict,
        name="metadata"
    )
    
    # Indexes
    __table_args__ = (
        Index("ix_presence_tenant_status", "tenant_id", "status"),
    )
