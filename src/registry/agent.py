"""
Agent Record Model

Represents a registered agent in the Semantic Bus.
Contains identity, capabilities, presence information, and connection state.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class AgentStatus(str, Enum):
    """Agent connection status."""
    ONLINE = "online"
    OFFLINE = "offline"
    IDLE = "idle"  # Connected but not actively processing


class AgentRecord(BaseModel):
    """
    Represents a registered agent in the bus.
    
    This is the bus's view of an agent - what it knows about
    who is connected and what they can do.
    """
    
    # === Identity ===
    agent_id: UUID = Field(
        ...,
        description="Unique identifier for the agent"
    )
    tenant_id: str = Field(
        ...,
        description="Tenant/organization the agent belongs to"
    )
    
    # === Connection ===
    conn_id: str = Field(
        ...,
        description="WebSocket connection identifier"
    )
    
    # === Capabilities ===
    capabilities: list[str] = Field(
        default_factory=list,
        description="List of capabilities this agent can fulfill (e.g., 'translate', 'summarize')"
    )
    description: str | None = Field(
        default=None,
        description="Optional description of what the agent does"
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Semantic tags for more flexible matching (e.g., 'nlp', 'fast', 'premium')"
    )
    
    # === Schemas ===
    schemas: dict[str, dict] = Field(
        default_factory=dict,
        description="Response schemas for this agent's capabilities (capability -> JSON schema)"
    )
    
    # === Presence ===
    status: AgentStatus = Field(
        default=AgentStatus.ONLINE,
        description="Current agent status"
    )
    last_seen: datetime = Field(
        default_factory=datetime.utcnow,
        description="Last heartbeat or activity timestamp"
    )
    registered_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the agent registered"
    )
    
    # === Load (for future load balancing) ===
    load: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Current load factor (0.0 = idle, 1.0 = fully loaded)"
    )
    
    # === Metadata ===
    metadata: dict = Field(
        default_factory=dict,
        description="Additional agent metadata"
    )
    
    def is_alive(self, timeout_seconds: float) -> bool:
        """Check if agent is still alive based on heartbeat timeout."""
        elapsed = (datetime.utcnow() - self.last_seen).total_seconds()
        return elapsed <= timeout_seconds
    
    def touch(self) -> None:
        """Update last_seen to current time."""
        self.last_seen = datetime.utcnow()
    
    def to_public_dict(self) -> dict:
        """Return a public view of the agent (for agent.list responses)."""
        return {
            "agent_id": str(self.agent_id),
            "tenant_id": self.tenant_id,
            "capabilities": self.capabilities,
            "tags": self.tags,
            "status": self.status.value,
            "load": self.load
        }

    class Config:
        # Allow mutation for touch() and status updates
        frozen = False
