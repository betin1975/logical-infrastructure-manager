"""Unit tests for the read-only Linux collector and its parsers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from app.collectors.linux import (
    COLLECTOR_VERSION,
    COMMANDS,
    HOSTNAME_FALLBACK,
    LinuxCollector,
    LinuxCollectorValidationError,
)
from app.collectors.linux.exceptions import LinuxParserError
from app.collectors.linux.parser import (
    active_product,
    freepbx_product,
    parse_cpu,
    parse_df,
    parse_docker_containers,
    parse_docker_version,
    parse_hostname,
    parse_ip_address,
    parse_listening_services,
    parse_lsblk,
    parse_memory,
    parse_os_release,
    parse_systemd_services,
)
from app.discovery import DiscoveryStatus, ObservationSource, ObservationState
from app.ssh import (
    SSHCommandRequest,
    SSHCommandResult,
    SSHConnectionTarget,
    SSHFailureType,
    SSHIdentity,
    SSHManagerError,
)
from tests.helpers import INVENTORY_SERVER_ID, make_inventory_server

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
OBSERVATION_ID = UUID("33333333-3333-4333-8333-333333333333")


@dataclass(frozen=True)
class PlannedResult:
    stdout: str = ""
    exit_code: int | None = 0
    failure_type: SSHFailureType = SSHFailureType.NONE
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    attempts: int = 1
    stderr: str = ""


class FakeSSHManager:
    """Deterministic SSHManager-shaped double with no network behavior."""

    def __init__(self, plan: dict[tuple[str, ...], PlannedResult]) -> None:
        self.plan = plan
        self.requests: list[SSHCommandRequest] = []
        self.targets: list[SSHConnectionTarget] = []

    def create_target(
        self,
        host: str,
        username: str,
        *,
        port: int | None = None,
        server_uuid: UUID | str | None = None,
    ) -> SSHConnectionTarget:
        target = SSHConnectionTarget(host, username, port or 22, server_uuid)
        self.targets.append(target)
        return target

    def run(self, request: SSHCommandRequest) -> SSHCommandResult:
        self.requests.append(request)
        planned = self.plan.get(
            request.command,
            PlannedResult(
                exit_code=127, failure_type=SSHFailureType.REMOTE_NONZERO_EXIT
            ),
        )
        return SSHCommandResult(
            target=request.target,
            exit_code=planned.exit_code,
            stdout=planned.stdout,
            stderr=planned.stderr,
            started_at=NOW,
            finished_at=NOW,
            duration_seconds=0.01,
            timed_out=planned.timed_out,
            stdout_truncated=planned.stdout_truncated,
            stderr_truncated=planned.stderr_truncated,
            failure_type=planned.failure_type,
            attempts=planned.attempts,
        )


class RecordingLogger:
    """Capture safe structured logger calls for assertions."""

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


def _base_plan(os_release: str | None = None) -> dict[tuple[str, ...], PlannedResult]:
    outputs = {
        ("hostnamectl", "--static"): "node-01\n",
        ("hostname", "--fqdn"): "node-01.example.test\n",
        ("cat", "/etc/os-release"): os_release
        or (
            'NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n'
            'ID=ubuntu\nVERSION_ID="24.04"\n'
        ),
        ("uname", "-r"): "6.8.0-40-generic\n",
        ("uname", "-m"): "x86_64\n",
        ("nproc",): "8\n",
        ("lscpu",): (
            "CPU(s): 8\nCore(s) per socket: 4\nSocket(s): 1\n"
            "Model name: Example CPU\nUnknown: ignored\n"
        ),
        (
            "free",
            "-b",
        ): (
            "total used free shared buff/cache available\n"
            "Mem: 16000000000 1 2 3 4 8000000000\n"
        ),
        (
            "df",
            "-P",
            "-B1",
        ): (
            "Filesystem 1-blocks Used Available Capacity Mounted on\n"
            "/dev/sda1 100000 40000 60000 40% /\n"
            "tmpfs 1000 0 1000 0% /run/user space\n"
        ),
        (
            "lsblk",
            "-J",
        ): (
            '{"blockdevices":[{"name":"sda","size":100000,"fstype":"ext4",'
            '"mountpoints":[null],"unknown":true},{"name":"sdb","size":"200000",'
            '"children":[{"name":"sdb1","size":"200000","fstype":"xfs",'
            '"mountpoint":"/data"}]}]}'
        ),
        (
            "ip",
            "-j",
            "address",
        ): (
            '[{"ifname":"lo","flags":["LOOPBACK","UP"],"mtu":65536,'
            '"address":"00:00:00:00:00:00","addr_info":[{"family":"inet",'
            '"local":"127.0.0.1"}]},{"ifname":"eth0","flags":["UP"],'
            '"mtu":1500,"address":"02:00:00:00:00:01","addr_info":['
            '{"family":"inet","local":"192.0.2.10"},{"family":"inet6",'
            '"local":"2001:db8::10"}],"future":"ignored"}]'
        ),
        (
            "ss",
            "-tuln",
        ): (
            "Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port\n"
            "tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n"
            "tcp LISTEN 0 128 [::]:22 [::]:*\n"
            "udp UNCONN 0 0 0.0.0.0:53 0.0.0.0:*\n"
        ),
        (
            "systemctl",
            "list-units",
            "--type=service",
            "--state=running",
            "--no-pager",
        ): (
            "UNIT LOAD ACTIVE SUB DESCRIPTION\n"
            "  ssh.service loaded active running OpenSSH\n"
            "● redis.service loaded active running Redis\n"
            "2 loaded units listed.\n"
        ),
        (
            "docker",
            "version",
            "--format",
            "{{json .}}",
        ): (
            '{"Client":{"Version":"27.1"},"Server":{"Version":"27.1"},'
            '"Unexpected":{"nested":true}}'
        ),
        (
            "docker",
            "ps",
            "--format",
            "{{json .}}",
        ): (
            '{"ID":"abc123","Names":"web","Image":"example/web:1",'
            '"State":"running","Command":"do not persist"}\nmalformed\n'
        ),
        ("systemctl", "is-active", "mysql"): "active\n",
        ("systemctl", "is-active", "redis"): "active\n",
        ("systemctl", "is-active", "asterisk"): "active\n",
        ("fwconsole", "--version"): "17.0.19\n",
    }
    return {command: PlannedResult(stdout=value) for command, value in outputs.items()}


def _collector(
    plan: dict[tuple[str, ...], PlannedResult],
    logger: RecordingLogger | None = None,
) -> tuple[LinuxCollector, FakeSSHManager, RecordingLogger]:
    ssh = FakeSSHManager(plan)
    logs = logger or RecordingLogger()
    ticks = iter((10.0, 10.125))
    collector = LinuxCollector(
        ssh,  # type: ignore[arg-type]
        logs,
        username="lim-monitor",
        clock=lambda: NOW,
        monotonic=lambda: next(ticks),
        uuid_factory=lambda: OBSERVATION_ID,
    )
    return collector, ssh, logs


@pytest.mark.parametrize(
    ("distribution", "name", "version"),
    [
        ("ubuntu", "Ubuntu", "24.04"),
        ("debian", "Debian GNU/Linux", "12"),
        ("rocky", "Rocky Linux", "9.4"),
        ("almalinux", "AlmaLinux", "9.4"),
    ],
)
def test_collect_maps_supported_linux_distributions(
    distribution: str, name: str, version: str
) -> None:
    plan = _base_plan(
        f'NAME="{name}"\nPRETTY_NAME="{name} {version}"\n'
        f'ID={distribution}\nVERSION_ID="{version}"\nFUTURE_FIELD=ignored\n'
    )
    collector, ssh, _ = _collector(plan)

    observation = collector.collect(make_inventory_server())

    assert observation.uuid == OBSERVATION_ID
    assert observation.server_uuid == INVENTORY_SERVER_ID
    assert observation.source is ObservationSource.SSH
    assert observation.state is ObservationState.PENDING
    assert observation.status is DiscoveryStatus.UNKNOWN
    assert observation.collector_version == COLLECTOR_VERSION
    assert observation.collection_duration_ms == 125
    assert observation.hostname == "node-01"
    assert observation.fqdn == "node-01.example.test"
    assert observation.operating_system is not None
    assert observation.operating_system.distribution == name
    assert observation.operating_system.version == version
    assert observation.kernel and observation.kernel.version == "6.8.0-40-generic"
    assert observation.architecture == "x86_64"
    assert observation.cpu and observation.cpu.logical_cores == 8
    assert observation.cpu.physical_cores == 4
    assert observation.memory and observation.memory.total_bytes == 16_000_000_000
    assert {disk.mount_point for disk in observation.disks} >= {"/", "/data"}
    assert {interface.name for interface in observation.interfaces} == {"lo", "eth0"}
    assert {address.address for address in observation.addresses} >= {
        "192.0.2.10",
        "2001:db8::10",
    }
    assert {(service.name, service.port) for service in observation.services} >= {
        ("ssh", None),
        ("listen/tcp", 22),
    }
    assert observation.containers[0].identifier == "abc123"
    assert dict(observation.docker.entries)["server_version"] == "27.1"
    assert dict(observation.mysql.entries)["product"] == "mysql"
    assert dict(observation.redis.entries)["detected"] == "true"
    assert dict(observation.freepbx.entries)["asterisk_detected"] == "true"
    assert dict(observation.freepbx.entries)["freepbx_detected"] == "true"
    assert dict(observation.raw_metadata.entries)["distribution_supported"] == "true"
    assert ssh.targets[0].host == "2001:db8::10"
    assert all(request.identity is SSHIdentity.MONITOR for request in ssh.requests)


def test_hostname_falls_back_without_shell_or_duplicate_retry() -> None:
    plan = _base_plan()
    plan[("hostnamectl", "--static")] = PlannedResult(
        exit_code=127, failure_type=SSHFailureType.REMOTE_NONZERO_EXIT
    )
    plan[("hostname",)] = PlannedResult(stdout="fallback.example.test\n", attempts=2)
    collector, ssh, _ = _collector(plan)

    observation = collector.collect(make_inventory_server())

    assert observation.hostname == "fallback.example.test"
    commands = [request.command for request in ssh.requests]
    assert commands.count(("hostnamectl", "--static")) == 1
    assert commands.count(("hostname",)) == 1
    assert all("||" not in argument for command in commands for argument in command)


def test_command_catalog_is_fixed_read_only_and_complete() -> None:
    commands = {spec.argv for spec in COMMANDS}
    assert {
        ("cat", "/etc/os-release"),
        ("uname", "-r"),
        ("uname", "-m"),
        ("nproc",),
        ("lscpu",),
        ("free", "-b"),
        ("df", "-P", "-B1"),
        ("lsblk", "-J"),
        ("ip", "-j", "address"),
        ("ss", "-tuln"),
        ("docker", "version", "--format", "{{json .}}"),
        ("docker", "ps", "--format", "{{json .}}"),
    } <= commands
    assert all(spec.timeout_seconds > 0 for spec in COMMANDS)
    assert len({spec.name for spec in COMMANDS}) == len(COMMANDS)


def test_core_timeout_and_partial_failures_return_partial_observation() -> None:
    plan = _base_plan()
    plan[("free", "-b")] = PlannedResult(
        exit_code=None,
        failure_type=SSHFailureType.COMMAND_TIMEOUT,
        timed_out=True,
        stderr="credential must never be logged",
    )
    plan[("ip", "-j", "address")] = PlannedResult(stdout="not-json")
    collector, _, logs = _collector(plan)

    observation = collector.collect(make_inventory_server())

    assert observation.status is DiscoveryStatus.PARTIAL
    assert observation.memory is None
    assert observation.interfaces == ()
    assert int(dict(observation.raw_metadata.entries)["commands_degraded"]) >= 2
    rendered = repr(logs.records)
    assert "credential must never be logged" not in rendered
    assert "timeout" in rendered
    assert "output rejected" in rendered


def test_unexpected_os_text_is_partial_and_unsupported_distribution_is_safe() -> None:
    malformed = _base_plan("this is not os-release")
    collector, _, _ = _collector(malformed)
    observation = collector.collect(make_inventory_server())
    assert observation.status is DiscoveryStatus.PARTIAL
    assert observation.operating_system is None

    unsupported = _base_plan('NAME="Gentoo"\nID=gentoo\nVERSION_ID="2"\n')
    collector, _, _ = _collector(unsupported)
    observation = collector.collect(make_inventory_server())
    assert observation.status is DiscoveryStatus.UNKNOWN
    assert observation.operating_system is not None
    assert dict(observation.raw_metadata.entries)["distribution_supported"] == "false"


def test_one_sshmanager_exception_does_not_abort_remaining_collection() -> None:
    class RaisingSSHManager(FakeSSHManager):
        def run(self, request: SSHCommandRequest) -> SSHCommandResult:
            if request.command == ("free", "-b"):
                self.requests.append(request)
                raise SSHManagerError("synthetic safe failure")
            return super().run(request)

    ssh = RaisingSSHManager(_base_plan())
    logs = RecordingLogger()
    ticks = iter((1.0, 1.1))
    collector = LinuxCollector(
        ssh,  # type: ignore[arg-type]
        logs,
        username="lim-monitor",
        clock=lambda: NOW,
        monotonic=lambda: next(ticks),
    )

    observation = collector.collect(make_inventory_server())

    assert observation.status is DiscoveryStatus.PARTIAL
    assert observation.memory is None
    assert observation.kernel is not None


def test_missing_optional_commands_do_not_abort_or_mark_partial() -> None:
    collector, ssh, _ = _collector(_base_plan())

    observation = collector.collect(make_inventory_server())

    assert observation.status is DiscoveryStatus.UNKNOWN
    assert observation.prometheus.entries == ()
    assert any(
        request.command == ("systemctl", "is-active", "prometheus")
        for request in ssh.requests
    )


def test_docker_absent_and_systemd_absent_are_graceful() -> None:
    plan = _base_plan()
    for command in tuple(plan):
        if command[0] in {"docker", "systemctl", "fwconsole"}:
            plan[command] = PlannedResult(
                exit_code=127,
                failure_type=SSHFailureType.REMOTE_NONZERO_EXIT,
            )
    collector, _, _ = _collector(plan)

    observation = collector.collect(make_inventory_server())

    assert observation.status is DiscoveryStatus.UNKNOWN
    assert observation.docker.entries == ()
    assert observation.containers == ()
    assert observation.mysql.entries == ()
    assert observation.redis.entries == ()
    assert observation.prometheus.entries == ()
    assert observation.freepbx.entries == ()


def test_output_limit_and_permission_errors_are_safe() -> None:
    plan = _base_plan()
    plan[("df", "-P", "-B1")] = PlannedResult(
        stdout="partial",
        stdout_truncated=True,
        failure_type=SSHFailureType.OUTPUT_LIMIT_EXCEEDED,
    )
    plan[("lsblk", "-J")] = PlannedResult(
        exit_code=126,
        failure_type=SSHFailureType.REMOTE_NONZERO_EXIT,
        stderr="private host detail",
    )
    collector, _, logs = _collector(plan)

    observation = collector.collect(make_inventory_server())

    assert observation.status is DiscoveryStatus.PARTIAL
    rendered = repr(logs.records)
    assert "output_limit" in rendered
    assert "permission_denied" in rendered
    assert "private host detail" not in rendered


def test_collector_validates_inputs_and_uses_primary_address() -> None:
    with pytest.raises(LinuxCollectorValidationError):
        LinuxCollector(FakeSSHManager({}), RecordingLogger(), username=" ")  # type: ignore[arg-type]
    collector, ssh, _ = _collector(_base_plan())
    with pytest.raises(LinuxCollectorValidationError):
        collector.collect(object())  # type: ignore[arg-type]

    collector, ssh, _ = _collector(_base_plan())
    collector.collect(make_inventory_server(management_address=None))
    assert ssh.targets[0].host == "192.0.2.10"


def test_every_command_has_timeout_and_sshmanager_owns_retries() -> None:
    collector, ssh, logs = _collector(_base_plan())
    collector.collect(make_inventory_server())

    expected = {spec.argv: spec.timeout_seconds for spec in COMMANDS}
    for request in ssh.requests:
        assert request.timeout_seconds == expected.get(
            request.command, HOSTNAME_FALLBACK.timeout_seconds
        )
    assert all(request.correlation_id is None for request in ssh.requests)
    assert any("attempts=%d" in message for _, message, _ in logs.records)


def test_logging_contains_safe_required_context_and_command_metadata() -> None:
    collector, _, logs = _collector(_base_plan())
    collector.collect(make_inventory_server())

    assert logs.contexts == [
        {
            "server_id": str(INVENTORY_SERVER_ID),
            "server_name": "server-01.example.test",
            "operation": "collect_linux",
        }
    ]
    rendered = repr(logs.records)
    assert "collector_version=%s" in rendered
    assert "command=%s" in rendered
    assert "duration=%.3f" in rendered
    assert "exit_code=%s" in rendered
    assert "Command" not in rendered


def test_parser_handles_unknown_os_fields_and_malformed_lines() -> None:
    operating_system, os_id, os_like = parse_os_release(
        '# comment\nNAME=Debian\nID=debian\nID_LIKE="debian linux"\n'
        'BROKEN="unterminated\nunknown-key=value\nEXTRA=value with spaces\n'
    )
    assert operating_system.name == "Debian"
    assert os_id == "debian"
    assert os_like == "debian linux"
    assert parse_hostname("bad host name\n") is None
    with pytest.raises(LinuxParserError):
        parse_os_release("unexpected text")


def test_parser_rejects_malformed_json_but_skips_bad_nested_records() -> None:
    with pytest.raises(LinuxParserError):
        parse_lsblk("not-json")
    with pytest.raises(LinuxParserError):
        parse_ip_address('{"not":"a-list"}')
    with pytest.raises(LinuxParserError):
        parse_docker_version("[]")

    assert parse_lsblk('{"blockdevices":[null,{"name":"sda"}]}') == ()
    interfaces, addresses = parse_ip_address(
        '[null,{"future":true},{"ifname":"bad","address":"invalid"}]'
    )
    assert interfaces == ()
    assert addresses == ()
    assert parse_docker_containers("bad\n{}\n") == ()


def test_text_parsers_tolerate_headers_spacing_and_unexpected_text() -> None:
    cpu = parse_cpu("4\n", "CPU(s): 8\nModel name: CPU: with colon\n")
    assert cpu and cpu.logical_cores == 4 and cpu.model == "CPU: with colon"
    assert parse_cpu("unexpected", "nothing useful") is None
    assert parse_memory("Mem: 100 1 2") == parse_memory("Mem: 100 1 2")
    with pytest.raises(LinuxParserError):
        parse_memory("unexpected")
    with pytest.raises(LinuxParserError):
        parse_df("header\nbad")
    assert len(parse_df("header\n/dev/sda 100 10 90 10% / space\nbad")) == 1
    listening = parse_listening_services(
        "unexpected\ntcp LISTEN 0 1 [::1]:443 [::]:*\ntcp LISTEN 0 1 host:* peer:*"
    )
    assert [(item.name, item.port) for item in listening] == [("listen/tcp", 443)]
    running = parse_systemd_services(
        "UNIT LOAD ACTIVE SUB DESCRIPTION\n  cron.service loaded active running Cron\n"
    )
    assert running[0].name == "cron"


def test_product_detection_requires_positive_evidence() -> None:
    assert active_product("redis", "active\n") == (
        ("detected", "true"),
        ("product", "redis"),
        ("state", "active"),
    )
    assert active_product("redis", "inactive\n") == ()
    assert freepbx_product(None, "active\n") == (
        ("asterisk_detected", "true"),
        ("asterisk_state", "active"),
    )
    assert freepbx_product("17.0\n", None) == (
        ("freepbx_detected", "true"),
        ("freepbx_version", "17.0"),
    )
