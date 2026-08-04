"""SQLite implementation of the inventory-owned repository interface."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TypeVar
from uuid import UUID

from ..inventory import (
    DiscoveryState,
    DuplicateInventoryError,
    HealthStatus,
    InventoryConflictError,
    InventoryError,
    InventoryRepositoryError,
    Label,
    OperatingSystem,
    Platform,
    RepositoryResult,
    Server,
    ServerNotFoundError,
    ServerStatus,
    ServerType,
    SynchronizationState,
    Tag,
)
from ..inventory.validation import (
    normalize_address,
    normalize_hostname,
    normalize_tag_name,
    normalize_uuid,
)
from .database import DatabaseManager
from .errors import PersistenceError
from .repository import BaseRepository
from .transactions import TransactionManager

_SERVER_COLUMNS = """
    s.uuid, s.hostname, s.display_name,
    primary_address.address AS primary_address,
    management_address.address AS management_address,
    s.platform, s.operating_system, s.distribution,
    s.distribution_version, s.kernel_version, s.architecture,
    s.server_type, s.environment, s.location, s.description,
    s.enabled, s.managed, s.discovery_state, s.health_status, s.status,
    s.last_poll_at, s.last_successful_poll_at, s.last_failure_at,
    s.failure_count, s.last_bootstrap_at, s.created_at, s.updated_at,
    s.deleted_at, s.synchronization_state, s.inventory_version, s.notes
"""
_SERVER_FROM = """
    FROM inventory_servers AS s
    JOIN inventory_server_addresses AS primary_address
      ON primary_address.server_uuid = s.uuid
     AND primary_address.kind = 'primary'
    LEFT JOIN inventory_server_addresses AS management_address
      ON management_address.server_uuid = s.uuid
     AND management_address.kind = 'management'
"""
_T = TypeVar("_T")


class SQLiteInventoryRepository:
    """Persist inventory through short explicit transactions.

    This is the only inventory class that receives persistence infrastructure.
    Consumers depend on ``InventoryRepository`` instead of this implementation.
    """

    def __init__(
        self,
        database: DatabaseManager,
        transactions: TransactionManager,
    ) -> None:
        if not database.is_initialized:
            raise InventoryRepositoryError(
                "database must be initialized before the inventory repository"
            )
        if transactions.database is not database:
            raise InventoryRepositoryError(
                "inventory repository dependencies must share one database"
            )
        self._transactions = transactions

    def create(self, server: Server) -> Server:
        """Insert a new server and all normalized child records."""
        def operation(connection: sqlite3.Connection) -> Server:
            queries = _InventoryQueries(connection)
            queries.assert_unique(server)
            queries.insert(server)
            return server

        return self._write(operation)

    def update(self, server: Server) -> Server:
        """Replace an active server using optimistic inventory versioning."""
        return self._write(
            lambda connection: _InventoryQueries(connection).replace(
                server,
                existing_deleted=False,
            )
        )

    def delete(self, server: Server) -> Server:
        """Persist a domain-approved soft-deleted version."""
        if server.deleted_at is None or server.status is not ServerStatus.DELETED:
            raise InventoryRepositoryError("delete requires a soft-deleted server")
        return self._write(
            lambda connection: _InventoryQueries(connection).replace(
                server,
                existing_deleted=False,
            )
        )

    def restore(self, server: Server) -> Server:
        """Persist a domain-approved restored version."""
        if server.deleted_at is not None or server.status is ServerStatus.DELETED:
            raise InventoryRepositoryError("restore requires a restored server")
        return self._write(
            lambda connection: _InventoryQueries(connection).replace(
                server,
                existing_deleted=True,
            )
        )

    def find_by_uuid(
        self,
        server_uuid: UUID,
        *,
        include_deleted: bool = False,
    ) -> Server | None:
        """Find one server by UUID."""
        normalized = str(normalize_uuid(server_uuid))
        deleted_clause = "" if include_deleted else " AND s.deleted_at IS NULL"
        return self._read(
            lambda queries: queries.find_one(
                f"s.uuid = ?{deleted_clause}",
                (normalized,),
            )
        )

    def find_by_hostname(
        self,
        hostname: str,
        *,
        include_deleted: bool = False,
    ) -> Server | None:
        """Find one server by case-insensitive normalized hostname."""
        normalized = normalize_hostname(hostname)
        deleted_clause = "" if include_deleted else " AND s.deleted_at IS NULL"
        return self._read(
            lambda queries: queries.find_one(
                f"s.hostname = ? COLLATE NOCASE{deleted_clause}",
                (normalized,),
            )
        )

    def find_by_address(
        self,
        address: str,
        *,
        include_deleted: bool = False,
    ) -> Server | None:
        """Find one server by canonical primary or management address."""
        normalized = normalize_address(address)
        deleted_clause = "" if include_deleted else " AND s.deleted_at IS NULL"
        return self._read(
            lambda queries: queries.find_one(
                "EXISTS (SELECT 1 FROM inventory_server_addresses lookup_address "
                "WHERE lookup_address.server_uuid = s.uuid "
                f"AND lookup_address.address = ?){deleted_clause}",
                (normalized,),
            )
        )

    def find_enabled(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> RepositoryResult[Server]:
        """Return enabled, non-deleted servers."""
        return self._page(
            "s.enabled = 1 AND s.deleted_at IS NULL",
            (),
            limit=limit,
            offset=offset,
        )

    def find_managed(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> RepositoryResult[Server]:
        """Return managed, non-deleted servers."""
        return self._page(
            "s.managed = 1 AND s.deleted_at IS NULL",
            (),
            limit=limit,
            offset=offset,
        )

    def list_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> RepositoryResult[Server]:
        """Return a stable inventory page."""
        where = "1 = 1" if include_deleted else "s.deleted_at IS NULL"
        return self._page(where, (), limit=limit, offset=offset)

    def search(
        self,
        query: str,
        *,
        limit: int = 100,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> RepositoryResult[Server]:
        """Search normalized server, address, tag, and label fields."""
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 256:
            raise InventoryRepositoryError(
                "search query must contain 1 to 256 characters"
            )
        escaped = (
            query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        searchable = """
            (s.hostname LIKE ? ESCAPE '\\' COLLATE NOCASE
             OR s.display_name LIKE ? ESCAPE '\\' COLLATE NOCASE
             OR COALESCE(s.environment, '') LIKE ? ESCAPE '\\' COLLATE NOCASE
             OR COALESCE(s.location, '') LIKE ? ESCAPE '\\' COLLATE NOCASE
             OR EXISTS (
                 SELECT 1 FROM inventory_server_addresses search_address
                 WHERE search_address.server_uuid = s.uuid
                   AND search_address.address LIKE ? ESCAPE '\\'
             )
             OR EXISTS (
                 SELECT 1
                 FROM inventory_server_tags search_server_tag
                 JOIN inventory_tags search_tag
                   ON search_tag.id = search_server_tag.tag_id
                 WHERE search_server_tag.server_uuid = s.uuid
                   AND search_tag.name LIKE ? ESCAPE '\\' COLLATE NOCASE
             )
             OR EXISTS (
                 SELECT 1 FROM inventory_server_labels search_label
                 WHERE search_label.server_uuid = s.uuid
                   AND (search_label.key LIKE ? ESCAPE '\\' COLLATE NOCASE
                        OR search_label.value LIKE ? ESCAPE '\\' COLLATE NOCASE)
             ))
        """
        if not include_deleted:
            searchable += " AND s.deleted_at IS NULL"
        return self._page(
            searchable,
            (pattern,) * 8,
            limit=limit,
            offset=offset,
        )

    def count(self, *, include_deleted: bool = False) -> int:
        """Count inventory servers."""
        where = "1 = 1" if include_deleted else "deleted_at IS NULL"
        return self._read(
            lambda queries: queries.connection.execute(
                f"SELECT COUNT(*) FROM inventory_servers WHERE {where}"
            ).fetchone()[0]
        )

    def find_by_tag(
        self,
        tag: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> RepositoryResult[Server]:
        """Return non-deleted servers carrying an exact tag."""
        normalized = normalize_tag_name(tag)
        return self._page(
            "s.deleted_at IS NULL AND EXISTS ("
            "SELECT 1 FROM inventory_server_tags lookup_server_tag "
            "JOIN inventory_tags lookup_tag "
            "ON lookup_tag.id = lookup_server_tag.tag_id "
            "WHERE lookup_server_tag.server_uuid = s.uuid "
            "AND lookup_tag.name = ? COLLATE NOCASE)",
            (normalized,),
            limit=limit,
            offset=offset,
        )

    def find_by_health(
        self,
        health_status: HealthStatus,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> RepositoryResult[Server]:
        """Return non-deleted servers with an exact health state."""
        if not isinstance(health_status, HealthStatus):
            raise InventoryRepositoryError("health status must be HealthStatus")
        return self._page(
            "s.health_status = ? AND s.deleted_at IS NULL",
            (health_status.value,),
            limit=limit,
            offset=offset,
        )

    def _page(
        self,
        where: str,
        parameters: tuple[object, ...],
        *,
        limit: int,
        offset: int,
    ) -> RepositoryResult[Server]:
        _validate_pagination(limit, offset)
        return self._read(
            lambda queries: queries.page(
                where,
                parameters,
                limit=limit,
                offset=offset,
            )
        )

    def _read(self, operation: Callable[[_InventoryQueries], _T]) -> _T:
        try:
            with self._transactions.transaction() as connection:
                return operation(_InventoryQueries(connection))
        except (DuplicateInventoryError, InventoryConflictError, ServerNotFoundError):
            raise
        except PersistenceError as exc:
            raise _translate_repository_error(exc) from exc
        except (sqlite3.Error, ValueError, TypeError) as exc:
            raise InventoryRepositoryError(
                f"inventory read failed due to {type(exc).__name__}"
            ) from exc

    def _write(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        try:
            with self._transactions.transaction() as connection:
                return operation(connection)
        except (DuplicateInventoryError, InventoryConflictError, ServerNotFoundError):
            raise
        except PersistenceError as exc:
            raise _translate_repository_error(exc) from exc
        except sqlite3.Error as exc:
            raise InventoryRepositoryError(
                f"inventory write failed due to {type(exc).__name__}"
            ) from exc


class _InventoryQueries(BaseRepository):
    """Connection-scoped SQL owned only by the SQLite repository."""

    def assert_unique(self, server: Server) -> None:
        hostname = self.connection.execute(
            "SELECT 1 FROM inventory_servers WHERE hostname = ? COLLATE NOCASE",
            (server.hostname,),
        ).fetchone()
        if hostname is not None:
            raise DuplicateInventoryError("inventory hostname is already reserved")
        addresses = tuple(
            address
            for address in (server.primary_address, server.management_address)
            if address is not None
        )
        placeholders = ", ".join("?" for _ in addresses)
        duplicate = self.connection.execute(
            "SELECT 1 FROM inventory_server_addresses "
            f"WHERE address IN ({placeholders}) LIMIT 1",
            addresses,
        ).fetchone()
        if duplicate is not None:
            raise DuplicateInventoryError("inventory address is already reserved")

    def insert(self, server: Server) -> None:
        self.connection.execute(
            """
            INSERT INTO inventory_servers (
                uuid, hostname, display_name, platform, operating_system,
                distribution, distribution_version, kernel_version, architecture,
                server_type, environment, location, description, enabled, managed,
                discovery_state, health_status, status, last_poll_at,
                last_successful_poll_at, last_failure_at, failure_count,
                last_bootstrap_at, created_at, updated_at, deleted_at,
                synchronization_state, inventory_version, notes
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            _server_parameters(server),
        )
        self._replace_children(server)

    def replace(self, server: Server, *, existing_deleted: bool) -> Server:
        current = self.connection.execute(
            "SELECT inventory_version, deleted_at, created_at "
            "FROM inventory_servers "
            "WHERE uuid = ?",
            (str(server.uuid),),
        ).fetchone()
        if current is None:
            raise ServerNotFoundError("inventory server was not found")
        if (current[1] is not None) is not existing_deleted:
            raise InventoryConflictError("inventory soft-delete state changed")
        expected_version = server.inventory_version - 1
        if current[0] != expected_version:
            raise InventoryConflictError("inventory version is stale")
        if current[2] != server.created_at.isoformat():
            raise InventoryConflictError("inventory creation timestamp changed")
        cursor = self.connection.execute(
            """
            UPDATE inventory_servers SET
                hostname = ?, display_name = ?, platform = ?, operating_system = ?,
                distribution = ?, distribution_version = ?, kernel_version = ?,
                architecture = ?, server_type = ?, environment = ?, location = ?,
                description = ?, enabled = ?, managed = ?, discovery_state = ?,
                health_status = ?, status = ?, last_poll_at = ?,
                last_successful_poll_at = ?, last_failure_at = ?, failure_count = ?,
                last_bootstrap_at = ?, created_at = ?, updated_at = ?, deleted_at = ?,
                synchronization_state = ?, inventory_version = ?, notes = ?
            WHERE uuid = ? AND inventory_version = ?
            """,
            (*_server_parameters(server)[1:], str(server.uuid), expected_version),
        )
        if cursor.rowcount != 1:
            raise InventoryConflictError("inventory version changed during update")
        self._replace_children(server)
        return server

    def find_one(
        self,
        where: str,
        parameters: tuple[object, ...],
    ) -> Server | None:
        rows = self._select(where, parameters, limit=2, offset=0)
        if len(rows) > 1:
            raise InventoryRepositoryError("inventory uniqueness invariant failed")
        return rows[0] if rows else None

    def page(
        self,
        where: str,
        parameters: tuple[object, ...],
        *,
        limit: int,
        offset: int,
    ) -> RepositoryResult[Server]:
        total = self.connection.execute(
            f"SELECT COUNT(*) FROM inventory_servers AS s WHERE {where}",
            parameters,
        ).fetchone()[0]
        items = self._select(where, parameters, limit=limit, offset=offset)
        return RepositoryResult(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
        )

    def _select(
        self,
        where: str,
        parameters: tuple[object, ...],
        *,
        limit: int,
        offset: int,
    ) -> tuple[Server, ...]:
        rows = self.connection.execute(
            f"SELECT {_SERVER_COLUMNS} {_SERVER_FROM} "
            f"WHERE {where} ORDER BY s.hostname, s.uuid LIMIT ? OFFSET ?",
            (*parameters, limit, offset),
        ).fetchall()
        if not rows:
            return ()
        server_ids = tuple(row["uuid"] for row in rows)
        tags = self._load_tags(server_ids)
        labels = self._load_labels(server_ids)
        return tuple(
            _server_from_row(
                row,
                tags=frozenset(tags.get(row["uuid"], ())),
                labels=frozenset(labels.get(row["uuid"], ())),
            )
            for row in rows
        )

    def _replace_children(self, server: Server) -> None:
        server_id = str(server.uuid)
        self.connection.execute(
            "DELETE FROM inventory_server_addresses WHERE server_uuid = ?",
            (server_id,),
        )
        self.connection.execute(
            "INSERT INTO inventory_server_addresses (address, server_uuid, kind) "
            "VALUES (?, ?, 'primary')",
            (server.primary_address, server_id),
        )
        if server.management_address is not None:
            self.connection.execute(
                "INSERT INTO inventory_server_addresses "
                "(address, server_uuid, kind) VALUES (?, ?, 'management')",
                (server.management_address, server_id),
            )
        self.connection.execute(
            "DELETE FROM inventory_server_tags WHERE server_uuid = ?",
            (server_id,),
        )
        for tag in sorted(server.tags, key=lambda item: item.name):
            self.connection.execute(
                "INSERT INTO inventory_tags (name) VALUES (?) "
                "ON CONFLICT(name) DO NOTHING",
                (tag.name,),
            )
            tag_id = self.connection.execute(
                "SELECT id FROM inventory_tags WHERE name = ? COLLATE NOCASE",
                (tag.name,),
            ).fetchone()[0]
            self.connection.execute(
                "INSERT INTO inventory_server_tags (server_uuid, tag_id) VALUES (?, ?)",
                (server_id, tag_id),
            )
        self.connection.execute(
            "DELETE FROM inventory_tags WHERE NOT EXISTS ("
            "SELECT 1 FROM inventory_server_tags "
            "WHERE inventory_server_tags.tag_id = inventory_tags.id)",
        )
        self.connection.execute(
            "DELETE FROM inventory_server_labels WHERE server_uuid = ?",
            (server_id,),
        )
        self.connection.executemany(
            "INSERT INTO inventory_server_labels (server_uuid, key, value) "
            "VALUES (?, ?, ?)",
            (
                (server_id, label.key, label.value)
                for label in sorted(server.labels, key=lambda item: item.key)
            ),
        )

    def _load_tags(self, server_ids: Sequence[str]) -> dict[str, list[Tag]]:
        placeholders = ", ".join("?" for _ in server_ids)
        rows = self.connection.execute(
            "SELECT server_tag.server_uuid, tag.name "
            "FROM inventory_server_tags server_tag "
            "JOIN inventory_tags tag ON tag.id = server_tag.tag_id "
            f"WHERE server_tag.server_uuid IN ({placeholders})",
            tuple(server_ids),
        ).fetchall()
        result: dict[str, list[Tag]] = {}
        for row in rows:
            result.setdefault(row[0], []).append(Tag(row[1]))
        return result

    def _load_labels(self, server_ids: Sequence[str]) -> dict[str, list[Label]]:
        placeholders = ", ".join("?" for _ in server_ids)
        rows = self.connection.execute(
            "SELECT server_uuid, key, value FROM inventory_server_labels "
            f"WHERE server_uuid IN ({placeholders})",
            tuple(server_ids),
        ).fetchall()
        result: dict[str, list[Label]] = {}
        for row in rows:
            result.setdefault(row[0], []).append(Label(row[1], row[2]))
        return result


def _server_parameters(server: Server) -> tuple[object, ...]:
    return (
        str(server.uuid),
        server.hostname,
        server.display_name,
        server.platform.value,
        server.operating_system.value,
        server.distribution,
        server.distribution_version,
        server.kernel_version,
        server.architecture,
        server.server_type.value,
        server.environment,
        server.location,
        server.description,
        int(server.enabled),
        int(server.managed),
        server.discovery_state.value,
        server.health_status.value,
        server.status.value,
        _timestamp(server.last_poll_at),
        _timestamp(server.last_successful_poll_at),
        _timestamp(server.last_failure_at),
        server.failure_count,
        _timestamp(server.last_bootstrap_at),
        _timestamp(server.created_at),
        _timestamp(server.updated_at),
        _timestamp(server.deleted_at),
        server.synchronization_state.value,
        server.inventory_version,
        server.notes,
    )


def _server_from_row(
    row: sqlite3.Row,
    *,
    tags: frozenset[Tag],
    labels: frozenset[Label],
) -> Server:
    return Server(
        uuid=UUID(row["uuid"]),
        hostname=row["hostname"],
        display_name=row["display_name"],
        primary_address=row["primary_address"],
        management_address=row["management_address"],
        platform=Platform(row["platform"]),
        operating_system=OperatingSystem(row["operating_system"]),
        distribution=row["distribution"],
        distribution_version=row["distribution_version"],
        kernel_version=row["kernel_version"],
        architecture=row["architecture"],
        server_type=ServerType(row["server_type"]),
        environment=row["environment"],
        location=row["location"],
        description=row["description"],
        tags=tags,
        labels=labels,
        enabled=bool(row["enabled"]),
        managed=bool(row["managed"]),
        discovery_state=DiscoveryState(row["discovery_state"]),
        health_status=HealthStatus(row["health_status"]),
        status=ServerStatus(row["status"]),
        last_poll_at=_parse_timestamp(row["last_poll_at"]),
        last_successful_poll_at=_parse_timestamp(row["last_successful_poll_at"]),
        last_failure_at=_parse_timestamp(row["last_failure_at"]),
        failure_count=row["failure_count"],
        last_bootstrap_at=_parse_timestamp(row["last_bootstrap_at"]),
        created_at=_parse_required_timestamp(row["created_at"]),
        updated_at=_parse_required_timestamp(row["updated_at"]),
        deleted_at=_parse_timestamp(row["deleted_at"]),
        synchronization_state=SynchronizationState(row["synchronization_state"]),
        inventory_version=row["inventory_version"],
        notes=row["notes"],
    )


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _parse_required_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _validate_pagination(limit: int, offset: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise InventoryRepositoryError("limit must be an integer from 1 to 1000")
    if type(offset) is not int or offset < 0:
        raise InventoryRepositoryError("offset must be a non-negative integer")


def _translate_repository_error(error: BaseException) -> InventoryError:
    cause: BaseException | None = error
    while cause is not None:
        if isinstance(cause, sqlite3.IntegrityError):
            message = str(cause).lower()
            if "hostname" in message:
                return DuplicateInventoryError(
                    "inventory hostname is already reserved"
                )
            if "address" in message:
                return DuplicateInventoryError("inventory address is already reserved")
        cause = cause.__cause__
    return InventoryRepositoryError(
        f"inventory persistence failed due to {type(error).__name__}"
    )
