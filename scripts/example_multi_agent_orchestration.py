#!/usr/bin/env python3
"""
Multi-Agent Orchestration Example (LangGraph-based)

Demonstrates the simplified multi-agent orchestration using AgentFlowPlanner.

Workflow:
1. User broadcasts an intent: "Translate this Spanish article and summarize it"
2. AgentFlowPlanner uses LLM to discover the workflow
3. LangGraph executes the agent workflow
4. State flows through agents, collecting results
5. Final aggregated response returned

Usage:
    python scripts/example_multi_agent_orchestration.py
"""

import asyncio
import logging
from uuid import uuid4
from typing import Any

from src.orchestration import AgentFlowPlanner, AgentFlow, AgentNode, AgentFlowState

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# =============================================================================
# Mock Agent Records
# =============================================================================

class MockAgentRecord:
    """Simulated agent record."""
    def __init__(
        self,
        agent_id: str,
        name: str,
        capabilities: list[str],
        description: str,
        tags: list[str] | None = None,
    ):
        self.agent_id = agent_id
        self.name = name
        self.conn_id = f"ws_{agent_id[:8]}"
        self.tenant_id = "example-tenant"
        self.capabilities = capabilities
        self.description = description
        self.tags = tags or []


# Example agents
TRANSLATOR = MockAgentRecord(
    agent_id="translator-001",
    name="TranslatorAgent",
    capabilities=["translate", "language-detection"],
    description="Translates text between languages",
    tags=["nlp", "language"],
)

SUMMARIZER = MockAgentRecord(
    agent_id="summarizer-001",
    name="SummarizerAgent",
    capabilities=["summarize", "extract-key-points"],
    description="Creates concise summaries from long documents",
    tags=["nlp", "summarization"],
)


# =============================================================================
# Example 1: Manually Created Flow (no LLM needed)
# =============================================================================

async def example_manual_flow():
    """
    Example of creating a flow manually without LLM discovery.
    
    This is useful when you know exactly what workflow you need.
    """
    logger.info("=" * 60)
    logger.info("Example 1: Manual Flow Creation")
    logger.info("=" * 60)
    
    planner = AgentFlowPlanner()
    
    # Create flow manually
    flow = AgentFlow(
        intent_summary="Translate Spanish text and then summarize it",
        agents=[
            AgentNode(
                agent_id="translator-001",
                capability="translate",
                outputs=["translated_text"],
                description="Translates the Spanish text to English",
            ),
            AgentNode(
                agent_id="summarizer-001",
                capability="summarize",
                depends_on=["translator-001"],
                required_inputs=["translated_text"],
                description="Summarizes the translated text",
            ),
        ],
        entry_point="translator-001",
        reasoning="Translation must happen before summarization",
        confidence=1.0,  # Manual flow = 100% confidence
    )
    
    logger.info(f"Flow summary: {flow.intent_summary}")
    logger.info(f"Agents: {[a.agent_id for a in flow.agents]}")
    logger.info(f"Entry point: {flow.entry_point}")
    logger.info(f"Dependencies: {flow.agents[1].agent_id} depends on {flow.agents[1].depends_on}")
    
    # Build the LangGraph
    agent_registry = {
        TRANSLATOR.agent_id: TRANSLATOR,
        SUMMARIZER.agent_id: SUMMARIZER,
    }
    
    graph = planner.build_langgraph(flow, agent_registry)
    logger.info(f"Built LangGraph: {graph}")
    
    # Create initial state
    initial_state: AgentFlowState = {
        "intent": "Translate this Spanish article and summarize it",
        "params": {"source_language": "es", "target_language": "en"},
        "tenant_id": "example-tenant",
        "requester_id": "user-123",
        "current_step": 0,
        "completed_agents": [],
        "agent_outputs": {},
        "next_agent": None,
        "should_continue": True,
        "errors": [],
    }
    
    logger.info(f"Initial state ready. Would execute graph with: {initial_state['intent']}")
    
    # Note: To actually execute, you would run:
    # result = await graph.ainvoke(initial_state)
    # But this requires the graph nodes to be connected to real agent execution
    
    return flow, graph


# =============================================================================
# Example 2: LLM-Discovered Flow
# =============================================================================

async def example_llm_discovery():
    """
    Example of using LLM to discover the workflow.
    
    This is the recommended approach - let the LLM figure out the best flow.
    
    NOTE: Requires OPENAI_API_KEY environment variable.
    """
    logger.info("=" * 60)
    logger.info("Example 2: LLM Flow Discovery")
    logger.info("=" * 60)
    
    planner = AgentFlowPlanner(
        model="gpt-4o-mini",  # Cost-effective model for flow planning
        temperature=0.0,     # Deterministic output
    )
    
    available_agents = [TRANSLATOR, SUMMARIZER]
    
    intent = "Translate this Spanish news article to English and give me a brief summary"
    
    logger.info(f"Intent: {intent}")
    logger.info(f"Available agents: {[a.name for a in available_agents]}")
    
    try:
        flow = await planner.discover_flow(
            intent=intent,
            available_agents=available_agents,
            params={"text": "El artículo en español sobre tecnología..."},
        )
        
        if flow:
            logger.info(f"Discovered flow: {flow.intent_summary}")
            logger.info(f"Agents in flow: {[a.agent_id for a in flow.agents]}")
            logger.info(f"Reasoning: {flow.reasoning}")
            logger.info(f"Confidence: {flow.confidence}")
            
            # Build executable graph
            agent_registry = {a.agent_id: a for a in available_agents}
            graph = planner.build_langgraph(flow, agent_registry)
            logger.info("LangGraph built successfully!")
            
            return flow, graph
        else:
            logger.warning("Could not discover flow - check API key")
            return None, None
            
    except Exception as e:
        logger.error(f"LLM discovery failed: {e}")
        logger.info("Falling back to manual flow...")
        return await example_manual_flow()


# =============================================================================
# Example 3: One-Step Plan and Build
# =============================================================================

async def example_plan_and_build():
    """
    Example of using plan_and_build() for a single-step workflow creation.
    """
    logger.info("=" * 60)
    logger.info("Example 3: Plan and Build (One Step)")
    logger.info("=" * 60)
    
    planner = AgentFlowPlanner()
    
    available_agents = [TRANSLATOR, SUMMARIZER]
    
    try:
        flow, graph = await planner.plan_and_build(
            intent="Translate from Spanish and summarize the result",
            available_agents=available_agents,
        )
        
        if flow and graph:
            logger.info(f"Created flow with {len(flow.agents)} agents")
            logger.info(f"Graph ready to execute")
            return flow, graph
        else:
            logger.warning("Could not plan and build - using manual flow")
            return await example_manual_flow()
            
    except Exception as e:
        logger.error(f"Plan and build failed: {e}")
        return await example_manual_flow()


# =============================================================================
# Main
# =============================================================================

async def main():
    """Run all examples."""
    logger.info("Multi-Agent Orchestration Examples")
    logger.info("=" * 60)
    
    # Example 1: Manual flow (always works)
    await example_manual_flow()
    
    print()
    
    # Example 2: LLM discovery (requires API key)
    # Uncomment to test with real LLM:
    # await example_llm_discovery()
    
    # Example 3: One-step plan and build
    # await example_plan_and_build()
    
    logger.info("")
    logger.info("Examples complete!")
    logger.info("")
    logger.info("The new flow:")
    logger.info("  1. Intent arrives")
    logger.info("  2. AgentFlowPlanner discovers workflow (via LLM or manual)")
    logger.info("  3. LangGraph executes the agent workflow")
    logger.info("  4. State flows through, collecting results")
    logger.info("  5. Final result returned")


if __name__ == "__main__":
    asyncio.run(main())
