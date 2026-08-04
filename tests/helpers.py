from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml

from app.config import ConfigurationManager
from app.inventory import Label, OperatingSystem, Platform, Server, ServerType, Tag
from app.persistence import DatabaseManager, MigrationManager, TransactionManager
from app.runtime import RuntimeManager

INVENTORY_NOW = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
INVENTORY_SERVER_ID = UUID("11111111-1111-4111-8111-111111111111")


def write_yaml(path: Path, data: Any) -> None:
    """Write test data as safe YAML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@dataclass(frozen=True)
class PersistenceStack:
    """Temporary configured persistence dependencies for tests."""

    config: ConfigurationManager
    runtime: RuntimeManager
    database: DatabaseManager
    transactions: TransactionManager


def create_persistence_stack(
    tmp_path: Path,
    overrides: dict[str, Any] | None = None,
    *,
    initialize_runtime: bool = True,
    initialize_database: bool = True,
) -> PersistenceStack:
    """Create isolated persistence dependencies below ``tmp_path``."""
    database_config: dict[str, Any] = {
        "filename": "lim.sqlite3",
        "foreign_keys": True,
        "busy_timeout_ms": 100,
        "connection_timeout_seconds": 0.1,
        "journal_mode": "WAL",
        "synchronous_mode": "NORMAL",
        "row_factory": "row",
        "transaction_mode": "DEFERRED",
        "check_same_thread": True,
        "backup_prefix": "lim-backup",
    }
    if overrides:
        database_config.update(overrides)
    default = tmp_path / "config/default.yml"
    write_yaml(
        default,
        {
            "paths": {
                "runtime": str(tmp_path / "runtime"),
                "data": str(tmp_path / "runtime/data"),
                "jobs": str(tmp_path / "runtime/jobs"),
                "logs": str(tmp_path / "runtime/logs"),
                "backups": str(tmp_path / "runtime/backups"),
            },
            "logging": {"level": "INFO"},
            "database": database_config,
        },
    )
    config = ConfigurationManager(default, tmp_path / "config/local.yml", environ={})
    runtime = RuntimeManager(config, application_root=tmp_path)
    if initialize_runtime:
        runtime.initialize()
    database = DatabaseManager(config, runtime)
    if initialize_database:
        database.initialize()
    return PersistenceStack(config, runtime, database, TransactionManager(database))


def migrate_database(stack: PersistenceStack) -> MigrationManager:
    """Apply internal migrations with a deterministic history timestamp."""
    manager = MigrationManager(
        stack.database,
        stack.transactions,
        clock=lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )
    manager.apply_pending()
    return manager


def make_inventory_server(**changes: object) -> Server:
    """Create a complete synthetic immutable inventory server."""
    values: dict[str, object] = {
        "uuid": INVENTORY_SERVER_ID,
        "hostname": "server-01.example.test",
        "display_name": "Server 01",
        "primary_address": "192.0.2.10",
        "management_address": "2001:db8::10",
        "platform": Platform.VIRTUAL_MACHINE,
        "operating_system": OperatingSystem.LINUX,
        "distribution": "Example Linux",
        "distribution_version": "1.0",
        "kernel_version": "6.12.1",
        "architecture": "x86_64",
        "server_type": ServerType.VIRTUAL_MACHINE,
        "environment": "test",
        "location": "lab-a",
        "description": "Synthetic inventory server",
        "tags": frozenset({Tag("linux"), Tag("web")}),
        "labels": frozenset(
            {Label("owner", "platform"), Label("tier", "edge")}
        ),
        "created_at": INVENTORY_NOW,
        "updated_at": INVENTORY_NOW,
        "notes": "Synthetic notes",
    }
    values.update(changes)
    return Server(**values)  # type: ignore[arg-type]
