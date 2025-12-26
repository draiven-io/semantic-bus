"""
Agent Flow Planner using LangGraph 1.0

Uses LangGraph to discover and build executable agent workflows.
Instead of keyword matching, the LLM analyzes the intent and available agents
to construct a StateGraph representing the actual flow of data and control.

Key concepts:
- Each agent becomes a node in the graph
- LLM determines execution order and dependencies
- Conditional routing handled by LangGraph's conditional edges
- The flow itself is executable - no manual orchestration needed
"""

import logging
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict

from src.llm import create_llm
from src.registry import AgentRecord

logger = logging.getLogger(__name__)


# =============================================================================
# State Definition (LangGraph 1.0 uses TypedDict)
# =============================================================================

class AgentFlowState(TypedDict):
    """
    State that flows through the agent graph.
    
    This is what gets passed between agent nodes and updated as the flow executes.
    """
    # Original intent and context
    intent: str
    params: dict[str, Any]
    tenant_id: str
    requester_id: str
    
    # Execution tracking
    current_step: int
    completed_agents: list[str]
    
    # Data accumulation
    agent_outputs: dict[str, Any]  # agent_id -> output
    
    # Flow control
    next_agent: str | None
    should_continue: bool
    
    # Error handling
    errors: list[dict[str, Any]]


# =============================================================================
# LLM-Structured Outputs for Flow Planning
# =============================================================================

class AgentNode(BaseModel):
    """Represents a single agent in the flow."""
    agent_id: str = Field(description="The agent's unique identifier")
    capability: str = Field(description="The capability this agent provides")
    depends_on: list[str] = Field(
        default_factory=list,
        description="List of agent_ids that must execute before this agent"
    )
    required_inputs: list[str] = Field(
        default_factory=list,
        description="Names of data fields required from previous agents"
    )
    outputs: list[str] = Field(
        default_factory=list,
        description="Names of data fields this agent will produce"
    )
    description: str = Field(description="Brief description of what this agent does in the flow")


class RouteMapping(BaseModel):
    """A single condition-to-agent mapping."""
    condition_value: str = Field(description="The value to check for")
    next_agent: str = Field(description="The agent to route to if condition matches")


class ConditionalRoute(BaseModel):
    """Represents a conditional decision point in the flow."""
    from_agent: str = Field(description="Agent that produces the condition")
    condition_field: str = Field(description="Field to check for routing decision")
    routes: list[RouteMapping] = Field(
        default_factory=list,
        description="List of condition-to-agent mappings"
    )
    default_route: str = Field(description="Default agent if condition doesn't match")


class AgentFlow(BaseModel):
    """
    Complete flow structure returned by LLM.
    
    This represents the discovered workflow of agents to fulfill the intent.
    """
    intent_summary: str = Field(description="Brief summary of what this flow accomplishes")
    
    agents: list[AgentNode] = Field(
        description="All agents in the flow with their dependencies"
    )
    
    entry_point: str = Field(
        description="Agent ID to start the flow"
    )
    
    conditional_routes: list[ConditionalRoute] = Field(
        default_factory=list,
        description="Optional conditional routing decisions"
    )
    
    parallel_groups: list[list[str]] = Field(
        default_factory=list,
        description="Groups of agent IDs that can execute in parallel"
    )
    
    reasoning: str = Field(
        description="Explanation of why this flow was chosen"
    )
    
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in this flow design"
    )


# =============================================================================
# Flow Planner
# =============================================================================

class AgentFlowPlanner:
    """
    Uses LLM + LangGraph to discover agent workflows.
    
    Instead of keyword matching, this planner:
    1. Analyzes the intent with LLM
    2. Discovers which agents are needed and in what order
    3. Builds an executable LangGraph StateGraph
    4. Returns the graph ready to execute
    """
    
    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.0,
    ):
        """
        Initialize the flow planner.
        
        Args:
            model: LLM model to use for flow discovery
            temperature: LLM temperature
        """
        self._model = model
        self._temperature = temperature
        self._llm = None
    
    async def _init_llm(self) -> bool:
        """Initialize the LLM with structured output."""
        if self._llm is not None:
            return True
        
        try:
            llm = create_llm(
                model=self._model,
                temperature=self._temperature,
            )
            self._llm = llm.with_structured_output(AgentFlow)
            logger.info(f"Initialized flow planner with model: {self._model}")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize flow planner LLM: {e}")
            return False
    
    async def discover_flow(
        self,
        intent: str,
        available_agents: list[AgentRecord],
        params: dict[str, Any] | None = None,
    ) -> AgentFlow | None:
        """
        Discover the agent flow needed to fulfill an intent.
        
        Uses LLM to analyze the intent and available agents, then returns
        a structured flow describing which agents to use and in what order.
        
        Args:
            intent: Natural language description of what's needed
            available_agents: List of agents that could be used
            params: Additional parameters from the intent
        
        Returns:
            AgentFlow structure, or None if no flow can be determined
        """
        if not await self._init_llm():
            return None
        
        if not available_agents:
            logger.warning("No agents available to build flow")
            return None
        
        logger.info(f"Discovering flow for intent: '{intent[:80]}...'")
        logger.info(f"Available agents: {len(available_agents)}")
        
        # Build agent descriptions for LLM
        agent_descriptions = []
        for agent in available_agents:
            caps = ", ".join(agent.capabilities) if agent.capabilities else "none"
            logger.info(
                f"  Agent: {agent.agent_id} | "
                f"Capabilities: {caps} | "
                f"Description: {agent.description or 'N/A'}"
            )
            
            agent_descriptions.append(
                f"Agent '{agent.agent_id}':\n"
                f"  Description: {agent.description or 'N/A'}\n"
                f"  Capabilities: {caps}\n"
                f"  Tags: {', '.join(agent.tags) if agent.tags else 'none'}"
            )
        
        logger.debug(f"Agent descriptions for LLM:\n{chr(10).join(agent_descriptions)}")
        
        # Build the prompt
        system_prompt = """You are an expert agent workflow designer.

Your task is to analyze a user's intent and available agents, then design
an executable workflow that fulfills the intent.

Consider:
1. Which agents are needed based on their capabilities
2. Dependencies between agents (which must run first)
3. Data flow (which agent outputs feed into which agent inputs)
4. Whether agents can run in parallel or must be sequential
5. Any conditional routing needed based on intermediate results

Design the simplest flow that accomplishes the goal.
If the intent only needs one agent, return a flow with just that agent.
If multiple agents are needed, specify their execution order and dependencies.

IMPORTANT: Each agent can only appear ONCE in the flow. Use the exact agent_id provided.
If an agent needs to perform multiple sequential tasks, it should still only appear once.
Do NOT duplicate agents in the flow. If you need to use the same agent again, you can refer to the existing node.
"""
        
        user_prompt = f"""Intent: {intent}

Parameters: {params or 'none'}

Available Agents:
{chr(10).join(agent_descriptions)}

Design an executable agent workflow to fulfill this intent.
"""
        
        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            
            flow: AgentFlow = await self._llm.ainvoke(messages)
            
            logger.info(
                f"Discovered flow for intent '{intent[:50]}...': "
                f"{len(flow.agents)} agents, entry={flow.entry_point}"
            )
            logger.debug(f"Flow reasoning: {flow.reasoning}")
            logger.debug(f"Flow confidence: {flow.confidence}")
            logger.debug(f"Flow agents: {[a.agent_id for a in flow.agents]}")
            
            # Check for duplicates in discovered flow and deduplicate
            agent_ids = [a.agent_id for a in flow.agents]
            if len(agent_ids) != len(set(agent_ids)):
                duplicates = [aid for aid in agent_ids if agent_ids.count(aid) > 1]
                logger.warning(f"LLM returned duplicate agent IDs: {set(duplicates)}")
                logger.warning(f"Full agent list: {agent_ids}")
                logger.warning("Deduplicating agents - keeping first occurrence of each")
                
                # Deduplicate by keeping first occurrence of each agent
                seen = set()
                unique_agents = []
                for agent in flow.agents:
                    if agent.agent_id not in seen:
                        seen.add(agent.agent_id)
                        unique_agents.append(agent)
                    else:
                        logger.debug(f"  Removing duplicate: {agent.agent_id}")
                
                flow.agents = unique_agents
                logger.info(f"Deduplicated flow now has {len(flow.agents)} unique agents")
            
            return flow
            
        except Exception as e:
            logger.error(f"Failed to discover flow: {e}")
            return None
    
    def build_langgraph(
        self,
        flow: AgentFlow,
        agent_registry: dict[str, AgentRecord],
    ) -> StateGraph:
        """
        Build an executable LangGraph StateGraph from the discovered flow.
        
        Uses LangGraph 1.0 API with START and END constants.
        
        Args:
            flow: The agent flow structure from LLM
            agent_registry: Mapping of agent_id -> AgentRecord
        
        Returns:
            Compiled StateGraph ready to execute
        """
        logger.info(f"Building LangGraph for flow with {len(flow.agents)} agents")
        logger.debug(f"Flow entry point: {flow.entry_point}")
        logger.debug(f"Agent IDs in flow: {[a.agent_id for a in flow.agents]}")
        
        # Sanity check for duplicates (should not happen after deduplication in discover_flow)
        agent_ids = [a.agent_id for a in flow.agents]
        if len(agent_ids) != len(set(agent_ids)):
            duplicates = [aid for aid in agent_ids if agent_ids.count(aid) > 1]
            logger.error(f"Unexpected duplicate agent IDs in flow: {set(duplicates)}")
            logger.error("This should have been caught in discover_flow")
            raise ValueError(f"Flow contains duplicate agent IDs: {set(duplicates)}")
        
        # Create the graph with state schema
        workflow = StateGraph(AgentFlowState)
        
        # Add agent nodes
        logger.info("Adding agent nodes to graph...")
        for agent_node in flow.agents:
            agent_id = agent_node.agent_id
            logger.debug(f"Adding node: {agent_id} (capability: {agent_node.capability})")
            
            # Create node function that executes this agent
            def create_agent_fn(node_id: str, node_info: AgentNode):
                async def agent_fn(state: AgentFlowState) -> dict[str, Any]:
                    """Execute this agent and update state."""
                    logger.info(f"Executing agent node: {node_id}")
                    
                    # In real implementation, this would:
                    # 1. Send intent to the agent via the bus
                    # 2. Wait for agent's response
                    # 3. Extract outputs according to node_info.outputs
                    # For now, placeholder
                    
                    output = {
                        "agent_id": node_id,
                        "capability": node_info.capability,
                        "result": f"Result from {node_id}",
                    }
                    
                    return {
                        "current_step": state["current_step"] + 1,
                        "completed_agents": state["completed_agents"] + [node_id],
                        "agent_outputs": {
                            **state["agent_outputs"],
                            node_id: output,
                        },
                    }
                
                return agent_fn
            
            workflow.add_node(agent_id, create_agent_fn(agent_id, agent_node))
            logger.debug(f"Successfully added node: {agent_id}")
        
        # Set entry point using START (LangGraph 1.0)
        logger.info(f"Setting entry point: START -> {flow.entry_point}")
        workflow.add_edge(START, flow.entry_point)
        
        # Add edges based on dependencies
        logger.info("Adding dependency edges...")
        dependency_edges_added = set()
        
        for agent_node in flow.agents:
            if agent_node.depends_on:
                logger.debug(f"Agent {agent_node.agent_id} depends on: {agent_node.depends_on}")
                # Has dependencies - add edges from dependencies to this node
                for dep_id in agent_node.depends_on:
                    if dep_id in agent_registry or dep_id in [a.agent_id for a in flow.agents]:
                        logger.debug(f"  Adding edge: {dep_id} -> {agent_node.agent_id}")
                        workflow.add_edge(dep_id, agent_node.agent_id)
                        dependency_edges_added.add((dep_id, agent_node.agent_id))
                    else:
                        logger.warning(f"  Dependency {dep_id} not found in registry or flow agents")
        
        # Handle parallel groups
        # Agents in the same parallel group should all connect to the next step
        logger.info(f"Processing {len(flow.parallel_groups)} parallel groups...")
        for parallel_group in flow.parallel_groups:
            if len(parallel_group) > 1:
                # These agents can run in parallel
                # They all start from same predecessor and converge to same successor
                logger.info(f"Parallel group detected: {parallel_group}")
                # In LangGraph 1.0, parallel execution happens when multiple nodes
                # have no dependencies between them
        
        # Handle conditional routes
        logger.info(f"Processing {len(flow.conditional_routes)} conditional routes...")
        for route in flow.conditional_routes:
            from_agent = route.from_agent
            logger.debug(f"Conditional route from {from_agent} on field '{route.condition_field}'")
            
            def create_routing_fn(route_config: ConditionalRoute):
                def route_fn(state: AgentFlowState) -> str:
                    """Determine next agent based on state."""
                    # Check the condition field in the agent's output
                    agent_output = state["agent_outputs"].get(route_config.from_agent, {})
                    condition_value = agent_output.get(route_config.condition_field)
                    
                    # Return the appropriate next agent by checking route mappings
                    for route_mapping in route_config.routes:
                        if route_mapping.condition_value == str(condition_value):
                            return route_mapping.next_agent
                    
                    return route_config.default_route
                
                return route_fn
            
            # Add conditional edges in LangGraph 1.0
            # Map the routing function to possible destinations
            # Build set of all possible next agents from route mappings
            possible_agents = {rm.next_agent for rm in route.routes}
            possible_agents.add(route.default_route)
            logger.debug(f"  Possible next agents: {possible_agents}")
            
            workflow.add_conditional_edges(
                from_agent,
                create_routing_fn(route),
                {next_agent: next_agent for next_agent in possible_agents}
            )
        
        # Connect terminal nodes to END
        logger.info("Connecting terminal nodes to END...")
        # Nodes with no outgoing edges should end
        nodes_with_outgoing = set()
        for agent_node in flow.agents:
            if agent_node.depends_on:
                nodes_with_outgoing.update(agent_node.depends_on)
        
        # Also check conditional routes
        for route in flow.conditional_routes:
            nodes_with_outgoing.add(route.from_agent)
        
        logger.debug(f"Nodes with outgoing edges: {nodes_with_outgoing}")
        
        # Any node not in nodes_with_outgoing is terminal
        terminal_nodes = []
        for agent_node in flow.agents:
            if agent_node.agent_id not in nodes_with_outgoing:
                # Check if it already has a conditional edge
                has_conditional = any(
                    route.from_agent == agent_node.agent_id 
                    for route in flow.conditional_routes
                )
                if not has_conditional:
                    terminal_nodes.append(agent_node.agent_id)
                    logger.debug(f"  Adding terminal edge: {agent_node.agent_id} -> END")
                    workflow.add_edge(agent_node.agent_id, END)
        
        logger.info(f"Graph construction complete. Terminal nodes: {terminal_nodes}")
        return workflow.compile()
    
    async def plan_and_build(
        self,
        intent: str,
        available_agents: list[AgentRecord],
        params: dict[str, Any] | None = None,
    ) -> tuple[AgentFlow | None, StateGraph | None]:
        """
        Discover the flow and build the executable graph in one step.
        
        Returns:
            Tuple of (AgentFlow, compiled StateGraph) or (None, None) if failed
        """
        logger.info(f"Planning and building flow with available_agents: {len(available_agents)}")
        flow = await self.discover_flow(intent, available_agents, params)
        if not flow:
            return None, None
        
        # Build registry mapping
        agent_registry = {agent.agent_id: agent for agent in available_agents}
        
        try:
            graph = self.build_langgraph(flow, agent_registry)
            return flow, graph
        except Exception as e:
            logger.error(f"Failed to build graph from flow: {e}")
            return flow, None
