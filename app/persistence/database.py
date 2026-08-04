"""SQLite connection lifecycle and policy management for LIM."""

from __future__ import annotations

import errno
import os
import sqlite3
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from ..config import ConfigError, ConfigurationManager
from ..runtime import RuntimeManager, RuntimeManagerError
from .errors import (
    DatabaseConfigurationError,
    DatabaseConnectionError,
    DatabaseInitializationError,
)

_JOURNAL_MODES = frozenset({"DELETE", "WAL"})
_SYNCHRONOUS_MODES = frozenset({"NORMAL", "FULL", "EXTRA"})
_TRANSACTION_MODES = frozenset({"DEFERRED", "IMMEDIATE", "EXCLUSIVE"})


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """Validated SQLite policies loaded from configuration."""

    filename: str
    foreign_keys: bool
    busy_timeout_ms: int
    connection_timeout_seconds: float
    journal_mode: str
    synchronous_mode: str
    row_factory: str
    transaction_mode: str
    check_same_thread: bool
    backup_prefix: str


class DatabaseManager:
    """Create operation-scoped SQLite connections with uniform policies.

    Connections are never cached or shared by this manager. Every call to
    :meth:`connection` creates and closes one connection, making transaction and
    thread ownership explicit at the call site.
    """

    def __init__(
        self,
        config: ConfigurationManager,
        runtime: RuntimeManager,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self.settings = self._load_settings()
        try:
            self.database_path = runtime.data_path(self.settings.filename)
        except RuntimeManagerError as exc:
            raise DatabaseConfigurationError(
                "invalid database configuration: database.filename must be a "
                "single safe filename"
            ) from exc
        self._lock = threading.RLock()
        self._initialized = False

    @property
    def is_initialized(self) -> bool:
        """Return whether database initialization completed successfully."""
        return self._initialized

    def initialize(self) -> Path:
        """Securely create and configure the managed SQLite database."""
        with self._lock:
            self._initialized = False
            if not self._runtime.is_initialized:
                raise DatabaseInitializationError(
                    "runtime must be initialized before the database"
                )
            self._validate_managed_path()
            self._ensure_database_file()
            try:
                connection = self._open_connection(read_only=False)
                connection.close()
                self._restrict_database_files()
            except (OSError, sqlite3.Error) as exc:
                raise DatabaseInitializationError(
                    f"cannot initialize SQLite database: {type(exc).__name__}"
                ) from exc
            self._initialized = True
            return self.database_path

    @contextmanager
    def connection(self, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield a new configured connection and always close it afterward."""
        if not self._initialized:
            raise DatabaseConnectionError("database has not been initialized")
        self._validate_managed_path()
        try:
            connection = self._open_connection(read_only=read_only)
        except (OSError, sqlite3.Error) as exc:
            raise DatabaseConnectionError(
                f"cannot open SQLite connection: {type(exc).__name__}"
            ) from exc
        try:
            yield connection
        finally:
            connection.close()
            if not read_only:
                with suppress(OSError):
                    self._restrict_database_files()

    def _open_connection(self, *, read_only: bool) -> sqlite3.Connection:
        uri_path = quote(str(self.database_path), safe="/")
        mode = "ro" if read_only else "rw"
        target = f"file:{uri_path}?mode={mode}&nofollow=1"
        connection = sqlite3.connect(
            target,
            timeout=self.settings.connection_timeout_seconds,
            isolation_level=None,
            check_same_thread=self.settings.check_same_thread,
            uri=True,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(
                f"PRAGMA busy_timeout = {self.settings.busy_timeout_ms}"
            )
            connection.execute("PRAGMA foreign_keys = ON")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise sqlite3.OperationalError("foreign key enforcement unavailable")
            if not read_only:
                journal_mode = connection.execute(
                    f"PRAGMA journal_mode = {self.settings.journal_mode}"
                ).fetchone()[0]
                if str(journal_mode).upper() != self.settings.journal_mode:
                    raise sqlite3.OperationalError("requested journal mode unavailable")
                connection.execute(
                    f"PRAGMA synchronous = {self.settings.synchronous_mode}"
                )
                self._restrict_database_files()
            return connection
        except BaseException:
            connection.close()
            raise

    def _validate_managed_path(self) -> None:
        data_directory = self._runtime.paths.data
        if data_directory.is_symlink():
            raise DatabaseInitializationError(
                "configured SQLite data directory cannot be a symlink"
            )
        try:
            self.database_path.parent.resolve(strict=True).relative_to(
                data_directory.resolve(strict=True)
            )
        except (OSError, ValueError) as exc:
            raise DatabaseInitializationError(
                "database path is outside the managed SQLite data directory"
            ) from exc
        if self.database_path.parent.resolve() != data_directory.resolve():
            raise DatabaseInitializationError(
                "database path must be directly inside the SQLite data directory"
            )
        if self.database_path.is_symlink():
            raise DatabaseInitializationError("database path cannot be a symlink")

    def _ensure_database_file(self) -> None:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.database_path, flags, 0o600)
        except FileExistsError as exc:
            try:
                metadata = self.database_path.lstat()
            except OSError as exc:
                raise DatabaseInitializationError(
                    "cannot inspect existing database file"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise DatabaseInitializationError(
                    "database path must be a regular non-symlink file"
                ) from exc
            try:
                self.database_path.chmod(0o600)
            except OSError as exc:
                raise DatabaseInitializationError(
                    "cannot restrict database file permissions"
                ) from exc
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise DatabaseInitializationError(
                    "database path cannot be a symlink"
                ) from exc
            raise DatabaseInitializationError(
                f"cannot create database file: {type(exc).__name__}"
            ) from exc
        else:
            os.close(descriptor)

    def _restrict_database_files(self) -> None:
        for suffix in ("", "-wal", "-shm", "-journal"):
            path = Path(f"{self.database_path}{suffix}")
            if path.exists() and not path.is_symlink():
                path.chmod(0o600)

    def _load_settings(self) -> DatabaseSettings:
        try:
            filename = self._config.require("database.filename", str)
            foreign_keys = self._config.require("database.foreign_keys", bool)
            busy_timeout_ms = self._config.require("database.busy_timeout_ms", int)
            connection_timeout = self._config.require(
                "database.connection_timeout_seconds", (int, float)
            )
            journal_mode = self._config.require("database.journal_mode", str).upper()
            synchronous_mode = self._config.require(
                "database.synchronous_mode", str
            ).upper()
            row_factory = self._config.require("database.row_factory", str).lower()
            transaction_mode = self._config.require(
                "database.transaction_mode", str
            ).upper()
            check_same_thread = self._config.require(
                "database.check_same_thread", bool
            )
            backup_prefix = self._config.require("database.backup_prefix", str)
        except ConfigError as exc:
            raise DatabaseConfigurationError(
                f"invalid database configuration: {exc}"
            ) from exc

        if (
            not filename.strip()
            or not _is_safe_name(filename)
            or filename.startswith(".")
        ):
            raise DatabaseConfigurationError(
                "database.filename must be a visible safe filename"
            )
        if type(foreign_keys) is not bool or not foreign_keys:
            raise DatabaseConfigurationError("database.foreign_keys must be true")
        if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 3_600_000:
            raise DatabaseConfigurationError(
                "database.busy_timeout_ms must be an integer from 1 to 3600000"
            )
        if (
            type(connection_timeout) not in {int, float}
            or not 0 < connection_timeout <= 3600
        ):
            raise DatabaseConfigurationError(
                "database.connection_timeout_seconds must be greater than 0 "
                "and at most 3600"
            )
        if journal_mode not in _JOURNAL_MODES:
            raise DatabaseConfigurationError(
                "database.journal_mode must be DELETE or WAL"
            )
        if synchronous_mode not in _SYNCHRONOUS_MODES:
            raise DatabaseConfigurationError(
                "database.synchronous_mode must be NORMAL, FULL, or EXTRA"
            )
        if row_factory != "row":
            raise DatabaseConfigurationError("database.row_factory must be row")
        if transaction_mode not in _TRANSACTION_MODES:
            raise DatabaseConfigurationError(
                "database.transaction_mode must be DEFERRED, IMMEDIATE, or EXCLUSIVE"
            )
        if type(check_same_thread) is not bool:
            raise DatabaseConfigurationError(
                "database.check_same_thread must be bool"
            )
        if not backup_prefix or not _is_safe_name(backup_prefix):
            raise DatabaseConfigurationError(
                "database.backup_prefix must contain only letters, numbers, dots, "
                "dashes, or underscores"
            )
        return DatabaseSettings(
            filename=filename,
            foreign_keys=foreign_keys,
            busy_timeout_ms=busy_timeout_ms,
            connection_timeout_seconds=float(connection_timeout),
            journal_mode=journal_mode,
            synchronous_mode=synchronous_mode,
            row_factory=row_factory,
            transaction_mode=transaction_mode,
            check_same_thread=check_same_thread,
            backup_prefix=backup_prefix,
        )


def _is_safe_name(value: str) -> bool:
    return bool(value) and all(
        character.isalnum() or character in "._-" for character in value
    )
