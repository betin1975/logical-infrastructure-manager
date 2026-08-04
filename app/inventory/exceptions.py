"""Inventory domain and repository exceptions."""


class InventoryError(RuntimeError):
    """Base class for inventory failures."""


class InventoryValidationError(InventoryError, ValueError):
    """Raised when inventory data violates a domain invariant."""


class DuplicateInventoryError(InventoryError):
    """Raised when a unique hostname or address is already reserved."""


class ServerNotFoundError(InventoryError):
    """Raised when an inventory operation cannot find its target server."""


class InventoryConflictError(InventoryError):
    """Raised when optimistic inventory versioning detects stale state."""


class InventoryRepositoryError(InventoryError):
    """Raised when inventory persistence cannot complete safely."""
