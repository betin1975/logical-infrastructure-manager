"""Ordered, transactional Python migrations for LIM's SQLite schema."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from .database import DatabaseManager
from .errors import MigrationError, TransactionError
from .transactions import TransactionManager

MIGRATION_TABLE = "lim_schema_migrations"
_MIGRATION_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
MigrationFunction = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True, slots=True)
class Migration:
    """One immutable, ordered Python schema migration."""

    version: int
    name: str
    upgrade: MigrationFunction


@dataclass(frozen=True, slots=True)
class MigrationRecord:
    """One migration recorded in the database history."""

    version: int
    name: str
    applied_at: str


@dataclass(frozen=True, slots=True)
class MigrationState:
    """Read-only view of current migration metadata."""

    schema_version: int
    history: tuple[MigrationRecord, ...]
    metadata_exists: bool


def _create_migration_metadata(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE {MIGRATION_TABLE} (
            version INTEGER PRIMARY KEY CHECK (version > 0),
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL
        )
        """
    )


def _internal_migrations() -> tuple[Migration, ...]:
    # Import after Migration is defined so the inventory schema can reuse it
    # without introducing a cycle at module import time.
    from .discovery_schema import DISCOVERY_MIGRATION
    from .inventory_schema import INVENTORY_MIGRATION

    return (
        Migration(1, "create_migration_metadata", _create_migration_metadata),
        INVENTORY_MIGRATION,
        DISCOVERY_MIGRATION,
    )


INTERNAL_MIGRATIONS = _internal_migrations()


class MigrationManager:
    """Validate, apply, and inspect LIM's ordered internal migrations."""

    def __init__(
        self,
        database: DatabaseManager,
        transactions: TransactionManager | None = None,
        *,
        migrations: Iterable[Migration] = INTERNAL_MIGRATIONS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._transactions = transactions or TransactionManager(database)
        self._migrations = self._validate_migrations(tuple(migrations))
        self._clock = clock or (lambda: datetime.now(UTC))

    def apply_pending(self) -> MigrationState:
        """Apply each unapplied migration in its own transaction."""
        state = self.inspect()
        applied = {record.version: record for record in state.history}
        known_versions = {migration.version for migration in self._migrations}
        unknown_applied = sorted(set(applied) - known_versions)
        if unknown_applied:
            raise MigrationError(
                "database contains migration versions unknown to this application"
            )

        for migration in self._migrations:
            existing = applied.get(migration.version)
            if existing is not None:
                if existing.name != migration.name:
                    raise MigrationError(
                        f"migration {migration.version} metadata does not match"
                    )
                continue
            try:
                with self._transactions.transaction() as connection:
                    migration.upgrade(connection)
                    applied_at = self._clock().astimezone(UTC).isoformat()
                    connection.execute(
                        f"INSERT INTO {MIGRATION_TABLE} "
                        "(version, name, applied_at) VALUES (?, ?, ?)",
                        (migration.version, migration.name, applied_at),
                    )
            except (TransactionError, sqlite3.Error) as exc:
                raise MigrationError(
                    f"migration {migration.version} failed due to "
                    f"{type(exc).__name__}"
                ) from exc
            except Exception as exc:
                raise MigrationError(
                    f"migration {migration.version} failed due to "
                    f"{type(exc).__name__}"
                ) from exc
            applied[migration.version] = MigrationRecord(
                migration.version,
                migration.name,
                applied_at,
            )
        return self.inspect()

    def inspect(self) -> MigrationState:
        """Inspect migration state through a read-only connection."""
        try:
            with self._database.connection(read_only=True) as connection:
                return inspect_migration_connection(connection)
        except MigrationError:
            raise
        except sqlite3.Error as exc:
            raise MigrationError(
                f"cannot inspect migration state: {type(exc).__name__}"
            ) from exc

    def schema_version(self) -> int:
        """Return the greatest successfully applied migration version."""
        return self.inspect().schema_version

    @staticmethod
    def _validate_migrations(
        migrations: tuple[Migration, ...],
    ) -> tuple[Migration, ...]:
        if not migrations:
            raise MigrationError("at least one migration is required")
        versions: set[int] = set()
        validated: list[Migration] = []
        for migration in migrations:
            if not isinstance(migration, Migration):
                raise MigrationError("migration metadata must use Migration records")
            if type(migration.version) is not int or migration.version <= 0:
                raise MigrationError("migration version must be a positive integer")
            if migration.version in versions:
                raise MigrationError(
                    f"duplicate migration version: {migration.version}"
                )
            if not isinstance(migration.name, str) or not _MIGRATION_NAME.fullmatch(
                migration.name
            ):
                raise MigrationError(
                    f"migration {migration.version} has an invalid name"
                )
            if not callable(migration.upgrade):
                raise MigrationError(
                    f"migration {migration.version} upgrade must be callable"
                )
            versions.add(migration.version)
            validated.append(migration)
        validated.sort(key=lambda item: item.version)
        expected = list(range(1, len(validated) + 1))
        actual = [migration.version for migration in validated]
        if actual != expected:
            raise MigrationError("migration versions must be contiguous starting at 1")
        return tuple(validated)


def inspect_migration_connection(connection: sqlite3.Connection) -> MigrationState:
    """Inspect and validate migration metadata on an existing connection."""
    table = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (MIGRATION_TABLE,),
    ).fetchone()
    if table is None:
        return MigrationState(schema_version=0, history=(), metadata_exists=False)

    columns = connection.execute(f"PRAGMA table_info({MIGRATION_TABLE})").fetchall()
    column_contract = tuple(
        (row[1], str(row[2]).upper(), row[3], row[5]) for row in columns
    )
    if column_contract != (
        ("version", "INTEGER", 0, 1),
        ("name", "TEXT", 1, 0),
        ("applied_at", "TEXT", 1, 0),
    ):
        raise MigrationError("migration metadata table has an invalid schema")
    rows = connection.execute(
        f"SELECT version, name, applied_at FROM {MIGRATION_TABLE} ORDER BY version"
    ).fetchall()
    if not rows:
        raise MigrationError("migration metadata table has no initial migration")
    history: list[MigrationRecord] = []
    previous = 0
    for row in rows:
        version, name, applied_at = row
        try:
            applied_timestamp = datetime.fromisoformat(applied_at)
        except (TypeError, ValueError):
            applied_timestamp = None
        if (
            type(version) is not int
            or version != previous + 1
            or not isinstance(name, str)
            or not _MIGRATION_NAME.fullmatch(name)
            or not isinstance(applied_at, str)
            or applied_timestamp is None
            or applied_timestamp.tzinfo is None
        ):
            raise MigrationError("migration history contains invalid metadata")
        history.append(MigrationRecord(version, name, applied_at))
        previous = version
    return MigrationState(
        schema_version=history[-1].version if history else 0,
        history=tuple(history),
        metadata_exists=True,
    )
