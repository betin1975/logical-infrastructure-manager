from __future__ import annotations

import hashlib
import sqlite3
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import app.__main__ as app_main
from app.persistence import (
    INTERNAL_MIGRATIONS,
    MIGRATION_TABLE,
    BackupError,
    BackupManager,
    BaseRepository,
    DatabaseConfigurationError,
    DatabaseConnectionError,
    DatabaseInitializationError,
    DatabaseManager,
    Migration,
    MigrationError,
    MigrationManager,
    PersistenceError,
    Repository,
    RestoreValidationError,
    TransactionError,
)
from tests.helpers import (
    create_persistence_stack,
    migrate_database,
)

create_stack = create_persistence_stack
migrate = migrate_database
LATEST_SCHEMA_VERSION = INTERNAL_MIGRATIONS[-1].version


def test_database_creation_and_restrictive_permissions(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)

    assert stack.database.database_path == stack.runtime.paths.data / "lim.sqlite3"
    assert stack.database.database_path.is_file()
    assert stat.S_IMODE(stack.database.database_path.stat().st_mode) == 0o600


def test_database_rejects_use_before_runtime_initialization(tmp_path: Path) -> None:
    stack = create_stack(
        tmp_path,
        initialize_runtime=False,
        initialize_database=False,
    )

    with pytest.raises(
        DatabaseInitializationError, match="runtime must be initialized"
    ):
        stack.database.initialize()
    with (
        pytest.raises(DatabaseConnectionError, match="has not been initialized"),
        stack.database.connection(),
    ):
        pass


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"filename": "../escape.sqlite3"}, "visible safe filename"),
        ({"filename": ".gitkeep"}, "visible safe filename"),
        ({"foreign_keys": False}, "must be true"),
        ({"busy_timeout_ms": 0}, "busy_timeout_ms"),
        ({"busy_timeout_ms": True}, "busy_timeout_ms"),
        ({"connection_timeout_seconds": 0}, "connection_timeout_seconds"),
        ({"journal_mode": "MEMORY"}, "journal_mode"),
        ({"synchronous_mode": "OFF"}, "synchronous_mode"),
        ({"row_factory": "tuple"}, "row_factory"),
        ({"transaction_mode": "AUTOMATIC"}, "transaction_mode"),
        ({"check_same_thread": 1}, "check_same_thread"),
        ({"backup_prefix": "../backup"}, "backup_prefix"),
    ],
)
def test_invalid_database_configuration_is_rejected(
    tmp_path: Path,
    overrides: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(DatabaseConfigurationError, match=message):
        create_stack(tmp_path, overrides, initialize_database=False)


def test_connection_applies_all_sqlite_policies_and_row_factory(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)

    with stack.database.connection() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 100
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert connection.isolation_level is None
        row = connection.execute("SELECT 7 AS value").fetchone()
        assert isinstance(row, sqlite3.Row)
        assert row["value"] == 7


def test_connection_is_closed_when_context_exits(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)

    with stack.database.connection() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def test_database_rejects_symlink_file_and_data_directory(
    tmp_path: Path,
) -> None:
    stack = create_stack(tmp_path, initialize_database=False)
    target = tmp_path / "outside.sqlite3"
    target.touch()
    stack.database.database_path.symlink_to(target)

    with pytest.raises(DatabaseInitializationError, match="symlink"):
        stack.database.initialize()

    stack.database.database_path.unlink()
    data = stack.runtime.paths.data
    (data / ".gitkeep").unlink()
    data.rmdir()
    data.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(
        DatabaseInitializationError, match="directory cannot be a symlink"
    ):
        stack.database.initialize()
    assert not stack.database.is_initialized


def test_failed_database_reinitialization_clears_initialized_state(
    tmp_path: Path,
) -> None:
    stack = create_stack(tmp_path)
    stack.database.database_path.unlink()
    stack.database.database_path.symlink_to(tmp_path / "outside.sqlite3")

    with pytest.raises(DatabaseInitializationError, match="symlink"):
        stack.database.initialize()

    assert not stack.database.is_initialized


def test_transaction_commits_successfully(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)

    with stack.transactions.transaction() as connection:
        connection.execute("CREATE TABLE sample (value INTEGER NOT NULL)")
        connection.execute("INSERT INTO sample VALUES (?)", (10,))

    with stack.database.connection(read_only=True) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == 10


def test_transaction_rolls_back_without_partial_writes(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)
    with stack.transactions.transaction() as connection:
        connection.execute("CREATE TABLE sample (value INTEGER NOT NULL)")

    with (
        pytest.raises(RuntimeError, match="synthetic failure"),
        stack.transactions.transaction() as connection,
    ):
        connection.execute("INSERT INTO sample VALUES (1)")
        connection.execute("INSERT INTO sample VALUES (2)")
        raise RuntimeError("synthetic failure")

    with stack.database.connection(read_only=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 0


def test_nested_transactions_use_savepoints(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)
    with stack.transactions.transaction() as connection:
        connection.execute("CREATE TABLE sample (value INTEGER NOT NULL)")
        connection.execute("INSERT INTO sample VALUES (1)")
        with stack.transactions.transaction() as nested:
            assert nested is connection
            nested.execute("INSERT INTO sample VALUES (2)")

    with stack.database.connection(read_only=True) as connection:
        values = [row[0] for row in connection.execute("SELECT value FROM sample")]
    assert values == [1, 2]


def test_nested_failure_rolls_back_only_savepoint(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)
    with stack.transactions.transaction() as connection:
        connection.execute("CREATE TABLE sample (value INTEGER NOT NULL)")
        connection.execute("INSERT INTO sample VALUES (1)")
        with (
            pytest.raises(ValueError),
            stack.transactions.transaction() as nested,
        ):
            nested.execute("INSERT INTO sample VALUES (2)")
            raise ValueError("rollback nested work")
        connection.execute("INSERT INTO sample VALUES (3)")

    with stack.database.connection(read_only=True) as connection:
        values = [row[0] for row in connection.execute("SELECT value FROM sample")]
    assert values == [1, 3]


def test_repository_cannot_commit_managed_transaction(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)

    with (
        pytest.raises(TransactionError, match="DatabaseError"),
        stack.transactions.transaction() as connection,
    ):
        connection.execute("CREATE TABLE sample (value INTEGER)")
        connection.commit()

    with stack.database.connection(read_only=True) as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'sample'"
        ).fetchone()
    assert table is None


def test_base_repository_uses_injected_transaction_connection(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)

    class SampleRepository(BaseRepository):
        def add(self, value: int) -> None:
            self.connection.execute("INSERT INTO sample VALUES (?)", (value,))

    with stack.transactions.transaction() as connection:
        connection.execute("CREATE TABLE sample (value INTEGER)")
        repository = SampleRepository(connection)
        assert isinstance(repository, Repository)
        repository.add(42)

    with stack.database.connection(read_only=True) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == 42
    with pytest.raises(TypeError, match="sqlite3.Connection"):
        SampleRepository(object())  # type: ignore[arg-type]


def test_initial_migration_and_idempotent_history(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)
    manager = migrate(stack)

    first = manager.inspect()
    second = manager.apply_pending()

    assert first == second
    assert first.schema_version == LATEST_SCHEMA_VERSION
    assert first.metadata_exists
    assert [(item.version, item.name) for item in first.history] == [
        (1, "create_migration_metadata"),
        (2, "create_inventory_schema"),
    ]


def test_migrations_apply_in_version_order(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)
    order: list[int] = []

    def second(connection: sqlite3.Connection) -> None:
        order.append(2)
        connection.execute("CREATE TABLE second_table (id INTEGER)")

    def third(connection: sqlite3.Connection) -> None:
        order.append(3)
        connection.execute("CREATE TABLE third_table (id INTEGER)")

    manager = MigrationManager(
        stack.database,
        stack.transactions,
        migrations=(
            Migration(3, "third", third),
            INTERNAL_MIGRATIONS[0],
            Migration(2, "second", second),
        ),
    )

    assert manager.apply_pending().schema_version == 3
    assert order == [2, 3]


@pytest.mark.parametrize(
    "migrations",
    [
        (INTERNAL_MIGRATIONS[0], INTERNAL_MIGRATIONS[0]),
        (Migration(0, "invalid", lambda connection: None),),
        (Migration(1, "Invalid Name", lambda connection: None),),
        (Migration(2, "missing_first", lambda connection: None),),
        ("not-a-migration",),
    ],
)
def test_malformed_migrations_are_rejected(
    tmp_path: Path,
    migrations: tuple[Any, ...],
) -> None:
    stack = create_stack(tmp_path)
    with pytest.raises(MigrationError):
        MigrationManager(stack.database, migrations=migrations)


def test_failed_migration_rolls_back_but_preserves_earlier_history(
    tmp_path: Path,
) -> None:
    stack = create_stack(tmp_path)

    def fail(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE should_rollback (id INTEGER)")
        raise RuntimeError("secret SQL parameter must not escape")

    manager = MigrationManager(
        stack.database,
        stack.transactions,
        migrations=(
            INTERNAL_MIGRATIONS[0],
            Migration(2, "failing_migration", fail),
        ),
    )

    with pytest.raises(MigrationError) as error:
        manager.apply_pending()
    assert "secret SQL parameter" not in str(error.value)
    with stack.database.connection(read_only=True) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'should_rollback'"
        ).fetchone() is None
        versions = connection.execute(
            f"SELECT version FROM {MIGRATION_TABLE} ORDER BY version"
        ).fetchall()
    assert [row[0] for row in versions] == [1]


def test_migration_inspection_does_not_create_metadata(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)
    manager = MigrationManager(stack.database)

    state = manager.inspect()

    assert state.schema_version == 0
    assert not state.metadata_exists
    with stack.database.connection(read_only=True) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = ?", (MIGRATION_TABLE,)
        ).fetchone() is None


def test_migration_inspection_rejects_invalid_recorded_metadata(
    tmp_path: Path,
) -> None:
    stack = create_stack(tmp_path)
    with stack.transactions.transaction() as connection:
        connection.execute(
            f"CREATE TABLE {MIGRATION_TABLE} ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
            "applied_at TEXT NOT NULL)"
        )

    with pytest.raises(MigrationError, match="no initial migration"):
        MigrationManager(stack.database).inspect()


def test_backup_is_consistent_restrictive_and_reports_schema(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)
    migrate(stack)
    fixed_time = datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)
    backup = BackupManager(stack.database, stack.runtime, clock=lambda: fixed_time)

    result = backup.create_backup("foundation.sqlite3")

    assert result.path == stack.runtime.paths.backups / "foundation.sqlite3"
    assert result.size_bytes > 0
    assert result.created_at == fixed_time
    assert result.schema_version == LATEST_SCHEMA_VERSION
    assert stat.S_IMODE(result.path.stat().st_mode) == 0o600
    validation = backup.validate_restore(result.path)
    assert validation.schema_version == LATEST_SCHEMA_VERSION
    assert validation.size_bytes == result.size_bytes


def test_backup_succeeds_while_source_connection_is_open(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)
    migrate(stack)
    backup = BackupManager(stack.database, stack.runtime)

    with stack.database.connection(read_only=True) as source:
        before = source.execute(f"SELECT COUNT(*) FROM {MIGRATION_TABLE}").fetchone()[0]
        result = backup.create_backup("open-source.sqlite3")
        after = source.execute(f"SELECT COUNT(*) FROM {MIGRATION_TABLE}").fetchone()[0]

    assert before == after == LATEST_SCHEMA_VERSION
    assert result.path.is_file()


def test_backup_default_name_uses_validated_configured_prefix(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)
    migrate(stack)
    fixed_time = datetime(2026, 2, 3, 4, 5, 6, 123456, tzinfo=UTC)

    result = BackupManager(
        stack.database,
        stack.runtime,
        clock=lambda: fixed_time,
    ).create_backup()

    assert result.path.name == "lim-backup-20260203T040506123456Z.sqlite3"


@pytest.mark.parametrize(
    "name",
    ["../escape.sqlite3", "nested/backup.sqlite3", ".hidden", "bad name.sqlite3"],
)
def test_backup_rejects_unsafe_names(tmp_path: Path, name: str) -> None:
    stack = create_stack(tmp_path)
    migrate(stack)
    with pytest.raises(BackupError, match="backup name"):
        BackupManager(stack.database, stack.runtime).create_backup(name)


def test_backup_rejects_symlink_destination(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)
    migrate(stack)
    destination = stack.runtime.paths.backups / "linked.sqlite3"
    destination.symlink_to(tmp_path / "outside.sqlite3")

    with pytest.raises(BackupError, match="already exists"):
        BackupManager(stack.database, stack.runtime).create_backup(destination.name)


def test_backup_rejects_symlink_directory(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)
    migrate(stack)
    backup_directory = stack.runtime.paths.backups
    (backup_directory / ".gitkeep").unlink()
    backup_directory.rmdir()
    backup_directory.symlink_to(tmp_path, target_is_directory=True)

    with pytest.raises(BackupError, match="directory cannot be a symlink"):
        BackupManager(stack.database, stack.runtime).create_backup("backup.sqlite3")


def test_backup_failure_cleans_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = create_stack(tmp_path)
    migrate(stack)
    manager = BackupManager(stack.database, stack.runtime)

    def fail(_path: Path) -> None:
        raise sqlite3.OperationalError("synthetic backup failure")

    monkeypatch.setattr(manager, "_copy_database", fail)
    with pytest.raises(BackupError, match="OperationalError"):
        manager.create_backup("failed.sqlite3")

    assert not (stack.runtime.paths.backups / "failed.sqlite3").exists()
    assert not list(stack.runtime.paths.backups.glob(".lim-backup-*.tmp"))


def test_restore_validation_rejects_corrupt_and_missing_metadata(
    tmp_path: Path,
) -> None:
    stack = create_stack(tmp_path)
    manager = BackupManager(stack.database, stack.runtime)
    corrupt = stack.runtime.paths.backups / "corrupt.sqlite3"
    corrupt.write_bytes(b"not a SQLite database")
    with pytest.raises(RestoreValidationError, match="invalid"):
        manager.validate_restore(corrupt)

    empty = stack.runtime.paths.backups / "empty.sqlite3"
    sqlite3.connect(empty).close()
    with pytest.raises(RestoreValidationError, match="lacks LIM migration metadata"):
        manager.validate_restore(empty)


def test_restore_validation_rejects_symlink_and_preserves_active_database(
    tmp_path: Path,
) -> None:
    stack = create_stack(tmp_path)
    migrate(stack)
    backup = BackupManager(stack.database, stack.runtime)
    result = backup.create_backup("valid.sqlite3")
    active_hash = hashlib.sha256(stack.database.database_path.read_bytes()).digest()
    link = stack.runtime.paths.backups / "candidate.sqlite3"
    link.symlink_to(result.path)

    with pytest.raises(RestoreValidationError, match="non-symlink"):
        backup.validate_restore(link)

    current_hash = hashlib.sha256(stack.database.database_path.read_bytes()).digest()
    assert current_hash == active_hash


def test_concurrent_connections_support_readers(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)
    with stack.transactions.transaction() as connection:
        connection.execute("CREATE TABLE sample (value INTEGER)")
        connection.executemany("INSERT INTO sample VALUES (?)", [(1,), (2,), (3,)])

    def read_total() -> int:
        with stack.database.connection(read_only=True) as connection:
            return connection.execute("SELECT SUM(value) FROM sample").fetchone()[0]

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(lambda _: read_total(), range(8))) == [6] * 8


def test_writer_contention_honors_busy_timeout(tmp_path: Path) -> None:
    stack = create_stack(tmp_path, {"busy_timeout_ms": 50})
    with stack.transactions.transaction() as connection:
        connection.execute("CREATE TABLE sample (value INTEGER)")

    attempted = threading.Event()
    with stack.database.connection() as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO sample VALUES (1)")

        def contend() -> str:
            attempted.set()
            try:
                with stack.database.connection() as connection:
                    connection.execute("INSERT INTO sample VALUES (2)")
            except sqlite3.OperationalError as exc:
                return str(exc)
            return "unexpected success"

        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(contend).result(timeout=2)
        assert attempted.is_set()
        assert "locked" in result
        writer.rollback()


def test_wal_reader_sees_committed_snapshot_during_writer(tmp_path: Path) -> None:
    stack = create_stack(tmp_path)
    with stack.transactions.transaction() as connection:
        connection.execute("CREATE TABLE sample (value INTEGER)")
        connection.execute("INSERT INTO sample VALUES (1)")

    with stack.database.connection() as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO sample VALUES (2)")
        with stack.database.connection(read_only=True) as reader:
            assert reader.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert reader.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 1
        writer.rollback()


def test_application_startup_creates_migrated_database_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = create_stack(tmp_path, initialize_database=False)
    log_events: list[tuple[str, tuple[object, ...]]] = []

    class FakeLogger:
        def info(self, message: str, *args: object) -> None:
            log_events.append((message, args))

        def exception(self, message: str) -> None:
            log_events.append((message, ()))

    class FakeLoggingManager:
        def __init__(self, config: object, runtime: object) -> None:
            pass

        def initialize(self) -> None:
            pass

        def get_logger(self, component: str, **context: object) -> FakeLogger:
            return FakeLogger()

    monkeypatch.setattr(app_main, "ConfigManager", lambda: stack.config)
    monkeypatch.setattr(app_main, "LoggingManager", FakeLoggingManager)

    assert app_main.main() == 0
    assert app_main.main() == 0

    database = DatabaseManager(stack.config, stack.runtime)
    database.initialize()
    assert MigrationManager(database).schema_version() == LATEST_SCHEMA_VERSION
    assert log_events == [
        (
            "LIM startup foundation initialized with schema_version=%d",
            (LATEST_SCHEMA_VERSION,),
        ),
        (
            "LIM startup foundation initialized with schema_version=%d",
            (LATEST_SCHEMA_VERSION,),
        ),
    ]
    assert all("sqlite" not in message.lower() for message, _args in log_events)


def test_application_startup_logs_redacted_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeRuntime:
        def __init__(self, config: object, *, application_root: Path) -> None:
            pass

        def initialize(self) -> None:
            pass

    class FakeLogger:
        def exception(self, message: str) -> None:
            events.append(message)

    class FakeLogging:
        def __init__(self, config: object, runtime: object) -> None:
            pass

        def initialize(self) -> None:
            pass

        def get_logger(self, component: str, **context: object) -> FakeLogger:
            return FakeLogger()

    class FailingDatabase:
        def __init__(self, config: object, runtime: object) -> None:
            pass

        def initialize(self) -> None:
            raise PersistenceError("password=database-secret")

    monkeypatch.setattr(app_main, "ConfigManager", object)
    monkeypatch.setattr(app_main, "RuntimeManager", FakeRuntime)
    monkeypatch.setattr(app_main, "LoggingManager", FakeLogging)
    monkeypatch.setattr(app_main, "DatabaseManager", FailingDatabase)

    assert app_main.main() == 1
    assert events == ["LIM persistence initialization failed"]
