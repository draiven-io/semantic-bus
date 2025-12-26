"""
View Registry

Manages ephemeral response schemas registered by agents.
Schemas are tied to agent lifecycle and their capabilities.

Why Capability-Based Schemas?
- Each capability defines its own response structure
- When an agent registers a capability, it provides the schema for that capability's response
- Impossible for bus to know all possible schemas upfront
- Schemas are ephemeral - lifecycle tied to agent connection
- Validation happens using the capability's declared schema

Schema Format:
- capability (e.g., "translate") maps to JSON Schema defining the response structure
- Agents provide schemas during registration, one per capability
- Bus validates exec.result payloads against the provider's registered schema for that capability
"""

from typing import Any
import logging
from uuid import UUID

logger = logging.getLogger(__name__)


class ViewRegistry:
    """
    Registry for agent-provided response schemas.
    
    Schemas are stored per agent and capability, removed when agents unregister.
    This makes the registry ephemeral and agent-lifecycle-aware.
    """
    
    def __init__(self):
        # Map: agent_id -> (capability -> schema_definition)
        self._schemas_by_agent: dict[UUID, dict[str, dict]] = {}
        
        # Reverse index: capability -> set of agent_ids providing this capability
        self._agents_by_capability: dict[str, set[UUID]] = {}
    
    def register_agent_schemas(
        self,
        agent_id: UUID,
        schemas: dict[str, dict]
    ) -> None:
        """
        Register schemas for an agent's capabilities.
        
        Args:
            agent_id: The agent registering schemas
            schemas: Dictionary mapping capability to schema definition
        """
        if agent_id in self._schemas_by_agent:
            # Agent re-registering - clean up old schemas first
            self.unregister_agent_schemas(agent_id)
        
        self._schemas_by_agent[agent_id] = schemas
        
        # Update reverse index
        for capability in schemas.keys():
            if capability not in self._agents_by_capability:
                self._agents_by_capability[capability] = set()
            self._agents_by_capability[capability].add(agent_id)
        
        logger.info(
            f"Registered {len(schemas)} schema(s) for agent {agent_id}: "
            f"{list(schemas.keys())}"
        )
    
    def unregister_agent_schemas(self, agent_id: UUID) -> None:
        """
        Remove all schemas for an agent.
        
        Args:
            agent_id: The agent whose schemas should be removed
        """
        schemas = self._schemas_by_agent.pop(agent_id, None)
        if schemas:
            # Clean up reverse index
            for capability in schemas.keys():
                if capability in self._agents_by_capability:
                    self._agents_by_capability[capability].discard(agent_id)
                    if not self._agents_by_capability[capability]:
                        del self._agents_by_capability[capability]
            
            logger.info(
                f"Unregistered {len(schemas)} schema(s) for agent {agent_id}"
            )
    
    def get_schema(
        self,
        agent_id: UUID,
        capability: str
    ) -> dict | None:
        """
        Get the schema for a specific capability of an agent.
        
        Args:
            agent_id: The agent who registered the schema
            capability: The capability identifier
        
        Returns:
            Schema definition, or None if not found
        """
        agent_schemas = self._schemas_by_agent.get(agent_id)
        if agent_schemas:
            return agent_schemas.get(capability)
        return None
    
    def validate(
        self,
        agent_id: UUID,
        capability: str,
        payload: dict[str, Any]
    ) -> tuple[bool, str | None]:
        """
        Validate a payload against an agent's registered schema for a capability.
        
        Args:
            agent_id: The agent who owns the schema
            capability: The capability to validate against
            payload: The payload to validate
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        schema_def = self.get_schema(agent_id, capability)
        
        if schema_def is None:
            # No schema registered for this agent/capability - log warning but allow
            logger.warning(
                f"No schema registered for agent {agent_id}, capability {capability}. "
                f"Skipping validation."
            )
            return True, None
        
        try:
            # Try to validate using JSON Schema format
            # For now, we'll do basic validation - in production, you might want
            # to use jsonschema library for proper validation
            
            if "properties" in schema_def:
                # JSON Schema format
                required_fields = schema_def.get("required", [])
                for field in required_fields:
                    if field not in payload:
                        return False, f"Missing required field: {field}"
            
            return True, None
            
        except Exception as e:
            error_msg = f"Validation error: {str(e)}"
            logger.warning(f"Schema validation failed for {capability}: {error_msg}")
            return False, error_msg
    
    def list_agent_schemas(self, agent_id: UUID) -> list[str]:
        """
        List all capabilities with registered schemas for an agent.
        
        Args:
            agent_id: The agent to query
        
        Returns:
            List of capability strings
        """
        agent_schemas = self._schemas_by_agent.get(agent_id, {})
        return list(agent_schemas.keys())
    
    def list_all_capabilities(self) -> list[dict[str, Any]]:
        """
        List all registered capabilities across all agents.
        
        Returns:
            List of dicts with capability and provider agent_ids
        """
        return [
            {
                "capability": capability,
                "providers": [str(agent_id) for agent_id in agent_ids]
            }
            for capability, agent_ids in self._agents_by_capability.items()
        ]
    
    @property
    def schema_count(self) -> int:
        """Total number of unique schemas registered."""
        return sum(len(schemas) for schemas in self._schemas_by_agent.values())
    
    @property
    def agent_count(self) -> int:
        """Number of agents with registered schemas."""
        return len(self._schemas_by_agent)
