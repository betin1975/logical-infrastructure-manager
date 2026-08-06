"""Collector upgrade service tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.collector_upgrade import (
    CollectorUpgradeService,
    CollectorUpgradeStatus,
)


class Polling:
    def poll(self, server_uuid):
        raise AssertionError("Polling should not run in this test.")


class Inventory:
    def __init__(self, servers):
        self.servers = servers

    def list_servers(self, *, limit: int):
        assert limit == 1000
        return SimpleNamespace(items=self.servers)


def test_dry_run_filters_ineligible_servers(tmp_path: Path) -> None:
    artifact = tmp_path / "remote_health.py"
    artifact.write_text('COLLECTOR_VERSION = "1.1.0"\n', encoding="utf-8")

    eligible = SimpleNamespace(
        uuid=uuid4(),
        hostname="db1",
        primary_address="192.0.2.10",
        management_address=None,
        managed=True,
        enabled=True,
        last_bootstrap_at=object(),
    )
    disabled = SimpleNamespace(
        uuid=uuid4(),
        hostname="db2",
        primary_address="192.0.2.11",
        management_address=None,
        managed=True,
        enabled=False,
        last_bootstrap_at=object(),
    )

    service = CollectorUpgradeService(
        Inventory((eligible, disabled)),
        object(),
        Polling(),
        monitor_username="monitor",
        artifact_path=artifact,
    )

    results = service.upgrade_all(
        version="1.1.0",
        concurrency=10,
        dry_run=True,
        artifact_base_url="http://lim:8094",
    )

    assert len(results) == 1
    assert results[0].hostname == "db1"
    assert results[0].status is CollectorUpgradeStatus.PENDING
