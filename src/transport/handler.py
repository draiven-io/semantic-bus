"""
WebSocket Handler

The control plane for the Semantic Bus.
All agent communication flows through WebSocket using the fixed envelope format.

Supported message types:
- agent.register -> agent.registered
- agent.heartbeat -> agent.heartbeat_ack
- agent.list -> agent.list.result
- intent.broadcast -> creates session, routes to candidates as intent.deliver
- intent.offer -> adds offer to session, acks provider, forwards to requester
- intent.agree -> activates session, sends session.created to both parties
- session.close -> closes session
- exec.request -> validates session, routes to provider
- exec.result -> validates session, routes to requester

Why WebSocket?
- Bidirectional communication
- Real-time message delivery
- Connection state tracking
- Natural fit for agent presence
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from src.protocol.envelope import (
    MessageEnvelope,
    MessageType,
    create_agent_registered,
    create_heartbeat_ack,
    create_agent_list_result,
    create_intent_deliver_with_session,
    create_intent_offer_received,
    create_intent_offers,
    create_session_created,
    create_exec_accepted,
    create_exec_error,
    create_error,
    create_trace_result,
    create_event_subscribed,
    create_event_envelope,
)
from src.registry import AgentRegistry
from src.routing import IntentRouter
from src.session import SessionManager, ExecutionRecord, ExecutionStatus, Session, SessionStatus
from src.schema import ViewRegistry
from src.policy import PolicyEngine, PolicyContext
from src.transport.queue import ConnectionQueueManager, QueueFullError
from src.trace import (
    SemanticTraceStore,
    trace_agent_register,
    trace_agent_unregister,
    trace_intent_broadcast,
    trace_intent_offer,
    trace_intent_agree,
    trace_session_created,
    trace_session_closed,
    trace_policy_decision,
    trace_exec_request,
    trace_exec_result,
    trace_exec_error,
    trace_stream_start,
    trace_stream_chunk,
    trace_stream_end,
    trace_stream_backpressure,
)
from src.events import EventManager, EventFilter, EventType, EventSeverity, EventSubscription

if TYPE_CHECKING:
    from src.storage import ExecStore
    from src.orchestration.flow_planner import AgentFlow
    from typing import Any as CompiledStateGraph  # Placeholder for langgraph type

logger = logging.getLogger(__name__)

# Bus identity - used as sender_id for bus-originated messages
BUS_ID = UUID("00000000-0000-0000-0000-000000000000")


class WebSocketHandler:
    """
    Handles WebSocket connections and message routing.
    
    Each connected agent gets a dedicated handler instance
    that processes messages and maintains connection state.
    """
    
    def __init__(
        self,
        registry: AgentRegistry,
        router: IntentRouter,
        session_manager: SessionManager,
        view_registry: ViewRegistry | None = None,
        policy_engine: PolicyEngine | None = None,
        trace_store: SemanticTraceStore | None = None,
        queue_manager: ConnectionQueueManager | None = None,
        exec_store: "ExecStore | None" = None,
        event_manager: EventManager | None = None,
    ):
        """
        Initialize the handler.
        
        Args:
            registry: Agent registry for registration/presence
            router: Intent router for message routing with LangGraph flow discovery
            session_manager: Session manager for negotiation lifecycle
            view_registry: View registry for structure validation
            policy_engine: Policy engine for IBAC enforcement
            trace_store: Semantic trace store for event recording
            queue_manager: Per-connection queue manager for backpressure
            exec_store: Optional persistent storage for execution records
            event_manager: Event manager for real-time event streaming
        """
        self._registry = registry
        self._router = router
        self._sessions = session_manager
        self._views = view_registry or ViewRegistry()
        self._policy = policy_engine or PolicyEngine()
        self._trace = trace_store or SemanticTraceStore()
        self._queues = queue_manager or ConnectionQueueManager(max_queue_size=200)
        self._exec_store = exec_store
        self._events = event_manager or EventManager()
        
        # Pending orchestration results: session_id -> (event, result_data)
        self._pending_orchestration_results: dict[str, tuple[asyncio.Event, dict]] = {}
        
        # Active event delivery tasks per connection: conn_id -> task
        self._event_delivery_tasks: dict[str, asyncio.Task] = {}
    
    async def handle_connection(self, websocket: WebSocket) -> None:
        """
        Handle a WebSocket connection lifecycle.
        
        The first message must be agent.register.
        After registration, the agent can send any supported message type.
        
        Args:
            websocket: The WebSocket connection
        """
        await websocket.accept()
        
        conn_id: str | None = None
        agent_id: UUID | None = None
        tenant_id: str | None = None
        
        try:
            # First message must be registration
            registration_envelope = await self._receive_envelope(websocket)
            if registration_envelope is None:
                return
            
            if registration_envelope.message_type != MessageType.AGENT_REGISTER:
                await self._send_error(
                    websocket,
                    None,
                    None,
                    "REGISTRATION_REQUIRED",
                    "First message must be agent.register"
                )
                await websocket.close(code=1002, reason="Registration required")
                return
            
            # Process registration
            result = await self._handle_register(websocket, registration_envelope)
            if result is None:
                await websocket.close(code=1002, reason="Registration failed")
                return
            
            conn_id, agent_id, tenant_id = result
            
            # Main message loop
            while True:
                envelope = await self._receive_envelope(websocket)
                if envelope is None:
                    break
                
                await self._handle_message(websocket, conn_id, envelope)
        
        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected: {conn_id or 'unregistered'}")
        
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        
        finally:
            # Cleanup
            if conn_id:
                await self._cleanup_connection_events(conn_id)
                await self._registry.unregister(conn_id)
    
    async def _receive_envelope(self, websocket: WebSocket) -> MessageEnvelope | None:
        """
        Receive and parse a message envelope from WebSocket.
        
        Returns:
            Parsed envelope, or None if connection closed or parse error
        """
        try:
            data = await websocket.receive_text()
            envelope = MessageEnvelope.model_validate_json(data)
            return envelope
        
        except WebSocketDisconnect:
            return None
        
        except ValidationError as e:
            logger.warning(f"Invalid message format: {e}")
            await self._send_error(
                websocket,
                None,
                None,
                "INVALID_ENVELOPE",
                f"Message validation failed: {str(e)}"
            )
            return None
        
        except Exception as e:
            logger.error(f"Error receiving message: {e}")
            return None
    
    async def _send_envelope(self, websocket: WebSocket, envelope: MessageEnvelope) -> bool:
        """Send an envelope to a WebSocket connection."""
        try:
            await websocket.send_text(envelope.model_dump_json())
            return True
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False
    
    async def _send_error(
        self,
        websocket: WebSocket,
        correlation_id: UUID | None,
        tenant_id: str | None,
        error_code: str,
        error_message: str
    ) -> None:
        """Send an error message."""
        envelope = create_error(
            sender_id=BUS_ID,
            tenant_id=tenant_id or "system",
            correlation_id=correlation_id,
            error_code=error_code,
            error_message=error_message
        )
        await self._send_envelope(websocket, envelope)
    
    async def _handle_register(
        self,
        websocket: WebSocket,
        envelope: MessageEnvelope
    ) -> tuple[str, UUID, str] | None:
        """
        Handle agent.register message.
        
        Payload expected:
        - capabilities: list[str] (optional)
        - tags: list[str] (optional)
        - schemas: dict[str, dict] (optional) - Response schemas keyed by view_id
        - metadata: dict (optional)
        
        Returns:
            Tuple of (conn_id, agent_id, tenant_id) on success, None on failure
        """
        try:
            agent_id = envelope.sender_id
            tenant_id = envelope.tenant_id
            
            capabilities = envelope.payload.get("capabilities", [])
            tags = envelope.payload.get("tags", [])
            schemas = envelope.payload.get("schemas", {})
            metadata = envelope.payload.get("metadata", {})
            
            conn_id, agent = await self._registry.register(
                agent_id=agent_id,
                tenant_id=tenant_id,
                websocket=websocket,
                capabilities=capabilities,
                tags=tags,
                schemas=schemas,
                metadata=metadata
            )
            
            # Record trace event
            self._trace.append(trace_agent_register(
                tenant_id=tenant_id,
                agent_id=agent_id,
                conn_id=conn_id,
                capabilities=capabilities
            ))
            
            # Send confirmation
            response = create_agent_registered(
                sender_id=BUS_ID,
                tenant_id=tenant_id,
                correlation_id=envelope.message_id,
                agent_id=agent_id,
                conn_id=conn_id
            )
            await self._send_envelope(websocket, response)
            
            logger.info(f"Agent registered: {agent_id} as {conn_id}")
            return conn_id, agent_id, tenant_id
        
        except Exception as e:
            logger.error(f"Registration failed: {e}")
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "REGISTRATION_FAILED",
                str(e)
            )
            return None
    
    async def _handle_message(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Route a message to the appropriate handler.
        
        Args:
            websocket: The WebSocket connection
            conn_id: Connection identifier
            envelope: The message envelope
        """
        handlers = {
            MessageType.AGENT_HEARTBEAT: self._handle_heartbeat,
            MessageType.AGENT_LIST: self._handle_list,
            MessageType.INTENT_BROADCAST: self._handle_intent_broadcast,
            MessageType.INTENT_OFFER: self._handle_intent_offer,
            MessageType.INTENT_AGREE: self._handle_intent_agree,
            MessageType.SESSION_CLOSE: self._handle_session_close,
            MessageType.EXEC_REQUEST: self._handle_exec_request,
            MessageType.EXEC_ACCEPTED: self._handle_exec_accepted,
            MessageType.EXEC_PROGRESS: self._handle_exec_progress,
            MessageType.EXEC_RESULT: self._handle_exec_result,
            MessageType.EXEC_ERROR: self._handle_exec_error_from_provider,
            MessageType.EXEC_PARTIAL_FAILURE: self._handle_exec_partial_failure,
            # Streaming
            MessageType.EXEC_STREAM_START: self._handle_stream_start,
            MessageType.EXEC_STREAM_CHUNK: self._handle_stream_chunk,
            MessageType.EXEC_STREAM_END: self._handle_stream_end,
            # Trace
            MessageType.TRACE_GET: self._handle_trace_get,
            # Event streaming
            MessageType.EVENT_SUBSCRIBE: self._handle_event_subscribe,
            MessageType.EVENT_UNSUBSCRIBE: self._handle_event_unsubscribe,
            MessageType.EMIT_EVENT: self._handle_emit_event,
        }
        
        handler = handlers.get(envelope.message_type)
        if handler:
            await handler(websocket, conn_id, envelope)
        else:
            logger.warning(f"Unsupported message type: {envelope.message_type}")
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "UNSUPPORTED_MESSAGE_TYPE",
                f"Message type {envelope.message_type} not supported"
            )
    
    async def _handle_heartbeat(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """Handle agent.heartbeat message."""
        success = await self._registry.heartbeat(conn_id)
        
        if success:
            response = create_heartbeat_ack(
                sender_id=BUS_ID,
                tenant_id=envelope.tenant_id,
                correlation_id=envelope.message_id
            )
            await self._send_envelope(websocket, response)
        else:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "AGENT_NOT_FOUND",
                "Agent not registered"
            )
    
    async def _handle_list(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle agent.list message.
        
        Payload options:
        - tenant_id: filter by tenant (default: requester's tenant)
        - only_online: only online agents (default: True)
        """
        filter_tenant = envelope.payload.get("tenant_id", envelope.tenant_id)
        only_online = envelope.payload.get("only_online", True)
        
        agents = await self._registry.list_agents(
            tenant_id=filter_tenant,
            only_online=only_online
        )
        
        response = create_agent_list_result(
            sender_id=BUS_ID,
            tenant_id=envelope.tenant_id,
            correlation_id=envelope.message_id,
            agents=[a.to_public_dict() for a in agents]
        )
        await self._send_envelope(websocket, response)
    
    async def _handle_intent_broadcast(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle intent.broadcast message.
        
        Uses LangGraph-based flow discovery to determine if this needs:
        - Single agent (simple routing)
        - Multiple agents (orchestrated workflow)
        
        The flow planner analyzes the intent and available agents to build
        an executable workflow graph that handles all orchestration.
        
        Payload should include:
        - intent: natural language description of what's needed
        - Additional parameters for the intent
        """
        # Try to discover an agent workflow for this intent
        flow, graph = await self._router.find_agent_flow(envelope)
        
        if flow is None:
            # No flow found - no agents can handle this
            logger.info(f"No flow found for intent {envelope.message_id}")
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "NO_AGENTS",
                "No agents available to fulfill this intent"
            )
            return
        
        # Execute the agent flow (works for 1 or N agents - same path)
        await self._execute_agent_flow(
            websocket=websocket,
            conn_id=conn_id,
            envelope=envelope,
            flow=flow,
            graph=graph,
        )
    
    async def _execute_agent_flow(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope,
        flow: "AgentFlow",
        graph: "CompiledStateGraph | None",
    ) -> None:
        """
        Execute an agent workflow using the discovered flow.
        
        This is the unified flow for 1 or N agents - no special cases.
        
        Flow execution phases:
        1. COLLECT OFFERS: Request offers from all agents in the flow
        2. AGGREGATE: Create a combined offer representing the pipeline
        3. NEGOTIATE: Let requester agree to the offer
        4. EXECUTE: Run each agent in sequence, passing results forward
        5. RETURN: Send aggregated results back to requester
        
        Args:
            websocket: Requester's websocket
            conn_id: Requester's connection ID
            envelope: Original intent envelope
            flow: Discovered agent flow structure
            graph: Compiled LangGraph ready to execute (not used directly yet)
        """
        agent_count = len(flow.agents)
        logger.info(
            f"Executing flow for intent {envelope.message_id}: "
            f"{agent_count} agent(s)"
        )
        
        # Create session that tracks the flow
        session = await self._sessions.create_session(
            tenant_id=envelope.tenant_id,
            requester_agent_id=envelope.sender_id,
            requester_conn_id=conn_id,
            intent_message_id=envelope.message_id,
            intent=envelope.payload,
            flow_agents=[agent.agent_id for agent in flow.agents],
            flow_entry_point=flow.entry_point,
        )
        
        logger.info(
            f"Created session {session.ephemeral_interface_id} "
            f"for {agent_count}-agent flow"
        )
        
        # Step 1: Collect offers from all agents in the flow
        collected_offers = []
        agent_records = {}
        
        for agent_node in flow.agents:
            agent_id = agent_node.agent_id
            agent = await self._registry.get_agent_by_id(UUID(agent_id))
            
            if not agent:
                logger.warning(f"Agent {agent_id} not found for flow execution")
                continue
            
            agent_records[agent_id] = agent
            
            # Send intent.deliver to each agent to request their offer
            deliver_envelope = create_intent_deliver_with_session(
                sender_id=BUS_ID,
                tenant_id=envelope.tenant_id,
                correlation_id=envelope.message_id,
                ephemeral_interface_id=session.ephemeral_interface_id,
                original_sender_id=envelope.sender_id,
                intent_payload={
                    **envelope.payload,
                    "orchestration_role": agent_node.capability,
                    "flow_step": agent_node.description,
                },
                purpose=envelope.purpose,
                data_scope=envelope.data_scope
            )
            
            await self._router.route_to_agent(agent, deliver_envelope)
            logger.debug(f"Sent intent.deliver to agent {agent_id} for offer collection")
        
        # Wait for offers with timeout
        # The offers will be collected by _handle_intent_offer and stored in session
        timeout_seconds = 10.0
        poll_interval = 0.2
        elapsed = 0.0
        expected_offers = len(flow.agents)
        
        while elapsed < timeout_seconds:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            
            # Check how many offers we've collected
            current_session = await self._sessions.get_session(session.ephemeral_interface_id)
            if current_session and len(current_session.offers) >= expected_offers:
                collected_offers = current_session.offers
                break
        
        if len(collected_offers) < expected_offers:
            logger.warning(
                f"Only collected {len(collected_offers)}/{expected_offers} offers "
                f"for flow {session.ephemeral_interface_id}"
            )
        
        if not collected_offers:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "NO_OFFERS",
                "No agents responded with offers for the flow"
            )
            return
        
        # Step 2: Create aggregated offer from all collected offers
        # Combine capabilities, schemas, and requirements from all agents
        aggregated_schemas = []
        aggregated_requires = []
        aggregated_accepts = []
        total_score = 0.0
        
        for offer in collected_offers:
            aggregated_schemas.extend(offer.schema_ids or [])
            aggregated_requires.extend(offer.requires or [])
            aggregated_accepts.extend(offer.accepts or [])
            total_score += offer.score or 0.0
        
        # Remove duplicates
        aggregated_schemas = list(set(aggregated_schemas))
        aggregated_requires = list(set(aggregated_requires))
        aggregated_accepts = list(set(aggregated_accepts))
        avg_score = total_score / len(collected_offers) if collected_offers else 0.0
        
        # Create the aggregated offer
        aggregated_offer_id = str(uuid4())
        aggregated_offer = {
            "offer_id": aggregated_offer_id,
            "provider_id": str(BUS_ID),  # Bus is the "provider" for multi-agent flows
            "schema_ids": aggregated_schemas,
            "requires": aggregated_requires,
            "accepts": aggregated_accepts,
            "score": avg_score,
            "metadata": {
                "is_multi_agent_flow": True,
                "flow_agents": [agent.agent_id for agent in flow.agents],
                "flow_reasoning": flow.reasoning,
                "flow_confidence": flow.confidence,
                "individual_offers": [o.to_dict() for o in collected_offers],
            }
        }
        
        # Store the flow info in the session for later execution
        await self._sessions.set_flow_info(
            session_id=session.ephemeral_interface_id,
            flow=flow,
            agent_records=agent_records,
            aggregated_offer_id=aggregated_offer_id,
        )
        
        # Step 3: Send aggregated offer to requester
        offers_msg = create_intent_offers(
            sender_id=BUS_ID,
            tenant_id=envelope.tenant_id,
            correlation_id=envelope.message_id,
            ephemeral_interface_id=session.ephemeral_interface_id,
            offers=[aggregated_offer]
        )
        await self._send_envelope(websocket, offers_msg)
        
        logger.info(
            f"Sent aggregated offer {aggregated_offer_id} to requester "
            f"for {len(flow.agents)}-agent flow (session: {session.ephemeral_interface_id})"
        )
        
        # The requester will now send intent.agree, which activates the session.
        # When they send exec.request, _execute_agent_flow runs all agents sequentially.

    async def _handle_intent_offer(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle intent.offer message from a provider.
        
        Payload expected:
        - schema_ids: list[str] - schemas the provider can return
        - view_ids: list[str] - views the provider supports
        - requires: list[str] - required inputs
        - accepts: list[str] - accepted input formats
        - score: float - optional relevance score
        - metadata: dict - additional offer data
        
        The ephemeral_interface_id must reference an existing session.
        """
        session_id = envelope.ephemeral_interface_id
        if session_id is None:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "MISSING_SESSION_ID",
                "intent.offer requires ephemeral_interface_id"
            )
            return
        
        # Get the session
        session = await self._sessions.get_session(session_id)
        if session is None:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "SESSION_NOT_FOUND",
                f"Session {session_id} not found"
            )
            return
        
        # Add the offer
        offer = await self._sessions.add_offer(
            session_id=session_id,
            provider_agent_id=envelope.sender_id,
            provider_conn_id=conn_id,
            schema_ids=envelope.payload.get("schema_ids", []),
            view_ids=envelope.payload.get("view_ids", []),
            requires=envelope.payload.get("requires", []),
            accepts=envelope.payload.get("accepts", []),
            score=envelope.payload.get("score", 0.0),
            metadata=envelope.payload.get("metadata", {})
        )
        
        if offer is None:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "OFFER_REJECTED",
                "Could not add offer to session (session may be closed or expired)"
            )
            return
        
        # Ack to provider
        ack = create_intent_offer_received(
            sender_id=BUS_ID,
            tenant_id=envelope.tenant_id,
            correlation_id=envelope.message_id,
            offer_id=offer.offer_id,
            ephemeral_interface_id=session_id
        )
        await self._send_envelope(websocket, ack)
        
        # Record trace event
        self._trace.append(trace_intent_offer(
            tenant_id=envelope.tenant_id,
            session_id=session_id,
            agent_id=envelope.sender_id,
            offer_id=offer.offer_id,
            view_ids=envelope.payload.get("view_ids", [])
        ))
        
        # Don't forward individual offers to requester
        # The aggregated offer is sent by _execute_agent_flow after collecting all offers
        logger.debug(
            f"Session {session_id}: collected offer from {envelope.sender_id}, "
            f"waiting for all offers before sending aggregated offer"
        )
        # Individual offers are stored in session - aggregated offer sent later
    
    async def _handle_intent_agree(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle intent.agree message from the requester.
        
        Payload expected:
        - offer_id: str - the offer to accept
        - chosen_schema_id: str (optional) - the agreed schema
        - chosen_view_id: str (optional) - the agreed view
        
        The ephemeral_interface_id must reference an existing session.
        """
        session_id = envelope.ephemeral_interface_id
        if session_id is None:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "MISSING_SESSION_ID",
                "intent.agree requires ephemeral_interface_id"
            )
            return
        
        offer_id = envelope.payload.get("offer_id")
        if not offer_id:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "MISSING_OFFER_ID",
                "intent.agree requires offer_id in payload"
            )
            return
        
        # Verify requester owns this session
        session = await self._sessions.get_session(session_id)
        if session is None:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "SESSION_NOT_FOUND",
                f"Session {session_id} not found"
            )
            return
        
        if session.requester_agent_id != envelope.sender_id:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "NOT_AUTHORIZED",
                "Only the requester can agree to an offer"
            )
            return
        
        # Verify the agreed offer matches our aggregated offer
        if offer_id != session.aggregated_offer_id:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "INVALID_OFFER",
                f"Offer {offer_id} does not match session offer"
            )
            return
        
        # Activate the session (bus manages the flow execution)
        session.status = SessionStatus.ACTIVE
        session.activated_at = datetime.utcnow()
        session.chosen_offer_id = offer_id
        session.provider_agent_id = BUS_ID  # Bus manages execution
        session.provider_conn_id = "bus-flow-executor"
        
        # Get flow agent info for the response
        agent_count = len(session.flow_agents)
        
        # Send session.created to requester
        session_created_msg = create_session_created(
            sender_id=BUS_ID,
            tenant_id=session.tenant_id,
            correlation_id=session.intent_message_id,
            ephemeral_interface_id=session.ephemeral_interface_id,
            requester_id=session.requester_agent_id,
            provider_id=BUS_ID,
            chosen_offer_id=offer_id,
            chosen_schema_id=envelope.payload.get("chosen_schema_id"),
            chosen_view_id=envelope.payload.get("chosen_view_id"),
            expires_at=session.expires_at,
        )
        # Add flow metadata
        session_created_msg.payload["flow_agents"] = session.flow_agents
        
        await self._send_envelope(websocket, session_created_msg)
        
        # Record trace events
        self._trace.append(trace_intent_agree(
            tenant_id=session.tenant_id,
            session_id=session.ephemeral_interface_id,
            requester_id=session.requester_agent_id,
            provider_id=session.provider_agent_id,
            offer_id=offer_id,
            chosen_view_id=envelope.payload.get("chosen_view_id")
        ))
        self._trace.append(trace_session_created(
            tenant_id=session.tenant_id,
            session_id=session.ephemeral_interface_id,
            requester_id=session.requester_agent_id,
            provider_id=session.provider_agent_id
        ))
        
        logger.info(
            f"Session {session.ephemeral_interface_id} activated with {agent_count} agent(s), "
            f"ready for exec.request"
        )
    
    async def _handle_session_close(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle session.close message.
        
        Either party can close a session.
        """
        session_id = envelope.ephemeral_interface_id
        if session_id is None:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "MISSING_SESSION_ID",
                "session.close requires ephemeral_interface_id"
            )
            return
        
        reason = envelope.payload.get("reason")
        session = await self._sessions.close_session(session_id, reason)
        
        if session is None:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "SESSION_NOT_FOUND",
                f"Session {session_id} not found"
            )
            return
        
        # Notify the other party
        other_conn_id = (
            session.provider_conn_id 
            if conn_id == session.requester_conn_id 
            else session.requester_conn_id
        )
        
        if other_conn_id:
            other_ws = await self._registry.get_websocket(other_conn_id)
            if other_ws:
                # Forward the close message
                close_msg = MessageEnvelope(
                    message_type=MessageType.SESSION_CLOSE,
                    sender_id=BUS_ID,
                    tenant_id=session.tenant_id,
                    ephemeral_interface_id=session_id,
                    correlation_id=envelope.message_id,
                    payload={"reason": reason, "closed_by": str(envelope.sender_id)}
                )
                await self._send_envelope(other_ws, close_msg)
        
        # Record trace event
        self._trace.append(trace_session_closed(
            tenant_id=session.tenant_id,
            session_id=session_id,
            reason=reason or "explicit"
        ))
        
        logger.info(f"Session {session_id} closed (reason: {reason})")
    
    async def _handle_exec_request(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle exec.request message.
        
        Flow:
        1. Validate session is active
        2. Check idempotency (dedup by exec_id or idempotency_key)
        3. Enforce IBAC policy
        4. Create execution record
        5. Route to provider
        """
        session_id = envelope.ephemeral_interface_id
        if session_id is None:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "MISSING_SESSION_ID",
                "exec.request requires ephemeral_interface_id"
            )
            return
        
        # Validate session
        is_valid, error_msg, session = await self._sessions.validate_session_for_exec(
            session_id, envelope.sender_id
        )
        
        if not is_valid:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "INVALID_SESSION",
                error_msg
            )
            return
        
        # Only requester can send exec.request
        if envelope.sender_id != session.requester_agent_id:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "NOT_AUTHORIZED",
                "Only the requester can send exec.request"
            )
            return
        
        # Execute the agent flow (works for 1 to N agents)
        await self._execute_agent_flow(
            websocket=websocket,
            conn_id=conn_id,
            envelope=envelope,
            session=session,
        )
    
    async def _handle_exec_accepted(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle exec.accepted from provider.
        
        Updates execution status and forwards to requester.
        """
        session_id = envelope.ephemeral_interface_id
        if session_id is None:
            return
        
        is_valid, _, session = await self._sessions.validate_session_for_exec(
            session_id, envelope.sender_id
        )
        if not is_valid or envelope.sender_id != session.provider_agent_id:
            return
        
        # Update execution status
        body = envelope.payload or {}
        exec_id = body.get("exec_id")
        if exec_id:
            session.update_execution_status(exec_id, ExecutionStatus.ACCEPTED)
        
        # Forward to requester
        requester_ws = await self._registry.get_websocket(session.requester_conn_id)
        if requester_ws:
            await self._send_envelope(requester_ws, envelope)
            logger.debug(f"exec.accepted forwarded to requester (exec_id={exec_id})")
    
    async def _handle_exec_progress(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle exec.progress from provider (streaming/partial results).
        
        Updates execution status and forwards to requester.
        """
        session_id = envelope.ephemeral_interface_id
        if session_id is None:
            return
        
        is_valid, _, session = await self._sessions.validate_session_for_exec(
            session_id, envelope.sender_id
        )
        if not is_valid or envelope.sender_id != session.provider_agent_id:
            return
        
        # Update execution status
        body = envelope.payload or {}
        exec_id = body.get("exec_id")
        if exec_id:
            session.update_execution_status(exec_id, ExecutionStatus.IN_PROGRESS)
        
        # Forward to requester
        requester_ws = await self._registry.get_websocket(session.requester_conn_id)
        if requester_ws:
            await self._send_envelope(requester_ws, envelope)
            logger.debug(f"exec.progress forwarded to requester (exec_id={exec_id})")
    
    async def _handle_exec_error_from_provider(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle exec.error from provider (execution failed).
        
        Updates execution status and forwards to requester.
        """
        session_id = envelope.ephemeral_interface_id
        if session_id is None:
            return
        
        is_valid, _, session = await self._sessions.validate_session_for_exec(
            session_id, envelope.sender_id
        )
        if not is_valid or envelope.sender_id != session.provider_agent_id:
            return
        
        # Update execution status
        body = envelope.payload or {}
        exec_id = body.get("exec_id")
        if exec_id:
            session.update_execution_status(exec_id, ExecutionStatus.FAILED)
        
        # Forward to requester
        requester_ws = await self._registry.get_websocket(session.requester_conn_id)
        if requester_ws:
            await self._send_envelope(requester_ws, envelope)
            logger.debug(f"exec.error forwarded to requester (exec_id={exec_id})")
    
    async def _handle_exec_result(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle exec.result message.
        
        Flow:
        1. Validate session is active
        2. Verify sender is the provider
        3. Lookup execution record by exec_id
        4. Validate result against agreed view (structure enforcement)
        5. Update execution status
        6. Route to requester
        """
        session_id = envelope.ephemeral_interface_id
        if session_id is None:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "MISSING_SESSION_ID",
                "exec.result requires ephemeral_interface_id"
            )
            return
        
        # Validate session
        is_valid, error_msg, session = await self._sessions.validate_session_for_exec(
            session_id, envelope.sender_id
        )
        
        if not is_valid:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "INVALID_SESSION",
                error_msg
            )
            return
        
        # Only provider can send exec.result
        if envelope.sender_id != session.provider_agent_id:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "NOT_AUTHORIZED",
                "Only the provider can send exec.result"
            )
            return
        
        # === Lookup Execution Record ===
        body = envelope.payload or {}
        exec_id_str = body.get("exec_id")
        exec_record = None
        
        if exec_id_str:
            exec_record = session.get_execution(exec_id_str)
        
        # === Schema Validation ===
        if session.chosen_schema_id and envelope.schema_id != session.chosen_schema_id:
            if exec_record:
                session.update_execution_status(
                    exec_id_str,
                    ExecutionStatus.FAILED,
                    result_valid=False
                )
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "SCHEMA_MISMATCH",
                f"Response schema {envelope.schema_id} does not match agreed schema {session.chosen_schema_id}"
            )
            return
        
        # === View Validation (Structure Enforcement) ===
        # Validate using the capability schema from the session's intent
        capability = session.intent.get("capability", "unknown")
        view_valid = True
        
        if capability and body.get("result"):
            # Validate using the provider agent's registered schema for this capability
            is_valid, error_msg = self._views.validate(
                agent_id=session.provider_agent_id,
                capability=capability,
                payload=body["result"]
            )
            view_valid = is_valid
            
            if not is_valid:
                logger.warning(f"Schema validation failed for capability {capability}: {error_msg}")
                # Note: We still forward but flag the validation failure
                # The requester can decide how to handle invalid structure
        
        # === Update Execution Status ===
        if exec_record:
            session.update_execution_status(
                exec_id_str,
                ExecutionStatus.COMPLETED,
                result_view_id=capability,  # Store capability instead of view_id
                result_valid=view_valid
            )
        
        # === Check if this is an orchestrated flow sub-session ===
        # If so, signal the pending result and don't forward to "requester" (which is the bus)
        pending_key = str(session_id)
        if pending_key in self._pending_orchestration_results:
            event, result_data = self._pending_orchestration_results[pending_key]
            result_data["result"] = body.get("result", body)
            event.set()
            logger.debug(f"Orchestration step result received for session {session_id}")
            return
        
        # Record trace event
        self._trace.append(trace_exec_result(
            tenant_id=envelope.tenant_id,
            session_id=session_id,
            exec_id=exec_id_str or "",
            agent_id=envelope.sender_id,
            view_valid=view_valid
        ))
        
        # === Route to Requester ===
        requester_ws = await self._registry.get_websocket(session.requester_conn_id)
        if requester_ws is None:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "REQUESTER_OFFLINE",
                "Requester is not connected"
            )
            return
        
        # Inject validation result and capability into payload
        result_payload = dict(body)
        result_payload["_view_valid"] = view_valid
        result_payload["_capability"] = capability
        
        forwarded = MessageEnvelope(
            message_id=envelope.message_id,
            message_type=MessageType.EXEC_RESULT,
            sender_id=envelope.sender_id,
            tenant_id=envelope.tenant_id,
            ephemeral_interface_id=session_id,
            correlation_id=envelope.correlation_id,
            schema_id=envelope.schema_id,
            payload=result_payload
        )
        await self._send_envelope(requester_ws, forwarded)
        
        logger.debug(f"exec.result (exec_id={exec_id_str}, view_valid={view_valid}) forwarded to requester in session {session_id}")
    
    async def _handle_exec_partial_failure(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle exec.partial_failure message.
        
        Validates session and routes to requester.
        Allows for renegotiation.
        """
        session_id = envelope.ephemeral_interface_id
        if session_id is None:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "MISSING_SESSION_ID",
                "exec.partial_failure requires ephemeral_interface_id"
            )
            return
        
        # Validate session
        is_valid, error_msg, session = await self._sessions.validate_session_for_exec(
            session_id, envelope.sender_id
        )
        
        if not is_valid:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "INVALID_SESSION",
                error_msg
            )
            return
        
        # Only provider can send exec.partial_failure
        if envelope.sender_id != session.provider_agent_id:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "NOT_AUTHORIZED",
                "Only the provider can send exec.partial_failure"
            )
            return
        
        # Route to requester
        requester_ws = await self._registry.get_websocket(session.requester_conn_id)
        if requester_ws is None:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "REQUESTER_OFFLINE",
                "Requester is not connected"
            )
            return
        
        # Forward the partial failure
        await self._send_envelope(requester_ws, envelope)
        
        logger.info(
            f"exec.partial_failure forwarded to requester in session {session_id} "
            f"(can_retry: {envelope.payload.get('can_retry', True)})"
        )

    # =========================================================================
    # Streaming Handlers
    # =========================================================================
    
    async def _handle_stream_start(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle exec.stream.start from provider.
        
        Validates session and routes to requester via queue.
        """
        session_id = envelope.ephemeral_interface_id
        if session_id is None:
            return
        
        is_valid, _, session = await self._sessions.validate_session_for_exec(
            session_id, envelope.sender_id
        )
        if not is_valid or envelope.sender_id != session.provider_agent_id:
            return
        
        body = envelope.payload or {}
        exec_id = body.get("exec_id", "")
        
        # Record trace event
        self._trace.append(trace_stream_start(
            tenant_id=envelope.tenant_id,
            session_id=session_id,
            exec_id=exec_id,
            agent_id=envelope.sender_id,
            content_type=body.get("content_type")
        ))
        
        # Route to requester via queue
        await self._send_to_requester_queued(session, envelope)
        
        logger.debug(f"stream.start forwarded (exec_id={exec_id})")
    
    async def _handle_stream_chunk(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle exec.stream.chunk from provider.
        
        Routes to requester via queue with backpressure handling.
        Trace records only chunk metadata (length, hash).
        """
        session_id = envelope.ephemeral_interface_id
        if session_id is None:
            return
        
        is_valid, _, session = await self._sessions.validate_session_for_exec(
            session_id, envelope.sender_id
        )
        if not is_valid or envelope.sender_id != session.provider_agent_id:
            return
        
        body = envelope.payload or {}
        exec_id = body.get("exec_id", "")
        seq = body.get("seq", 0)
        data = body.get("data", "")
        
        # Record trace with metadata only (not full chunk)
        chunk_len = len(data) if isinstance(data, (str, bytes)) else 0
        self._trace.append(trace_stream_chunk(
            tenant_id=envelope.tenant_id,
            session_id=session_id,
            exec_id=exec_id,
            seq=seq,
            chunk_len=chunk_len,
            chunk_hash=None  # Skip hash for performance
        ))
        
        # Route to requester via queue with backpressure
        try:
            await self._send_to_requester_queued(session, envelope)
        except QueueFullError as e:
            # Backpressure: terminate stream
            logger.warning(f"Backpressure on {session.requester_conn_id}: {e}")
            
            # Record backpressure event
            self._trace.append(trace_stream_backpressure(
                tenant_id=envelope.tenant_id,
                session_id=session_id,
                exec_id=exec_id,
                queue_size=e.queue_size
            ))
            
            # Send error to provider
            error_msg = create_exec_error(
                sender_id=BUS_ID,
                tenant_id=envelope.tenant_id,
                ephemeral_interface_id=session_id,
                correlation_id=envelope.correlation_id or uuid4(),
                exec_id=exec_id,
                error_code="BACKPRESSURE",
                error_message="Requester queue full, stream terminated"
            )
            await self._send_envelope(websocket, error_msg)
            
            # Also notify requester
            requester_ws = await self._registry.get_websocket(session.requester_conn_id)
            if requester_ws:
                await self._send_envelope(requester_ws, error_msg)
            return
        
        logger.debug(f"stream.chunk forwarded (exec_id={exec_id}, seq={seq})")
    
    async def _handle_stream_end(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle exec.stream.end from provider.
        
        Validates session and routes to requester.
        """
        session_id = envelope.ephemeral_interface_id
        if session_id is None:
            return
        
        is_valid, _, session = await self._sessions.validate_session_for_exec(
            session_id, envelope.sender_id
        )
        if not is_valid or envelope.sender_id != session.provider_agent_id:
            return
        
        body = envelope.payload or {}
        exec_id = body.get("exec_id", "")
        
        # Record trace event
        self._trace.append(trace_stream_end(
            tenant_id=envelope.tenant_id,
            session_id=session_id,
            exec_id=exec_id,
            total_chunks=body.get("total_chunks"),
            total_bytes=body.get("total_bytes")
        ))
        
        # Update execution status
        if exec_id:
            session.update_execution_status(exec_id, ExecutionStatus.COMPLETED)
        
        # Route to requester
        await self._send_to_requester_queued(session, envelope)
        
        logger.debug(f"stream.end forwarded (exec_id={exec_id})")
    
    async def _send_to_requester_queued(
        self,
        session,
        envelope: MessageEnvelope
    ) -> None:
        """
        Send envelope to requester via their outbound queue.
        
        Raises:
            QueueFullError: If requester queue is full
        """
        requester_ws = await self._registry.get_websocket(session.requester_conn_id)
        if requester_ws is None:
            return
        
        # Ensure queue exists for this connection
        async def send_fn(msg: str) -> None:
            await requester_ws.send_text(msg)
        
        queue = await self._queues.get_or_create(session.requester_conn_id, send_fn)
        
        # Put on queue (raises QueueFullError if full)
        queue.put_nowait(envelope.model_dump_json())

    # =========================================================================
    # Trace Handlers
    # =========================================================================
    
    async def _handle_trace_get(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle trace.get request.
        
        Returns trace events for a session or execution.
        """
        body = envelope.payload or {}
        session_id = envelope.ephemeral_interface_id
        exec_id = body.get("exec_id")
        tail = body.get("tail", 100)
        
        # Limit tail to prevent huge responses
        tail = min(tail, 1000)
        
        if session_id:
            events, truncated = self._trace.get_by_session(
                session_id,
                tail=tail,
                exec_id=exec_id
            )
        elif exec_id:
            events, truncated = self._trace.get_by_exec(exec_id, tail=tail)
        else:
            events, truncated = self._trace.get_global(tail=tail)
        
        # Create response
        response = create_trace_result(
            sender_id=BUS_ID,
            tenant_id=envelope.tenant_id,
            ephemeral_interface_id=session_id,
            events=[e.to_dict() for e in events],
            truncated=truncated,
            correlation_id=envelope.message_id
        )
        
        await self._send_envelope(websocket, response)
        logger.debug(f"trace.result sent ({len(events)} events, truncated={truncated})")

    # =========================================================================
    # Event Streaming Handlers
    # =========================================================================
    
    async def _handle_event_subscribe(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle event.subscribe request.
        
        Creates a subscription for the connection and starts event delivery.
        
        Payload:
        - session_id: UUID of session to subscribe to
        - exec_id: Optional execution ID to filter events
        - event_types: Optional list of event type prefixes to filter
        - include_history: Whether to replay past events (default: true)
        """
        body = envelope.payload or {}
        session_id_str = body.get("session_id")
        exec_id = body.get("exec_id")
        event_type_prefixes = body.get("event_types")
        include_history = body.get("include_history", True)
        
        if not session_id_str:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "MISSING_SESSION_ID",
                "session_id is required for event subscription"
            )
            return
        
        try:
            session_id = UUID(session_id_str)
        except ValueError:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "INVALID_SESSION_ID",
                "session_id must be a valid UUID"
            )
            return
        
        # Validate session exists and requester has access
        session = await self._sessions.get_session(session_id)
        if session is None:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "SESSION_NOT_FOUND",
                f"Session {session_id} not found"
            )
            return
        
        # Check tenant match
        if session.tenant_id != envelope.tenant_id:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "TENANT_MISMATCH",
                "Cannot subscribe to events from different tenant"
            )
            return
        
        # Build filter
        filter_kwargs = {"session_ids": {session_id}}
        if exec_id:
            filter_kwargs["exec_ids"] = {exec_id}
        if event_type_prefixes:
            filter_kwargs["event_type_prefixes"] = event_type_prefixes
        
        event_filter = EventFilter(**filter_kwargs)
        
        # Create subscription
        try:
            if exec_id:
                subscription = await self._events.subscribe_execution(
                    conn_id=conn_id,
                    session_id=session_id,
                    exec_id=exec_id,
                )
            else:
                subscription = await self._events.subscribe_session(
                    conn_id=conn_id,
                    session_id=session_id,
                    include_history=include_history,
                )
        except ValueError as e:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "SUBSCRIPTION_ERROR",
                str(e)
            )
            return
        
        # Start event delivery task for this subscription
        delivery_task = asyncio.create_task(
            self._deliver_events_to_connection(
                conn_id=conn_id,
                websocket=websocket,
                subscription=subscription,
                session_id=session_id,
                tenant_id=envelope.tenant_id,
            ),
            name=f"event_delivery_{subscription.subscription_id}"
        )
        self._event_delivery_tasks[subscription.subscription_id] = delivery_task
        
        # Send confirmation
        response = create_event_subscribed(
            sender_id=BUS_ID,
            tenant_id=envelope.tenant_id,
            correlation_id=envelope.message_id,
            session_id=session_id,
            subscription_id=subscription.subscription_id,
            event_types=event_type_prefixes,
        )
        await self._send_envelope(websocket, response)
        logger.debug(f"Event subscription created: {subscription.subscription_id}")
    
    async def _handle_event_unsubscribe(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle event.unsubscribe request.
        
        Cancels event subscriptions for the connection.
        
        Payload:
        - session_id: UUID of session to unsubscribe from
        - subscription_id: Optional specific subscription to cancel
        """
        body = envelope.payload or {}
        session_id_str = body.get("session_id")
        subscription_id = body.get("subscription_id")
        
        if subscription_id:
            # Cancel specific subscription
            removed = await self._events.unsubscribe(subscription_id)
            if subscription_id in self._event_delivery_tasks:
                self._event_delivery_tasks[subscription_id].cancel()
                del self._event_delivery_tasks[subscription_id]
            logger.debug(f"Event subscription cancelled: {subscription_id} (removed={removed})")
        else:
            # Cancel all subscriptions for this connection
            count = await self._events.unsubscribe_connection(conn_id)
            # Cancel delivery tasks
            for sub_id, task in list(self._event_delivery_tasks.items()):
                if sub_id.startswith(f"{conn_id}:"):
                    task.cancel()
                    del self._event_delivery_tasks[sub_id]
            logger.debug(f"All event subscriptions cancelled for {conn_id} (count={count})")
    
    async def _handle_emit_event(
        self,
        websocket: WebSocket,
        conn_id: str,
        envelope: MessageEnvelope
    ) -> None:
        """
        Handle emit.event from an agent.
        
        Allows agents to emit custom events during execution.
        These events are published to all subscribers of the session.
        
        Payload:
        - event_type: Event type string (e.g., "agent.status", "reasoning.thought")
        - message: Human-readable message
        - exec_id: Optional execution ID
        - severity: Event severity (debug, info, warning, error, critical)
        - progress_percent: Optional progress percentage (0-100)
        - data: Optional additional data
        """
        session_id = envelope.ephemeral_interface_id
        if session_id is None:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "MISSING_SESSION_ID",
                "ephemeral_interface_id is required for event emission"
            )
            return
        
        # Validate session
        session = await self._sessions.get_session(session_id)
        if session is None:
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "SESSION_NOT_FOUND",
                f"Session {session_id} not found"
            )
            return
        
        # Check sender is part of the session (requester or provider)
        agent_record = await self._registry.get_by_conn_id(conn_id)
        if agent_record is None:
            return
        
        if (agent_record.agent_id != session.requester_agent_id and
            agent_record.agent_id != session.provider_agent_id):
            await self._send_error(
                websocket,
                envelope.message_id,
                envelope.tenant_id,
                "NOT_SESSION_PARTICIPANT",
                "Only session participants can emit events"
            )
            return
        
        # Parse event data from payload
        body = envelope.payload or {}
        event_type_str = body.get("event_type", "agent.custom")
        message = body.get("message", "")
        exec_id = body.get("exec_id")
        severity_str = body.get("severity", "info")
        progress_percent = body.get("progress_percent")
        data = body.get("data", {})
        
        # Map severity string to enum
        severity_map = {
            "debug": EventSeverity.DEBUG,
            "info": EventSeverity.INFO,
            "warning": EventSeverity.WARNING,
            "error": EventSeverity.ERROR,
            "critical": EventSeverity.CRITICAL,
        }
        severity = severity_map.get(severity_str.lower(), EventSeverity.INFO)
        
        # Map event type string to enum
        try:
            event_type = EventType(event_type_str)
        except ValueError:
            # Default to AGENT_CUSTOM for unknown types
            event_type = EventType.AGENT_CUSTOM
            data["original_event_type"] = event_type_str
        
        # Publish the event
        await self._events.publish_custom_event(
            session_id=session_id,
            tenant_id=envelope.tenant_id,
            event_type=event_type,
            message=message,
            exec_id=exec_id,
            source_agent_id=agent_record.agent_id,
            source_name=agent_record.metadata.get("name", str(agent_record.agent_id)),
            severity=severity,
            progress_percent=progress_percent,
            data=data,
        )
        
        logger.debug(f"Event emitted: {event_type_str} from {agent_record.agent_id}")
    
    async def _deliver_events_to_connection(
        self,
        conn_id: str,
        websocket: WebSocket,
        subscription: EventSubscription,
        session_id: UUID,
        tenant_id: str,
    ) -> None:
        """
        Background task to deliver events to a WebSocket connection.
        
        Reads from the subscription queue and sends events as envelopes.
        """
        try:
            async for event in subscription:
                # Convert event to envelope
                envelope = create_event_envelope(
                    sender_id=BUS_ID,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    event_data=event.to_payload(),
                    exec_id=event.exec_id,
                )
                
                # Send to WebSocket
                try:
                    await self._send_envelope(websocket, envelope)
                except Exception as e:
                    logger.warning(f"Failed to send event to {conn_id}: {e}")
                    break
        
        except asyncio.CancelledError:
            logger.debug(f"Event delivery cancelled for {subscription.subscription_id}")
        
        except Exception as e:
            logger.error(f"Error in event delivery for {conn_id}: {e}")
        
        finally:
            subscription.close()
    
    async def _cleanup_connection_events(self, conn_id: str) -> None:
        """
        Clean up event subscriptions when a connection closes.
        """
        # Cancel delivery tasks for this connection
        for sub_id, task in list(self._event_delivery_tasks.items()):
            if sub_id.startswith(f"{conn_id}:"):
                task.cancel()
                del self._event_delivery_tasks[sub_id]
        
        # Unsubscribe all
        await self._events.unsubscribe_connection(conn_id)
