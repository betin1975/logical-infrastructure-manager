"""Targeted tests for LIM's minimal argparse CLI."""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

from app.bootstrap import BootstrapFailureType
from app.cli import main
from app.composition import ApplicationServices
from app.inventory import Label, RepositoryResult, Server
from app.polling import PollingFailureType
from app.ssh import (
    SSHConnectionTarget,
    SSHHostKey,
    SSHTrustResult,
    SSHTrustStatus,
)
from tests.helpers import INVENTORY_NOW, make_inventory_server


class FakeInventoryService:
    def __init__(self, server: Server) -> None:
        self.servers = [server]
        self.added: tuple[str, str, dict[str, str]] | None = None

    def register_server(
        self, hostname: str, address: str, *, labels: dict[str, str]
    ) -> Server:
        self.added = (hostname, address, labels)
        server = make_inventory_server(
            uuid=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            hostname=hostname,
            display_name=hostname,
            primary_address=address,
            management_address=None,
            labels=frozenset({Label(key, value) for key, value in labels.items()}),
        )
        self.servers.append(server)
        return server

    def list_servers(self) -> RepositoryResult[Server]:
        return RepositoryResult(tuple(self.servers), len(self.servers), 100, 0)

    def resolve_server(self, reference: str) -> Server:
        return next(
            server
            for server in self.servers
            if reference in {server.hostname, str(server.uuid)}
        )


class FakeSSHManager:
    def __init__(self) -> None:
        self.target: SSHConnectionTarget | None = None
        self.fingerprint: str | None = None
        self.inspect_status = SSHTrustStatus.UNKNOWN

    def create_target(
        self,
        host: str,
        username: str,
        *,
        server_uuid: UUID,
    ) -> SSHConnectionTarget:
        self.target = SSHConnectionTarget(host, username, server_uuid=server_uuid)
        return self.target

    def inspect_host_key(self, target: SSHConnectionTarget) -> SSHTrustResult:
        key = SSHHostKey(
            target.host,
            "ssh-ed25519",
            "public-key-body-must-not-print",
            "SHA256:presented",
        )
        return SSHTrustResult(target, self.inspect_status, (key,), ())

    def trust_host_key(
        self, target: SSHConnectionTarget, fingerprint: str
    ) -> SSHTrustResult:
        self.fingerprint = fingerprint
        return SSHTrustResult(target, SSHTrustStatus.TRUSTED)


class FakeBootstrapService:
    def __init__(self) -> None:
        self.request: object | None = None
        self.success = True

    def bootstrap(self, request: object) -> SimpleNamespace:
        self.request = request
        return SimpleNamespace(
            success=self.success,
            failure_type=(
                BootstrapFailureType.NONE
                if self.success
                else BootstrapFailureType.SUDO_UNAVAILABLE
            ),
        )


class FakePollingService:
    def __init__(self) -> None:
        self.server_uuid: UUID | None = None
        self.success = True

    def poll(self, server_uuid: UUID) -> SimpleNamespace:
        self.server_uuid = server_uuid
        return SimpleNamespace(
            succeeded=self.success,
            failure_type=(
                PollingFailureType.NONE
                if self.success
                else PollingFailureType.COLLECTION_FAILED
            ),
        )


def _services() -> tuple[
    ApplicationServices,
    FakeInventoryService,
    FakeSSHManager,
    FakeBootstrapService,
    FakePollingService,
]:
    server = make_inventory_server(
        labels=frozenset({Label("ssh_user", "deployer")}),
        last_bootstrap_at=INVENTORY_NOW,
    )
    inventory = FakeInventoryService(server)
    ssh = FakeSSHManager()
    bootstrap = FakeBootstrapService()
    polling = FakePollingService()
    services = cast(
        ApplicationServices,
        SimpleNamespace(
            inventory_service=inventory,
            ssh_manager=ssh,
            bootstrap_service=bootstrap,
            polling_service=polling,
        ),
    )
    return services, inventory, ssh, bootstrap, polling


def _run(arguments: list[str], services: ApplicationServices) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = main(arguments, services=services, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def test_help_returns_success_without_composing_services(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--help"]) == 0
    assert "Logical Infrastructure Manager" in capsys.readouterr().out


def test_invalid_command_returns_usage_error() -> None:
    services, *_ = _services()
    code, _, error = _run(["invalid"], services)
    assert code == 2
    assert "invalid command" in error


def test_server_add_uses_inventory_service_and_stores_admin_username() -> None:
    services, inventory, *_ = _services()
    code, output, error = _run(
        ["server", "add", "mailcow", "192.168.40.138", "--user", "deployer"],
        services,
    )
    assert code == 0
    assert error == ""
    assert output.startswith("added mailcow ")
    assert inventory.added == (
        "mailcow",
        "192.168.40.138",
        {"ssh_user": "deployer"},
    )


def test_server_list_outputs_bounded_safe_inventory_fields() -> None:
    services, *_ = _services()
    code, output, _ = _run(["server", "list"], services)
    assert code == 0
    assert "server-01.example.test\t192.0.2.10\tactive" in output
    assert "Synthetic notes" not in output


def test_server_show_resolves_hostname_through_inventory_service() -> None:
    services, *_ = _services()
    code, output, _ = _run(["server", "show", "server-01.example.test"], services)
    assert code == 0
    assert "hostname: server-01.example.test" in output
    assert "bootstrapped: true" in output


def test_trust_inspect_outputs_status_and_fingerprint_not_public_key() -> None:
    services, _, ssh, *_ = _services()
    code, output, _ = _run(["trust", "inspect", "server-01.example.test"], services)
    assert code == 0
    assert "trust server-01.example.test unknown" in output
    assert "SHA256:presented" in output
    assert "public-key-body-must-not-print" not in output
    assert ssh.target is not None and ssh.target.username == "deployer"


def test_trust_add_uses_confirmed_fingerprint() -> None:
    services, _, ssh, *_ = _services()
    code, output, _ = _run(
        [
            "trust",
            "add",
            "server-01.example.test",
            "--fingerprint",
            "SHA256:confirmed",
        ],
        services,
    )
    assert code == 0
    assert "trusted" in output
    assert ssh.fingerprint == "SHA256:confirmed"


def test_bootstrap_resolves_server_and_uses_stored_admin_username() -> None:
    services, _, _, bootstrap, _ = _services()
    code, output, _ = _run(["bootstrap", "server-01.example.test"], services)
    assert code == 0
    assert "bootstrap server-01.example.test succeeded" in output
    assert bootstrap.request is not None
    assert bootstrap.request.admin_username == "deployer"  # type: ignore[attr-defined]


def test_poll_resolves_exactly_one_server() -> None:
    services, inventory, _, _, polling = _services()
    code, output, _ = _run(["poll", "server-01.example.test"], services)
    assert code == 0
    assert "poll server-01.example.test succeeded" in output
    assert polling.server_uuid == inventory.servers[0].uuid


def test_failed_operations_return_nonzero_exit_codes() -> None:
    services, _, ssh, bootstrap, polling = _services()
    ssh.inspect_status = SSHTrustStatus.UNREACHABLE
    assert _run(["trust", "inspect", "server-01.example.test"], services)[0] == 1
    bootstrap.success = False
    assert _run(["bootstrap", "server-01.example.test"], services)[0] == 1
    polling.success = False
    assert _run(["poll", "server-01.example.test"], services)[0] == 1
