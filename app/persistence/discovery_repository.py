"""SQLite implementation of the discovery-owned repository interface."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import TypeVar
from uuid import UUID

from ..discovery import (
    DiscoveryAddress,
    DiscoveryConflictError,
    DiscoveryContainer,
    DiscoveryCPU,
    DiscoveryDisk,
    DiscoveryInterface,
    DiscoveryKernel,
    DiscoveryMemory,
    DiscoveryMetadata,
    DiscoveryNetwork,
    DiscoveryObservation,
    DiscoveryOperatingSystem,
    DiscoveryPackage,
    DiscoveryProcess,
    DiscoveryRepositoryError,
    DiscoveryResult,
    DiscoveryStatus,
    ObservationNotFoundError,
    ObservationSource,
    ObservationState,
    ObservedService,
    SynchronizationState,
)
from ..discovery.validation import normalize_timestamp, normalize_uuid
from .database import DatabaseManager
from .errors import PersistenceError
from .repository import BaseRepository
from .transactions import TransactionManager

_T = TypeVar("_T")
_COLUMNS = """o.uuid, o.server_uuid, o.source, o.state, o.status,
    o.synchronization_state, o.discovered_at, o.collection_duration_ms,
    o.collector_version, o.hostname, o.fqdn, o.os_name, o.os_distribution,
    o.os_version, o.kernel_name, o.kernel_version, o.architecture, o.cpu_model,
    o.cpu_logical_cores, o.cpu_physical_cores, o.memory_total_bytes,
    o.memory_available_bytes, o.network_domain, o.default_gateway, o.notes,
    o.failure_reason, o.created_at, o.updated_at, o.version"""


class SQLiteDiscoveryRepository:
    """Persist immutable observation history through explicit transactions."""

    def __init__(
        self, database: DatabaseManager, transactions: TransactionManager
    ) -> None:
        if not database.is_initialized:
            raise DiscoveryRepositoryError(
                "database must be initialized before the discovery repository"
            )
        if transactions.database is not database:
            raise DiscoveryRepositoryError(
                "discovery repository dependencies must share one database"
            )
        self._transactions = transactions

    def create(self, observation: DiscoveryObservation) -> DiscoveryObservation:
        """Insert an observation and all normalized facts atomically."""

        def operation(connection: sqlite3.Connection) -> DiscoveryObservation:
            queries = _DiscoveryQueries(connection)
            queries.insert(observation)
            return observation

        return self._write(operation)

    def update(self, observation: DiscoveryObservation) -> DiscoveryObservation:
        """Persist a lifecycle transition using optimistic versioning."""
        return self._write(
            lambda connection: _DiscoveryQueries(connection).update_state(observation)
        )

    def find_by_uuid(self, observation_uuid: UUID | str) -> DiscoveryObservation | None:
        """Find one observation by stable UUID."""
        identifier = str(normalize_uuid(observation_uuid, field="observation UUID"))
        return self._read(lambda queries: queries.find_one("o.uuid = ?", (identifier,)))

    def find_latest(
        self, server_uuid: UUID | str, *, source: ObservationSource | None = None
    ) -> DiscoveryObservation | None:
        """Find the newest observation for a server and optional source."""
        identifier = str(normalize_uuid(server_uuid, field="server UUID"))
        if source is not None and not isinstance(source, ObservationSource):
            raise DiscoveryRepositoryError("source must be ObservationSource")
        where = "o.server_uuid = ?"
        parameters: tuple[object, ...] = (identifier,)
        if source is not None:
            where += " AND o.source = ?"
            parameters += (source.value,)
        return self._read(lambda queries: queries.find_one(where, parameters))

    def history(
        self, server_uuid: UUID | str, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult:
        """Return newest-first history for a server."""
        return self.list_by_server(server_uuid, limit=limit, offset=offset)

    def list_by_server(
        self, server_uuid: UUID | str, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult:
        """Return observations for one inventory server."""
        identifier = str(normalize_uuid(server_uuid, field="server UUID"))
        return self._page("o.server_uuid = ?", (identifier,), limit, offset)

    def list_by_source(
        self, source: ObservationSource, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult:
        """Return observations from an exact source."""
        if not isinstance(source, ObservationSource):
            raise DiscoveryRepositoryError("source must be ObservationSource")
        return self._page("o.source = ?", (source.value,), limit, offset)

    def list_by_status(
        self, status: DiscoveryStatus, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult:
        """Return observations with an exact collector status."""
        if not isinstance(status, DiscoveryStatus):
            raise DiscoveryRepositoryError("status must be DiscoveryStatus")
        return self._page("o.status = ?", (status.value,), limit, offset)

    def list_by_state(
        self, state: ObservationState, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult:
        """Return observations in an exact lifecycle state."""
        if not isinstance(state, ObservationState):
            raise DiscoveryRepositoryError("state must be ObservationState")
        return self._page("o.state = ?", (state.value,), limit, offset)

    def search(
        self, query: str, *, limit: int = 100, offset: int = 0
    ) -> DiscoveryResult:
        """Search bounded non-secret observation identity and fact fields."""
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 256:
            raise DiscoveryRepositoryError(
                "search query must contain 1 to 256 characters"
            )
        escaped = (
            query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        where = """(o.hostname LIKE ? ESCAPE '\\' COLLATE NOCASE
            OR COALESCE(o.fqdn, '') LIKE ? ESCAPE '\\' COLLATE NOCASE
            OR EXISTS (SELECT 1 FROM discovery_addresses a
                WHERE a.observation_uuid = o.uuid AND a.address LIKE ? ESCAPE '\\')
            OR EXISTS (SELECT 1 FROM discovery_services s
                WHERE s.observation_uuid = o.uuid
                  AND s.name LIKE ? ESCAPE '\\' COLLATE NOCASE)
            OR EXISTS (SELECT 1 FROM discovery_packages p
                WHERE p.observation_uuid = o.uuid
                  AND p.name LIKE ? ESCAPE '\\' COLLATE NOCASE))"""
        return self._page(where, (pattern,) * 5, limit, offset)

    def count(
        self,
        *,
        server_uuid: UUID | str | None = None,
        source: ObservationSource | None = None,
        status: DiscoveryStatus | None = None,
        state: ObservationState | None = None,
    ) -> int:
        """Count observations matching optional exact filters."""
        clauses: list[str] = []
        values: list[object] = []
        if server_uuid is not None:
            clauses.append("server_uuid = ?")
            values.append(str(normalize_uuid(server_uuid, field="server UUID")))
        for field, value, enum_type in (
            ("source", source, ObservationSource),
            ("status", status, DiscoveryStatus),
            ("state", state, ObservationState),
        ):
            if value is not None:
                if not isinstance(value, enum_type):
                    raise DiscoveryRepositoryError(f"{field} has an invalid enum value")
                clauses.append(f"{field} = ?")
                values.append(value.value)
        where = " AND ".join(clauses) or "1 = 1"
        return self._read(
            lambda queries: queries.connection.execute(
                f"SELECT COUNT(*) FROM discovery_observations WHERE {where}",
                tuple(values),
            ).fetchone()[0]
        )

    def cleanup(self, *, before: datetime) -> int:
        """Delete expired history older than the explicit cutoff."""
        cutoff = normalize_timestamp(before, field="cleanup cutoff").isoformat()
        return self._write(
            lambda connection: (
                connection.execute(
                    "DELETE FROM discovery_observations "
                    "WHERE state = 'expired' AND updated_at < ?",
                    (cutoff,),
                ).rowcount
            )
        )

    def _page(
        self, where: str, parameters: tuple[object, ...], limit: int, offset: int
    ) -> DiscoveryResult:
        _validate_pagination(limit, offset)
        return self._read(
            lambda queries: queries.page(where, parameters, limit=limit, offset=offset)
        )

    def _read(self, operation: Callable[[_DiscoveryQueries], _T]) -> _T:
        try:
            with self._transactions.transaction() as connection:
                return operation(_DiscoveryQueries(connection))
        except (DiscoveryConflictError, ObservationNotFoundError):
            raise
        except (PersistenceError, sqlite3.Error, ValueError, TypeError) as exc:
            raise DiscoveryRepositoryError(
                f"discovery read failed due to {type(exc).__name__}"
            ) from exc

    def _write(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        try:
            with self._transactions.transaction() as connection:
                return operation(connection)
        except (DiscoveryConflictError, ObservationNotFoundError):
            raise
        except (PersistenceError, sqlite3.Error) as exc:
            raise DiscoveryRepositoryError(
                f"discovery write failed due to {type(exc).__name__}"
            ) from exc


class _DiscoveryQueries(BaseRepository):
    """Connection-scoped discovery SQL."""

    def insert(self, item: DiscoveryObservation) -> None:
        self.connection.execute(
            """INSERT INTO discovery_observations (
                uuid, server_uuid, source, state, status, synchronization_state,
                discovered_at, collection_duration_ms, collector_version, hostname,
                fqdn, os_name, os_distribution, os_version, kernel_name,
                kernel_version, architecture, cpu_model, cpu_logical_cores,
                cpu_physical_cores, memory_total_bytes, memory_available_bytes,
                network_domain, default_gateway, notes, failure_reason, created_at,
                updated_at, version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            _parameters(item),
        )
        self._insert_children(item)

    def update_state(self, item: DiscoveryObservation) -> DiscoveryObservation:
        current = self.find_one("o.uuid = ?", (str(item.uuid),))
        if current is None:
            raise ObservationNotFoundError("discovery observation was not found")
        expected = item.version - 1
        if current.version != expected or current.created_at != item.created_at:
            raise DiscoveryConflictError("discovery observation version is stale")
        if _fact_signature(current) != _fact_signature(item):
            raise DiscoveryConflictError("collected observation facts are immutable")
        if current.synchronization_state is not item.synchronization_state:
            raise DiscoveryConflictError(
                "only authoritative inventory may synchronize an observation"
            )
        cursor = self.connection.execute(
            """UPDATE discovery_observations SET state=?, status=?,
            synchronization_state=?, failure_reason=?, updated_at=?, version=?
            WHERE uuid=? AND version=?""",
            (
                item.state.value,
                item.status.value,
                item.synchronization_state.value,
                item.failure_reason,
                item.updated_at.isoformat(),
                item.version,
                str(item.uuid),
                expected,
            ),
        )
        if cursor.rowcount != 1:
            raise DiscoveryConflictError("discovery observation changed during update")
        return item

    def find_one(
        self, where: str, values: tuple[object, ...]
    ) -> DiscoveryObservation | None:
        rows = self._select(where, values, limit=1, offset=0)
        return rows[0] if rows else None

    def page(
        self, where: str, values: tuple[object, ...], *, limit: int, offset: int
    ) -> DiscoveryResult:
        total = self.connection.execute(
            f"SELECT COUNT(*) FROM discovery_observations o WHERE {where}", values
        ).fetchone()[0]
        return DiscoveryResult(
            self._select(where, values, limit=limit, offset=offset),
            total,
            limit,
            offset,
        )

    def _select(
        self, where: str, values: tuple[object, ...], *, limit: int, offset: int
    ) -> tuple[DiscoveryObservation, ...]:
        rows = self.connection.execute(
            f"SELECT {_COLUMNS} FROM discovery_observations o WHERE {where} "
            "ORDER BY o.discovered_at DESC, o.created_at DESC, o.uuid LIMIT ? OFFSET ?",
            (*values, limit, offset),
        ).fetchall()
        return tuple(self._hydrate(row) for row in rows)

    def _hydrate(self, row: sqlite3.Row) -> DiscoveryObservation:
        identifier = row["uuid"]

        def rows(table: str) -> list[sqlite3.Row]:
            return self.connection.execute(
                f"SELECT * FROM {table} WHERE observation_uuid = ? ORDER BY id",
                (identifier,),
            ).fetchall()

        metadata_rows = self.connection.execute(
            "SELECT namespace, key, value FROM discovery_metadata "
            "WHERE observation_uuid=? ORDER BY namespace,key",
            (identifier,),
        ).fetchall()
        metadata = {
            name: []
            for name in ("docker", "redis", "mysql", "freepbx", "prometheus", "raw")
        }
        for value in metadata_rows:
            metadata[value[0]].append((value[1], value[2]))
        return DiscoveryObservation(
            uuid=UUID(row["uuid"]),
            server_uuid=UUID(row["server_uuid"]),
            source=ObservationSource(row["source"]),
            state=ObservationState(row["state"]),
            status=DiscoveryStatus(row["status"]),
            synchronization_state=SynchronizationState(row["synchronization_state"]),
            discovered_at=datetime.fromisoformat(row["discovered_at"]),
            collection_duration_ms=row["collection_duration_ms"],
            collector_version=row["collector_version"],
            hostname=row["hostname"],
            fqdn=row["fqdn"],
            operating_system=DiscoveryOperatingSystem(
                row["os_name"], row["os_distribution"], row["os_version"]
            )
            if row["os_name"]
            else None,
            kernel=DiscoveryKernel(row["kernel_name"], row["kernel_version"])
            if row["kernel_name"]
            else None,
            architecture=row["architecture"],
            cpu=DiscoveryCPU(
                row["cpu_model"], row["cpu_logical_cores"], row["cpu_physical_cores"]
            )
            if row["cpu_logical_cores"] is not None
            else None,
            memory=DiscoveryMemory(
                row["memory_total_bytes"], row["memory_available_bytes"]
            )
            if row["memory_total_bytes"] is not None
            else None,
            network=DiscoveryNetwork(row["network_domain"], row["default_gateway"])
            if row["network_domain"] or row["default_gateway"]
            else None,
            interfaces=tuple(
                DiscoveryInterface(
                    r["name"],
                    r["mac_address"],
                    None if r["is_up"] is None else bool(r["is_up"]),
                    r["mtu"],
                )
                for r in rows("discovery_interfaces")
            ),
            addresses=tuple(
                DiscoveryAddress(r["address"], r["interface_name"], r["kind"])
                for r in rows("discovery_addresses")
            ),
            disks=tuple(
                DiscoveryDisk(
                    r["name"],
                    r["total_bytes"],
                    r["available_bytes"],
                    r["mount_point"],
                    r["filesystem"],
                )
                for r in rows("discovery_disks")
            ),
            services=tuple(
                ObservedService(r["name"], r["status"], r["version"], r["port"])
                for r in rows("discovery_services")
            ),
            packages=tuple(
                DiscoveryPackage(r["name"], r["version"], r["manager"])
                for r in rows("discovery_packages")
            ),
            containers=tuple(
                DiscoveryContainer(r["identifier"], r["name"], r["image"], r["status"])
                for r in rows("discovery_containers")
            ),
            processes=tuple(
                DiscoveryProcess(r["pid"], r["name"])
                for r in rows("discovery_processes")
            ),
            docker=DiscoveryMetadata(tuple(metadata["docker"])),
            redis=DiscoveryMetadata(tuple(metadata["redis"])),
            mysql=DiscoveryMetadata(tuple(metadata["mysql"])),
            freepbx=DiscoveryMetadata(tuple(metadata["freepbx"])),
            prometheus=DiscoveryMetadata(tuple(metadata["prometheus"])),
            raw_metadata=DiscoveryMetadata(tuple(metadata["raw"])),
            notes=row["notes"],
            failure_reason=row["failure_reason"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            version=row["version"],
        )

    def _insert_children(self, item: DiscoveryObservation) -> None:
        oid = str(item.uuid)
        self.connection.executemany(
            "INSERT INTO discovery_interfaces"
            "(observation_uuid,name,mac_address,is_up,mtu) VALUES(?,?,?,?,?)",
            (
                (
                    oid,
                    x.name,
                    x.mac_address,
                    None if x.is_up is None else int(x.is_up),
                    x.mtu,
                )
                for x in item.interfaces
            ),
        )
        self.connection.executemany(
            "INSERT INTO discovery_addresses"
            "(observation_uuid,address,interface_name,kind) VALUES(?,?,?,?)",
            ((oid, x.address, x.interface_name, x.kind) for x in item.addresses),
        )
        self.connection.executemany(
            "INSERT INTO discovery_disks"
            "(observation_uuid,name,total_bytes,available_bytes,"
            "mount_point,filesystem) "
            "VALUES(?,?,?,?,?,?)",
            (
                (
                    oid,
                    x.name,
                    x.total_bytes,
                    x.available_bytes,
                    x.mount_point,
                    x.filesystem,
                )
                for x in item.disks
            ),
        )
        self.connection.executemany(
            "INSERT INTO discovery_services"
            "(observation_uuid,name,status,version,port) VALUES(?,?,?,?,?)",
            ((oid, x.name, x.status, x.version, x.port) for x in item.services),
        )
        self.connection.executemany(
            "INSERT INTO discovery_packages"
            "(observation_uuid,name,version,manager) VALUES(?,?,?,?)",
            ((oid, x.name, x.version, x.manager) for x in item.packages),
        )
        self.connection.executemany(
            "INSERT INTO discovery_containers"
            "(observation_uuid,identifier,name,image,status) VALUES(?,?,?,?,?)",
            ((oid, x.identifier, x.name, x.image, x.status) for x in item.containers),
        )
        self.connection.executemany(
            "INSERT INTO discovery_processes(observation_uuid,pid,name) VALUES(?,?,?)",
            ((oid, x.pid, x.name) for x in item.processes),
        )
        metadata = (
            (oid, namespace, key, value)
            for namespace, group in (
                ("docker", item.docker),
                ("redis", item.redis),
                ("mysql", item.mysql),
                ("freepbx", item.freepbx),
                ("prometheus", item.prometheus),
                ("raw", item.raw_metadata),
            )
            for key, value in group.entries
        )
        self.connection.executemany(
            "INSERT INTO discovery_metadata"
            "(observation_uuid,namespace,key,value) VALUES(?,?,?,?)",
            metadata,
        )


def _parameters(item: DiscoveryObservation) -> tuple[object, ...]:
    os = item.operating_system
    kernel = item.kernel
    cpu = item.cpu
    memory = item.memory
    network = item.network
    return (
        str(item.uuid),
        str(item.server_uuid),
        item.source.value,
        item.state.value,
        item.status.value,
        item.synchronization_state.value,
        item.discovered_at.isoformat(),
        item.collection_duration_ms,
        item.collector_version,
        item.hostname,
        item.fqdn,
        os.name if os else None,
        os.distribution if os else None,
        os.version if os else None,
        kernel.name if kernel else None,
        kernel.version if kernel else None,
        item.architecture,
        cpu.model if cpu else None,
        cpu.logical_cores if cpu else None,
        cpu.physical_cores if cpu else None,
        memory.total_bytes if memory else None,
        memory.available_bytes if memory else None,
        network.domain if network else None,
        network.default_gateway if network else None,
        item.notes,
        item.failure_reason,
        item.created_at.isoformat(),
        item.updated_at.isoformat(),
        item.version,
    )


def _validate_pagination(limit: int, offset: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise DiscoveryRepositoryError("limit must be an integer from 1 to 1000")
    if type(offset) is not int or offset < 0:
        raise DiscoveryRepositoryError("offset must be a non-negative integer")


def _fact_signature(item: DiscoveryObservation) -> tuple[object, ...]:
    """Return collected fields that lifecycle updates may never rewrite."""
    return (
        item.uuid,
        item.server_uuid,
        item.source,
        item.discovered_at,
        item.collection_duration_ms,
        item.collector_version,
        item.hostname,
        item.fqdn,
        item.operating_system,
        item.kernel,
        item.architecture,
        item.cpu,
        item.memory,
        item.disks,
        item.interfaces,
        item.addresses,
        item.services,
        item.packages,
        item.containers,
        item.processes,
        item.network,
        item.docker,
        item.redis,
        item.mysql,
        item.freepbx,
        item.prometheus,
        item.raw_metadata,
        item.notes,
    )
