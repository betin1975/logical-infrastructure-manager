"""On-demand polling coordination across existing application services."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from app.collectors.linux import LinuxCollectorError
from app.discovery import (
    DiscoveryError,
    DiscoveryObservation,
    DiscoveryStatus,
)
from app.inventory import InventoryError, Server, ServerStatus

from .exceptions import PollingValidationError
from .models import PollingFailureType, PollingStatus, PollResult


class PollingInventory(Protocol):
    """Narrow InventoryService operations required by polling."""

    def get_server(self, server_uuid: UUID) -> Server: ...

    def record_successful_poll(self, server_uuid: UUID) -> Server: ...

    def record_failed_poll(self, server_uuid: UUID) -> Server: ...


class PollingDiscovery(Protocol):
    """Narrow DiscoveryService operations required by polling."""

    def record_observation(
        self, observation: DiscoveryObservation
    ) -> DiscoveryObservation: ...

    def mark_successful(self, observation_uuid: UUID) -> DiscoveryObservation: ...

    def mark_failed(
        self, observation_uuid: UUID, reason: str
    ) -> DiscoveryObservation: ...


class PollingCollector(Protocol):
    """Narrow LinuxCollector operation required by polling."""

    def collect(self, server: Server) -> DiscoveryObservation: ...


class PollingLogger(Protocol):
    """Structured logger dependency used without owning logging configuration."""

    def bind(self, **context: Any) -> PollingLogger: ...

    def info(self, message: object, *args: object, **kwargs: object) -> None: ...

    def warning(self, message: object, *args: object, **kwargs: object) -> None: ...


class PollingService:
    """Coordinate one explicit Linux poll without scheduling or persistence access."""

    def __init__(
        self,
        inventory_service: PollingInventory,
        discovery_service: PollingDiscovery,
        collector: PollingCollector,
        logger: PollingLogger,
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._inventory = inventory_service
        self._discovery = discovery_service
        self._collector = collector
        self._logger = logger
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic

    def poll(
        self, server_uuid: UUID, *, correlation_id: str | None = None
    ) -> PollResult:
        """Run one on-demand poll and return a safe immutable result."""
        if not isinstance(server_uuid, UUID) or server_uuid.int == 0:
            raise PollingValidationError("polling server UUID is invalid")
        if correlation_id is not None and (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or len(correlation_id) > 128
        ):
            raise PollingValidationError("polling correlation ID is invalid")

        started_at = self._now()
        started_tick = self._monotonic()
        logger = self._logger.bind(
            component="polling",
            server_id=str(server_uuid),
            operation="poll_linux",
            correlation_id=correlation_id.strip() if correlation_id else None,
        )
        logger.info("On-demand poll started")

        try:
            server = self._inventory.get_server(server_uuid)
        except InventoryError:
            return self._failure(
                server_uuid,
                PollingFailureType.SERVER_UNAVAILABLE,
                started_at,
                started_tick,
                logger,
            )

        eligibility_failure = self._eligibility_failure(server)
        if eligibility_failure is not PollingFailureType.NONE:
            return self._failure(
                server_uuid,
                eligibility_failure,
                started_at,
                started_tick,
                logger,
            )

        try:
            observation = self._collector.collect(server)
        except (LinuxCollectorError, DiscoveryError):
            return self._record_collection_failure(
                server_uuid, started_at, started_tick, logger
            )

        try:
            pending = self._discovery.record_observation(observation)
        except DiscoveryError:
            return self._failure(
                server_uuid,
                PollingFailureType.DISCOVERY_SUBMISSION_FAILED,
                started_at,
                started_tick,
                logger,
                discovery_status=observation.status,
            )

        try:
            finalized = self._discovery.mark_successful(pending.uuid)
        except DiscoveryError:
            return self._handle_finalization_failure(
                server_uuid, pending, started_at, started_tick, logger
            )

        try:
            self._inventory.record_successful_poll(server_uuid)
        except InventoryError:
            return self._failure(
                server_uuid,
                PollingFailureType.INVENTORY_SUCCESS_UPDATE_FAILED,
                started_at,
                started_tick,
                logger,
                observation=finalized,
            )

        result = self._result(
            server_uuid,
            PollingStatus.SUCCEEDED,
            PollingFailureType.NONE,
            started_at,
            started_tick,
            inventory_updated=True,
            observation=finalized,
        )
        logger.info(
            "On-demand poll completed status=%s partial=%s duration_ms=%d",
            result.status.value,
            result.is_partial,
            result.duration_ms,
        )
        return result

    @staticmethod
    def _eligibility_failure(server: Server) -> PollingFailureType:
        if server.deleted_at is not None or server.status is ServerStatus.DELETED:
            return PollingFailureType.SERVER_DELETED
        if not server.enabled or server.status is ServerStatus.DISABLED:
            return PollingFailureType.SERVER_DISABLED
        if not server.managed:
            return PollingFailureType.SERVER_UNMANAGED
        if server.last_bootstrap_at is None:
            return PollingFailureType.SERVER_NOT_BOOTSTRAPPED
        return PollingFailureType.NONE

    def _record_collection_failure(
        self,
        server_uuid: UUID,
        started_at: datetime,
        started_tick: float,
        logger: PollingLogger,
    ) -> PollResult:
        try:
            self._inventory.record_failed_poll(server_uuid)
        except InventoryError:
            failure = PollingFailureType.INVENTORY_FAILURE_UPDATE_FAILED
            updated = False
        else:
            failure = PollingFailureType.COLLECTION_FAILED
            updated = True
        return self._failure(
            server_uuid,
            failure,
            started_at,
            started_tick,
            logger,
            inventory_updated=updated,
        )

    def _handle_finalization_failure(
        self,
        server_uuid: UUID,
        pending: DiscoveryObservation,
        started_at: datetime,
        started_tick: float,
        logger: PollingLogger,
    ) -> PollResult:
        try:
            finalized = self._discovery.mark_failed(
                pending.uuid, "poll discovery finalization failed"
            )
        except DiscoveryError:
            return self._failure(
                server_uuid,
                PollingFailureType.DISCOVERY_FINALIZATION_FAILED,
                started_at,
                started_tick,
                logger,
                observation=pending,
            )
        try:
            self._inventory.record_failed_poll(server_uuid)
        except InventoryError:
            return self._failure(
                server_uuid,
                PollingFailureType.INVENTORY_FAILURE_UPDATE_FAILED,
                started_at,
                started_tick,
                logger,
                observation=finalized,
            )
        return self._failure(
            server_uuid,
            PollingFailureType.DISCOVERY_FINALIZATION_FAILED,
            started_at,
            started_tick,
            logger,
            inventory_updated=True,
            observation=finalized,
        )

    def _failure(
        self,
        server_uuid: UUID,
        failure_type: PollingFailureType,
        started_at: datetime,
        started_tick: float,
        logger: PollingLogger,
        *,
        inventory_updated: bool = False,
        observation: DiscoveryObservation | None = None,
        discovery_status: DiscoveryStatus | None = None,
    ) -> PollResult:
        result = self._result(
            server_uuid,
            PollingStatus.FAILED,
            failure_type,
            started_at,
            started_tick,
            inventory_updated=inventory_updated,
            observation=observation,
            discovery_status=discovery_status,
        )
        logger.warning(
            "On-demand poll failed failure=%s inventory_updated=%s duration_ms=%d",
            failure_type.value,
            inventory_updated,
            result.duration_ms,
        )
        return result

    def _result(
        self,
        server_uuid: UUID,
        status: PollingStatus,
        failure_type: PollingFailureType,
        started_at: datetime,
        started_tick: float,
        *,
        inventory_updated: bool,
        observation: DiscoveryObservation | None = None,
        discovery_status: DiscoveryStatus | None = None,
    ) -> PollResult:
        finished_at = self._now()
        return PollResult(
            server_uuid=server_uuid,
            status=status,
            failure_type=failure_type,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=max(0, round((self._monotonic() - started_tick) * 1000)),
            inventory_updated=inventory_updated,
            observation_uuid=observation.uuid if observation else None,
            observation_state=observation.state if observation else None,
            discovery_status=(
                observation.status if observation is not None else discovery_status
            ),
        )

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise PollingValidationError("polling clock must be timezone-aware")
        return value.astimezone(UTC)
