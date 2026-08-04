"""Centralized, structured, and secret-safe logging for LIM."""

from __future__ import annotations

import errno
import hashlib
import logging
import logging.handlers
import os
import re
import sys
import threading
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from .config import ConfigError, ConfigurationManager
from .runtime import RuntimeManager

REDACTED = "[REDACTED]"
CONTEXT_FIELDS = (
    "component",
    "server_id",
    "server_name",
    "job_id",
    "operation",
    "correlation_id",
)
_SENSITIVE_TERMS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "privatekey",
    "authorization",
    "credential",
)
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?"
    r"(?:-----END [^-\r\n]*PRIVATE KEY-----|$)",
    re.DOTALL,
)
_AUTHORIZATION_PATTERN = re.compile(
    r"(?im)(\bauthorization\b[\"']?\s*[:=]\s*)([^\r\n,;}]+)"
)
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)([\"']?(?:password|passwd|secret|token|api[_-]?key|apikey|"
    r"private[_-]?key|authorization|credential)[\"']?"
    r"(?:\s*[:=]\s*|\s+))"
    r"([\"'].*?[\"']|[^\s,;}\]]+)"
)
_COMPONENT_PATTERN = re.compile(
    r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$"
)
_TIMESTAMP_DIRECTIVES = frozenset("aAbBcdHIjmMpSUwWxXyYZfzGgVuV")


class LoggingManagerError(RuntimeError):
    """Raised when LIM logging cannot be configured safely."""


@dataclass(frozen=True, slots=True)
class LoggingSettings:
    """Validated logging settings loaded from :class:`ConfigManager`."""

    level: int
    level_name: str
    console_enabled: bool
    file_enabled: bool
    max_bytes: int
    backup_count: int
    log_format: str
    timestamp_format: str


class SecretRedactor:
    """Redact known and key-identified secrets from logging values."""

    def __init__(
        self,
        configuration: Mapping[str, Any],
        environment: Mapping[str, str],
    ) -> None:
        sensitive_values: set[str] = set()
        self._collect_sensitive_values(configuration, sensitive_values)
        for name, value in environment.items():
            if self.is_sensitive_key(name) and value:
                sensitive_values.add(value)
        sensitive_values.discard(REDACTED)
        self._sensitive_values = tuple(
            sorted(sensitive_values, key=lambda value: (-len(value), value))
        )
        digest = hashlib.sha256()
        for value in self._sensitive_values:
            digest.update(value.encode("utf-8", errors="replace"))
            digest.update(b"\x00")
        self.signature = digest.hexdigest()

    @staticmethod
    def is_sensitive_key(key: object) -> bool:
        """Return whether a field name identifies sensitive data."""
        normalized = str(key).lower().replace("-", "_")
        return any(term in normalized for term in _SENSITIVE_TERMS)

    def redact(self, value: Any) -> Any:
        """Return a recursively redacted copy suitable for logging."""
        return self._redact(value, seen=set())

    def redact_text(self, value: str) -> str:
        """Redact private keys, credentials, and known secret values in text."""
        redacted = _PRIVATE_KEY_PATTERN.sub(REDACTED, value)
        redacted = _AUTHORIZATION_PATTERN.sub(rf"\1{REDACTED}", redacted)
        redacted = _SENSITIVE_VALUE_PATTERN.sub(rf"\1{REDACTED}", redacted)
        for secret in self._sensitive_values:
            if len(secret) >= 4:
                redacted = redacted.replace(secret, REDACTED)
            else:
                redacted = re.sub(
                    rf"(?<!\w){re.escape(secret)}(?!\w)",
                    REDACTED,
                    redacted,
                )
        return redacted

    def _redact(self, value: Any, *, seen: set[int]) -> Any:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, bytes):
            return self.redact_text(value.decode("utf-8", errors="replace"))
        if isinstance(value, Mapping):
            if id(value) in seen:
                return "[CIRCULAR]"
            seen.add(id(value))
            try:
                return {
                    key: REDACTED
                    if self.is_sensitive_key(key)
                    else self._redact(item, seen=seen)
                    for key, item in value.items()
                }
            finally:
                seen.remove(id(value))
        if isinstance(value, tuple):
            if id(value) in seen:
                return ("[CIRCULAR]",)
            seen.add(id(value))
            try:
                return tuple(self._redact(item, seen=seen) for item in value)
            finally:
                seen.remove(id(value))
        if isinstance(value, list):
            if id(value) in seen:
                return ["[CIRCULAR]"]
            seen.add(id(value))
            try:
                return [self._redact(item, seen=seen) for item in value]
            finally:
                seen.remove(id(value))
        if isinstance(value, set):
            if id(value) in seen:
                return {"[CIRCULAR]"}
            seen.add(id(value))
            try:
                return {self._redact(item, seen=seen) for item in value}
            finally:
                seen.remove(id(value))
        return value

    def _collect_sensitive_values(
        self,
        value: Any,
        destination: set[str],
        *,
        sensitive: bool = False,
    ) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                self._collect_sensitive_values(
                    item,
                    destination,
                    sensitive=sensitive or self.is_sensitive_key(key),
                )
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                self._collect_sensitive_values(
                    item,
                    destination,
                    sensitive=sensitive,
                )
            return
        if sensitive and value is not None:
            rendered = str(value)
            if rendered:
                destination.add(rendered)


class RedactionFilter(logging.Filter):
    """Add standard context fields and redact each log record before output."""

    def __init__(self, redactor: SecretRedactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact message arguments and structured fields in place."""
        record.msg = self._redactor.redact(record.msg)
        record.args = self._redactor.redact(record.args)
        for field in CONTEXT_FIELDS:
            value = getattr(record, field, "-")
            setattr(record, field, self._redactor.redact(value))
        return True


class RedactingFormatter(logging.Formatter):
    """Format UTC log records and redact the final message and traceback."""

    def __init__(
        self,
        *,
        fmt: str,
        datefmt: str,
        redactor: SecretRedactor,
    ) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        """Return fully formatted output with no secret-bearing traceback text."""
        try:
            rendered = super().format(record)
            return self._redactor.redact_text(rendered)
        finally:
            record.exc_text = None

    def formatTime(
        self,
        record: logging.LogRecord,
        datefmt: str | None = None,
    ) -> str:
        """Format record timestamps explicitly in UTC."""
        timestamp = datetime.fromtimestamp(record.created, tz=UTC)
        if datefmt:
            return timestamp.strftime(datefmt)
        return timestamp.isoformat(timespec="milliseconds")


class SecureRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """A rotating file handler that enforces owner/group-readable log files."""

    def _open(self) -> TextIO:
        if not hasattr(os, "O_NOFOLLOW") and Path(self.baseFilename).is_symlink():
            raise OSError(errno.ELOOP, "refusing to follow a log file symlink")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.baseFilename, flags, 0o640)
        try:
            fchmod = getattr(os, "fchmod", None)
            if fchmod is not None:
                fchmod(descriptor, 0o640)
            else:
                os.chmod(self.baseFilename, 0o640)
            return open(
                descriptor,
                self.mode,
                encoding=self.encoding,
                errors=self.errors,
                closefd=True,
            )
        except Exception:
            with suppress(OSError):
                os.close(descriptor)
            raise


class ContextLogger(logging.LoggerAdapter):
    """A LIM logger with immutable structured context and secret redaction."""

    def __init__(
        self,
        logger: logging.Logger,
        context: Mapping[str, Any],
        redactor: SecretRedactor,
    ) -> None:
        super().__init__(logger, dict(context))
        self._redactor = redactor

    def bind(self, **context: Any) -> ContextLogger:
        """Return a new adapter with additional or replacement context."""
        _validate_context(context)
        return ContextLogger(
            self.logger,
            {**self.extra, **context},
            self._redactor,
        )

    def process(
        self,
        msg: object,
        kwargs: dict[str, Any],
    ) -> tuple[object, dict[str, Any]]:
        """Merge call context and redact messages before creating a record."""
        call_context = kwargs.get("extra", {})
        if not isinstance(call_context, Mapping):
            raise LoggingManagerError("logging extra context must be a mapping")
        _validate_context(call_context)
        kwargs["extra"] = self._redactor.redact(
            {**self.extra, **dict(call_context)}
        )
        return self._redactor.redact(msg), kwargs


def _validate_context(context: Mapping[str, Any]) -> None:
    caller_fields = set(CONTEXT_FIELDS) - {"component"}
    unknown = sorted(str(key) for key in context if key not in caller_fields)
    if unknown:
        raise LoggingManagerError(
            "unsupported logging context field(s): " + ", ".join(unknown)
        )


class LoggingManager:
    """Configure and provide LIM's centralized logging subsystem."""

    _configuration_lock = threading.RLock()

    def __init__(
        self,
        config: ConfigurationManager,
        runtime: RuntimeManager,
        *,
        namespace: str = "lim",
        console_stream: TextIO | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        if not namespace or not _COMPONENT_PATTERN.fullmatch(namespace):
            raise LoggingManagerError("logging namespace is invalid")
        self._config = config
        self._runtime = runtime
        self._namespace = namespace
        self._console_stream = console_stream
        self._environment = environment
        self._logger = logging.getLogger(namespace)
        self._handlers: tuple[logging.Handler, ...] = ()
        self._active_signature: tuple[LoggingSettings, Path, str] | None = None
        self._redactor: SecretRedactor | None = None

    @property
    def application_log_path(self) -> Path:
        """Return the configured rotating application log path."""
        return self._runtime.log_path("application.log")

    def initialize(self) -> LoggingSettings:
        """Configure logging atomically and return the active settings.

        New handlers and formatters are fully constructed before the active
        configuration changes. A failure therefore leaves the last valid setup
        installed and usable.
        """
        settings = self._load_settings()
        environment = dict(
            os.environ if self._environment is None else self._environment
        )
        redactor = SecretRedactor(self._config.as_dict(), environment)
        signature = (settings, self.application_log_path, redactor.signature)

        with self._configuration_lock:
            if (
                signature == self._active_signature
                and tuple(self._logger.handlers) == self._handlers
            ):
                return settings

        handlers = self._build_handlers(settings, redactor)

        with self._configuration_lock:
            if (
                signature == self._active_signature
                and tuple(self._logger.handlers) == self._handlers
            ):
                self._close_handlers(handlers)
                return settings

            old_handlers = tuple(self._logger.handlers)
            old_level = self._logger.level
            old_propagate = self._logger.propagate
            try:
                for handler in old_handlers:
                    self._logger.removeHandler(handler)
                for handler in handlers:
                    self._logger.addHandler(handler)
                self._logger.setLevel(settings.level)
                self._logger.propagate = False
                self._logger.disabled = False
            except Exception as exc:
                for handler in tuple(self._logger.handlers):
                    self._logger.removeHandler(handler)
                for handler in old_handlers:
                    self._logger.addHandler(handler)
                self._logger.setLevel(old_level)
                self._logger.propagate = old_propagate
                self._close_handlers(handlers)
                raise LoggingManagerError(
                    f"cannot activate LIM logging: {type(exc).__name__}"
                ) from exc

            self._handlers = handlers
            self._active_signature = signature
            self._redactor = redactor

        self._close_handlers(old_handlers)
        return settings

    def get_logger(self, component: str, **context: Any) -> ContextLogger:
        """Return a component logger with validated structured context."""
        if self._redactor is None or self._active_signature is None:
            raise LoggingManagerError("logging has not been initialized")
        if not component or not _COMPONENT_PATTERN.fullmatch(component):
            raise LoggingManagerError("logging component name is invalid")
        _validate_context(context)
        component_context = {"component": component, **context}
        component_logger = logging.getLogger(f"{self._namespace}.{component}")
        with self._configuration_lock:
            stale_handlers = tuple(component_logger.handlers)
            for handler in stale_handlers:
                component_logger.removeHandler(handler)
            component_logger.setLevel(logging.NOTSET)
            component_logger.propagate = True
            component_logger.disabled = False
        self._close_handlers(stale_handlers)
        return ContextLogger(
            component_logger,
            component_context,
            self._redactor,
        )

    def shutdown(self) -> None:
        """Remove and close handlers installed by this manager."""
        with self._configuration_lock:
            for handler in self._handlers:
                self._logger.removeHandler(handler)
            self._close_handlers(self._handlers)
            self._handlers = ()
            self._active_signature = None
            self._redactor = None

    def _load_settings(self) -> LoggingSettings:
        try:
            level_name = self._config.require("logging.level", str).upper()
            console_enabled = self._config.require(
                "logging.console_enabled", bool
            )
            file_enabled = self._config.require("logging.file_enabled", bool)
            max_bytes = self._config.require("logging.max_bytes", int)
            backup_count = self._config.require("logging.backup_count", int)
            log_format = self._config.require("logging.format", str)
            timestamp_format = self._config.require(
                "logging.timestamp_format", str
            )
        except ConfigError as exc:
            raise LoggingManagerError(f"invalid logging configuration: {exc}") from exc

        valid_levels = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        if level_name not in valid_levels:
            raise LoggingManagerError(
                "invalid logging configuration: logging.level must be one of "
                + ", ".join(valid_levels)
            )
        if type(console_enabled) is not bool:
            raise LoggingManagerError(
                "invalid logging configuration: logging.console_enabled must be bool"
            )
        if type(file_enabled) is not bool:
            raise LoggingManagerError(
                "invalid logging configuration: logging.file_enabled must be bool"
            )
        if type(max_bytes) is not int or max_bytes <= 0:
            raise LoggingManagerError(
                "invalid logging configuration: logging.max_bytes must be a "
                "positive integer"
            )
        if type(backup_count) is not int or backup_count <= 0:
            raise LoggingManagerError(
                "invalid logging configuration: logging.backup_count must be a "
                "positive integer"
            )
        if not log_format:
            raise LoggingManagerError(
                "invalid logging configuration: logging.format cannot be empty"
            )
        if not timestamp_format:
            raise LoggingManagerError(
                "invalid logging configuration: logging.timestamp_format "
                "cannot be empty"
            )
        self._validate_timestamp_format(timestamp_format)

        return LoggingSettings(
            level=valid_levels[level_name],
            level_name=level_name,
            console_enabled=console_enabled,
            file_enabled=file_enabled,
            max_bytes=max_bytes,
            backup_count=backup_count,
            log_format=log_format,
            timestamp_format=timestamp_format,
        )

    def _build_handlers(
        self,
        settings: LoggingSettings,
        redactor: SecretRedactor,
    ) -> tuple[logging.Handler, ...]:
        formatter = RedactingFormatter(
            fmt=settings.log_format,
            datefmt=settings.timestamp_format,
            redactor=redactor,
        )
        self._validate_formatter(formatter, redactor)
        handlers: list[logging.Handler] = []
        try:
            if settings.console_enabled:
                console = logging.StreamHandler(
                    self._console_stream
                    if self._console_stream is not None
                    else sys.stderr
                )
                self._configure_handler(console, formatter, redactor)
                handlers.append(console)
            if settings.file_enabled:
                file_handler = SecureRotatingFileHandler(
                    self.application_log_path,
                    maxBytes=settings.max_bytes,
                    backupCount=settings.backup_count,
                    encoding="utf-8",
                )
                self._configure_handler(file_handler, formatter, redactor)
                handlers.append(file_handler)
            if not handlers:
                handlers.append(logging.NullHandler())
        except OSError as exc:
            self._close_handlers(tuple(handlers))
            raise LoggingManagerError(
                f"cannot initialize file logging at {self.application_log_path}: "
                f"{type(exc).__name__}"
            ) from exc
        return tuple(handlers)

    @staticmethod
    def _validate_timestamp_format(timestamp_format: str) -> None:
        index = 0
        while index < len(timestamp_format):
            if timestamp_format[index] != "%":
                index += 1
                continue
            index += 1
            if index >= len(timestamp_format):
                raise LoggingManagerError(
                    "invalid logging configuration: logging.timestamp_format "
                    "contains an incomplete directive"
                )
            directive = timestamp_format[index]
            if directive != "%" and directive not in _TIMESTAMP_DIRECTIVES:
                raise LoggingManagerError(
                    "invalid logging configuration: logging.timestamp_format "
                    f"contains unsupported directive %{directive}"
                )
            index += 1

    @staticmethod
    def _configure_handler(
        handler: logging.Handler,
        formatter: logging.Formatter,
        redactor: SecretRedactor,
    ) -> None:
        handler.setLevel(logging.NOTSET)
        handler.setFormatter(formatter)
        handler.addFilter(RedactionFilter(redactor))

    @staticmethod
    def _validate_formatter(
        formatter: logging.Formatter,
        redactor: SecretRedactor,
    ) -> None:
        record = logging.LogRecord(
            name="lim.validation",
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg="logging configuration validation",
            args=(),
            exc_info=None,
        )
        RedactionFilter(redactor).filter(record)
        try:
            formatter.format(record)
        except (KeyError, TypeError, ValueError) as exc:
            raise LoggingManagerError(
                "invalid logging configuration: logging.format cannot be rendered"
            ) from exc

    @staticmethod
    def _close_handlers(handlers: tuple[logging.Handler, ...]) -> None:
        for handler in handlers:
            with suppress(Exception):
                handler.flush()
            with suppress(Exception):
                handler.close()
