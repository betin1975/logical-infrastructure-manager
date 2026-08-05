"""Safe SSH subsystem exceptions."""


class SSHManagerError(RuntimeError):
    """Base exception for SSH infrastructure failures."""


class SSHConfigurationError(SSHManagerError):
    """Raised when SSH configuration is incomplete or unsafe."""


class SSHValidationError(SSHManagerError, ValueError):
    """Raised when a target, command, or transfer is invalid."""


class SSHIdentityError(SSHManagerError):
    """Raised when a configured private identity is unavailable or unsafe."""


class SSHExecutableError(SSHManagerError):
    """Raised when a required OpenSSH executable is unavailable."""


class SSHTrustStoreError(SSHManagerError):
    """Raised when application-owned host trust cannot be used safely."""


class SSHHostNotTrustedError(SSHManagerError):
    """Raised when strict host verification has no trusted key."""


class SSHHostKeyChangedError(SSHManagerError):
    """Raised when the presented host key differs from application trust."""


class SSHFingerprintMismatchError(SSHManagerError):
    """Raised when explicit confirmation does not match the presented key."""


class SSHLocalProcessError(SSHManagerError):
    """Raised when a local OpenSSH process cannot be started safely."""
