"""Exceptions for on-demand polling coordination."""


class PollingError(RuntimeError):
    """Base exception for polling-service errors."""


class PollingValidationError(PollingError, ValueError):
    """Raised when a polling request or result violates its contract."""
