"""
Multi-Agent Orchestration Module

Uses LangGraph to discover and execute agent workflows.

The simplified orchestration flow:
1. Intent → Router discovers flow via LLM
2. LangGraph executes the agent workflow
3. State flows through agents, collecting results
4. Final aggregated response returned

Key components:
- AgentFlowPlanner: Uses LLM to discover which agents are needed and in what order
- AgentFlow: Structured description of the workflow
- AgentFlowState: TypedDict state that flows through the LangGraph
"""

from src.orchestration.flow_planner import (
    AgentFlowPlanner,
    AgentFlow,
    AgentNode,
    ConditionalRoute,
    AgentFlowState,
)

__all__ = [
    # Flow Planner (LangGraph-based)
    "AgentFlowPlanner",
    "AgentFlow",
    "AgentNode",
    "ConditionalRoute",
    "AgentFlowState",
]
