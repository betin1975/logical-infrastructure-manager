"""Safe Bootstrap Service exceptions."""


class BootstrapError(RuntimeError):
    """Base bootstrap foundation error."""


class BootstrapConfigurationError(BootstrapError):
    """Raised when local bootstrap configuration is unsafe."""


class BootstrapValidationError(BootstrapError):
    """Raised when a bootstrap request is invalid."""
