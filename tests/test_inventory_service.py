from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pytest

from app.inventory import (
    DiscoveryState,
    DuplicateInventoryError,
    HealthStatus,
    InventoryRepository,
    InventoryService,
    InventoryValidationError,
    Label,
    RepositoryResult,
    Server,
    ServerNotFoundError,
    ServerStatus,
    SynchronizationState,
    Tag,
)
from tests.helpers import (
    INVENTORY_NOW as NOW,
)
from tests.helpers import (
    INVENTORY_SERVER_ID as SERVER_ID,
)


class RepositoryDouble:
    """SQL-free injected repository used to isolate InventoryService tests."""

    def __init__(self) -> None:
        self.servers: dict[UUID, Server] = {}

    def create(self, server: Server) -> Server:
        if any(item.hostname == server.hostname for item in self.servers.values()):
            raise DuplicateInventoryError("duplicate hostname")
        self.servers[server.uuid] = server
        return server

    def update(self, server: Server) -> Server:
        if server.uuid not in self.servers:
            raise ServerNotFoundError("missing")
        self.servers[server.uuid] = server
        return server

    def delete(self, server: Server) -> Server:
        return self.update(server)

    def restore(self, server: Server) -> Server:
        return self.update(server)

    def find_by_uuid(
        self,
        server_uuid: UUID,
        *,
        include_deleted: bool = False,
    ) -> Server | None:
        server = self.servers.get(server_uuid)
        if server is not None and server.deleted_at is not None and not include_deleted:
            return None
        return server

    def find_by_hostname(
        self,
        hostname: str,
        *,
        include_deleted: bool = False,
    ) -> Server | None:
        return next(
            (
                server
                for server in self.servers.values()
                if server.hostname == hostname
                and (include_deleted or server.deleted_at is None)
            ),
            None,
        )

    def find_by_address(
        self,
        address: str,
        *,
        include_deleted: bool = False,
    ) -> Server | None:
        return next(
            (
                server
                for server in self.servers.values()
                if address in {server.primary_address, server.management_address}
                and (include_deleted or server.deleted_at is None)
            ),
            None,
        )

    def find_enabled(
        self, *, limit: int = 100, offset: int = 0
    ) -> RepositoryResult[Server]:
        return self._result(
            [server for server in self.servers.values() if server.enabled],
            limit,
            offset,
        )

    def find_managed(
        self, *, limit: int = 100, offset: int = 0
    ) -> RepositoryResult[Server]:
        return self._result(
            [server for server in self.servers.values() if server.managed],
            limit,
            offset,
        )

    def list_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> RepositoryResult[Server]:
        return self._result(
            [
                server
                for server in self.servers.values()
                if include_deleted or server.deleted_at is None
            ],
            limit,
            offset,
        )

    def search(
        self,
        query: str,
        *,
        limit: int = 100,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> RepositoryResult[Server]:
        return self.list_all(
            limit=limit,
            offset=offset,
            include_deleted=include_deleted,
        )

    def count(self, *, include_deleted: bool = False) -> int:
        return self.list_all(include_deleted=include_deleted).total

    def find_by_tag(
        self,
        tag: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> RepositoryResult[Server]:
        return self._result(
            [server for server in self.servers.values() if Tag(tag) in server.tags],
            limit,
            offset,
        )

    def find_by_health(
        self,
        health_status: HealthStatus,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> RepositoryResult[Server]:
        return self._result(
            [
                server
                for server in self.servers.values()
                if server.health_status is health_status
            ],
            limit,
            offset,
        )

    @staticmethod
    def _result(
        servers: list[Server], limit: int, offset: int
    ) -> RepositoryResult[Server]:
        return RepositoryResult(
            items=tuple(servers[offset : offset + limit]),
            total=len(servers),
            limit=limit,
            offset=offset,
        )


class LoggerDouble:
    def __init__(self) -> None:
        self.context: dict[str, object] = {}
        self.events: list[tuple[str, dict[str, object]]] = []

    def bind(self, **context: object) -> LoggerDouble:
        bound = LoggerDouble()
        bound.events = self.events
        bound.context = {**self.context, **context}
        return bound

    def info(self, message: object, *args: object, **kwargs: object) -> None:
        self.events.append((str(message), self.context))


@pytest.fixture
def service_context() -> tuple[InventoryService, RepositoryDouble, LoggerDouble]:
    repository = RepositoryDouble()
    logger = LoggerDouble()
    service = InventoryService(
        repository,
        logger,
        clock=lambda: NOW,
        uuid_factory=lambda: SERVER_ID,
    )
    return service, repository, logger


def register(service: InventoryService) -> Server:
    return service.register_server(
        "service.example.test",
        "192.0.2.50",
        tags=("linux",),
        labels={"owner": "platform"},
    )


def test_service_uses_injected_repository_and_registers_server(
    service_context: tuple[InventoryService, RepositoryDouble, LoggerDouble],
) -> None:
    service, repository, logger = service_context

    server = register(service)

    assert isinstance(repository, InventoryRepository)
    assert repository.servers[SERVER_ID] == server
    assert server.created_at == server.updated_at == NOW
    assert server.inventory_version == 1
    assert server.tags == frozenset({Tag("linux")})
    assert server.labels == frozenset({Label("owner", "platform")})
    assert logger.events == [
        (
            "inventory server registered",
            {
                "server_id": str(SERVER_ID),
                "server_name": "service.example.test",
                "operation": "register",
            },
        )
    ]
    assert "192.0.2.50" not in str(logger.events)


def test_service_updates_disables_and_enables_server(
    service_context: tuple[InventoryService, RepositoryDouble, LoggerDouble],
) -> None:
    service, _, _ = service_context
    server = register(service)

    updated = service.update_server(server.uuid, location="lab-b", managed=False)
    disabled = service.disable_server(server.uuid)
    enabled = service.enable_server(server.uuid)

    assert updated.location == "lab-b"
    assert not updated.managed
    assert disabled.status is ServerStatus.DISABLED and not disabled.enabled
    assert enabled.status is ServerStatus.ACTIVE and enabled.enabled
    assert enabled.inventory_version == 4


def test_service_health_and_discovery_transitions(
    service_context: tuple[InventoryService, RepositoryDouble, LoggerDouble],
) -> None:
    service, _, _ = service_context
    server = register(service)

    assert service.mark_healthy(server.uuid).health_status is HealthStatus.HEALTHY
    assert service.mark_unhealthy(server.uuid).health_status is HealthStatus.UNHEALTHY
    discovered = service.mark_discovered(server.uuid)
    missing = service.mark_missing(server.uuid)

    assert discovered.discovery_state is DiscoveryState.DISCOVERED
    assert missing.discovery_state is DiscoveryState.MISSING
    assert missing.status is ServerStatus.MISSING


def test_service_tag_and_label_operations_are_idempotent(
    service_context: tuple[InventoryService, RepositoryDouble, LoggerDouble],
) -> None:
    service, _, _ = service_context
    server = register(service)

    tagged = service.tag_server(server.uuid, "database")
    tagged_again = service.tag_server(server.uuid, "database")
    untagged = service.untag_server(server.uuid, "linux")
    labelled = service.label_server(server.uuid, "tier", "edge")
    relabelled = service.label_server(server.uuid, "tier", "core")
    removed = service.remove_label(server.uuid, "owner")

    assert {tag.name for tag in tagged.tags} == {"linux", "database"}
    assert tagged_again.tags == tagged.tags
    assert tagged_again.inventory_version == tagged.inventory_version
    assert {tag.name for tag in untagged.tags} == {"database"}
    assert Label("tier", "edge") in labelled.labels
    assert Label("tier", "core") in relabelled.labels
    assert {label.key for label in removed.labels} == {"tier"}


def test_service_failure_count_and_poll_records(
    service_context: tuple[InventoryService, RepositoryDouble, LoggerDouble],
) -> None:
    service, _, _ = service_context
    server = register(service)

    assert service.increment_failure_count(server.uuid).failure_count == 1
    assert service.reset_failure_count(server.uuid).failure_count == 0
    failed = service.record_failed_poll(server.uuid)
    successful = service.record_successful_poll(server.uuid)

    assert failed.failure_count == 1
    assert failed.last_failure_at == failed.last_poll_at == NOW
    assert failed.health_status is HealthStatus.UNHEALTHY
    assert failed.synchronization_state is SynchronizationState.ERROR
    assert successful.failure_count == 0
    assert successful.last_successful_poll_at == successful.last_poll_at == NOW
    assert successful.health_status is HealthStatus.HEALTHY
    assert successful.synchronization_state is SynchronizationState.IN_SYNC


def test_service_soft_delete_and_restore(
    service_context: tuple[InventoryService, RepositoryDouble, LoggerDouble],
) -> None:
    service, repository, _ = service_context
    server = register(service)

    deleted = service.mark_deleted(server.uuid)
    assert deleted.status is ServerStatus.DELETED
    assert repository.find_by_uuid(server.uuid) is None

    restored = service.restore_server(server.uuid)
    assert restored.status is ServerStatus.DISABLED
    assert restored.deleted_at is None
    assert repository.find_by_uuid(server.uuid) == restored


def test_service_rejects_missing_server_protected_fields_and_bad_clock(
    service_context: tuple[InventoryService, RepositoryDouble, LoggerDouble],
) -> None:
    service, repository, logger = service_context
    with pytest.raises(ServerNotFoundError):
        service.enable_server(SERVER_ID)

    server = register(service)
    with pytest.raises(InventoryValidationError, match="unsupported"):
        service.update_server(server.uuid, inventory_version=99)
    with pytest.raises(InventoryValidationError, match="unsupported"):
        service.update_server(server.uuid, health_status=HealthStatus.HEALTHY)

    invalid_clock = InventoryService(
        repository,
        logger,
        clock=lambda: datetime(2026, 1, 1),
    )
    with pytest.raises(InventoryValidationError, match="timezone-aware"):
        invalid_clock.disable_server(server.uuid)


def test_service_propagates_duplicate_detection(
    service_context: tuple[InventoryService, RepositoryDouble, LoggerDouble],
) -> None:
    service, _, _ = service_context
    register(service)
    with pytest.raises(DuplicateInventoryError):
        service.register_server("service.example.test", "192.0.2.60")
