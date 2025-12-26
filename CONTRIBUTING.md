# Contributing to Semantic Bus

First off, thank you for considering contributing to Semantic Bus! It's people like you that make this project possible.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Pull Request Process](#pull-request-process)
- [Style Guidelines](#style-guidelines)
- [Architecture Decisions](#architecture-decisions)

---

## Code of Conduct

This project and everyone participating in it is governed by our commitment to creating a welcoming and inclusive environment. By participating, you are expected to:

- Use welcoming and inclusive language
- Be respectful of differing viewpoints and experiences
- Gracefully accept constructive criticism
- Focus on what is best for the community

---

## Getting Started

### Prerequisites

- Python 3.11 or higher
- Git
- A text editor or IDE (VS Code recommended)

### Understanding the Project

Before contributing, we recommend:

1. Reading the [README.md](README.md) to understand what Semantic Bus does
2. Reviewing the [Liquid Interface Protocol concepts](README.md#core-concepts)
3. Exploring the [project structure](README.md#project-structure)

---

## Development Setup

### 1. Fork and Clone

```bash
# Fork the repository on GitHub, then:
git clone https://github.com/YOUR-USERNAME/semantic-bus.git
cd semantic-bus
```

### 2. Create a Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 3. Install Dependencies

```bash
# Install all development dependencies
pip install -e ".[dev,storage]"
```

### 4. Verify Setup

```bash
# Run tests
pytest

# Type checking
mypy src

# Linting
ruff check src
```

---

## Making Changes

### 1. Create a Branch

```bash
git checkout -b feature/your-feature-name
# or
git checkout -b fix/issue-description
```

### 2. Make Your Changes

- Write clean, readable code
- Add type hints to all functions
- Write docstrings for public APIs
- Add tests for new functionality

### 3. Test Your Changes

```bash
# Run the full test suite
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Type check
mypy src

# Lint
ruff check src
ruff format src
```

### 4. Commit Your Changes

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```bash
git commit -m "feat: add streaming support for large payloads"
git commit -m "fix: handle connection timeout in heartbeat loop"
git commit -m "docs: update API reference for trace.get"
git commit -m "refactor: extract policy evaluation to separate module"
```

**Commit Types:**
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation only
- `refactor:` Code change that neither fixes a bug nor adds a feature
- `test:` Adding or updating tests
- `chore:` Maintenance tasks

---

## Pull Request Process

### 1. Before Submitting

- [ ] All tests pass
- [ ] Type checking passes
- [ ] Linting passes
- [ ] Documentation is updated (if applicable)
- [ ] CHANGELOG.md is updated (for significant changes)

### 2. Submit Your PR

1. Push your branch to your fork
2. Open a Pull Request against `main`
3. Fill out the PR template completely
4. Link any related issues

### 3. Review Process

- A maintainer will review your PR
- Address any requested changes
- Once approved, your PR will be merged

---

## Style Guidelines

### Python Style

We follow PEP 8 with some modifications enforced by Ruff:

```python
# Good: Type hints on all public functions
async def create_session(
    tenant_id: str,
    requester_id: UUID,
    intent: dict[str, Any],
) -> Session:
    """
    Create a new ephemeral interface session.
    
    Args:
        tenant_id: The tenant context
        requester_id: Agent requesting the session
        intent: The declared intent
        
    Returns:
        The created Session
        
    Raises:
        ValidationError: If intent is malformed
    """
    ...

# Good: Use dataclasses or Pydantic for data structures
@dataclass
class AgentRecord:
    agent_id: UUID
    tenant_id: str
    capabilities: list[str]
    
# Good: Async-first
async def fetch_data() -> Data:
    ...

# Bad: Sync in async context
def fetch_data_sync() -> Data:  # Don't do this
    ...
```

### Async Guidelines

All I/O operations must be async:

```python
# Good
async with session_factory() as session:
    result = await session.execute(select(Model))

# Bad - blocks the event loop
with sync_session() as session:
    result = session.execute(select(Model))
```

### Import Order

```python
# 1. Standard library
from datetime import datetime
from typing import Any
from uuid import UUID

# 2. Third-party
from fastapi import WebSocket
from pydantic import BaseModel

# 3. Local
from src.protocol.envelope import MessageEnvelope
from src.session import Session
```

---

## Architecture Decisions

### Key Principles

1. **Async-Only**: No blocking I/O anywhere
2. **Dependency Inversion**: Depend on interfaces (ports), not implementations
3. **Explicit > Implicit**: No magic, clear data flow
4. **Fail Fast**: Validate early, reject bad input immediately

### When Adding New Features

Ask yourself:

- Does this align with the LIP protocol philosophy?
- Is it async-compatible?
- Does it follow the ports/adapters pattern?
- Is it testable in isolation?

### Storage Layer

When adding new storage adapters:

1. Implement the interface from `src/storage/ports.py`
2. Add tests that verify the contract
3. Update `src/storage/factory.py` if needed
4. Document any new environment variables

---

## Questions?

- Open a [Discussion](https://github.com/your-org/semantic-bus/discussions) for questions
- Open an [Issue](https://github.com/your-org/semantic-bus/issues) for bugs or feature requests

Thank you for contributing! 🌊
