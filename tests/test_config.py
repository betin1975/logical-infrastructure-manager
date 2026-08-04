import traceback
from pathlib import Path

import pytest

from app import ConfigManager
from app.config import ConfigError, ConfigurationManager
from tests.helpers import write_yaml


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    default = tmp_path / "default.yml"
    local = tmp_path / "local.yml"
    write_yaml(
        default,
        {
            "app": {"name": "LIM", "debug": False},
            "logging": {"level": "INFO", "format": "plain"},
            "workers": 2,
        },
    )
    return default, local


def test_loads_default_configuration(paths: tuple[Path, Path]) -> None:
    default, local = paths
    config = ConfigurationManager(default, local, environ={})

    assert config.get("app.name") == "LIM"
    assert config.require("workers", int) == 2
    assert ConfigManager is ConfigurationManager


def test_deep_merges_local_configuration(paths: tuple[Path, Path]) -> None:
    default, local = paths
    write_yaml(local, {"app": {"debug": True}, "logging": {"level": "DEBUG"}})

    config = ConfigurationManager(default, local, environ={})

    assert config.get("app") == {"name": "LIM", "debug": True}
    assert config.get("logging.format") == "plain"
    assert config.get("logging.level") == "DEBUG"


def test_environment_overrides_are_nested_and_typed(paths: tuple[Path, Path]) -> None:
    default, local = paths
    config = ConfigurationManager(
        default,
        local,
        environ={"LIM_APP__DEBUG": "true", "LIM_WORKERS": "4"},
    )

    assert config.get("app.debug") is True
    assert config.get("workers") == 4


def test_environment_expansion_and_fallback(paths: tuple[Path, Path]) -> None:
    default, local = paths
    write_yaml(
        local,
        {"database": {"url": "postgres://${DB_HOST}:5432/${DB_NAME:-lim}"}},
    )

    config = ConfigurationManager(default, local, environ={"DB_HOST": "db.internal"})

    assert config.get("database.url") == "postgres://db.internal:5432/lim"


def test_missing_environment_reference_fails(paths: tuple[Path, Path]) -> None:
    default, local = paths
    write_yaml(local, {"token": "${REQUIRED_TOKEN}"})

    with pytest.raises(ConfigError, match="REQUIRED_TOKEN"):
        ConfigurationManager(default, local, environ={})


def test_missing_file_and_invalid_yaml_are_reported(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yml"
    with pytest.raises(ConfigError, match="required configuration file"):
        ConfigurationManager(missing, tmp_path / "local.yml", environ={})

    invalid = tmp_path / "invalid.yml"
    invalid.write_text("root: [unterminated", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot load configuration"):
        ConfigurationManager(invalid, tmp_path / "local.yml", environ={})


def test_missing_key_default_and_type_check(paths: tuple[Path, Path]) -> None:
    default, local = paths
    config = ConfigurationManager(default, local, environ={})

    assert config.get("missing", "fallback") == "fallback"
    with pytest.raises(ConfigError, match="missing required"):
        config.get("missing")
    with pytest.raises(ConfigError, match="must be str"):
        config.require("workers", str)


def test_returned_values_cannot_mutate_active_config(paths: tuple[Path, Path]) -> None:
    default, local = paths
    config = ConfigurationManager(default, local, environ={})

    app = config.get("app")
    app["name"] = "changed"
    snapshot = config.as_dict()
    snapshot["app"]["name"] = "also changed"

    assert config.get("app.name") == "LIM"


def test_reload_is_atomic_on_error(paths: tuple[Path, Path]) -> None:
    default, local = paths
    config = ConfigurationManager(default, local, environ={})
    write_yaml(local, {"logging": {"level": "VERBOSE"}})

    with pytest.raises(ConfigError, match="logging.level"):
        config.reload()

    assert config.get("logging.level") == "INFO"


def test_empty_key_and_tuple_type_error_are_reported(
    paths: tuple[Path, Path],
) -> None:
    default, local = paths
    config = ConfigurationManager(default, local, environ={})

    with pytest.raises(ConfigError, match="cannot be empty"):
        config.get("")
    with pytest.raises(ConfigError, match="str or list"):
        config.get("workers", expected_type=(str, list))


def test_empty_local_file_and_list_environment_expansion(
    paths: tuple[Path, Path],
) -> None:
    default, local = paths
    local.write_text("", encoding="utf-8")
    write_yaml(default, {"values": ["${ITEM}", 2]})

    config = ConfigurationManager(default, local, environ={"ITEM": "expanded"})

    assert config.get("values") == ["expanded", 2]


def test_non_mapping_file_and_section_are_rejected(tmp_path: Path) -> None:
    default = tmp_path / "default.yml"
    write_yaml(default, ["not", "a", "mapping"])

    with pytest.raises(ConfigError, match="must contain a mapping"):
        ConfigurationManager(default, tmp_path / "local.yml", environ={})

    write_yaml(default, {"app": "invalid"})
    with pytest.raises(ConfigError, match="section 'app'"):
        ConfigurationManager(default, tmp_path / "local.yml", environ={})


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"LIM_APP____DEBUG": "true"}, "invalid environment configuration key"),
        (
            {"LIM_APP": "scalar", "LIM_APP__DEBUG": "true"},
            "conflicting environment configuration key",
        ),
        ({"LIM_VALUE": "["}, "invalid value for environment variable"),
    ],
)
def test_invalid_environment_overrides_are_rejected(
    paths: tuple[Path, Path], environment: dict[str, str], message: str
) -> None:
    default, local = paths

    with pytest.raises(ConfigError, match=message):
        ConfigurationManager(default, local, environ=environment)


def test_invalid_environment_value_is_redacted(
    paths: tuple[Path, Path],
) -> None:
    default, local = paths
    secret = "synthetic-secret-that-must-not-leak"

    with pytest.raises(ConfigError) as caught:
        ConfigurationManager(
            default,
            local,
            environ={"LIM_TOKEN": f"[{secret}"},
        )

    rendered_error = "".join(traceback.format_exception(caught.value))
    assert secret not in str(caught.value)
    assert secret not in rendered_error
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_empty_environment_prefix_disables_overrides(
    paths: tuple[Path, Path],
) -> None:
    default, local = paths
    config = ConfigurationManager(
        default,
        local,
        env_prefix="",
        environ={"LIM_WORKERS": "99"},
    )

    assert config.get("workers") == 2
