"""
Storage Factory

Environment-based configuration and factory for storage adapters.
Returns a StorageBundle with appropriate implementations based on settings.

Supported backends:
- memory: In-memory storage (development/testing)
- sqlite: SQLite with aiosqlite (single-node production)
- postgresql: PostgreSQL with asyncpg (distributed production)
- mysql: MySQL with aiomysql (distributed production)
- redis: Redis for ExecStore and RegistryStore (optional overlay)

Usage:
    # From environment
    bundle = await create_storage_from_env()
    
    # From settings
    settings = StorageSettings(database_url="postgresql+asyncpg://...")
    bundle = await create_storage(settings)
    
    # Use in bus
    session_manager = SessionManager(storage=bundle.sessions)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)

from .ports import StorageBundle, SessionStore, TraceStore, ExecStore, RegistryStore
from .memory import (
    InMemorySessionStore,
    InMemoryTraceStore,
    InMemoryExecStore,
    InMemoryRegistryStore,
)
from .sqlalchemy import (
    SqlAlchemySessionStore,
    SqlAlchemyTraceStore,
    SqlAlchemyExecStore,
)
from .models import Base


class StorageBackend(str, Enum):
    """Supported storage backends."""
    MEMORY = "memory"
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"


@dataclass
class StorageSettings:
    """
    Configuration for storage layer.
    
    Attributes:
        backend: Storage backend type
        database_url: SQLAlchemy async connection URL (for SQL backends)
        redis_url: Redis connection URL (optional, for distributed presence/exec)
        pool_size: Connection pool size for SQL
        pool_max_overflow: Max overflow for connection pool
        echo_sql: Whether to log SQL queries
        create_tables: Whether to auto-create tables on startup
        key_prefix: Prefix for Redis keys (multi-tenant isolation)
        exec_ttl_seconds: TTL for execution records in Redis
        presence_ttl_seconds: TTL for presence records in Redis
    """
    backend: StorageBackend = StorageBackend.MEMORY
    database_url: str | None = None
    redis_url: str | None = None
    pool_size: int = 5
    pool_max_overflow: int = 10
    echo_sql: bool = False
    create_tables: bool = True
    key_prefix: str = "lip"
    exec_ttl_seconds: int = 86400 * 7  # 7 days
    presence_ttl_seconds: int = 300  # 5 minutes


@dataclass
class StorageBundleImpl(StorageBundle):
    """
    StorageBundle implementation with cleanup support.
    """
    sessions: SessionStore
    trace: TraceStore
    execs: ExecStore
    registry: RegistryStore | None = None
    _engine: AsyncEngine | None = field(default=None, repr=False)
    _redis: Any = field(default=None, repr=False)  # redis.asyncio.Redis
    
    async def close(self) -> None:
        """Close all storage connections."""
        if self._engine:
            await self._engine.dispose()
        if self._redis:
            await self._redis.close()


def _parse_database_url(url: str) -> StorageBackend:
    """Determine backend from database URL."""
    if url.startswith("sqlite"):
        return StorageBackend.SQLITE
    elif url.startswith("postgresql") or url.startswith("postgres"):
        return StorageBackend.POSTGRESQL
    elif url.startswith("mysql"):
        return StorageBackend.MYSQL
    else:
        raise ValueError(f"Unsupported database URL scheme: {url}")


def settings_from_env() -> StorageSettings:
    """
    Create StorageSettings from environment variables.
    
    Environment variables:
        LIP_STORAGE_BACKEND: "memory", "sqlite", "postgresql", "mysql"
        LIP_DATABASE_URL: SQLAlchemy async connection URL
        LIP_REDIS_URL: Redis connection URL (optional)
        LIP_POOL_SIZE: Connection pool size
        LIP_ECHO_SQL: "true" to log SQL
        LIP_CREATE_TABLES: "false" to disable table creation
        LIP_KEY_PREFIX: Redis key prefix
        LIP_EXEC_TTL: Execution TTL in seconds
        LIP_PRESENCE_TTL: Presence TTL in seconds
    """
    database_url = os.getenv("LIP_DATABASE_URL")
    backend_str = os.getenv("LIP_STORAGE_BACKEND", "memory")
    
    # Auto-detect backend from URL if provided
    if database_url and backend_str == "memory":
        backend = _parse_database_url(database_url)
    else:
        backend = StorageBackend(backend_str)
    
    return StorageSettings(
        backend=backend,
        database_url=database_url,
        redis_url=os.getenv("LIP_REDIS_URL"),
        pool_size=int(os.getenv("LIP_POOL_SIZE", "5")),
        pool_max_overflow=int(os.getenv("LIP_POOL_MAX_OVERFLOW", "10")),
        echo_sql=os.getenv("LIP_ECHO_SQL", "").lower() == "true",
        create_tables=os.getenv("LIP_CREATE_TABLES", "true").lower() != "false",
        key_prefix=os.getenv("LIP_KEY_PREFIX", "lip"),
        exec_ttl_seconds=int(os.getenv("LIP_EXEC_TTL", str(86400 * 7))),
        presence_ttl_seconds=int(os.getenv("LIP_PRESENCE_TTL", "300")),
    )


async def create_storage(settings: StorageSettings) -> StorageBundle:
    """
    Create storage bundle from settings.
    
    Args:
        settings: Storage configuration
        
    Returns:
        Configured StorageBundle
        
    Raises:
        ValueError: If settings are invalid
    """
    engine: AsyncEngine | None = None
    redis_client: Any = None
    
    # Create SQL engine if needed
    if settings.backend != StorageBackend.MEMORY:
        if not settings.database_url:
            raise ValueError(
                f"database_url required for backend {settings.backend}"
            )
        
        # Ensure async driver is in URL
        url = settings.database_url
        if settings.backend == StorageBackend.SQLITE:
            if "+aiosqlite" not in url:
                url = url.replace("sqlite://", "sqlite+aiosqlite://")
        elif settings.backend == StorageBackend.POSTGRESQL:
            if "+asyncpg" not in url:
                url = url.replace("postgresql://", "postgresql+asyncpg://")
                url = url.replace("postgres://", "postgresql+asyncpg://")
        elif settings.backend == StorageBackend.MYSQL:
            if "+aiomysql" not in url:
                url = url.replace("mysql://", "mysql+aiomysql://")
        
        engine = create_async_engine(
            url,
            pool_size=settings.pool_size,
            max_overflow=settings.pool_max_overflow,
            echo=settings.echo_sql,
        )
        
        # Create tables if requested
        if settings.create_tables:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
    
    # Create Redis client if URL provided
    if settings.redis_url:
        try:
            from redis.asyncio import Redis
            redis_client = Redis.from_url(
                settings.redis_url,
                decode_responses=False,
            )
        except ImportError:
            raise ImportError(
                "redis package required for Redis storage. "
                "Install with: pip install redis"
            )
    
    # Create stores based on backend
    if settings.backend == StorageBackend.MEMORY:
        sessions: SessionStore = InMemorySessionStore()
        trace: TraceStore = InMemoryTraceStore()
        execs: ExecStore = InMemoryExecStore()
        registry: RegistryStore | None = InMemoryRegistryStore()
    else:
        # SQL backend
        assert engine is not None
        session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        
        sessions = SqlAlchemySessionStore(session_factory)
        trace = SqlAlchemyTraceStore(session_factory)
        execs = SqlAlchemyExecStore(session_factory)
        registry = None  # SQL registry not implemented, use Redis or in-memory
    
    # Overlay Redis for exec and registry if configured
    if redis_client:
        from .redis import RedisExecStore, RedisRegistryStore
        
        execs = RedisExecStore(
            redis=redis_client,
            key_prefix=settings.key_prefix,
            default_ttl_seconds=settings.exec_ttl_seconds,
        )
        registry = RedisRegistryStore(
            redis=redis_client,
            key_prefix=settings.key_prefix,
            presence_ttl_seconds=settings.presence_ttl_seconds,
        )
    
    return StorageBundleImpl(
        sessions=sessions,
        trace=trace,
        execs=execs,
        registry=registry,
        _engine=engine,
        _redis=redis_client,
    )


async def create_storage_from_env() -> StorageBundle:
    """
    Create storage bundle from environment variables.
    
    Convenience function that combines settings_from_env() and create_storage().
    
    Returns:
        Configured StorageBundle
    """
    settings = settings_from_env()
    return await create_storage(settings)


# Convenience for quick setup
async def create_memory_storage() -> StorageBundle:
    """Create in-memory storage bundle (for testing)."""
    return await create_storage(StorageSettings(backend=StorageBackend.MEMORY))


async def create_sqlite_storage(
    path: str = ":memory:",
    create_tables: bool = True,
) -> StorageBundle:
    """Create SQLite storage bundle."""
    url = f"sqlite+aiosqlite:///{path}"
    return await create_storage(StorageSettings(
        backend=StorageBackend.SQLITE,
        database_url=url,
        create_tables=create_tables,
    ))


async def create_postgres_storage(
    url: str,
    create_tables: bool = True,
    pool_size: int = 5,
) -> StorageBundle:
    """Create PostgreSQL storage bundle."""
    return await create_storage(StorageSettings(
        backend=StorageBackend.POSTGRESQL,
        database_url=url,
        create_tables=create_tables,
        pool_size=pool_size,
    ))
