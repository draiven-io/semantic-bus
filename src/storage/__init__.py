# Storage Layer
# Pluggable persistence for the LIP Semantic Bus
#
# This module provides:
# - Port interfaces (ABCs) defining storage contracts
# - In-memory implementations for development/testing
# - SQLAlchemy implementations for production persistence
# - Redis implementations for high-performance state
# - Factory for configuration-based adapter selection

from .ports import (
    SessionStore,
    SessionRecord,
    TraceStore,
    TraceRecord,
    ExecStore,
    ExecRecord,
    RegistryStore,
    PresenceRecord,
    StorageBundle,
    StorageError,
    NotFoundError,
    ConflictError,
)
from .factory import (
    StorageSettings,
    StorageBackend,
    create_storage,
    create_storage_from_env,
    create_memory_storage,
    create_sqlite_storage,
    create_postgres_storage,
    settings_from_env,
)

__all__ = [
    # Ports
    "SessionStore",
    "SessionRecord",
    "TraceStore",
    "TraceRecord",
    "ExecStore",
    "ExecRecord",
    "RegistryStore",
    "PresenceRecord",
    "StorageBundle",
    "StorageError",
    "NotFoundError",
    "ConflictError",
    # Factory
    "StorageSettings",
    "StorageBackend",
    "create_storage",
    "create_storage_from_env",
    "create_memory_storage",
    "create_sqlite_storage",
    "create_postgres_storage",
    "settings_from_env",
]
