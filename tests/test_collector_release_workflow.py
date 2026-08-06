"""Collector release workflow tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.collector_upgrade import CollectorUpgradeService, CollectorUpgradeStatus
from app.ssh import SSHConnectionTarget


class Inventory:
    def __init__(self, server):
        self.server = server

    def list_servers(self, *, limit: int):
        assert limit == 1000
        return SimpleNamespace(items=(self.server,))


class Polling:
    def __init__(self):
        self.calls = []

    def poll(self, server_uuid):
        self.calls.append(server_uuid)
        return SimpleNamespace(status=SimpleNamespace(value="succeeded"))


class SSH:
    def __init__(self, versions):
        self.versions = iter(versions)
        self.commands = []

    def create_target(self, host, username, *, server_uuid):
        return SSHConnectionTarget(
            host=host,
            port=22,
            username=username,
            server_uuid=server_uuid,
        )

    def run(self, request):
        self.commands.append(request.command)
        if request.command == ("true",):
            version = next(self.versions)
            return SimpleNamespace(
                succeeded=True,
                stdout=json.dumps({"collector_version": version}),
            )
        return SimpleNamespace(succeeded=True, stdout="")


def _server():
    return SimpleNamespace(
        uuid=uuid4(), hostname="db1", primary_address="192.0.2.10",
        management_address=None, managed=True, enabled=True,
        last_bootstrap_at=object(),
    )


def _service(tmp_path, server, ssh, polling):
    artifact = tmp_path / "remote_health.py"
    artifact.write_text('COLLECTOR_VERSION = "1.1.0"\n', encoding="utf-8")
    return CollectorUpgradeService(
        Inventory(server), ssh, polling,
        monitor_username="monitor", artifact_path=artifact,
    )


def test_already_current_skips_upgrade_and_polls(tmp_path: Path) -> None:
    server = _server()
    ssh = SSH(("1.1.0",))
    polling = Polling()
    service = _service(tmp_path, server, ssh, polling)
    results = service.upgrade_all(
        version="1.1.0", concurrency=1, dry_run=False,
        artifact_base_url="http://lim:8094",
    )
    assert results[0].status is CollectorUpgradeStatus.ALREADY_CURRENT
    assert ssh.commands == [("true",)]
    assert polling.calls == [server.uuid]


def test_upgrade_verifies_version_and_polls(tmp_path: Path) -> None:
    server = _server()
    ssh = SSH(("1.0.0", "1.1.0"))
    polling = Polling()
    service = _service(tmp_path, server, ssh, polling)
    results = service.upgrade_all(
        version="1.1.0", concurrency=1, dry_run=False,
        artifact_base_url="http://lim:8094",
    )
    assert results[0].status is CollectorUpgradeStatus.SUCCEEDED
    assert ssh.commands[0] == ("true",)
    assert ssh.commands[1][0] == "upgrade-collector"
    assert ssh.commands[2] == ("true",)
    assert polling.calls == [server.uuid]
