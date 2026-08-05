"""Focused tests for single-shot forced-command polling collection."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from app.collectors.linux import ForcedCommandLinuxCollector
from app.collectors.linux.exceptions import LinuxCommandError, LinuxParserError
from app.discovery import DiscoveryStatus, ObservationState
from app.ssh import (
    SSHCommandRequest,
    SSHCommandResult,
    SSHConnectionTarget,
    SSHFailureType,
    SSHIdentity,
)
from tests.helpers import make_inventory_server

STARTED = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
FINISHED = datetime(2026, 8, 6, 12, 0, 1, tzinfo=UTC)
OBSERVATION_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
JSON_MARKER = "remote-json-document-must-not-be-logged"


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "collector_version": "1.0.0",
        "collected_at": "2026-08-06T12:00:00+00:00",
        "host": {
            "hostname": "mailcow.example.test",
            "operating_system": {
                "name": "Ubuntu",
                "pretty_name": "Ubuntu 24.04 LTS",
                "id": "ubuntu",
                "version_id": "24.04",
            },
            "kernel": "6.8.0-40-generic",
            "architecture": "x86_64",
            "logical_cpus": 8,
            "memory": {
                "total_bytes": 16_000_000_000,
                "available_bytes": 8_000_000_000,
            },
            "filesystems": [
                {
                    "name": "/dev/mapper/vg-root",
                    "total_bytes": 100_000_000_000,
                    "available_bytes": 40_000_000_000,
                    "mount_point": "/",
                }
            ],
            "interfaces": [
                {
                    "name": "eth0",
                    "up": True,
                    "addresses": ["192.168.40.138", "2001:db8::138"],
                }
            ],
        },
        "services": {
            "docker": {"installation": "installed", "activity": "active"},
            "mysql": {
                "installation": "not_installed",
                "activity": "not_applicable",
            },
            "mariadb": {"installation": "installed", "activity": "active"},
            "redis": {"installation": "installed", "activity": "active"},
            "prometheus": {
                "installation": "not_installed",
                "activity": "not_applicable",
            },
            "asterisk": {
                "installation": "not_installed",
                "activity": "not_applicable",
            },
            "freepbx": {
                "installation": "not_installed",
                "activity": "not_applicable",
            },
        },
        "ignored_marker": JSON_MARKER,
    }


class FakeSSHManager:
    def __init__(self, stdout: str, *, failure: SSHFailureType = SSHFailureType.NONE):
        self.stdout = stdout
        self.failure = failure
        self.requests: list[SSHCommandRequest] = []

    def create_target(
        self,
        host: str,
        username: str,
        *,
        server_uuid: UUID,
    ) -> SSHConnectionTarget:
        return SSHConnectionTarget(host, username, server_uuid=server_uuid)

    def run(self, request: SSHCommandRequest) -> SSHCommandResult:
        self.requests.append(request)
        return SSHCommandResult(
            target=request.target,
            exit_code=0 if self.failure is SSHFailureType.NONE else 255,
            stdout=self.stdout,
            stderr="remote stderr must not be inspected",
            started_at=STARTED,
            finished_at=FINISHED,
            duration_seconds=1.0,
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
            failure_type=self.failure,
            attempts=1,
        )


class RecordingLogger:
    def __init__(
        self,
        records: list[tuple[object, tuple[object, ...], dict[str, Any]]] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.records = records if records is not None else []
        self.context = context or {}

    def bind(self, **context: Any) -> RecordingLogger:
        return RecordingLogger(self.records, {**self.context, **context})

    def info(self, message: object, *args: object, **kwargs: object) -> None:
        self.records.append((message, args, self.context))

    def warning(self, message: object, *args: object, **kwargs: object) -> None:
        self.records.append((message, args, self.context))


def _collector(
    payload: object,
    *,
    failure: SSHFailureType = SSHFailureType.NONE,
    max_output_bytes: int = 262_144,
) -> tuple[ForcedCommandLinuxCollector, FakeSSHManager, RecordingLogger]:
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    ssh = FakeSSHManager(stdout, failure=failure)
    logger = RecordingLogger()
    collector = ForcedCommandLinuxCollector(
        ssh,  # type: ignore[arg-type]
        logger,
        username="monitor",
        max_output_bytes=max_output_bytes,
        uuid_factory=lambda: OBSERVATION_ID,
    )
    return collector, ssh, logger


def _server():
    return make_inventory_server(
        hostname="mailcow",
        display_name="Mailcow",
        primary_address="192.168.40.138",
        management_address=None,
    )


def test_mailcow_payload_maps_to_observation_with_exactly_one_ssh_request() -> None:
    collector, ssh, logger = _collector(_payload())

    observation = collector.collect(_server())

    assert observation.uuid == OBSERVATION_ID
    assert observation.hostname == "mailcow.example.test"
    assert observation.operating_system is not None
    assert observation.operating_system.distribution == "ubuntu"
    assert observation.cpu is not None and observation.cpu.logical_cores == 8
    assert observation.memory is not None
    assert {address.address for address in observation.addresses} == {
        "192.168.40.138",
        "2001:db8::138",
    }
    assert {service.name: service.status for service in observation.services}[
        "docker"
    ] == "active"
    assert observation.state is ObservationState.PENDING
    assert observation.status is DiscoveryStatus.UNKNOWN
    assert len(ssh.requests) == 1
    assert ssh.requests[0].identity is SSHIdentity.MONITOR
    assert ssh.requests[0].command == ("true",)
    assert JSON_MARKER not in repr(logger.records)


def test_invalid_json_is_rejected_without_logging_document() -> None:
    collector, ssh, logger = _collector(f'{{"marker":"{JSON_MARKER}"')

    with pytest.raises(LinuxParserError, match="JSON is invalid"):
        collector.collect(_server())

    assert len(ssh.requests) == 1
    assert JSON_MARKER not in repr(logger.records)


def test_unsupported_schema_version_is_rejected() -> None:
    payload = _payload()
    payload["schema_version"] = 2
    collector, ssh, _ = _collector(payload)

    with pytest.raises(LinuxParserError, match="schema is unsupported"):
        collector.collect(_server())

    assert len(ssh.requests) == 1


def test_mismatched_host_identity_is_rejected() -> None:
    payload = _payload()
    host = deepcopy(payload["host"])
    assert isinstance(host, dict)
    host["hostname"] = "different.example.test"
    payload["host"] = host
    collector, ssh, _ = _collector(payload)

    with pytest.raises(LinuxParserError, match="host identity mismatched"):
        collector.collect(_server())

    assert len(ssh.requests) == 1


def test_ssh_failure_is_safe_and_does_not_retry() -> None:
    collector, ssh, logger = _collector(
        _payload(), failure=SSHFailureType.AUTHENTICATION_FAILED
    )

    with pytest.raises(LinuxCommandError, match="SSH collection failed"):
        collector.collect(_server())

    assert len(ssh.requests) == 1
    assert "remote stderr" not in repr(logger.records)


def test_excessive_output_is_rejected() -> None:
    collector, ssh, _ = _collector("x" * 2048, max_output_bytes=1024)

    with pytest.raises(LinuxCommandError, match="exceeded its limit"):
        collector.collect(_server())

    assert len(ssh.requests) == 1


def test_missing_core_facts_are_partial_and_unknown_services_are_preserved() -> None:
    payload = _payload()
    host = deepcopy(payload["host"])
    assert isinstance(host, dict)
    host["logical_cpus"] = None
    payload["host"] = host
    services = deepcopy(payload["services"])
    assert isinstance(services, dict)
    services["docker"] = {"installation": "unknown", "activity": "unknown"}
    payload["services"] = services
    collector, _, _ = _collector(payload)

    observation = collector.collect(_server())

    assert observation.status is DiscoveryStatus.PARTIAL
    states = {service.name: service.status for service in observation.services}
    assert states["docker"] == "unknown"
    assert dict(observation.docker.entries) == {
        "activity": "unknown",
        "installation": "unknown",
    }
