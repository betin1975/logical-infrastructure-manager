"""Intentional public API for LIM's SQLite persistence foundation."""

from .backup import BackupManager, BackupResult, RestoreValidationResult
from .database import DatabaseManager, DatabaseSettings
from .errors import (
    BackupError,
    DatabaseConfigurationError,
    DatabaseConnectionError,
    DatabaseInitializationError,
    MigrationError,
    PersistenceError,
    RestoreValidationError,
    TransactionError,
)
from .inventory_repository import SQLiteInventoryRepository
from .migrations import (
    INTERNAL_MIGRATIONS,
    MIGRATION_TABLE,
    Migration,
    MigrationManager,
    MigrationRecord,
    MigrationState,
)
from .repository import BaseRepository, Repository
from .transactions import TransactionManager

__all__ = [
    "BackupError",
    "BackupManager",
    "BackupResult",
    "BaseRepository",
    "DatabaseConfigurationError",
    "DatabaseConnectionError",
    "DatabaseInitializationError",
    "DatabaseManager",
    "DatabaseSettings",
    "INTERNAL_MIGRATIONS",
    "MIGRATION_TABLE",
    "Migration",
    "MigrationError",
    "MigrationManager",
    "MigrationRecord",
    "MigrationState",
    "PersistenceError",
    "Repository",
    "RestoreValidationError",
    "RestoreValidationResult",
    "SQLiteInventoryRepository",
    "TransactionError",
    "TransactionManager",
]
