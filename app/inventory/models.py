"""Immutable inventory domain models and state enumerations."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from .exceptions import InventoryValidationError
from .validation import (
    normalize_address,
    normalize_hostname,
    normalize_label_key,
    normalize_optional_text,
    normalize_optional_timestamp,
    normalize_required_text,
    normalize_tag_name,
    normalize_timestamp,
    normalize_uuid,
)


class DiscoveryState(StrEnum):
    """Current discovery relationship between LIM and a server."""

    UNKNOWN = "unknown"
    DISCOVERED = "discovered"
    MISSING = "missing"


class HealthStatus(StrEnum):
    """Latest known health assessment."""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class OperatingSystem(StrEnum):
    """Normalized operating-system family."""

    UNKNOWN = "unknown"
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"
    FREEBSD = "freebsd"
    NETWORK_OS = "network_os"
    OTHER = "other"


class Platform(StrEnum):
    """Normalized infrastructure platform."""

    UNKNOWN = "unknown"
    BARE_METAL = "bare_metal"
    VIRTUAL_MACHINE = "virtual_machine"
    CLOUD = "cloud"
    CONTAINER = "container"
    NETWORK = "network"
    APPLIANCE = "appliance"


class ServerStatus(StrEnum):
    """Administrative lifecycle state for a server."""

    ACTIVE = "active"
    DISABLED = "disabled"
    MISSING = "missing"
    DELETED = "deleted"


class ServerType(StrEnum):
    """Normalized server or managed-device type."""

    UNKNOWN = "unknown"
    PHYSICAL = "physical"
    VIRTUAL_MACHINE = "virtual_machine"
    CLOUD_INSTANCE = "cloud_instance"
    CONTAINER_HOST = "container_host"
    NETWORK_DEVICE = "network_device"
    APPLIANCE = "appliance"


class SynchronizationState(StrEnum):
    """Whether persisted inventory agrees with the latest accepted observation."""

    PENDING = "pending"
    IN_SYNC = "in_sync"
    ERROR = "error"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class Tag:
    """Normalized server classification tag."""

    name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", normalize_tag_name(self.name))


@dataclass(frozen=True, slots=True)
class Label:
    """Normalized key/value server metadata."""

    key: str
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", normalize_label_key(self.key))
        object.__setattr__(
            self,
            "value",
            normalize_required_text(self.value, field="label value", maximum=512),
        )


@dataclass(frozen=True, slots=True)
class Server:
    """Authoritative immutable representation of one managed server."""

    uuid: UUID
    hostname: str
    display_name: str
    primary_address: str
    created_at: datetime
    updated_at: datetime
    management_address: str | None = None
    platform: Platform = Platform.UNKNOWN
    operating_system: OperatingSystem = OperatingSystem.UNKNOWN
    distribution: str | None = None
    distribution_version: str | None = None
    kernel_version: str | None = None
    architecture: str | None = None
    server_type: ServerType = ServerType.UNKNOWN
    environment: str | None = None
    location: str | None = None
    description: str | None = None
    tags: frozenset[Tag] = field(default_factory=frozenset)
    labels: frozenset[Label] = field(default_factory=frozenset)
    enabled: bool = True
    managed: bool = True
    discovery_state: DiscoveryState = DiscoveryState.UNKNOWN
    health_status: HealthStatus = HealthStatus.UNKNOWN
    status: ServerStatus = ServerStatus.ACTIVE
    last_poll_at: datetime | None = None
    last_successful_poll_at: datetime | None = None
    last_failure_at: datetime | None = None
    failure_count: int = 0
    last_bootstrap_at: datetime | None = None
    deleted_at: datetime | None = None
    synchronization_state: SynchronizationState = SynchronizationState.PENDING
    inventory_version: int = 1
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "uuid", normalize_uuid(self.uuid))
        object.__setattr__(self, "hostname", normalize_hostname(self.hostname))
        object.__setattr__(
            self,
            "display_name",
            normalize_required_text(
                self.display_name, field="display name", maximum=255
            ),
        )
        object.__setattr__(
            self,
            "primary_address",
            normalize_address(self.primary_address, field="primary address"),
        )
        if self.management_address is not None:
            object.__setattr__(
                self,
                "management_address",
                normalize_address(
                    self.management_address, field="management address"
                ),
            )
        if self.management_address == self.primary_address:
            raise InventoryValidationError(
                "management address must differ from primary address"
            )
        for name, enum_type in (
            ("platform", Platform),
            ("operating_system", OperatingSystem),
            ("server_type", ServerType),
            ("discovery_state", DiscoveryState),
            ("health_status", HealthStatus),
            ("status", ServerStatus),
            ("synchronization_state", SynchronizationState),
        ):
            if not isinstance(getattr(self, name), enum_type):
                raise InventoryValidationError(f"{name} has an invalid enum value")
        for name, maximum in (
            ("distribution", 128),
            ("distribution_version", 128),
            ("kernel_version", 255),
            ("architecture", 64),
            ("environment", 128),
            ("location", 255),
            ("description", 2048),
            ("notes", 4096),
        ):
            object.__setattr__(
                self,
                name,
                normalize_optional_text(
                    getattr(self, name), field=name.replace("_", " "), maximum=maximum
                ),
            )
        if any(not isinstance(tag, Tag) for tag in self.tags):
            raise InventoryValidationError("server tags must contain Tag values")
        if any(not isinstance(label, Label) for label in self.labels):
            raise InventoryValidationError("server labels must contain Label values")
        normalized_tags = frozenset(self.tags)
        normalized_labels = frozenset(self.labels)
        if len({label.key for label in normalized_labels}) != len(normalized_labels):
            raise InventoryValidationError("label keys must be unique per server")
        object.__setattr__(self, "tags", normalized_tags)
        object.__setattr__(self, "labels", normalized_labels)
        if type(self.enabled) is not bool or type(self.managed) is not bool:
            raise InventoryValidationError("enabled and managed must be booleans")
        if type(self.failure_count) is not int or self.failure_count < 0:
            raise InventoryValidationError("failure count cannot be negative")
        if type(self.inventory_version) is not int or self.inventory_version < 1:
            raise InventoryValidationError("inventory version must be positive")
        for name in (
            "created_at",
            "updated_at",
        ):
            object.__setattr__(
                self,
                name,
                normalize_timestamp(getattr(self, name), field=name.replace("_", " ")),
            )
        for name in (
            "last_poll_at",
            "last_successful_poll_at",
            "last_failure_at",
            "last_bootstrap_at",
            "deleted_at",
        ):
            object.__setattr__(
                self,
                name,
                normalize_optional_timestamp(
                    getattr(self, name), field=name.replace("_", " ")
                ),
            )
        self._validate_state()

    def evolve(self, *, now: datetime, **changes: object) -> Server:
        """Return a new version after validating a normal inventory mutation."""
        if self.deleted_at is not None:
            raise InventoryValidationError("deleted servers must be restored first")
        protected = {"uuid", "created_at", "updated_at", "inventory_version"}
        if protected.intersection(changes):
            raise InventoryValidationError(
                "immutable server identity cannot be changed"
            )
        timestamp = normalize_timestamp(now, field="updated at")
        if timestamp < self.updated_at:
            raise InventoryValidationError("updated at cannot move backwards")
        changes.setdefault("synchronization_state", SynchronizationState.PENDING)
        return replace(
            self,
            **changes,
            updated_at=timestamp,
            inventory_version=self.inventory_version + 1,
        )

    def soft_delete(self, *, now: datetime) -> Server:
        """Return a disabled soft-deleted version of this server."""
        if self.deleted_at is not None:
            raise InventoryValidationError("server is already deleted")
        timestamp = normalize_timestamp(now, field="deleted at")
        return self.evolve(
            now=timestamp,
            enabled=False,
            status=ServerStatus.DELETED,
            deleted_at=timestamp,
        )

    def restore(self, *, now: datetime) -> Server:
        """Return a restored but disabled version of a deleted server."""
        if self.deleted_at is None:
            raise InventoryValidationError("server is not deleted")
        timestamp = normalize_timestamp(now, field="updated at")
        if timestamp < self.updated_at:
            raise InventoryValidationError("updated at cannot move backwards")
        return replace(
            self,
            deleted_at=None,
            enabled=False,
            status=ServerStatus.DISABLED,
            synchronization_state=SynchronizationState.PENDING,
            updated_at=timestamp,
            inventory_version=self.inventory_version + 1,
        )

    def _validate_state(self) -> None:
        if self.updated_at < self.created_at:
            raise InventoryValidationError("updated at cannot precede created at")
        if self.status is ServerStatus.DELETED:
            if self.deleted_at is None or self.enabled:
                raise InventoryValidationError(
                    "deleted servers require deleted at and must be disabled"
                )
        elif self.deleted_at is not None:
            raise InventoryValidationError(
                "deleted at requires the deleted server status"
            )
        if self.status is ServerStatus.ACTIVE and not self.enabled:
            raise InventoryValidationError("active servers must be enabled")
        if self.status is ServerStatus.DISABLED and self.enabled:
            raise InventoryValidationError("disabled servers cannot be enabled")
        for timestamp in (
            self.last_poll_at,
            self.last_successful_poll_at,
            self.last_failure_at,
            self.last_bootstrap_at,
            self.deleted_at,
        ):
            if timestamp is not None and timestamp < self.created_at:
                raise InventoryValidationError(
                    "server event timestamps cannot precede creation"
                )
        if (
            self.last_successful_poll_at is not None
            and (
                self.last_poll_at is None
                or self.last_successful_poll_at > self.last_poll_at
            )
        ):
            raise InventoryValidationError(
                "successful poll cannot occur after the latest poll"
            )
        if (
            self.last_failure_at is not None
            and (
                self.last_poll_at is None
                or self.last_failure_at > self.last_poll_at
            )
        ):
            raise InventoryValidationError(
                "failure cannot occur after the latest poll"
            )


@dataclass(frozen=True, slots=True)
class RepositoryResult[T]:
    """Immutable paginated repository result."""

    items: tuple[T, ...]
    total: int
    limit: int
    offset: int

    def __post_init__(self) -> None:
        if self.total < 0 or self.limit < 1 or self.offset < 0:
            raise InventoryValidationError("repository pagination metadata is invalid")

    @property
    def has_more(self) -> bool:
        """Return whether another result page exists."""
        return self.offset + len(self.items) < self.total
