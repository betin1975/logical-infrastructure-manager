from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

import pytest

from app.discovery import (
    DiscoveryObservation,
    DiscoveryRepository,
    DiscoveryResult,
    DiscoveryService,
    DiscoveryStatus,
    DiscoveryValidationError,
    ObservationNotFoundError,
    ObservationSource,
    ObservationState,
)
from tests.helpers import INVENTORY_NOW as NOW
from tests.helpers import INVENTORY_SERVER_ID as SERVER_ID
from tests.helpers import make_discovery_observation


class RepositoryDouble:
    """SQL-free observation store used to isolate the application service."""

    def __init__(self) -> None:
        self.items: dict[UUID, DiscoveryObservation] = {}

    def create(self, observation: DiscoveryObservation) -> DiscoveryObservation:
        self.items[observation.uuid] = observation
        return observation

    def update(self, observation: DiscoveryObservation) -> DiscoveryObservation:
        self.items[observation.uuid] = observation
        return observation

    def find_by_uuid(self, observation_uuid: UUID | str) -> DiscoveryObservation | None:
        return self.items.get(UUID(str(observation_uuid)))

    def find_latest(
        self, server_uuid: UUID | str, *, source: ObservationSource | None = None
    ) -> DiscoveryObservation | None:
        matches = [
            item
            for item in self.items.values()
            if item.server_uuid == UUID(str(server_uuid))
            and (source is None or item.source is source)
        ]
        return max(matches, key=lambda item: item.discovered_at, default=None)

    def history(
        self, server_uuid: UUID | str, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult:
        values = tuple(
            item
            for item in self.items.values()
            if item.server_uuid == UUID(str(server_uuid))
        )
        return DiscoveryResult(
            values[offset : offset + limit], len(values), limit, offset
        )

    def search(
        self, query: str, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult:
        return DiscoveryResult((), 0, limit, offset)

    def list_by_server(
        self, server_uuid: UUID | str, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult:
        return self.history(server_uuid, limit=limit, offset=offset)

    def list_by_source(
        self, source: ObservationSource, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult:
        return DiscoveryResult((), 0, limit, offset)

    def list_by_status(
        self, status: DiscoveryStatus, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult:
        return DiscoveryResult((), 0, limit, offset)

    def list_by_state(
        self, state: ObservationState, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult:
        return DiscoveryResult((), 0, limit, offset)

    def count(
        self,
        *,
        server_uuid: UUID | str | None = None,
        source: ObservationSource | None = None,
        status: DiscoveryStatus | None = None,
        state: ObservationState | None = None,
    ) -> int:
        return len(self.items)

    def cleanup(self, *, before: datetime) -> int:
        expired = [
            key
            for key, item in self.items.items()
            if item.state is ObservationState.EXPIRED and item.updated_at < before
        ]
        for key in expired:
            del self.items[key]
        return len(expired)


class LoggerDouble:
    def __init__(self) -> None:
        self.records: list[tuple[dict[str, object], object, tuple[object, ...]]] = []
        self.context: dict[str, object] = {}

    def bind(self, **context: object) -> LoggerDouble:
        bound = LoggerDouble()
        bound.records = self.records
        bound.context = self.context | context
        return bound

    def info(self, message: object, *args: object, **kwargs: object) -> None:
        self.records.append((self.context, message, args))


@pytest.fixture
def dependencies() -> tuple[RepositoryDouble, LoggerDouble, DiscoveryService]:
    repository = RepositoryDouble()
    logger = LoggerDouble()
    service = DiscoveryService(
        repository, logger, clock=lambda: NOW + timedelta(seconds=10)
    )
    return repository, logger, service


def test_service_records_and_retrieves_observation(
    dependencies: tuple[RepositoryDouble, LoggerDouble, DiscoveryService],
) -> None:
    repository, logger, service = dependencies
    observation = make_discovery_observation()
    assert isinstance(repository, DiscoveryRepository)
    assert service.record_observation(observation) == observation
    assert service.retrieve_latest(SERVER_ID) == observation
    assert service.retrieve_history(SERVER_ID).items == (observation,)
    assert logger.records[0][0] == {
        "component": "discovery",
        "server_id": str(SERVER_ID),
        "operation": "record",
    }


def test_service_marks_success_failed_and_expired(
    dependencies: tuple[RepositoryDouble, LoggerDouble, DiscoveryService],
) -> None:
    repository, _, service = dependencies
    first = service.record_observation(make_discovery_observation())
    successful = service.mark_successful(first.uuid)
    expired = service.expire_observation(successful.uuid)
    assert expired.state is ObservationState.EXPIRED

    failed_item = make_discovery_observation(
        uuid=UUID("33333333-3333-4333-8333-333333333333")
    )
    service.record_observation(failed_item)
    failed = service.mark_failed(failed_item.uuid, "synthetic failure")
    assert failed.status is DiscoveryStatus.FAILED
    assert failed.failure_reason == "synthetic failure"
    assert repository.items[failed.uuid] == failed


def test_service_purges_only_old_expired_observations(
    dependencies: tuple[RepositoryDouble, LoggerDouble, DiscoveryService],
) -> None:
    _, logger, service = dependencies
    item = service.record_observation(make_discovery_observation())
    service.mark_failed(item.uuid, "failed")
    service.expire_observation(item.uuid)
    assert service.purge_old_observations(before=NOW + timedelta(seconds=20)) == 1
    assert logger.records[-1][2] == (1,)


def test_service_rejects_invalid_input_and_missing_observations(
    dependencies: tuple[RepositoryDouble, LoggerDouble, DiscoveryService],
) -> None:
    _, _, service = dependencies
    with pytest.raises(DiscoveryValidationError, match="DiscoveryObservation"):
        service.record_observation(object())  # type: ignore[arg-type]
    with pytest.raises(DiscoveryValidationError, match="pending"):
        service.record_observation(
            make_discovery_observation().transition(
                ObservationState.SUCCESSFUL, now=NOW + timedelta(seconds=1)
            )
        )
    with pytest.raises(DiscoveryValidationError, match="failure reason"):
        service.mark_failed(make_discovery_observation().uuid, "")
    with pytest.raises(ObservationNotFoundError):
        service.mark_successful(make_discovery_observation().uuid)


def test_service_requires_repository_contract_and_aware_clock() -> None:
    with pytest.raises(TypeError, match="repository"):
        DiscoveryService(object(), LoggerDouble())  # type: ignore[arg-type]
    repository = RepositoryDouble()
    service = DiscoveryService(
        repository, LoggerDouble(), clock=lambda: datetime(2026, 1, 1)
    )
    repository.create(make_discovery_observation())
    with pytest.raises(DiscoveryValidationError, match="timezone-aware"):
        service.mark_successful(make_discovery_observation().uuid)


def test_logs_exclude_observed_addresses_notes_and_failure_reason(
    dependencies: tuple[RepositoryDouble, LoggerDouble, DiscoveryService],
) -> None:
    _, logger, service = dependencies
    item = service.record_observation(
        make_discovery_observation(notes="sensitive-note")
    )
    service.mark_failed(item.uuid, "credential-shaped but synthetic")
    rendered = repr(logger.records)
    assert "192.0.2.10" not in rendered
    assert "sensitive-note" not in rendered
    assert "credential-shaped" not in rendered
