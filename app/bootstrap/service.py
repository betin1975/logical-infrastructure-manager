"""Idempotent least-privileged Linux bootstrap through SSHManager."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.config import ConfigError, ConfigurationManager
from app.inventory import (
    InventoryError,
    InventoryService,
    Server,
    ServerNotFoundError,
    ServerStatus,
)
from app.runtime import RuntimeManager
from app.ssh import (
    SSHCommandRequest,
    SSHCommandResult,
    SSHConnectionTarget,
    SSHFailureType,
    SSHFileTransferRequest,
    SSHIdentity,
    SSHManager,
    SSHManagerError,
    SSHTransferDirection,
    SSHTrustStatus,
    SSHValidationError,
)

from .exceptions import BootstrapConfigurationError, BootstrapValidationError
from .models import (
    BootstrapArtifact,
    BootstrapFailureType,
    BootstrapRequest,
    BootstrapResult,
    BootstrapSettings,
    BootstrapStep,
    BootstrapStepName,
    BootstrapStepStatus,
    BootstrapVerificationResult,
)
from .plan import DEFAULT_BOOTSTRAP_PLAN
from .scripts import remote_file_manager_command
from .validation import (
    load_public_key,
    validate_marker,
    validate_mode,
    validate_remote_path,
    validate_username,
    validate_version,
)

_REQUIRED_UTILITIES = frozenset(
    {
        "python",
        "sudo",
        "getent",
        "id",
        "useradd",
        "usermod",
        "gpasswd",
        "passwd",
        "test",
        "rm",
    }
)
_FORCED_COMMAND_PROBE = "LIM_FORCED_COMMAND_PROBE"


class BootstrapLogger(Protocol):
    """Narrow contextual logger accepted by BootstrapService."""

    def bind(self, **context: Any) -> BootstrapLogger: ...

    def info(self, message: object, *args: object, **kwargs: object) -> None: ...

    def warning(self, message: object, *args: object, **kwargs: object) -> None: ...


@dataclass(slots=True)
class _Context:
    request: BootstrapRequest
    server: Server | None = None
    admin_target: SSHConnectionTarget | None = None
    monitor_target: SSHConnectionTarget | None = None
    account: tuple[str, ...] | None = None
    uid: int | None = None
    gid: int | None = None
    remote_hostname: str | None = None
    remote_changed: bool = False
    inventory_updated: bool = False
    staged_remote_paths: set[str] = field(default_factory=set)
    verification: BootstrapVerificationResult = field(
        default_factory=BootstrapVerificationResult
    )


class _StepAbort(Exception):
    def __init__(self, failure_type: BootstrapFailureType, message: str) -> None:
        super().__init__(message)
        self.failure_type = failure_type
        self.message = message


class BootstrapService:
    """Prepare one trusted Linux server for forced-command LIM monitoring."""

    def __init__(
        self,
        config: ConfigurationManager,
        runtime: RuntimeManager,
        ssh_manager: SSHManager,
        inventory_service: InventoryService,
        logger: BootstrapLogger,
        *,
        application_root: str | Path,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._ssh = ssh_manager
        self._inventory = inventory_service
        self._logger = logger
        self._application_root = Path(application_root).resolve()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._uuid_factory = uuid_factory or uuid4
        self.settings = self._load_settings()
        self.artifact: BootstrapArtifact | None = None
        self._authorized_key_entry: str | None = None
        self._initialized = False

    @property
    def is_initialized(self) -> bool:
        """Return whether local, network-free bootstrap validation succeeded."""
        return self._initialized

    def initialize(self) -> BootstrapSettings:
        """Validate local bootstrap dependencies without contacting any host."""
        self._initialized = False
        if not self._runtime.is_initialized:
            raise BootstrapConfigurationError(
                "runtime must be initialized before BootstrapService"
            )
        if not self._ssh.is_initialized:
            raise BootstrapConfigurationError(
                "SSHManager must be initialized before BootstrapService"
            )
        try:
            key_type, key_body = load_public_key(self.settings.monitor_public_key)
        except BootstrapValidationError as exc:
            raise BootstrapConfigurationError(
                "bootstrap monitor public key is unavailable"
            ) from exc
        try:
            configured_artifact = self.settings.collector_artifact
            if configured_artifact.is_symlink() or not configured_artifact.is_file():
                raise BootstrapConfigurationError(
                    "bootstrap collector artifact is invalid"
                )
            artifact_path = configured_artifact.resolve(strict=True)
            artifact_path.relative_to(self._application_root)
            artifact_bytes = artifact_path.read_bytes()
        except (OSError, ValueError) as exc:
            raise BootstrapConfigurationError(
                "bootstrap collector artifact is unavailable"
            ) from exc
        if len(artifact_bytes) > 1_048_576:
            raise BootstrapConfigurationError("bootstrap collector artifact is invalid")
        version_marker = (
            f'COLLECTOR_VERSION = "{self.settings.collector_version}"'.encode()
        )
        if version_marker not in artifact_bytes:
            raise BootstrapConfigurationError(
                "bootstrap collector version does not match its artifact"
            )
        self.artifact = BootstrapArtifact(
            local_path=artifact_path,
            remote_path=self.settings.collector_path,
            version=self.settings.collector_version,
            sha256=hashlib.sha256(artifact_bytes).hexdigest(),
        )
        options = ",".join(self.settings.authorized_key_options)
        self._authorized_key_entry = (
            f'{options},command="{self.settings.collector_path}" '
            f"{key_type} {key_body} {self.settings.key_marker}\n"
        )
        self._initialized = True
        self._logger.bind(component="bootstrap", operation="initialize").info(
            "Bootstrap foundation initialized collector_version=%s",
            self.settings.collector_version,
        )
        return self.settings

    def bootstrap(self, request: BootstrapRequest) -> BootstrapResult:
        """Execute the ordered plan and stop safely after the first fatal step."""
        if not self._initialized or self.artifact is None:
            raise BootstrapConfigurationError("BootstrapService is not initialized")
        if not isinstance(request, BootstrapRequest):
            raise BootstrapValidationError("bootstrap request is invalid")
        context = _Context(request)
        started_at = self._now()
        started_tick = self._monotonic()
        logger = self._logger.bind(
            server_id=str(request.server_uuid),
            operation="bootstrap_linux",
            correlation_id=request.correlation_id,
        )
        completed: list[BootstrapStep] = []
        failure = BootstrapFailureType.NONE
        actions = self._actions()

        for name in DEFAULT_BOOTSTRAP_PLAN.steps:
            step_started = self._now()
            step_tick = self._monotonic()
            try:
                changed, message = actions[name](context)
            except _StepAbort as exc:
                step_finished = self._now()
                step = BootstrapStep(
                    name,
                    BootstrapStepStatus.FAILED,
                    step_started,
                    step_finished,
                    self._elapsed_ms(step_tick),
                    False,
                    exc.message,
                    exc.failure_type,
                )
                completed.append(step)
                failure = exc.failure_type
                logger.warning(
                    "Bootstrap step failed step=%s changed=false failure=%s "
                    "duration_ms=%d collector_version=%s",
                    name.value,
                    failure.value,
                    step.duration_ms,
                    self.settings.collector_version,
                )
                break
            step_finished = self._now()
            step = BootstrapStep(
                name,
                BootstrapStepStatus.SUCCEEDED,
                step_started,
                step_finished,
                self._elapsed_ms(step_tick),
                changed,
                message,
            )
            completed.append(step)
            logger.info(
                "Bootstrap step completed step=%s changed=%s duration_ms=%d "
                "collector_version=%s",
                name.value,
                changed,
                step.duration_ms,
                self.settings.collector_version,
            )

        if failure is not BootstrapFailureType.NONE:
            timestamp = self._now()
            completed.extend(
                BootstrapStep(
                    name,
                    BootstrapStepStatus.SKIPPED,
                    timestamp,
                    timestamp,
                    0,
                    False,
                    "not run after earlier failure",
                )
                for name in DEFAULT_BOOTSTRAP_PLAN.steps[len(completed) :]
            )
        finished_at = self._now()
        success = failure is BootstrapFailureType.NONE
        result = BootstrapResult(
            request=request,
            success=success,
            failure_type=failure,
            steps=tuple(completed),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=self._elapsed_ms(started_tick),
            changed=any(step.changed for step in completed),
            inventory_updated=context.inventory_updated,
            verification=context.verification,
        )
        logger.info(
            "Bootstrap completed success=%s changed=%s failure=%s duration_ms=%d "
            "collector_version=%s",
            result.success,
            result.changed,
            result.failure_type.value,
            result.duration_ms,
            self.settings.collector_version,
        )
        return result

    def _actions(
        self,
    ) -> dict[BootstrapStepName, Callable[[_Context], tuple[bool, str]]]:
        return {
            BootstrapStepName.VALIDATE_REQUEST: self._validate_request,
            BootstrapStepName.INSPECT_SSH_TRUST: self._inspect_trust,
            BootstrapStepName.VERIFY_ADMIN_AUTHENTICATION: self._verify_admin,
            BootstrapStepName.DETECT_TARGET_PLATFORM: self._detect_platform,
            BootstrapStepName.VERIFY_NONINTERACTIVE_SUDO: self._verify_sudo,
            BootstrapStepName.INSPECT_MONITOR_ACCOUNT: self._inspect_account,
            BootstrapStepName.REPAIR_MONITOR_ACCOUNT: self._repair_account,
            BootstrapStepName.CREATE_MONITOR_SSH_DIRECTORY: self._create_ssh_directory,
            BootstrapStepName.INSTALL_MONITOR_AUTHORIZED_KEY: self._install_key,
            BootstrapStepName.INSTALL_COLLECTOR: self._install_collector,
            BootstrapStepName.APPLY_OWNERSHIP_AND_PERMISSIONS: self._verify_files,
            BootstrapStepName.VERIFY_COLLECTOR_AS_MONITOR: self._verify_direct,
            BootstrapStepName.VERIFY_MONITOR_AUTHENTICATION: self._verify_monitor,
            BootstrapStepName.VERIFY_FORCED_COMMAND: self._verify_forced_command,
            BootstrapStepName.RECORD_INVENTORY_SUCCESS: self._record_success,
        }

    def _validate_request(self, context: _Context) -> tuple[bool, str]:
        try:
            server = self._inventory.get_server(context.request.server_uuid)
        except ServerNotFoundError as exc:
            raise _StepAbort(
                BootstrapFailureType.INVENTORY_SERVER_UNAVAILABLE,
                "inventory server is unavailable",
            ) from exc
        if (
            server.status is not ServerStatus.ACTIVE
            or not server.enabled
            or not server.managed
            or server.deleted_at is not None
        ):
            raise _StepAbort(
                BootstrapFailureType.INVENTORY_SERVER_UNAVAILABLE,
                "inventory server is not enabled, managed, and active",
            )
        if not self._ssh.identity_available(SSHIdentity.ADMIN):
            raise _StepAbort(
                BootstrapFailureType.ADMIN_KEY_MISSING,
                "administrative SSH identity is unavailable",
            )
        if not self._ssh.identity_available(SSHIdentity.MONITOR):
            raise _StepAbort(
                BootstrapFailureType.MONITOR_KEY_MISSING,
                "monitor SSH identity is unavailable",
            )
        address = server.management_address or server.primary_address
        try:
            context.admin_target = self._ssh.create_target(
                address,
                context.request.admin_username,
                server_uuid=server.uuid,
            )
            context.monitor_target = self._ssh.create_target(
                address,
                self.settings.monitor_username,
                server_uuid=server.uuid,
            )
        except SSHValidationError as exc:
            raise _StepAbort(
                BootstrapFailureType.INVALID_REQUEST,
                "bootstrap SSH target is invalid",
            ) from exc
        context.server = server
        return False, "request and local identities validated"

    def _inspect_trust(self, context: _Context) -> tuple[bool, str]:
        target = self._admin_target(context)
        try:
            trust = self._ssh.inspect_host_key(target)
        except SSHManagerError as exc:
            raise _StepAbort(
                BootstrapFailureType.HOST_NOT_TRUSTED,
                "SSH host trust could not be verified",
            ) from exc
        if trust.status is SSHTrustStatus.CHANGED:
            raise _StepAbort(
                BootstrapFailureType.HOST_KEY_CHANGED, "SSH host key has changed"
            )
        if trust.status is not SSHTrustStatus.TRUSTED:
            raise _StepAbort(
                BootstrapFailureType.HOST_NOT_TRUSTED,
                "SSH host is not explicitly trusted",
            )
        return False, "SSH host trust verified without mutation"

    def _verify_admin(self, context: _Context) -> tuple[bool, str]:
        result = self._run(
            context,
            ("true",),
            SSHIdentity.ADMIN,
            timeout=self.settings.verification_timeout_seconds,
        )
        if not result.succeeded:
            failure = self._command_failure(
                result, BootstrapFailureType.ADMIN_AUTHENTICATION_FAILED
            )
            raise _StepAbort(failure, "administrative SSH authentication failed")
        context.verification = self._verification(context, admin_verified=True)
        return False, "administrative SSH authentication verified"

    def _detect_platform(self, context: _Context) -> tuple[bool, str]:
        result = self._admin(context, ("uname", "-s"))
        if not result.succeeded or result.stdout.strip().lower() != "linux":
            raise _StepAbort(
                BootstrapFailureType.TARGET_NOT_LINUX,
                "bootstrap target is not supported Linux",
            )
        return False, "Linux target verified"

    def _verify_sudo(self, context: _Context) -> tuple[bool, str]:
        utilities = dict(self.settings.utility_paths)
        for path in utilities.values():
            result = self._admin(
                context,
                (utilities["test"], "-x", path),
                allow_nonzero=True,
            )
            if not result.succeeded:
                raise _StepAbort(
                    BootstrapFailureType.REQUIRED_UTILITY_MISSING,
                    "required remote bootstrap utility is unavailable",
                )
        result = self._admin(
            context,
            (utilities["sudo"], "-n", "true"),
            allow_nonzero=True,
        )
        if not result.succeeded:
            failure = (
                BootstrapFailureType.SUDO_REQUIRES_PASSWORD
                if result.exit_code == 1
                else BootstrapFailureType.SUDO_UNAVAILABLE
            )
            raise _StepAbort(failure, "noninteractive sudo is unavailable")
        return False, "noninteractive sudo and required utilities verified"

    def _inspect_account(self, context: _Context) -> tuple[bool, str]:
        utilities = dict(self.settings.utility_paths)
        result = self._admin(
            context,
            (utilities["getent"], "passwd", self.settings.monitor_username),
            allow_nonzero=True,
        )
        if result.exit_code in {1, 2}:
            context.account = None
            return False, "monitor account is absent"
        if not result.succeeded:
            raise _StepAbort(
                BootstrapFailureType.USER_MANAGEMENT_FAILED,
                "monitor account inspection failed",
            )
        fields = tuple(result.stdout.strip().split(":"))
        if len(fields) != 7 or fields[0] != self.settings.monitor_username:
            raise _StepAbort(
                BootstrapFailureType.CONFLICTING_ACCOUNT,
                "monitor account record is invalid",
            )
        if fields[4] != self.settings.account_comment:
            raise _StepAbort(
                BootstrapFailureType.CONFLICTING_ACCOUNT,
                "existing monitor account is not LIM-managed",
            )
        context.account = fields
        return False, "existing LIM monitor account inspected"

    def _repair_account(self, context: _Context) -> tuple[bool, str]:
        utilities = dict(self.settings.utility_paths)
        changed = False
        if context.account is None:
            command = (
                utilities["sudo"],
                "-n",
                utilities["useradd"],
                "--system",
                "--user-group",
                "--create-home",
                "--home-dir",
                self.settings.monitor_home,
                "--shell",
                self.settings.monitor_shell,
                "--comment",
                self.settings.account_comment,
                self.settings.monitor_username,
            )
            self._require_admin_success(
                context, command, BootstrapFailureType.USER_MANAGEMENT_FAILED
            )
            changed = True
        elif (
            context.account[5] != self.settings.monitor_home
            or context.account[6] != self.settings.monitor_shell
        ):
            command = (
                utilities["sudo"],
                "-n",
                utilities["usermod"],
                "--home",
                self.settings.monitor_home,
                "--move-home",
                "--shell",
                self.settings.monitor_shell,
                self.settings.monitor_username,
            )
            self._require_admin_success(
                context, command, BootstrapFailureType.USER_MANAGEMENT_FAILED
            )
            changed = True
        uid_result = self._admin(
            context, (utilities["id"], "-u", self.settings.monitor_username)
        )
        gid_result = self._admin(
            context, (utilities["id"], "-g", self.settings.monitor_username)
        )
        try:
            context.uid = (
                int(uid_result.stdout.strip()) if uid_result.succeeded else None
            )
            context.gid = (
                int(gid_result.stdout.strip()) if gid_result.succeeded else None
            )
        except ValueError as exc:
            raise _StepAbort(
                BootstrapFailureType.USER_MANAGEMENT_FAILED,
                "monitor account identity is invalid",
            ) from exc
        if (
            context.uid is None
            or context.gid is None
            or context.uid <= 0
            or context.gid <= 0
        ):
            raise _StepAbort(
                BootstrapFailureType.USER_MANAGEMENT_FAILED,
                "monitor account identity is unavailable",
            )
        password = self._sudo(
            context,
            (utilities["passwd"], "-S", self.settings.monitor_username),
            allow_nonzero=True,
        )
        status_fields = password.stdout.split()
        if not password.succeeded or len(status_fields) < 2:
            raise _StepAbort(
                BootstrapFailureType.USER_MANAGEMENT_FAILED,
                "monitor password state is unavailable",
            )
        if status_fields[1] not in {"L", "LK"}:
            self._require_admin_success(
                context,
                (
                    utilities["sudo"],
                    "-n",
                    utilities["usermod"],
                    "--lock",
                    self.settings.monitor_username,
                ),
                BootstrapFailureType.USER_MANAGEMENT_FAILED,
            )
            changed = True
        groups = self._admin(
            context, (utilities["id"], "-nG", self.settings.monitor_username)
        )
        if not groups.succeeded:
            raise _StepAbort(
                BootstrapFailureType.USER_MANAGEMENT_FAILED,
                "monitor group membership is unavailable",
            )
        for group in sorted(
            set(groups.stdout.split()) & set(self.settings.forbidden_groups)
        ):
            self._require_admin_success(
                context,
                (
                    utilities["sudo"],
                    "-n",
                    utilities["gpasswd"],
                    "--delete",
                    self.settings.monitor_username,
                    group,
                ),
                BootstrapFailureType.USER_MANAGEMENT_FAILED,
            )
            changed = True
        changed |= self._ensure_directory(
            context,
            self.settings.monitor_home,
            context.uid,
            context.gid,
            self.settings.directory_mode,
        )
        context.remote_changed |= changed
        return changed, "monitor account created or repaired"

    def _create_ssh_directory(self, context: _Context) -> tuple[bool, str]:
        changed = self._ensure_directory(
            context,
            str(PurePosixPath(self.settings.authorized_keys_path).parent),
            self._uid(context),
            self._gid(context),
            self.settings.ssh_directory_mode,
        )
        context.remote_changed |= changed
        return changed, "monitor SSH directory is secure"

    def _install_key(self, context: _Context) -> tuple[bool, str]:
        if self._authorized_key_entry is None:
            raise _StepAbort(
                BootstrapFailureType.MONITOR_KEY_MISSING,
                "monitor public key is unavailable",
            )
        remote = self._remote_temp_path(context, "authorized-key")
        local = self._write_local_stage(self._authorized_key_entry.encode("utf-8"))
        primary_failure: _StepAbort | None = None
        try:
            self._upload(context, local, remote)
            result = self._helper(
                context,
                "merge_key",
                remote,
                self.settings.authorized_keys_path,
                str(self._uid(context)),
                str(self._gid(context)),
                self.settings.authorized_keys_mode,
                self.settings.key_marker,
                failure=BootstrapFailureType.AUTHORIZED_KEY_UPDATE_FAILED,
            )
            changed = bool(result.get("changed"))
        except _StepAbort as exc:
            primary_failure = exc
            raise
        finally:
            local.unlink(missing_ok=True)
            try:
                self._cleanup_remote(context, remote)
            except _StepAbort:
                if primary_failure is None:
                    raise
        context.remote_changed |= changed
        return changed, "LIM monitor authorized key installed atomically"

    def _install_collector(self, context: _Context) -> tuple[bool, str]:
        if self.artifact is None:
            raise _StepAbort(
                BootstrapFailureType.COLLECTOR_INSTALLATION_FAILED,
                "collector artifact is unavailable",
            )
        parent = str(PurePosixPath(self.settings.collector_path).parent)
        changed = self._ensure_directory(
            context, parent, 0, 0, self.settings.collector_directory_mode
        )
        remote = self._remote_temp_path(context, "collector")
        primary_failure: _StepAbort | None = None
        try:
            self._upload(context, self.artifact.local_path, remote)
            result = self._helper(
                context,
                "install",
                remote,
                self.settings.collector_path,
                "0",
                "0",
                self.settings.collector_mode,
                failure=BootstrapFailureType.COLLECTOR_INSTALLATION_FAILED,
            )
            changed |= bool(result.get("changed"))
        except _StepAbort as exc:
            primary_failure = exc
            raise
        finally:
            try:
                self._cleanup_remote(context, remote)
            except _StepAbort:
                if primary_failure is None:
                    raise
        context.remote_changed |= changed
        return changed, "remote health collector installed atomically"

    def _verify_account_state(self, context: _Context) -> None:
        utilities = dict(self.settings.utility_paths)
        account = self._admin(
            context,
            (utilities["getent"], "passwd", self.settings.monitor_username),
        )
        fields = account.stdout.strip().split(":")
        if len(fields) != 7 or (
            fields[0] != self.settings.monitor_username
            or fields[2] != str(self._uid(context))
            or fields[3] != str(self._gid(context))
            or fields[4] != self.settings.account_comment
            or fields[5] != self.settings.monitor_home
            or fields[6] != self.settings.monitor_shell
        ):
            raise _StepAbort(
                BootstrapFailureType.VERIFICATION_FAILED,
                "monitor account verification failed",
            )
        password = self._sudo(
            context,
            (utilities["passwd"], "-S", self.settings.monitor_username),
        )
        password_fields = password.stdout.split()
        groups = self._admin(
            context, (utilities["id"], "-nG", self.settings.monitor_username)
        )
        if (
            len(password_fields) < 2
            or password_fields[1] not in {"L", "LK"}
            or set(groups.stdout.split()) & set(self.settings.forbidden_groups)
        ):
            raise _StepAbort(
                BootstrapFailureType.VERIFICATION_FAILED,
                "monitor account restrictions are invalid",
            )
        context.verification = self._verification(context, account_verified=True)

    def _verify_files(self, context: _Context) -> tuple[bool, str]:
        self._verify_account_state(context)
        expected = (
            (
                self.settings.monitor_home,
                "directory",
                self._uid(context),
                self._gid(context),
                self.settings.directory_mode,
                None,
            ),
            (
                str(PurePosixPath(self.settings.authorized_keys_path).parent),
                "directory",
                self._uid(context),
                self._gid(context),
                self.settings.ssh_directory_mode,
                None,
            ),
            (
                self.settings.authorized_keys_path,
                "file",
                self._uid(context),
                self._gid(context),
                self.settings.authorized_keys_mode,
                None,
            ),
            (
                self.settings.collector_path,
                "file",
                0,
                0,
                self.settings.collector_mode,
                self.artifact.sha256 if self.artifact else None,
            ),
        )
        for path, kind, uid, gid, mode, digest in expected:
            state = self._helper(
                context,
                "state",
                path,
                failure=BootstrapFailureType.VERIFICATION_FAILED,
            ).get("state", {})
            if not isinstance(state, Mapping) or not (
                state.get("exists")
                and state.get("kind") == kind
                and state.get("uid") == uid
                and state.get("gid") == gid
                and state.get("mode") == int(mode, 8)
                and (digest is None or state.get("sha256") == digest)
            ):
                raise _StepAbort(
                    BootstrapFailureType.VERIFICATION_FAILED,
                    "bootstrap ownership or permissions are invalid",
                )
        context.verification = self._verification(context, files_verified=True)
        return False, "account files and collector permissions verified"

    def _verify_direct(self, context: _Context) -> tuple[bool, str]:
        utilities = dict(self.settings.utility_paths)
        result = self._admin(
            context,
            (
                utilities["sudo"],
                "-n",
                "-u",
                self.settings.monitor_username,
                "--",
                self.settings.collector_path,
            ),
            timeout=self.settings.verification_timeout_seconds,
        )
        document = self._collector_document(result)
        context.remote_hostname = self._document_hostname(document)
        context.verification = self._verification(
            context,
            direct_collector_verified=True,
            collector_schema_version=int(document["schema_version"]),
        )
        return False, "collector JSON verified under monitor identity"

    def _verify_monitor(self, context: _Context) -> tuple[bool, str]:
        target = self._monitor_target(context)
        try:
            trust = self._ssh.inspect_host_key(target)
        except SSHManagerError as exc:
            raise _StepAbort(
                BootstrapFailureType.HOST_NOT_TRUSTED,
                "SSH host trust was lost during bootstrap",
            ) from exc
        if trust.status is SSHTrustStatus.CHANGED:
            raise _StepAbort(
                BootstrapFailureType.HOST_KEY_CHANGED,
                "SSH host key changed during bootstrap",
            )
        if trust.status is not SSHTrustStatus.TRUSTED:
            raise _StepAbort(
                BootstrapFailureType.HOST_NOT_TRUSTED,
                "SSH host trust was lost during bootstrap",
            )
        result = self._run(
            context,
            ("true",),
            SSHIdentity.MONITOR,
            timeout=self.settings.verification_timeout_seconds,
        )
        document = self._collector_document(
            result, failure=BootstrapFailureType.MONITOR_AUTHENTICATION_FAILED
        )
        if self._document_hostname(document) != context.remote_hostname:
            raise _StepAbort(
                BootstrapFailureType.HOST_IDENTITY_MISMATCH,
                "monitor collector host identity does not match",
            )
        context.verification = self._verification(context, monitor_authenticated=True)
        return False, "monitor SSH authentication verified"

    def _verify_forced_command(self, context: _Context) -> tuple[bool, str]:
        result = self._run(
            context,
            ("printf", _FORCED_COMMAND_PROBE),
            SSHIdentity.MONITOR,
            timeout=self.settings.verification_timeout_seconds,
        )
        if _FORCED_COMMAND_PROBE in result.stdout:
            raise _StepAbort(
                BootstrapFailureType.VERIFICATION_FAILED,
                "monitor forced command did not block requested command",
            )
        document = self._collector_document(result)
        if self._document_hostname(document) != context.remote_hostname:
            raise _StepAbort(
                BootstrapFailureType.HOST_IDENTITY_MISMATCH,
                "forced-command collector host identity does not match",
            )
        context.verification = self._verification(context, forced_command_verified=True)
        return False, "forced collector command and schema verified"

    def _record_success(self, context: _Context) -> tuple[bool, str]:
        server = self._server(context)
        should_update = context.remote_changed or server.last_bootstrap_at is None
        if should_update:
            try:
                self._inventory.record_bootstrap_success(server.uuid)
            except InventoryError as exc:
                raise _StepAbort(
                    BootstrapFailureType.INVENTORY_SERVER_UNAVAILABLE,
                    "verified bootstrap could not be recorded",
                ) from exc
            context.inventory_updated = True
        return should_update, "verified bootstrap success recorded in inventory"

    def _collector_document(
        self,
        result: SSHCommandResult,
        *,
        failure: BootstrapFailureType = BootstrapFailureType.INVALID_COLLECTOR_JSON,
    ) -> Mapping[str, Any]:
        if not result.succeeded:
            classified = (
                failure
                if result.failure_type is SSHFailureType.AUTHENTICATION_FAILED
                else self._command_failure(result, failure)
            )
            raise _StepAbort(
                classified,
                "collector execution failed",
            )
        if (
            result.stdout_truncated
            or len(result.stdout.encode("utf-8"))
            > self.settings.maximum_collector_output_bytes
        ):
            raise _StepAbort(
                BootstrapFailureType.INVALID_COLLECTOR_JSON,
                "collector output exceeded its limit",
            )
        try:
            document = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise _StepAbort(
                BootstrapFailureType.INVALID_COLLECTOR_JSON,
                "collector returned invalid JSON",
            ) from exc
        if not isinstance(document, Mapping):
            raise _StepAbort(
                BootstrapFailureType.INVALID_COLLECTOR_JSON,
                "collector returned invalid JSON",
            )
        schema = document.get("schema_version")
        if (
            type(schema) is not int
            or schema not in self.settings.supported_schema_versions
        ):
            raise _StepAbort(
                BootstrapFailureType.COLLECTOR_SCHEMA_MISMATCH,
                "collector schema version is unsupported",
            )
        if document.get("collector_version") != self.settings.collector_version:
            raise _StepAbort(
                BootstrapFailureType.COLLECTOR_SCHEMA_MISMATCH,
                "collector application version is unexpected",
            )
        return document

    @staticmethod
    def _document_hostname(document: Mapping[str, Any]) -> str:
        host = document.get("host")
        hostname = host.get("hostname") if isinstance(host, Mapping) else None
        if (
            not isinstance(hostname, str)
            or not hostname.strip()
            or len(hostname) > 253
            or any(character.isspace() for character in hostname)
        ):
            raise _StepAbort(
                BootstrapFailureType.HOST_IDENTITY_MISMATCH,
                "collector host identity is invalid",
            )
        return hostname.lower().rstrip(".")

    def _ensure_directory(
        self,
        context: _Context,
        path: str,
        uid: int,
        gid: int,
        mode: str,
    ) -> bool:
        result = self._helper(
            context,
            "ensure_dir",
            path,
            str(uid),
            str(gid),
            mode,
            failure=BootstrapFailureType.UNSAFE_REMOTE_PATH,
        )
        return bool(result.get("changed"))

    def _helper(
        self,
        context: _Context,
        action: str,
        *arguments: str,
        failure: BootstrapFailureType,
    ) -> Mapping[str, Any]:
        utilities = dict(self.settings.utility_paths)
        helper = remote_file_manager_command(utilities["python"], action, *arguments)
        result = self._sudo(context, helper, allow_nonzero=True)
        if not result.succeeded:
            raise _StepAbort(failure, "secure remote file operation failed")
        try:
            document = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise _StepAbort(failure, "secure remote file operation failed") from exc
        if not isinstance(document, Mapping) or document.get("ok") is not True:
            raise _StepAbort(failure, "secure remote file operation failed")
        return document

    def _upload(self, context: _Context, local: Path, remote: str) -> None:
        try:
            request = SSHFileTransferRequest(
                target=self._admin_target(context),
                local_path=local.absolute(),
                remote_path=remote,
                direction=SSHTransferDirection.UPLOAD,
                identity=SSHIdentity.ADMIN,
                timeout_seconds=self.settings.transfer_timeout_seconds,
                correlation_id=context.request.correlation_id,
            )
            result = self._ssh.transfer(request)
        except (SSHManagerError, SSHValidationError) as exc:
            raise _StepAbort(
                BootstrapFailureType.FILE_TRANSFER_FAILED,
                "bootstrap file transfer failed",
            ) from exc
        if not result.succeeded:
            failure = (
                BootstrapFailureType.COMMAND_TIMEOUT
                if result.timed_out
                else BootstrapFailureType.FILE_TRANSFER_FAILED
            )
            raise _StepAbort(failure, "bootstrap file transfer failed")
        context.staged_remote_paths.add(remote)

    def _cleanup_remote(self, context: _Context, remote: str) -> None:
        if remote not in context.staged_remote_paths:
            return
        utilities = dict(self.settings.utility_paths)
        result = self._admin(
            context, (utilities["rm"], "-f", "--", remote), allow_nonzero=True
        )
        if not result.succeeded:
            raise _StepAbort(
                BootstrapFailureType.CLEANUP_FAILED,
                "temporary bootstrap artifact cleanup failed",
            )
        context.staged_remote_paths.discard(remote)

    def _write_local_stage(self, content: bytes) -> Path:
        descriptor, name = tempfile.mkstemp(
            prefix="lim-bootstrap-", dir=self._runtime.paths.jobs
        )
        try:
            os.fchmod(descriptor, 0o600)
            remaining = memoryview(content)
            while remaining:
                written = os.write(descriptor, remaining)
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return Path(name)

    def _remote_temp_path(self, context: _Context, purpose: str) -> str:
        nonce = self._uuid_factory().hex
        return (
            f"{self.settings.remote_temp_directory}/"
            f"lim-bootstrap-{context.request.server_uuid.hex}-{nonce}-{purpose}"
        )

    def _admin(
        self,
        context: _Context,
        command: tuple[str, ...],
        *,
        timeout: float | None = None,
        allow_nonzero: bool = False,
    ) -> SSHCommandResult:
        result = self._run(
            context,
            command,
            SSHIdentity.ADMIN,
            timeout=timeout or self.settings.command_timeout_seconds,
        )
        if not allow_nonzero and not result.succeeded:
            raise _StepAbort(
                self._command_failure(result, BootstrapFailureType.VERIFICATION_FAILED),
                "remote bootstrap command failed",
            )
        return result

    def _sudo(
        self,
        context: _Context,
        command: tuple[str, ...],
        *,
        allow_nonzero: bool = False,
    ) -> SSHCommandResult:
        utilities = dict(self.settings.utility_paths)
        return self._admin(
            context,
            (utilities["sudo"], "-n", "--", *command),
            allow_nonzero=allow_nonzero,
        )

    def _run(
        self,
        context: _Context,
        command: tuple[str, ...],
        identity: SSHIdentity,
        *,
        timeout: float,
    ) -> SSHCommandResult:
        target = (
            self._admin_target(context)
            if identity is SSHIdentity.ADMIN
            else self._monitor_target(context)
        )
        try:
            return self._ssh.run(
                SSHCommandRequest(
                    target=target,
                    command=command,
                    identity=identity,
                    timeout_seconds=timeout,
                    correlation_id=context.request.correlation_id,
                )
            )
        except (SSHManagerError, SSHValidationError) as exc:
            raise _StepAbort(
                BootstrapFailureType.VERIFICATION_FAILED,
                "SSHManager could not execute bootstrap command",
            ) from exc

    def _require_admin_success(
        self,
        context: _Context,
        command: tuple[str, ...],
        failure: BootstrapFailureType,
    ) -> SSHCommandResult:
        result = self._admin(context, command, allow_nonzero=True)
        if not result.succeeded:
            raise _StepAbort(
                self._command_failure(result, failure),
                "remote account operation failed",
            )
        return result

    @staticmethod
    def _command_failure(
        result: SSHCommandResult,
        fallback: BootstrapFailureType,
    ) -> BootstrapFailureType:
        if result.timed_out or result.failure_type is SSHFailureType.COMMAND_TIMEOUT:
            return BootstrapFailureType.COMMAND_TIMEOUT
        if result.failure_type is SSHFailureType.AUTHENTICATION_FAILED:
            return BootstrapFailureType.ADMIN_AUTHENTICATION_FAILED
        return fallback

    def _verification(
        self, context: _Context, **changes: object
    ) -> BootstrapVerificationResult:
        values = {
            name: getattr(context.verification, name)
            for name in BootstrapVerificationResult.__dataclass_fields__
        }
        values.update(changes)
        return BootstrapVerificationResult(**values)

    @staticmethod
    def _server(context: _Context) -> Server:
        if context.server is None:
            raise _StepAbort(
                BootstrapFailureType.INVALID_REQUEST, "inventory server is unavailable"
            )
        return context.server

    @staticmethod
    def _admin_target(context: _Context) -> SSHConnectionTarget:
        if context.admin_target is None:
            raise _StepAbort(
                BootstrapFailureType.INVALID_REQUEST, "admin SSH target is unavailable"
            )
        return context.admin_target

    @staticmethod
    def _monitor_target(context: _Context) -> SSHConnectionTarget:
        if context.monitor_target is None:
            raise _StepAbort(
                BootstrapFailureType.INVALID_REQUEST,
                "monitor SSH target is unavailable",
            )
        return context.monitor_target

    @staticmethod
    def _uid(context: _Context) -> int:
        if context.uid is None:
            raise _StepAbort(
                BootstrapFailureType.USER_MANAGEMENT_FAILED,
                "monitor UID is unavailable",
            )
        return context.uid

    @staticmethod
    def _gid(context: _Context) -> int:
        if context.gid is None:
            raise _StepAbort(
                BootstrapFailureType.USER_MANAGEMENT_FAILED,
                "monitor GID is unavailable",
            )
        return context.gid

    def _elapsed_ms(self, started: float) -> int:
        return max(0, round((self._monotonic() - started) * 1000))

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise BootstrapValidationError("bootstrap clock must be timezone-aware")
        return value.astimezone(UTC)

    def _load_settings(self) -> BootstrapSettings:
        try:
            monitor_username = validate_username(
                self._config.require("bootstrap.monitor_username", str),
                field="bootstrap monitor username",
            )
            monitor_home = validate_remote_path(
                self._config.require("bootstrap.monitor_home", str),
                field="bootstrap monitor home",
            )
            monitor_shell = validate_remote_path(
                self._config.require("bootstrap.monitor_shell", str),
                field="bootstrap monitor shell",
            )
            collector_path = validate_remote_path(
                self._config.require("bootstrap.collector_path", str),
                field="bootstrap collector path",
            )
            authorized_keys = validate_remote_path(
                self._config.require("bootstrap.authorized_keys_path", str),
                field="bootstrap authorized keys path",
            )
            remote_temp = validate_remote_path(
                self._config.require("bootstrap.remote_temp_directory", str),
                field="bootstrap remote temporary directory",
            )
            version = validate_version(
                self._config.require("bootstrap.collector_version", str),
                field="bootstrap collector version",
            )
            marker = validate_marker(self._config.require("bootstrap.key_marker", str))
            account_comment = self._config.require("bootstrap.account_comment", str)
            options_raw = self._config.require("bootstrap.authorized_key_options", list)
            forbidden_raw = self._config.require("bootstrap.forbidden_groups", list)
            utility_raw = self._config.require("bootstrap.utility_paths", dict)
            schema_raw = self._config.require(
                "bootstrap.supported_collector_schema_versions", list
            )
            settings = BootstrapSettings(
                monitor_username=monitor_username,
                monitor_home=monitor_home,
                monitor_shell=monitor_shell,
                collector_path=collector_path,
                collector_version=version,
                monitor_public_key=self._resolve_local(
                    self._config.require("bootstrap.monitor_public_key", str)
                ),
                collector_artifact=self._resolve_local(
                    self._config.require("bootstrap.collector_artifact", str)
                ),
                authorized_keys_path=authorized_keys,
                authorized_key_options=self._options(options_raw),
                key_marker=marker,
                remote_temp_directory=remote_temp,
                account_comment=self._account_comment(account_comment),
                forbidden_groups=self._groups(forbidden_raw),
                directory_mode=validate_mode(
                    self._config.require("bootstrap.directory_mode", str),
                    field="bootstrap directory mode",
                    directory=True,
                ),
                ssh_directory_mode=validate_mode(
                    self._config.require("bootstrap.ssh_directory_mode", str),
                    field="bootstrap SSH directory mode",
                    directory=True,
                ),
                authorized_keys_mode=validate_mode(
                    self._config.require("bootstrap.authorized_keys_mode", str),
                    field="bootstrap authorized-keys mode",
                    directory=False,
                ),
                collector_directory_mode=validate_mode(
                    self._config.require("bootstrap.collector_directory_mode", str),
                    field="bootstrap collector directory mode",
                    directory=True,
                ),
                collector_mode=validate_mode(
                    self._config.require("bootstrap.collector_mode", str),
                    field="bootstrap collector mode",
                    directory=True,
                ),
                command_timeout_seconds=self._timeout("command_timeout_seconds"),
                transfer_timeout_seconds=self._timeout("transfer_timeout_seconds"),
                verification_timeout_seconds=self._timeout(
                    "verification_timeout_seconds"
                ),
                maximum_collector_output_bytes=self._output_limit(),
                supported_schema_versions=self._schemas(schema_raw),
                utility_paths=self._utilities(utility_raw),
            )
        except (ConfigError, BootstrapConfigurationError) as exc:
            raise BootstrapConfigurationError(
                f"invalid bootstrap configuration: {type(exc).__name__}"
            ) from exc
        expected_ssh = str(PurePosixPath(monitor_home) / ".ssh")
        if str(PurePosixPath(authorized_keys).parent) != expected_ssh:
            raise BootstrapConfigurationError(
                "bootstrap authorized keys must be directly inside monitor .ssh"
            )
        if collector_path == monitor_shell or monitor_home == "/":
            raise BootstrapConfigurationError("bootstrap remote paths conflict")
        return settings

    def _resolve_local(self, value: str) -> Path:
        if not value.strip():
            raise BootstrapConfigurationError("bootstrap local path cannot be empty")
        path = Path(value)
        return (
            (self._application_root / path).absolute()
            if not path.is_absolute()
            else path.absolute()
        )

    def _timeout(self, name: str) -> float:
        value = self._config.require(f"bootstrap.{name}")
        if type(value) not in {int, float} or not 0 < float(value) <= 3600:
            raise BootstrapConfigurationError(f"bootstrap {name} is invalid")
        return float(value)

    def _output_limit(self) -> int:
        value = self._config.require("bootstrap.maximum_collector_output_bytes")
        if type(value) is not int or not 1024 <= value <= 4 * 1024 * 1024:
            raise BootstrapConfigurationError(
                "bootstrap collector output limit is invalid"
            )
        return value

    @staticmethod
    def _options(values: list[object]) -> tuple[str, ...]:
        allowed = {
            "restrict",
            "no-agent-forwarding",
            "no-port-forwarding",
            "no-X11-forwarding",
            "no-pty",
        }
        if not values or any(
            not isinstance(value, str) or value not in allowed for value in values
        ):
            raise BootstrapConfigurationError(
                "bootstrap authorized-key options are invalid"
            )
        options = tuple(dict.fromkeys(values))
        legacy = {
            "no-agent-forwarding",
            "no-port-forwarding",
            "no-X11-forwarding",
            "no-pty",
        }
        if "restrict" not in options and not legacy.issubset(options):
            raise BootstrapConfigurationError(
                "bootstrap authorized-key options are incomplete"
            )
        return options

    @staticmethod
    def _groups(values: list[object]) -> tuple[str, ...]:
        try:
            groups = tuple(
                validate_username(value, field="bootstrap forbidden group")
                for value in values
            )
        except BootstrapConfigurationError:
            raise
        if not groups:
            raise BootstrapConfigurationError("bootstrap forbidden groups are required")
        return tuple(dict.fromkeys(groups))

    @staticmethod
    def _account_comment(value: str) -> str:
        if (
            not value.strip()
            or len(value) > 128
            or any(char in value for char in ":\n\r")
        ):
            raise BootstrapConfigurationError("bootstrap account comment is invalid")
        return value.strip()

    @staticmethod
    def _schemas(values: list[object]) -> tuple[int, ...]:
        if not values or any(
            type(value) is not int or not 1 <= value <= 1000 for value in values
        ):
            raise BootstrapConfigurationError("bootstrap schema versions are invalid")
        return tuple(dict.fromkeys(values))

    @staticmethod
    def _utilities(values: dict[object, object]) -> tuple[tuple[str, str], ...]:
        if set(values) != _REQUIRED_UTILITIES:
            raise BootstrapConfigurationError("bootstrap utility paths are incomplete")
        normalized = tuple(
            sorted(
                (
                    str(name),
                    validate_remote_path(value, field=f"bootstrap {name} utility"),
                )
                for name, value in values.items()
                if isinstance(name, str) and isinstance(value, str)
            )
        )
        if len(normalized) != len(_REQUIRED_UTILITIES):
            raise BootstrapConfigurationError("bootstrap utility paths are invalid")
        return normalized
