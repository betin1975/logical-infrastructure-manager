"""Collector upgrade integration tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.collector_upgrade import CollectorUpgradeService


class Polling:
    def poll(self, server_uuid):
        raise AssertionError("Polling should not run in this test.")


class Inventory:
    def list_servers(self, *, limit: int):
        assert limit == 1000
        return SimpleNamespace(items=())


def test_release_uses_request_base_url(tmp_path: Path) -> None:
    artifact = tmp_path / "remote_health.py"
    artifact.write_text(
        'COLLECTOR_VERSION = "1.1.0"\n',
        encoding="utf-8",
    )
    service = CollectorUpgradeService(
        Inventory(),
        object(),
        Polling(),
        monitor_username="monitor",
        artifact_path=artifact,
    )

    release = service.release(
        "1.1.0",
        artifact_base_url="http://lim.example:8094/",
    )

    assert release.artifact_url.startswith(
        "http://lim.example:8094/internal/collector?"
    )
    assert "version=1.1.0" in release.artifact_url
    assert "sha256=" in release.artifact_url
