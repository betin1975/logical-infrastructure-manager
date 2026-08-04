from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from app.inventory import (
    DuplicateInventoryError,
    HealthStatus,
    InventoryConflictError,
    InventoryRepository,
    InventoryRepositoryError,
    Label,
    ServerNotFoundError,
    ServerStatus,
    Tag,
)
from app.persistence import (
    INTERNAL_MIGRATIONS,
    MigrationError,
    MigrationManager,
    SQLiteInventoryRepository,
    TransactionManager,
)
from tests.helpers import (
    INVENTORY_NOW as NOW,
)
from tests.helpers import (
    create_persistence_stack,
    migrate_database,
)
from tests.helpers import (
    make_inventory_server as make_server,
)


@pytest.fixture
def repository(tmp_path: Path) -> SQLiteInventoryRepository:
    stack = create_persistence_stack(tmp_path)
    migrate_database(stack)
    return SQLiteInventoryRepository(stack.database, stack.transactions)


def test_inventory_migration_creates_only_normalized_inventory_tables_and_indexes(
    tmp_path: Path,
) -> None:
    stack = create_persistence_stack(tmp_path)
    state = MigrationManager(
        stack.database,
        stack.transactions,
        migrations=INTERNAL_MIGRATIONS[:2],
    ).apply_pending()

    assert state.schema_version == 2
    with stack.database.connection(read_only=True) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND name LIKE 'idx_inventory_%'"
            )
        }

    assert tables == {
        "lim_schema_migrations",
        "inventory_servers",
        "inventory_server_addresses",
        "inventory_tags",
        "inventory_server_tags",
        "inventory_server_labels",
    }
    assert indexes == {
        "idx_inventory_servers_enabled",
        "idx_inventory_servers_managed",
        "idx_inventory_servers_health",
        "idx_inventory_servers_status",
        "idx_inventory_addresses_server",
        "idx_inventory_server_tags_tag",
        "idx_inventory_labels_key_value",
    }


def test_inventory_migration_failure_rolls_back_partial_schema(tmp_path: Path) -> None:
    stack = create_persistence_stack(tmp_path)
    MigrationManager(
        stack.database,
        stack.transactions,
        migrations=(INTERNAL_MIGRATIONS[0],),
    ).apply_pending()
    with stack.transactions.transaction() as connection:
        connection.execute("CREATE TABLE inventory_tags (conflict TEXT)")

    with pytest.raises(MigrationError, match="migration 2 failed"):
        MigrationManager(stack.database, stack.transactions).apply_pending()

    with stack.database.connection(read_only=True) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        versions = connection.execute(
            "SELECT version FROM lim_schema_migrations ORDER BY version"
        ).fetchall()
    assert "inventory_servers" not in tables
    assert [row[0] for row in versions] == [1]


def test_repository_requires_initialized_matching_persistence_dependencies(
    tmp_path: Path,
) -> None:
    uninitialized = create_persistence_stack(
        tmp_path / "uninitialized",
        initialize_database=False,
    )
    with pytest.raises(InventoryRepositoryError, match="must be initialized"):
        SQLiteInventoryRepository(
            uninitialized.database,
            uninitialized.transactions,
        )

    first = create_persistence_stack(tmp_path / "first")
    second = create_persistence_stack(tmp_path / "second")
    with pytest.raises(InventoryRepositoryError, match="share one database"):
        SQLiteInventoryRepository(
            first.database,
            TransactionManager(second.database),
        )


def test_repository_create_round_trips_full_normalized_server(
    repository: SQLiteInventoryRepository,
) -> None:
    server = make_server()

    assert isinstance(repository, InventoryRepository)
    assert repository.create(server) == server
    assert repository.find_by_uuid(server.uuid) == server
    assert repository.find_by_hostname("SERVER-01.EXAMPLE.TEST") == server
    assert repository.find_by_address("2001:db8::10") == server


@pytest.mark.parametrize(
    "duplicate",
    [
        make_server(uuid=UUID("22222222-2222-4222-8222-222222222222")),
        make_server(
            uuid=UUID("22222222-2222-4222-8222-222222222222"),
            hostname="different.example.test",
            primary_address="2001:db8::10",
            management_address="192.0.2.20",
        ),
        make_server(
            uuid=UUID("22222222-2222-4222-8222-222222222222"),
            hostname="different.example.test",
            primary_address="192.0.2.20",
            management_address="192.0.2.10",
        ),
    ],
)
def test_repository_rejects_duplicate_hostname_or_cross_kind_address(
    repository: SQLiteInventoryRepository,
    duplicate: object,
) -> None:
    repository.create(make_server())
    with pytest.raises(DuplicateInventoryError, match="reserved"):
        repository.create(duplicate)  # type: ignore[arg-type]
    assert repository.count() == 1


def test_repository_update_replaces_children_and_detects_stale_versions(
    repository: SQLiteInventoryRepository,
) -> None:
    original = repository.create(make_server())
    changed = original.evolve(
        now=NOW + timedelta(minutes=1),
        hostname="renamed.example.test",
        primary_address="192.0.2.11",
        management_address=None,
        tags=frozenset({Tag("database")}),
        labels=frozenset({Label("owner", "data")}),
        health_status=HealthStatus.HEALTHY,
    )

    assert repository.update(changed) == changed
    assert repository.find_by_hostname(original.hostname) is None
    assert repository.find_by_address(original.primary_address) is None
    assert repository.find_by_uuid(original.uuid) == changed
    with pytest.raises(InventoryConflictError, match="stale"):
        repository.update(changed)


def test_duplicate_hostname_update_rolls_back_all_changes(
    repository: SQLiteInventoryRepository,
) -> None:
    first = repository.create(make_server())
    second = repository.create(
        make_server(
            uuid=UUID("22222222-2222-4222-8222-222222222222"),
            hostname="server-02.example.test",
            primary_address="192.0.2.20",
            management_address=None,
        )
    )
    conflicting = second.evolve(
        now=NOW + timedelta(minutes=1),
        hostname=first.hostname,
        display_name="Must roll back",
    )

    with pytest.raises(DuplicateInventoryError, match="hostname"):
        repository.update(conflicting)

    assert repository.find_by_uuid(second.uuid) == second


def test_failed_child_update_rolls_back_parent_and_addresses(
    repository: SQLiteInventoryRepository,
) -> None:
    first = repository.create(make_server())
    second = repository.create(
        make_server(
            uuid=UUID("22222222-2222-4222-8222-222222222222"),
            hostname="server-02.example.test",
            primary_address="192.0.2.20",
            management_address=None,
        )
    )
    conflicting = second.evolve(
        now=NOW + timedelta(minutes=1),
        display_name="Must roll back",
        primary_address=first.primary_address,
    )

    with pytest.raises(DuplicateInventoryError, match="address"):
        repository.update(conflicting)

    assert repository.find_by_uuid(second.uuid) == second
    assert repository.find_by_address(second.primary_address) == second


def test_soft_delete_hides_server_reserves_identity_and_restore_recovers(
    repository: SQLiteInventoryRepository,
) -> None:
    server = repository.create(make_server())
    deleted = server.soft_delete(now=NOW + timedelta(minutes=1))

    assert repository.delete(deleted) == deleted
    assert repository.find_by_uuid(server.uuid) is None
    assert repository.find_by_uuid(server.uuid, include_deleted=True) == deleted
    assert repository.count() == 0
    assert repository.count(include_deleted=True) == 1
    with pytest.raises(DuplicateInventoryError):
        repository.create(
            make_server(
                uuid=UUID("22222222-2222-4222-8222-222222222222"),
                hostname=server.hostname,
                primary_address="192.0.2.20",
                management_address=None,
            )
        )

    restored = deleted.restore(now=NOW + timedelta(minutes=2))
    assert repository.restore(restored) == restored
    assert repository.find_by_uuid(server.uuid) == restored


def test_repository_list_filters_paginates_and_counts(
    repository: SQLiteInventoryRepository,
) -> None:
    servers = []
    for number in range(5):
        server = make_server(
            uuid=UUID(f"00000000-0000-4000-8000-{number + 1:012d}"),
            hostname=f"server-{number}.example.test",
            display_name=f"Server {number}",
            primary_address=f"192.0.2.{number + 1}",
            management_address=None,
            enabled=number != 1,
            managed=number != 2,
            status=ServerStatus.DISABLED if number == 1 else ServerStatus.ACTIVE,
        )
        servers.append(repository.create(server))

    first = repository.list_all(limit=2)
    second = repository.list_all(limit=2, offset=2)

    assert first.total == 5
    assert len(first.items) == 2
    assert first.has_more
    assert len(second.items) == 2
    assert repository.find_enabled().total == 4
    assert repository.find_managed().total == 4
    assert repository.count() == 5


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (1001, 0), (True, 0), (10, -1), (10, False)],
)
def test_repository_rejects_invalid_pagination(
    repository: SQLiteInventoryRepository,
    limit: object,
    offset: object,
) -> None:
    with pytest.raises(InventoryRepositoryError):
        repository.list_all(limit=limit, offset=offset)  # type: ignore[arg-type]


def test_repository_search_tag_and_health_lookup(
    repository: SQLiteInventoryRepository,
) -> None:
    healthy = repository.create(make_server(health_status=HealthStatus.HEALTHY))
    repository.create(
        make_server(
            uuid=UUID("22222222-2222-4222-8222-222222222222"),
            hostname="database.example.test",
            display_name="Database Node",
            primary_address="192.0.2.20",
            management_address=None,
            environment="production",
            location="dc-west",
            tags=frozenset({Tag("database")}),
            labels=frozenset({Label("role", "primary")}),
            health_status=HealthStatus.UNHEALTHY,
        )
    )

    assert repository.search("Database").total == 1
    assert repository.search("192.0.2.20").total == 1
    assert repository.search("primary").total == 1
    assert repository.find_by_tag("web").items == (healthy,)
    assert repository.find_by_health(HealthStatus.HEALTHY).items == (healthy,)
    assert repository.find_by_health(HealthStatus.UNHEALTHY).total == 1


def test_search_escapes_wildcards_and_can_explicitly_include_deleted(
    repository: SQLiteInventoryRepository,
) -> None:
    server = repository.create(make_server(display_name="Load 100%"))
    assert repository.search("%").items == (server,)

    deleted = server.soft_delete(now=NOW + timedelta(minutes=1))
    repository.delete(deleted)
    assert repository.search("%").total == 0
    assert repository.search("%", include_deleted=True).items == (deleted,)


def test_repository_rejects_changed_creation_timestamp(
    repository: SQLiteInventoryRepository,
) -> None:
    server = repository.create(make_server())
    invalid = make_server(
        created_at=NOW + timedelta(seconds=1),
        updated_at=NOW + timedelta(seconds=1),
        inventory_version=server.inventory_version + 1,
    )

    with pytest.raises(InventoryConflictError, match="creation timestamp"):
        repository.update(invalid)


def test_repository_missing_server_and_invalid_lifecycle_are_descriptive(
    repository: SQLiteInventoryRepository,
) -> None:
    server = make_server().evolve(now=NOW + timedelta(minutes=1), notes="new")
    with pytest.raises(ServerNotFoundError):
        repository.update(server)
    with pytest.raises(InventoryRepositoryError, match="soft-deleted"):
        repository.delete(make_server())
    with pytest.raises(InventoryRepositoryError, match="restored"):
        repository.restore(make_server().soft_delete(now=NOW + timedelta(minutes=1)))


def test_inventory_foreign_keys_and_enum_checks_are_enforced(tmp_path: Path) -> None:
    stack = create_persistence_stack(tmp_path)
    migrate_database(stack)

    with (
        pytest.raises(sqlite3.IntegrityError),
        stack.database.connection() as connection,
    ):
        connection.execute(
            "INSERT INTO inventory_server_addresses (address, server_uuid, kind) "
            "VALUES ('192.0.2.1', 'missing', 'primary')"
        )

    with (
        pytest.raises(sqlite3.IntegrityError),
        stack.database.connection() as connection,
    ):
        values = list(_minimal_server_row())
        values[3] = "invalid-platform"
        connection.execute(
            """
            INSERT INTO inventory_servers (
                uuid, hostname, display_name, platform, operating_system,
                server_type, enabled, managed, discovery_state, health_status,
                status, failure_count, created_at, updated_at,
                synchronization_state, inventory_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )


def _minimal_server_row() -> tuple[object, ...]:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
    return (
        "33333333-3333-4333-8333-333333333333",
        "raw.example.test",
        "Raw",
        "unknown",
        "unknown",
        "unknown",
        1,
        1,
        "unknown",
        "unknown",
        "active",
        0,
        timestamp,
        timestamp,
        "pending",
        1,
    )
