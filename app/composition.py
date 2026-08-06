"""Reusable application composition for startup and operator interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .bootstrap import BootstrapConfigurationError, BootstrapService
from .collector_upgrade import CollectorUpgradeService
from .collectors.linux import ForcedCommandLinuxCollector, LinuxCollectorError
from .config import ConfigError, ConfigManager, ConfigurationManager
from .discovery import DiscoveryError, DiscoveryService
from .inventory import InventoryError, InventoryService
from .logging_manager import LoggingManager, LoggingManagerError
from .persistence import (
    DatabaseManager,
    MigrationManager,
    MigrationState,
    PersistenceError,
    SQLiteDiscoveryRepository,
    SQLiteInventoryRepository,
    TransactionManager,
)
from .polling import PollingError, PollingService
from .runtime import RuntimeManager, RuntimeManagerError
from .ssh import SSHManager, SSHManagerError


class CompositionError(RuntimeError):
    """Safe failure raised when an application dependency cannot initialize."""

    def __init__(self, stage: str) -> None:
        super().__init__(f"application composition failed during {stage}")
        self.stage = stage


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    """Immutable dependency graph shared by startup and CLI entry points."""

    config: ConfigurationManager
    runtime: RuntimeManager
    logging_manager: LoggingManager
    database: DatabaseManager
    migration_state: MigrationState
    inventory_service: InventoryService
    discovery_service: DiscoveryService
    ssh_manager: SSHManager
    bootstrap_service: BootstrapService
    linux_collector: ForcedCommandLinuxCollector
    polling_service: PollingService
    collector_upgrade_service: CollectorUpgradeService


_COMPOSITION_ERRORS = (
    BootstrapConfigurationError,
    ConfigError,
    DiscoveryError,
    InventoryError,
    LinuxCollectorError,
    LoggingManagerError,
    PersistenceError,
    PollingError,
    RuntimeManagerError,
    SSHManagerError,
)


def build_application_services(
    *, application_root: Path | None = None
) -> ApplicationServices:
    """Build and initialize the approved dependency graph without remote calls."""
    root = (application_root or Path(__file__).resolve().parent.parent).resolve()
    stage = "configuration"
    try:
        config = ConfigManager()
        stage = "runtime"
        runtime = RuntimeManager(config, application_root=root)
        runtime.initialize()

        stage = "logging"
        logging_manager = LoggingManager(config, runtime)
        logging_manager.initialize()

        stage = "persistence"
        database = DatabaseManager(config, runtime)
        database.initialize()
        migration_state = MigrationManager(database).apply_pending()
        transactions = TransactionManager(database)

        stage = "inventory"
        inventory_repository = SQLiteInventoryRepository(database, transactions)
        inventory_service = InventoryService(
            inventory_repository,
            logging_manager.get_logger("inventory"),
        )

        stage = "discovery"
        discovery_repository = SQLiteDiscoveryRepository(database, transactions)
        discovery_service = DiscoveryService(
            discovery_repository,
            logging_manager.get_logger("discovery"),
        )

        stage = "ssh"
        ssh_manager = SSHManager(
            config,
            runtime,
            logging_manager.get_logger("ssh", operation="initialize"),
            application_root=root,
        )
        ssh_manager.initialize()

        stage = "bootstrap"
        bootstrap_service = BootstrapService(
            config,
            runtime,
            ssh_manager,
            inventory_service,
            logging_manager.get_logger("bootstrap.service"),
            application_root=root,
        )
        bootstrap_service.initialize()

        stage = "polling"
        linux_collector = ForcedCommandLinuxCollector(
            ssh_manager,
            logging_manager.get_logger("collector.forced_linux"),
            username=bootstrap_service.settings.monitor_username,
            timeout_seconds=bootstrap_service.settings.verification_timeout_seconds,
            max_output_bytes=bootstrap_service.settings.maximum_collector_output_bytes,
        )
        polling_service = PollingService(
            inventory_service,
            discovery_service,
            linux_collector,
            logging_manager.get_logger("polling"),
        )
        collector_upgrade_service = CollectorUpgradeService(
            inventory_service,
            ssh_manager,
            monitor_username=bootstrap_service.settings.monitor_username,
            artifact_path=root / "app/bootstrap/artifacts/remote_health.py",
        )
    except _COMPOSITION_ERRORS as exc:
        raise CompositionError(stage) from exc

    return ApplicationServices(
        config=config,
        runtime=runtime,
        logging_manager=logging_manager,
        database=database,
        migration_state=migration_state,
        inventory_service=inventory_service,
        discovery_service=discovery_service,
        ssh_manager=ssh_manager,
        bootstrap_service=bootstrap_service,
        linux_collector=linux_collector,
        polling_service=polling_service,
        collector_upgrade_service=collector_upgrade_service,
    )
