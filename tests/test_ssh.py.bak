from __future__ import annotations

import base64
import stat
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml

import app.__main__ as app_main
from app.config import ConfigManager
from app.runtime import RuntimeManager
from app.ssh import (
    SSHCommandRequest,
    SSHConfigurationError,
    SSHConnectionTarget,
    SSHExecutableError,
    SSHFailureType,
    SSHFileTransferRequest,
    SSHFingerprintMismatchError,
    SSHIdentity,
    SSHIdentityError,
    SSHManager,
    SSHManagerError,
    SSHTransferDirection,
    SSHTrustStatus,
    SSHTrustStoreError,
    SSHValidationError,
)
from app.ssh.command import OpenSSHProcessRunner, ProcessOutcome
from tests.helpers import write_yaml

NOW = datetime(2026, 4, 5, 6, 7, 8, tzinfo=UTC)
KEY_ONE = base64.b64encode(b"synthetic-host-key-one").decode()
KEY_TWO = base64.b64encode(b"synthetic-host-key-two").decode()


class LoggerDouble:
    def __init__(self) -> None:
        self.records: list[tuple[dict[str, Any], object, tuple[object, ...]]] = []
        self.context: dict[str, Any] = {}

    def bind(self, **context: Any) -> LoggerDouble:
        result = LoggerDouble()
        result.records = self.records
        result.context = self.context | context
        return result

    def info(self, message: object, *args: object, **kwargs: object) -> None:
        self.records.append((self.context, message, args))

    def warning(self, message: object, *args: object, **kwargs: object) -> None:
        self.records.append((self.context, message, args))


class RunnerDouble:
    def __init__(self, outcome: ProcessOutcome | None = None) -> None:
        self.outcome = outcome or process_outcome()
        self.outcomes: list[ProcessOutcome] = []
        self.arguments: list[tuple[str, ...]] = []
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        max_stdout_bytes: int,
        max_stderr_bytes: int,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> ProcessOutcome:
        self.arguments.append(tuple(arguments))
        self.calls.append(
            {
                "timeout": timeout_seconds,
                "stdout_limit": max_stdout_bytes,
                "stderr_limit": max_stderr_bytes,
                "cancel": cancellation_requested,
            }
        )
        return self.outcomes.pop(0) if self.outcomes else self.outcome


def process_outcome(
    *,
    exit_code: int | None = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    timed_out: bool = False,
    cancelled: bool = False,
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
) -> ProcessOutcome:
    return ProcessOutcome(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        cancelled=cancelled,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        duration_seconds=0.25,
    )


def base_config(tmp_path: Path) -> dict[str, object]:
    executable = tmp_path / "bin/openssh"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o500)
    credential_root = tmp_path / "ssh"
    credential_root.mkdir()
    for name in ("admin", "monitor"):
        key = credential_root / name
        key.write_text("synthetic private key", encoding="utf-8")
        key.chmod(0o400)
    return {
        "paths": {
            "runtime": str(tmp_path / "runtime"),
            "data": str(tmp_path / "runtime/data"),
            "jobs": str(tmp_path / "runtime/jobs"),
            "logs": str(tmp_path / "runtime/logs"),
            "backups": str(tmp_path / "runtime/backups"),
        },
        "logging": {"level": "INFO"},
        "database": {},
        "ssh": {
            "credential_directory": str(credential_root),
            "admin_private_key": str(credential_root / "admin"),
            "monitor_private_key": str(credential_root / "monitor"),
            "known_hosts": str(tmp_path / "runtime/data/known_hosts"),
            "connect_timeout_seconds": 2,
            "command_timeout_seconds": 5,
            "max_stdout_bytes": 32,
            "max_stderr_bytes": 32,
            "port": 22,
            "strict_host_key_checking": True,
            "authentication_methods": ["publickey"],
            "retry_count": 1,
            "retry_delay_seconds": 0,
            "keepalive_interval_seconds": 15,
            "keepalive_count": 3,
            "ssh_executable": str(executable),
            "scp_executable": str(executable),
            "keyscan_executable": str(executable),
        },
    }


def build_manager(
    tmp_path: Path,
    *,
    changes: dict[str, object] | None = None,
    runner: RunnerDouble | None = None,
    initialize: bool = True,
) -> tuple[SSHManager, RunnerDouble, LoggerDouble, dict[str, object]]:
    data = base_config(tmp_path)
    ssh = data["ssh"]
    assert isinstance(ssh, dict)
    if changes:
        ssh.update(changes)
    default = tmp_path / "config/default.yml"
    write_yaml(default, data)
    config = ConfigManager(default, tmp_path / "config/local.yml", environ={})
    runtime = RuntimeManager(config, application_root=tmp_path)
    runtime.initialize()
    actual_runner = runner or RunnerDouble()
    logger = LoggerDouble()
    manager = SSHManager(
        config,
        runtime,
        logger,
        application_root=tmp_path,
        runner=actual_runner,  # type: ignore[arg-type]
        clock=lambda: NOW,
        sleeper=lambda _delay: None,
    )
    if initialize:
        manager.initialize()
    return manager, actual_runner, logger, data


def target(host: str = "host.example.test", port: int = 22) -> SSHConnectionTarget:
    return SSHConnectionTarget(
        host,
        "lim_monitor",
        port,
        UUID("11111111-1111-4111-8111-111111111111"),
    )


def scan(*keys: tuple[str, str]) -> ProcessOutcome:
    content = "".join(
        f"host.example.test {algorithm} {key}\n" for algorithm, key in keys
    )
    return process_outcome(stdout=content.encode())


def test_initialization_is_idempotent_and_never_contacts_hosts(tmp_path: Path) -> None:
    manager, runner, logger, _ = build_manager(tmp_path)
    manager.initialize()
    assert manager.is_initialized
    assert runner.arguments == []
    assert stat.S_IMODE(manager.settings.known_hosts.stat().st_mode) == 0o600
    assert manager.identity_available(SSHIdentity.ADMIN)
    assert manager.identity_available(SSHIdentity.MONITOR)
    manager.settings.monitor_private_key.unlink()
    assert not manager.identity_available(SSHIdentity.MONITOR)
    assert len(logger.records) == 2


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"strict_host_key_checking": False}, SSHConfigurationError),
        ({"port": 0}, SSHConfigurationError),
        ({"connect_timeout_seconds": 0}, SSHConfigurationError),
        ({"command_timeout_seconds": -1}, SSHConfigurationError),
        ({"authentication_methods": ["password"]}, SSHConfigurationError),
        ({"retry_count": 11}, SSHConfigurationError),
        ({"max_stdout_bytes": 0}, SSHConfigurationError),
        ({"max_stdout_bytes": 20 * 1024 * 1024}, SSHConfigurationError),
        ({"max_stderr_bytes": 5 * 1024 * 1024}, SSHConfigurationError),
        ({"keepalive_count": 101}, SSHConfigurationError),
    ],
)
def test_invalid_configuration_fails_closed(
    tmp_path: Path, changes: dict[str, object], error: type[Exception]
) -> None:
    with pytest.raises(error):
        build_manager(tmp_path, changes=changes)


def test_missing_and_unsafe_executables_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(SSHExecutableError):
        build_manager(tmp_path / "missing", changes={"ssh_executable": "/missing/ssh"})
    executable = tmp_path / "unsafe/bin/ssh"
    executable.parent.mkdir(parents=True)
    executable.write_text("not executable", encoding="utf-8")
    with pytest.raises(SSHExecutableError):
        build_manager(tmp_path / "unsafe", changes={"ssh_executable": str(executable)})


@pytest.mark.parametrize("mode", [0o600, 0o440, 0o404])
def test_writable_or_shared_private_keys_are_rejected(
    tmp_path: Path, mode: int
) -> None:
    manager, _, _, data = build_manager(tmp_path, initialize=False)
    ssh = data["ssh"]
    assert isinstance(ssh, dict)
    Path(str(ssh["admin_private_key"])).chmod(mode)
    with pytest.raises(SSHIdentityError):
        manager.initialize()


def test_missing_symlink_and_outside_private_keys_are_rejected(tmp_path: Path) -> None:
    manager, _, _, data = build_manager(tmp_path / "missing", initialize=False)
    ssh = data["ssh"]
    assert isinstance(ssh, dict)
    Path(str(ssh["admin_private_key"])).unlink()
    with pytest.raises(SSHIdentityError):
        manager.initialize()

    root = tmp_path / "symlink"
    manager, _, _, data = build_manager(root, initialize=False)
    ssh = data["ssh"]
    assert isinstance(ssh, dict)
    key = Path(str(ssh["admin_private_key"]))
    key.unlink()
    key.symlink_to(Path(str(ssh["monitor_private_key"])))
    with pytest.raises(SSHIdentityError):
        manager.initialize()

    outside = tmp_path / "outside-key"
    outside.write_text("synthetic", encoding="utf-8")
    outside.chmod(0o400)
    with pytest.raises(SSHIdentityError, match="outside"):
        build_manager(tmp_path / "outside", changes={"admin_private_key": str(outside)})


def test_known_hosts_must_be_contained_writable_and_not_symlinked(
    tmp_path: Path,
) -> None:
    with pytest.raises(SSHConfigurationError, match="inside"):
        build_manager(
            tmp_path / "outside", changes={"known_hosts": str(tmp_path / "known_hosts")}
        )
    root = tmp_path / "symlink"
    manager, _, _, _ = build_manager(root)
    manager.settings.known_hosts.unlink()
    manager.settings.known_hosts.symlink_to(root / "target")
    with pytest.raises(SSHTrustStoreError):
        manager.initialize()

    manager, _, _, _ = build_manager(tmp_path / "writable-parent")
    manager.settings.known_hosts.parent.chmod(0o770)
    with pytest.raises(SSHTrustStoreError, match="group or world writes"):
        manager.initialize()

    manager, _, _, _ = build_manager(tmp_path / "post-init-symlink")
    outside = tmp_path / "outside-known-hosts"
    outside.write_text("", encoding="utf-8")
    manager.settings.known_hosts.unlink()
    manager.settings.known_hosts.symlink_to(outside)
    with pytest.raises(SSHTrustStoreError, match="regular file"):
        manager.list_trusted_keys(target())

    manager, _, _, _ = build_manager(tmp_path / "oversized-trust")
    manager.settings.known_hosts.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(SSHTrustStoreError, match="size limit"):
        manager.list_trusted_keys(target())


def test_target_command_and_transfer_validation(tmp_path: Path) -> None:
    with pytest.raises(SSHValidationError):
        target("bad host")
    with pytest.raises(SSHValidationError):
        SSHConnectionTarget("host", "user;id")
    with pytest.raises(SSHValidationError):
        target(port=65536)
    with pytest.raises(SSHValidationError):
        SSHCommandRequest(target(), (), SSHIdentity.ADMIN)
    with pytest.raises(SSHValidationError):
        SSHCommandRequest(target(), ("echo", "bad\narg"), SSHIdentity.ADMIN)
    with pytest.raises(SSHValidationError, match="too many"):
        SSHCommandRequest(target(), ("echo",) * 257, SSHIdentity.ADMIN)
    with pytest.raises(SSHValidationError):
        SSHFileTransferRequest(
            target(),
            Path("relative"),
            "/tmp/file",
            SSHTransferDirection.UPLOAD,
            SSHIdentity.ADMIN,
        )


def test_manager_target_factory_uses_configured_port(tmp_path: Path) -> None:
    manager, _, _, _ = build_manager(tmp_path, changes={"port": 2222})
    assert manager.create_target("host.example.test", "lim").port == 2222
    assert manager.create_target("host.example.test", "lim", port=2200).port == 2200
    with pytest.raises(SSHValidationError):
        SSHFileTransferRequest(
            target(),
            tmp_path / "file",
            "/tmp/a;id",
            SSHTransferDirection.UPLOAD,
            SSHIdentity.ADMIN,
        )


def test_unknown_trusted_changed_multiple_and_removed_host_keys(tmp_path: Path) -> None:
    runner = RunnerDouble(scan(("ssh-ed25519", KEY_ONE), ("ssh-rsa", KEY_TWO)))
    manager, _, _, _ = build_manager(tmp_path, runner=runner)
    unknown = manager.inspect_host_key(target())
    assert unknown.status is SSHTrustStatus.UNKNOWN
    assert len(unknown.presented_keys) == 2

    trusted = manager.trust_host_key(target(), unknown.presented_keys[0].fingerprint)
    assert trusted.status is SSHTrustStatus.TRUSTED
    assert len(manager.list_trusted_keys(target())) == 1
    assert manager.settings.known_hosts.read_text().count("\n") == 1

    runner.outcome = scan(("ssh-ed25519", KEY_TWO))
    changed = manager.inspect_host_key(target())
    assert changed.status is SSHTrustStatus.CHANGED
    replaced = manager.replace_host_key(target(), changed.presented_keys[0].fingerprint)
    assert replaced.status is SSHTrustStatus.TRUSTED
    assert manager.remove_host_trust(target()) == 1
    assert manager.remove_host_trust(target()) == 0


def test_trust_requires_matching_presented_fingerprint(tmp_path: Path) -> None:
    manager, _, _, _ = build_manager(
        tmp_path, runner=RunnerDouble(scan(("ssh-ed25519", KEY_ONE)))
    )
    with pytest.raises(SSHFingerprintMismatchError):
        manager.trust_host_key(target(), "SHA256:not-presented")
    assert manager.settings.known_hosts.read_text() == ""


def test_trust_rolls_back_if_presented_key_changes_during_confirmation(
    tmp_path: Path,
) -> None:
    runner = RunnerDouble()
    runner.outcomes = [
        scan(("ssh-ed25519", KEY_ONE)),
        scan(("ssh-ed25519", KEY_ONE)),
        scan(("ssh-ed25519", KEY_TWO)),
    ]
    manager, _, _, _ = build_manager(tmp_path, runner=runner)
    fingerprint = manager.inspect_host_key(target()).presented_keys[0].fingerprint
    with pytest.raises(SSHTrustStoreError, match="changed during"):
        manager.trust_host_key(target(), fingerprint)
    assert manager.settings.known_hosts.read_text() == ""


def test_trust_store_write_failure_preserves_original_and_cleans_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _, _, _ = build_manager(
        tmp_path, runner=RunnerDouble(scan(("ssh-ed25519", KEY_ONE)))
    )
    fingerprint = manager.inspect_host_key(target()).presented_keys[0].fingerprint

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr("app.ssh.trust.os.replace", fail_replace)
    with pytest.raises(SSHTrustStoreError, match="update failed"):
        manager.trust_host_key(target(), fingerprint)
    assert manager.settings.known_hosts.read_text() == ""
    assert not list(manager.settings.known_hosts.parent.glob(".known-hosts-*"))


@pytest.mark.parametrize(
    "outcome",
    [
        process_outcome(stdout=b"malformed\n"),
        process_outcome(stdout=b"host ssh-ed25519 not-base64\n"),
        process_outcome(stdout=b"x" * 10, stdout_truncated=True),
        process_outcome(timed_out=True),
        process_outcome(exit_code=2),
    ],
)
def test_scan_failures_are_safe(tmp_path: Path, outcome: ProcessOutcome) -> None:
    manager, _, _, _ = build_manager(tmp_path, runner=RunnerDouble(outcome))
    with pytest.raises(SSHTrustStoreError):
        manager.inspect_host_key(target())


def test_trust_updates_are_atomic_concurrent_and_restrictive(tmp_path: Path) -> None:
    manager, _, _, _ = build_manager(
        tmp_path, runner=RunnerDouble(scan(("ssh-ed25519", KEY_ONE)))
    )
    fingerprints = manager.inspect_host_key(target()).presented_keys[0].fingerprint
    targets = [target(f"host-{index}.example.test") for index in range(5)]
    errors: list[Exception] = []

    def trust(item: SSHConnectionTarget) -> None:
        try:
            manager.trust_host_key(item, fingerprints)
        except Exception as exc:  # pragma: no cover - asserted empty
            errors.append(exc)

    threads = [threading.Thread(target=trust, args=(item,)) for item in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(manager.settings.known_hosts.read_text().splitlines()) == 5
    assert stat.S_IMODE(manager.settings.known_hosts.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("outcome", "failure"),
    [
        (process_outcome(stdout=b"ok\n"), SSHFailureType.NONE),
        (
            process_outcome(exit_code=7, stderr=b"remote failed"),
            SSHFailureType.REMOTE_NONZERO_EXIT,
        ),
        (
            process_outcome(exit_code=255, stderr=b"Permission denied"),
            SSHFailureType.AUTHENTICATION_FAILED,
        ),
        (
            process_outcome(exit_code=255, stderr=b"Connection refused"),
            SSHFailureType.CONNECTION_REFUSED,
        ),
        (
            process_outcome(exit_code=255, stderr=b"Could not resolve hostname"),
            SSHFailureType.DNS_RESOLUTION_FAILED,
        ),
        (
            process_outcome(exit_code=255, stderr=b"Host key verification failed"),
            SSHFailureType.HOST_NOT_TRUSTED,
        ),
        (
            process_outcome(
                exit_code=255, stderr=b"REMOTE HOST IDENTIFICATION HAS CHANGED"
            ),
            SSHFailureType.HOST_KEY_CHANGED,
        ),
        (process_outcome(timed_out=True), SSHFailureType.COMMAND_TIMEOUT),
        (process_outcome(cancelled=True), SSHFailureType.CANCELLED),
        (
            process_outcome(stdout=b"x", stdout_truncated=True),
            SSHFailureType.OUTPUT_LIMIT_EXCEEDED,
        ),
        (
            process_outcome(stderr=b"x", stderr_truncated=True),
            SSHFailureType.OUTPUT_LIMIT_EXCEEDED,
        ),
        (
            process_outcome(exit_code=255, stderr=b"Connection timed out"),
            SSHFailureType.CONNECTION_TIMEOUT,
        ),
        (
            process_outcome(exit_code=255, stderr=b"local failure"),
            SSHFailureType.LOCAL_PROCESS_FAILURE,
        ),
    ],
)
def test_command_results_are_typed_and_classified(
    tmp_path: Path, outcome: ProcessOutcome, failure: SSHFailureType
) -> None:
    runner = RunnerDouble(outcome)
    manager, _, _, _ = build_manager(tmp_path, runner=runner)
    result = manager.run(
        SSHCommandRequest(
            target(),
            ("printf", "%s", "a; touch /tmp/not-created"),
            SSHIdentity.ADMIN,
            correlation_id="corr-1",
        )
    )
    assert result.failure_type is failure
    assert result.succeeded is (failure is SSHFailureType.NONE)
    assert runner.arguments[0][-1] == "printf %s 'a; touch /tmp/not-created'"
    assert "StrictHostKeyChecking=yes" in runner.arguments[0]
    assert "PasswordAuthentication=no" in runner.arguments[0]
    assert result.correlation_id == "corr-1"
    assert runner.arguments[0][-2].startswith("lim_monitor@")
    assert runner.arguments[0][-2] != "--"


def test_retry_policy_is_transient_only(tmp_path: Path) -> None:
    runner = RunnerDouble()
    runner.outcomes = [
        process_outcome(exit_code=255, stderr=b"Connection refused"),
        process_outcome(stdout=b"ready"),
    ]
    manager, _, _, _ = build_manager(tmp_path, runner=runner)
    result = manager.run(SSHCommandRequest(target(), ("true",), SSHIdentity.MONITOR))
    assert result.succeeded and result.attempts == 2

    runner.outcomes = [process_outcome(exit_code=255, stderr=b"Permission denied")]
    result = manager.run(SSHCommandRequest(target(), ("true",), SSHIdentity.MONITOR))
    assert result.attempts == 1


def test_outputs_and_logs_are_bounded_and_do_not_expose_identity_paths(
    tmp_path: Path,
) -> None:
    runner = RunnerDouble(process_outcome(stderr=b"identity PLACEHOLDER secret-output"))
    manager, _, logger, data = build_manager(tmp_path, runner=runner)
    ssh = data["ssh"]
    assert isinstance(ssh, dict)
    runner.outcome = process_outcome(
        stderr=f"identity {ssh['admin_private_key']} secret-output".encode()
    )
    result = manager.run(SSHCommandRequest(target(), ("true",), SSHIdentity.ADMIN))
    assert str(ssh["admin_private_key"]) not in result.stderr
    assert "secret-output" not in repr(logger.records)
    assert runner.calls[-1]["stdout_limit"] == 32
    assert runner.calls[-1]["stderr_limit"] == 32


def test_file_transfer_is_nonrecursive_strict_and_content_free(tmp_path: Path) -> None:
    manager, runner, logger, _ = build_manager(tmp_path)
    source = (tmp_path / "payload.txt").absolute()
    source.write_text("sensitive payload", encoding="utf-8")
    request = SSHFileTransferRequest(
        target(),
        source,
        "/var/tmp/payload.txt",
        SSHTransferDirection.UPLOAD,
        SSHIdentity.ADMIN,
    )
    result = manager.transfer(request)
    assert result.succeeded
    assert "-r" not in runner.arguments[-1]
    assert str(source) in runner.arguments[-1]
    assert "sensitive payload" not in repr(result)
    assert "sensitive payload" not in repr(logger.records)


def test_diagnostics_never_modify_unknown_trust(tmp_path: Path) -> None:
    manager, runner, _, _ = build_manager(
        tmp_path, runner=RunnerDouble(scan(("ssh-ed25519", KEY_ONE)))
    )
    before = manager.settings.known_hosts.read_bytes()
    diagnostic = manager.diagnose(target())
    assert diagnostic.trust_status is SSHTrustStatus.UNKNOWN
    assert diagnostic.authentication_succeeded is None
    assert manager.settings.known_hosts.read_bytes() == before
    assert len(runner.arguments) == 1


def test_diagnostics_attempt_monitor_command_only_for_trusted_host(
    tmp_path: Path,
) -> None:
    runner = RunnerDouble(scan(("ssh-ed25519", KEY_ONE)))
    manager, _, _, _ = build_manager(tmp_path, runner=runner)
    key = manager.inspect_host_key(target()).presented_keys[0]
    manager.trust_host_key(target(), key.fingerprint)
    runner.outcomes = [scan(("ssh-ed25519", KEY_ONE)), process_outcome(stdout=b"ok")]
    diagnostic = manager.diagnose(target())
    assert diagnostic.trust_status is SSHTrustStatus.TRUSTED
    assert diagnostic.authentication_succeeded is True
    assert diagnostic.command_succeeded is True


def test_diagnostics_report_missing_keys_unreachable_and_authentication_failure(
    tmp_path: Path,
) -> None:
    runner = RunnerDouble(process_outcome(exit_code=1))
    manager, _, _, _ = build_manager(tmp_path, runner=runner)
    manager.settings.admin_private_key.unlink()
    diagnostic = manager.diagnose(target())
    assert not diagnostic.admin_identity_available
    assert diagnostic.trust_status is SSHTrustStatus.UNREACHABLE
    assert diagnostic.reachable is False

    root = tmp_path / "auth"
    runner = RunnerDouble(scan(("ssh-ed25519", KEY_ONE)))
    manager, _, _, _ = build_manager(root, runner=runner)
    key = manager.inspect_host_key(target()).presented_keys[0]
    manager.trust_host_key(target(), key.fingerprint)
    runner.outcomes = [
        scan(("ssh-ed25519", KEY_ONE)),
        process_outcome(exit_code=255, stderr=b"Permission denied"),
    ]
    diagnostic = manager.diagnose(target())
    assert diagnostic.authentication_succeeded is False
    assert diagnostic.command_succeeded is False


def test_manager_rejects_operations_before_initialization(tmp_path: Path) -> None:
    manager, _, _, _ = build_manager(tmp_path, initialize=False)
    with pytest.raises(SSHManagerError, match="not been initialized"):
        manager.inspect_host_key(target())


def test_startup_reports_sanitized_ssh_initialization_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Runtime:
        def __init__(self, config: object, *, application_root: Path) -> None:
            pass

        def initialize(self) -> None:
            pass

    class Logger:
        def info(self, message: str, *args: object) -> None:
            pass

        def exception(self, message: str) -> None:
            events.append(message)

    class Logging:
        def __init__(self, config: object, runtime: object) -> None:
            pass

        def initialize(self) -> None:
            pass

        def get_logger(self, component: str, **context: object) -> Logger:
            return Logger()

    class Database:
        def __init__(self, config: object, runtime: object) -> None:
            pass

        def initialize(self) -> None:
            pass

    class MigrationState:
        schema_version = 3

    class Migration:
        def __init__(self, database: object) -> None:
            pass

        def apply_pending(self) -> MigrationState:
            return MigrationState()

    class FailingSSH:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def initialize(self) -> None:
            raise SSHIdentityError("private key contents: synthetic-secret")

    monkeypatch.setattr(app_main, "ConfigManager", object)
    monkeypatch.setattr(app_main, "RuntimeManager", Runtime)
    monkeypatch.setattr(app_main, "LoggingManager", Logging)
    monkeypatch.setattr(app_main, "DatabaseManager", Database)
    monkeypatch.setattr(app_main, "MigrationManager", Migration)
    monkeypatch.setattr(app_main, "SSHManager", FailingSSH)

    assert app_main.main() == 1
    assert events == ["LIM SSH foundation initialization failed"]


def test_process_runner_uses_no_shell_and_bounds_both_streams(tmp_path: Path) -> None:
    executable = tmp_path / "emit"
    executable.write_text(
        "#!/bin/sh\nprintf '1234567890'\nprintf 'abcdefghij' >&2\n",
        encoding="utf-8",
    )
    executable.chmod(0o500)
    result = OpenSSHProcessRunner().run(
        (str(executable),),
        timeout_seconds=2,
        max_stdout_bytes=4,
        max_stderr_bytes=5,
    )
    assert result.stdout == b"1234" and result.stdout_truncated
    assert result.stderr == b"abcde" and result.stderr_truncated


def test_process_runner_timeout_and_cancellation(tmp_path: Path) -> None:
    executable = tmp_path / "wait"
    executable.write_text("#!/bin/sh\nsleep 2\n", encoding="utf-8")
    executable.chmod(0o500)
    runner = OpenSSHProcessRunner()
    assert runner.run(
        (str(executable),),
        timeout_seconds=0.02,
        max_stdout_bytes=1,
        max_stderr_bytes=1,
    ).timed_out
    assert runner.run(
        (str(executable),),
        timeout_seconds=2,
        max_stdout_bytes=1,
        max_stderr_bytes=1,
        cancellation_requested=lambda: True,
    ).cancelled
    assert runner.run(
        (str(executable),),
        timeout_seconds=2,
        max_stdout_bytes=1,
        max_stderr_bytes=1,
        cancellation_requested=lambda: (_ for _ in ()).throw(RuntimeError()),
    ).cancelled


def test_process_runner_sanitizes_local_start_failure() -> None:
    with pytest.raises(SSHManagerError, match="could not start"):
        OpenSSHProcessRunner().run(
            ("/definitely/missing/ssh",),
            timeout_seconds=1,
            max_stdout_bytes=1,
            max_stderr_bytes=1,
        )


def test_container_layout_separates_read_only_keys_from_writable_trust() -> None:
    project_root = Path(__file__).resolve().parent.parent
    compose = yaml.safe_load(
        (project_root / "docker-compose.yml").read_text(encoding="utf-8")
    )
    service = compose["services"]["lim"]
    assert service["volumes"][0] == "./runtime:/opt/lim/runtime"
    key_mounts = service["volumes"][1:]
    assert {item["target"] for item in key_mounts} == {
        "/opt/lim/ssh/lim_admin_ed25519",
        "/opt/lim/ssh/monitor_ed25519",
        "/opt/lim/ssh/monitor_ed25519.pub",
    }
    assert all(item["read_only"] is True for item in key_mounts)
    dockerfile = (project_root / "Dockerfile").read_text(encoding="utf-8")
    assert "openssh-client" in dockerfile
