"""Single-shot adapter for the forced monitor-key health document."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable, Mapping
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.discovery import (
    DiscoveryAddress,
    DiscoveryCPU,
    DiscoveryDisk,
    DiscoveryInterface,
    DiscoveryKernel,
    DiscoveryMemory,
    DiscoveryMetadata,
    DiscoveryObservation,
    DiscoveryOperatingSystem,
    DiscoveryStatus,
    ObservationSource,
    ObservedService,
)
from app.inventory import Server
from app.ssh import (
    SSHCommandRequest,
    SSHCommandResult,
    SSHIdentity,
    SSHManager,
    SSHManagerError,
)

from .exceptions import LinuxCommandError, LinuxParserError
from .validation import validate_server

SCHEMA_VERSION = 1
DEFAULT_MAX_OUTPUT_BYTES = 262_144
DEFAULT_TIMEOUT_SECONDS = 30.0
_SERVICE_NAMES = (
    "docker",
    "mysql",
    "mariadb",
    "redis",
    "prometheus",
    "asterisk",
    "freepbx",
    "rsyslog",
    "syslog-ng",
    "systemd-journald",
    "node_exporter",
    "time-sync",
)


class ForcedCommandCollectorLogger(Protocol):
    """Narrow structured logger accepted by the forced-command collector."""

    def bind(self, **context: Any) -> ForcedCommandCollectorLogger: ...

    def info(self, message: object, *args: object, **kwargs: object) -> None: ...

    def warning(self, message: object, *args: object, **kwargs: object) -> None: ...


class ForcedCommandLinuxCollector:
    """Collect one observation from exactly one forced monitor SSH response."""

    def __init__(
        self,
        ssh_manager: SSHManager,
        logger: ForcedCommandCollectorLogger,
        *,
        username: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        if not isinstance(username, str) or not username.strip():
            raise LinuxParserError("forced-command monitor username is invalid")
        if type(timeout_seconds) not in {int, float} or not 0 < timeout_seconds <= 3600:
            raise LinuxParserError("forced-command timeout is invalid")
        if (
            type(max_output_bytes) is not int
            or not 1024 <= max_output_bytes <= 4_194_304
        ):
            raise LinuxParserError("forced-command output limit is invalid")
        self._ssh = ssh_manager
        self._logger = logger
        self._username = username.strip()
        self._timeout_seconds = float(timeout_seconds)
        self._max_output_bytes = max_output_bytes
        self._uuid_factory = uuid_factory or uuid4

    def collect(self, server: Server) -> DiscoveryObservation:
        """Execute the forced command once and map its bounded schema-1 document."""
        server = validate_server(server)
        logger = self._logger.bind(
            server_id=str(server.uuid),
            server_name=server.hostname,
            operation="collect_forced_linux",
        )
        logger.info("Forced-command Linux collection started")
        target = self._ssh.create_target(
            server.management_address or server.primary_address,
            self._username,
            server_uuid=server.uuid,
        )
        request = SSHCommandRequest(
            target=target,
            command=("true",),
            identity=SSHIdentity.MONITOR,
            timeout_seconds=self._timeout_seconds,
        )
        try:
            result = self._ssh.run(request)
        except SSHManagerError as exc:
            raise LinuxCommandError("forced-command SSH collection failed") from exc
        document = self._document(result)
        observation = self._observation(server, result, document)
        logger.info(
            "Forced-command Linux collection completed duration_ms=%d status=%s",
            observation.collection_duration_ms,
            observation.status.value,
        )
        return observation

    def _document(self, result: SSHCommandResult) -> Mapping[str, Any]:
        if not isinstance(result, SSHCommandResult):
            raise LinuxCommandError("forced-command SSH result is invalid")
        if (
            result.stdout_truncated
            or len(result.stdout.encode("utf-8")) > self._max_output_bytes
        ):
            raise LinuxCommandError("forced-command output exceeded its limit")
        if not result.succeeded:
            raise LinuxCommandError("forced-command SSH collection failed")
        try:
            document = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise LinuxParserError("forced-command collector JSON is invalid") from exc
        if not isinstance(document, Mapping):
            raise LinuxParserError("forced-command collector document is invalid")
        if type(document.get("schema_version")) is not int or document.get(
            "schema_version"
        ) != SCHEMA_VERSION:
            raise LinuxParserError("forced-command collector schema is unsupported")
        version = document.get("collector_version")
        if not isinstance(version, str) or not version.strip() or len(version) > 128:
            raise LinuxParserError("forced-command collector version is invalid")
        if not isinstance(document.get("host"), Mapping) or not isinstance(
            document.get("services"), Mapping
        ):
            raise LinuxParserError("forced-command collector document is incomplete")
        return document

    def _observation(
        self,
        server: Server,
        result: SSHCommandResult,
        document: Mapping[str, Any],
    ) -> DiscoveryObservation:
        host = _mapping(document["host"])
        hostname = _hostname(host.get("hostname"))
        if hostname is None or not _matches_inventory_host(hostname, server.hostname):
            raise LinuxParserError("forced-command collector host identity mismatched")

        operating_system, os_partial = _operating_system(host.get("operating_system"))
        kernel_text = _text(host.get("kernel"), maximum=255)
        architecture = _text(host.get("architecture"), maximum=64)
        logical_cpus = _non_negative_integer(host.get("logical_cpus"))
        memory, memory_partial = _memory(host.get("memory"))
        disks = _disks(host.get("filesystems"))
        interfaces, addresses = _interfaces(host.get("interfaces"))
        services, product_metadata = _services(document["services"])
        is_partial = any(
            (
                os_partial,
                kernel_text in {None, "unknown"},
                architecture in {None, "unknown"},
                logical_cpus is None,
                memory_partial,
            )
        )
        finished_at = result.finished_at
        return DiscoveryObservation(
            uuid=self._uuid_factory(),
            server_uuid=server.uuid,
            source=ObservationSource.SSH,
            discovered_at=result.started_at,
            collection_duration_ms=max(0, round(result.duration_seconds * 1000)),
            collector_version=str(document["collector_version"]),
            hostname=hostname,
            fqdn=hostname if "." in hostname else None,
            operating_system=operating_system,
            kernel=(
                DiscoveryKernel("Linux", kernel_text)
                if kernel_text not in {None, "unknown"}
                else None
            ),
            architecture=(
                architecture if architecture not in {None, "unknown"} else None
            ),
            cpu=(
                DiscoveryCPU(logical_cores=logical_cpus)
                if logical_cpus is not None
                else None
            ),
            memory=memory,
            disks=disks,
            interfaces=interfaces,
            addresses=addresses,
            services=services,
            docker=DiscoveryMetadata(product_metadata["docker"]),
            redis=DiscoveryMetadata(product_metadata["redis"]),
            mysql=DiscoveryMetadata(product_metadata["mysql"]),
            freepbx=DiscoveryMetadata(product_metadata["freepbx"]),
            prometheus=DiscoveryMetadata(product_metadata["prometheus"]),
            raw_metadata=DiscoveryMetadata(
                (("remote_schema_version", str(SCHEMA_VERSION)),)
            ),
            status=DiscoveryStatus.PARTIAL if is_partial else DiscoveryStatus.UNKNOWN,
            created_at=finished_at,
            updated_at=finished_at,
        )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object, *, maximum: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:maximum]


def _hostname(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > 253
        or any(character.isspace() for character in value)
    ):
        return None
    return value.strip().lower().rstrip(".")


def _non_negative_integer(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _matches_inventory_host(observed: str, inventory: str) -> bool:
    observed_name = observed.lower().rstrip(".")
    inventory_name = inventory.lower().rstrip(".")
    return (
        observed_name == inventory_name
        or observed_name.split(".", 1)[0] == inventory_name.split(".", 1)[0]
    )


def _operating_system(value: object) -> tuple[DiscoveryOperatingSystem | None, bool]:
    data = _mapping(value)
    name = _text(data.get("pretty_name"), maximum=128) or _text(
        data.get("name"), maximum=128
    )
    distribution = _text(data.get("id"), maximum=128)
    version = _text(data.get("version_id"), maximum=128) or _text(
        data.get("version"), maximum=128
    )
    if name is None:
        return None, True
    return DiscoveryOperatingSystem(name, distribution, version), False


def _memory(value: object) -> tuple[DiscoveryMemory | None, bool]:
    data = _mapping(value)
    total = _non_negative_integer(data.get("total_bytes"))
    available = _non_negative_integer(data.get("available_bytes"))
    if total is None:
        return None, True
    if available is not None and available > total:
        available = None
    return DiscoveryMemory(total, available), False


def _disks(value: object) -> tuple[DiscoveryDisk, ...]:
    if not isinstance(value, list) or len(value) > 256:
        return ()
    disks: list[DiscoveryDisk] = []
    seen: set[tuple[str, str | None]] = set()
    for item in value:
        data = _mapping(item)
        name = _text(data.get("name"), maximum=255)
        total = _non_negative_integer(data.get("total_bytes"))
        available = _non_negative_integer(data.get("available_bytes"))
        mount = _text(data.get("mount_point"), maximum=1024)
        key = (name or "", mount)
        if name is None or total is None or key in seen:
            continue
        if available is not None and available > total:
            available = None
        disks.append(DiscoveryDisk(name, total, available, mount))
        seen.add(key)
    return tuple(disks)


def _interfaces(
    value: object,
) -> tuple[tuple[DiscoveryInterface, ...], tuple[DiscoveryAddress, ...]]:
    if not isinstance(value, list) or len(value) > 256:
        return (), ()
    interfaces: list[DiscoveryInterface] = []
    addresses: list[DiscoveryAddress] = []
    seen_interfaces: set[str] = set()
    seen_addresses: set[tuple[str, str]] = set()
    for item in value:
        data = _mapping(item)
        name = _text(data.get("name"), maximum=128)
        if name is None or name in seen_interfaces:
            continue
        is_up = data.get("up") if type(data.get("up")) is bool else None
        interfaces.append(DiscoveryInterface(name, is_up=is_up))
        seen_interfaces.add(name)
        raw_addresses = data.get("addresses")
        if not isinstance(raw_addresses, list) or len(raw_addresses) > 256:
            continue
        for raw_address in raw_addresses:
            if not isinstance(raw_address, str):
                continue
            try:
                address = str(ipaddress.ip_address(raw_address.strip()))
            except ValueError:
                continue
            key = (address, name)
            if key not in seen_addresses:
                addresses.append(DiscoveryAddress(address, name))
                seen_addresses.add(key)
    return tuple(interfaces), tuple(addresses)


def _services(
    value: object,
) -> tuple[tuple[ObservedService, ...], dict[str, tuple[tuple[str, str], ...]]]:
    data = _mapping(value)
    observed: list[ObservedService] = []
    metadata: dict[str, list[tuple[str, str]]] = {
        "docker": [],
        "redis": [],
        "mysql": [],
        "freepbx": [],
        "prometheus": [],
    }
    for name in _SERVICE_NAMES:
        state = _mapping(data.get(name))
        installation = _service_value(
            state.get("installation"), {"installed", "not_installed", "unknown"}
        )
        activity = _service_value(
            state.get("activity"), {"active", "inactive", "not_applicable", "unknown"}
        )
        version = _text(state.get("version"), maximum=255)
        status = activity if installation == "installed" else installation
        observed.append(ObservedService(name, status, version))
        namespace = "mysql" if name in {"mysql", "mariadb"} else name
        if namespace in metadata:
            prefix = f"{name}_" if namespace == "mysql" else ""
            metadata[namespace].extend(
                (
                    (f"{prefix}installation", installation),
                    (f"{prefix}activity", activity),
                )
            )
            if version:
                metadata[namespace].append((f"{prefix}version", version))
    return tuple(observed), {key: tuple(entries) for key, entries in metadata.items()}


def _service_value(value: object, allowed: set[str]) -> str:
    return value if isinstance(value, str) and value in allowed else "unknown"
