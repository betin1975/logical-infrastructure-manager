import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

import app.__main__ as app_main
import app.runtime as runtime_module
from app.config import ConfigError, ConfigurationManager
from app.logging_manager import LoggingManagerError
from app.runtime import RuntimeManager, RuntimeManagerError, RuntimePaths
from tests.helpers import write_yaml

RuntimeConfigFactory = Callable[[dict[str, object] | None], ConfigurationManager]


@pytest.fixture
def runtime_config_factory(tmp_path: Path) -> RuntimeConfigFactory:
    default_paths: dict[str, object] = {
        "runtime": "runtime",
        "data": "runtime/data",
        "jobs": "runtime/jobs",
        "logs": "runtime/logs",
        "backups": "runtime/backups",
    }

    def create(overrides: dict[str, object] | None = None) -> ConfigurationManager:
        paths = dict(default_paths)
        if overrides:
            paths.update(overrides)
        default = tmp_path / "config/default.yml"
        write_yaml(default, {"paths": paths, "logging": {"level": "INFO"}})
        return ConfigurationManager(
            default,
            tmp_path / "config/local.yml",
            environ={},
        )

    return create


def test_initialize_creates_complete_runtime_tree(
    tmp_path: Path,
    runtime_config_factory: RuntimeConfigFactory,
) -> None:
    manager = RuntimeManager(
        runtime_config_factory(),
        application_root=tmp_path,
    )
    assert not manager.is_initialized

    paths = manager.initialize()

    assert manager.is_initialized
    assert paths == RuntimePaths(
        root=tmp_path / "runtime",
        data=tmp_path / "runtime/data",
        jobs=tmp_path / "runtime/jobs",
        logs=tmp_path / "runtime/logs",
        backups=tmp_path / "runtime/backups",
    )
    assert manager.runtime_path == paths.root
    assert paths.root.is_dir()
    for directory in (paths.data, paths.jobs, paths.logs, paths.backups):
        assert directory.is_dir()
        assert (directory / ".gitkeep").is_file()


def test_initialize_is_idempotent_and_preserves_existing_placeholders(
    tmp_path: Path,
    runtime_config_factory: RuntimeConfigFactory,
) -> None:
    manager = RuntimeManager(
        runtime_config_factory(),
        application_root=tmp_path,
    )
    manager.paths.logs.mkdir(parents=True)
    placeholder = manager.paths.logs / ".gitkeep"
    placeholder.write_text("preserve", encoding="utf-8")
    original_mtime = placeholder.stat().st_mtime_ns

    first = manager.initialize()
    second = manager.initialize()

    assert first == second
    assert placeholder.read_text(encoding="utf-8") == "preserve"
    assert placeholder.stat().st_mtime_ns == original_mtime


def test_initialize_recreates_a_missing_managed_directory(
    tmp_path: Path,
    runtime_config_factory: RuntimeConfigFactory,
) -> None:
    manager = RuntimeManager(
        runtime_config_factory(),
        application_root=tmp_path,
    )
    manager.initialize()
    shutil.rmtree(manager.paths.jobs)

    manager.initialize()

    assert manager.paths.jobs.is_dir()
    assert (manager.paths.jobs / ".gitkeep").is_file()


@pytest.mark.parametrize(
    ("path_name", "error_message"),
    [
        ("data", "SQLite data directory is not writable"),
        ("logs", "log directory is not writable"),
    ],
)
def test_initialize_rejects_non_writable_critical_directories(
    tmp_path: Path,
    runtime_config_factory: RuntimeConfigFactory,
    monkeypatch: pytest.MonkeyPatch,
    path_name: str,
    error_message: str,
) -> None:
    manager = RuntimeManager(
        runtime_config_factory(),
        application_root=tmp_path,
    )
    target = getattr(manager.paths, path_name)
    real_access = runtime_module.os.access
    monkeypatch.setattr(
        runtime_module.os,
        "access",
        lambda path, mode: False if path == target else real_access(path, mode),
    )

    with pytest.raises(RuntimeManagerError, match=error_message):
        manager.initialize()


def test_initialize_rejects_a_failed_write_probe(
    tmp_path: Path,
    runtime_config_factory: RuntimeConfigFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = RuntimeManager(
        runtime_config_factory(),
        application_root=tmp_path,
    )
    real_temporary_file = runtime_module.tempfile.NamedTemporaryFile

    def create_temporary_file(*args: object, **kwargs: object) -> object:
        if kwargs.get("dir") == manager.paths.logs:
            raise PermissionError("synthetic permission denial")
        return real_temporary_file(*args, **kwargs)

    monkeypatch.setattr(
        runtime_module.tempfile,
        "NamedTemporaryFile",
        create_temporary_file,
    )

    with pytest.raises(RuntimeManagerError, match="log directory is not writable"):
        manager.initialize()


@pytest.mark.parametrize(
    ("overrides", "error_message"),
    [
        ({"runtime": ""}, "paths.runtime cannot be empty"),
        ({"data": "outside/data"}, "paths.data must be inside paths.runtime"),
        ({"logs": 42}, "paths.logs.*must be str"),
    ],
)
def test_invalid_runtime_configuration_is_rejected(
    tmp_path: Path,
    runtime_config_factory: RuntimeConfigFactory,
    overrides: dict[str, object],
    error_message: str,
) -> None:
    with pytest.raises(RuntimeManagerError, match=error_message):
        RuntimeManager(
            runtime_config_factory(overrides),
            application_root=tmp_path,
        )


def test_missing_runtime_configuration_is_rejected(
    tmp_path: Path,
) -> None:
    default = tmp_path / "config/default.yml"
    write_yaml(
        default,
        {
            "paths": {
                "data": "runtime/data",
                "jobs": "runtime/jobs",
                "logs": "runtime/logs",
                "backups": "runtime/backups",
            }
        },
    )
    config = ConfigurationManager(
        default,
        tmp_path / "config/local.yml",
        environ={},
    )

    with pytest.raises(RuntimeManagerError, match="paths.runtime"):
        RuntimeManager(config, application_root=tmp_path)


def test_initialize_rejects_a_file_where_directory_is_required(
    tmp_path: Path,
    runtime_config_factory: RuntimeConfigFactory,
) -> None:
    manager = RuntimeManager(
        runtime_config_factory(),
        application_root=tmp_path,
    )
    manager.paths.root.mkdir()
    manager.paths.logs.write_text("not a directory", encoding="utf-8")

    with pytest.raises(RuntimeManagerError, match="cannot create log directory"):
        manager.initialize()


def test_initialize_rejects_non_regular_placeholder(
    tmp_path: Path,
    runtime_config_factory: RuntimeConfigFactory,
) -> None:
    manager = RuntimeManager(
        runtime_config_factory(),
        application_root=tmp_path,
    )
    manager.paths.logs.mkdir(parents=True)
    (manager.paths.logs / ".gitkeep").mkdir()

    with pytest.raises(RuntimeManagerError, match="placeholder is not a regular file"):
        manager.initialize()


def test_helper_methods_return_paths_below_managed_directories(
    tmp_path: Path,
    runtime_config_factory: RuntimeConfigFactory,
) -> None:
    manager = RuntimeManager(
        runtime_config_factory(),
        application_root=tmp_path,
    )

    assert manager.data_path("lim.sqlite3") == manager.paths.data / "lim.sqlite3"
    assert manager.job_path("job-123") == manager.paths.jobs / "job-123"
    assert manager.log_path("lim.log") == manager.paths.logs / "lim.log"
    assert manager.backup_path("lim.db.gz") == manager.paths.backups / "lim.db.gz"


@pytest.mark.parametrize(
    "unsafe_name",
    ["", ".", "..", "../escape", "nested/file", "nested\\file", "/absolute"],
)
def test_helper_methods_reject_unsafe_names(
    tmp_path: Path,
    runtime_config_factory: RuntimeConfigFactory,
    unsafe_name: str,
) -> None:
    manager = RuntimeManager(
        runtime_config_factory(),
        application_root=tmp_path,
    )

    for helper in (
        manager.data_path,
        manager.job_path,
        manager.log_path,
        manager.backup_path,
    ):
        with pytest.raises(RuntimeManagerError, match="single relative path component"):
            helper(unsafe_name)


def test_application_startup_initializes_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    fake_config = object()

    class FakeRuntimeManager:
        def __init__(self, config: object, *, application_root: Path) -> None:
            events.extend((config, application_root))

        def initialize(self) -> None:
            events.append("initialized")

    class FakeLogger:
        def info(self, message: str, *args: object) -> None:
            events.append((message, args))

        def exception(self, message: str) -> None:
            events.append(message)

    class FakeLoggingManager:
        def __init__(self, config: object, runtime: object) -> None:
            events.extend((config, runtime))

        def initialize(self) -> None:
            events.append("logging initialized")

        def get_logger(self, component: str, **context: str) -> FakeLogger:
            events.extend((component, context))
            return FakeLogger()

    class FakeDatabaseManager:
        def __init__(self, config: object, runtime: object) -> None:
            events.extend((config, runtime))

        def initialize(self) -> None:
            events.append("database initialized")

    class FakeMigrationState:
        schema_version = 1

    class FakeMigrationManager:
        def __init__(self, database: object) -> None:
            events.append(database)

        def apply_pending(self) -> FakeMigrationState:
            events.append("migrations applied")
            return FakeMigrationState()

    class FakeSSHManager:
        def __init__(
            self,
            config: object,
            runtime: object,
            logger: object,
            *,
            application_root: Path,
        ) -> None:
            events.extend(("ssh manager", config, runtime, logger, application_root))

        def initialize(self) -> None:
            events.append("ssh initialized")

    class FakeTransactionManager:
        def __init__(self, database: object) -> None:
            events.append("transactions initialized")

    class FakeInventoryRepository:
        def __init__(self, database: object, transactions: object) -> None:
            events.append("inventory repository initialized")

    class FakeInventoryService:
        def __init__(self, repository: object, logger: object) -> None:
            events.append("inventory service initialized")

    class FakeBootstrapService:
        def __init__(self, *args: object, **kwargs: object) -> None:
            events.append("bootstrap service constructed")

        def initialize(self) -> None:
            events.append("bootstrap service initialized")

    monkeypatch.setattr(app_main, "ConfigManager", lambda: fake_config)
    monkeypatch.setattr(app_main, "RuntimeManager", FakeRuntimeManager)
    monkeypatch.setattr(app_main, "LoggingManager", FakeLoggingManager)
    monkeypatch.setattr(app_main, "DatabaseManager", FakeDatabaseManager)
    monkeypatch.setattr(app_main, "MigrationManager", FakeMigrationManager)
    monkeypatch.setattr(app_main, "SSHManager", FakeSSHManager)
    monkeypatch.setattr(app_main, "TransactionManager", FakeTransactionManager)
    monkeypatch.setattr(app_main, "SQLiteInventoryRepository", FakeInventoryRepository)
    monkeypatch.setattr(app_main, "InventoryService", FakeInventoryService)
    monkeypatch.setattr(app_main, "BootstrapService", FakeBootstrapService)

    assert app_main.main() == 0
    assert events[0] is fake_config
    assert events[1] == Path(app_main.__file__).resolve().parent.parent
    assert events[2] == "initialized"
    assert events[3] is fake_config
    assert isinstance(events[4], FakeRuntimeManager)
    assert events[5:9] == [
        "logging initialized",
        "bootstrap",
        {"operation": "startup"},
        fake_config,
    ]
    assert isinstance(events[9], FakeRuntimeManager)
    assert events[10] == "database initialized"
    assert isinstance(events[11], FakeDatabaseManager)
    assert events[12] == "migrations applied"
    assert events[13:16] == [
        "ssh",
        {"operation": "initialize"},
        "ssh manager",
    ]
    assert events[16] is fake_config
    assert isinstance(events[17], FakeRuntimeManager)
    assert isinstance(events[18], FakeLogger)
    assert events[19] == Path(app_main.__file__).resolve().parent.parent
    assert events[20] == "ssh initialized"
    assert events[21:24] == [
        "transactions initialized",
        "inventory repository initialized",
        "inventory",
    ]
    assert "inventory service initialized" in events
    assert "bootstrap service constructed" in events
    assert "bootstrap service initialized" in events
    assert events[-1] == (
        "LIM startup foundation initialized with schema_version=%d "
        "ssh_initialized=true bootstrap_initialized=true",
        (1,),
    )


def test_application_startup_sanitizes_pre_logging_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_configuration() -> None:
        raise ConfigError("token=pre-logging-secret")

    monkeypatch.setattr(app_main, "ConfigManager", fail_configuration)

    assert app_main.main() == 1
    assert capsys.readouterr().err == (
        "LIM startup failed before logging initialization\n"
    )


def test_application_startup_sanitizes_logging_initialization_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeRuntimeManager:
        def __init__(self, config: object, *, application_root: Path) -> None:
            pass

        def initialize(self) -> None:
            pass

    class FailingLoggingManager:
        def __init__(self, config: object, runtime: object) -> None:
            pass

        def initialize(self) -> None:
            raise LoggingManagerError("password=logging-secret")

    monkeypatch.setattr(app_main, "ConfigManager", object)
    monkeypatch.setattr(app_main, "RuntimeManager", FakeRuntimeManager)
    monkeypatch.setattr(app_main, "LoggingManager", FailingLoggingManager)

    assert app_main.main() == 1
    assert capsys.readouterr().err == "LIM logging initialization failed\n"
