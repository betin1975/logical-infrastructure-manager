"""Layered configuration loading for Logical Infrastructure Manager.

Configuration is assembled in this order (later sources win):

1. ``config/default.yml``
2. ``config/local.yml`` (optional and intentionally ignored by git)
3. Environment variables prefixed with ``LIM_``

Environment variable names use a double underscore to delimit nested keys. For
example, ``LIM_DATABASE__PORT=5433`` becomes ``database.port: 5433``. Values are
parsed as YAML scalars, so booleans, numbers, lists, and null remain typed.
"""

from __future__ import annotations

import copy
import os
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeVar

import yaml


class ConfigError(RuntimeError):
    """Raised when configuration cannot be loaded or validated."""


_MISSING = object()
_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_T = TypeVar("_T")


class ConfigurationManager:
    """Load, merge, and provide read-only access to LIM configuration.

    The manager is safe to share between threads. Returned mappings are deep
    copies so callers cannot accidentally mutate the active configuration.
    """

    def __init__(
        self,
        default_path: str | Path | None = None,
        local_path: str | Path | None = None,
        *,
        env_prefix: str = "LIM_",
        environ: Mapping[str, str] | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parent.parent
        self.default_path = Path(default_path or project_root / "config/default.yml")
        self.local_path = Path(local_path or project_root / "config/local.yml")
        self.env_prefix = env_prefix
        self._environ = environ
        self._lock = threading.RLock()
        self._config: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        """Reload all sources, replacing the active config only on success."""
        environment = dict(os.environ if self._environ is None else self._environ)
        config = self._load_yaml(self.default_path, required=True)

        if self.local_path.is_file():
            config = self._deep_merge(config, self._load_yaml(self.local_path))

        config = self._deep_merge(config, self._environment_config(environment))
        config = self._expand_environment(config, environment)
        self._validate(config)

        with self._lock:
            self._config = config

    def get(
        self,
        key: str,
        default: _T | object = _MISSING,
        *,
        expected_type: type[_T] | tuple[type[Any], ...] | None = None,
    ) -> Any | _T:
        """Return a value addressed by a dotted key such as ``logging.level``.

        A missing key raises :class:`ConfigError` unless ``default`` is given.
        ``expected_type`` provides an optional, explicit runtime type check.
        """
        if not key:
            raise ConfigError("configuration key cannot be empty")

        with self._lock:
            value: Any = self._config
            for segment in key.split("."):
                if not isinstance(value, Mapping) or segment not in value:
                    if default is not _MISSING:
                        return copy.deepcopy(default)
                    raise ConfigError(f"missing required configuration key: {key}")
                value = value[segment]
            value = copy.deepcopy(value)

        if expected_type is not None and not isinstance(value, expected_type):
            expected_name = self._type_name(expected_type)
            raise ConfigError(
                f"configuration key {key!r} must be {expected_name}; "
                f"got {type(value).__name__}"
            )
        return value

    def require(self, key: str, expected_type: type[_T] | None = None) -> Any | _T:
        """Return a required value, optionally checking its type."""
        return self.get(key, expected_type=expected_type)

    def as_dict(self) -> dict[str, Any]:
        """Return an isolated copy of the complete active configuration."""
        with self._lock:
            return copy.deepcopy(self._config)

    @staticmethod
    def _load_yaml(path: Path, *, required: bool = False) -> dict[str, Any]:
        if not path.is_file():
            if required:
                raise ConfigError(f"required configuration file not found: {path}")
            return {}

        try:
            with path.open(encoding="utf-8") as config_file:
                data = yaml.safe_load(config_file)
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigError(f"cannot load configuration file {path}: {exc}") from exc

        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ConfigError(f"configuration file must contain a mapping: {path}")
        return data

    @classmethod
    def _deep_merge(
        cls, base: Mapping[str, Any], override: Mapping[str, Any]
    ) -> dict[str, Any]:
        merged = copy.deepcopy(dict(base))
        for key, value in override.items():
            if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged

    def _environment_config(self, environment: Mapping[str, str]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if not self.env_prefix:
            return result

        for name in sorted(environment):
            if not name.startswith(self.env_prefix):
                continue
            suffix = name[len(self.env_prefix) :]
            if not suffix:
                continue
            segments = [segment.lower() for segment in suffix.split("__")]
            if any(not segment for segment in segments):
                raise ConfigError(f"invalid environment configuration key: {name}")

            cursor = result
            for segment in segments[:-1]:
                existing = cursor.setdefault(segment, {})
                if not isinstance(existing, dict):
                    raise ConfigError(
                        f"conflicting environment configuration key: {name}"
                    )
                cursor = existing
            try:
                cursor[segments[-1]] = yaml.safe_load(environment[name])
            except yaml.YAMLError:
                raise ConfigError(
                    f"invalid value for environment variable {name}"
                ) from None
        return result

    @classmethod
    def _expand_environment(cls, value: Any, environment: Mapping[str, str]) -> Any:
        if isinstance(value, dict):
            return {
                key: cls._expand_environment(item, environment)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._expand_environment(item, environment) for item in value]
        if not isinstance(value, str):
            return value

        def replace(match: re.Match[str]) -> str:
            name, fallback = match.groups()
            if name in environment:
                return environment[name]
            if fallback is not None:
                return fallback
            raise ConfigError(
                f"environment variable {name} is required by configuration"
            )

        return _ENV_REFERENCE.sub(replace, value)

    @staticmethod
    def _validate(config: Mapping[str, Any]) -> None:
        if not isinstance(config, Mapping):
            raise ConfigError("configuration root must be a mapping")

        for key in ("app", "paths", "logging", "database"):
            if key in config and not isinstance(config[key], Mapping):
                raise ConfigError(f"configuration section {key!r} must be a mapping")

        level = config.get("logging", {}).get("level")
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level is not None and (
            not isinstance(level, str) or level.upper() not in valid_levels
        ):
            raise ConfigError(
                "configuration key 'logging.level' must be one of "
                + ", ".join(sorted(valid_levels))
            )

    @staticmethod
    def _type_name(expected_type: type[Any] | tuple[type[Any], ...]) -> str:
        if isinstance(expected_type, tuple):
            return " or ".join(item.__name__ for item in expected_type)
        return expected_type.__name__


# Concise alias for call sites that prefer the conventional manager name.
ConfigManager = ConfigurationManager
