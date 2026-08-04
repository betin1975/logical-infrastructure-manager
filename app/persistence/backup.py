"""Consistent SQLite backup creation and non-destructive restore validation."""

from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from ..runtime import RuntimeManager, RuntimeManagerError
from .database import DatabaseManager, _is_safe_name
from .errors import BackupError, MigrationError, RestoreValidationError
from .migrations import inspect_migration_connection


@dataclass(frozen=True, slots=True)
class BackupResult:
    """Metadata for one successfully published SQLite backup."""

    path: Path
    size_bytes: int
    created_at: datetime
    schema_version: int


@dataclass(frozen=True, slots=True)
class RestoreValidationResult:
    """Read-only integrity and schema result for a candidate backup."""

    path: Path
    size_bytes: int
    checked_at: datetime
    schema_version: int


class BackupManager:
    """Create atomic SQLite backups and validate candidates without restoring."""

    def __init__(
        self,
        database: DatabaseManager,
        runtime: RuntimeManager,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._runtime = runtime
        self._clock = clock or (lambda: datetime.now(UTC))

    def create_backup(self, name: str | None = None) -> BackupResult:
        """Publish one consistent backup using SQLite's online backup API."""
        if not self._database.is_initialized:
            raise BackupError("database must be initialized before backup")
        created_at = self._clock().astimezone(UTC)
        filename = name or (
            f"{self._database.settings.backup_prefix}-"
            f"{created_at:%Y%m%dT%H%M%S%fZ}.sqlite3"
        )
        destination = self._backup_path(filename, BackupError)
        self._validate_backup_directory(BackupError)
        if destination.exists() or destination.is_symlink():
            raise BackupError("backup destination already exists")

        descriptor = -1
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".lim-backup-",
                suffix=".tmp",
                dir=self._runtime.paths.backups,
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            os.close(descriptor)
            descriptor = -1
            self._copy_database(temporary_path)
            temporary_path.chmod(0o600)
            validation = self.validate_restore(temporary_path, allow_temporary=True)
            if destination.exists() or destination.is_symlink():
                raise BackupError("backup destination changed during backup")
            os.replace(temporary_path, destination)
            temporary_path = None
            destination.chmod(0o600)
            return BackupResult(
                path=destination,
                size_bytes=destination.stat().st_size,
                created_at=created_at,
                schema_version=validation.schema_version,
            )
        except BackupError:
            raise
        except (OSError, sqlite3.Error, RestoreValidationError) as exc:
            raise BackupError(
                f"database backup failed due to {type(exc).__name__}"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)

    def validate_restore(
        self,
        candidate: str | Path,
        *,
        allow_temporary: bool = False,
    ) -> RestoreValidationResult:
        """Validate a candidate read-only without replacing the active database."""
        checked_at = self._clock().astimezone(UTC)
        path = self._candidate_path(candidate, allow_temporary=allow_temporary)
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise RestoreValidationError("candidate backup does not exist") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise RestoreValidationError(
                "candidate backup must be a regular non-symlink file"
            )
        uri_path = quote(str(path), safe="/")
        try:
            connection = sqlite3.connect(
                f"file:{uri_path}?mode=ro&nofollow=1",
                uri=True,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
                if integrity is None or integrity[0] != "ok":
                    raise RestoreValidationError(
                        "candidate backup failed SQLite integrity validation"
                    )
                state = inspect_migration_connection(connection)
                if not state.metadata_exists:
                    raise RestoreValidationError(
                        "candidate backup lacks LIM migration metadata"
                    )
            finally:
                connection.close()
        except RestoreValidationError:
            raise
        except (sqlite3.Error, MigrationError) as exc:
            raise RestoreValidationError(
                f"candidate backup is invalid: {type(exc).__name__}"
            ) from exc
        return RestoreValidationResult(
            path=path,
            size_bytes=metadata.st_size,
            checked_at=checked_at,
            schema_version=state.schema_version,
        )

    def _copy_database(self, temporary_path: Path) -> None:
        with self._database.connection(read_only=True) as source:
            destination = sqlite3.connect(
                temporary_path,
                isolation_level=None,
                check_same_thread=True,
            )
            try:
                source.backup(destination)
            finally:
                destination.close()

    def _candidate_path(self, candidate: str | Path, *, allow_temporary: bool) -> Path:
        raw_path = Path(candidate)
        if raw_path.is_absolute():
            path = raw_path
        else:
            path = self._backup_path(str(candidate), RestoreValidationError)
        backup_directory = self._runtime.paths.backups
        if path.parent != backup_directory:
            raise RestoreValidationError(
                "candidate backup must be directly inside the managed backup directory"
            )
        if not allow_temporary and path.name.startswith(".lim-backup-"):
            raise RestoreValidationError(
                "temporary backup files are not restore candidates"
            )
        self._validate_backup_directory(RestoreValidationError)
        return path

    def _backup_path(
        self,
        name: str,
        error_type: type[BackupError] | type[RestoreValidationError],
    ) -> Path:
        if not _is_safe_name(name) or name in {".", ".."} or name.startswith("."):
            raise error_type(
                "backup name must contain only letters, numbers, dots, dashes, "
                "or underscores"
            )
        try:
            return self._runtime.backup_path(name)
        except RuntimeManagerError as exc:
            raise error_type("backup name is not a safe filename") from exc

    def _validate_backup_directory(
        self,
        error_type: type[BackupError] | type[RestoreValidationError],
    ) -> None:
        directory = self._runtime.paths.backups
        if not self._runtime.is_initialized or not directory.is_dir():
            raise error_type("managed backup directory is not initialized")
        if directory.is_symlink():
            raise error_type("managed backup directory cannot be a symlink")
