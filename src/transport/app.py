"""
Semantic Bus Application

FastAPI application with WebSocket endpoint for the Semantic Bus.
This is the main entry point for running the bus.

Storage is configured via environment variables:
- LIP_STORAGE_BACKEND: "memory", "sqlite", "postgresql", "mysql"
- LIP_DATABASE_URL: SQLAlchemy async connection URL
- LIP_REDIS_URL: Redis URL for distributed presence/exec

Semantic matching (LangChain 1.2.0) is configured via:
- LIP_LLM_MODEL: Model identifier (default: "gpt-4o-mini")
  - OpenAI: "gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"
  - Azure OpenAI: "azure/gpt-4o-mini", "azure/gpt-4o"
  - Anthropic: "claude-sonnet-4-5-20250929", "claude-3-5-sonnet-20241022"
  - Google Gemini: "gemini/gemini-2.0-flash-exp", "gemini/gemini-1.5-pro"
  - Ollama: "ollama/llama3.2", "ollama/mistral"

Provider-specific API keys and configuration:
- OPENAI_API_KEY: Required for OpenAI models
- AZURE_OPENAI_API_KEY: Required for Azure OpenAI models
- AZURE_OPENAI_ENDPOINT: Azure OpenAI endpoint URL
- ANTHROPIC_API_KEY: Required for Anthropic models
- GOOGLE_API_KEY: Required for Google Gemini models
- (Ollama runs locally, no API key needed)

Environment variables can be loaded from a .env file in the project root.
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket

# Load environment variables from .env file
load_dotenv()

from src.registry import AgentRegistry
from src.routing import IntentRouter
from src.session import SessionManager
from src.schema import ViewRegistry
from src.policy import PolicyEngine
from src.trace import SemanticTraceStore
from src.transport.queue import ConnectionQueueManager
from src.transport.handler import WebSocketHandler
from src.storage import (
    StorageBundle,
    create_storage_from_env,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global instances (created at startup)
storage: StorageBundle | None = None
registry: AgentRegistry | None = None
router: IntentRouter | None = None
session_manager: SessionManager | None = None
view_registry: ViewRegistry | None = None
policy_engine: PolicyEngine | None = None
trace_store: SemanticTraceStore | None = None
queue_manager: ConnectionQueueManager | None = None
handler: WebSocketHandler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    
    Initializes and tears down all bus components.
    """
    global storage, registry, router, session_manager, view_registry, policy_engine
    global trace_store, queue_manager, handler
    
    # Startup
    logger.info("Starting Semantic Bus...")
    
    # Create storage from environment
    storage = await create_storage_from_env()
    logger.info(f"Storage initialized: {type(storage.sessions).__name__}")
    
    # Initialize ViewRegistry first (needed by AgentRegistry)
    view_registry = ViewRegistry()
    
    # Initialize AgentRegistry with ViewRegistry
    registry = AgentRegistry(
        heartbeat_timeout_seconds=30.0,
        view_registry=view_registry
    )
    await registry.start()
    
    session_manager = SessionManager(
        negotiation_ttl_seconds=30,
        session_ttl_seconds=300,
        storage=storage.sessions,
    )
    await session_manager.start()
    
    # Configure router with semantic matching
    llm_model = os.getenv("LIP_LLM_MODEL", "gpt-4o-mini")
    
    router = IntentRouter(
        registry,
        max_candidates=3,
        llm_model=llm_model,
    )
    
    logger.info(f"Semantic matching enabled with model: {llm_model}")
    
    policy_engine = PolicyEngine()
    trace_store = SemanticTraceStore(
        max_events_per_session=10000,
        storage=storage.trace,
    )
    queue_manager = ConnectionQueueManager(max_queue_size=200)
    
    handler = WebSocketHandler(
        registry=registry,
        router=router,
        session_manager=session_manager,
        view_registry=view_registry,
        policy_engine=policy_engine,
        trace_store=trace_store,
        queue_manager=queue_manager,
        exec_store=storage.execs,
    )
    
    logger.info("Semantic Bus started")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Semantic Bus...")
    await queue_manager.shutdown()
    await session_manager.stop()
    await registry.stop()
    await storage.close()
    logger.info("Semantic Bus stopped")


app = FastAPI(
    title="Semantic Bus",
    description="Liquid Interface Protocol (LIP) implementation for agent-based systems",
    version="0.1.0",
    lifespan=lifespan
)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for agent communication.
    
    All agent messages flow through this endpoint.
    The first message must be agent.register.
    """
    if handler is None:
        await websocket.close(code=1011, reason="Bus not initialized")
        return
    
    await handler.handle_connection(websocket)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "agents": registry.agent_count if registry else 0,
        "online": registry.online_count if registry else 0,
        "sessions": session_manager.session_count if session_manager else 0,
        "active_sessions": session_manager.active_count if session_manager else 0,
        "trace_events": trace_store.total_events() if trace_store else 0,
        "connection_queues": queue_manager.connection_count() if queue_manager else 0
    }
