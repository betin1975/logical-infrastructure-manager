"""SQL-free application service for authoritative inventory changes."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from .exceptions import InventoryValidationError, ServerNotFoundError
from .models import (
    DiscoveryState,
    HealthStatus,
    Label,
    OperatingSystem,
    Platform,
    Server,
    ServerStatus,
    ServerType,
    SynchronizationState,
    Tag,
)
from .repository import InventoryRepository
from .validation import normalize_label_key, normalize_tag_name, normalize_uuid


class InventoryLogger(Protocol):
    """Narrow contextual logging dependency used by inventory services."""

    def bind(self, **context: Any) -> InventoryLogger:
        """Return a logger with additional structured context."""

    def info(self, message: object, *args: object, **kwargs: object) -> None:
        """Record a meaningful inventory lifecycle event."""


class InventoryService:
    """Coordinate validated inventory changes through a repository interface."""

    _MUTABLE_FIELDS = frozenset(
        {
            "hostname",
            "display_name",
            "primary_address",
            "management_address",
            "platform",
            "operating_system",
            "distribution",
            "distribution_version",
            "kernel_version",
            "architecture",
            "server_type",
            "environment",
            "location",
            "description",
            "managed",
            "notes",
        }
    )

    def __init__(
        self,
        repository: InventoryRepository,
        logger: InventoryLogger,
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        if not isinstance(repository, InventoryRepository):
            raise TypeError("repository must implement InventoryRepository")
        self._repository = repository
        self._logger = logger
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory or uuid4

    def register_server(
        self,
        hostname: str,
        primary_address: str,
        *,
        display_name: str | None = None,
        management_address: str | None = None,
        platform: Platform = Platform.UNKNOWN,
        operating_system: OperatingSystem = OperatingSystem.UNKNOWN,
        distribution: str | None = None,
        distribution_version: str | None = None,
        kernel_version: str | None = None,
        architecture: str | None = None,
        server_type: ServerType = ServerType.UNKNOWN,
        environment: str | None = None,
        location: str | None = None,
        description: str | None = None,
        tags: Iterable[Tag | str] = (),
        labels: Mapping[str, str] | Iterable[Label] = (),
        enabled: bool = True,
        managed: bool = True,
        notes: str | None = None,
    ) -> Server:
        """Validate and register a new authoritative server."""
        now = self._now()
        normalized_tags = frozenset(
            tag if isinstance(tag, Tag) else Tag(tag) for tag in tags
        )
        if isinstance(labels, Mapping):
            normalized_labels = frozenset(
                Label(key, value) for key, value in labels.items()
            )
        else:
            normalized_labels = frozenset(labels)
        status = ServerStatus.ACTIVE if enabled else ServerStatus.DISABLED
        server = Server(
            uuid=self._uuid_factory(),
            hostname=hostname,
            display_name=display_name if display_name is not None else hostname,
            primary_address=primary_address,
            management_address=management_address,
            platform=platform,
            operating_system=operating_system,
            distribution=distribution,
            distribution_version=distribution_version,
            kernel_version=kernel_version,
            architecture=architecture,
            server_type=server_type,
            environment=environment,
            location=location,
            description=description,
            tags=normalized_tags,
            labels=normalized_labels,
            enabled=enabled,
            managed=managed,
            status=status,
            created_at=now,
            updated_at=now,
            notes=notes,
        )
        created = self._repository.create(server)
        self._log(created, "register", "inventory server registered")
        return created

    def update_server(self, server_uuid: UUID | str, **changes: object) -> Server:
        """Apply validated mutable field changes to a server."""
        unknown = sorted(set(changes) - self._MUTABLE_FIELDS)
        if unknown:
            raise InventoryValidationError(
                "unsupported server update field(s): " + ", ".join(unknown)
            )
        return self._mutate(server_uuid, "update", **changes)

    def get_server(self, server_uuid: UUID | str) -> Server:
        """Return one active inventory server through the service boundary."""
        return self._get(server_uuid)

    def record_bootstrap_success(self, server_uuid: UUID | str) -> Server:
        """Record one fully verified bootstrap completion."""
        timestamp = self._now()
        return self._mutate(
            server_uuid,
            "bootstrap_success",
            last_bootstrap_at=timestamp,
        )

    def disable_server(self, server_uuid: UUID | str) -> Server:
        """Disable normal inventory operations for a server."""
        return self._mutate(
            server_uuid,
            "disable",
            enabled=False,
            status=ServerStatus.DISABLED,
        )

    def enable_server(self, server_uuid: UUID | str) -> Server:
        """Enable normal inventory operations for a server."""
        return self._mutate(
            server_uuid,
            "enable",
            enabled=True,
            status=ServerStatus.ACTIVE,
        )

    def mark_healthy(self, server_uuid: UUID | str) -> Server:
        """Record a healthy assessment without changing poll timestamps."""
        return self._mutate(
            server_uuid,
            "mark_healthy",
            health_status=HealthStatus.HEALTHY,
        )

    def mark_unhealthy(self, server_uuid: UUID | str) -> Server:
        """Record an unhealthy assessment without changing failure counters."""
        return self._mutate(
            server_uuid,
            "mark_unhealthy",
            health_status=HealthStatus.UNHEALTHY,
        )

    def mark_discovered(self, server_uuid: UUID | str) -> Server:
        """Mark a server as present in the latest accepted discovery."""
        server = self._get(server_uuid)
        status = ServerStatus.ACTIVE if server.enabled else ServerStatus.DISABLED
        return self._persist_update(
            server.evolve(
                now=self._now(),
                discovery_state=DiscoveryState.DISCOVERED,
                status=status,
            ),
            "mark_discovered",
        )

    def mark_missing(self, server_uuid: UUID | str) -> Server:
        """Mark a server as absent without deleting it."""
        return self._mutate(
            server_uuid,
            "mark_missing",
            discovery_state=DiscoveryState.MISSING,
            status=ServerStatus.MISSING,
        )

    def mark_deleted(self, server_uuid: UUID | str) -> Server:
        """Soft-delete a server while reserving its identity and addresses."""
        deleted = self._get(server_uuid).soft_delete(now=self._now())
        result = self._repository.delete(deleted)
        self._log(result, "delete", "inventory server soft deleted")
        return result

    def restore_server(self, server_uuid: UUID | str) -> Server:
        """Restore a soft-deleted server in a disabled state."""
        restored = self._get(server_uuid, include_deleted=True).restore(now=self._now())
        result = self._repository.restore(restored)
        self._log(result, "restore", "inventory server restored")
        return result

    def tag_server(self, server_uuid: UUID | str, tag: Tag | str) -> Server:
        """Attach a normalized tag idempotently."""
        server = self._get(server_uuid)
        normalized = tag if isinstance(tag, Tag) else Tag(tag)
        if normalized in server.tags:
            return server
        return self._persist_update(
            server.evolve(now=self._now(), tags=server.tags | {normalized}),
            "tag",
        )

    def untag_server(self, server_uuid: UUID | str, tag: Tag | str) -> Server:
        """Remove a normalized tag idempotently."""
        server = self._get(server_uuid)
        name = tag.name if isinstance(tag, Tag) else normalize_tag_name(tag)
        tags = frozenset(item for item in server.tags if item.name != name)
        if tags == server.tags:
            return server
        return self._persist_update(
            server.evolve(now=self._now(), tags=tags),
            "untag",
        )

    def label_server(
        self,
        server_uuid: UUID | str,
        key: str,
        value: str,
    ) -> Server:
        """Set or replace one normalized label."""
        server = self._get(server_uuid)
        label = Label(key, value)
        labels = frozenset(
            item for item in server.labels if item.key != label.key
        ) | {label}
        if labels == server.labels:
            return server
        return self._persist_update(
            server.evolve(now=self._now(), labels=labels),
            "label",
        )

    def remove_label(self, server_uuid: UUID | str, key: str) -> Server:
        """Remove one normalized label idempotently."""
        server = self._get(server_uuid)
        normalized = normalize_label_key(key)
        labels = frozenset(item for item in server.labels if item.key != normalized)
        if labels == server.labels:
            return server
        return self._persist_update(
            server.evolve(now=self._now(), labels=labels),
            "remove_label",
        )

    def increment_failure_count(self, server_uuid: UUID | str) -> Server:
        """Increment the consecutive failure count."""
        server = self._get(server_uuid)
        return self._persist_update(
            server.evolve(now=self._now(), failure_count=server.failure_count + 1),
            "increment_failure",
        )

    def reset_failure_count(self, server_uuid: UUID | str) -> Server:
        """Reset the consecutive failure count."""
        return self._mutate(server_uuid, "reset_failure", failure_count=0)

    def record_successful_poll(self, server_uuid: UUID | str) -> Server:
        """Record one successful poll and reset failure state."""
        server = self._get(server_uuid)
        timestamp = self._now()
        return self._persist_update(
            server.evolve(
                now=timestamp,
                last_poll_at=timestamp,
                last_successful_poll_at=timestamp,
                failure_count=0,
                health_status=HealthStatus.HEALTHY,
                synchronization_state=SynchronizationState.IN_SYNC,
            ),
            "poll_success",
        )

    def record_failed_poll(self, server_uuid: UUID | str) -> Server:
        """Record one failed poll and increment failure state."""
        server = self._get(server_uuid)
        timestamp = self._now()
        return self._persist_update(
            server.evolve(
                now=timestamp,
                last_poll_at=timestamp,
                last_failure_at=timestamp,
                failure_count=server.failure_count + 1,
                health_status=HealthStatus.UNHEALTHY,
                synchronization_state=SynchronizationState.ERROR,
            ),
            "poll_failure",
        )

    def _mutate(
        self,
        server_uuid: UUID | str,
        operation: str,
        **changes: object,
    ) -> Server:
        server = self._get(server_uuid)
        if all(getattr(server, name) == value for name, value in changes.items()):
            return server
        return self._persist_update(
            server.evolve(now=self._now(), **changes),
            operation,
        )

    def _persist_update(self, server: Server, operation: str) -> Server:
        updated = self._repository.update(server)
        self._log(updated, operation, "inventory server updated")
        return updated

    def _get(
        self,
        server_uuid: UUID | str,
        *,
        include_deleted: bool = False,
    ) -> Server:
        normalized = normalize_uuid(server_uuid)
        server = self._repository.find_by_uuid(
            normalized,
            include_deleted=include_deleted,
        )
        if server is None:
            raise ServerNotFoundError("inventory server was not found")
        return server

    def _now(self) -> datetime:
        timestamp = self._clock()
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise InventoryValidationError("inventory clock must be timezone-aware")
        return timestamp.astimezone(UTC)

    def _log(self, server: Server, operation: str, message: str) -> None:
        self._logger.bind(
            server_id=str(server.uuid),
            server_name=server.hostname,
            operation=operation,
        ).info(message)
