"""Deterministic tests for idempotent, SSHManager-only Linux bootstrap."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from app.bootstrap import (
    BootstrapConfigurationError,
    BootstrapFailureType,
    BootstrapRequest,
    BootstrapService,
    BootstrapStepName,
    BootstrapStepStatus,
    BootstrapValidationError,
)
from app.inventory import ServerNotFoundError, ServerStatus
from app.ssh import (
    SSHCommandRequest,
    SSHCommandResult,
    SSHConnectionTarget,
    SSHFailureType,
    SSHFileTransferRequest,
    SSHFileTransferResult,
    SSHIdentity,
    SSHTrustResult,
    SSHTrustStatus,
)
from tests.helpers import make_inventory_server, write_yaml

NOW = datetime(2026, 8, 5, 14, 0, tzinfo=UTC)
NONCE = UUID("44444444-4444-4444-8444-444444444444")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = PROJECT_ROOT / "app/bootstrap/artifacts/remote_health.py"


class RecordingLogger:
    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []
        self.records: list[tuple[str, str, tuple[object, ...]]] = []

    def bind(self, **context: Any) -> RecordingLogger:
        self.contexts.append(context)
        return self

    def info(self, message: object, *args: object, **kwargs: object) -> None:
        self.records.append(("info", str(message), args))

    def warning(self, message: object, *args: object, **kwargs: object) -> None:
        self.records.append(("warning", str(message), args))


class FakeInventoryService:
    def __init__(self, server: object | None = None) -> None:
        self.server = server if server is not None else make_inventory_server()
        self.bootstrap_calls = 0

    def get_server(self, server_uuid: UUID):
        if self.server is None or self.server.uuid != server_uuid:
            raise ServerNotFoundError("not found")
        if self.server.deleted_at is not None:
            raise ServerNotFoundError("not found")
        return self.server

    def record_bootstrap_success(self, server_uuid: UUID):
        server = self.get_server(server_uuid)
        self.bootstrap_calls += 1
        self.server = server.evolve(now=NOW, last_bootstrap_at=NOW)
        return self.server


class FakeSSHManager:
    """Stateful remote-host double for repeat and repair behavior."""

    is_initialized = True

    def __init__(self) -> None:
        self.identities = {SSHIdentity.ADMIN: True, SSHIdentity.MONITOR: True}
        self.trust_statuses = [SSHTrustStatus.TRUSTED]
        self.admin_auth = True
        self.platform = "Linux"
        self.sudo_exit = 0
        self.sudo_failure = SSHFailureType.NONE
        self.account: tuple[str, ...] | None = None
        self.uid = 991
        self.gid = 991
        self.password_status = "L"
        self.groups = ["monitor"]
        self.files: dict[str, bytes] = {}
        self.states: dict[str, dict[str, object]] = {}
        self.requests: list[SSHCommandRequest] = []
        self.transfers: list[SSHFileTransferRequest] = []
        self.upload_failure = False
        self.helper_failure: str | None = None
        self.cleanup_failure = False
        self.monitor_failure = False
        self.missing_utility: str | None = None
        self.collector_output: str | None = None
        self.collector_truncated = False
        self.honor_monitor_command = False
        self.collector_document = {
            "schema_version": 1,
            "collector_version": "1.0.0",
            "host": {"hostname": "server-01.example.test"},
            "services": {
                "mysql": {
                    "installation": "not_installed",
                    "activity": "not_applicable",
                }
            },
        }

    def identity_available(self, identity: SSHIdentity) -> bool:
        return self.identities[identity]

    def create_target(
        self,
        host: str,
        username: str,
        *,
        port: int | None = None,
        server_uuid: UUID | str | None = None,
    ) -> SSHConnectionTarget:
        return SSHConnectionTarget(host, username, port or 22, server_uuid)

    def inspect_host_key(self, target: SSHConnectionTarget) -> SSHTrustResult:
        status = (
            self.trust_statuses.pop(0)
            if len(self.trust_statuses) > 1
            else self.trust_statuses[0]
        )
        return SSHTrustResult(target, status)

    def run(self, request: SSHCommandRequest) -> SSHCommandResult:
        self.requests.append(request)
        if request.identity is SSHIdentity.MONITOR:
            if self.monitor_failure:
                return self._result(
                    request,
                    exit_code=255,
                    failure=SSHFailureType.AUTHENTICATION_FAILED,
                )
            if self.honor_monitor_command and request.command[0] == "printf":
                return self._result(request, stdout=request.command[1])
            return self._result(
                request,
                stdout=self.collector_output
                if self.collector_output is not None
                else json.dumps(self.collector_document) + "\n",
                stdout_truncated=self.collector_truncated,
            )
        command = request.command
        if command == ("true",):
            return self._result(
                request,
                exit_code=0 if self.admin_auth else 255,
                failure=(
                    SSHFailureType.NONE
                    if self.admin_auth
                    else SSHFailureType.AUTHENTICATION_FAILED
                ),
            )
        if command == ("uname", "-s"):
            return self._result(request, stdout=f"{self.platform}\n")
        if len(command) >= 3 and command[1:3] == ("-x", command[2]):
            if command[2] == self.missing_utility:
                return self._result(
                    request,
                    exit_code=1,
                    failure=SSHFailureType.REMOTE_NONZERO_EXIT,
                )
            return self._result(request)
        if command[-2:] == ("-n", "true"):
            return self._result(
                request,
                exit_code=self.sudo_exit,
                failure=self.sudo_failure,
            )
        if "getent" in command[0] and command[1] == "passwd":
            if self.account is None:
                return self._result(
                    request,
                    exit_code=2,
                    failure=SSHFailureType.REMOTE_NONZERO_EXIT,
                )
            return self._result(request, stdout=":".join(self.account) + "\n")
        if any(item.endswith("/useradd") for item in command):
            self.account = (
                "monitor",
                "x",
                str(self.uid),
                str(self.gid),
                "LIM monitoring account",
                "/var/lib/monitor",
                "/bin/sh",
            )
            return self._result(request)
        if any(item.endswith("/usermod") for item in command) and "--home" in command:
            assert self.account is not None
            self.account = (*self.account[:5], "/var/lib/monitor", "/bin/sh")
            return self._result(request)
        if any(item.endswith("/usermod") for item in command) and "--lock" in command:
            self.password_status = "L"
            return self._result(request)
        if command[-2:] == ("-u", "monitor") and "id" in command[0]:
            return self._result(request, stdout=f"{self.uid}\n")
        if command[-2:] == ("-g", "monitor") and "id" in command[0]:
            return self._result(request, stdout=f"{self.gid}\n")
        if command[-2:] == ("-nG", "monitor") and "id" in command[0]:
            return self._result(request, stdout=" ".join(self.groups) + "\n")
        if any(item.endswith("/passwd") for item in command) and "-S" in command:
            return self._result(
                request,
                stdout=f"monitor {self.password_status} 2026-08-05 0 99999 7 -1\n",
            )
        if any(item.endswith("/gpasswd") for item in command) and "--delete" in command:
            self.groups.remove(command[-1])
            return self._result(request)
        if len(command) > 6 and command[1:3] == ("-n", "--") and command[4] == "-c":
            return self._helper_result(request, command[6], command[7:])
        if (
            command[-2:]
            and command[-2] == "--"
            and command[-1].startswith("/tmp/lim-bootstrap-")
        ):
            if self.cleanup_failure:
                return self._result(
                    request,
                    exit_code=1,
                    failure=SSHFailureType.REMOTE_NONZERO_EXIT,
                )
            self.files.pop(command[-1], None)
            return self._result(request)
        if "-u" in command and "/usr/local/libexec/lim/remote-health-json" in command:
            return self._result(
                request,
                stdout=self.collector_output
                if self.collector_output is not None
                else json.dumps(self.collector_document) + "\n",
                stdout_truncated=self.collector_truncated,
            )
        return self._result(request)

    def transfer(self, request: SSHFileTransferRequest) -> SSHFileTransferResult:
        self.transfers.append(request)
        if not self.upload_failure:
            self.files[request.remote_path] = request.local_path.read_bytes()
        return SSHFileTransferResult(
            request=request,
            exit_code=1 if self.upload_failure else 0,
            started_at=NOW,
            finished_at=NOW,
            duration_seconds=0.01,
            timed_out=False,
            failure_type=(
                SSHFailureType.LOCAL_PROCESS_FAILURE
                if self.upload_failure
                else SSHFailureType.NONE
            ),
        )

    def _helper_result(
        self,
        request: SSHCommandRequest,
        action: str,
        arguments: tuple[str, ...],
    ) -> SSHCommandResult:
        if self.helper_failure == action:
            return self._result(
                request,
                exit_code=1,
                failure=SSHFailureType.REMOTE_NONZERO_EXIT,
            )
        changed = False
        document: dict[str, object] = {"ok": True, "changed": False}
        if action == "ensure_dir":
            path, uid, gid, mode = arguments
            desired = {
                "exists": True,
                "kind": "directory",
                "uid": int(uid),
                "gid": int(gid),
                "mode": int(mode, 8),
            }
            changed = self.states.get(path) != desired
            self.states[path] = desired
        elif action == "merge_key":
            staged, destination, uid, gid, mode, marker = arguments
            previous = self.files.get(destination, b"").decode()
            entry = self.files[staged].decode()
            preserved = [line for line in previous.splitlines() if marker not in line]
            content = (
                "\n".join(preserved) + ("\n" if preserved else "") + entry
            ).encode()
            desired = {
                "exists": True,
                "kind": "file",
                "uid": int(uid),
                "gid": int(gid),
                "mode": int(mode, 8),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            changed = (
                self.files.get(destination) != content
                or self.states.get(destination) != desired
            )
            self.files[destination] = content
            self.states[destination] = desired
        elif action == "install":
            staged, destination, uid, gid, mode = arguments
            content = self.files[staged]
            desired = {
                "exists": True,
                "kind": "file",
                "uid": int(uid),
                "gid": int(gid),
                "mode": int(mode, 8),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            changed = (
                self.files.get(destination) != content
                or self.states.get(destination) != desired
            )
            self.files[destination] = content
            self.states[destination] = desired
        elif action == "state":
            document["state"] = self.states.get(arguments[0], {"exists": False})
        document["changed"] = changed
        return self._result(request, stdout=json.dumps(document) + "\n")

    @staticmethod
    def _result(
        request: SSHCommandRequest,
        *,
        stdout: str = "",
        exit_code: int | None = 0,
        failure: SSHFailureType = SSHFailureType.NONE,
        stdout_truncated: bool = False,
    ) -> SSHCommandResult:
        return SSHCommandResult(
            target=request.target,
            exit_code=exit_code,
            stdout=stdout,
            stderr="never expose this remote detail",
            started_at=NOW,
            finished_at=NOW,
            duration_seconds=0.01,
            timed_out=failure is SSHFailureType.COMMAND_TIMEOUT,
            stdout_truncated=stdout_truncated,
            stderr_truncated=False,
            failure_type=failure,
            attempts=1,
            correlation_id=request.correlation_id,
        )


def _settings(
    tmp_path: Path, public_key: Path, **overrides: object
) -> dict[str, object]:
    bootstrap: dict[str, object] = {
        "monitor_username": "monitor",
        "monitor_home": "/var/lib/monitor",
        "monitor_shell": "/bin/sh",
        "collector_path": "/usr/local/libexec/lim/remote-health-json",
        "collector_version": "1.0.0",
        "monitor_public_key": str(public_key),
        "collector_artifact": str(ARTIFACT),
        "authorized_keys_path": "/var/lib/monitor/.ssh/authorized_keys",
        "authorized_key_options": ["restrict"],
        "key_marker": "lim-managed-monitor",
        "remote_temp_directory": "/tmp",
        "account_comment": "LIM monitoring account",
        "forbidden_groups": ["root", "wheel", "sudo", "docker"],
        "directory_mode": "0750",
        "ssh_directory_mode": "0700",
        "authorized_keys_mode": "0600",
        "collector_directory_mode": "0755",
        "collector_mode": "0755",
        "command_timeout_seconds": 30,
        "transfer_timeout_seconds": 30,
        "verification_timeout_seconds": 30,
        "maximum_collector_output_bytes": 262144,
        "supported_collector_schema_versions": [1],
        "utility_paths": {
            "python": "/usr/bin/python3",
            "sudo": "/usr/bin/sudo",
            "getent": "/usr/bin/getent",
            "id": "/usr/bin/id",
            "useradd": "/usr/sbin/useradd",
            "usermod": "/usr/sbin/usermod",
            "gpasswd": "/usr/bin/gpasswd",
            "passwd": "/usr/bin/passwd",
            "test": "/usr/bin/test",
            "rm": "/usr/bin/rm",
        },
    }
    bootstrap.update(overrides)
    return {
        "paths": {
            "runtime": str(tmp_path / "runtime"),
            "data": str(tmp_path / "runtime/data"),
            "jobs": str(tmp_path / "runtime/jobs"),
            "logs": str(tmp_path / "runtime/logs"),
            "backups": str(tmp_path / "runtime/backups"),
        },
        "bootstrap": bootstrap,
    }


def _service(
    tmp_path: Path,
    *,
    ssh: FakeSSHManager | None = None,
    inventory: FakeInventoryService | None = None,
    overrides: dict[str, object] | None = None,
) -> tuple[BootstrapService, FakeSSHManager, FakeInventoryService, RecordingLogger]:
    from app.config import ConfigurationManager
    from app.runtime import RuntimeManager

    tmp_path.mkdir(parents=True, exist_ok=True)
    public_key = tmp_path / "monitor.pub"
    key_body = base64.b64encode(b"synthetic-ed25519-public-key-data").decode()
    public_key.write_text(f"ssh-ed25519 {key_body} test-only\n", encoding="utf-8")
    default = tmp_path / "config/default.yml"
    write_yaml(default, _settings(tmp_path, public_key, **(overrides or {})))
    config = ConfigurationManager(default, tmp_path / "missing.yml", environ={})
    runtime = RuntimeManager(config, application_root=tmp_path)
    runtime.initialize()
    ssh = ssh or FakeSSHManager()
    inventory = inventory or FakeInventoryService()
    logger = RecordingLogger()
    ticks = iter(float(index) / 100 for index in range(10_000))
    service = BootstrapService(
        config,
        runtime,
        ssh,  # type: ignore[arg-type]
        inventory,  # type: ignore[arg-type]
        logger,
        application_root=PROJECT_ROOT,
        clock=lambda: NOW,
        monotonic=lambda: next(ticks),
        uuid_factory=lambda: NONCE,
    )
    service.initialize()
    return service, ssh, inventory, logger


def _request() -> BootstrapRequest:
    return BootstrapRequest(
        make_inventory_server().uuid,
        "deployer",
        correlation_id="bootstrap-test",
    )


def test_first_bootstrap_and_idempotent_repeat_preserve_unrelated_keys(
    tmp_path: Path,
) -> None:
    service, ssh, inventory, _ = _service(tmp_path)
    authorized = "/var/lib/monitor/.ssh/authorized_keys"
    ssh.files[authorized] = (
        b"ssh-ed25519 unrelated unrelated-key\n"
        b'restrict,command="/old" ssh-ed25519 stale lim-managed-monitor\n'
    )
    ssh.states[authorized] = {
        "exists": True,
        "kind": "file",
        "uid": ssh.uid,
        "gid": ssh.gid,
        "mode": 0o644,
    }

    first = service.bootstrap(_request())
    second = service.bootstrap(_request())

    assert first.success and first.changed and first.inventory_updated
    assert [step.name for step in first.steps] == list(BootstrapStepName)
    assert all(step.status is BootstrapStepStatus.SUCCEEDED for step in first.steps)
    assert first.verification == replace(
        first.verification,
        admin_verified=True,
        account_verified=True,
        files_verified=True,
        direct_collector_verified=True,
        monitor_authenticated=True,
        forced_command_verified=True,
        collector_schema_version=1,
    )
    content = ssh.files[authorized].decode()
    assert "unrelated-key" in content
    assert content.count("lim-managed-monitor") == 1
    assert "/old" not in content
    assert "PRIVATE KEY" not in content
    assert second.success and not second.changed and not second.inventory_updated
    assert inventory.bootstrap_calls == 1


@pytest.mark.parametrize(
    ("configure", "failure"),
    [
        (
            lambda ssh, inv: setattr(inv, "server", None),
            BootstrapFailureType.INVENTORY_SERVER_UNAVAILABLE,
        ),
        (
            lambda ssh, inv: setattr(
                inv,
                "server",
                make_inventory_server(enabled=False, status=ServerStatus.DISABLED),
            ),
            BootstrapFailureType.INVENTORY_SERVER_UNAVAILABLE,
        ),
        (
            lambda ssh, inv: ssh.identities.__setitem__(SSHIdentity.ADMIN, False),
            BootstrapFailureType.ADMIN_KEY_MISSING,
        ),
        (
            lambda ssh, inv: ssh.identities.__setitem__(SSHIdentity.MONITOR, False),
            BootstrapFailureType.MONITOR_KEY_MISSING,
        ),
        (
            lambda ssh, inv: ssh.trust_statuses.__setitem__(0, SSHTrustStatus.UNKNOWN),
            BootstrapFailureType.HOST_NOT_TRUSTED,
        ),
        (
            lambda ssh, inv: ssh.trust_statuses.__setitem__(0, SSHTrustStatus.CHANGED),
            BootstrapFailureType.HOST_KEY_CHANGED,
        ),
        (
            lambda ssh, inv: setattr(ssh, "admin_auth", False),
            BootstrapFailureType.ADMIN_AUTHENTICATION_FAILED,
        ),
        (
            lambda ssh, inv: setattr(ssh, "platform", "FreeBSD"),
            BootstrapFailureType.TARGET_NOT_LINUX,
        ),
        (
            lambda ssh, inv: setattr(ssh, "sudo_exit", 1),
            BootstrapFailureType.SUDO_REQUIRES_PASSWORD,
        ),
        (
            lambda ssh, inv: setattr(ssh, "sudo_exit", 127),
            BootstrapFailureType.SUDO_UNAVAILABLE,
        ),
        (
            lambda ssh, inv: setattr(ssh, "missing_utility", "/usr/sbin/useradd"),
            BootstrapFailureType.REQUIRED_UTILITY_MISSING,
        ),
    ],
)
def test_prerequisite_failures_stop_later_steps(
    tmp_path: Path, configure: Any, failure: BootstrapFailureType
) -> None:
    ssh = FakeSSHManager()
    inventory = FakeInventoryService()
    configure(ssh, inventory)
    service, _, _, logs = _service(tmp_path, ssh=ssh, inventory=inventory)

    result = service.bootstrap(_request())

    assert not result.success and result.failure_type is failure
    failed = next(
        step for step in result.steps if step.status is BootstrapStepStatus.FAILED
    )
    assert failed.failure_type is failure
    assert all(
        step.status is BootstrapStepStatus.SKIPPED
        for step in result.steps[result.steps.index(failed) + 1 :]
    )
    assert inventory.bootstrap_calls == 0
    assert "never expose this remote detail" not in repr(logs.records)


def test_account_repair_locks_password_and_removes_privileged_groups(
    tmp_path: Path,
) -> None:
    ssh = FakeSSHManager()
    ssh.account = (
        "monitor",
        "x",
        "991",
        "991",
        "LIM monitoring account",
        "/home/monitor",
        "/bin/bash",
    )
    ssh.password_status = "P"
    ssh.groups = ["monitor", "sudo", "docker"]
    service, _, _, _ = _service(tmp_path, ssh=ssh)

    result = service.bootstrap(_request())

    assert result.success
    assert ssh.account[5:] == ("/var/lib/monitor", "/bin/sh")
    assert ssh.password_status == "L"
    assert ssh.groups == ["monitor"]
    repair = result.steps[6]
    assert repair.name is BootstrapStepName.REPAIR_MONITOR_ACCOUNT
    assert repair.changed


def test_conflicting_existing_account_fails_closed(tmp_path: Path) -> None:
    ssh = FakeSSHManager()
    ssh.account = (
        "monitor",
        "x",
        "991",
        "991",
        "Unrelated account",
        "/var/lib/monitor",
        "/bin/sh",
    )
    service, _, inventory, _ = _service(tmp_path, ssh=ssh)

    result = service.bootstrap(_request())

    assert result.failure_type is BootstrapFailureType.CONFLICTING_ACCOUNT
    assert inventory.bootstrap_calls == 0


@pytest.mark.parametrize(
    ("attribute", "value", "failure"),
    [
        ("upload_failure", True, BootstrapFailureType.FILE_TRANSFER_FAILED),
        (
            "helper_failure",
            "merge_key",
            BootstrapFailureType.AUTHORIZED_KEY_UPDATE_FAILED,
        ),
        (
            "helper_failure",
            "install",
            BootstrapFailureType.COLLECTOR_INSTALLATION_FAILED,
        ),
        ("helper_failure", "state", BootstrapFailureType.VERIFICATION_FAILED),
        ("cleanup_failure", True, BootstrapFailureType.CLEANUP_FAILED),
        ("monitor_failure", True, BootstrapFailureType.MONITOR_AUTHENTICATION_FAILED),
    ],
)
def test_deployment_and_verification_failures_are_typed_and_retryable(
    tmp_path: Path,
    attribute: str,
    value: object,
    failure: BootstrapFailureType,
) -> None:
    ssh = FakeSSHManager()
    setattr(ssh, attribute, value)
    service, _, inventory, _ = _service(tmp_path, ssh=ssh)

    result = service.bootstrap(_request())

    assert result.failure_type is failure
    assert inventory.bootstrap_calls == 0
    setattr(ssh, attribute, False if isinstance(value, bool) else None)
    retry = service.bootstrap(_request())
    assert retry.success


@pytest.mark.parametrize(
    ("document", "failure"),
    [
        ({"not": "collector"}, BootstrapFailureType.COLLECTOR_SCHEMA_MISMATCH),
        (
            {
                "schema_version": 99,
                "collector_version": "1.0.0",
                "host": {"hostname": "server-01.example.test"},
            },
            BootstrapFailureType.COLLECTOR_SCHEMA_MISMATCH,
        ),
        (
            {
                "schema_version": 1,
                "collector_version": "old",
                "host": {"hostname": "server-01.example.test"},
            },
            BootstrapFailureType.COLLECTOR_SCHEMA_MISMATCH,
        ),
        (
            {
                "schema_version": 1,
                "collector_version": "1.0.0",
                "host": {"hostname": "bad host"},
            },
            BootstrapFailureType.HOST_IDENTITY_MISMATCH,
        ),
    ],
)
def test_collector_verification_rejects_invalid_documents(
    tmp_path: Path, document: dict[str, object], failure: BootstrapFailureType
) -> None:
    ssh = FakeSSHManager()
    ssh.collector_document = document
    service, _, _, _ = _service(tmp_path, ssh=ssh)
    result = service.bootstrap(_request())
    assert result.failure_type is failure


def test_host_key_change_during_bootstrap_fails_closed(tmp_path: Path) -> None:
    ssh = FakeSSHManager()
    ssh.trust_statuses = [SSHTrustStatus.TRUSTED, SSHTrustStatus.CHANGED]
    service, _, inventory, _ = _service(tmp_path, ssh=ssh)
    result = service.bootstrap(_request())
    assert result.failure_type is BootstrapFailureType.HOST_KEY_CHANGED
    assert inventory.bootstrap_calls == 0


@pytest.mark.parametrize(
    ("configure", "failure"),
    [
        (
            lambda ssh: setattr(ssh, "collector_output", "not-json"),
            BootstrapFailureType.INVALID_COLLECTOR_JSON,
        ),
        (
            lambda ssh: setattr(ssh, "collector_truncated", True),
            BootstrapFailureType.INVALID_COLLECTOR_JSON,
        ),
        (
            lambda ssh: setattr(ssh, "honor_monitor_command", True),
            BootstrapFailureType.VERIFICATION_FAILED,
        ),
    ],
)
def test_collector_output_and_forced_command_fail_closed(
    tmp_path: Path, configure: Any, failure: BootstrapFailureType
) -> None:
    ssh = FakeSSHManager()
    configure(ssh)
    service, _, inventory, _ = _service(tmp_path, ssh=ssh)
    result = service.bootstrap(_request())
    assert result.failure_type is failure
    assert inventory.bootstrap_calls == 0


def test_configuration_and_request_validation_are_secret_safe(tmp_path: Path) -> None:
    with pytest.raises(BootstrapValidationError):
        BootstrapRequest(UUID(int=0), "admin")
    with pytest.raises(BootstrapValidationError):
        BootstrapRequest(make_inventory_server().uuid, "")

    with pytest.raises(BootstrapConfigurationError) as error:
        _service(tmp_path, overrides={"collector_path": "/tmp/../unsafe"})
    assert "unsafe" not in str(error.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"authorized_key_options": ["no-pty"]},
        {"authorized_keys_mode": "0666"},
        {"monitor_home": "/"},
        {"supported_collector_schema_versions": []},
        {"maximum_collector_output_bytes": 10},
        {"forbidden_groups": []},
    ],
)
def test_unsafe_bootstrap_configuration_fails_closed(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    with pytest.raises(BootstrapConfigurationError):
        _service(tmp_path, overrides=overrides)


def test_local_public_key_and_artifact_prerequisites(tmp_path: Path) -> None:
    service, _, _, _ = _service(tmp_path)
    assert service.is_initialized
    assert service.artifact is not None
    assert service.artifact.sha256

    public = tmp_path / "missing.pub"
    with pytest.raises(BootstrapConfigurationError):
        _service(tmp_path / "missing", overrides={"monitor_public_key": str(public)})
