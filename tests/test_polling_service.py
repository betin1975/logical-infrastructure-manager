"""Deterministic tests for SQL-free on-demand polling coordination."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from app.collectors.linux import LinuxCollectorError
from app.discovery import (
    DiscoveryObservation,
    DiscoveryRepositoryError,
    DiscoveryStatus,
    DiscoveryValidationError,
    ObservationState,
)
from app.inventory import (
    InventoryRepositoryError,
    Server,
    ServerStatus,
)
from app.polling import PollingFailureType, PollingService, PollingStatus
from tests.helpers import (
    INVENTORY_NOW,
    INVENTORY_SERVER_ID,
    make_discovery_observation,
    make_inventory_server,
)

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
SECRET = "raw-ssh-output-password=never-log-this"


class FakeInventoryService:
    """InventoryService-shaped double recording only application operations."""

    def __init__(self, server: Server, events: list[str]) -> None:
        self.server = server
        self.events = events
        self.get_error: Exception | None = None
        self.success_error: Exception | None = None
        self.failure_error: Exception | None = None

    def get_server(self, server_uuid: UUID) -> Server:
        self.events.append("inventory.get")
        if self.get_error:
            raise self.get_error
        assert server_uuid == self.server.uuid
        return self.server

    def record_successful_poll(self, server_uuid: UUID) -> Server:
        self.events.append("inventory.success")
        if self.success_error:
            raise self.success_error
        return self.server

    def record_failed_poll(self, server_uuid: UUID) -> Server:
        self.events.append("inventory.failure")
        if self.failure_error:
            raise self.failure_error
        return self.server


class FakeDiscoveryService:
    """DiscoveryService-shaped double with real immutable state transitions."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.submission_error: Exception | None = None
        self.success_error: Exception | None = None
        self.failure_error: Exception | None = None
        self.observation: DiscoveryObservation | None = None

    def record_observation(
        self, observation: DiscoveryObservation
    ) -> DiscoveryObservation:
        self.events.append("discovery.record")
        if self.submission_error:
            raise self.submission_error
        self.observation = observation
        return observation

    def mark_successful(self, observation_uuid: UUID) -> DiscoveryObservation:
        self.events.append("discovery.success")
        if self.success_error:
            raise self.success_error
        assert self.observation is not None
        assert observation_uuid == self.observation.uuid
        self.observation = self.observation.transition(
            ObservationState.SUCCESSFUL, now=NOW
        )
        return self.observation

    def mark_failed(
        self, observation_uuid: UUID, reason: str
    ) -> DiscoveryObservation:
        self.events.append("discovery.failure")
        if self.failure_error:
            raise self.failure_error
        assert self.observation is not None
        assert observation_uuid == self.observation.uuid
        assert reason == "poll discovery finalization failed"
        self.observation = self.observation.transition(
            ObservationState.FAILED,
            now=NOW,
            failure_reason=reason,
        )
        return self.observation


class FakeLinuxCollector:
    """LinuxCollector-shaped double with no SSH dependency."""

    def __init__(self, observation: DiscoveryObservation, events: list[str]) -> None:
        self.observation = observation
        self.events = events
        self.error: Exception | None = None

    def collect(self, server: Server) -> DiscoveryObservation:
        self.events.append("collector.collect")
        if self.error:
            raise self.error
        assert server.uuid == self.observation.server_uuid
        return self.observation


class RecordingLogger:
    """Capture structured messages without formatting external exceptions."""

    def __init__(
        self,
        records: list[tuple[str, object, tuple[object, ...], dict[str, Any]]]
        | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.records = records if records is not None else []
        self.context = context or {}

    def bind(self, **context: Any) -> RecordingLogger:
        return RecordingLogger(self.records, {**self.context, **context})

    def info(self, message: object, *args: object, **kwargs: object) -> None:
        self.records.append(("info", message, args, self.context))

    def warning(self, message: object, *args: object, **kwargs: object) -> None:
        self.records.append(("warning", message, args, self.context))


def _server(**changes: object) -> Server:
    return make_inventory_server(last_bootstrap_at=INVENTORY_NOW, **changes)


def _dependencies(
    *,
    server: Server | None = None,
    observation: DiscoveryObservation | None = None,
) -> tuple[
    PollingService,
    FakeInventoryService,
    FakeDiscoveryService,
    FakeLinuxCollector,
    RecordingLogger,
    list[str],
]:
    events: list[str] = []
    inventory = FakeInventoryService(server or _server(), events)
    discovery = FakeDiscoveryService(events)
    collector = FakeLinuxCollector(
        observation or make_discovery_observation(), events
    )
    logger = RecordingLogger()
    ticks = iter((0.0, 0.025, 0.050, 0.075))
    service = PollingService(
        inventory,
        discovery,
        collector,
        logger,
        clock=lambda: NOW,
        monotonic=lambda: next(ticks),
    )
    return service, inventory, discovery, collector, logger, events


def test_successful_poll_finalizes_discovery_before_inventory() -> None:
    service, _, discovery, _, _, events = _dependencies()

    result = service.poll(INVENTORY_SERVER_ID, correlation_id="poll-test")

    assert result.status is PollingStatus.SUCCEEDED
    assert result.succeeded
    assert result.failure_type is PollingFailureType.NONE
    assert result.observation_state is ObservationState.SUCCESSFUL
    assert result.discovery_status is DiscoveryStatus.COMPLETE
    assert result.inventory_updated
    assert discovery.observation is not None
    assert type(result).__name__ == "PollResult"
    with pytest.raises(FrozenInstanceError):
        result.status = PollingStatus.FAILED  # type: ignore[misc]
    assert events == [
        "inventory.get",
        "collector.collect",
        "discovery.record",
        "discovery.success",
        "inventory.success",
    ]


def test_partial_collector_result_is_successful_and_remains_partial() -> None:
    observation = make_discovery_observation(status=DiscoveryStatus.PARTIAL)
    service, _, _, _, _, events = _dependencies(observation=observation)

    result = service.poll(INVENTORY_SERVER_ID)

    assert result.succeeded
    assert result.is_partial
    assert result.observation_state is ObservationState.SUCCESSFUL
    assert result.discovery_status is DiscoveryStatus.PARTIAL
    assert events[-1] == "inventory.success"
    assert "inventory.failure" not in events


@pytest.mark.parametrize(
    "error",
    [LinuxCollectorError(SECRET), DiscoveryValidationError(SECRET)],
)
def test_collector_exception_records_only_inventory_poll_failure(
    error: Exception,
) -> None:
    service, _, _, collector, _, events = _dependencies()
    collector.error = error

    result = service.poll(INVENTORY_SERVER_ID)

    assert result.failure_type is PollingFailureType.COLLECTION_FAILED
    assert result.inventory_updated
    assert result.observation_uuid is None
    assert events == ["inventory.get", "collector.collect", "inventory.failure"]


@pytest.mark.parametrize(
    ("server", "failure"),
    [
        (
            _server(enabled=False, status=ServerStatus.DISABLED),
            PollingFailureType.SERVER_DISABLED,
        ),
        (_server(managed=False), PollingFailureType.SERVER_UNMANAGED),
        (
            _server(
                enabled=False,
                status=ServerStatus.DELETED,
                deleted_at=INVENTORY_NOW,
            ),
            PollingFailureType.SERVER_DELETED,
        ),
        (
            make_inventory_server(last_bootstrap_at=None),
            PollingFailureType.SERVER_NOT_BOOTSTRAPPED,
        ),
    ],
    ids=("disabled", "unmanaged", "deleted", "not-bootstrapped"),
)
def test_ineligible_server_stops_before_collection(
    server: Server, failure: PollingFailureType
) -> None:
    service, _, _, _, _, events = _dependencies(server=server)

    result = service.poll(INVENTORY_SERVER_ID)

    assert result.failure_type is failure
    assert not result.inventory_updated
    assert events == ["inventory.get"]


def test_discovery_submission_failure_leaves_inventory_unchanged() -> None:
    service, _, discovery, _, _, events = _dependencies()
    discovery.submission_error = DiscoveryRepositoryError(SECRET)

    result = service.poll(INVENTORY_SERVER_ID)

    assert result.failure_type is PollingFailureType.DISCOVERY_SUBMISSION_FAILED
    assert not result.inventory_updated
    assert events == ["inventory.get", "collector.collect", "discovery.record"]


def test_discovery_finalization_failure_is_failed_before_inventory_update() -> None:
    service, _, discovery, _, _, events = _dependencies()
    discovery.success_error = DiscoveryRepositoryError(SECRET)

    result = service.poll(INVENTORY_SERVER_ID)

    assert result.failure_type is PollingFailureType.DISCOVERY_FINALIZATION_FAILED
    assert result.observation_state is ObservationState.FAILED
    assert result.inventory_updated
    assert events == [
        "inventory.get",
        "collector.collect",
        "discovery.record",
        "discovery.success",
        "discovery.failure",
        "inventory.failure",
    ]

    service, _, discovery, _, _, events = _dependencies()
    discovery.success_error = DiscoveryRepositoryError(SECRET)
    discovery.failure_error = DiscoveryRepositoryError(SECRET)
    result = service.poll(INVENTORY_SERVER_ID)
    assert result.failure_type is PollingFailureType.DISCOVERY_FINALIZATION_FAILED
    assert result.observation_state is ObservationState.PENDING
    assert not result.inventory_updated
    assert "inventory.failure" not in events


def test_inventory_success_update_failure_cannot_report_poll_success() -> None:
    service, inventory, _, _, _, events = _dependencies()
    inventory.success_error = InventoryRepositoryError(SECRET)

    result = service.poll(INVENTORY_SERVER_ID)

    assert not result.succeeded
    assert result.failure_type is PollingFailureType.INVENTORY_SUCCESS_UPDATE_FAILED
    assert result.observation_state is ObservationState.SUCCESSFUL
    assert not result.inventory_updated
    assert events[-1] == "inventory.success"
    assert "inventory.failure" not in events


def test_inventory_failure_update_error_is_typed_without_false_success() -> None:
    service, inventory, _, collector, _, events = _dependencies()
    collector.error = LinuxCollectorError(SECRET)
    inventory.failure_error = InventoryRepositoryError(SECRET)

    result = service.poll(INVENTORY_SERVER_ID)

    assert not result.succeeded
    assert result.failure_type is PollingFailureType.INVENTORY_FAILURE_UPDATE_FAILED
    assert not result.inventory_updated
    assert events[-1] == "inventory.failure"
    assert "inventory.success" not in events


def test_safe_logging_excludes_dependency_exception_and_raw_output() -> None:
    service, _, _, collector, logger, _ = _dependencies()
    collector.error = LinuxCollectorError(SECRET)

    result = service.poll(INVENTORY_SERVER_ID, correlation_id="safe-correlation")

    rendered = repr((result, logger.records))
    assert SECRET not in rendered
    assert "raw-ssh-output" not in rendered
    assert all(record[3].get("component") == "polling" for record in logger.records)


def test_missing_server_returns_typed_failure_without_other_calls() -> None:
    service, inventory, _, _, _, events = _dependencies()
    inventory.get_error = InventoryRepositoryError(SECRET)

    result = service.poll(INVENTORY_SERVER_ID)

    assert result.failure_type is PollingFailureType.SERVER_UNAVAILABLE
    assert events == ["inventory.get"]
