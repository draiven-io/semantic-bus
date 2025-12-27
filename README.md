<p align="center">
  <img src="sematicbus.png" alt="Semantic Bus" width="400">
  <h1 align="center">Semantic Bus</h1>
  <p align="center">
    <strong><a href="https://github.com/draiven-io/liquid-interfaces">Liquid Interface Protocol (LIP)</a> Implementation for Agent-Based Systems</strong>
  </p>
  <p align="center">
    Intent-based negotiation • Ephemeral interfaces • IBAC governance
  </p>
</p>

<p align="center">
  <a href="#installation"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License"></a>
  <a href="#contributing"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg" alt="PRs Welcome"></a>
</p>

---

## What is Semantic Bus?

Semantic Bus is a **centralized message bus** that implements the **Liquid Interface Protocol (LIP)** — a paradigm shift in how autonomous agents communicate. Instead of rigid, pre-defined APIs, agents negotiate ephemeral interfaces at runtime based on *intent*.

### The Problem with Traditional Agent Communication

Traditional approaches force agents into one of two extremes:

| Approach | Problem |
|----------|---------|
| **Static APIs** | Brittle contracts that break when either party evolves |
| **Unstructured messaging** | No guarantees, validation chaos, debugging nightmares |

### The LIP Solution

LIP introduces a third way: **negotiated, ephemeral interfaces** that:

- ✅ Are created *on-demand* based on intent
- ✅ Have explicit lifetimes (they dissolve)
- ✅ Support graceful renegotiation on failure
- ✅ Enforce structure *after* agreement, not before

---

## Core Concepts

### 🎯 Intent-First Communication

Agents don't call endpoints — they **declare intent**:

```
"Translate this text to Spanish and summarize it"
```

The bus uses **LLM-powered flow planning** to discover which agents can fulfill the intent — whether that's one agent or many working together.

### 🔄 Unified Flow Lifecycle

All intents follow the same flow, whether they need 1 agent or N agents:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   INTENT    │────▶│   DISCOVER  │────▶│  NEGOTIATE  │────▶│   EXECUTE   │
│  Broadcast  │     │  (LLM Flow) │     │   Offers    │     │    Flow     │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
       │                   │                   │                   │
       ▼                   ▼                   ▼                   ▼
   "I need X"       LangGraph finds       Aggregated offer    Sequential agent
                    1-N capable agents    sent to requester    execution with
                    in optimal order                           progress updates
```

### 🤖 Multi-Agent Flows

The bus automatically plans and executes multi-agent workflows:

```python
# Example: "Translate and summarize this text"
# The bus discovers: translator → summarizer

# Requester receives aggregated offer:
{
    "offer_id": "aggregated-...",
    "provider_id": "bus",
    "metadata": {
        "is_multi_agent_flow": True,
        "flow_agents": ["translator-agent", "summarizer-agent"],
        "flow_reasoning": "Intent requires translation then summarization"
    }
}

# After agreement, the bus executes agents sequentially:
# 1. translator-agent receives: original text
# 2. summarizer-agent receives: translated text + previous result
# 3. Requester receives: merged results from all agents
```

### ⏳ Ephemeral Interfaces

Sessions have explicit TTLs. They are born, serve their purpose, and dissolve:

```python
# Session created with 5-minute lifetime
session = Session(
    session_ttl_seconds=300,
    negotiation_ttl_seconds=30
)
# After 5 minutes: session dissolves automatically
```

### 🛡️ IBAC (Intent-Based Access Control)

Access control based on *what you're trying to do*, not just *who you are*:

```python
policy = PolicyRule(
    intent_pattern="data.*",
    allowed_roles=["data-reader", "admin"],
    max_payload_size=1_000_000
)
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        SEMANTIC BUS                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │   Registry   │  │    Flow      │  │   Session    │          │
│  │   (Agents)   │  │  (Planner)   │  │  (Lifecycle) │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│         │                 │                  │                  │
│         ▼                 ▼                  ▼                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │   Router     │  │   Policy     │  │    Trace     │          │
│  │  (Semantic)  │  │   (IBAC)     │  │  (Observe)   │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────────┤
│  │                   FLOW EXECUTION LAYER                      │
│  │   LangGraph planning • Sequential agent execution           │
│  │   Progress updates • Result aggregation                     │
│  └─────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────────┤
│  │                    TASK QUEUE LAYER                         │
│  │   In-Memory │ Redis Streams │ Kafka │ RabbitMQ             │
│  └─────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────────┤
│  │                    STORAGE LAYER                            │
│  │   In-Memory │ SQLite │ PostgreSQL │ MySQL │ Redis          │
│  └─────────────────────────────────────────────────────────────┤
│                                                                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │ WebSocket
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
        ┌───────┐       ┌───────┐       ┌───────┐
        │Agent A│       │Agent B│       │Agent C│
        └───────┘       └───────┘       └───────┘
```

---

## Installation

### Basic Installation

```bash
# Clone the repository
git clone https://github.com/draiven-io/semantic-bus.git
cd semantic-bus

# Install with pip (development mode)
pip install -e ".[dev]"
```

### With Storage Backends

```bash
# SQLite (lightweight, single-node)
pip install -e ".[sqlite]"

# PostgreSQL (production, distributed)
pip install -e ".[postgresql]"

# All storage backends + Redis
pip install -e ".[storage]"
```

### With Task Queue Backends

```bash
# Redis Streams (recommended for most production)
pip install -e ".[redis]"

# Apache Kafka (high-throughput streaming)
pip install -e ".[kafka]"

# RabbitMQ (reliable messaging)
pip install -e ".[rabbitmq]"

# All queue backends
pip install -e ".[queues]"
```

### Full Installation

```bash
# Everything: dev tools, storage, queues, all LLM providers
pip install -e ".[all]"
```

### Requirements

- Python 3.11+
- FastAPI + WebSockets
- Pydantic v2

---

## Docker

### Build the Image

```bash
# Build production image with default extras (postgresql, redis, langchain)
docker build -t semantic-bus .

# Build with specific optional dependencies
docker build --build-arg INSTALL_EXTRAS="postgresql,redis,langchain-azure" -t semantic-bus .

# Build with all dependencies
docker build --build-arg INSTALL_EXTRAS="all" -t semantic-bus .

# Build development image with hot-reload
docker build --target development -t semantic-bus:dev .
```

### Run the Container

```bash
# Run production container
docker run -p 8000:8000 \
  -e LIP_LLM_PROVIDER=azure \
  -e LIP_AZURE_OPENAI_API_KEY=your-key \
  -e LIP_AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com \
  semantic-bus

# Run with environment file
docker run -p 8000:8000 --env-file .env semantic-bus

# Run development container with volume mount for hot-reload
docker run -p 8000:8000 -v $(pwd):/app semantic-bus:dev
```

### Docker Compose (Example)

```yaml
version: '3.8'

services:
  semantic-bus:
    build:
      context: .
      args:
        INSTALL_EXTRAS: "postgresql,redis,langchain-azure"
    ports:
      - "8000:8000"
    environment:
      - LIP_DATABASE_URL=postgresql+asyncpg://postgres:postgres@db/semantic_bus
      - LIP_REDIS_URL=redis://redis:6379
      - LIP_LLM_PROVIDER=azure
    depends_on:
      - db
      - redis

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: semantic_bus
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data

volumes:
  postgres_data:
  redis_data:
```

### Available Build Extras

| Extra | Description |
|-------|-------------|
| `dev` | Development tools (pytest, ruff, mypy) |
| `sqlite` | SQLite storage backend |
| `postgresql` | PostgreSQL storage backend |
| `mysql` | MySQL storage backend |
| `redis` | Redis for distributed presence/queues |
| `kafka` | Kafka for high-throughput streaming |
| `rabbitmq` | RabbitMQ for reliable messaging |
| `queues` | All queue backends (redis, kafka, rabbitmq) |
| `langchain` | Base LangChain for semantic matching |
| `langchain-azure` | LangChain with Azure OpenAI |
| `langchain-anthropic` | LangChain with Anthropic |
| `langchain-google` | LangChain with Google Gemini |
| `langchain-ollama` | LangChain with local Ollama models |
| `langchain-all` | All LLM providers |
| `storage` | All storage backends |
| `all` | Everything |

---

## Quick Start

### 1. Configure Environment

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your configuration
# At minimum, set your LLM provider and API key
nano .env
```

### 2. Start the Bus

```bash
# In-memory mode (development) - reads .env automatically
uvicorn src.transport.app:app --reload

# With PostgreSQL (configure in .env or override with environment variables)
export LIP_DATABASE_URL="postgresql+asyncpg://user:pass@localhost/lip"
uvicorn src.transport.app:app
```

**Note:** The application automatically loads environment variables from `.env` file if present. You can also override them by exporting environment variables directly.

### 3. Connect an Agent

```python
import asyncio
import websockets
import json
from uuid import uuid4

async def agent():
    async with websockets.connect("ws://localhost:8000/ws") as ws:
        # Register
        await ws.send(json.dumps({
            "message_id": str(uuid4()),
            "message_type": "agent.register",
            "sender_id": str(uuid4()),
            "tenant_id": "my-tenant",
            "payload": {
                "capabilities": ["weather.query", "weather.forecast"],
                "tags": ["weather", "brazil"]
            }
        }))
        
        response = await ws.recv()
        print("Registered:", json.loads(response))

asyncio.run(agent())
```

### 4. Broadcast an Intent

```python
# Requester broadcasts intent
await ws.send(json.dumps({
    "message_id": str(uuid4()),
    "message_type": "intent.broadcast",
    "sender_id": agent_id,
    "tenant_id": "my-tenant",
    "payload": {
        "intent": "weather.query",
        "parameters": {"city": "São Paulo"},
        "preferred_schemas": ["weather.v1", "weather.v2"]
    }
}))

# Receive offers from capable agents
offers = await ws.recv()
```

### 5. Agree & Execute

```python
# Accept an offer
await ws.send(json.dumps({
    "message_type": "intent.agree",
    "ephemeral_interface_id": session_id,
    "payload": {"offer_id": chosen_offer_id}
}))

# Execute within the session
await ws.send(json.dumps({
    "message_type": "exec.request",
    "ephemeral_interface_id": session_id,
    "payload": {"action": "get_current", "city": "São Paulo"}
}))

result = await ws.recv()
```

---

## Message Types

### Lifecycle Messages

| Type | Direction | Description |
|------|-----------|-------------|
| `agent.register` | Agent → Bus | Register agent with capabilities |
| `agent.registered` | Bus → Agent | Registration confirmed |
| `agent.heartbeat` | Agent → Bus | Keep-alive signal |
| `intent.broadcast` | Agent → Bus | Declare intent to the network |
| `intent.deliver` | Bus → Agents | Forward intent to capable agents |
| `intent.offer` | Agent → Bus | Offer to fulfill an intent |
| `intent.offers` | Bus → Agent | Collected offers for requester |
| `intent.agree` | Agent → Bus | Accept an offer |
| `session.created` | Bus → Agents | Ephemeral interface established |

### Execution Messages

| Type | Direction | Description |
|------|-----------|-------------|
| `exec.request` | Requester → Bus → Provider | Execute action in session |
| `exec.accepted` | Bus → Requester | Request acknowledged |
| `exec.result` | Provider → Bus → Requester | Successful result |
| `exec.error` | Bus → Agent | Execution error |
| `exec.stream.start` | Provider → Requester | Begin streaming response |
| `exec.stream.chunk` | Provider → Requester | Stream data chunk |
| `exec.stream.end` | Provider → Requester | End streaming |

---

## Semantic Matching with LangChain 1.2.0

Semantic Bus uses **LangChain 1.2.0** for intelligent intent-to-capability matching. Instead of rigid string matching, agents describe their capabilities in natural language, and an LLM determines the best matches.

### Why LLM-Based Matching?

- ✅ **Semantic Understanding**: "weather forecast" matches "meteorological predictions"
- ✅ **Context Awareness**: Considers intent parameters and agent descriptions
- ✅ **Flexible**: Works with any capability description format
- ✅ **Ranked Results**: Returns top N matches with confidence scores

### Configuration

```bash
# Choose your model from supported providers
export LIP_LLM_MODEL=gpt-4o-mini                      # OpenAI (default)
# or
export LIP_LLM_MODEL=azure/gpt-4o-mini                # Azure OpenAI
# or
export LIP_LLM_MODEL=claude-sonnet-4-5-20250929       # Anthropic
# or
export LIP_LLM_MODEL=gemini/gemini-2.0-flash-exp      # Google Gemini
# or
export LIP_LLM_MODEL=ollama/llama3.2                  # Ollama (local)

# Set API keys and configuration (if needed)
export OPENAI_API_KEY=sk-...                          # OpenAI
export AZURE_OPENAI_API_KEY=...                       # Azure OpenAI
export AZURE_OPENAI_ENDPOINT=https://....openai.azure.com/  # Azure OpenAI
export ANTHROPIC_API_KEY=sk-ant-...                   # Anthropic
export GOOGLE_API_KEY=...                             # Google Gemini
# Ollama runs locally, no API key needed
```

### Installation

```bash
# OpenAI (default)
pip install -e ".[langchain]"

# Azure OpenAI
pip install -e ".[langchain-azure]"

# Anthropic
pip install -e ".[langchain-anthropic]"

# Google Gemini
pip install -e ".[langchain-google]"

# Ollama (local models)
pip install -e ".[langchain-ollama]"
```

### How It Works

When an agent broadcasts an intent:

```python
{
    "intent": "I need current weather data for Brazilian cities",
    "parameters": {"country": "Brazil"}
}
```

The LLM evaluates each agent's capabilities:

```python
# Agent A capabilities
["weather data provider", "supports real-time forecasts"]

# Agent B capabilities
["meteorological information", "covers South America"]

# LLM matches:
# - Agent B: 0.95 (explicitly mentions meteorological + South America)
# - Agent A: 0.80 (weather data matches, but no regional specificity)
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LIP_LLM_MODEL` | `gpt-4o-mini` | Model identifier (see configuration examples above) |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `AZURE_OPENAI_API_KEY` | — | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | — | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_API_VERSION` | `2024-02-15-preview` | Azure OpenAI API version |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `GOOGLE_API_KEY` | — | Google Gemini API key |

---

## Multi-Agent Flow Execution

The Semantic Bus uses **LangGraph** to plan and execute multi-agent workflows. This is a unified approach — the same flow logic handles single-agent and multi-agent intents.

### How Flow Planning Works

1. **Intent Analysis**: When an intent is received, the LLM analyzes it against all registered agent capabilities
2. **Flow Discovery**: LangGraph determines which agents are needed and in what order
3. **Offer Aggregation**: The bus collects offers from all flow agents and sends an aggregated offer to the requester
4. **Sequential Execution**: After agreement, agents are executed in order, with each receiving the previous agent's output

### Example: Multi-Agent Translation + Summarization

```python
# Intent: "Translate this text to Spanish and summarize it"

# Flow Planning discovers:
# 1. translator-agent (can translate text)
# 2. summarizer-agent (can summarize text)

# Execution sequence:
# Step 1/2: translator-agent receives original text
#           → outputs: {"translated_text": "Hola mundo..."}

# Step 2/2: summarizer-agent receives translated text + previous results
#           → outputs: {"summary": "A greeting..."}

# Final result to requester (merged):
{
    "result": {
        "translated_text": "Hola mundo...",
        "summary": "A greeting..."
    },
    "aggregated": True,
    "flow_metadata": {
        "steps": 2,
        "agent_results": {
            "translator-agent": {...},
            "summarizer-agent": {...}
        }
    }
}
```

### Progress Updates

During multi-agent execution, the requester receives progress updates:

```python
# exec.progress messages during flow execution:
{"progress": 0.5, "status_message": "Executing step 1/2: agent translator..."}
{"progress": 1.0, "status_message": "Executing step 2/2: agent summarizer..."}
```

---

## Configuration

### Using .env File (Recommended)

The easiest way to configure Semantic Bus is using a `.env` file:

```bash
# Copy the example file
cp .env.example .env

# Edit with your settings
nano .env
```

The application automatically loads environment variables from `.env` on startup. This is the recommended approach for local development and keeps sensitive credentials out of your command history.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LIP_STORAGE_BACKEND` | `memory` | `memory`, `sqlite`, `postgresql`, `mysql` |
| `LIP_DATABASE_URL` | — | SQLAlchemy async connection URL |
| `LIP_REDIS_URL` | — | Redis URL for distributed presence |
| `LIP_POOL_SIZE` | `5` | Database connection pool size |
| `LIP_CREATE_TABLES` | `true` | Auto-create tables on startup |
| `LIP_KEY_PREFIX` | `lip` | Redis key prefix (multi-tenant) |

### Example Configurations

```bash
# Development (in-memory)
export LIP_STORAGE_BACKEND=memory

# SQLite (single-node production)
export LIP_DATABASE_URL="sqlite+aiosqlite:///./lip.db"

# PostgreSQL (distributed production)
export LIP_DATABASE_URL="postgresql+asyncpg://user:pass@db.example.com/lip"
export LIP_REDIS_URL="redis://cache.example.com:6379"
export LIP_POOL_SIZE=20
```

---

## Task Queue (Scalability)

For high-throughput scenarios, Semantic Bus supports **distributed task queuing** to handle agent requests asynchronously. This enables horizontal scaling across multiple worker nodes.

### Why Use a Task Queue?

- ✅ **Horizontal Scaling**: Multiple workers process tasks in parallel
- ✅ **Backpressure Handling**: Queue absorbs traffic spikes
- ✅ **Retry Logic**: Failed deliveries are automatically retried
- ✅ **Durability**: Redis/Kafka/RabbitMQ provide persistence
- ✅ **Load Balancing**: Tasks distributed across workers

### Supported Backends

| Backend | Best For | Install |
|---------|----------|---------|
| `memory` | Development, testing, single-node | Built-in |
| `redis` | Most production deployments | `pip install -e ".[redis]"` |
| `kafka` | High-throughput event streaming | `pip install -e ".[kafka]"` |
| `rabbitmq` | Complex routing, reliable messaging | `pip install -e ".[rabbitmq]"` |

### Configuration

```bash
# Choose your backend
export TASK_QUEUE_BACKEND=redis  # memory, redis, kafka, rabbitmq

# Redis (recommended for most cases)
export REDIS_URL=redis://localhost:6379

# Kafka (high-throughput streaming)
export KAFKA_BOOTSTRAP_SERVERS=kafka1:9092,kafka2:9092

# RabbitMQ (reliable messaging)
export RABBITMQ_URL=amqp://user:pass@localhost:5672/
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TASK_QUEUE_BACKEND` | `memory` | Queue backend type |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka broker addresses |
| `RABBITMQ_URL` | `amqp://guest:guest@localhost:5672/` | RabbitMQ AMQP URL |

---

## Project Structure

```
src/
├── llm/               # LLM factory for multi-provider support
│   ├── __init__.py    # Public API exports
│   └── factory.py     # LLM instantiation for all providers
├── orchestration/     # Multi-agent flow planning and execution
│   ├── __init__.py    # Public API exports
│   └── flow_planner.py  # LangGraph-based flow discovery
├── protocol/          # Message envelope, types, factories
│   └── envelope.py    # MessageEnvelope, MessageType enum
├── transport/         # WebSocket server, handlers
│   ├── app.py         # FastAPI application
│   ├── handler.py     # Unified flow execution (1-N agents)
│   └── queue.py       # Per-connection backpressure queues
├── registry/          # Agent lifecycle
│   ├── agent.py       # AgentRecord model
│   └── registry.py    # AgentRegistry with heartbeats
├── routing/           # Intent-based routing
│   ├── router.py      # IntentRouter with semantic matching
│   ├── queued_router.py  # Queue-backed router for scaling
│   └── semantic_matcher.py  # LangChain-based capability matching
├── session/           # Ephemeral interface management
│   ├── session.py     # Session with flow_agents, ExecutionRecord
│   └── manager.py     # SessionManager with TTL
├── policy/            # IBAC governance
│   └── engine.py      # PolicyEngine, PolicyRule
├── schema/            # View registry
│   └── view_registry.py  # Pydantic model validation
├── trace/             # Observability
│   └── store.py       # SemanticTraceStore
├── queue/             # Distributed task queuing
│   ├── ports.py       # TaskQueue abstract interface
│   ├── memory.py      # In-memory queue (dev/testing)
│   ├── redis_queue.py # Redis Streams backend
│   ├── kafka_queue.py # Apache Kafka backend
│   ├── rabbitmq_queue.py  # RabbitMQ backend
│   ├── worker.py      # Task worker pool
│   └── factory.py     # Queue factory with env detection
└── storage/           # Pluggable persistence
    ├── ports.py       # Abstract interfaces
    ├── memory.py      # In-memory adapters
    ├── sqlalchemy.py  # SQL adapters
    ├── redis.py       # Redis adapters
    └── factory.py     # Configuration-based factory
```

---

## API Reference

### Health Check

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "healthy",
  "agents": 12,
  "online": 10,
  "sessions": 45,
  "active_sessions": 8,
  "trace_events": 1523,
  "connection_queues": 10
}
```

### WebSocket Endpoint

```
ws://localhost:8000/ws
```

All agent communication flows through this single WebSocket endpoint using the LIP message envelope format.

---

## Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

### Development Setup

```bash
# Clone and install
git clone https://github.com/draiven-io/semantic-bus.git
cd semantic-bus
pip install -e ".[dev,storage]"

# Run tests
pytest

# Type checking
mypy src

# Linting
ruff check src
```

### Code Style

- Python 3.11+ with type hints
- Async-first (no blocking calls)
- Pydantic v2 for models
- SQLAlchemy 2.0 async for persistence

---

## Roadmap

### 🎯 Upcoming Features

#### Core Capabilities
- [ ] **Memory** — Add persistent memory layer for agents to maintain context across sessions and improve learning from past interactions
- [ ] **Guardrails** — Implement safety constraints and validation mechanisms to ensure agents operate within defined boundaries and policies
- [ ] **Authentication & Authorization** — Secure agent-to-bus communication:
  - Agent identity verification and registration
  - Token-based authentication (JWT/API keys)
  - Role-based access control (RBAC) for agent capabilities
  - Intent-level authorization policies
  - Audit logging for security events
- [ ] **Improved Agent Ranking System** — Enhanced agent selection with rewards and penalties system:
  - Track agent performance metrics (success rate, response time, quality)
  - Apply reward weights for successful completions
  - Apply penalty weights for failures or timeouts
  - Use weighted scoring in semantic matching and agent selection process

#### Infrastructure & Scaling
- [ ] **v0.2** — Redis cluster support, horizontal scaling
- [ ] **v0.3** — GraphQL introspection API
- [ ] **v0.4** — OpenTelemetry integration
- [ ] **v0.5** — Multi-bus federation

#### Release Goals
- [ ] **v1.0** — Production-ready release

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

## Acknowledgments

Semantic Bus implements the concepts described in the **[Liquid Interface Protocol (LIP)](https://github.com/draiven-io/liquid-interfaces)** specification. Special thanks to the research on intent-based agent communication and ephemeral interface patterns.

---

<p align="center">
  <sub>Built with 🌊 for the age of autonomous agents</sub>
</p>
