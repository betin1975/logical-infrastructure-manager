"""SQL-free repository contract owned by the discovery domain."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from .models import (
    DiscoveryObservation,
    DiscoveryResult,
    DiscoveryStatus,
    ObservationSource,
    ObservationState,
)


@runtime_checkable
class DiscoveryRepository(Protocol):
    """Persistence operations required by ``DiscoveryService``."""

    def create(self, observation: DiscoveryObservation) -> DiscoveryObservation: ...

    def update(self, observation: DiscoveryObservation) -> DiscoveryObservation: ...

    def find_by_uuid(
        self, observation_uuid: UUID | str
    ) -> DiscoveryObservation | None: ...

    def find_latest(
        self, server_uuid: UUID | str, *, source: ObservationSource | None = None
    ) -> DiscoveryObservation | None: ...

    def history(
        self, server_uuid: UUID | str, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult: ...

    def search(
        self, query: str, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult: ...

    def list_by_server(
        self, server_uuid: UUID | str, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult: ...

    def list_by_source(
        self, source: ObservationSource, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult: ...

    def list_by_status(
        self, status: DiscoveryStatus, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult: ...

    def list_by_state(
        self, state: ObservationState, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult: ...

    def count(
        self,
        *,
        server_uuid: UUID | str | None = None,
        source: ObservationSource | None = None,
        status: DiscoveryStatus | None = None,
        state: ObservationState | None = None,
    ) -> int: ...

    def cleanup(self, *, before: datetime) -> int: ...
