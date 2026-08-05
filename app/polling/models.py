"""Immutable typed results for on-demand polling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.discovery import DiscoveryStatus, ObservationState

from .exceptions import PollingValidationError


class PollingStatus(StrEnum):
    """Final outcome of one on-demand poll."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PollingFailureType(StrEnum):
    """Safe failure classifications that contain no infrastructure output."""

    NONE = "none"
    SERVER_UNAVAILABLE = "server_unavailable"
    SERVER_DISABLED = "server_disabled"
    SERVER_UNMANAGED = "server_unmanaged"
    SERVER_DELETED = "server_deleted"
    SERVER_NOT_BOOTSTRAPPED = "server_not_bootstrapped"
    COLLECTION_FAILED = "collection_failed"
    DISCOVERY_SUBMISSION_FAILED = "discovery_submission_failed"
    DISCOVERY_FINALIZATION_FAILED = "discovery_finalization_failed"
    INVENTORY_SUCCESS_UPDATE_FAILED = "inventory_success_update_failed"
    INVENTORY_FAILURE_UPDATE_FAILED = "inventory_failure_update_failed"


@dataclass(frozen=True, slots=True)
class PollingResult:
    """Safe final result of one explicitly requested poll."""

    server_uuid: UUID
    status: PollingStatus
    failure_type: PollingFailureType
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    inventory_updated: bool = False
    observation_uuid: UUID | None = None
    observation_state: ObservationState | None = None
    discovery_status: DiscoveryStatus | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.server_uuid, UUID) or self.server_uuid.int == 0:
            raise PollingValidationError("polling server UUID is invalid")
        if not isinstance(self.status, PollingStatus):
            raise PollingValidationError("polling status is invalid")
        if not isinstance(self.failure_type, PollingFailureType):
            raise PollingValidationError("polling failure type is invalid")
        if (
            not isinstance(self.started_at, datetime)
            or self.started_at.tzinfo is None
            or not isinstance(self.finished_at, datetime)
            or self.finished_at.tzinfo is None
            or self.finished_at < self.started_at
        ):
            raise PollingValidationError("polling timestamps are invalid")
        if type(self.duration_ms) is not int or self.duration_ms < 0:
            raise PollingValidationError("polling duration is invalid")
        if type(self.inventory_updated) is not bool:
            raise PollingValidationError("polling inventory flag is invalid")
        if self.observation_uuid is not None and not isinstance(
            self.observation_uuid, UUID
        ):
            raise PollingValidationError("polling observation UUID is invalid")
        if self.observation_state is not None and not isinstance(
            self.observation_state, ObservationState
        ):
            raise PollingValidationError("polling observation state is invalid")
        if self.discovery_status is not None and not isinstance(
            self.discovery_status, DiscoveryStatus
        ):
            raise PollingValidationError("polling discovery status is invalid")
        if self.status is PollingStatus.SUCCEEDED:
            if (
                self.failure_type is not PollingFailureType.NONE
                or not self.inventory_updated
                or self.observation_uuid is None
                or self.observation_state is not ObservationState.SUCCESSFUL
                or self.discovery_status
                not in {DiscoveryStatus.COMPLETE, DiscoveryStatus.PARTIAL}
            ):
                raise PollingValidationError(
                    "successful polling result is inconsistent"
                )
        elif self.failure_type is PollingFailureType.NONE:
            raise PollingValidationError(
                "failed polling result requires a failure type"
            )

    @property
    def succeeded(self) -> bool:
        """Return whether discovery finalized and inventory recorded success."""
        return self.status is PollingStatus.SUCCEEDED

    @property
    def is_partial(self) -> bool:
        """Return whether a successful poll retained partial collected facts."""
        return self.discovery_status is DiscoveryStatus.PARTIAL
