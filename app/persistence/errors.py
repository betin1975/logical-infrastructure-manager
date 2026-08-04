"""Persistence-specific exceptions for LIM."""


class PersistenceError(RuntimeError):
    """Base class for safe, descriptive persistence failures."""


class DatabaseConfigurationError(PersistenceError):
    """Raised when database configuration violates LIM policy."""


class DatabaseInitializationError(PersistenceError):
    """Raised when the managed SQLite database cannot be initialized."""


class DatabaseConnectionError(PersistenceError):
    """Raised when an operation-scoped SQLite connection cannot be opened."""


class TransactionError(PersistenceError):
    """Raised when an explicit transaction cannot complete safely."""


class MigrationError(PersistenceError):
    """Raised when migration metadata or execution is invalid."""


class BackupError(PersistenceError):
    """Raised when a consistent database backup cannot be created."""


class RestoreValidationError(PersistenceError):
    """Raised when a candidate database backup is unsafe or invalid."""
