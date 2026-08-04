"""Discovery domain and persistence exceptions."""


class DiscoveryError(Exception):
    """Base exception for discovery operations."""


class DiscoveryValidationError(DiscoveryError, ValueError):
    """Raised when an observation violates a domain invariant."""


class ObservationNotFoundError(DiscoveryError, LookupError):
    """Raised when an observation cannot be found."""


class DiscoveryConflictError(DiscoveryError):
    """Raised when an observation changed concurrently or has invalid state."""


class DiscoveryRepositoryError(DiscoveryError):
    """Raised when discovery persistence fails safely."""
