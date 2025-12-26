"""
Intent Router

Routes intent broadcasts to candidate agents using LangChain-based semantic matching.

Routing flow:
1. Pre-filter by tenant_id (tenant isolation)
2. Exclude sender (don't route back to broadcaster)
3. Apply LLM-based semantic matching to find best capability matches
4. Route to top N candidates (default: 3)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from src.registry import AgentRecord, AgentRegistry
from src.protocol.envelope import MessageEnvelope
from src.routing.semantic_matcher import SemanticMatcher
from src.orchestration.flow_planner import AgentFlowPlanner, AgentFlow

if TYPE_CHECKING:
    from langgraph.graph import CompiledStateGraph

logger = logging.getLogger(__name__)


class IntentRouter:
    """
    Routes intent broadcasts to candidate agents using semantic matching.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        max_candidates: int = 3,
        semantic_matcher: SemanticMatcher | None = None,
        flow_planner: AgentFlowPlanner | None = None,
        llm_model: str = "gpt-4o-mini",
    ):
        """
        Initialize the router.

        Args:
            registry: Agent registry for lookups
            max_candidates: Maximum number of agents to route to
            semantic_matcher: Custom semantic matcher (optional)
            flow_planner: Custom flow planner for multi-agent orchestration (optional)
            llm_model: LLM model to use (e.g., "gpt-4o-mini", "claude-sonnet-4-5-20250929", "ollama/llama3.2")
        """
        self._registry = registry
        self._max_candidates = max_candidates

        # Initialize semantic matcher
        if semantic_matcher:
            self._matcher = semantic_matcher
        else:
            self._matcher = SemanticMatcher(model=llm_model)
        
        # Initialize flow planner for multi-agent orchestration
        if flow_planner:
            self._flow_planner = flow_planner
        else:
            self._flow_planner = AgentFlowPlanner(model=llm_model)

    async def find_candidates_for_intent(
        self, envelope: MessageEnvelope
    ) -> list[AgentRecord]:
        """
        Find candidate agents for an intent broadcast using semantic matching.

        Extracts routing hints from the envelope payload:
        - intent: Natural language description of what's needed

        Args:
            envelope: The intent.broadcast message

        Returns:
            List of candidate AgentRecords
        """
        # Extract intent
        intent_text = envelope.payload.get("intent", "")

        if not intent_text:
            logger.warning(f"Intent routing: {envelope.message_id} -> no intent provided")
            return []

        # Pre-filter: get all potential candidates from registry
        # (same tenant, online, not sender)
        all_candidates = await self._registry.find_by_tenant(
            tenant_id=envelope.tenant_id
        )

        # Exclude sender
        all_candidates = [
            c for c in all_candidates if c.agent_id != envelope.sender_id
        ]

        if not all_candidates:
            logger.info(
                f"Intent routing: {envelope.message_id} -> no candidates available"
            )
            return []

        # Use semantic matching
        try:
            matched_agents = await self._matcher.rank_agents(
                intent=intent_text,
                agents=all_candidates,
                top_k=self._max_candidates,
            )

            if not matched_agents:
                logger.info(
                    f"Intent routing (semantic): {envelope.message_id} -> "
                    f"no matches found for intent '{str(intent_text)[:50]}...'"
                )
                return []

            logger.info(
                f"Intent routing (semantic): {envelope.message_id} -> "
                f"{len(matched_agents)} candidates found for intent '{str(intent_text)[:50]}...'"
            )

            return matched_agents

        except Exception as e:
            logger.error(f"Semantic matching failed: {e}", exc_info=True)
            return []
    
    async def find_agent_flow(
        self,
        envelope: MessageEnvelope,
    ) -> tuple[AgentFlow | None, "CompiledStateGraph | None"]:
        """
        Discover an executable agent workflow for the intent.
        
        This is the evolution of find_candidates_for_intent. Instead of just
        returning a list of candidate agents, this uses LLM + LangGraph to:
        1. Analyze the intent and available agents
        2. Discover the optimal workflow (single agent, sequential, parallel, DAG)
        3. Build an executable LangGraph that represents the flow
        4. Return the flow structure and executable graph
        
        Args:
            envelope: The intent.broadcast message
        
        Returns:
            Tuple of (AgentFlow, CompiledStateGraph) or (None, None) if no flow found
        """
        # Extract intent
        intent_text = envelope.payload.get("intent", "")
        params = {k: v for k, v in envelope.payload.items() if k != "intent"}
        
        if not intent_text:
            logger.warning(f"Flow discovery: {envelope.message_id} -> no intent provided")
            return None, None
        
        # Get all available agents (same tenant, online, not sender)
        all_agents = await self._registry.find_by_tenant(
            tenant_id=envelope.tenant_id
        )
        
        logger.debug(
            f"Flow discovery: found {len(all_agents)} agents in tenant {envelope.tenant_id}"
        )
        
        # Exclude sender
        all_agents = [
            agent for agent in all_agents 
            if agent.agent_id != envelope.sender_id
        ]
        
        logger.info(
            f"Flow discovery: {envelope.message_id} -> "
            f"{len(all_agents)} available agents (excluding sender {envelope.sender_id})"
        )
        for agent in all_agents:
            logger.info(
                f"  - Agent ID: {agent.agent_id} | "
                f"Capabilities: {agent.capabilities} | "
                f"Description: {agent.description or 'N/A'}"
            )
        
        if not all_agents:
            logger.info(f"Flow discovery: {envelope.message_id} -> no agents available")
            return None, None
        
        # Use flow planner to discover the workflow
        try:
            flow, graph = await self._flow_planner.plan_and_build(
                intent=intent_text,
                available_agents=all_agents,
                params=params,
            )
            
            if flow is None:
                logger.info(
                    f"Flow discovery: {envelope.message_id} -> "
                    f"no flow found for intent '{intent_text[:50]}...'"
                )
                return None, None
            
            logger.info(
                f"Flow discovery: {envelope.message_id} -> "
                f"found {len(flow.agents)}-agent flow "
                f"(confidence: {flow.confidence:.2f})"
            )
            
            return flow, graph
            
        except Exception as e:
            logger.error(f"Flow discovery failed: {e}", exc_info=True)
            return None, None

    async def route_to_agent(
        self, agent: AgentRecord, envelope: MessageEnvelope
    ) -> bool:
        """
        Route an envelope to a specific agent.

        Args:
            agent: Target agent
            envelope: Message to send

        Returns:
            True if message was sent, False otherwise
        """
        websocket = await self._registry.get_websocket(agent.conn_id)
        if websocket is None:
            logger.warning(f"No websocket for agent {agent.agent_id}")
            return False

        try:
            # Serialize envelope to JSON
            message_json = envelope.model_dump_json()
            await websocket.send_text(message_json)
            logger.debug(f"Routed {envelope.message_type} to {agent.agent_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to route to {agent.agent_id}: {e}")
            return False

    async def broadcast_to_candidates(
        self, candidates: list[AgentRecord], envelope: MessageEnvelope
    ) -> list[UUID]:
        """
        Broadcast an envelope to multiple candidate agents.

        Args:
            candidates: List of target agents
            envelope: Message to send

        Returns:
            List of agent_ids that received the message
        """
        successful = []

        for agent in candidates:
            if await self.route_to_agent(agent, envelope):
                successful.append(agent.agent_id)

        logger.info(
            f"Broadcast complete: {len(successful)}/{len(candidates)} delivered"
        )

        return successful
