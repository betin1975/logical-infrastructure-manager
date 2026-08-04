"""SQL-free application service for discovery observation history."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from .exceptions import DiscoveryValidationError, ObservationNotFoundError
from .models import (
    DiscoveryObservation,
    DiscoveryResult,
    ObservationSource,
    ObservationState,
)
from .repository import DiscoveryRepository
from .validation import normalize_timestamp, normalize_uuid


class DiscoveryLogger(Protocol):
    """Narrow structured logging dependency used by discovery services."""

    def bind(self, **context: Any) -> DiscoveryLogger: ...

    def info(self, message: object, *args: object, **kwargs: object) -> None: ...


class DiscoveryService:
    """Validate and coordinate non-authoritative observations."""

    def __init__(
        self,
        repository: DiscoveryRepository,
        logger: DiscoveryLogger,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(repository, DiscoveryRepository):
            raise TypeError("repository must implement DiscoveryRepository")
        self._repository = repository
        self._logger = logger
        self._clock = clock or (lambda: datetime.now(UTC))

    def record_observation(
        self, observation: DiscoveryObservation
    ) -> DiscoveryObservation:
        """Persist a newly collected pending observation."""
        self.validate_observation(observation)
        if observation.state is not ObservationState.PENDING:
            raise DiscoveryValidationError("new observations must be pending")
        created = self._repository.create(observation)
        self._log(created, "record", "discovery observation recorded")
        return created

    def mark_successful(self, observation_uuid: UUID | str) -> DiscoveryObservation:
        """Mark a pending collection successful."""
        return self._transition(observation_uuid, ObservationState.SUCCESSFUL)

    def mark_failed(
        self, observation_uuid: UUID | str, reason: str
    ) -> DiscoveryObservation:
        """Mark a pending collection failed with a bounded non-secret reason."""
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason.strip()) > 1024
        ):
            raise DiscoveryValidationError(
                "failure reason must contain 1 to 1024 characters"
            )
        return self._transition(
            observation_uuid, ObservationState.FAILED, failure_reason=reason.strip()
        )

    def expire_observation(self, observation_uuid: UUID | str) -> DiscoveryObservation:
        """Expire a completed observation while preserving its history."""
        return self._transition(observation_uuid, ObservationState.EXPIRED)

    def purge_old_observations(self, *, before: datetime) -> int:
        """Purge only expired observations older than an explicit UTC cutoff."""
        cutoff = normalize_timestamp(before, field="cleanup cutoff")
        removed = self._repository.cleanup(before=cutoff)
        self._logger.bind(component="discovery", operation="cleanup").info(
            "expired discovery observations purged count=%d", removed
        )
        return removed

    def retrieve_latest(
        self, server_uuid: UUID | str, *, source: ObservationSource | None = None
    ) -> DiscoveryObservation | None:
        """Retrieve the latest observation for a server and optional source."""
        return self._repository.find_latest(
            normalize_uuid(server_uuid, field="server UUID"), source=source
        )

    def retrieve_history(
        self, server_uuid: UUID | str, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult:
        """Retrieve newest-first immutable observation history."""
        return self._repository.history(
            normalize_uuid(server_uuid, field="server UUID"),
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def validate_observation(observation: DiscoveryObservation) -> None:
        """Validate that a fully constructed domain observation is acceptable."""
        if not isinstance(observation, DiscoveryObservation):
            raise DiscoveryValidationError("observation must be a DiscoveryObservation")

    def _transition(
        self,
        observation_uuid: UUID | str,
        state: ObservationState,
        *,
        failure_reason: str | None = None,
    ) -> DiscoveryObservation:
        identifier = normalize_uuid(observation_uuid, field="observation UUID")
        current = self._repository.find_by_uuid(identifier)
        if current is None:
            raise ObservationNotFoundError("discovery observation was not found")
        changed = current.transition(
            state, now=self._now(), failure_reason=failure_reason
        )
        updated = self._repository.update(changed)
        self._log(updated, state.value, "discovery observation state changed")
        return updated

    def _now(self) -> datetime:
        return normalize_timestamp(self._clock(), field="current time")

    def _log(
        self, observation: DiscoveryObservation, operation: str, message: str
    ) -> None:
        self._logger.bind(
            component="discovery",
            server_id=str(observation.server_uuid),
            operation=operation,
        ).info(message)
