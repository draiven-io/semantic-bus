"""
Event Streaming Models

Defines the event types and data structures for real-time execution
progress streaming. These events allow requesters to track:
- Execution lifecycle (started, progress, completed, failed)
- Multi-agent flow progress (which agent is active, transitions)
- Agent reasoning/thinking updates (for transparent AI)
- Status updates and custom agent events

Design Principles:
- Events are immutable records
- Each event type has a well-defined payload structure
- Events are correlated by session_id and optional exec_id
- Events can be filtered by type, severity, and source
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """
    Categories of streamable events.
    
    Grouped by concern:
    - execution.* - Execution lifecycle events
    - flow.* - Multi-agent orchestration events
    - agent.* - Individual agent events
    - reasoning.* - AI reasoning/thinking transparency
    - system.* - Bus-level events
    """
    # Execution Lifecycle
    EXECUTION_STARTED = "execution.started"
    EXECUTION_PROGRESS = "execution.progress"
    EXECUTION_STEP_STARTED = "execution.step.started"
    EXECUTION_STEP_COMPLETED = "execution.step.completed"
    EXECUTION_COMPLETED = "execution.completed"
    EXECUTION_FAILED = "execution.failed"
    EXECUTION_RETRYING = "execution.retrying"
    EXECUTION_TIMEOUT = "execution.timeout"
    
    # Multi-Agent Flow Events
    FLOW_STARTED = "flow.started"
    FLOW_AGENT_QUEUED = "flow.agent.queued"
    FLOW_AGENT_STARTED = "flow.agent.started"
    FLOW_AGENT_COMPLETED = "flow.agent.completed"
    FLOW_AGENT_FAILED = "flow.agent.failed"
    FLOW_TRANSITION = "flow.transition"
    FLOW_COMPLETED = "flow.completed"
    FLOW_FAILED = "flow.failed"
    
    # Agent Events (from executing agents)
    AGENT_STATUS = "agent.status"
    AGENT_PROGRESS = "agent.progress"
    AGENT_METRIC = "agent.metric"
    AGENT_CUSTOM = "agent.custom"
    
    # Reasoning Events (AI transparency)
    REASONING_STARTED = "reasoning.started"
    REASONING_STEP = "reasoning.step"
    REASONING_OBSERVATION = "reasoning.observation"
    REASONING_THOUGHT = "reasoning.thought"
    REASONING_ACTION = "reasoning.action"
    REASONING_COMPLETED = "reasoning.completed"
    
    # System Events
    SYSTEM_INFO = "system.info"
    SYSTEM_WARNING = "system.warning"
    SYSTEM_ERROR = "system.error"


class EventSeverity(str, Enum):
    """Severity levels for events."""
    DEBUG = "debug"      # Verbose debugging info
    INFO = "info"        # Normal progress updates
    WARNING = "warning"  # Non-fatal issues
    ERROR = "error"      # Errors that may affect outcome
    CRITICAL = "critical"  # Critical failures


class BaseEvent(BaseModel):
    """
    Base class for all streamable events.
    
    Provides common identification, timing, and correlation fields.
    """
    # Identity
    event_id: UUID = Field(
        default_factory=uuid4,
        description="Unique event identifier"
    )
    event_type: EventType = Field(
        ...,
        description="Type/category of the event"
    )
    
    # Timing
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the event occurred"
    )
    
    # Correlation
    session_id: UUID = Field(
        ...,
        description="Ephemeral interface session this event belongs to"
    )
    exec_id: str | None = Field(
        default=None,
        description="Execution ID if event is part of a specific execution"
    )
    correlation_id: UUID | None = Field(
        default=None,
        description="For linking related events (e.g., request/response pairs)"
    )
    
    # Source
    tenant_id: str = Field(
        ...,
        description="Tenant context"
    )
    source_agent_id: UUID | None = Field(
        default=None,
        description="Agent that emitted the event (if any)"
    )
    source_name: str | None = Field(
        default=None,
        description="Human-readable name of the source (e.g., agent name)"
    )
    
    # Classification
    severity: EventSeverity = Field(
        default=EventSeverity.INFO,
        description="Event severity level"
    )
    
    # Payload
    message: str = Field(
        ...,
        description="Human-readable event description"
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Event-specific structured data"
    )
    
    # Progress tracking
    progress_percent: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Optional progress percentage (0-100)"
    )
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v)
        }
    
    def to_payload(self) -> dict[str, Any]:
        """Convert to envelope payload format."""
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "session_id": str(self.session_id),
            "exec_id": self.exec_id,
            "correlation_id": str(self.correlation_id) if self.correlation_id else None,
            "tenant_id": self.tenant_id,
            "source_agent_id": str(self.source_agent_id) if self.source_agent_id else None,
            "source_name": self.source_name,
            "severity": self.severity.value,
            "message": self.message,
            "data": self.data,
            "progress_percent": self.progress_percent,
        }


class ExecutionEvent(BaseEvent):
    """
    Event related to execution lifecycle.
    
    Tracks the progress of a single execution request.
    """
    # Execution context
    action: str | None = Field(
        default=None,
        description="The action being executed"
    )
    step_index: int | None = Field(
        default=None,
        description="Current step index (for multi-step executions)"
    )
    total_steps: int | None = Field(
        default=None,
        description="Total number of steps (if known)"
    )
    
    # Timing
    elapsed_ms: int | None = Field(
        default=None,
        description="Milliseconds elapsed since execution started"
    )
    estimated_remaining_ms: int | None = Field(
        default=None,
        description="Estimated milliseconds remaining"
    )


class FlowEvent(BaseEvent):
    """
    Event related to multi-agent flow orchestration.
    
    Tracks which agents are involved and their execution order.
    """
    # Flow context
    flow_id: str | None = Field(
        default=None,
        description="Identifier for the orchestration flow"
    )
    flow_name: str | None = Field(
        default=None,
        description="Human-readable flow name"
    )
    
    # Agent progression
    current_agent_id: UUID | None = Field(
        default=None,
        description="Currently active agent in the flow"
    )
    current_agent_name: str | None = Field(
        default=None,
        description="Name of the currently active agent"
    )
    previous_agent_id: UUID | None = Field(
        default=None,
        description="Previous agent in the flow (for transitions)"
    )
    next_agent_id: UUID | None = Field(
        default=None,
        description="Next agent in the flow (if known)"
    )
    
    # Flow progress
    agents_completed: int = Field(
        default=0,
        description="Number of agents that have completed"
    )
    agents_total: int = Field(
        default=0,
        description="Total number of agents in the flow"
    )
    agent_order: list[str] = Field(
        default_factory=list,
        description="Ordered list of agent names in the flow"
    )


class AgentEvent(BaseEvent):
    """
    Custom event emitted by an executing agent.
    
    Allows agents to send arbitrary progress/status updates.
    """
    # Agent context
    agent_id: UUID = Field(
        ...,
        description="Agent emitting the event"
    )
    agent_name: str | None = Field(
        default=None,
        description="Human-readable agent name"
    )
    capability: str | None = Field(
        default=None,
        description="Capability being executed"
    )
    
    # Custom event type
    custom_type: str | None = Field(
        default=None,
        description="Agent-defined event subtype"
    )


class ReasoningEvent(BaseEvent):
    """
    Event related to AI reasoning/thinking process.
    
    Enables transparency into how AI agents are processing requests.
    Useful for debugging, user confidence, and explainability.
    """
    # Reasoning step
    step_type: str | None = Field(
        default=None,
        description="Type of reasoning step (thought, observation, action, etc.)"
    )
    step_number: int | None = Field(
        default=None,
        description="Sequential step number in reasoning chain"
    )
    
    # Content
    thought: str | None = Field(
        default=None,
        description="The agent's internal thought/reasoning"
    )
    observation: str | None = Field(
        default=None,
        description="External information observed"
    )
    action: str | None = Field(
        default=None,
        description="Action being taken"
    )
    tool_name: str | None = Field(
        default=None,
        description="Name of tool being used (if any)"
    )
    tool_input: dict[str, Any] | None = Field(
        default=None,
        description="Input to the tool"
    )


# Factory functions for common events

def create_execution_started(
    session_id: UUID,
    tenant_id: str,
    exec_id: str,
    action: str,
    source_agent_id: UUID | None = None,
    source_name: str | None = None,
    total_steps: int | None = None,
) -> ExecutionEvent:
    """Create an execution.started event."""
    return ExecutionEvent(
        event_type=EventType.EXECUTION_STARTED,
        session_id=session_id,
        tenant_id=tenant_id,
        exec_id=exec_id,
        source_agent_id=source_agent_id,
        source_name=source_name,
        message=f"Execution started: {action}",
        action=action,
        total_steps=total_steps,
        progress_percent=0.0,
    )


def create_execution_progress(
    session_id: UUID,
    tenant_id: str,
    exec_id: str,
    message: str,
    progress_percent: float | None = None,
    step_index: int | None = None,
    total_steps: int | None = None,
    source_agent_id: UUID | None = None,
    source_name: str | None = None,
    data: dict[str, Any] | None = None,
) -> ExecutionEvent:
    """Create an execution.progress event."""
    return ExecutionEvent(
        event_type=EventType.EXECUTION_PROGRESS,
        session_id=session_id,
        tenant_id=tenant_id,
        exec_id=exec_id,
        source_agent_id=source_agent_id,
        source_name=source_name,
        message=message,
        progress_percent=progress_percent,
        step_index=step_index,
        total_steps=total_steps,
        data=data or {},
    )


def create_execution_completed(
    session_id: UUID,
    tenant_id: str,
    exec_id: str,
    message: str = "Execution completed successfully",
    elapsed_ms: int | None = None,
    source_agent_id: UUID | None = None,
    source_name: str | None = None,
) -> ExecutionEvent:
    """Create an execution.completed event."""
    return ExecutionEvent(
        event_type=EventType.EXECUTION_COMPLETED,
        session_id=session_id,
        tenant_id=tenant_id,
        exec_id=exec_id,
        source_agent_id=source_agent_id,
        source_name=source_name,
        message=message,
        progress_percent=100.0,
        elapsed_ms=elapsed_ms,
    )


def create_execution_failed(
    session_id: UUID,
    tenant_id: str,
    exec_id: str,
    error_message: str,
    error_code: str | None = None,
    source_agent_id: UUID | None = None,
    source_name: str | None = None,
) -> ExecutionEvent:
    """Create an execution.failed event."""
    return ExecutionEvent(
        event_type=EventType.EXECUTION_FAILED,
        session_id=session_id,
        tenant_id=tenant_id,
        exec_id=exec_id,
        source_agent_id=source_agent_id,
        source_name=source_name,
        severity=EventSeverity.ERROR,
        message=error_message,
        data={"error_code": error_code} if error_code else {},
    )


def create_flow_started(
    session_id: UUID,
    tenant_id: str,
    flow_id: str,
    flow_name: str,
    agent_order: list[str],
    exec_id: str | None = None,
) -> FlowEvent:
    """Create a flow.started event."""
    return FlowEvent(
        event_type=EventType.FLOW_STARTED,
        session_id=session_id,
        tenant_id=tenant_id,
        exec_id=exec_id,
        message=f"Multi-agent flow started: {flow_name}",
        flow_id=flow_id,
        flow_name=flow_name,
        agents_total=len(agent_order),
        agent_order=agent_order,
        progress_percent=0.0,
    )


def create_flow_agent_started(
    session_id: UUID,
    tenant_id: str,
    flow_id: str,
    agent_id: UUID,
    agent_name: str,
    agent_index: int,
    agents_total: int,
    exec_id: str | None = None,
) -> FlowEvent:
    """Create a flow.agent.started event."""
    progress = (agent_index / agents_total) * 100 if agents_total > 0 else 0
    return FlowEvent(
        event_type=EventType.FLOW_AGENT_STARTED,
        session_id=session_id,
        tenant_id=tenant_id,
        exec_id=exec_id,
        message=f"Agent started: {agent_name} ({agent_index + 1}/{agents_total})",
        flow_id=flow_id,
        current_agent_id=agent_id,
        current_agent_name=agent_name,
        agents_completed=agent_index,
        agents_total=agents_total,
        progress_percent=progress,
    )


def create_flow_agent_completed(
    session_id: UUID,
    tenant_id: str,
    flow_id: str,
    agent_id: UUID,
    agent_name: str,
    agent_index: int,
    agents_total: int,
    next_agent_name: str | None = None,
    exec_id: str | None = None,
) -> FlowEvent:
    """Create a flow.agent.completed event."""
    progress = ((agent_index + 1) / agents_total) * 100 if agents_total > 0 else 100
    message = f"Agent completed: {agent_name}"
    if next_agent_name:
        message += f" → Next: {next_agent_name}"
    return FlowEvent(
        event_type=EventType.FLOW_AGENT_COMPLETED,
        session_id=session_id,
        tenant_id=tenant_id,
        exec_id=exec_id,
        message=message,
        flow_id=flow_id,
        current_agent_id=agent_id,
        current_agent_name=agent_name,
        agents_completed=agent_index + 1,
        agents_total=agents_total,
        progress_percent=progress,
    )


def create_flow_completed(
    session_id: UUID,
    tenant_id: str,
    flow_id: str,
    flow_name: str,
    agents_total: int,
    elapsed_ms: int | None = None,
    exec_id: str | None = None,
) -> FlowEvent:
    """Create a flow.completed event."""
    return FlowEvent(
        event_type=EventType.FLOW_COMPLETED,
        session_id=session_id,
        tenant_id=tenant_id,
        exec_id=exec_id,
        message=f"Flow completed: {flow_name}",
        flow_id=flow_id,
        flow_name=flow_name,
        agents_completed=agents_total,
        agents_total=agents_total,
        progress_percent=100.0,
        data={"elapsed_ms": elapsed_ms} if elapsed_ms else {},
    )


def create_reasoning_thought(
    session_id: UUID,
    tenant_id: str,
    thought: str,
    step_number: int | None = None,
    source_agent_id: UUID | None = None,
    source_name: str | None = None,
    exec_id: str | None = None,
) -> ReasoningEvent:
    """Create a reasoning.thought event."""
    return ReasoningEvent(
        event_type=EventType.REASONING_THOUGHT,
        session_id=session_id,
        tenant_id=tenant_id,
        exec_id=exec_id,
        source_agent_id=source_agent_id,
        source_name=source_name,
        message=f"Thinking: {thought[:100]}..." if len(thought) > 100 else f"Thinking: {thought}",
        thought=thought,
        step_type="thought",
        step_number=step_number,
    )


def create_reasoning_action(
    session_id: UUID,
    tenant_id: str,
    action: str,
    tool_name: str | None = None,
    tool_input: dict[str, Any] | None = None,
    step_number: int | None = None,
    source_agent_id: UUID | None = None,
    source_name: str | None = None,
    exec_id: str | None = None,
) -> ReasoningEvent:
    """Create a reasoning.action event."""
    message = f"Action: {action}"
    if tool_name:
        message = f"Using tool: {tool_name}"
    return ReasoningEvent(
        event_type=EventType.REASONING_ACTION,
        session_id=session_id,
        tenant_id=tenant_id,
        exec_id=exec_id,
        source_agent_id=source_agent_id,
        source_name=source_name,
        message=message,
        action=action,
        tool_name=tool_name,
        tool_input=tool_input,
        step_type="action",
        step_number=step_number,
    )


def create_agent_status(
    session_id: UUID,
    tenant_id: str,
    agent_id: UUID,
    agent_name: str,
    status: str,
    message: str,
    progress_percent: float | None = None,
    exec_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> AgentEvent:
    """Create an agent.status event."""
    return AgentEvent(
        event_type=EventType.AGENT_STATUS,
        session_id=session_id,
        tenant_id=tenant_id,
        exec_id=exec_id,
        source_agent_id=agent_id,
        source_name=agent_name,
        agent_id=agent_id,
        agent_name=agent_name,
        message=message,
        progress_percent=progress_percent,
        data={"status": status, **(data or {})},
    )
