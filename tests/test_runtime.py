import shutil
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.__main__ as app_main
import app.runtime as runtime_module
from app.composition import CompositionError
from app.config import ConfigurationManager
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
    events: list[tuple[str, tuple[object, ...]]] = []

    class FakeLogger:
        def info(self, message: str, *args: object) -> None:
            events.append((message, args))

    class FakeLoggingManager:
        def get_logger(self, component: str, **context: object) -> FakeLogger:
            assert component == "bootstrap"
            assert context == {"operation": "startup"}
            return FakeLogger()

    services = SimpleNamespace(
        logging_manager=FakeLoggingManager(),
        migration_state=SimpleNamespace(schema_version=3),
    )
    monkeypatch.setattr(app_main, "build_application_services", lambda: services)

    assert app_main.main() == 0
    assert events == [
        (
            "LIM startup foundation initialized with schema_version=%d "
            "ssh_initialized=true bootstrap_initialized=true "
            "polling_initialized=true",
            (3,),
        )
    ]


def test_application_startup_sanitizes_pre_logging_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_composition() -> None:
        raise CompositionError("configuration")

    monkeypatch.setattr(app_main, "build_application_services", fail_composition)

    assert app_main.main() == 1
    assert capsys.readouterr().err == "LIM startup failed during configuration\n"


def test_application_startup_sanitizes_logging_initialization_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_composition() -> None:
        raise CompositionError("logging")

    monkeypatch.setattr(app_main, "build_application_services", fail_composition)

    assert app_main.main() == 1
    assert capsys.readouterr().err == "LIM startup failed during logging\n"
