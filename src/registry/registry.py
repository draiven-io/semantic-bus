"""
Agent Registry

In-memory registry tracking all connected agents, their capabilities,
and presence. Provides lookup methods for intent routing.

Integrates with ViewRegistry to manage agent schema lifecycle.

Why in-memory?
- Simplicity for initial implementation
- Low latency for routing decisions
- TODO: Add persistence layer for recovery
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, TYPE_CHECKING
from uuid import UUID

from fastapi import WebSocket

from src.registry.agent import AgentRecord, AgentStatus

if TYPE_CHECKING:
    from src.schema import ViewRegistry

logger = logging.getLogger(__name__)


class AgentRegistry:
    """
    Manages agent registration, presence, and capability lookup.
    
    Thread-safe for async operations using asyncio locks.
    Integrates with ViewRegistry to manage schema lifecycle.
    """
    
    def __init__(
        self,
        heartbeat_timeout_seconds: float = 30.0,
        view_registry: "ViewRegistry | None" = None
    ):
        """
        Initialize the registry.
        
        Args:
            heartbeat_timeout_seconds: Time after which an agent is considered offline
            view_registry: Optional ViewRegistry for schema management
        """
        self._heartbeat_timeout = heartbeat_timeout_seconds
        self._view_registry = view_registry
        
        # Primary index: conn_id -> AgentRecord
        self._agents_by_conn: dict[str, AgentRecord] = {}
        
        # Secondary index: agent_id -> conn_id (for lookups by agent_id)
        self._conn_by_agent: dict[UUID, str] = {}
        
        # Connection store: conn_id -> WebSocket
        self._connections: dict[str, WebSocket] = {}
        
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()
        
        # Background task for cleanup
        self._cleanup_task: asyncio.Task | None = None
    
    async def start(self) -> None:
        """Start background cleanup task."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("Agent registry cleanup task started")
    
    async def stop(self) -> None:
        """Stop background cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("Agent registry cleanup task stopped")
    
    async def _cleanup_loop(self) -> None:
        """
        Periodically check for dead agents and mark them offline.
        
        Runs every half the heartbeat timeout to catch dead agents quickly.
        """
        interval = self._heartbeat_timeout / 2
        while True:
            try:
                await asyncio.sleep(interval)
                await self._cleanup_dead_agents()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")
    
    async def _cleanup_dead_agents(self) -> None:
        """Mark agents as offline if they haven't sent a heartbeat."""
        async with self._lock:
            now = datetime.utcnow()
            dead_conn_ids = []
            
            for conn_id, agent in self._agents_by_conn.items():
                if not agent.is_alive(self._heartbeat_timeout):
                    if agent.status != AgentStatus.OFFLINE:
                        logger.info(
                            f"Agent {agent.agent_id} marked offline "
                            f"(last seen: {agent.last_seen})"
                        )
                        agent.status = AgentStatus.OFFLINE
                        dead_conn_ids.append(conn_id)
            
            # Close dead connections
            for conn_id in dead_conn_ids:
                ws = self._connections.get(conn_id)
                if ws:
                    try:
                        await ws.close(code=1000, reason="Heartbeat timeout")
                    except Exception:
                        pass  # Connection might already be closed
                # Remove from registry
                await self._remove_agent(conn_id)
    
    async def _remove_agent(self, conn_id: str) -> None:
        """Internal method to remove an agent (must be called with lock held)."""
        agent = self._agents_by_conn.pop(conn_id, None)
        if agent:
            self._conn_by_agent.pop(agent.agent_id, None)
            
            # Unregister schemas from ViewRegistry if available
            if self._view_registry:
                self._view_registry.unregister_agent_schemas(agent.agent_id)
        
        self._connections.pop(conn_id, None)
    
    async def register(
        self,
        agent_id: UUID,
        tenant_id: str,
        websocket: WebSocket,
        capabilities: list[str] | None = None,
        tags: list[str] | None = None,
        schemas: dict[str, dict] | None = None,
        metadata: dict[str, Any] | None = None
    ) -> tuple[str, AgentRecord]:
        """
        Register a new agent with the bus.
        
        Args:
            agent_id: Unique agent identifier
            tenant_id: Tenant the agent belongs to
            websocket: WebSocket connection
            capabilities: List of capabilities the agent provides
            tags: Semantic tags for matching
            schemas: Response schemas (view_id -> schema definition)
            metadata: Additional agent metadata
        
        Returns:
            Tuple of (conn_id, AgentRecord)
        """
        # Generate connection ID
        conn_id = f"{tenant_id}:{agent_id}:{id(websocket)}"
        
        async with self._lock:
            # Check if agent is already registered
            existing_conn = self._conn_by_agent.get(agent_id)
            if existing_conn:
                # Agent reconnecting - close old connection
                old_ws = self._connections.get(existing_conn)
                if old_ws:
                    try:
                        await old_ws.close(code=1000, reason="Agent reconnected")
                    except Exception:
                        pass
                await self._remove_agent(existing_conn)
            
            # Create agent record
            agent = AgentRecord(
                agent_id=agent_id,
                tenant_id=tenant_id,
                conn_id=conn_id,
                capabilities=capabilities or [],
                tags=tags or [],
                schemas=schemas or {},
                metadata=metadata or {}
            )
            
            # Store in indexes
            self._agents_by_conn[conn_id] = agent
            self._conn_by_agent[agent_id] = conn_id
            self._connections[conn_id] = websocket
            
            # Register schemas in ViewRegistry if available
            if self._view_registry and schemas:
                self._view_registry.register_agent_schemas(agent_id, schemas)
            
            logger.info(
                f"Agent registered: {agent_id} (tenant: {tenant_id}, "
                f"capabilities: {capabilities}, tags: {tags}, schemas: {list(schemas.keys()) if schemas else []})"
            )
            
            return conn_id, agent
    
    async def unregister(self, conn_id: str) -> AgentRecord | None:
        """
        Unregister an agent by connection ID.
        
        Args:
            conn_id: Connection identifier
        
        Returns:
            The removed AgentRecord, or None if not found
        """
        async with self._lock:
            agent = self._agents_by_conn.get(conn_id)
            if agent:
                await self._remove_agent(conn_id)
                logger.info(f"Agent unregistered: {agent.agent_id}")
            return agent
    
    async def heartbeat(self, conn_id: str) -> bool:
        """
        Update agent's last_seen timestamp.
        
        Args:
            conn_id: Connection identifier
        
        Returns:
            True if agent found and updated, False otherwise
        """
        async with self._lock:
            agent = self._agents_by_conn.get(conn_id)
            if agent:
                agent.touch()
                if agent.status == AgentStatus.OFFLINE:
                    agent.status = AgentStatus.ONLINE
                return True
            return False
    
    async def list_agents(
        self,
        tenant_id: str | None = None,
        only_online: bool = True
    ) -> list[AgentRecord]:
        """
        List all agents, optionally filtered by tenant.
        
        Args:
            tenant_id: Filter by tenant (None = all tenants)
            only_online: Only include online agents
        
        Returns:
            List of matching AgentRecords
        """
        async with self._lock:
            agents = list(self._agents_by_conn.values())
            
            if tenant_id:
                agents = [a for a in agents if a.tenant_id == tenant_id]
            
            if only_online:
                agents = [a for a in agents if a.status == AgentStatus.ONLINE]
            
            return agents
    
    async def find_by_tenant(
        self,
        tenant_id: str,
        only_online: bool = True
    ) -> list[AgentRecord]:
        """
        Find all agents belonging to a specific tenant.
        
        Args:
            tenant_id: Tenant ID to filter by
            only_online: Only include online agents (default: True)
        
        Returns:
            List of matching AgentRecords
        """
        return await self.list_agents(tenant_id=tenant_id, only_online=only_online)
    
    async def find_candidates(
        self,
        tenant_id: str,
        capability: str | None = None,
        tags: list[str] | None = None,
        exclude_agent_id: UUID | None = None,
        max_candidates: int = 3
    ) -> list[AgentRecord]:
        """
        Find candidate agents that can fulfill an intent.
        
        Matching rules (deterministic, simple for now):
        1. Same tenant_id
        2. Has matching capability (if specified)
        3. Has at least one matching tag (if specified)
        4. Exclude the sender
        5. Only online agents
        6. Return top N by load (prefer less loaded agents)
        
        Args:
            tenant_id: Tenant to search within
            capability: Required capability (optional)
            tags: Required tags - at least one must match (optional)
            exclude_agent_id: Agent to exclude (usually the sender)
            max_candidates: Maximum number of candidates to return
        
        Returns:
            List of matching AgentRecords, sorted by load (ascending)
        """
        async with self._lock:
            candidates = []
            
            for agent in self._agents_by_conn.values():
                # Must be same tenant
                if agent.tenant_id != tenant_id:
                    continue
                
                # Must be online
                if agent.status != AgentStatus.ONLINE:
                    continue
                
                # Exclude sender
                if exclude_agent_id and agent.agent_id == exclude_agent_id:
                    continue
                
                # Check capability match
                if capability and capability not in agent.capabilities:
                    continue
                
                # Check tag match (at least one)
                if tags and not any(t in agent.tags for t in tags):
                    continue
                
                candidates.append(agent)
            
            # Sort by load (prefer less loaded agents)
            candidates.sort(key=lambda a: a.load)
            
            return candidates[:max_candidates]
    
    async def get_agent_by_conn(self, conn_id: str) -> AgentRecord | None:
        """Get agent by connection ID."""
        async with self._lock:
            return self._agents_by_conn.get(conn_id)
    
    async def get_agent_by_id(self, agent_id: UUID) -> AgentRecord | None:
        """Get agent by agent ID."""
        async with self._lock:
            conn_id = self._conn_by_agent.get(agent_id)
            if conn_id:
                return self._agents_by_conn.get(conn_id)
            return None
    
    async def get_websocket(self, conn_id: str) -> WebSocket | None:
        """Get WebSocket connection for an agent."""
        async with self._lock:
            return self._connections.get(conn_id)
    
    async def get_websocket_by_agent_id(self, agent_id: UUID) -> WebSocket | None:
        """Get WebSocket connection by agent ID."""
        async with self._lock:
            conn_id = self._conn_by_agent.get(agent_id)
            if conn_id:
                return self._connections.get(conn_id)
            return None
    
    @property
    def agent_count(self) -> int:
        """Number of registered agents."""
        return len(self._agents_by_conn)
    
    @property
    def online_count(self) -> int:
        """Number of online agents."""
        return sum(
            1 for a in self._agents_by_conn.values() 
            if a.status == AgentStatus.ONLINE
        )
