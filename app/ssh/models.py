"""Immutable typed inputs and results for LIM's SSH boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from .exceptions import SSHValidationError
from .validation import (
    normalize_command,
    normalize_host,
    normalize_port,
    normalize_remote_path,
    normalize_timeout,
    normalize_username,
    normalize_uuid,
)


class SSHIdentity(StrEnum):
    """Configured private identity reference; never a key path or key content."""

    ADMIN = "admin"
    MONITOR = "monitor"


class SSHAuthenticationMethod(StrEnum):
    """Permitted non-interactive OpenSSH authentication methods."""

    PUBLIC_KEY = "publickey"


class SSHFailureType(StrEnum):
    """Safe classification of an SSH execution outcome."""

    NONE = "none"
    HOST_NOT_TRUSTED = "host_not_trusted"
    HOST_KEY_CHANGED = "host_key_changed"
    AUTHENTICATION_FAILED = "authentication_failed"
    CONNECTION_REFUSED = "connection_refused"
    DNS_RESOLUTION_FAILED = "dns_resolution_failed"
    CONNECTION_TIMEOUT = "connection_timeout"
    COMMAND_TIMEOUT = "command_timeout"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    REMOTE_NONZERO_EXIT = "remote_nonzero_exit"
    CANCELLED = "cancelled"
    LOCAL_PROCESS_FAILURE = "local_process_failure"


class SSHTrustStatus(StrEnum):
    """Comparison of currently presented and application-trusted keys."""

    UNKNOWN = "unknown"
    TRUSTED = "trusted"
    CHANGED = "changed"
    UNREACHABLE = "unreachable"


class SSHTransferDirection(StrEnum):
    """Direction of one non-recursive file transfer."""

    UPLOAD = "upload"
    DOWNLOAD = "download"


@dataclass(frozen=True, slots=True)
class SSHConnectionTarget:
    """Validated SSH network destination without credentials."""

    host: str
    username: str
    port: int = 22
    server_uuid: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "host", normalize_host(self.host))
        object.__setattr__(self, "username", normalize_username(self.username))
        object.__setattr__(self, "port", normalize_port(self.port))
        object.__setattr__(self, "server_uuid", normalize_uuid(self.server_uuid))


@dataclass(frozen=True, slots=True)
class SSHCommandRequest:
    """Structured remote command request; arguments are quoted independently."""

    target: SSHConnectionTarget
    command: tuple[str, ...]
    identity: SSHIdentity
    timeout_seconds: float | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, SSHConnectionTarget):
            raise SSHValidationError("command target is invalid")
        object.__setattr__(self, "command", normalize_command(self.command))
        if not isinstance(self.identity, SSHIdentity):
            raise SSHValidationError("SSH identity reference is invalid")
        if self.timeout_seconds is not None:
            object.__setattr__(
                self,
                "timeout_seconds",
                normalize_timeout(self.timeout_seconds, field="command timeout"),
            )
        if self.correlation_id is not None and (
            not isinstance(self.correlation_id, str)
            or not self.correlation_id.strip()
            or len(self.correlation_id) > 128
        ):
            raise SSHValidationError("correlation ID is invalid")


@dataclass(frozen=True, slots=True)
class SSHCommandResult:
    """Bounded remote command outcome."""

    target: SSHConnectionTarget
    exit_code: int | None
    stdout: str
    stderr: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool
    failure_type: SSHFailureType
    attempts: int
    correlation_id: str | None = None

    @property
    def succeeded(self) -> bool:
        """Return whether transport and remote command both succeeded."""
        return self.failure_type is SSHFailureType.NONE and self.exit_code == 0


@dataclass(frozen=True, slots=True)
class SSHFileTransferRequest:
    """One explicit, non-recursive local/remote file transfer."""

    target: SSHConnectionTarget
    local_path: Path
    remote_path: str
    direction: SSHTransferDirection
    identity: SSHIdentity
    timeout_seconds: float | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, SSHConnectionTarget):
            raise SSHValidationError("transfer target is invalid")
        if not isinstance(self.local_path, Path) or not self.local_path.is_absolute():
            raise SSHValidationError("local transfer path must be absolute")
        object.__setattr__(self, "remote_path", normalize_remote_path(self.remote_path))
        if not isinstance(self.direction, SSHTransferDirection):
            raise SSHValidationError("transfer direction is invalid")
        if not isinstance(self.identity, SSHIdentity):
            raise SSHValidationError("SSH identity reference is invalid")
        if self.timeout_seconds is not None:
            object.__setattr__(
                self,
                "timeout_seconds",
                normalize_timeout(self.timeout_seconds, field="transfer timeout"),
            )


@dataclass(frozen=True, slots=True)
class SSHFileTransferResult:
    """Safe file-transfer outcome without file contents."""

    request: SSHFileTransferRequest
    exit_code: int | None
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    timed_out: bool
    failure_type: SSHFailureType

    @property
    def succeeded(self) -> bool:
        return self.failure_type is SSHFailureType.NONE and self.exit_code == 0


@dataclass(frozen=True, slots=True)
class SSHHostKey:
    """Public host key and SHA256 fingerprint."""

    host_token: str
    algorithm: str
    public_key: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class SSHTrustResult:
    """Presented-versus-trusted host-key comparison."""

    target: SSHConnectionTarget
    status: SSHTrustStatus
    presented_keys: tuple[SSHHostKey, ...] = ()
    trusted_keys: tuple[SSHHostKey, ...] = ()


@dataclass(frozen=True, slots=True)
class SSHDiagnosticResult:
    """Non-mutating readiness report suitable for a future operator interface."""

    target: SSHConnectionTarget
    target_valid: bool
    admin_identity_available: bool
    monitor_identity_available: bool
    trust_store_writable: bool
    trust_status: SSHTrustStatus
    reachable: bool | None
    authentication_succeeded: bool | None
    command_succeeded: bool | None
