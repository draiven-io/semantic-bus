"""
SQLAlchemy Storage Adapters

Async SQLAlchemy 2.0 implementations for production persistence.
Uses async engine and session for all operations.

Compatible with:
- SQLite (aiosqlite)
- PostgreSQL (asyncpg)
- MySQL (aiomysql)

All operations are async. No sync DB calls.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, update, delete, and_, or_
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    create_async_engine,
    async_sessionmaker,
)

from src.storage.ports import (
    SessionStore,
    SessionRecord,
    TraceStore,
    TraceRecord,
    ExecStore,
    ExecRecord,
    RegistryStore,
    PresenceRecord,
    StorageBundle,
    NotFoundError,
    ConflictError,
)
from src.storage.models import (
    Base,
    SessionModel,
    ExecModel,
    TraceEventModel,
    PresenceModel,
)


# =============================================================================
# Converters
# =============================================================================

def session_model_to_record(model: SessionModel) -> SessionRecord:
    """Convert SQLAlchemy model to port record."""
    return SessionRecord(
        ephemeral_interface_id=model.ephemeral_interface_id,
        tenant_id=model.tenant_id,
        requester_agent_id=model.requester_agent_id,
        requester_conn_id=model.requester_conn_id,
        provider_agent_id=model.provider_agent_id,
        provider_conn_id=model.provider_conn_id,
        intent_message_id=model.intent_message_id,
        intent_data=model.intent_data,
        status=model.status,
        offers=model.offers,
        chosen_offer_id=model.chosen_offer_id,
        chosen_schema_id=model.chosen_schema_id,
        chosen_view_id=model.chosen_view_id,
        created_at=model.created_at,
        activated_at=model.activated_at,
        closed_at=model.closed_at,
        negotiation_ttl_seconds=model.negotiation_ttl_seconds,
        session_ttl_seconds=model.session_ttl_seconds,
        metadata=model.metadata_json,
    )


def session_record_to_model(record: SessionRecord) -> SessionModel:
    """Convert port record to SQLAlchemy model."""
    return SessionModel(
        ephemeral_interface_id=record.ephemeral_interface_id,
        tenant_id=record.tenant_id,
        requester_agent_id=record.requester_agent_id,
        requester_conn_id=record.requester_conn_id,
        provider_agent_id=record.provider_agent_id,
        provider_conn_id=record.provider_conn_id,
        intent_message_id=record.intent_message_id,
        intent_data=record.intent_data,
        status=record.status,
        offers=record.offers,
        chosen_offer_id=record.chosen_offer_id,
        chosen_schema_id=record.chosen_schema_id,
        chosen_view_id=record.chosen_view_id,
        created_at=record.created_at,
        activated_at=record.activated_at,
        closed_at=record.closed_at,
        negotiation_ttl_seconds=record.negotiation_ttl_seconds,
        session_ttl_seconds=record.session_ttl_seconds,
        metadata_json=record.metadata,
    )


def exec_model_to_record(model: ExecModel) -> ExecRecord:
    """Convert SQLAlchemy model to port record."""
    return ExecRecord(
        exec_id=model.exec_id,
        session_id=model.session_id,
        correlation_id=model.correlation_id,
        idempotency_key=model.idempotency_key,
        status=model.status,
        requester_conn_id=model.requester_conn_id,
        action=model.action,
        result_view_id=model.result_view_id,
        result_valid=model.result_valid,
        policy_allowed=model.policy_allowed,
        policy_reason=model.policy_reason,
        created_at=model.created_at,
        accepted_at=model.accepted_at,
        completed_at=model.completed_at,
        metadata=model.metadata_json,
    )


def exec_record_to_model(record: ExecRecord) -> ExecModel:
    """Convert port record to SQLAlchemy model."""
    return ExecModel(
        exec_id=record.exec_id,
        session_id=record.session_id,
        correlation_id=record.correlation_id,
        idempotency_key=record.idempotency_key,
        status=record.status,
        requester_conn_id=record.requester_conn_id,
        action=record.action,
        result_view_id=record.result_view_id,
        result_valid=record.result_valid,
        policy_allowed=record.policy_allowed,
        policy_reason=record.policy_reason,
        created_at=record.created_at,
        accepted_at=record.accepted_at,
        completed_at=record.completed_at,
        metadata_json=record.metadata,
    )


def trace_model_to_record(model: TraceEventModel) -> TraceRecord:
    """Convert SQLAlchemy model to port record."""
    return TraceRecord(
        event_id=model.event_id,
        event_type=model.event_type,
        timestamp=model.timestamp,
        tenant_id=model.tenant_id,
        ephemeral_interface_id=model.ephemeral_interface_id,
        exec_id=model.exec_id,
        agent_id=model.agent_id,
        conn_id=model.conn_id,
        data=model.data,
    )


def trace_record_to_model(record: TraceRecord) -> TraceEventModel:
    """Convert port record to SQLAlchemy model."""
    return TraceEventModel(
        event_id=record.event_id,
        event_type=record.event_type,
        timestamp=record.timestamp,
        tenant_id=record.tenant_id,
        ephemeral_interface_id=record.ephemeral_interface_id,
        exec_id=record.exec_id,
        agent_id=record.agent_id,
        conn_id=record.conn_id,
        data=record.data,
    )


def presence_model_to_record(model: PresenceModel) -> PresenceRecord:
    """Convert SQLAlchemy model to port record."""
    return PresenceRecord(
        agent_id=model.agent_id,
        tenant_id=model.tenant_id,
        conn_id=model.conn_id,
        status=model.status,
        capabilities=model.capabilities,
        tags=model.tags,
        load=model.load,
        last_seen=model.last_seen,
        registered_at=model.registered_at,
        metadata=model.metadata_json,
    )


# =============================================================================
# SQLAlchemy Session Store
# =============================================================================

class SqlAlchemySessionStore(SessionStore):
    """
    SQLAlchemy implementation of session storage.
    """
    
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory
    
    async def create(self, record: SessionRecord) -> SessionRecord:
        async with self._session_factory() as session:
            async with session.begin():
                # Check if exists (idempotent create)
                existing = await session.get(SessionModel, record.ephemeral_interface_id)
                if existing:
                    return session_model_to_record(existing)
                
                model = session_record_to_model(record)
                session.add(model)
            
            return record
    
    async def get(self, session_id: UUID) -> SessionRecord | None:
        async with self._session_factory() as session:
            model = await session.get(SessionModel, session_id)
            if model is None:
                return None
            return session_model_to_record(model)
    
    async def update(self, record: SessionRecord) -> SessionRecord:
        async with self._session_factory() as session:
            async with session.begin():
                existing = await session.get(SessionModel, record.ephemeral_interface_id)
                if existing is None:
                    raise NotFoundError(f"Session {record.ephemeral_interface_id} not found")
                
                # Update all fields
                existing.status = record.status
                existing.provider_agent_id = record.provider_agent_id
                existing.provider_conn_id = record.provider_conn_id
                existing.offers = record.offers
                existing.chosen_offer_id = record.chosen_offer_id
                existing.chosen_schema_id = record.chosen_schema_id
                existing.chosen_view_id = record.chosen_view_id
                existing.activated_at = record.activated_at
                existing.closed_at = record.closed_at
                existing.metadata_json = record.metadata
            
            return record
    
    async def add_offer(
        self,
        session_id: UUID,
        offer: dict[str, Any]
    ) -> SessionRecord | None:
        async with self._session_factory() as session:
            async with session.begin():
                model = await session.get(SessionModel, session_id)
                if model is None:
                    return None
                
                # Append offer to list
                model.offers = list(model.offers) + [offer]
                
                return session_model_to_record(model)
    
    async def select_offer(
        self,
        session_id: UUID,
        offer_id: str,
        provider_agent_id: UUID,
        provider_conn_id: str,
        chosen_schema_id: str | None,
        chosen_view_id: str | None
    ) -> SessionRecord | None:
        async with self._session_factory() as session:
            async with session.begin():
                model = await session.get(SessionModel, session_id)
                if model is None:
                    return None
                
                model.status = "active"
                model.chosen_offer_id = offer_id
                model.provider_agent_id = provider_agent_id
                model.provider_conn_id = provider_conn_id
                model.chosen_schema_id = chosen_schema_id
                model.chosen_view_id = chosen_view_id
                model.activated_at = datetime.utcnow()
                
                return session_model_to_record(model)
    
    async def close(
        self,
        session_id: UUID,
        reason: str | None = None
    ) -> SessionRecord | None:
        async with self._session_factory() as session:
            async with session.begin():
                model = await session.get(SessionModel, session_id)
                if model is None:
                    return None
                
                model.status = "closed"
                model.closed_at = datetime.utcnow()
                if reason:
                    model.metadata_json = dict(model.metadata_json)
                    model.metadata_json["close_reason"] = reason
                
                return session_model_to_record(model)
    
    async def expire_before(self, cutoff: datetime) -> int:
        async with self._session_factory() as session:
            async with session.begin():
                # Find sessions that should be expired
                result = await session.execute(
                    select(SessionModel).where(
                        SessionModel.status.in_(["negotiating", "active"])
                    )
                )
                sessions = result.scalars().all()
                
                count = 0
                for model in sessions:
                    if model.status == "negotiating":
                        expires = model.created_at + timedelta(seconds=model.negotiation_ttl_seconds)
                    else:
                        if model.activated_at:
                            expires = model.activated_at + timedelta(seconds=model.session_ttl_seconds)
                        else:
                            continue
                    
                    if expires < cutoff:
                        model.status = "expired"
                        model.closed_at = datetime.utcnow()
                        count += 1
                
                return count
    
    async def list_by_tenant(
        self,
        tenant_id: str,
        status: str | None = None,
        limit: int = 100
    ) -> list[SessionRecord]:
        async with self._session_factory() as session:
            query = select(SessionModel).where(SessionModel.tenant_id == tenant_id)
            if status:
                query = query.where(SessionModel.status == status)
            query = query.limit(limit)
            
            result = await session.execute(query)
            return [session_model_to_record(m) for m in result.scalars().all()]


# =============================================================================
# SQLAlchemy Trace Store
# =============================================================================

class SqlAlchemyTraceStore(TraceStore):
    """
    SQLAlchemy implementation of trace storage.
    """
    
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory
    
    async def append(self, record: TraceRecord) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                model = trace_record_to_model(record)
                session.add(model)
    
    async def tail(
        self,
        session_id: UUID | None = None,
        exec_id: str | None = None,
        limit: int = 100
    ) -> tuple[list[TraceRecord], bool]:
        async with self._session_factory() as session:
            query = select(TraceEventModel)
            
            if session_id:
                query = query.where(TraceEventModel.ephemeral_interface_id == session_id)
            if exec_id:
                query = query.where(TraceEventModel.exec_id == exec_id)
            
            # Get count for truncation check
            count_query = query
            query = query.order_by(TraceEventModel.timestamp.desc()).limit(limit + 1)
            
            result = await session.execute(query)
            events = list(result.scalars().all())
            
            truncated = len(events) > limit
            events = events[:limit]
            events.reverse()  # Return in chronological order
            
            return [trace_model_to_record(e) for e in events], truncated
    
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
        async with self._session_factory() as session:
            query = select(TraceEventModel)
            
            conditions = []
            if tenant_id:
                conditions.append(TraceEventModel.tenant_id == tenant_id)
            if session_id:
                conditions.append(TraceEventModel.ephemeral_interface_id == session_id)
            if exec_id:
                conditions.append(TraceEventModel.exec_id == exec_id)
            if event_type:
                conditions.append(TraceEventModel.event_type == event_type)
            if since:
                conditions.append(TraceEventModel.timestamp >= since)
            if until:
                conditions.append(TraceEventModel.timestamp <= until)
            
            if conditions:
                query = query.where(and_(*conditions))
            
            query = query.order_by(TraceEventModel.timestamp.desc()).limit(limit)
            
            result = await session.execute(query)
            events = list(result.scalars().all())
            events.reverse()
            
            return [trace_model_to_record(e) for e in events]


# =============================================================================
# SQLAlchemy Exec Store
# =============================================================================

class SqlAlchemyExecStore(ExecStore):
    """
    SQLAlchemy implementation of execution storage.
    """
    
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory
    
    async def put_if_absent(
        self,
        record: ExecRecord
    ) -> tuple[ExecRecord, bool]:
        async with self._session_factory() as session:
            async with session.begin():
                # Check idempotency key first
                if record.idempotency_key:
                    result = await session.execute(
                        select(ExecModel).where(
                            ExecModel.idempotency_key == record.idempotency_key
                        )
                    )
                    existing = result.scalar_one_or_none()
                    if existing:
                        return exec_model_to_record(existing), False
                
                # Check exec_id
                existing = await session.get(ExecModel, record.exec_id)
                if existing:
                    return exec_model_to_record(existing), False
                
                # Create new
                model = exec_record_to_model(record)
                session.add(model)
                
                return record, True
    
    async def get(
        self,
        exec_id: UUID | None = None,
        idempotency_key: str | None = None
    ) -> ExecRecord | None:
        async with self._session_factory() as session:
            if exec_id:
                model = await session.get(ExecModel, exec_id)
                if model:
                    return exec_model_to_record(model)
            
            if idempotency_key:
                result = await session.execute(
                    select(ExecModel).where(
                        ExecModel.idempotency_key == idempotency_key
                    )
                )
                model = result.scalar_one_or_none()
                if model:
                    return exec_model_to_record(model)
            
            return None
    
    async def set_status(
        self,
        exec_id: UUID,
        status: str,
        **kwargs: Any
    ) -> ExecRecord | None:
        async with self._session_factory() as session:
            async with session.begin():
                model = await session.get(ExecModel, exec_id)
                if model is None:
                    return None
                
                model.status = status
                
                if "accepted_at" in kwargs:
                    model.accepted_at = kwargs["accepted_at"]
                if "completed_at" in kwargs:
                    model.completed_at = kwargs["completed_at"]
                if "result_view_id" in kwargs:
                    model.result_view_id = kwargs["result_view_id"]
                if "result_valid" in kwargs:
                    model.result_valid = kwargs["result_valid"]
                
                return exec_model_to_record(model)
    
    async def list_by_session(
        self,
        session_id: UUID,
        status: str | None = None
    ) -> list[ExecRecord]:
        async with self._session_factory() as session:
            query = select(ExecModel).where(ExecModel.session_id == session_id)
            if status:
                query = query.where(ExecModel.status == status)
            
            result = await session.execute(query)
            return [exec_model_to_record(m) for m in result.scalars().all()]


# =============================================================================
# SQLAlchemy Registry Store
# =============================================================================

class SqlAlchemyRegistryStore(RegistryStore):
    """
    SQLAlchemy implementation of registry storage.
    """
    
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory
    
    async def upsert_presence(self, record: PresenceRecord) -> PresenceRecord:
        async with self._session_factory() as session:
            async with session.begin():
                # Remove old connection for same agent
                result = await session.execute(
                    select(PresenceModel).where(
                        and_(
                            PresenceModel.agent_id == record.agent_id,
                            PresenceModel.conn_id != record.conn_id
                        )
                    )
                )
                old = result.scalar_one_or_none()
                if old:
                    await session.delete(old)
                
                # Upsert
                existing = await session.get(PresenceModel, record.conn_id)
                if existing:
                    existing.status = record.status
                    existing.capabilities = record.capabilities
                    existing.tags = record.tags
                    existing.load = record.load
                    existing.last_seen = record.last_seen
                    existing.metadata_json = record.metadata
                else:
                    model = PresenceModel(
                        conn_id=record.conn_id,
                        agent_id=record.agent_id,
                        tenant_id=record.tenant_id,
                        status=record.status,
                        capabilities=record.capabilities,
                        tags=record.tags,
                        load=record.load,
                        last_seen=record.last_seen,
                        registered_at=record.registered_at,
                        metadata_json=record.metadata,
                    )
                    session.add(model)
                
                return record
    
    async def heartbeat(
        self,
        conn_id: str,
        load: float | None = None
    ) -> bool:
        async with self._session_factory() as session:
            async with session.begin():
                model = await session.get(PresenceModel, conn_id)
                if model is None:
                    return False
                
                model.last_seen = datetime.utcnow()
                if load is not None:
                    model.load = load
                
                return True
    
    async def remove(self, conn_id: str) -> bool:
        async with self._session_factory() as session:
            async with session.begin():
                model = await session.get(PresenceModel, conn_id)
                if model is None:
                    return False
                
                await session.delete(model)
                return True
    
    async def list_by_tenant(
        self,
        tenant_id: str,
        status: str | None = None,
        capability: str | None = None,
        tag: str | None = None
    ) -> list[PresenceRecord]:
        async with self._session_factory() as session:
            query = select(PresenceModel).where(PresenceModel.tenant_id == tenant_id)
            
            if status:
                query = query.where(PresenceModel.status == status)
            
            result = await session.execute(query)
            records = []
            
            for model in result.scalars().all():
                # Post-filter for capability/tag (JSON arrays)
                if capability and capability not in model.capabilities:
                    continue
                if tag and tag not in model.tags:
                    continue
                records.append(presence_model_to_record(model))
            
            return records
    
    async def expire_stale(self, timeout_seconds: float) -> int:
        async with self._session_factory() as session:
            async with session.begin():
                cutoff = datetime.utcnow() - timedelta(seconds=timeout_seconds)
                
                result = await session.execute(
                    update(PresenceModel)
                    .where(
                        and_(
                            PresenceModel.status == "online",
                            PresenceModel.last_seen < cutoff
                        )
                    )
                    .values(status="offline")
                )
                
                return result.rowcount or 0


# =============================================================================
# Bundle Factory
# =============================================================================

async def create_sqlalchemy_bundle(
    database_url: str,
    echo: bool = False,
    create_tables: bool = True
) -> StorageBundle:
    """
    Create a storage bundle with SQLAlchemy adapters.
    
    Args:
        database_url: Async database URL (e.g., "sqlite+aiosqlite:///./bus.db")
        echo: Enable SQL logging
        create_tables: Auto-create tables if not exist
        
    Returns:
        StorageBundle with SQLAlchemy adapters
    """
    engine = create_async_engine(database_url, echo=echo)
    
    if create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    bundle = StorageBundle(
        sessions=SqlAlchemySessionStore(session_factory),
        trace=SqlAlchemyTraceStore(session_factory),
        execs=SqlAlchemyExecStore(session_factory),
        registry=SqlAlchemyRegistryStore(session_factory),
    )
    
    # Override close to dispose engine
    async def close():
        await engine.dispose()
    
    bundle.close = close
    
    return bundle
