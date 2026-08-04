"""Immutable models for collected, non-authoritative infrastructure facts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from .exceptions import DiscoveryConflictError, DiscoveryValidationError
from .validation import (
    normalize_address,
    normalize_hostname,
    normalize_mac,
    normalize_optional_text,
    normalize_required_text,
    normalize_timestamp,
    normalize_uuid,
    validate_metadata_size,
)


class ObservationSource(StrEnum):
    """Origin of collected facts without implying authority."""

    MANUAL = "manual"
    SSH = "ssh"
    PLUGIN = "plugin"
    IMPORT = "import"
    OTHER = "other"


class ObservationState(StrEnum):
    """Persistence lifecycle of an observation."""

    PENDING = "pending"
    SUCCESSFUL = "successful"
    FAILED = "failed"
    EXPIRED = "expired"


class DiscoveryStatus(StrEnum):
    """Overall outcome reported by the collector."""

    UNKNOWN = "unknown"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class SynchronizationState(StrEnum):
    """Whether an observation has been considered by authoritative inventory."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class DiscoveryAddress:
    """An IP address observed on a host."""

    address: str
    interface_name: str | None = None
    kind: str = "interface"

    def __post_init__(self) -> None:
        object.__setattr__(self, "address", normalize_address(self.address))
        object.__setattr__(
            self,
            "interface_name",
            _optional(self.interface_name, "interface name", 128),
        )
        object.__setattr__(
            self, "kind", _required(self.kind, "address kind", 32).lower()
        )


@dataclass(frozen=True, slots=True)
class DiscoveryInterface:
    """A network interface observed by a collector."""

    name: str
    mac_address: str | None = None
    is_up: bool | None = None
    mtu: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required(self.name, "interface name", 128))
        object.__setattr__(self, "mac_address", normalize_mac(self.mac_address))
        if self.is_up is not None and type(self.is_up) is not bool:
            raise DiscoveryValidationError("interface is_up must be a boolean")
        if self.mtu is not None and (type(self.mtu) is not int or self.mtu < 0):
            raise DiscoveryValidationError("interface MTU must be non-negative")


@dataclass(frozen=True, slots=True)
class DiscoveryOperatingSystem:
    """Observed operating-system identity."""

    name: str
    distribution: str | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required(self.name, "operating system", 128))
        object.__setattr__(
            self, "distribution", _optional(self.distribution, "distribution", 128)
        )
        object.__setattr__(self, "version", _optional(self.version, "OS version", 128))


@dataclass(frozen=True, slots=True)
class DiscoveryKernel:
    """Observed kernel identity."""

    name: str
    version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required(self.name, "kernel name", 128))
        object.__setattr__(
            self, "version", _required(self.version, "kernel version", 255)
        )


@dataclass(frozen=True, slots=True)
class DiscoveryCPU:
    """Observed processor capacity."""

    model: str | None = None
    logical_cores: int = 0
    physical_cores: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "model", _optional(self.model, "CPU model", 255))
        if type(self.logical_cores) is not int or self.logical_cores < 0:
            raise DiscoveryValidationError("logical cores must be non-negative")
        if self.physical_cores is not None and (
            type(self.physical_cores) is not int or self.physical_cores < 0
        ):
            raise DiscoveryValidationError("physical cores must be non-negative")


@dataclass(frozen=True, slots=True)
class DiscoveryMemory:
    """Observed memory capacity in bytes."""

    total_bytes: int
    available_bytes: int | None = None

    def __post_init__(self) -> None:
        if type(self.total_bytes) is not int or self.total_bytes < 0:
            raise DiscoveryValidationError("total memory must be non-negative")
        if self.available_bytes is not None and (
            type(self.available_bytes) is not int
            or not 0 <= self.available_bytes <= self.total_bytes
        ):
            raise DiscoveryValidationError("available memory is invalid")


@dataclass(frozen=True, slots=True)
class DiscoveryDisk:
    """Observed storage device or mounted filesystem."""

    name: str
    total_bytes: int
    available_bytes: int | None = None
    mount_point: str | None = None
    filesystem: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required(self.name, "disk name", 255))
        object.__setattr__(
            self, "mount_point", _optional(self.mount_point, "mount point", 1024)
        )
        object.__setattr__(
            self, "filesystem", _optional(self.filesystem, "filesystem", 64)
        )
        if type(self.total_bytes) is not int or self.total_bytes < 0:
            raise DiscoveryValidationError("disk total bytes must be non-negative")
        if self.available_bytes is not None and (
            type(self.available_bytes) is not int
            or not 0 <= self.available_bytes <= self.total_bytes
        ):
            raise DiscoveryValidationError("disk available bytes is invalid")


@dataclass(frozen=True, slots=True)
class DiscoveryService:
    """Observed running service."""

    name: str
    status: str
    version: str | None = None
    port: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required(self.name, "service name", 255))
        object.__setattr__(self, "status", _required(self.status, "service status", 64))
        object.__setattr__(
            self, "version", _optional(self.version, "service version", 255)
        )
        if self.port is not None and (
            type(self.port) is not int or not 1 <= self.port <= 65535
        ):
            raise DiscoveryValidationError("service port is invalid")


@dataclass(frozen=True, slots=True)
class DiscoveryPackage:
    """Observed installed package."""

    name: str
    version: str
    manager: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required(self.name, "package name", 255))
        object.__setattr__(
            self, "version", _required(self.version, "package version", 255)
        )
        object.__setattr__(
            self, "manager", _optional(self.manager, "package manager", 64)
        )


@dataclass(frozen=True, slots=True)
class DiscoveryContainer:
    """Observed container without credentials or command output."""

    identifier: str
    name: str
    image: str
    status: str

    def __post_init__(self) -> None:
        for field_name, maximum in (
            ("identifier", 255),
            ("name", 255),
            ("image", 512),
            ("status", 64),
        ):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), field_name, maximum),
            )


@dataclass(frozen=True, slots=True)
class DiscoveryProcess:
    """Observed process summary; command lines are deliberately excluded."""

    pid: int
    name: str

    def __post_init__(self) -> None:
        if type(self.pid) is not int or self.pid < 0:
            raise DiscoveryValidationError("process PID must be non-negative")
        object.__setattr__(self, "name", _required(self.name, "process name", 255))


@dataclass(frozen=True, slots=True)
class DiscoveryNetwork:
    """Observed host-level network facts."""

    domain: str | None = None
    default_gateway: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "domain", _optional(self.domain, "network domain", 253)
        )
        if self.default_gateway is not None:
            object.__setattr__(
                self,
                "default_gateway",
                normalize_address(self.default_gateway, field="default gateway"),
            )


@dataclass(frozen=True, slots=True)
class DiscoveryMetadata:
    """Bounded normalized key/value metadata for a named collector namespace."""

    entries: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if any(
            not isinstance(entry, tuple) or len(entry) != 2 for entry in self.entries
        ):
            raise DiscoveryValidationError("metadata entries must be key/value pairs")
        normalized = tuple(
            sorted(
                (
                    _required(k, "metadata key", 255),
                    _required(v, "metadata value", 4096),
                )
                for k, v in self.entries
            )
        )
        if len({key for key, _ in normalized}) != len(normalized):
            raise DiscoveryValidationError("metadata keys must be unique")
        sensitive_markers = (
            "password",
            "token",
            "secret",
            "api_key",
            "apikey",
            "credential",
            "private_key",
            "authorization",
        )
        if any(
            marker in key.lower().replace("-", "_")
            for key, _ in normalized
            for marker in sensitive_markers
        ):
            raise DiscoveryValidationError(
                "metadata must not contain credential fields"
            )
        validate_metadata_size(normalized)
        object.__setattr__(self, "entries", normalized)


@dataclass(frozen=True, slots=True)
class DiscoveryObservation:
    """A complete immutable observation that never represents accepted truth."""

    uuid: UUID
    server_uuid: UUID
    source: ObservationSource
    discovered_at: datetime
    collection_duration_ms: int
    collector_version: str
    hostname: str
    created_at: datetime
    updated_at: datetime
    fqdn: str | None = None
    operating_system: DiscoveryOperatingSystem | None = None
    kernel: DiscoveryKernel | None = None
    architecture: str | None = None
    cpu: DiscoveryCPU | None = None
    memory: DiscoveryMemory | None = None
    disks: tuple[DiscoveryDisk, ...] = ()
    interfaces: tuple[DiscoveryInterface, ...] = ()
    addresses: tuple[DiscoveryAddress, ...] = ()
    services: tuple[DiscoveryService, ...] = ()
    packages: tuple[DiscoveryPackage, ...] = ()
    containers: tuple[DiscoveryContainer, ...] = ()
    processes: tuple[DiscoveryProcess, ...] = ()
    network: DiscoveryNetwork | None = None
    docker: DiscoveryMetadata = field(default_factory=DiscoveryMetadata)
    redis: DiscoveryMetadata = field(default_factory=DiscoveryMetadata)
    mysql: DiscoveryMetadata = field(default_factory=DiscoveryMetadata)
    freepbx: DiscoveryMetadata = field(default_factory=DiscoveryMetadata)
    prometheus: DiscoveryMetadata = field(default_factory=DiscoveryMetadata)
    raw_metadata: DiscoveryMetadata = field(default_factory=DiscoveryMetadata)
    notes: str | None = None
    synchronization_state: SynchronizationState = SynchronizationState.PENDING
    state: ObservationState = ObservationState.PENDING
    status: DiscoveryStatus = DiscoveryStatus.UNKNOWN
    failure_reason: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "uuid", normalize_uuid(self.uuid, field="observation UUID")
        )
        object.__setattr__(
            self, "server_uuid", normalize_uuid(self.server_uuid, field="server UUID")
        )
        for name, enum_type in (
            ("source", ObservationSource),
            ("synchronization_state", SynchronizationState),
            ("state", ObservationState),
            ("status", DiscoveryStatus),
        ):
            if not isinstance(getattr(self, name), enum_type):
                raise DiscoveryValidationError(f"{name} has an invalid enum value")
        for name, expected_type in (
            ("operating_system", DiscoveryOperatingSystem),
            ("kernel", DiscoveryKernel),
            ("cpu", DiscoveryCPU),
            ("memory", DiscoveryMemory),
            ("network", DiscoveryNetwork),
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, expected_type):
                raise DiscoveryValidationError(f"{name} contains an invalid value")
        for name in (
            "docker",
            "redis",
            "mysql",
            "freepbx",
            "prometheus",
            "raw_metadata",
        ):
            if not isinstance(getattr(self, name), DiscoveryMetadata):
                raise DiscoveryValidationError(f"{name} contains invalid metadata")
        object.__setattr__(self, "hostname", normalize_hostname(self.hostname))
        if self.fqdn is not None:
            object.__setattr__(
                self, "fqdn", normalize_hostname(self.fqdn, field="FQDN")
            )
        object.__setattr__(
            self,
            "collector_version",
            _required(self.collector_version, "collector version", 128),
        )
        object.__setattr__(
            self, "architecture", _optional(self.architecture, "architecture", 64)
        )
        object.__setattr__(self, "notes", _optional(self.notes, "notes", 4096))
        object.__setattr__(
            self,
            "failure_reason",
            _optional(self.failure_reason, "failure reason", 1024),
        )
        if (
            type(self.collection_duration_ms) is not int
            or self.collection_duration_ms < 0
        ):
            raise DiscoveryValidationError("collection duration must be non-negative")
        if type(self.version) is not int or self.version < 1:
            raise DiscoveryValidationError("observation version must be positive")
        for name in ("discovered_at", "created_at", "updated_at"):
            object.__setattr__(
                self,
                name,
                normalize_timestamp(getattr(self, name), field=name.replace("_", " ")),
            )
        for name, item_type in (
            ("disks", DiscoveryDisk),
            ("interfaces", DiscoveryInterface),
            ("addresses", DiscoveryAddress),
            ("services", DiscoveryService),
            ("packages", DiscoveryPackage),
            ("containers", DiscoveryContainer),
            ("processes", DiscoveryProcess),
        ):
            values = tuple(getattr(self, name))
            if any(not isinstance(item, item_type) for item in values):
                raise DiscoveryValidationError(f"{name} contains an invalid value")
            object.__setattr__(self, name, values)
        for name, key in (
            ("interfaces", lambda item: item.name),
            ("addresses", lambda item: (item.address, item.kind)),
            ("disks", lambda item: (item.name, item.mount_point)),
            ("services", lambda item: (item.name, item.port)),
            ("packages", lambda item: (item.name, item.version, item.manager)),
            ("containers", lambda item: item.identifier),
            ("processes", lambda item: item.pid),
        ):
            values = getattr(self, name)
            if len({key(item) for item in values}) != len(values):
                raise DiscoveryValidationError(f"{name} contains duplicate facts")
        self._validate_state()

    def transition(
        self,
        state: ObservationState,
        *,
        now: datetime,
        failure_reason: str | None = None,
    ) -> DiscoveryObservation:
        """Return a valid next lifecycle version."""
        allowed = {
            ObservationState.PENDING: {
                ObservationState.SUCCESSFUL,
                ObservationState.FAILED,
            },
            ObservationState.SUCCESSFUL: {ObservationState.EXPIRED},
            ObservationState.FAILED: {ObservationState.EXPIRED},
            ObservationState.EXPIRED: set(),
        }
        if state not in allowed[self.state]:
            raise DiscoveryConflictError(
                "cannot transition observation from "
                f"{self.state.value} to {state.value}"
            )
        timestamp = normalize_timestamp(now, field="updated at")
        if timestamp < self.updated_at:
            raise DiscoveryValidationError("updated at cannot move backwards")
        status = (
            DiscoveryStatus.FAILED if state is ObservationState.FAILED else self.status
        )
        if state is ObservationState.SUCCESSFUL and status is DiscoveryStatus.UNKNOWN:
            status = DiscoveryStatus.COMPLETE
        reason = (
            self.failure_reason if state is ObservationState.EXPIRED else failure_reason
        )
        return replace(
            self,
            state=state,
            status=status,
            failure_reason=reason,
            updated_at=timestamp,
            version=self.version + 1,
        )

    def _validate_state(self) -> None:
        if self.updated_at < self.created_at or self.discovered_at > self.created_at:
            raise DiscoveryValidationError("observation timestamps are inconsistent")
        if self.state is ObservationState.FAILED:
            if self.status is not DiscoveryStatus.FAILED or self.failure_reason is None:
                raise DiscoveryValidationError(
                    "failed observations require failed status and a reason"
                )
        elif (
            self.state is not ObservationState.EXPIRED
            and self.failure_reason is not None
        ):
            raise DiscoveryValidationError(
                "failure reason is only valid for failed observations"
            )
        if self.state is ObservationState.PENDING and self.status not in {
            DiscoveryStatus.UNKNOWN,
            DiscoveryStatus.PARTIAL,
        }:
            raise DiscoveryValidationError("pending observation status is invalid")
        if self.state is ObservationState.SUCCESSFUL and self.status not in {
            DiscoveryStatus.COMPLETE,
            DiscoveryStatus.PARTIAL,
        }:
            raise DiscoveryValidationError("successful observation status is invalid")


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Immutable paginated observation result."""

    items: tuple[DiscoveryObservation, ...]
    total: int
    limit: int
    offset: int

    def __post_init__(self) -> None:
        if self.total < 0 or not 1 <= self.limit <= 1000 or self.offset < 0:
            raise DiscoveryValidationError("discovery pagination metadata is invalid")
        if any(not isinstance(item, DiscoveryObservation) for item in self.items):
            raise DiscoveryValidationError("discovery result contains invalid items")

    @property
    def has_more(self) -> bool:
        """Return whether another page exists."""
        return self.offset + len(self.items) < self.total


def _required(value: str, field: str, maximum: int) -> str:
    return normalize_required_text(value, field=field, maximum=maximum)


def _optional(value: str | None, field: str, maximum: int) -> str | None:
    return normalize_optional_text(value, field=field, maximum=maximum)
