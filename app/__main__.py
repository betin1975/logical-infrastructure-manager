"""LIM application startup entry point."""

import sys
from pathlib import Path

from .config import ConfigError, ConfigManager
from .logging_manager import LoggingManager, LoggingManagerError
from .persistence import DatabaseManager, MigrationManager, PersistenceError
from .runtime import RuntimeManager, RuntimeManagerError


def main() -> int:
    """Initialize the currently implemented LIM foundation services."""
    application_root = Path(__file__).resolve().parent.parent
    try:
        config = ConfigManager()
        runtime = RuntimeManager(config, application_root=application_root)
        runtime.initialize()
    except (ConfigError, RuntimeManagerError):
        sys.stderr.write("LIM startup failed before logging initialization\n")
        return 1

    logging_manager = LoggingManager(config, runtime)
    try:
        logging_manager.initialize()
    except LoggingManagerError:
        sys.stderr.write("LIM logging initialization failed\n")
        return 1

    logger = logging_manager.get_logger("bootstrap", operation="startup")
    try:
        database = DatabaseManager(config, runtime)
        database.initialize()
        migration_state = MigrationManager(database).apply_pending()
    except PersistenceError:
        logger.exception("LIM persistence initialization failed")
        return 1

    logger.info(
        "LIM startup foundation initialized with schema_version=%d",
        migration_state.schema_version,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
