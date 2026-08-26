"""Join deterministic LIM analysis with a Hermes explanation."""

from __future__ import annotations

from uuid import UUID

from .hermes import HermesInsight
from .hermes_cli import HermesCLIRunner
from .hermes_store import HermesInsightStore
from .service import LogAnalysisService


class AssistedLogAnalysisService:
    def __init__(
        self,
        local_service: LogAnalysisService,
        hermes: HermesCLIRunner,
        store: HermesInsightStore,
    ) -> None:
        self._local = local_service
        self._hermes = hermes
        self._store = store

    def explain_latest(self, server_uuid: UUID) -> HermesInsight:
        local = self._local.latest(server_uuid)
        if local is None:
            local = self._local.analyze(server_uuid)
        insight = self._hermes.analyze(local)
        self._store.save(server_uuid, insight)
        return insight

    def latest(self, server_uuid: UUID) -> HermesInsight | None:
        return self._store.latest(server_uuid)
