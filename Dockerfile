# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Build stage for dependencies
FROM base AS builder

# Copy project files
COPY pyproject.toml README.md ./

# Install build dependencies
RUN pip install --upgrade pip && \
    pip install hatchling

# Create a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install the package with optional dependencies
# Use --no-deps first, then install deps to leverage layer caching
COPY src/ ./src/

# Install base dependencies (modify the extras as needed)
# Options: dev, sqlite, postgresql, mysql, redis, kafka, rabbitmq, 
#          queues, langchain, langchain-azure, langchain-anthropic, 
#          langchain-google, langchain-ollama, langchain-all, storage, all
ARG INSTALL_EXTRAS="postgresql,redis,langchain"
RUN pip install ".[${INSTALL_EXTRAS}]"

# Production stage
FROM base AS production

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY src/ ./src/
COPY pyproject.toml README.md ./

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# Expose the default FastAPI port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Default command - run with uvicorn
CMD ["uvicorn", "src.transport.app:app", "--host", "0.0.0.0", "--port", "8000"]

# Development stage
FROM base AS development

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install dev dependencies
RUN pip install pytest pytest-asyncio httpx ruff mypy

# Copy application code
COPY . .

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Default command for development with auto-reload
CMD ["uvicorn", "src.transport.app:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
