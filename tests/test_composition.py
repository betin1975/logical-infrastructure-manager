"""Targeted tests for reusable, immutable application composition."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import app.composition as composition
from app.persistence import MigrationState


class FakeConfig:
    def require(self, key: str, expected_type: type | None = None) -> object:
        assert key == "bootstrap.monitor_username"
        assert expected_type is str
        return "monitor"


class FakeLogger:
    def info(self, message: object, *args: object, **kwargs: object) -> None:
        pass


class FakeLoggingManager:
    def __init__(self, *args: object) -> None:
        self.initialized = False

    def initialize(self) -> None:
        self.initialized = True

    def get_logger(self, *args: object, **kwargs: object) -> FakeLogger:
        return FakeLogger()


class FakeInitializable:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.initialized = False

    def initialize(self) -> None:
        self.initialized = True


class FakeMigrationManager:
    def __init__(self, database: object) -> None:
        self.database = database

    def apply_pending(self) -> MigrationState:
        return MigrationState(3, (), True)


class FakeNode:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs


def test_composition_builds_one_immutable_reusable_service_graph(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(composition, "ConfigManager", FakeConfig)
    monkeypatch.setattr(composition, "RuntimeManager", FakeInitializable)
    monkeypatch.setattr(composition, "LoggingManager", FakeLoggingManager)
    monkeypatch.setattr(composition, "DatabaseManager", FakeInitializable)
    monkeypatch.setattr(composition, "MigrationManager", FakeMigrationManager)
    monkeypatch.setattr(composition, "TransactionManager", FakeNode)
    monkeypatch.setattr(composition, "SQLiteInventoryRepository", FakeNode)
    monkeypatch.setattr(composition, "SQLiteDiscoveryRepository", FakeNode)
    monkeypatch.setattr(composition, "InventoryService", FakeNode)
    monkeypatch.setattr(composition, "DiscoveryService", FakeNode)
    monkeypatch.setattr(composition, "SSHManager", FakeInitializable)
    monkeypatch.setattr(composition, "BootstrapService", FakeInitializable)
    monkeypatch.setattr(composition, "LinuxCollector", FakeNode)
    monkeypatch.setattr(composition, "PollingService", FakeNode)

    services = composition.build_application_services(application_root=tmp_path)

    assert services.migration_state.schema_version == 3
    assert services.runtime.initialized  # type: ignore[attr-defined]
    assert services.logging_manager.initialized  # type: ignore[attr-defined]
    assert services.database.initialized  # type: ignore[attr-defined]
    assert services.ssh_manager.initialized  # type: ignore[attr-defined]
    assert services.bootstrap_service.initialized  # type: ignore[attr-defined]
    assert services.polling_service.args[0] is services.inventory_service  # type: ignore[attr-defined]
    assert services.polling_service.args[1] is services.discovery_service  # type: ignore[attr-defined]
    assert services.polling_service.args[2] is services.linux_collector  # type: ignore[attr-defined]
    with pytest.raises(FrozenInstanceError):
        services.inventory_service = FakeNode()  # type: ignore[misc]
