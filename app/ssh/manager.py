"""LIM's sole secure OpenSSH orchestration boundary."""

from __future__ import annotations

import os
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from ..config import ConfigError, ConfigurationManager
from ..runtime import RuntimeManager
from .command import OpenSSHProcessRunner, ProcessOutcome
from .exceptions import (
    SSHConfigurationError,
    SSHExecutableError,
    SSHIdentityError,
    SSHManagerError,
    SSHValidationError,
)
from .models import (
    SSHAuthenticationMethod,
    SSHCommandRequest,
    SSHCommandResult,
    SSHConnectionTarget,
    SSHDiagnosticResult,
    SSHFailureType,
    SSHFileTransferRequest,
    SSHFileTransferResult,
    SSHIdentity,
    SSHTransferDirection,
    SSHTrustResult,
    SSHTrustStatus,
)
from .trust import SSHTrustStore
from .validation import (
    normalize_timeout,
    validate_executable,
    validate_private_key,
)


class SSHLogger(Protocol):
    """Narrow structured logger used by SSHManager."""

    def bind(self, **context: Any) -> SSHLogger: ...

    def info(self, message: object, *args: object, **kwargs: object) -> None: ...

    def warning(self, message: object, *args: object, **kwargs: object) -> None: ...


@dataclass(frozen=True, slots=True)
class SSHSettings:
    """Validated OpenSSH policy and resolved paths."""

    credential_root: Path
    admin_private_key: Path
    monitor_private_key: Path
    known_hosts: Path
    ssh_executable: Path
    scp_executable: Path
    keyscan_executable: Path
    connect_timeout_seconds: float
    command_timeout_seconds: float
    max_stdout_bytes: int
    max_stderr_bytes: int
    default_port: int
    authentication_methods: tuple[SSHAuthenticationMethod, ...]
    retry_count: int
    retry_delay_seconds: float
    keepalive_interval_seconds: int
    keepalive_count: int


class SSHManager:
    """Validate trust, run structured commands, and transfer individual files."""

    def __init__(
        self,
        config: ConfigurationManager,
        runtime: RuntimeManager,
        logger: SSHLogger,
        *,
        application_root: str | Path,
        runner: OpenSSHProcessRunner | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._logger = logger
        self._application_root = Path(application_root).resolve()
        self._runner = runner or OpenSSHProcessRunner()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleeper = sleeper or time.sleep
        self.settings = self._load_settings()
        self._identities = {
            SSHIdentity.ADMIN: self.settings.admin_private_key,
            SSHIdentity.MONITOR: self.settings.monitor_private_key,
        }
        self._trust_store = SSHTrustStore(
            self.settings.known_hosts,
            self.settings.keyscan_executable,
            self._runner,
            scan_timeout_seconds=self.settings.connect_timeout_seconds,
        )
        self._initialized = False

    @property
    def is_initialized(self) -> bool:
        """Return whether configuration, keys, executables, and trust are valid."""
        return self._initialized

    def initialize(self) -> None:
        """Validate local SSH infrastructure without contacting remote hosts."""
        self._initialized = False
        if not self._runtime.is_initialized:
            raise SSHConfigurationError("runtime must be initialized before SSHManager")
        for label, executable in (
            ("ssh", self.settings.ssh_executable),
            ("scp", self.settings.scp_executable),
            ("ssh-keyscan", self.settings.keyscan_executable),
        ):
            try:
                validate_executable(executable, label=label)
            except SSHValidationError as exc:
                raise SSHExecutableError(str(exc)) from exc
        for identity in SSHIdentity:
            self._identity_path(identity)
        self._trust_store.initialize()
        self._initialized = True
        self._logger.bind(operation="initialize").info(
            "SSH foundation initialized"
        )

    def inspect_host_key(self, target: SSHConnectionTarget) -> SSHTrustResult:
        """Inspect presented and trusted host keys without changing trust."""
        self._require_initialized()
        result = self._trust_store.inspect(target)
        self._log_trust(target, "inspect_trust", result.status)
        return result

    def create_target(
        self,
        host: str,
        username: str,
        *,
        port: int | None = None,
        server_uuid: UUID | str | None = None,
    ) -> SSHConnectionTarget:
        """Create a target using the configured default port when omitted."""
        return SSHConnectionTarget(
            host,
            username,
            self.settings.default_port if port is None else port,
            server_uuid,  # type: ignore[arg-type]
        )

    def identity_available(self, identity: SSHIdentity) -> bool:
        """Return whether one configured identity passes local key validation."""
        self._require_initialized()
        return self._identity_available(identity)

    def trust_host_key(
        self, target: SSHConnectionTarget, expected_fingerprint: str
    ) -> SSHTrustResult:
        """Explicitly trust one currently presented key for an unknown host."""
        self._require_initialized()
        result = self._trust_store.trust(target, expected_fingerprint)
        self._log_trust(target, "trust_host", result.status)
        return result

    def replace_host_key(
        self, target: SSHConnectionTarget, expected_fingerprint: str
    ) -> SSHTrustResult:
        """Explicitly replace changed trust after fingerprint confirmation."""
        self._require_initialized()
        result = self._trust_store.replace(target, expected_fingerprint)
        self._log_trust(target, "replace_trust", result.status)
        return result

    def remove_host_trust(self, target: SSHConnectionTarget) -> int:
        """Explicitly remove all trusted key types for a host."""
        self._require_initialized()
        removed = self._trust_store.remove(target)
        self._logger_for(target, operation="remove_trust").info(
            "SSH host trust removed key_count=%d", removed
        )
        return removed

    def list_trusted_keys(self, target: SSHConnectionTarget):
        """List safe public host keys and fingerprints for one host."""
        self._require_initialized()
        return self._trust_store.list_for_host(target)

    def run(
        self,
        request: SSHCommandRequest,
        *,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> SSHCommandResult:
        """Execute one structured command with strict trust and bounded output."""
        self._require_initialized()
        if not isinstance(request, SSHCommandRequest):
            raise SSHValidationError("command request is invalid")
        timeout = request.timeout_seconds or self.settings.command_timeout_seconds
        arguments = self._ssh_arguments(request.target, request.identity)
        arguments.append(f"{request.target.username}@{request.target.host}")
        arguments.append(shlex.join(request.command))
        attempts = 0
        while True:
            attempts += 1
            started = self._clock()
            outcome = self._runner.run(
                arguments,
                timeout_seconds=timeout,
                max_stdout_bytes=self.settings.max_stdout_bytes,
                max_stderr_bytes=self.settings.max_stderr_bytes,
                cancellation_requested=cancellation_requested,
            )
            finished = self._clock()
            failure = _classify(outcome)
            if (
                failure
                not in {
                    SSHFailureType.CONNECTION_REFUSED,
                    SSHFailureType.CONNECTION_TIMEOUT,
                }
                or attempts > self.settings.retry_count
            ):
                break
            self._sleeper(self.settings.retry_delay_seconds)
        stdout, stderr = self._safe_output(outcome)
        result = SSHCommandResult(
            target=request.target,
            exit_code=outcome.exit_code,
            stdout=stdout,
            stderr=stderr,
            started_at=started,
            finished_at=finished,
            duration_seconds=outcome.duration_seconds,
            timed_out=outcome.timed_out,
            stdout_truncated=outcome.stdout_truncated,
            stderr_truncated=outcome.stderr_truncated,
            failure_type=failure,
            attempts=attempts,
            correlation_id=request.correlation_id,
        )
        logger = self._logger_for(
            request.target,
            operation="run",
            identity=request.identity,
            correlation_id=request.correlation_id,
        )
        logger.info(
            "SSH command completed exit_code=%s duration=%.3f timed_out=%s "
            "failure_type=%s attempts=%d",
            result.exit_code,
            result.duration_seconds,
            result.timed_out,
            result.failure_type.value,
            attempts,
        )
        return result

    def transfer(self, request: SSHFileTransferRequest) -> SSHFileTransferResult:
        """Transfer one regular file without recursion or permission escalation."""
        self._require_initialized()
        if not isinstance(request, SSHFileTransferRequest):
            raise SSHValidationError("file-transfer request is invalid")
        self._validate_local_transfer_path(request)
        arguments = self._scp_arguments(request.target, request.identity)
        transfer_host = (
            f"[{request.target.host}]"
            if ":" in request.target.host
            else request.target.host
        )
        remote = f"{request.target.username}@{transfer_host}:{request.remote_path}"
        if request.direction is SSHTransferDirection.UPLOAD:
            arguments.extend((str(request.local_path), remote))
        else:
            arguments.extend((remote, str(request.local_path)))
        timeout = request.timeout_seconds or self.settings.command_timeout_seconds
        started = self._clock()
        outcome = self._runner.run(
            arguments,
            timeout_seconds=timeout,
            max_stdout_bytes=0,
            max_stderr_bytes=self.settings.max_stderr_bytes,
        )
        finished = self._clock()
        failure = _classify(outcome)
        result = SSHFileTransferResult(
            request=request,
            exit_code=outcome.exit_code,
            started_at=started,
            finished_at=finished,
            duration_seconds=outcome.duration_seconds,
            timed_out=outcome.timed_out,
            failure_type=failure,
        )
        self._logger_for(
            request.target,
            operation="transfer",
            identity=request.identity,
            correlation_id=request.correlation_id,
        ).info(
            "SSH file transfer completed exit_code=%s duration=%.3f "
            "timed_out=%s failure_type=%s",
            result.exit_code,
            result.duration_seconds,
            result.timed_out,
            result.failure_type.value,
        )
        return result

    def diagnose(self, target: SSHConnectionTarget) -> SSHDiagnosticResult:
        """Inspect trust and, only when trusted, test monitor authentication."""
        self._require_initialized()
        admin = self._identity_available(SSHIdentity.ADMIN)
        monitor = self._identity_available(SSHIdentity.MONITOR)
        trust = self.inspect_host_key(target)
        reachable = trust.status is not SSHTrustStatus.UNREACHABLE
        authentication: bool | None = None
        command: bool | None = None
        if trust.status is SSHTrustStatus.TRUSTED and monitor:
            result = self.run(
                SSHCommandRequest(
                    target=target,
                    command=("true",),
                    identity=SSHIdentity.MONITOR,
                )
            )
            command = result.succeeded
            authentication = (
                False
                if result.failure_type is SSHFailureType.AUTHENTICATION_FAILED
                else True
                if result.failure_type
                in {
                    SSHFailureType.NONE,
                    SSHFailureType.REMOTE_NONZERO_EXIT,
                    SSHFailureType.OUTPUT_LIMIT_EXCEEDED,
                }
                else None
            )
        return SSHDiagnosticResult(
            target=target,
            target_valid=True,
            admin_identity_available=admin,
            monitor_identity_available=monitor,
            trust_store_writable=os.access(self.settings.known_hosts, os.W_OK),
            trust_status=trust.status,
            reachable=reachable,
            authentication_succeeded=authentication,
            command_succeeded=command,
        )

    def _load_settings(self) -> SSHSettings:
        try:
            root = self._resolve(self._config.require("ssh.credential_directory", str))
            admin = self._resolve(self._config.require("ssh.admin_private_key", str))
            monitor = self._resolve(
                self._config.require("ssh.monitor_private_key", str)
            )
            known_hosts = self._resolve(self._config.require("ssh.known_hosts", str))
            ssh = self._resolve(self._config.require("ssh.ssh_executable", str))
            scp = self._resolve(self._config.require("ssh.scp_executable", str))
            keyscan = self._resolve(self._config.require("ssh.keyscan_executable", str))
            strict = self._config.require("ssh.strict_host_key_checking", bool)
            methods = self._config.require("ssh.authentication_methods", list)
            settings = SSHSettings(
                credential_root=root,
                admin_private_key=admin,
                monitor_private_key=monitor,
                known_hosts=known_hosts,
                ssh_executable=ssh,
                scp_executable=scp,
                keyscan_executable=keyscan,
                connect_timeout_seconds=self._number("ssh.connect_timeout_seconds"),
                command_timeout_seconds=self._number("ssh.command_timeout_seconds"),
                max_stdout_bytes=self._bounded_int(
                    "ssh.max_stdout_bytes", maximum=16 * 1024 * 1024
                ),
                max_stderr_bytes=self._bounded_int(
                    "ssh.max_stderr_bytes", maximum=4 * 1024 * 1024
                ),
                default_port=self._port(),
                authentication_methods=self._methods(methods),
                retry_count=self._nonnegative_int("ssh.retry_count"),
                retry_delay_seconds=self._nonnegative_number("ssh.retry_delay_seconds"),
                keepalive_interval_seconds=self._bounded_int(
                    "ssh.keepalive_interval_seconds", maximum=3600
                ),
                keepalive_count=self._bounded_int("ssh.keepalive_count", maximum=100),
            )
        except (ConfigError, SSHValidationError, ValueError) as exc:
            raise SSHConfigurationError(
                f"invalid SSH configuration: {type(exc).__name__}"
            ) from exc
        if strict is not True:
            raise SSHConfigurationError("strict SSH host-key checking is mandatory")
        try:
            known_hosts.relative_to(self._runtime.paths.data)
        except ValueError as exc:
            raise SSHConfigurationError(
                "SSH known_hosts must be inside the runtime data directory"
            ) from exc
        if known_hosts.parent != self._runtime.paths.data:
            raise SSHConfigurationError(
                "SSH known_hosts must be directly inside the runtime data directory"
            )
        return settings

    def _resolve(self, value: str) -> Path:
        if not value.strip():
            raise SSHConfigurationError("SSH path cannot be empty")
        path = Path(value)
        return (
            (self._application_root / path).absolute()
            if not path.is_absolute()
            else path.absolute()
        )

    def _number(self, key: str) -> float:
        return normalize_timeout(self._config.require(key), field=key)

    def _nonnegative_number(self, key: str) -> float:
        value = self._config.require(key)
        if type(value) not in (int, float) or value < 0 or value > 3600:
            raise SSHConfigurationError(f"{key} must be a non-negative number")
        return float(value)

    def _positive_int(self, key: str) -> int:
        value = self._config.require(key)
        if type(value) is not int or not 1 <= value <= 2**31 - 1:
            raise SSHConfigurationError(f"{key} must be a positive integer")
        return value

    def _bounded_int(self, key: str, *, maximum: int) -> int:
        value = self._positive_int(key)
        if value > maximum:
            raise SSHConfigurationError(f"{key} exceeds its safe maximum")
        return value

    def _nonnegative_int(self, key: str) -> int:
        value = self._config.require(key)
        if type(value) is not int or not 0 <= value <= 10:
            raise SSHConfigurationError(f"{key} must be from 0 to 10")
        return value

    def _port(self) -> int:
        from .validation import normalize_port

        return normalize_port(self._config.require("ssh.port"))

    @staticmethod
    def _methods(values: list[object]) -> tuple[SSHAuthenticationMethod, ...]:
        try:
            methods = tuple(SSHAuthenticationMethod(value) for value in values)
        except (TypeError, ValueError) as exc:
            raise SSHConfigurationError(
                "SSH authentication methods are invalid"
            ) from exc
        if methods != (SSHAuthenticationMethod.PUBLIC_KEY,):
            raise SSHConfigurationError(
                "only public-key SSH authentication is permitted"
            )
        return methods

    def _identity_path(self, identity: SSHIdentity) -> Path:
        try:
            path = self._identities[identity]
        except (KeyError, TypeError) as exc:
            raise SSHIdentityError("SSH identity reference is invalid") from exc
        return validate_private_key(path, self.settings.credential_root)

    def _identity_available(self, identity: SSHIdentity) -> bool:
        try:
            self._identity_path(identity)
        except SSHIdentityError:
            return False
        return True

    def _ssh_arguments(
        self, target: SSHConnectionTarget, identity: SSHIdentity
    ) -> list[str]:
        return [
            str(self.settings.ssh_executable),
            *self._common_options(target, identity),
            "-p",
            str(target.port),
        ]

    def _scp_arguments(
        self, target: SSHConnectionTarget, identity: SSHIdentity
    ) -> list[str]:
        return [
            str(self.settings.scp_executable),
            *self._common_options(target, identity),
            "-P",
            str(target.port),
        ]

    def _common_options(
        self, target: SSHConnectionTarget, identity: SSHIdentity
    ) -> list[str]:
        key = self._identity_path(identity)
        return [
            "-F",
            "/dev/null",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self.settings.known_hosts}",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            f"ConnectTimeout={max(1, int(self.settings.connect_timeout_seconds))}",
            "-o",
            f"ServerAliveInterval={self.settings.keepalive_interval_seconds}",
            "-o",
            f"ServerAliveCountMax={self.settings.keepalive_count}",
            "-o",
            "LogLevel=ERROR",
            "-i",
            str(key),
        ]

    def _safe_output(self, outcome: ProcessOutcome) -> tuple[str, str]:
        stdout = outcome.stdout.decode("utf-8", errors="replace")
        stderr = outcome.stderr.decode("utf-8", errors="replace")
        for path in (*self._identities.values(), self.settings.known_hosts):
            stderr = stderr.replace(str(path), "[REDACTED PATH]")
        return stdout, stderr

    @staticmethod
    def _validate_local_transfer_path(request: SSHFileTransferRequest) -> None:
        path = request.local_path
        if path.is_symlink():
            raise SSHValidationError("local transfer path must not be a symlink")
        if request.direction is SSHTransferDirection.UPLOAD:
            if not path.is_file():
                raise SSHValidationError("upload source must be a regular file")
        elif not path.parent.is_dir() or path.exists() and not path.is_file():
            raise SSHValidationError("download destination is unsafe")

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise SSHManagerError("SSHManager has not been initialized")

    def _logger_for(
        self,
        target: SSHConnectionTarget,
        *,
        operation: str,
        identity: SSHIdentity | None = None,
        correlation_id: str | None = None,
        trust_status: SSHTrustStatus | None = None,
    ) -> SSHLogger:
        return self._logger.bind(
            server_id=str(target.server_uuid) if target.server_uuid else "-",
            server_name=target.host,
            operation=operation,
            correlation_id=correlation_id or "-",
            username=target.username,
            port=target.port,
            identity=identity.value if identity else "-",
            trust_status=trust_status.value if trust_status else "-",
        )

    def _log_trust(
        self,
        target: SSHConnectionTarget,
        operation: str,
        status: SSHTrustStatus,
    ) -> None:
        self._logger_for(target, operation=operation, trust_status=status).info(
            "SSH host trust inspected status=%s", status.value
        )


def _classify(outcome: ProcessOutcome) -> SSHFailureType:
    if outcome.cancelled:
        return SSHFailureType.CANCELLED
    if outcome.timed_out:
        return SSHFailureType.COMMAND_TIMEOUT
    if outcome.stdout_truncated or outcome.stderr_truncated:
        return SSHFailureType.OUTPUT_LIMIT_EXCEEDED
    if outcome.exit_code == 0:
        return SSHFailureType.NONE
    message = outcome.stderr.decode("utf-8", errors="replace").lower()
    if "remote host identification has changed" in message:
        return SSHFailureType.HOST_KEY_CHANGED
    if "host key verification failed" in message:
        return SSHFailureType.HOST_NOT_TRUSTED
    if "permission denied" in message:
        return SSHFailureType.AUTHENTICATION_FAILED
    if "connection refused" in message:
        return SSHFailureType.CONNECTION_REFUSED
    if (
        "could not resolve hostname" in message
        or "name or service not known" in message
    ):
        return SSHFailureType.DNS_RESOLUTION_FAILED
    if "connection timed out" in message or "operation timed out" in message:
        return SSHFailureType.CONNECTION_TIMEOUT
    if outcome.exit_code == 255:
        return SSHFailureType.LOCAL_PROCESS_FAILURE
    return SSHFailureType.REMOTE_NONZERO_EXIT
