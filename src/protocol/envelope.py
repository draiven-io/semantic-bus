"""
LIP Message Envelope Model

Every message in the Semantic Bus uses a fixed envelope structure.
The envelope provides:
- Unique message identification and tracing
- Message type classification for routing
- Temporal context (timestamps, TTL)
- Provenance (sender, tenant)
- Session binding for ephemeral interfaces
- Payload with schema reference for validation

Why a fixed envelope?
- Enables consistent validation at the bus level
- Allows routing decisions before payload inspection
- Supports tracing and debugging across the system
- Enforces the LIP negotiation lifecycle
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """
    LIP message types following the negotiation lifecycle.
    
    The lifecycle flows:
    1. intent.broadcast -> Agent declares intent to the bus
    2. intent.offer -> Responder offers to fulfill the intent
    3. intent.agree -> Requester accepts an offer, creating an ephemeral interface
    4. exec.request -> Actual execution request within the session
    5. exec.result -> Successful execution response
    6. exec.partial_failure -> Partial failure with renegotiation opportunity
    7. session.close -> Explicit dissolution of the ephemeral interface
    
    Additional system messages:
    - agent.register -> Agent registration with the bus
    - agent.heartbeat -> Presence signal
    - error.protocol -> Protocol-level errors (not payload errors)
    """
    # Agent lifecycle
    AGENT_REGISTER = "agent.register"
    AGENT_REGISTERED = "agent.registered"  # Bus confirms registration
    AGENT_HEARTBEAT = "agent.heartbeat"
    AGENT_HEARTBEAT_ACK = "agent.heartbeat_ack"  # Bus acknowledges heartbeat
    AGENT_DEREGISTER = "agent.deregister"
    AGENT_LIST = "agent.list"  # Request list of agents
    AGENT_LIST_RESULT = "agent.list.result"  # Response with agent list
    
    # Intent negotiation phase
    INTENT_BROADCAST = "intent.broadcast"
    INTENT_DELIVER = "intent.deliver"  # Bus forwards intent to candidates
    INTENT_OFFER = "intent.offer"  # Provider offers to fulfill
    INTENT_OFFER_RECEIVED = "intent.offer.received"  # Bus acks offer to provider
    INTENT_OFFERS = "intent.offers"  # Bus sends collected offers to requester
    INTENT_AGREE = "intent.agree"  # Requester accepts an offer
    INTENT_REJECT = "intent.reject"  # Explicit rejection of an offer
    
    # Session lifecycle
    SESSION_CREATED = "session.created"  # Ephemeral interface established
    SESSION_CLOSE = "session.close"  # Explicit dissolution
    
    # Execution phase (within ephemeral interface)
    EXEC_REQUEST = "exec.request"  # Requester sends action request
    EXEC_ACCEPTED = "exec.accepted"  # Bus acks request to requester
    EXEC_PROGRESS = "exec.progress"  # Optional streaming/status updates
    EXEC_RESULT = "exec.result"  # Successful result from provider
    EXEC_PARTIAL_FAILURE = "exec.partial_failure"  # Partial failure, can retry
    EXEC_ERROR = "exec.error"  # Execution error (IBAC, schema mismatch, etc.)
    
    # Streaming (fine-grained provider->requester flow)
    EXEC_STREAM_START = "exec.stream.start"  # Provider begins streaming response
    EXEC_STREAM_CHUNK = "exec.stream.chunk"  # Individual stream chunk
    EXEC_STREAM_END = "exec.stream.end"  # Provider finishes streaming
    
    # Trace retrieval
    TRACE_GET = "trace.get"  # Request trace by session/exec_id
    TRACE_RESULT = "trace.result"  # Trace events response
    
    # System messages
    ERROR = "error"  # General error message
    
    # === Multi-Agent Orchestration ===
    # Planning
    ORCHESTRATION_PLAN = "orchestration.plan"  # Plan created for multi-agent task
    ORCHESTRATION_PLAN_UPDATE = "orchestration.plan.update"  # Plan modified
    
    # Multi-agent negotiation
    MULTI_OFFER_REQUEST = "orchestration.multi_offer.request"  # Request offers from multiple agents
    MULTI_OFFER_STATUS = "orchestration.multi_offer.status"  # Status update on offers
    MULTI_NEGOTIATION_REQUEST = "orchestration.multi_negotiation.request"  # Start multi-agent negotiation
    MULTI_NEGOTIATION_STATUS = "orchestration.multi_negotiation.status"  # Negotiation status
    
    # Multi-agent execution
    MULTI_EXEC_START = "orchestration.multi_exec.start"  # Start multi-agent execution
    MULTI_EXEC_TASK_RESULT = "orchestration.multi_exec.task_result"  # Individual task result
    MULTI_EXEC_STATUS = "orchestration.multi_exec.status"  # Overall execution status
    MULTI_EXEC_COMPLETE = "orchestration.multi_exec.complete"  # All tasks done
    
    # Aggregation
    AGGREGATION_RESULT = "orchestration.aggregation.result"  # Final aggregated response
    
    # === Event Streaming ===
    # Subscription management
    EVENT_SUBSCRIBE = "event.subscribe"  # Subscribe to session/execution events
    EVENT_UNSUBSCRIBE = "event.unsubscribe"  # Unsubscribe from events
    EVENT_SUBSCRIBED = "event.subscribed"  # Confirm subscription
    
    # Event delivery
    EVENT = "event"  # Generic event delivery
    EVENT_EXECUTION = "event.execution"  # Execution lifecycle event
    EVENT_FLOW = "event.flow"  # Multi-agent flow event
    EVENT_AGENT = "event.agent"  # Agent-emitted event
    EVENT_REASONING = "event.reasoning"  # AI reasoning/thinking event
    EVENT_SYSTEM = "event.system"  # System-level event
    
    # Agent event emission (provider -> bus)
    EMIT_EVENT = "emit.event"  # Agent sends a custom event


class MessageEnvelope(BaseModel):
    """
    The fixed message envelope for all LIP communication.
    
    This envelope wraps every message flowing through the Semantic Bus.
    It provides the metadata necessary for routing, validation, and lifecycle management
    while keeping the actual payload flexible through schema references.
    """
    
    # === Identity ===
    message_id: UUID = Field(
        default_factory=uuid4,
        description="Unique identifier for this message instance"
    )
    correlation_id: UUID | None = Field(
        default=None,
        description="Links related messages in a conversation (e.g., request-response pairs)"
    )
    causation_id: UUID | None = Field(
        default=None,
        description="ID of the message that directly caused this one (for tracing chains)"
    )
    
    # === Type & Routing ===
    message_type: MessageType = Field(
        ...,
        description="The LIP message type, determines how the bus routes and processes this message"
    )
    
    # === Provenance ===
    sender_id: UUID = Field(
        ...,
        description="Agent ID of the message sender"
    )
    tenant_id: str = Field(
        ...,
        description="Tenant/organization context for multi-tenancy isolation"
    )
    
    # === Session Binding ===
    ephemeral_interface_id: UUID | None = Field(
        default=None,
        description="ID of the ephemeral interface (session) this message belongs to. "
                    "Required for exec.* and session.* messages. "
                    "None for intent.broadcast and agent.* messages."
    )
    
    # === Temporal ===
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the message was created"
    )
    ttl_seconds: int | None = Field(
        default=None,
        ge=1,
        description="Time-to-live in seconds. After this, the message should be dropped. "
                    "None means no expiration."
    )
    
    # === Payload ===
    schema_id: str | None = Field(
        default=None,
        description="JSON Schema ID for validating the payload. "
                    "References a schema registered with the bus."
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="The actual message content. Structure depends on message_type and schema_id."
    )
    
    # === IBAC Context ===
    # These fields support Intent-Based Access Control decisions
    purpose: str | None = Field(
        default=None,
        description="Declared purpose of this message (for IBAC policy evaluation)"
    )
    data_scope: list[str] = Field(
        default_factory=list,
        description="Data categories this message accesses or produces"
    )
    risk_level: str | None = Field(
        default=None,
        description="Self-declared risk level: 'low', 'medium', 'high'"
    )

    class Config:
        # Allow JSON serialization of datetime and UUID
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v)
        }


# === Convenience constructors for common message types ===

def create_intent_broadcast(
    sender_id: UUID,
    tenant_id: str,
    intent_payload: dict[str, Any],
    purpose: str | None = None,
    data_scope: list[str] | None = None,
    ttl_seconds: int | None = 60
) -> MessageEnvelope:
    """
    Create an intent broadcast message.
    
    Intent broadcasts announce what an agent wants to accomplish
    without specifying who should fulfill it.
    """
    return MessageEnvelope(
        message_type=MessageType.INTENT_BROADCAST,
        sender_id=sender_id,
        tenant_id=tenant_id,
        payload=intent_payload,
        purpose=purpose,
        data_scope=data_scope or [],
        ttl_seconds=ttl_seconds
    )


def create_intent_offer(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    offer_payload: dict[str, Any],
    ttl_seconds: int | None = 30
) -> MessageEnvelope:
    """
    Create an intent offer message.
    
    Offers are responses to broadcasts, indicating capability to fulfill.
    The correlation_id links back to the original broadcast.
    """
    return MessageEnvelope(
        message_type=MessageType.INTENT_OFFER,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload=offer_payload,
        ttl_seconds=ttl_seconds
    )


def create_intent_agree(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    causation_id: UUID,
    ephemeral_interface_id: UUID,
    agreement_payload: dict[str, Any]
) -> MessageEnvelope:
    """
    Create an intent agreement message.
    
    Agreement accepts an offer and establishes an ephemeral interface.
    The ephemeral_interface_id is assigned by the Session Manager.
    """
    return MessageEnvelope(
        message_type=MessageType.INTENT_AGREE,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        ephemeral_interface_id=ephemeral_interface_id,
        payload=agreement_payload
    )


def create_exec_request(
    sender_id: UUID,
    tenant_id: str,
    ephemeral_interface_id: UUID,
    correlation_id: UUID,
    schema_id: str,
    request_payload: dict[str, Any]
) -> MessageEnvelope:
    """
    Create an execution request message.
    
    Execution requests happen within an established ephemeral interface.
    They must reference a valid schema_id for payload validation.
    """
    return MessageEnvelope(
        message_type=MessageType.EXEC_REQUEST,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=ephemeral_interface_id,
        correlation_id=correlation_id,
        schema_id=schema_id,
        payload=request_payload
    )


def create_exec_result(
    sender_id: UUID,
    tenant_id: str,
    ephemeral_interface_id: UUID,
    correlation_id: UUID,
    causation_id: UUID,
    schema_id: str,
    result_payload: dict[str, Any]
) -> MessageEnvelope:
    """
    Create an execution result message.
    
    Results are successful responses to execution requests.
    """
    return MessageEnvelope(
        message_type=MessageType.EXEC_RESULT,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=ephemeral_interface_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        schema_id=schema_id,
        payload=result_payload
    )


def create_session_close(
    sender_id: UUID,
    tenant_id: str,
    ephemeral_interface_id: UUID,
    reason: str | None = None
) -> MessageEnvelope:
    """
    Create a session close message.
    
    Explicitly dissolves an ephemeral interface.
    Both parties should clean up session state upon receiving this.
    """
    return MessageEnvelope(
        message_type=MessageType.SESSION_CLOSE,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=ephemeral_interface_id,
        payload={"reason": reason} if reason else {}
    )


# === Bus response message constructors ===

def create_agent_registered(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    agent_id: UUID,
    conn_id: str
) -> MessageEnvelope:
    """
    Bus response confirming agent registration.
    """
    return MessageEnvelope(
        message_type=MessageType.AGENT_REGISTERED,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload={
            "agent_id": str(agent_id),
            "conn_id": conn_id,
            "status": "registered"
        }
    )


def create_heartbeat_ack(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID
) -> MessageEnvelope:
    """
    Bus response acknowledging heartbeat.
    """
    return MessageEnvelope(
        message_type=MessageType.AGENT_HEARTBEAT_ACK,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload={"status": "alive"}
    )


def create_agent_list_result(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    agents: list[dict[str, Any]]
) -> MessageEnvelope:
    """
    Bus response with list of agents.
    """
    return MessageEnvelope(
        message_type=MessageType.AGENT_LIST_RESULT,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload={"agents": agents}
    )


def create_intent_deliver(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    original_sender_id: UUID,
    intent_payload: dict[str, Any],
    purpose: str | None = None,
    data_scope: list[str] | None = None
) -> MessageEnvelope:
    """
    Bus forwards an intent to candidate agents.
    
    The correlation_id links back to the original broadcast.
    The original_sender_id identifies who broadcasted the intent.
    """
    return MessageEnvelope(
        message_type=MessageType.INTENT_DELIVER,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload={
            **intent_payload,
            "original_sender_id": str(original_sender_id)
        },
        purpose=purpose,
        data_scope=data_scope or []
    )


def create_error(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID | None,
    error_code: str,
    error_message: str,
    details: dict[str, Any] | None = None
) -> MessageEnvelope:
    """
    Create an error message.
    
    Used for protocol-level errors, validation failures, etc.
    """
    return MessageEnvelope(
        message_type=MessageType.ERROR,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload={
            "error_code": error_code,
            "error_message": error_message,
            "details": details or {}
        }
    )


# === Negotiation message constructors ===

def create_intent_deliver_with_session(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    ephemeral_interface_id: UUID,
    original_sender_id: UUID,
    intent_payload: dict[str, Any],
    purpose: str | None = None,
    data_scope: list[str] | None = None
) -> MessageEnvelope:
    """
    Bus forwards an intent to candidate agents with session ID.
    
    The ephemeral_interface_id allows providers to reference the session.
    """
    return MessageEnvelope(
        message_type=MessageType.INTENT_DELIVER,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        ephemeral_interface_id=ephemeral_interface_id,
        payload={
            **intent_payload,
            "original_sender_id": str(original_sender_id)
        },
        purpose=purpose,
        data_scope=data_scope or []
    )


def create_intent_offer_received(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    offer_id: str,
    ephemeral_interface_id: UUID
) -> MessageEnvelope:
    """
    Bus acknowledges receipt of an offer to the provider.
    """
    return MessageEnvelope(
        message_type=MessageType.INTENT_OFFER_RECEIVED,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        ephemeral_interface_id=ephemeral_interface_id,
        payload={
            "offer_id": offer_id,
            "status": "received"
        }
    )


def create_intent_offers(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    ephemeral_interface_id: UUID,
    offers: list[dict[str, Any]]
) -> MessageEnvelope:
    """
    Bus sends collected offers to the requester.
    
    Sent incrementally as offers arrive, or as a batch.
    """
    return MessageEnvelope(
        message_type=MessageType.INTENT_OFFERS,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        ephemeral_interface_id=ephemeral_interface_id,
        payload={"offers": offers}
    )


def create_session_created(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    ephemeral_interface_id: UUID,
    requester_id: UUID,
    provider_id: UUID,
    chosen_offer_id: str,
    chosen_schema_id: str | None = None,
    chosen_view_id: str | None = None,
    expires_at: datetime | None = None
) -> MessageEnvelope:
    """
    Bus notifies both parties that a session is established.
    
    Sent to both requester and provider after intent.agree.
    """
    return MessageEnvelope(
        message_type=MessageType.SESSION_CREATED,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        ephemeral_interface_id=ephemeral_interface_id,
        payload={
            "requester_id": str(requester_id),
            "provider_id": str(provider_id),
            "chosen_offer_id": chosen_offer_id,
            "chosen_schema_id": chosen_schema_id,
            "chosen_view_id": chosen_view_id,
            "expires_at": expires_at.isoformat() if expires_at else None
        }
    )


def create_exec_request_msg(
    sender_id: UUID,
    tenant_id: str,
    ephemeral_interface_id: UUID,
    correlation_id: UUID,
    schema_id: str,
    request_payload: dict[str, Any]
) -> MessageEnvelope:
    """
    Create an execution request within a session.
    """
    return MessageEnvelope(
        message_type=MessageType.EXEC_REQUEST,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=ephemeral_interface_id,
        correlation_id=correlation_id,
        schema_id=schema_id,
        payload=request_payload
    )


def create_exec_result_msg(
    sender_id: UUID,
    tenant_id: str,
    ephemeral_interface_id: UUID,
    correlation_id: UUID,
    causation_id: UUID,
    schema_id: str,
    result_payload: dict[str, Any]
) -> MessageEnvelope:
    """
    Create an execution result.
    """
    return MessageEnvelope(
        message_type=MessageType.EXEC_RESULT,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=ephemeral_interface_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        schema_id=schema_id,
        payload=result_payload
    )


def create_exec_partial_failure(
    sender_id: UUID,
    tenant_id: str,
    ephemeral_interface_id: UUID,
    correlation_id: UUID,
    causation_id: UUID,
    error_code: str,
    error_message: str,
    partial_result: dict[str, Any] | None = None,
    can_retry: bool = True
) -> MessageEnvelope:
    """
    Create a partial failure response.
    
    Indicates the request partially failed but renegotiation is possible.
    """
    return MessageEnvelope(
        message_type=MessageType.EXEC_PARTIAL_FAILURE,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=ephemeral_interface_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload={
            "error_code": error_code,
            "error_message": error_message,
            "partial_result": partial_result,
            "can_retry": can_retry
        }
    )


# === Execution phase message constructors ===

def create_exec_accepted(
    sender_id: UUID,
    tenant_id: str,
    ephemeral_interface_id: UUID,
    correlation_id: UUID,
    exec_id: str
) -> MessageEnvelope:
    """
    Bus acknowledges exec.request to requester.
    
    Indicates the request was validated and forwarded to provider.
    """
    return MessageEnvelope(
        message_type=MessageType.EXEC_ACCEPTED,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=ephemeral_interface_id,
        correlation_id=correlation_id,
        payload={
            "exec_id": exec_id,
            "status": "accepted"
        }
    )


def create_exec_progress(
    sender_id: UUID,
    tenant_id: str,
    ephemeral_interface_id: UUID,
    correlation_id: UUID,
    exec_id: str,
    progress: float,
    status_message: str | None = None
) -> MessageEnvelope:
    """
    Progress update during long-running execution.
    
    Allows streaming status updates from provider to requester.
    """
    return MessageEnvelope(
        message_type=MessageType.EXEC_PROGRESS,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=ephemeral_interface_id,
        correlation_id=correlation_id,
        payload={
            "exec_id": exec_id,
            "progress": progress,
            "status_message": status_message
        }
    )


def create_exec_error(
    sender_id: UUID,
    tenant_id: str,
    ephemeral_interface_id: UUID,
    correlation_id: UUID,
    exec_id: str | None,
    error_code: str,
    error_message: str,
    details: dict[str, Any] | None = None
) -> MessageEnvelope:
    """
    Execution error message.
    
    Used for:
    - IBAC_DENY: Policy denied the request
    - SCHEMA_MISMATCH: Result doesn't match agreed view/schema
    - SESSION_INVALID: Session not active
    - PROVIDER_ERROR: Provider-side error
    - BACKPRESSURE: Stream terminated due to queue full
    """
    return MessageEnvelope(
        message_type=MessageType.EXEC_ERROR,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=ephemeral_interface_id,
        correlation_id=correlation_id,
        payload={
            "exec_id": exec_id,
            "error_code": error_code,
            "error_message": error_message,
            "details": details or {}
        }
    )


# =============================================================================
# Streaming Constructors
# =============================================================================

def create_exec_stream_start(
    sender_id: UUID,
    tenant_id: str,
    ephemeral_interface_id: UUID,
    exec_id: str,
    seq: int = 0,
    content_type: str | None = None,
    expected_chunks: int | None = None,
    correlation_id: UUID | None = None
) -> MessageEnvelope:
    """
    Signal start of a streaming response.
    
    Args:
        sender_id: Provider agent ID
        tenant_id: Tenant context
        ephemeral_interface_id: Session ID
        exec_id: Execution ID being streamed
        seq: Sequence number (0 for start)
        content_type: MIME type of stream content (e.g., "text/plain")
        expected_chunks: Optional hint for total chunks
        correlation_id: Links to original exec.request
    """
    return MessageEnvelope(
        message_type=MessageType.EXEC_STREAM_START,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=ephemeral_interface_id,
        correlation_id=correlation_id,
        payload={
            "exec_id": exec_id,
            "seq": seq,
            "content_type": content_type,
            "expected_chunks": expected_chunks
        }
    )


def create_exec_stream_chunk(
    sender_id: UUID,
    tenant_id: str,
    ephemeral_interface_id: UUID,
    exec_id: str,
    seq: int,
    data: str | bytes,
    is_final: bool = False,
    correlation_id: UUID | None = None
) -> MessageEnvelope:
    """
    Individual chunk in a streaming response.
    
    Args:
        sender_id: Provider agent ID
        tenant_id: Tenant context
        ephemeral_interface_id: Session ID
        exec_id: Execution ID being streamed
        seq: Monotonically increasing sequence number
        data: Chunk payload (string or bytes as base64)
        is_final: True if this is the last chunk (can skip stream.end)
        correlation_id: Links to original exec.request
    """
    return MessageEnvelope(
        message_type=MessageType.EXEC_STREAM_CHUNK,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=ephemeral_interface_id,
        correlation_id=correlation_id,
        payload={
            "exec_id": exec_id,
            "seq": seq,
            "data": data,
            "is_final": is_final
        }
    )


def create_exec_stream_end(
    sender_id: UUID,
    tenant_id: str,
    ephemeral_interface_id: UUID,
    exec_id: str,
    seq: int,
    total_chunks: int | None = None,
    total_bytes: int | None = None,
    correlation_id: UUID | None = None
) -> MessageEnvelope:
    """
    Signal end of a streaming response.
    
    Args:
        sender_id: Provider agent ID
        tenant_id: Tenant context
        ephemeral_interface_id: Session ID
        exec_id: Execution ID being streamed
        seq: Final sequence number
        total_chunks: Total chunks sent
        total_bytes: Total bytes sent
        correlation_id: Links to original exec.request
    """
    return MessageEnvelope(
        message_type=MessageType.EXEC_STREAM_END,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=ephemeral_interface_id,
        correlation_id=correlation_id,
        payload={
            "exec_id": exec_id,
            "seq": seq,
            "total_chunks": total_chunks,
            "total_bytes": total_bytes
        }
    )


# =============================================================================
# Trace Constructors
# =============================================================================

def create_trace_get(
    sender_id: UUID,
    tenant_id: str,
    ephemeral_interface_id: UUID | None = None,
    exec_id: str | None = None,
    tail: int = 100
) -> MessageEnvelope:
    """
    Request trace events for a session or execution.
    
    Args:
        sender_id: Requesting agent ID
        tenant_id: Tenant context
        ephemeral_interface_id: Filter by session
        exec_id: Filter by execution ID (within session)
        tail: Max number of events to return (most recent)
    """
    return MessageEnvelope(
        message_type=MessageType.TRACE_GET,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=ephemeral_interface_id,
        payload={
            "exec_id": exec_id,
            "tail": tail
        }
    )


def create_trace_result(
    sender_id: UUID,
    tenant_id: str,
    ephemeral_interface_id: UUID | None,
    events: list[dict[str, Any]],
    truncated: bool = False,
    correlation_id: UUID | None = None
) -> MessageEnvelope:
    """
    Trace events response.
    
    Args:
        sender_id: Bus agent ID
        tenant_id: Tenant context
        ephemeral_interface_id: Session these events belong to
        events: List of trace event dicts
        truncated: True if more events exist than returned
        correlation_id: Links to trace.get request
    """
    return MessageEnvelope(
        message_type=MessageType.TRACE_RESULT,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=ephemeral_interface_id,
        correlation_id=correlation_id,
        payload={
            "events": events,
            "count": len(events),
            "truncated": truncated
        }
    )


# =============================================================================
# Multi-Agent Orchestration Constructors
# =============================================================================

def create_orchestration_plan(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    plan_id: str,
    intent_id: UUID,
    selected_agents: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    execution_mode: str,
    aggregation: str,
    reasoning: str | None = None,
) -> MessageEnvelope:
    """
    Create an orchestration plan message.
    
    Sent to requester after planning phase to inform about multi-agent execution.
    """
    return MessageEnvelope(
        message_type=MessageType.ORCHESTRATION_PLAN,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload={
            "plan_id": plan_id,
            "intent_id": str(intent_id),
            "selected_agents": selected_agents,
            "tasks": tasks,
            "execution_mode": execution_mode,
            "aggregation": aggregation,
            "reasoning": reasoning,
            "agent_count": len(selected_agents),
            "task_count": len(tasks),
        }
    )


def create_multi_offer_status(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    plan_id: str,
    offers_received: int,
    offers_pending: int,
    offers: list[dict[str, Any]],
) -> MessageEnvelope:
    """
    Status update on multi-agent offers.
    """
    return MessageEnvelope(
        message_type=MessageType.MULTI_OFFER_STATUS,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload={
            "plan_id": plan_id,
            "offers_received": offers_received,
            "offers_pending": offers_pending,
            "offers": offers,
        }
    )


def create_multi_negotiation_status(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    plan_id: str,
    agents_accepted: int,
    agents_rejected: int,
    agents_pending: int,
    agent_statuses: list[dict[str, Any]],
) -> MessageEnvelope:
    """
    Status update on multi-agent negotiation.
    """
    return MessageEnvelope(
        message_type=MessageType.MULTI_NEGOTIATION_STATUS,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload={
            "plan_id": plan_id,
            "agents_accepted": agents_accepted,
            "agents_rejected": agents_rejected,
            "agents_pending": agents_pending,
            "agent_statuses": agent_statuses,
        }
    )


def create_multi_exec_start(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    plan_id: str,
    session_id: UUID,
    execution_mode: str,
    task_count: int,
    agent_count: int,
) -> MessageEnvelope:
    """
    Notify requester that multi-agent execution is starting.
    """
    return MessageEnvelope(
        message_type=MessageType.MULTI_EXEC_START,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload={
            "plan_id": plan_id,
            "session_id": str(session_id),
            "execution_mode": execution_mode,
            "task_count": task_count,
            "agent_count": agent_count,
        }
    )


def create_multi_exec_task_result(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    plan_id: str,
    session_id: UUID,
    task_id: str,
    agent_id: UUID,
    success: bool,
    output: dict[str, Any] | None = None,
    error: str | None = None,
    execution_time_ms: float = 0.0,
) -> MessageEnvelope:
    """
    Individual task result in multi-agent execution.
    """
    return MessageEnvelope(
        message_type=MessageType.MULTI_EXEC_TASK_RESULT,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload={
            "plan_id": plan_id,
            "session_id": str(session_id),
            "task_id": task_id,
            "agent_id": str(agent_id),
            "success": success,
            "output": output,
            "error": error,
            "execution_time_ms": execution_time_ms,
        }
    )


def create_multi_exec_complete(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    plan_id: str,
    session_id: UUID,
    tasks_completed: int,
    tasks_failed: int,
    total_execution_time_ms: float,
) -> MessageEnvelope:
    """
    Notify requester that all tasks are complete.
    """
    return MessageEnvelope(
        message_type=MessageType.MULTI_EXEC_COMPLETE,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload={
            "plan_id": plan_id,
            "session_id": str(session_id),
            "tasks_completed": tasks_completed,
            "tasks_failed": tasks_failed,
            "total_execution_time_ms": total_execution_time_ms,
        }
    )


def create_aggregation_result(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    plan_id: str,
    session_id: UUID,
    intent_id: UUID,
    success: bool,
    partial_success: bool,
    final_output: dict[str, Any],
    aggregation_strategy: str,
    per_agent_results: list[dict[str, Any]] | None = None,
    per_task_results: list[dict[str, Any]] | None = None,
    errors: list[dict[str, Any]] | None = None,
    total_execution_time_ms: float = 0.0,
) -> MessageEnvelope:
    """
    Final aggregated result from multi-agent execution.
    
    This is the final message sent to the requester containing
    combined output from all agents.
    """
    payload: dict[str, Any] = {
        "plan_id": plan_id,
        "session_id": str(session_id),
        "intent_id": str(intent_id),
        "success": success,
        "partial_success": partial_success,
        "final_output": final_output,
        "aggregation_strategy": aggregation_strategy,
        "total_execution_time_ms": total_execution_time_ms,
    }
    
    if per_agent_results is not None:
        payload["per_agent_results"] = per_agent_results
    if per_task_results is not None:
        payload["per_task_results"] = per_task_results
    if errors:
        payload["errors"] = errors
    
    return MessageEnvelope(
        message_type=MessageType.AGGREGATION_RESULT,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload=payload,
    )


# =============================================================================
# Event Streaming Envelope Helpers
# =============================================================================

def create_event_subscribe(
    sender_id: UUID,
    tenant_id: str,
    session_id: UUID,
    exec_id: str | None = None,
    event_types: list[str] | None = None,
    include_history: bool = True,
) -> MessageEnvelope:
    """
    Request to subscribe to events for a session or execution.
    
    Args:
        sender_id: Requester agent ID
        tenant_id: Tenant context
        session_id: Session to subscribe to
        exec_id: Optional specific execution ID
        event_types: Optional list of event type prefixes to filter
        include_history: Whether to replay past events
    """
    payload: dict[str, Any] = {
        "session_id": str(session_id),
        "include_history": include_history,
    }
    if exec_id:
        payload["exec_id"] = exec_id
    if event_types:
        payload["event_types"] = event_types
    
    return MessageEnvelope(
        message_type=MessageType.EVENT_SUBSCRIBE,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        payload=payload,
    )


def create_event_unsubscribe(
    sender_id: UUID,
    tenant_id: str,
    session_id: UUID,
    subscription_id: str | None = None,
) -> MessageEnvelope:
    """
    Request to unsubscribe from events.
    
    Args:
        sender_id: Requester agent ID
        tenant_id: Tenant context
        session_id: Session to unsubscribe from
        subscription_id: Optional specific subscription to cancel
    """
    payload: dict[str, Any] = {
        "session_id": str(session_id),
    }
    if subscription_id:
        payload["subscription_id"] = subscription_id
    
    return MessageEnvelope(
        message_type=MessageType.EVENT_UNSUBSCRIBE,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        payload=payload,
    )


def create_event_subscribed(
    sender_id: UUID,
    tenant_id: str,
    correlation_id: UUID,
    session_id: UUID,
    subscription_id: str,
    event_types: list[str] | None = None,
) -> MessageEnvelope:
    """
    Confirm event subscription.
    
    Sent by the bus to acknowledge an event subscription.
    """
    payload: dict[str, Any] = {
        "session_id": str(session_id),
        "subscription_id": subscription_id,
    }
    if event_types:
        payload["event_types"] = event_types
    
    return MessageEnvelope(
        message_type=MessageType.EVENT_SUBSCRIBED,
        sender_id=sender_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        ephemeral_interface_id=session_id,
        payload=payload,
    )


def create_event_envelope(
    sender_id: UUID,
    tenant_id: str,
    session_id: UUID,
    event_data: dict[str, Any],
    correlation_id: UUID | None = None,
    exec_id: str | None = None,
) -> MessageEnvelope:
    """
    Create an event delivery envelope.
    
    Used by the bus to deliver events to subscribers.
    
    Args:
        sender_id: Bus ID
        tenant_id: Tenant context
        session_id: Session the event belongs to
        event_data: The event payload (from BaseEvent.to_payload())
        correlation_id: Optional correlation ID
        exec_id: Optional execution ID
    """
    # Determine specific message type based on event type
    event_type_value = event_data.get("event_type", "")
    
    if event_type_value.startswith("execution."):
        message_type = MessageType.EVENT_EXECUTION
    elif event_type_value.startswith("flow."):
        message_type = MessageType.EVENT_FLOW
    elif event_type_value.startswith("agent."):
        message_type = MessageType.EVENT_AGENT
    elif event_type_value.startswith("reasoning."):
        message_type = MessageType.EVENT_REASONING
    elif event_type_value.startswith("system."):
        message_type = MessageType.EVENT_SYSTEM
    else:
        message_type = MessageType.EVENT
    
    return MessageEnvelope(
        message_type=message_type,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        correlation_id=correlation_id,
        payload=event_data,
    )


def create_emit_event(
    sender_id: UUID,
    tenant_id: str,
    session_id: UUID,
    event_type: str,
    message: str,
    exec_id: str | None = None,
    severity: str = "info",
    progress_percent: float | None = None,
    data: dict[str, Any] | None = None,
    correlation_id: UUID | None = None,
) -> MessageEnvelope:
    """
    Create an event emission from an agent.
    
    Used by agents to emit custom events to the bus.
    
    Args:
        sender_id: Agent ID emitting the event
        tenant_id: Tenant context
        session_id: Session the event belongs to
        event_type: Type of event (e.g., "agent.status", "reasoning.thought")
        message: Human-readable message
        exec_id: Optional execution ID
        severity: Event severity (debug, info, warning, error, critical)
        progress_percent: Optional progress (0-100)
        data: Optional additional data
        correlation_id: Optional correlation ID
    """
    payload: dict[str, Any] = {
        "event_type": event_type,
        "message": message,
        "severity": severity,
    }
    if exec_id:
        payload["exec_id"] = exec_id
    if progress_percent is not None:
        payload["progress_percent"] = progress_percent
    if data:
        payload["data"] = data
    
    return MessageEnvelope(
        message_type=MessageType.EMIT_EVENT,
        sender_id=sender_id,
        tenant_id=tenant_id,
        ephemeral_interface_id=session_id,
        correlation_id=correlation_id,
        payload=payload,
    )
