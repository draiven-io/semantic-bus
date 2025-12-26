"""
Redis Storage Adapters

Redis-based implementations of ExecStore and RegistryStore.
Ideal for:
- High-throughput execution idempotency with TTL
- Distributed agent presence with automatic expiration
- Ephemeral data that benefits from Redis speed

Uses redis.asyncio for async operations.

Note: SessionStore and TraceStore are better suited for SQL storage
due to their query requirements and data persistence needs.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from .ports import (
    ExecStore,
    ExecRecord,
    RegistryStore,
    PresenceRecord,
)


# =============================================================================
# Serialization Helpers
# =============================================================================

def _serialize_datetime(dt: datetime | None) -> str | None:
    """Serialize datetime to ISO format."""
    if dt is None:
        return None
    return dt.isoformat()


def _deserialize_datetime(s: str | None) -> datetime | None:
    """Deserialize ISO format to datetime."""
    if s is None:
        return None
    return datetime.fromisoformat(s)


def _serialize_uuid(u: UUID | None) -> str | None:
    """Serialize UUID to string."""
    if u is None:
        return None
    return str(u)


def _deserialize_uuid(s: str | None) -> UUID | None:
    """Deserialize string to UUID."""
    if s is None:
        return None
    return UUID(s)


# =============================================================================
# Redis Exec Store
# =============================================================================

class RedisExecStore(ExecStore):
    """
    Redis-based execution store with TTL support.
    
    Key patterns:
    - exec:{exec_id} -> JSON-encoded ExecRecord
    - exec:idem:{session_id}:{idempotency_key} -> exec_id (for idempotency lookup)
    - exec:session:{session_id} -> SET of exec_ids (for list_by_session)
    
    TTL is applied to all keys for automatic cleanup.
    """
    
    def __init__(
        self,
        redis: Redis,
        key_prefix: str = "lip",
        default_ttl_seconds: int = 86400 * 7,  # 7 days default
    ) -> None:
        """
        Initialize Redis exec store.
        
        Args:
            redis: Redis async client
            key_prefix: Prefix for all keys (multi-tenant isolation)
            default_ttl_seconds: Default TTL for records
        """
        self._redis = redis
        self._prefix = key_prefix
        self._ttl = default_ttl_seconds
    
    def _exec_key(self, exec_id: UUID) -> str:
        """Key for exec record."""
        return f"{self._prefix}:exec:{exec_id}"
    
    def _idem_key(self, session_id: UUID, idempotency_key: str) -> str:
        """Key for idempotency mapping."""
        return f"{self._prefix}:exec:idem:{session_id}:{idempotency_key}"
    
    def _session_index_key(self, session_id: UUID) -> str:
        """Key for session's exec index."""
        return f"{self._prefix}:exec:session:{session_id}"
    
    def _serialize_record(self, record: ExecRecord) -> str:
        """Serialize ExecRecord to JSON."""
        return json.dumps({
            "exec_id": _serialize_uuid(record.exec_id),
            "session_id": _serialize_uuid(record.session_id),
            "correlation_id": _serialize_uuid(record.correlation_id),
            "idempotency_key": record.idempotency_key,
            "status": record.status,
            "requester_conn_id": record.requester_conn_id,
            "action": record.action,
            "result_view_id": record.result_view_id,
            "result_valid": record.result_valid,
            "policy_allowed": record.policy_allowed,
            "policy_reason": record.policy_reason,
            "created_at": _serialize_datetime(record.created_at),
            "accepted_at": _serialize_datetime(record.accepted_at),
            "completed_at": _serialize_datetime(record.completed_at),
            "metadata": record.metadata,
        })
    
    def _deserialize_record(self, data: str) -> ExecRecord:
        """Deserialize JSON to ExecRecord."""
        d = json.loads(data)
        return ExecRecord(
            exec_id=_deserialize_uuid(d["exec_id"]),  # type: ignore
            session_id=_deserialize_uuid(d["session_id"]),  # type: ignore
            correlation_id=_deserialize_uuid(d["correlation_id"]),  # type: ignore
            idempotency_key=d["idempotency_key"],
            status=d["status"],
            requester_conn_id=d["requester_conn_id"],
            action=d.get("action"),
            result_view_id=d.get("result_view_id"),
            result_valid=d.get("result_valid"),
            policy_allowed=d["policy_allowed"],
            policy_reason=d.get("policy_reason"),
            created_at=_deserialize_datetime(d["created_at"]),  # type: ignore
            accepted_at=_deserialize_datetime(d.get("accepted_at")),
            completed_at=_deserialize_datetime(d.get("completed_at")),
            metadata=d.get("metadata", {}),
        )
    
    async def put_if_absent(
        self,
        record: ExecRecord
    ) -> tuple[ExecRecord, bool]:
        """
        Create execution record with idempotency check.
        
        Uses Redis SETNX for atomic put-if-absent semantics.
        """
        # Check idempotency key first if provided
        if record.idempotency_key:
            idem_key = self._idem_key(record.session_id, record.idempotency_key)
            existing_exec_id = await self._redis.get(idem_key)
            if existing_exec_id:
                # Return existing record
                existing = await self.get(exec_id=UUID(existing_exec_id.decode()))
                if existing:
                    return existing, False
        
        exec_key = self._exec_key(record.exec_id)
        serialized = self._serialize_record(record)
        
        # Atomic set-if-not-exists
        created = await self._redis.set(
            exec_key,
            serialized,
            ex=self._ttl,
            nx=True
        )
        
        if not created:
            # Record already exists
            existing_data = await self._redis.get(exec_key)
            if existing_data:
                return self._deserialize_record(existing_data.decode()), False
        
        # Set idempotency mapping if key provided
        if record.idempotency_key:
            idem_key = self._idem_key(record.session_id, record.idempotency_key)
            await self._redis.set(
                idem_key,
                str(record.exec_id),
                ex=self._ttl
            )
        
        # Add to session index
        session_key = self._session_index_key(record.session_id)
        await self._redis.sadd(session_key, str(record.exec_id))
        await self._redis.expire(session_key, self._ttl)
        
        return record, True
    
    async def get(
        self,
        exec_id: UUID | None = None,
        idempotency_key: str | None = None
    ) -> ExecRecord | None:
        """Get execution record by ID or idempotency key."""
        if exec_id is None and idempotency_key is None:
            return None
        
        # If only idempotency_key, we need session_id context
        # For now, require exec_id for direct lookup
        # Idempotency key lookup requires session context in put_if_absent
        
        if exec_id:
            data = await self._redis.get(self._exec_key(exec_id))
            if data:
                return self._deserialize_record(data.decode())
        
        return None
    
    async def set_status(
        self,
        exec_id: UUID,
        status: str,
        **kwargs: Any
    ) -> ExecRecord | None:
        """Update execution status."""
        record = await self.get(exec_id=exec_id)
        if not record:
            return None
        
        # Update fields
        record.status = status
        
        if "accepted_at" in kwargs:
            record.accepted_at = kwargs["accepted_at"]
        if "completed_at" in kwargs:
            record.completed_at = kwargs["completed_at"]
        if "result_view_id" in kwargs:
            record.result_view_id = kwargs["result_view_id"]
        if "result_valid" in kwargs:
            record.result_valid = kwargs["result_valid"]
        
        # Re-serialize and save
        serialized = self._serialize_record(record)
        await self._redis.set(
            self._exec_key(exec_id),
            serialized,
            ex=self._ttl
        )
        
        return record
    
    async def list_by_session(
        self,
        session_id: UUID,
        status: str | None = None
    ) -> list[ExecRecord]:
        """List executions for a session."""
        session_key = self._session_index_key(session_id)
        exec_ids = await self._redis.smembers(session_key)
        
        results: list[ExecRecord] = []
        
        # Pipeline fetch for efficiency
        if exec_ids:
            pipe = self._redis.pipeline()
            for exec_id_bytes in exec_ids:
                exec_id = UUID(exec_id_bytes.decode())
                pipe.get(self._exec_key(exec_id))
            
            values = await pipe.execute()
            
            for value in values:
                if value:
                    record = self._deserialize_record(value.decode())
                    if status is None or record.status == status:
                        results.append(record)
        
        return sorted(results, key=lambda r: r.created_at)


# =============================================================================
# Redis Registry Store
# =============================================================================

class RedisRegistryStore(RegistryStore):
    """
    Redis-based agent presence store.
    
    Key patterns:
    - presence:{conn_id} -> JSON-encoded PresenceRecord
    - presence:tenant:{tenant_id} -> SET of conn_ids
    - presence:heartbeat:{conn_id} -> timestamp (for staleness check)
    
    Uses Redis TTL for automatic presence expiration.
    """
    
    def __init__(
        self,
        redis: Redis,
        key_prefix: str = "lip",
        presence_ttl_seconds: int = 300,  # 5 minutes default
        heartbeat_interval_seconds: int = 30,
    ) -> None:
        """
        Initialize Redis registry store.
        
        Args:
            redis: Redis async client
            key_prefix: Prefix for all keys
            presence_ttl_seconds: TTL for presence records
            heartbeat_interval_seconds: Expected heartbeat interval
        """
        self._redis = redis
        self._prefix = key_prefix
        self._ttl = presence_ttl_seconds
        self._heartbeat_interval = heartbeat_interval_seconds
    
    def _presence_key(self, conn_id: str) -> str:
        """Key for presence record."""
        return f"{self._prefix}:presence:{conn_id}"
    
    def _tenant_index_key(self, tenant_id: str) -> str:
        """Key for tenant's presence index."""
        return f"{self._prefix}:presence:tenant:{tenant_id}"
    
    def _serialize_record(self, record: PresenceRecord) -> str:
        """Serialize PresenceRecord to JSON."""
        return json.dumps({
            "agent_id": _serialize_uuid(record.agent_id),
            "tenant_id": record.tenant_id,
            "conn_id": record.conn_id,
            "status": record.status,
            "capabilities": record.capabilities,
            "tags": record.tags,
            "load": record.load,
            "last_seen": _serialize_datetime(record.last_seen),
            "registered_at": _serialize_datetime(record.registered_at),
            "metadata": record.metadata,
        })
    
    def _deserialize_record(self, data: str) -> PresenceRecord:
        """Deserialize JSON to PresenceRecord."""
        d = json.loads(data)
        return PresenceRecord(
            agent_id=_deserialize_uuid(d["agent_id"]),  # type: ignore
            tenant_id=d["tenant_id"],
            conn_id=d["conn_id"],
            status=d["status"],
            capabilities=d.get("capabilities", []),
            tags=d.get("tags", []),
            load=d.get("load", 0.0),
            last_seen=_deserialize_datetime(d["last_seen"]),  # type: ignore
            registered_at=_deserialize_datetime(d["registered_at"]),  # type: ignore
            metadata=d.get("metadata", {}),
        )
    
    async def upsert_presence(self, record: PresenceRecord) -> PresenceRecord:
        """Create or update agent presence."""
        # Update last_seen to now
        record.last_seen = datetime.now(timezone.utc)
        
        presence_key = self._presence_key(record.conn_id)
        serialized = self._serialize_record(record)
        
        # Set presence with TTL
        await self._redis.set(
            presence_key,
            serialized,
            ex=self._ttl
        )
        
        # Add to tenant index
        tenant_key = self._tenant_index_key(record.tenant_id)
        await self._redis.sadd(tenant_key, record.conn_id)
        await self._redis.expire(tenant_key, self._ttl)
        
        return record
    
    async def heartbeat(
        self,
        conn_id: str,
        load: float | None = None
    ) -> bool:
        """Update heartbeat timestamp."""
        presence_key = self._presence_key(conn_id)
        data = await self._redis.get(presence_key)
        
        if not data:
            return False
        
        record = self._deserialize_record(data.decode())
        record.last_seen = datetime.now(timezone.utc)
        
        if load is not None:
            record.load = load
        
        serialized = self._serialize_record(record)
        await self._redis.set(
            presence_key,
            serialized,
            ex=self._ttl
        )
        
        return True
    
    async def remove(self, conn_id: str) -> bool:
        """Remove agent presence."""
        presence_key = self._presence_key(conn_id)
        
        # Get record to find tenant_id for index cleanup
        data = await self._redis.get(presence_key)
        if not data:
            return False
        
        record = self._deserialize_record(data.decode())
        
        # Remove from tenant index
        tenant_key = self._tenant_index_key(record.tenant_id)
        await self._redis.srem(tenant_key, conn_id)
        
        # Delete presence record
        deleted = await self._redis.delete(presence_key)
        
        return deleted > 0
    
    async def list_by_tenant(
        self,
        tenant_id: str,
        status: str | None = None,
        capability: str | None = None,
        tag: str | None = None
    ) -> list[PresenceRecord]:
        """List agents for a tenant with optional filters."""
        tenant_key = self._tenant_index_key(tenant_id)
        conn_ids = await self._redis.smembers(tenant_key)
        
        results: list[PresenceRecord] = []
        
        if not conn_ids:
            return results
        
        # Pipeline fetch for efficiency
        pipe = self._redis.pipeline()
        for conn_id_bytes in conn_ids:
            conn_id = conn_id_bytes.decode()
            pipe.get(self._presence_key(conn_id))
        
        values = await pipe.execute()
        
        for value in values:
            if value:
                record = self._deserialize_record(value.decode())
                
                # Apply filters
                if status and record.status != status:
                    continue
                if capability and capability not in record.capabilities:
                    continue
                if tag and tag not in record.tags:
                    continue
                
                results.append(record)
        
        return sorted(results, key=lambda r: r.registered_at)
    
    async def expire_stale(self, timeout_seconds: float) -> int:
        """
        Mark agents as offline if stale.
        
        Note: Redis TTL handles automatic deletion.
        This method is for explicit staleness marking.
        """
        # Get all presence keys matching pattern
        pattern = f"{self._prefix}:presence:*"
        cursor = 0
        expired_count = 0
        cutoff = datetime.now(timezone.utc)
        
        # SCAN through presence keys
        while True:
            cursor, keys = await self._redis.scan(
                cursor=cursor,
                match=pattern,
                count=100
            )
            
            for key in keys:
                # Skip index keys
                if b":tenant:" in key:
                    continue
                
                data = await self._redis.get(key)
                if data:
                    record = self._deserialize_record(data.decode())
                    age = (cutoff - record.last_seen).total_seconds()
                    
                    if age > timeout_seconds and record.status == "online":
                        record.status = "offline"
                        await self._redis.set(
                            key,
                            self._serialize_record(record),
                            ex=self._ttl
                        )
                        expired_count += 1
            
            if cursor == 0:
                break
        
        return expired_count
