"""
Session Model

Represents an ephemeral interface session in the Semantic Bus.
Sessions track the negotiation state and enforce the agreed contract.

Session Lifecycle:
1. NEGOTIATING - Intent broadcast, collecting offers
2. ACTIVE - Agreement reached, exec messages allowed
3. CLOSED - Explicitly closed or TTL expired
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    """Session lifecycle states."""
    NEGOTIATING = "negotiating"  # Collecting offers
    ACTIVE = "active"            # Agreement reached, session usable
    CLOSED = "closed"            # Explicitly closed
    EXPIRED = "expired"          # TTL expired


class ExecutionStatus(str, Enum):
    """Execution lifecycle states for idempotency tracking."""
    PENDING = "pending"          # Request received, awaiting routing
    ACCEPTED = "accepted"        # Provider accepted, processing
    IN_PROGRESS = "in_progress"  # Streaming/partial results
    COMPLETED = "completed"      # Final result received
    FAILED = "failed"            # Execution failed
    REJECTED = "rejected"        # Policy rejected the request


class ExecutionRecord(BaseModel):
    """
    Tracks a single execution request within a session.
    
    Used for idempotency (dedup by exec_id) and status tracking.
    """
    exec_id: UUID = Field(
        ...,
        description="Unique execution identifier"
    )
    correlation_id: UUID = Field(
        ...,
        description="Links exec.request → exec.result"
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Client-provided key for dedup"
    )
    status: ExecutionStatus = Field(
        default=ExecutionStatus.PENDING,
        description="Current execution status"
    )
    
    # Request context
    requester_conn_id: str = Field(
        ...,
        description="Connection that sent exec.request"
    )
    action: str | None = Field(
        default=None,
        description="Action from exec.request body"
    )
    
    # Result tracking
    result_view_id: str | None = Field(
        default=None,
        description="View ID used for the result"
    )
    result_valid: bool | None = Field(
        default=None,
        description="Whether result passed view validation"
    )
    
    # Timestamps
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When exec.request was received"
    )
    accepted_at: datetime | None = Field(
        default=None,
        description="When provider sent exec.accepted"
    )
    completed_at: datetime | None = Field(
        default=None,
        description="When exec.result was received"
    )
    
    # Policy decision
    policy_allowed: bool = Field(
        default=True,
        description="Whether IBAC policy allowed execution"
    )
    policy_reason: str | None = Field(
        default=None,
        description="Reason from policy engine"
    )
    
    def to_dict(self) -> dict[str, Any]:
        """Serialize for message payloads."""
        return {
            "exec_id": str(self.exec_id),
            "correlation_id": str(self.correlation_id),
            "idempotency_key": self.idempotency_key,
            "status": self.status.value,
            "action": self.action,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None
        }


class Offer(BaseModel):
    """
    An offer from a provider to fulfill an intent.
    
    Providers declare what they can do and what schema/views they support.
    """
    offer_id: str = Field(
        default_factory=lambda: f"off_{uuid4().hex[:12]}",
        description="Unique offer identifier"
    )
    provider_agent_id: UUID = Field(
        ...,
        description="Agent ID of the provider"
    )
    provider_conn_id: str = Field(
        ...,
        description="Connection ID of the provider"
    )
    
    # What the provider offers
    schema_ids: list[str] = Field(
        default_factory=list,
        description="JSON Schema IDs the provider can return"
    )
    view_ids: list[str] = Field(
        default_factory=list,
        description="View IDs the provider supports (e.g., view://answer.business.v1)"
    )
    
    # What the provider requires
    requires: list[str] = Field(
        default_factory=list,
        description="Required input fields/schemas"
    )
    accepts: list[str] = Field(
        default_factory=list,
        description="Accepted input formats"
    )
    
    # Scoring (for future ranking)
    score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Relevance/quality score (0.0-1.0)"
    )
    
    # Metadata
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional offer metadata"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the offer was received"
    )
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for envelope payload."""
        return {
            "offer_id": self.offer_id,
            "provider_agent_id": str(self.provider_agent_id),
            "schema_ids": self.schema_ids,
            "view_ids": self.view_ids,
            "requires": self.requires,
            "accepts": self.accepts,
            "score": self.score,
            "metadata": self.metadata
        }


class Session(BaseModel):
    """
    An ephemeral interface session.
    
    Created when an intent is broadcast, becomes active when agreement is reached,
    and closes either explicitly or when TTL expires.
    """
    
    # === Identity ===
    ephemeral_interface_id: UUID = Field(
        default_factory=uuid4,
        description="Unique session identifier"
    )
    tenant_id: str = Field(
        ...,
        description="Tenant this session belongs to"
    )
    
    # === Participants ===
    requester_agent_id: UUID = Field(
        ...,
        description="Agent ID of the requester"
    )
    requester_conn_id: str = Field(
        ...,
        description="Connection ID of the requester"
    )
    provider_agent_id: UUID | None = Field(
        default=None,
        description="Agent ID of the chosen provider (set after agreement)"
    )
    provider_conn_id: str | None = Field(
        default=None,
        description="Connection ID of the chosen provider (set after agreement)"
    )
    
    # === Intent ===
    intent_message_id: UUID = Field(
        ...,
        description="Message ID of the original intent.broadcast"
    )
    intent: dict[str, Any] = Field(
        default_factory=dict,
        description="The original intent payload"
    )
    
    # === Offers ===
    offers: list[Offer] = Field(
        default_factory=list,
        description="Collected offers from providers"
    )
    
    # === Agreement ===
    chosen_offer_id: str | None = Field(
        default=None,
        description="ID of the accepted offer"
    )
    chosen_schema_id: str | None = Field(
        default=None,
        description="Agreed schema ID for responses"
    )
    chosen_view_id: str | None = Field(
        default=None,
        description="Agreed view ID for responses"
    )
    
    # === Lifecycle ===
    status: SessionStatus = Field(
        default=SessionStatus.NEGOTIATING,
        description="Current session status"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the session was created"
    )
    activated_at: datetime | None = Field(
        default=None,
        description="When agreement was reached"
    )
    closed_at: datetime | None = Field(
        default=None,
        description="When session was closed"
    )
    
    # === TTL ===
    negotiation_ttl_seconds: int = Field(
        default=30,
        description="Max time to collect offers"
    )
    session_ttl_seconds: int = Field(
        default=300,
        description="Max session lifetime after activation"
    )
    
    # === Executions ===
    executions: list[ExecutionRecord] = Field(
        default_factory=list,
        description="Tracked execution requests for idempotency"
    )
    _exec_index: dict[str, int] = {}  # exec_id → index in executions list
    _idempotency_index: dict[str, int] = {}  # idempotency_key → index
    
    # === Agent Flow ===
    flow_agents: list[str] = Field(
        default_factory=list,
        description="Agent IDs in the flow (in execution order)"
    )
    flow_entry_point: str | None = Field(
        default=None,
        description="Entry point agent ID for the flow"
    )
    flow_current_step: int = Field(
        default=0,
        description="Current step in the flow execution"
    )
    flow_results: dict[str, Any] = Field(
        default_factory=dict,
        description="Results from each agent in the flow (agent_id -> result)"
    )
    aggregated_offer_id: str | None = Field(
        default=None,
        description="ID of the aggregated offer"
    )
    
    @property
    def has_multi_agent_flow(self) -> bool:
        """Check if this session has multiple agents in the flow."""
        return len(self.flow_agents) > 1
    
    @property
    def expires_at(self) -> datetime:
        """Calculate when this session expires."""
        if self.status == SessionStatus.NEGOTIATING:
            from datetime import timedelta
            return self.created_at + timedelta(seconds=self.negotiation_ttl_seconds)
        elif self.status == SessionStatus.ACTIVE and self.activated_at:
            from datetime import timedelta
            return self.activated_at + timedelta(seconds=self.session_ttl_seconds)
        else:
            return self.created_at  # Already expired/closed
    
    def is_expired(self) -> bool:
        """Check if the session has expired."""
        return datetime.utcnow() > self.expires_at
    
    def add_offer(self, offer: Offer) -> None:
        """Add an offer to the session."""
        self.offers.append(offer)
    
    def get_offer(self, offer_id: str) -> Offer | None:
        """Get an offer by ID."""
        for offer in self.offers:
            if offer.offer_id == offer_id:
                return offer
        return None
    
    def activate(
        self,
        offer_id: str,
        chosen_schema_id: str | None = None,
        chosen_view_id: str | None = None
    ) -> bool:
        """
        Activate the session by accepting an offer.
        
        Returns True if successful, False if offer not found.
        """
        offer = self.get_offer(offer_id)
        if offer is None:
            return False
        
        self.chosen_offer_id = offer_id
        self.provider_agent_id = offer.provider_agent_id
        self.provider_conn_id = offer.provider_conn_id
        self.chosen_schema_id = chosen_schema_id
        self.chosen_view_id = chosen_view_id
        self.status = SessionStatus.ACTIVE
        self.activated_at = datetime.utcnow()
        return True
    
    def close(self, reason: str | None = None) -> None:
        """Close the session."""
        self.status = SessionStatus.CLOSED
        self.closed_at = datetime.utcnow()
    
    def to_summary_dict(self) -> dict[str, Any]:
        """Return a summary for logging/debugging."""
        return {
            "ephemeral_interface_id": str(self.ephemeral_interface_id),
            "tenant_id": self.tenant_id,
            "status": self.status.value,
            "requester": str(self.requester_agent_id),
            "provider": str(self.provider_agent_id) if self.provider_agent_id else None,
            "offer_count": len(self.offers),
            "chosen_offer_id": self.chosen_offer_id,
            "execution_count": len(self.executions)
        }
    
    # === Execution Tracking ===
    
    def add_execution(self, record: ExecutionRecord) -> None:
        """
        Add an execution record to the session.
        
        Also indexes by exec_id and idempotency_key for fast lookup.
        """
        idx = len(self.executions)
        self.executions.append(record)
        self._exec_index[str(record.exec_id)] = idx
        if record.idempotency_key:
            self._idempotency_index[record.idempotency_key] = idx
    
    def get_execution(self, exec_id: str | UUID) -> ExecutionRecord | None:
        """Get execution record by exec_id."""
        key = str(exec_id)
        idx = self._exec_index.get(key)
        if idx is not None and idx < len(self.executions):
            return self.executions[idx]
        return None
    
    def get_execution_by_idempotency_key(self, key: str) -> ExecutionRecord | None:
        """Get execution record by idempotency key (for dedup)."""
        idx = self._idempotency_index.get(key)
        if idx is not None and idx < len(self.executions):
            return self.executions[idx]
        return None
    
    def update_execution_status(
        self,
        exec_id: str | UUID,
        status: ExecutionStatus,
        **kwargs: Any
    ) -> bool:
        """
        Update execution status and optional fields.
        
        Supports: accepted_at, completed_at, result_view_id, result_valid
        Returns True if updated, False if not found.
        """
        record = self.get_execution(exec_id)
        if record is None:
            return False
        
        record.status = status
        
        if status == ExecutionStatus.ACCEPTED:
            record.accepted_at = kwargs.get("accepted_at", datetime.utcnow())
        elif status in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED):
            record.completed_at = kwargs.get("completed_at", datetime.utcnow())
            if "result_view_id" in kwargs:
                record.result_view_id = kwargs["result_view_id"]
            if "result_valid" in kwargs:
                record.result_valid = kwargs["result_valid"]
        
        return True
    
    def has_pending_executions(self) -> bool:
        """Check if any executions are still pending/in-progress."""
        for rec in self.executions:
            if rec.status in (ExecutionStatus.PENDING, ExecutionStatus.ACCEPTED, ExecutionStatus.IN_PROGRESS):
                return True
        return False
