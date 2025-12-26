"""
Semantic Matcher for Intent-Capability Matching

Uses LangChain 1.2.0 for intelligent natural-language matching of intents to agent capabilities.
Supports multiple LLM providers including Azure OpenAI, OpenAI, Anthropic, Google Gemini, and Ollama.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from src.llm import create_llm
from src.registry import AgentRecord

logger = logging.getLogger(__name__)


# -----------------------
# Pydantic Models for Structured Output
# -----------------------


class MatchResult(BaseModel):
    """
    Structured result from LLM indicating how well an agent matches an intent.
    """

    agent_id: str = Field(description="The ID of the agent being evaluated")
    score: float = Field(
        description="Match score from 0.0 to 1.0, where 1.0 is a perfect match",
        ge=0.0,
        le=1.0,
    )
    matched_capability: str = Field(
        description="The specific capability that matches the intent"
    )
    reasoning: str = Field(
        description="Brief explanation of why this agent matches (or doesn't match) the intent"
    )
    confidence: float = Field(
        description="Confidence in this assessment from 0.0 to 1.0",
        ge=0.0,
        le=1.0,
    )


class MatchResultList(BaseModel):
    """
    Wrapper for a list of match results to support structured output.
    """

    results: list[MatchResult] = Field(
        description="List of match results for each agent"
    )


# -----------------------
# LangChain 1.2.0 Implementation
# -----------------------


class SemanticMatcher:
    """
    Uses LangChain 1.2.0 agents with structured output for semantic matching.
    Supports any model via LangChain's unified interface.

    Model format examples:
    - OpenAI: "gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo..."
    - Azure OpenAI: "azure/gpt-4o-mini", "azure/gpt-4o..."
    - Anthropic: "claude-sonnet-4-5-20250929", "claude-3-5-sonnet-20241022..."
    - Google Gemini: "gemini/gemini-2.0-flash-exp", "gemini/gemini-1.5-pro..."
    - Ollama: "ollama/llama3.2", "ollama/mistral..."
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.0,
        cache_ttl_seconds: int = 300,
        max_retries: int = 2,
    ):
        """
        Initialize the LangChain semantic matcher.

        Args:
            model: Model identifier (e.g., "gpt-4o-mini", "claude-sonnet-4-5-20250929", "ollama/llama3.2")
            temperature: LLM temperature (0.0 for deterministic)
            cache_ttl_seconds: How long to cache results
            max_retries: Max retries on LLM errors
        """
        self._model = model
        self._temperature = temperature
        self._cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self._max_retries = max_retries

        # Lazy initialization
        self._agent = None
        self._init_lock = asyncio.Lock()

        # Simple cache: (intent, agent_ids_hash) -> (results, timestamp)
        self._cache: dict[str, tuple[list[MatchResult], datetime]] = {}

    async def _init_agent(self) -> bool:
        """
        Lazy initialization of LLM with structured output support.
        Returns True if successful, False otherwise.
        """
        if self._agent is not None:
            return True

        async with self._init_lock:
            if self._agent is not None:
                return True

            try:
                # Create LLM using the factory
                llm = create_llm(
                    model=self._model,
                    temperature=self._temperature,
                    max_retries=self._max_retries,
                )
                
                # Create structured output agent
                self._agent = llm.with_structured_output(MatchResultList).with_retry(stop_after_attempt=self._max_retries)

                logger.info(f"Initialized LLM with model: {self._model}")
                return True

            except (ImportError, ValueError) as e:
                logger.error(f"Failed to initialize LLM: {e}")
                return False
            except Exception as e:
                logger.error(f"Unexpected error initializing LLM: {e}")
                return False

    def _build_user_message(self, intent: str, agents: list[AgentRecord]) -> str:
        """
        Build the user message describing the intent and available agents.
        """
        # Build agent descriptions
        agent_descriptions = []
        for agent in agents:
            caps = ", ".join(agent.capabilities) if agent.capabilities else "none listed"
            agent_descriptions.append(
                f"Agent '{agent.agent_id}':\n"
                f"  Description: {agent.description or 'N/A'}\n"
                f"  Capabilities: {caps}"
            )

        agents_text = "\n\n".join(agent_descriptions)

        return (
            f"Intent: {intent}\n\n"
            f"Available Agents:\n{agents_text}\n\n"
            f"Evaluate each agent's match to the intent."
        )

    async def _call_agent(
        self, intent: str, agents: list[AgentRecord]
    ) -> list[MatchResult]:
        """
        Call the LLM with structured output and retry logic.
        """
        if not await self._init_agent():
            raise RuntimeError("Failed to initialize LLM")

        # Build the evaluation prompt
        system_message = (
            "You are an expert at matching user intents to agent capabilities. "
            "Given a user's intent and a list of agents with their capabilities, "
            "evaluate each agent and provide a structured assessment.\n\n"
            "For each agent, provide:\n"
            "- agent_id: The agent's ID\n"
            "- score: 0.0 to 1.0, where 1.0 is perfect match\n"
            "- matched_capability: The specific capability that matches\n"
            "- reasoning: Brief explanation of the match quality\n"
            "- confidence: 0.0 to 1.0, your confidence in this assessment\n\n"
            "Return assessments for ALL agents provided."
        )
        
        user_message = self._build_user_message(intent, agents)

        try:
            # Build messages
            messages = [
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message}
            ]

            # Invoke with structured output
            structured_response = await asyncio.to_thread(
                self._agent.invoke, messages
            )

            if not structured_response or not isinstance(structured_response, MatchResultList):
                logger.warning(
                    f"LLM returned unexpected format: {type(structured_response)}"
                )
                return []

            # Extract results from wrapper
            results_list = structured_response.results

            # Filter results to match provided agents
            agent_ids = {str(agent.agent_id) for agent in agents}
            valid_results = [
                r for r in results_list if str(r.agent_id) in agent_ids
            ]

            return valid_results

        except Exception as e:
            raise e

    def _get_cache_key(self, intent: str, agents: list[AgentRecord]) -> str:
        """
        Generate a cache key from intent and agent IDs.
        """
        agent_ids = tuple(sorted(agent.agent_id for agent in agents))
        return f"{intent}:{hash(agent_ids)}"

    def _get_cached_results(
        self, intent: str, agents: list[AgentRecord]
    ) -> list[MatchResult] | None:
        """
        Get cached results if available and not expired.
        """
        key = self._get_cache_key(intent, agents)
        if key in self._cache:
            results, timestamp = self._cache[key]
            if datetime.now() - timestamp < self._cache_ttl:
                logger.debug(f"Cache hit for intent: {intent[:50]}...")
                return results
            else:
                del self._cache[key]
        return None

    def _cache_results(
        self, intent: str, agents: list[AgentRecord], results: list[MatchResult]
    ) -> None:
        """
        Cache results with timestamp.
        """
        key = self._get_cache_key(intent, agents)
        self._cache[key] = (results, datetime.now())

    async def rank_agents(
        self,
        intent: str,
        agents: list[AgentRecord],
        top_k: int = 3,
        score_threshold: float = 0.5,
    ) -> list[AgentRecord]:
        """
        Rank agents using LLM-based semantic matching.

        Args:
            intent: The natural-language intent from the user
            agents: List of available agents with their capabilities
            top_k: Maximum number of top matches to return
            score_threshold: Minimum score (0.0-1.0) for agents to be considered (default: 0.5)

        Returns:
            List of agents, sorted by score descending, filtered by score_threshold
        """
        if not agents:
            return []

        # Check cache
        cached = self._get_cached_results(intent, agents)
        if cached is not None:
            results = cached
        else:
            # Call agent
            try:
                results = await self._call_agent(intent, agents)
                self._cache_results(intent, agents, results)
            except Exception as e:
                logger.error(f"Failed to get agent rankings: {e}")
                return []

        # Build agent lookup
        agent_map = {str(agent.agent_id): agent for agent in agents}

        # Convert to (agent, score) tuples
        ranked = []
        for result in results:
            if result.agent_id in agent_map:
                agent_map[result.agent_id].metadata["_match_score"] = result.score
                # Only include agents that meet the score threshold
                if result.score >= score_threshold:
                    ranked.append(agent_map[result.agent_id])

        # Sort by score descending
        ranked.sort(key=lambda x: x.metadata["_match_score"], reverse=True)

        # Return top_k
        return ranked[:top_k]
