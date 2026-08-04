"""Runtime directory lifecycle management for LIM."""

from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, ConfigurationManager


class RuntimeManagerError(RuntimeError):
    """Raised when LIM runtime paths cannot be initialized or validated."""


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """Resolved paths owned by :class:`RuntimeManager`."""

    root: Path
    data: Path
    jobs: Path
    logs: Path
    backups: Path


class RuntimeManager:
    """Create, validate, and expose LIM's configured runtime directories."""

    _PATH_KEYS = {
        "root": "paths.runtime",
        "data": "paths.data",
        "jobs": "paths.jobs",
        "logs": "paths.logs",
        "backups": "paths.backups",
    }

    def __init__(
        self,
        config: ConfigurationManager,
        *,
        application_root: str | Path,
    ) -> None:
        self._config = config
        self._application_root = Path(application_root).resolve()
        self._lock = threading.RLock()
        self._initialized = False
        self.paths = self._resolve_paths()

    @property
    def runtime_path(self) -> Path:
        """Return the configured runtime root."""
        return self.paths.root

    @property
    def is_initialized(self) -> bool:
        """Return whether the runtime tree passed its latest initialization."""
        return self._initialized

    def initialize(self) -> RuntimePaths:
        """Create and validate the complete runtime tree.

        Initialization is idempotent. Every call revalidates the paths and their
        writability so permission changes are detected promptly.
        """
        with self._lock:
            self._initialized = False
            for label, path in self._directories():
                self._ensure_directory(label, path)

            for label, path in self._directories():
                self._verify_writable(label, path)

            for path in self._leaf_directories():
                self._ensure_placeholder(path)

            self._initialized = True
            return self.paths

    def data_path(self, name: str | Path) -> Path:
        """Return a safe path below the SQLite data directory."""
        return self._child_path(self.paths.data, name, "data name")

    def job_path(self, job_id: str | Path) -> Path:
        """Return a safe path below the job workspace directory."""
        return self._child_path(self.paths.jobs, job_id, "job ID")

    def log_path(self, filename: str | Path) -> Path:
        """Return a safe path below the log directory."""
        return self._child_path(self.paths.logs, filename, "log filename")

    def backup_path(self, name: str | Path) -> Path:
        """Return a safe path below the backup directory."""
        return self._child_path(self.paths.backups, name, "backup name")

    def _resolve_paths(self) -> RuntimePaths:
        try:
            configured = {
                name: self._config.require(key, str)
                for name, key in self._PATH_KEYS.items()
            }
        except ConfigError as exc:
            raise RuntimeManagerError(f"invalid runtime configuration: {exc}") from exc

        resolved: dict[str, Path] = {}
        for name, value in configured.items():
            if not value.strip():
                key = self._PATH_KEYS[name]
                raise RuntimeManagerError(
                    f"invalid runtime configuration: {key} cannot be empty"
                )
            path = Path(value)
            if not path.is_absolute():
                path = self._application_root / path
            resolved[name] = path.resolve()

        runtime_root = resolved["root"]
        for name in ("data", "jobs", "logs", "backups"):
            try:
                resolved[name].relative_to(runtime_root)
            except ValueError as exc:
                key = self._PATH_KEYS[name]
                raise RuntimeManagerError(
                    f"invalid runtime configuration: {key} must be inside "
                    f"{self._PATH_KEYS['root']}"
                ) from exc

        return RuntimePaths(
            root=runtime_root,
            data=resolved["data"],
            jobs=resolved["jobs"],
            logs=resolved["logs"],
            backups=resolved["backups"],
        )

    def _directories(self) -> tuple[tuple[str, Path], ...]:
        return (
            ("runtime root", self.paths.root),
            ("SQLite data directory", self.paths.data),
            ("job directory", self.paths.jobs),
            ("log directory", self.paths.logs),
            ("backup directory", self.paths.backups),
        )

    def _leaf_directories(self) -> tuple[Path, ...]:
        return (
            self.paths.data,
            self.paths.jobs,
            self.paths.logs,
            self.paths.backups,
        )

    @staticmethod
    def _ensure_directory(label: str, path: Path) -> None:
        try:
            path.mkdir(mode=0o750, parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeManagerError(
                f"cannot create {label} at {path}: {exc}"
            ) from exc

        if not path.is_dir():
            raise RuntimeManagerError(f"{label} is not a directory: {path}")

    @staticmethod
    def _ensure_placeholder(directory: Path) -> None:
        placeholder = directory / ".gitkeep"
        try:
            placeholder.touch(mode=0o640, exist_ok=False)
        except FileExistsError:
            pass
        except OSError as exc:
            raise RuntimeManagerError(
                f"cannot create runtime placeholder {placeholder}: {exc}"
            ) from exc

        if not placeholder.is_file() or placeholder.is_symlink():
            raise RuntimeManagerError(
                f"runtime placeholder is not a regular file: {placeholder}"
            )

    @staticmethod
    def _verify_writable(label: str, path: Path) -> None:
        if not os.access(path, os.W_OK | os.X_OK):
            raise RuntimeManagerError(f"{label} is not writable: {path}")

        try:
            with tempfile.NamedTemporaryFile(prefix=".lim-write-", dir=path):
                pass
        except OSError as exc:
            raise RuntimeManagerError(f"{label} is not writable: {path}") from exc

    @staticmethod
    def _child_path(parent: Path, value: str | Path, label: str) -> Path:
        raw_value = os.fspath(value)
        candidate = Path(raw_value)
        if (
            not raw_value
            or "\x00" in raw_value
            or candidate.is_absolute()
            or candidate.name != raw_value
            or raw_value in {".", ".."}
            or "\\" in raw_value
        ):
            raise RuntimeManagerError(
                f"invalid {label}: expected a single relative path component"
            )
        return parent / candidate
