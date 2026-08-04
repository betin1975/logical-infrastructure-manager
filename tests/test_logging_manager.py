import io
import logging
import logging.handlers
import re
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

import app.logging_manager as logging_module
from app.config import ConfigError, ConfigurationManager
from app.logging_manager import (
    REDACTED,
    ContextLogger,
    LoggingManager,
    LoggingManagerError,
    SecretRedactor,
)
from app.runtime import RuntimeManager
from tests.helpers import write_yaml

DEFAULT_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s component=%(component)s "
    "correlation_id=%(correlation_id)s server_id=%(server_id)s "
    "server_name=%(server_name)s job_id=%(job_id)s "
    "operation=%(operation)s %(message)s"
)


class LoggingFixture:
    """Isolated real configuration, runtime, and logging manager for a test."""

    def __init__(
        self,
        *,
        config: ConfigurationManager,
        runtime: RuntimeManager,
        manager: LoggingManager,
        environment: dict[str, str],
        namespace: str,
        console: io.StringIO,
    ) -> None:
        self.config = config
        self.runtime = runtime
        self.manager = manager
        self.environment = environment
        self.namespace = namespace
        self.console = console

    @property
    def namespace_logger(self) -> logging.Logger:
        return logging.getLogger(self.namespace)

    def flush(self) -> None:
        for handler in self.namespace_logger.handlers:
            handler.flush()


LoggingFactory = Callable[..., LoggingFixture]


@pytest.fixture
def logging_factory(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> LoggingFactory:
    managers: list[LoggingManager] = []
    counter = 0

    def create(
        *,
        logging_overrides: dict[str, object] | None = None,
        extra_config: dict[str, object] | None = None,
        environment: dict[str, str] | None = None,
    ) -> LoggingFixture:
        nonlocal counter
        counter += 1
        logging_config: dict[str, object] = {
            "level": "INFO",
            "console_enabled": True,
            "file_enabled": True,
            "max_bytes": 1024 * 1024,
            "backup_count": 3,
            "format": DEFAULT_LOG_FORMAT,
            "timestamp_format": "%Y-%m-%dT%H:%M:%S%z",
        }
        if logging_overrides:
            logging_config.update(logging_overrides)
        config_data: dict[str, object] = {
            "paths": {
                "runtime": "runtime",
                "data": "runtime/data",
                "jobs": "runtime/jobs",
                "logs": "runtime/logs",
                "backups": "runtime/backups",
            },
            "logging": logging_config,
        }
        if extra_config:
            config_data.update(extra_config)
        default = tmp_path / f"config/default-{counter}.yml"
        write_yaml(default, config_data)
        current_environment = dict(environment or {})
        config = ConfigurationManager(
            default,
            tmp_path / f"config/local-{counter}.yml",
            environ=current_environment,
        )
        runtime = RuntimeManager(config, application_root=tmp_path)
        runtime.initialize()
        console = io.StringIO()
        test_name = re.sub(r"[^A-Za-z0-9_.-]", "_", request.node.name)
        namespace = f"lim_test.{test_name}.{counter}"
        manager = LoggingManager(
            config,
            runtime,
            namespace=namespace,
            console_stream=console,
            environment=current_environment,
        )
        managers.append(manager)
        return LoggingFixture(
            config=config,
            runtime=runtime,
            manager=manager,
            environment=current_environment,
            namespace=namespace,
            console=console,
        )

    yield create

    for manager in reversed(managers):
        manager.shutdown()


def test_initialize_configures_console_and_rotating_file_handlers(
    logging_factory: LoggingFactory,
) -> None:
    fixture = logging_factory()

    settings = fixture.manager.initialize()

    handlers = fixture.namespace_logger.handlers
    assert settings.level_name == "INFO"
    assert any(type(handler) is logging.StreamHandler for handler in handlers)
    file_handler = next(
        handler
        for handler in handlers
        if isinstance(handler, logging.handlers.RotatingFileHandler)
    )
    assert file_handler.maxBytes == 1024 * 1024
    assert file_handler.backupCount == 3
    assert fixture.manager.application_log_path.is_file()
    assert stat.S_IMODE(fixture.manager.application_log_path.stat().st_mode) == 0o640
    assert fixture.namespace_logger.propagate is False


def test_repeated_initialize_does_not_duplicate_handlers(
    logging_factory: LoggingFactory,
) -> None:
    fixture = logging_factory()
    fixture.manager.initialize()
    first_handlers = tuple(fixture.namespace_logger.handlers)

    fixture.manager.initialize()

    assert tuple(fixture.namespace_logger.handlers) == first_handlers
    assert len(first_handlers) == 2


def test_console_logging_uses_structured_component_context(
    logging_factory: LoggingFactory,
) -> None:
    fixture = logging_factory(logging_overrides={"file_enabled": False})
    fixture.manager.initialize()

    logger = fixture.manager.get_logger(
        "bootstrap",
        operation="startup",
        correlation_id="correlation-123",
    )
    logger.info("foundation ready")
    fixture.flush()

    output = fixture.console.getvalue()
    assert isinstance(logger, ContextLogger)
    assert "foundation ready" in output
    assert "component=bootstrap" in output
    assert "operation=startup" in output
    assert "correlation_id=correlation-123" in output
    assert "+0000" in output


def test_file_logging_uses_one_structured_application_log(
    logging_factory: LoggingFactory,
) -> None:
    fixture = logging_factory(logging_overrides={"console_enabled": False})
    fixture.manager.initialize()

    for component in ("bootstrap", "ssh", "jobs"):
        fixture.manager.get_logger(component).info("component event")
    fixture.flush()

    output = fixture.manager.application_log_path.read_text(encoding="utf-8")
    assert "component=bootstrap" in output
    assert "component=ssh" in output
    assert "component=jobs" in output
    assert not fixture.runtime.log_path("ssh.log").exists()
    assert not fixture.runtime.log_path("jobs.log").exists()
    assert not fixture.runtime.log_path("bootstrap.log").exists()


def test_rotating_file_configuration_creates_bounded_backups(
    logging_factory: LoggingFactory,
) -> None:
    fixture = logging_factory(
        logging_overrides={
            "console_enabled": False,
            "max_bytes": 240,
            "backup_count": 2,
        }
    )
    fixture.manager.initialize()
    logger = fixture.manager.get_logger("bootstrap")

    for index in range(30):
        logger.info("rotation event %s %s", index, "x" * 80)
    fixture.flush()

    log_path = fixture.manager.application_log_path
    assert log_path.is_file()
    assert log_path.with_name("application.log.1").is_file()
    rotated_logs = list(log_path.parent.glob("application.log*"))
    assert len(rotated_logs) <= 3
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o640 for path in rotated_logs)


def test_bound_logger_adds_server_job_and_correlation_context(
    logging_factory: LoggingFactory,
) -> None:
    fixture = logging_factory(logging_overrides={"file_enabled": False})
    fixture.manager.initialize()
    base_logger = fixture.manager.get_logger(
        "jobs",
        server_id="server-7",
        server_name="example-server",
    )

    logger = base_logger.bind(
        job_id="job-9",
        operation="inspect",
        correlation_id="correlation-42",
    )
    logger.warning("context event", extra={"job_id": "call-job-10"})
    fixture.flush()

    output = fixture.console.getvalue()
    assert "server_id=server-7" in output
    assert "server_name=example-server" in output
    assert "job_id=call-job-10" in output
    assert "operation=inspect" in output
    assert "correlation_id=correlation-42" in output


def test_nested_messages_context_configuration_and_environment_are_redacted(
    logging_factory: LoggingFactory,
) -> None:
    fixture = logging_factory(
        logging_overrides={"file_enabled": False},
        extra_config={
            "integration": {
                "database_password": "config-password-123",
                "nested": {"private_key": "config-private-key-456"},
            }
        },
        environment={"SERVICE_AUTH_TOKEN": "environment-token-789"},
    )
    fixture.manager.initialize()
    logger = fixture.manager.get_logger(
        "bootstrap",
        server_name="token=context-token-012",
    )
    private_key = (
        "-----BEGIN PRIVATE KEY-----\n"
        "private-key-material\n"
        "-----END PRIVATE KEY-----"
    )

    logger.info(
        {
            "public": "visible",
            "password": "mapping-password-345",
            "nested": {
                "api_key": "mapping-api-key-678",
                "message": "uses config-password-123",
            },
        }
    )
    logger.warning("Authorization: Bearer authorization-secret-901")
    logger.error("private material: %s", private_key)
    logger.info("environment value %s", "environment-token-789")
    logger.info("configured key %s", "config-private-key-456")
    fixture.flush()

    output = fixture.console.getvalue()
    for secret in (
        "context-token-012",
        "mapping-password-345",
        "mapping-api-key-678",
        "config-password-123",
        "authorization-secret-901",
        "private-key-material",
        "environment-token-789",
        "config-private-key-456",
    ):
        assert secret not in output
    assert "visible" in output
    assert REDACTED in output


def test_redactor_handles_bytes_short_secrets_and_circular_values() -> None:
    redactor = SecretRedactor(
        {
            "password": ["pw", None],
            "nested": {"api_key": "list-secret"},
        },
        {"SERVICE_TOKEN": "xy"},
    )
    circular_mapping: dict[str, object] = {}
    circular_mapping["self"] = circular_mapping
    circular_list: list[object] = []
    circular_list.append(circular_list)

    assert redactor.redact(b"password=byte-secret") == f"password={REDACTED}"
    assert redactor.redact_text("pw xy") == f"{REDACTED} {REDACTED}"
    assert redactor.redact(circular_mapping) == {"self": "[CIRCULAR]"}
    assert redactor.redact(circular_list) == [["[CIRCULAR]"]]
    assert redactor.redact({"values": {b"token=set-secret"}}) == {
        "values": {f"token={REDACTED}"}
    }


def test_exception_logging_preserves_traceback_and_redacts_secrets(
    logging_factory: LoggingFactory,
) -> None:
    fixture = logging_factory(
        logging_overrides={"console_enabled": False},
        environment={"SERVICE_TOKEN": "known-exception-secret"},
    )
    fixture.manager.initialize()
    logger = fixture.manager.get_logger(
        "bootstrap",
        operation="token=context-exception-secret",
    )

    try:
        raise RuntimeError("password=exception-password-secret")
    except RuntimeError:
        logger.exception("failed with token=message-token-secret")
    logger.error("known value known-exception-secret")
    fixture.flush()

    output = fixture.manager.application_log_path.read_text(encoding="utf-8")
    assert "Traceback (most recent call last)" in output
    assert "RuntimeError" in output
    assert REDACTED in output
    for secret in (
        "context-exception-secret",
        "exception-password-secret",
        "message-token-secret",
        "known-exception-secret",
    ):
        assert secret not in output


def test_disabling_console_and_file_logging_uses_a_null_handler(
    logging_factory: LoggingFactory,
) -> None:
    fixture = logging_factory(
        logging_overrides={
            "console_enabled": False,
            "file_enabled": False,
        }
    )

    fixture.manager.initialize()
    fixture.manager.get_logger("bootstrap").error("discarded event")

    assert len(fixture.namespace_logger.handlers) == 1
    assert isinstance(fixture.namespace_logger.handlers[0], logging.NullHandler)
    assert fixture.console.getvalue() == ""
    assert not fixture.manager.application_log_path.exists()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"console_enabled": "yes"}, "console_enabled.*must be bool"),
        ({"file_enabled": 1}, "file_enabled.*must be bool"),
        ({"max_bytes": 0}, "max_bytes must be a positive integer"),
        ({"max_bytes": True}, "max_bytes must be a positive integer"),
        ({"backup_count": 0}, "backup_count must be a positive integer"),
        ({"backup_count": -1}, "backup_count must be a positive integer"),
        ({"backup_count": True}, "backup_count must be a positive integer"),
        ({"format": ""}, "logging.format cannot be empty"),
        ({"format": "%(missing)s"}, "logging.format cannot be rendered"),
        ({"timestamp_format": ""}, "timestamp_format cannot be empty"),
        ({"timestamp_format": "%Q"}, "unsupported directive %Q"),
        ({"timestamp_format": "%"}, "incomplete directive"),
    ],
)
def test_invalid_logging_settings_are_rejected(
    logging_factory: LoggingFactory,
    overrides: dict[str, object],
    message: str,
) -> None:
    fixture = logging_factory(logging_overrides=overrides)

    with pytest.raises(LoggingManagerError, match=message):
        fixture.manager.initialize()


def test_invalid_log_level_is_rejected_with_an_actionable_error(
    tmp_path: Path,
) -> None:
    default = tmp_path / "config/default.yml"
    write_yaml(default, {"logging": {"level": "VERBOSE"}})

    with pytest.raises(ConfigError, match="logging.level.*must be one of"):
        ConfigurationManager(default, tmp_path / "config/local.yml", environ={})


def test_unwritable_log_file_preserves_actionable_error(
    logging_factory: LoggingFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = logging_factory(logging_overrides={"console_enabled": False})

    def deny_file_handler(*args: object, **kwargs: object) -> None:
        raise PermissionError("synthetic permission denial")

    monkeypatch.setattr(
        logging_module,
        "SecureRotatingFileHandler",
        deny_file_handler,
    )

    with pytest.raises(
        LoggingManagerError,
        match="cannot initialize file logging.*PermissionError",
    ):
        fixture.manager.initialize()


def test_file_logging_refuses_to_follow_an_existing_symlink(
    logging_factory: LoggingFactory,
    tmp_path: Path,
) -> None:
    fixture = logging_factory(logging_overrides={"console_enabled": False})
    target = tmp_path / "outside-runtime.log"
    target.write_text("preserve", encoding="utf-8")
    fixture.manager.application_log_path.symlink_to(target)

    with pytest.raises(LoggingManagerError, match="cannot initialize file logging"):
        fixture.manager.initialize()

    assert target.read_text(encoding="utf-8") == "preserve"


def test_failed_reconfiguration_preserves_previous_valid_handlers(
    logging_factory: LoggingFactory,
) -> None:
    fixture = logging_factory(logging_overrides={"file_enabled": False})
    fixture.manager.initialize()
    original_handlers = tuple(fixture.namespace_logger.handlers)
    logger = fixture.manager.get_logger("bootstrap")
    fixture.environment["LIM_LOGGING__MAX_BYTES"] = "0"
    fixture.config.reload()

    with pytest.raises(LoggingManagerError, match="max_bytes"):
        fixture.manager.initialize()

    assert tuple(fixture.namespace_logger.handlers) == original_handlers
    logger.info("previous configuration remains active")
    fixture.flush()
    assert "previous configuration remains active" in fixture.console.getvalue()


def test_valid_reconfiguration_replaces_handlers_without_duplicates(
    logging_factory: LoggingFactory,
) -> None:
    fixture = logging_factory(logging_overrides={"file_enabled": False})
    fixture.manager.initialize()
    original_handlers = tuple(fixture.namespace_logger.handlers)
    fixture.environment["LIM_LOGGING__LEVEL"] = "DEBUG"
    fixture.config.reload()

    settings = fixture.manager.initialize()

    assert settings.level_name == "DEBUG"
    assert len(fixture.namespace_logger.handlers) == 1
    assert tuple(fixture.namespace_logger.handlers) != original_handlers
    fixture.manager.get_logger("bootstrap").debug("debug configuration active")
    fixture.flush()
    assert "debug configuration active" in fixture.console.getvalue()


def test_failed_handler_reconfiguration_preserves_previous_valid_handlers(
    logging_factory: LoggingFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = logging_factory(logging_overrides={"file_enabled": False})
    fixture.manager.initialize()
    original_handlers = tuple(fixture.namespace_logger.handlers)
    fixture.environment["LIM_LOGGING__FILE_ENABLED"] = "true"
    fixture.config.reload()

    def deny_file_handler(*args: object, **kwargs: object) -> None:
        raise PermissionError("synthetic permission denial")

    monkeypatch.setattr(
        logging_module,
        "SecureRotatingFileHandler",
        deny_file_handler,
    )

    with pytest.raises(LoggingManagerError, match="cannot initialize file logging"):
        fixture.manager.initialize()

    assert tuple(fixture.namespace_logger.handlers) == original_handlers
    fixture.manager.get_logger("bootstrap").info("old handler remains active")
    fixture.flush()
    assert "old handler remains active" in fixture.console.getvalue()


def test_shutdown_removes_handlers_and_allows_reinitialization(
    logging_factory: LoggingFactory,
) -> None:
    fixture = logging_factory(logging_overrides={"file_enabled": False})
    fixture.manager.initialize()

    fixture.manager.shutdown()

    assert fixture.namespace_logger.handlers == []
    with pytest.raises(LoggingManagerError, match="has not been initialized"):
        fixture.manager.get_logger("bootstrap")
    fixture.manager.initialize()
    fixture.manager.get_logger("bootstrap").info("reinitialized")
    fixture.flush()
    assert "reinitialized" in fixture.console.getvalue()


def test_component_logger_removes_stale_bypass_handlers(
    logging_factory: LoggingFactory,
) -> None:
    fixture = logging_factory(logging_overrides={"file_enabled": False})
    fixture.manager.initialize()
    component_logger = logging.getLogger(f"{fixture.namespace}.bootstrap")
    bypass_stream = io.StringIO()
    stale_handler = logging.StreamHandler(bypass_stream)
    component_logger.addHandler(stale_handler)

    logger = fixture.manager.get_logger("bootstrap")
    logger.info("central handler only")
    fixture.flush()

    assert component_logger.handlers == []
    assert bypass_stream.getvalue() == ""
    assert fixture.console.getvalue().count("central handler only") == 1


def test_component_and_context_validation_is_actionable(
    logging_factory: LoggingFactory,
) -> None:
    fixture = logging_factory()

    with pytest.raises(LoggingManagerError, match="has not been initialized"):
        fixture.manager.get_logger("bootstrap")

    fixture.manager.initialize()
    with pytest.raises(LoggingManagerError, match="component name is invalid"):
        fixture.manager.get_logger("invalid component")
    with pytest.raises(LoggingManagerError, match="unsupported.*password"):
        fixture.manager.get_logger("bootstrap", password="not-accepted")
    logger = fixture.manager.get_logger("bootstrap")
    with pytest.raises(LoggingManagerError, match="unsupported.*component"):
        logger.bind(component="spoofed")
    with pytest.raises(LoggingManagerError, match="extra context must be a mapping"):
        logger.info("invalid call context", extra=[])
    with pytest.raises(LoggingManagerError, match="unsupported.*custom"):
        logger.info("invalid field", extra={"custom": "value"})


def test_invalid_namespace_is_rejected(
    logging_factory: LoggingFactory,
) -> None:
    fixture = logging_factory()

    with pytest.raises(LoggingManagerError, match="namespace is invalid"):
        LoggingManager(
            fixture.config,
            fixture.runtime,
            namespace="invalid namespace",
        )
