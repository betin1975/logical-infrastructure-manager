"""Immutable typed values for idempotent Linux bootstrap."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from .exceptions import BootstrapValidationError


class BootstrapStepName(StrEnum):
    """Ordered bootstrap operations exposed to callers."""

    VALIDATE_REQUEST = "validate_request"
    INSPECT_SSH_TRUST = "inspect_ssh_trust"
    VERIFY_ADMIN_AUTHENTICATION = "verify_admin_authentication"
    DETECT_TARGET_PLATFORM = "detect_target_platform"
    VERIFY_NONINTERACTIVE_SUDO = "verify_noninteractive_sudo"
    INSPECT_MONITOR_ACCOUNT = "inspect_monitor_account"
    REPAIR_MONITOR_ACCOUNT = "repair_monitor_account"
    CREATE_MONITOR_SSH_DIRECTORY = "create_monitor_ssh_directory"
    INSTALL_MONITOR_AUTHORIZED_KEY = "install_monitor_authorized_key"
    INSTALL_COLLECTOR = "install_collector"
    APPLY_OWNERSHIP_AND_PERMISSIONS = "apply_ownership_and_permissions"
    VERIFY_COLLECTOR_AS_MONITOR = "verify_collector_as_monitor"
    VERIFY_MONITOR_AUTHENTICATION = "verify_monitor_authentication"
    VERIFY_FORCED_COMMAND = "verify_forced_command"
    RECORD_INVENTORY_SUCCESS = "record_inventory_success"


class BootstrapStepStatus(StrEnum):
    """Outcome of one represented plan step."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class BootstrapFailureType(StrEnum):
    """Safe bootstrap failure classifications."""

    NONE = "none"
    INVALID_REQUEST = "invalid_request"
    INVENTORY_SERVER_UNAVAILABLE = "inventory_server_unavailable"
    HOST_NOT_TRUSTED = "host_not_trusted"
    HOST_KEY_CHANGED = "host_key_changed"
    ADMIN_AUTHENTICATION_FAILED = "admin_authentication_failed"
    ADMIN_KEY_MISSING = "admin_key_missing"
    MONITOR_KEY_MISSING = "monitor_key_missing"
    TARGET_NOT_LINUX = "target_not_linux"
    REQUIRED_UTILITY_MISSING = "required_utility_missing"
    SUDO_UNAVAILABLE = "sudo_unavailable"
    SUDO_REQUIRES_PASSWORD = "sudo_requires_password"
    CONFLICTING_ACCOUNT = "conflicting_account"
    USER_MANAGEMENT_FAILED = "user_management_failed"
    FILE_TRANSFER_FAILED = "file_transfer_failed"
    UNSAFE_REMOTE_PATH = "unsafe_remote_path"
    AUTHORIZED_KEY_UPDATE_FAILED = "authorized_key_update_failed"
    COLLECTOR_INSTALLATION_FAILED = "collector_installation_failed"
    MONITOR_AUTHENTICATION_FAILED = "monitor_authentication_failed"
    INVALID_COLLECTOR_JSON = "invalid_collector_json"
    COLLECTOR_SCHEMA_MISMATCH = "collector_schema_mismatch"
    COMMAND_TIMEOUT = "command_timeout"
    CLEANUP_FAILED = "cleanup_failed"
    HOST_IDENTITY_MISMATCH = "host_identity_mismatch"
    VERIFICATION_FAILED = "verification_failed"


class PrivilegeEscalationStatus(StrEnum):
    """Noninteractive privilege-escalation readiness."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    PASSWORD_REQUIRED = "password_required"


@dataclass(frozen=True, slots=True)
class BootstrapRequest:
    """One target bootstrap request without passwords or key material."""

    server_uuid: UUID
    admin_username: str
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.server_uuid, UUID) or self.server_uuid.int == 0:
            raise BootstrapValidationError("bootstrap server UUID is invalid")
        if (
            not isinstance(self.admin_username, str)
            or not self.admin_username.strip()
            or len(self.admin_username) > 64
        ):
            raise BootstrapValidationError("bootstrap admin username is invalid")
        object.__setattr__(self, "admin_username", self.admin_username.strip())
        if self.correlation_id is not None and (
            not isinstance(self.correlation_id, str)
            or not self.correlation_id.strip()
            or len(self.correlation_id) > 128
        ):
            raise BootstrapValidationError("bootstrap correlation ID is invalid")


@dataclass(frozen=True, slots=True)
class BootstrapArtifact:
    """Validated local-to-remote artifact metadata without file contents."""

    local_path: Path
    remote_path: str
    version: str
    sha256: str


@dataclass(frozen=True, slots=True)
class BootstrapSettings:
    """Validated local and remote bootstrap policy."""

    monitor_username: str
    monitor_home: str
    monitor_shell: str
    collector_path: str
    collector_version: str
    monitor_public_key: Path
    collector_artifact: Path
    authorized_keys_path: str
    authorized_key_options: tuple[str, ...]
    key_marker: str
    remote_temp_directory: str
    account_comment: str
    forbidden_groups: tuple[str, ...]
    directory_mode: str
    ssh_directory_mode: str
    authorized_keys_mode: str
    collector_directory_mode: str
    collector_mode: str
    command_timeout_seconds: float
    transfer_timeout_seconds: float
    verification_timeout_seconds: float
    maximum_collector_output_bytes: int
    supported_schema_versions: tuple[int, ...]
    utility_paths: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class BootstrapStep:
    """Safe result for one ordered bootstrap step."""

    name: BootstrapStepName
    status: BootstrapStepStatus
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    changed: bool = False
    message: str = ""
    failure_type: BootstrapFailureType = BootstrapFailureType.NONE


@dataclass(frozen=True, slots=True)
class BootstrapPlan:
    """Explicit immutable ordered bootstrap plan."""

    steps: tuple[BootstrapStepName, ...]


@dataclass(frozen=True, slots=True)
class BootstrapVerificationResult:
    """Safe post-install verification summary."""

    admin_verified: bool = False
    account_verified: bool = False
    files_verified: bool = False
    direct_collector_verified: bool = False
    monitor_authenticated: bool = False
    forced_command_verified: bool = False
    collector_schema_version: int | None = None


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """Complete bootstrap outcome including skipped later plan steps."""

    request: BootstrapRequest
    success: bool
    failure_type: BootstrapFailureType
    steps: tuple[BootstrapStep, ...]
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    changed: bool
    inventory_updated: bool
    verification: BootstrapVerificationResult
