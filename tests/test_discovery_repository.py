from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from app.discovery import (
    DiscoveryConflictError,
    DiscoveryRepository,
    DiscoveryRepositoryError,
    DiscoveryStatus,
    ObservationSource,
    ObservationState,
    SynchronizationState,
)
from app.persistence import (
    INTERNAL_MIGRATIONS,
    MigrationError,
    MigrationManager,
    SQLiteDiscoveryRepository,
    SQLiteInventoryRepository,
    TransactionError,
    TransactionManager,
)
from tests.helpers import (
    DISCOVERY_OBSERVATION_ID,
    create_persistence_stack,
    make_discovery_observation,
    make_inventory_server,
    migrate_database,
)
from tests.helpers import INVENTORY_NOW as NOW
from tests.helpers import INVENTORY_SERVER_ID as SERVER_ID


@pytest.fixture
def repository(tmp_path: Path) -> SQLiteDiscoveryRepository:
    stack = create_persistence_stack(tmp_path)
    migrate_database(stack)
    SQLiteInventoryRepository(stack.database, stack.transactions).create(
        make_inventory_server()
    )
    return SQLiteDiscoveryRepository(stack.database, stack.transactions)


def test_discovery_migration_creates_normalized_tables_and_indexes(
    tmp_path: Path,
) -> None:
    stack = create_persistence_stack(tmp_path)
    state = migrate_database(stack).inspect()
    assert state.schema_version == 3
    with stack.database.connection(read_only=True) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name LIKE 'discovery_%'"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name LIKE 'idx_discovery_%'"
            )
        }
    assert tables == {
        "discovery_observations",
        "discovery_interfaces",
        "discovery_addresses",
        "discovery_disks",
        "discovery_services",
        "discovery_packages",
        "discovery_containers",
        "discovery_processes",
        "discovery_metadata",
    }
    assert "idx_discovery_observations_server_time" in indexes
    assert "idx_discovery_observations_state_updated" in indexes
    assert "idx_discovery_addresses_address" in indexes


def test_discovery_migration_failure_rolls_back_all_partial_tables(
    tmp_path: Path,
) -> None:
    stack = create_persistence_stack(tmp_path)
    MigrationManager(
        stack.database, stack.transactions, migrations=INTERNAL_MIGRATIONS[:2]
    ).apply_pending()
    with stack.transactions.transaction() as connection:
        connection.execute("CREATE TABLE discovery_packages(conflict TEXT)")
    with pytest.raises(MigrationError, match="migration 3 failed"):
        MigrationManager(stack.database, stack.transactions).apply_pending()
    with stack.database.connection(read_only=True) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM lim_schema_migrations").fetchone()[
                0
            ]
            == 2
        )
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='discovery_observations'"
            ).fetchone()
            is None
        )


def test_repository_requires_initialized_matching_dependencies(tmp_path: Path) -> None:
    empty = create_persistence_stack(tmp_path / "empty", initialize_database=False)
    with pytest.raises(DiscoveryRepositoryError, match="initialized"):
        SQLiteDiscoveryRepository(empty.database, empty.transactions)
    first = create_persistence_stack(tmp_path / "first")
    second = create_persistence_stack(tmp_path / "second")
    with pytest.raises(DiscoveryRepositoryError, match="share one database"):
        SQLiteDiscoveryRepository(first.database, TransactionManager(second.database))


def test_repository_round_trips_all_normalized_facts(
    repository: SQLiteDiscoveryRepository,
) -> None:
    observation = make_discovery_observation()
    assert isinstance(repository, DiscoveryRepository)
    assert repository.create(observation) == observation
    assert repository.find_by_uuid(observation.uuid) == observation
    assert repository.find_latest(SERVER_ID) == observation
    assert repository.count() == repository.count(server_uuid=SERVER_ID) == 1


def test_history_filters_search_and_pagination(
    repository: SQLiteDiscoveryRepository,
) -> None:
    first = make_discovery_observation()
    second = make_discovery_observation(
        uuid=UUID("33333333-3333-4333-8333-333333333333"),
        discovered_at=NOW + timedelta(seconds=1),
        created_at=NOW + timedelta(seconds=1),
        updated_at=NOW + timedelta(seconds=1),
        source=ObservationSource.PLUGIN,
        hostname="new.example.test",
    )
    repository.create(first)
    repository.create(second)
    assert repository.history(SERVER_ID, limit=1).items == (second,)
    assert repository.history(SERVER_ID, limit=1).has_more
    assert repository.find_latest(SERVER_ID, source=ObservationSource.MANUAL) == first
    assert repository.list_by_source(ObservationSource.PLUGIN).items == (second,)
    assert repository.list_by_status(DiscoveryStatus.UNKNOWN).total == 2
    assert repository.list_by_state(ObservationState.PENDING).total == 2
    assert repository.search("redis").total == 2
    assert repository.search("192.0.2.10").total == 2
    assert repository.search("new.example").items == (second,)


def test_lifecycle_updates_are_optimistic_and_preserve_facts(
    repository: SQLiteDiscoveryRepository,
) -> None:
    original = repository.create(make_discovery_observation())
    successful = original.transition(
        ObservationState.SUCCESSFUL, now=NOW + timedelta(seconds=1)
    )
    assert repository.update(successful) == successful
    assert repository.find_by_uuid(original.uuid) == successful
    with pytest.raises(DiscoveryConflictError, match="stale"):
        repository.update(successful)


def test_repository_rejects_fact_and_synchronization_mutation(
    repository: SQLiteDiscoveryRepository,
) -> None:
    original = repository.create(make_discovery_observation())
    successful = original.transition(
        ObservationState.SUCCESSFUL, now=NOW + timedelta(seconds=1)
    )
    with pytest.raises(DiscoveryConflictError, match="facts are immutable"):
        repository.update(replace(successful, hostname="changed.example.test"))
    with pytest.raises(DiscoveryConflictError, match="authoritative inventory"):
        repository.update(
            replace(successful, synchronization_state=SynchronizationState.ACCEPTED)
        )


def test_cleanup_only_purges_expired_history_before_cutoff(
    repository: SQLiteDiscoveryRepository,
) -> None:
    old = (
        make_discovery_observation()
        .transition(
            ObservationState.FAILED,
            now=NOW + timedelta(seconds=1),
            failure_reason="synthetic",
        )
        .transition(ObservationState.EXPIRED, now=NOW + timedelta(seconds=2))
    )
    pending = make_discovery_observation(
        uuid=UUID("44444444-4444-4444-8444-444444444444")
    )
    repository.create(
        replace(
            old,
            state=ObservationState.PENDING,
            status=DiscoveryStatus.UNKNOWN,
            failure_reason=None,
            version=1,
            updated_at=NOW,
        )
    )
    repository.update(
        replace(
            old,
            version=2,
            state=ObservationState.FAILED,
            status=DiscoveryStatus.FAILED,
            failure_reason="synthetic",
            updated_at=NOW + timedelta(seconds=1),
        )
    )
    repository.update(old)
    repository.create(pending)
    assert repository.cleanup(before=NOW + timedelta(seconds=3)) == 1
    assert repository.find_by_uuid(DISCOVERY_OBSERVATION_ID) is None
    assert repository.find_by_uuid(pending.uuid) == pending


def test_create_rolls_back_parent_when_child_constraint_fails(
    repository: SQLiteDiscoveryRepository,
) -> None:
    with repository._transactions.transaction() as connection:  # noqa: SLF001
        connection.execute(
            "CREATE TRIGGER reject_discovery_address "
            "BEFORE INSERT ON discovery_addresses "
            "BEGIN SELECT RAISE(ABORT, 'synthetic child failure'); END"
        )
    with pytest.raises(DiscoveryRepositoryError, match="write failed"):
        repository.create(make_discovery_observation())
    assert repository.count() == 0


def test_foreign_key_prevents_observations_for_unknown_inventory(
    repository: SQLiteDiscoveryRepository,
) -> None:
    with pytest.raises(DiscoveryRepositoryError):
        repository.create(
            make_discovery_observation(
                server_uuid=UUID("99999999-9999-4999-8999-999999999999")
            )
        )


@pytest.mark.parametrize("limit,offset", [(0, 0), (1001, 0), (1, -1), (True, 0)])
def test_repository_rejects_invalid_pagination(
    repository: SQLiteDiscoveryRepository, limit: int, offset: int
) -> None:
    with pytest.raises(DiscoveryRepositoryError):
        repository.history(SERVER_ID, limit=limit, offset=offset)


def test_database_constraints_reject_invalid_states(
    repository: SQLiteDiscoveryRepository,
) -> None:
    repository.create(make_discovery_observation())
    with (
        pytest.raises(TransactionError, match="IntegrityError"),
        repository._transactions.transaction() as connection,  # noqa: SLF001
    ):
        connection.execute(
            "UPDATE discovery_observations SET state='invalid' WHERE uuid=?",
            (str(DISCOVERY_OBSERVATION_ID),),
        )

    with (
        pytest.raises(TransactionError, match="IntegrityError"),
        repository._transactions.transaction() as connection,  # noqa: SLF001
    ):
        connection.execute(
            "UPDATE discovery_observations "
            "SET state='successful', status='unknown' WHERE uuid=?",
            (str(DISCOVERY_OBSERVATION_ID),),
        )
