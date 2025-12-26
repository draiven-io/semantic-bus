"""
In-Memory Storage Adapters

Thread-safe implementations for development and testing.
Uses asyncio locks for concurrent async safety.

These adapters store everything in memory and are lost on restart.
Use for:
- Local development
- Unit/integration testing
- Single-node deployments without persistence requirements
"""

import asyncio
from datetime import datetime
from typing import Any
from uuid import UUID

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
)


class InMemorySessionStore(SessionStore):
    """
    In-memory session storage.
    
    Uses dict with asyncio.Lock for thread-safety.
    """
    
    def __init__(self):
        self._sessions: dict[UUID, SessionRecord] = {}
        self._lock = asyncio.Lock()
    
    async def create(self, record: SessionRecord) -> SessionRecord:
        async with self._lock:
            if record.ephemeral_interface_id in self._sessions:
                # Return existing if already created (idempotent)
                return self._sessions[record.ephemeral_interface_id]
            self._sessions[record.ephemeral_interface_id] = record
            return record
    
    async def get(self, session_id: UUID) -> SessionRecord | None:
        async with self._lock:
            return self._sessions.get(session_id)
    
    async def update(self, record: SessionRecord) -> SessionRecord:
        async with self._lock:
            if record.ephemeral_interface_id not in self._sessions:
                raise NotFoundError(f"Session {record.ephemeral_interface_id} not found")
            self._sessions[record.ephemeral_interface_id] = record
            return record
    
    async def add_offer(
        self,
        session_id: UUID,
        offer: dict[str, Any]
    ) -> SessionRecord | None:
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return None
            
            # Create new list to avoid mutation issues
            record.offers = list(record.offers) + [offer]
            return record
    
    async def select_offer(
        self,
        session_id: UUID,
        offer_id: str,
        provider_agent_id: UUID,
        provider_conn_id: str,
        chosen_schema_id: str | None,
        chosen_view_id: str | None
    ) -> SessionRecord | None:
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return None
            
            record.status = "active"
            record.chosen_offer_id = offer_id
            record.provider_agent_id = provider_agent_id
            record.provider_conn_id = provider_conn_id
            record.chosen_schema_id = chosen_schema_id
            record.chosen_view_id = chosen_view_id
            record.activated_at = datetime.utcnow()
            return record
    
    async def close(
        self,
        session_id: UUID,
        reason: str | None = None
    ) -> SessionRecord | None:
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return None
            
            record.status = "closed"
            record.closed_at = datetime.utcnow()
            if reason:
                record.metadata = dict(record.metadata)
                record.metadata["close_reason"] = reason
            return record
    
    async def expire_before(self, cutoff: datetime) -> int:
        async with self._lock:
            count = 0
            for record in self._sessions.values():
                if record.status in ("negotiating", "active"):
                    # Check expiration
                    if record.status == "negotiating":
                        from datetime import timedelta
                        expires = record.created_at + timedelta(seconds=record.negotiation_ttl_seconds)
                    else:
                        if record.activated_at:
                            from datetime import timedelta
                            expires = record.activated_at + timedelta(seconds=record.session_ttl_seconds)
                        else:
                            continue
                    
                    if expires < cutoff:
                        record.status = "expired"
                        record.closed_at = datetime.utcnow()
                        count += 1
            return count
    
    async def list_by_tenant(
        self,
        tenant_id: str,
        status: str | None = None,
        limit: int = 100
    ) -> list[SessionRecord]:
        async with self._lock:
            results = []
            for record in self._sessions.values():
                if record.tenant_id == tenant_id:
                    if status is None or record.status == status:
                        results.append(record)
                        if len(results) >= limit:
                            break
            return results


class InMemoryTraceStore(TraceStore):
    """
    In-memory trace storage.
    
    Append-only with configurable max size.
    """
    
    def __init__(self, max_events: int = 100000):
        self._max_events = max_events
        self._events: list[TraceRecord] = []
        self._by_session: dict[UUID, list[int]] = {}  # session_id -> event indices
        self._by_exec: dict[str, list[int]] = {}  # exec_id -> event indices
        self._lock = asyncio.Lock()
    
    async def append(self, record: TraceRecord) -> None:
        async with self._lock:
            idx = len(self._events)
            self._events.append(record)
            
            # Index by session
            if record.ephemeral_interface_id:
                key = record.ephemeral_interface_id
                if key not in self._by_session:
                    self._by_session[key] = []
                self._by_session[key].append(idx)
            
            # Index by exec
            if record.exec_id:
                if record.exec_id not in self._by_exec:
                    self._by_exec[record.exec_id] = []
                self._by_exec[record.exec_id].append(idx)
            
            # Evict oldest if over limit
            if len(self._events) > self._max_events:
                # Simple eviction - remove oldest 10%
                to_remove = self._max_events // 10
                self._events = self._events[to_remove:]
                # Note: Indices become invalid, but for simplicity we rebuild on next query
                self._by_session.clear()
                self._by_exec.clear()
    
    async def tail(
        self,
        session_id: UUID | None = None,
        exec_id: str | None = None,
        limit: int = 100
    ) -> tuple[list[TraceRecord], bool]:
        async with self._lock:
            if session_id:
                indices = self._by_session.get(session_id, [])
                events = [self._events[i] for i in indices if i < len(self._events)]
                if exec_id:
                    events = [e for e in events if e.exec_id == exec_id]
            elif exec_id:
                indices = self._by_exec.get(exec_id, [])
                events = [self._events[i] for i in indices if i < len(self._events)]
            else:
                events = list(self._events)
            
            truncated = len(events) > limit
            return events[-limit:], truncated
    
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
        async with self._lock:
            results = []
            for event in self._events:
                if tenant_id and event.tenant_id != tenant_id:
                    continue
                if session_id and event.ephemeral_interface_id != session_id:
                    continue
                if exec_id and event.exec_id != exec_id:
                    continue
                if event_type and event.event_type != event_type:
                    continue
                if since and event.timestamp < since:
                    continue
                if until and event.timestamp > until:
                    continue
                results.append(event)
                if len(results) >= limit:
                    break
            return results


class InMemoryExecStore(ExecStore):
    """
    In-memory execution storage.
    
    Provides idempotency via put_if_absent.
    """
    
    def __init__(self):
        self._execs: dict[UUID, ExecRecord] = {}
        self._by_idempotency: dict[str, UUID] = {}  # idempotency_key -> exec_id
        self._by_session: dict[UUID, list[UUID]] = {}  # session_id -> exec_ids
        self._lock = asyncio.Lock()
    
    async def put_if_absent(
        self,
        record: ExecRecord
    ) -> tuple[ExecRecord, bool]:
        async with self._lock:
            # Check idempotency key first
            if record.idempotency_key:
                existing_id = self._by_idempotency.get(record.idempotency_key)
                if existing_id and existing_id in self._execs:
                    return self._execs[existing_id], False
            
            # Check exec_id
            if record.exec_id in self._execs:
                return self._execs[record.exec_id], False
            
            # Create new
            self._execs[record.exec_id] = record
            
            if record.idempotency_key:
                self._by_idempotency[record.idempotency_key] = record.exec_id
            
            if record.session_id not in self._by_session:
                self._by_session[record.session_id] = []
            self._by_session[record.session_id].append(record.exec_id)
            
            return record, True
    
    async def get(
        self,
        exec_id: UUID | None = None,
        idempotency_key: str | None = None
    ) -> ExecRecord | None:
        async with self._lock:
            if exec_id:
                return self._execs.get(exec_id)
            if idempotency_key:
                eid = self._by_idempotency.get(idempotency_key)
                if eid:
                    return self._execs.get(eid)
            return None
    
    async def set_status(
        self,
        exec_id: UUID,
        status: str,
        **kwargs: Any
    ) -> ExecRecord | None:
        async with self._lock:
            record = self._execs.get(exec_id)
            if record is None:
                return None
            
            record.status = status
            
            # Update optional fields
            if "accepted_at" in kwargs:
                record.accepted_at = kwargs["accepted_at"]
            if "completed_at" in kwargs:
                record.completed_at = kwargs["completed_at"]
            if "result_view_id" in kwargs:
                record.result_view_id = kwargs["result_view_id"]
            if "result_valid" in kwargs:
                record.result_valid = kwargs["result_valid"]
            
            return record
    
    async def list_by_session(
        self,
        session_id: UUID,
        status: str | None = None
    ) -> list[ExecRecord]:
        async with self._lock:
            exec_ids = self._by_session.get(session_id, [])
            results = []
            for eid in exec_ids:
                record = self._execs.get(eid)
                if record:
                    if status is None or record.status == status:
                        results.append(record)
            return results


class InMemoryRegistryStore(RegistryStore):
    """
    In-memory registry storage.
    
    For single-node deployments or testing.
    """
    
    def __init__(self):
        self._presence: dict[str, PresenceRecord] = {}  # conn_id -> record
        self._by_agent: dict[UUID, str] = {}  # agent_id -> conn_id
        self._lock = asyncio.Lock()
    
    async def upsert_presence(self, record: PresenceRecord) -> PresenceRecord:
        async with self._lock:
            # Remove old connection for same agent
            old_conn = self._by_agent.get(record.agent_id)
            if old_conn and old_conn != record.conn_id:
                self._presence.pop(old_conn, None)
            
            self._presence[record.conn_id] = record
            self._by_agent[record.agent_id] = record.conn_id
            return record
    
    async def heartbeat(
        self,
        conn_id: str,
        load: float | None = None
    ) -> bool:
        async with self._lock:
            record = self._presence.get(conn_id)
            if record is None:
                return False
            
            record.last_seen = datetime.utcnow()
            if load is not None:
                record.load = load
            return True
    
    async def remove(self, conn_id: str) -> bool:
        async with self._lock:
            record = self._presence.pop(conn_id, None)
            if record:
                self._by_agent.pop(record.agent_id, None)
                return True
            return False
    
    async def list_by_tenant(
        self,
        tenant_id: str,
        status: str | None = None,
        capability: str | None = None,
        tag: str | None = None
    ) -> list[PresenceRecord]:
        async with self._lock:
            results = []
            for record in self._presence.values():
                if record.tenant_id != tenant_id:
                    continue
                if status and record.status != status:
                    continue
                if capability and capability not in record.capabilities:
                    continue
                if tag and tag not in record.tags:
                    continue
                results.append(record)
            return results
    
    async def expire_stale(self, timeout_seconds: float) -> int:
        async with self._lock:
            from datetime import timedelta
            cutoff = datetime.utcnow() - timedelta(seconds=timeout_seconds)
            count = 0
            
            for record in self._presence.values():
                if record.status == "online" and record.last_seen < cutoff:
                    record.status = "offline"
                    count += 1
            
            return count


def create_in_memory_bundle() -> StorageBundle:
    """
    Create a storage bundle with all in-memory adapters.
    """
    return StorageBundle(
        sessions=InMemorySessionStore(),
        trace=InMemoryTraceStore(),
        execs=InMemoryExecStore(),
        registry=InMemoryRegistryStore()
    )
