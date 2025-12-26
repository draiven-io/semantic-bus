# Intent Routing
# Semantic matching and intent-based message routing
# Routes based on capabilities, not fixed endpoints
#
# Features:
# - LLM-based semantic matching for natural language capabilities
# - Embedding similarity for fast matching
# - Deterministic fallback for reliability
# - Queued routing for horizontal scaling

from src.routing.router import IntentRouter
from src.routing.semantic_matcher import (
    SemanticMatcher,
    MatchResult,
)
from src.routing.queued_router import (
    QueuedRouter,
    create_queued_router,
)

__all__ = [
    # Core router
    "IntentRouter",
    # Semantic matching
    "SemanticMatcher",
    "MatchResult",
    # Queued router for scaling
    "QueuedRouter",
    "create_queued_router",
]
