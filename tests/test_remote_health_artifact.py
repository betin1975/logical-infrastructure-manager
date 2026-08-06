"""Standalone tests for the deployed, standard-library-only health artifact."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from app.bootstrap.artifacts import remote_health


class FixtureRunner:
    def __init__(self, *, distribution: str = "ubuntu") -> None:
        self.distribution = distribution
        self.services = {
	    "docker": ("installed", "active"),
	    "mysql": ("not_installed", "not_applicable"),
	    "mariadb": ("installed", "inactive"),
	    "redis": ("installed", "active"),
	    "prometheus": ("installed", "active"),
	    "asterisk": ("not_installed", "not_installed"),

	    "rsyslog": ("installed", "active"),
	    "syslog-ng": ("not_installed", "not_installed"),
	    "systemd-journald": ("installed", "active"),
	    "node_exporter": ("not_installed", "not_installed"),
}

        self.systemd_absent = False
        self.freepbx = "not_installed"

    def __call__(self, command: tuple[str, ...]) -> remote_health.CommandResult:
        if command == ("hostname", "--fqdn"):
            return self._ok("node-01.example.test\n")
        if command == ("hostname",):
            return self._ok("node-01\n")
        if command == ("cat", "/etc/os-release"):
            names = {
                "ubuntu": "Ubuntu",
                "debian": "Debian GNU/Linux",
                "rocky": "Rocky Linux",
                "almalinux": "AlmaLinux",
            }
            return self._ok(
                f'NAME="{names[self.distribution]}"\nID={self.distribution}\n'
                'VERSION_ID="1"\nPASSWORD="must-not-appear"\n'
            )
        if command == ("uname", "-r"):
            return self._ok("6.8.0\n")
        if command == ("uname", "-m"):
            return self._ok("x86_64\n")
        if command == ("nproc",):
            return self._ok("4\n")
        if command == ("free", "-b"):
            return self._ok("Mem: 1000 100 100 0 0 800\n")
        if command == ("df", "-P", "-B1"):
            return self._ok(
                "Filesystem 1-blocks Used Available Capacity Mounted on\n"
                "/dev/sda1 1000 100 900 10% /\n"
            )
        if command == ("ip", "-j", "address"):
            return self._ok(
                '[{"ifname":"eth0","flags":["UP"],"addr_info":'
                '[{"local":"192.0.2.10"}],"credential":"never"}]'
            )
        if command == (
            "timedatectl",
            "show",
            "--property=NTPSynchronized",
            "--value",
        ):
            return self._ok("yes\\n")

        if command[0] == "systemctl" and command[1] == "list-unit-files":
            if self.systemd_absent:
                return remote_health.CommandResult(127, "", "not_installed")
            name = command[2].removesuffix(".service")
            installation, _ = self.services[name]
            if installation == "not_installed":
                return self._ok("")
            return self._ok(f"{name}.service enabled\n")
        if command[0:2] == ("systemctl", "is-active"):
            _, activity = self.services[command[2]]
            return (
                self._ok("active\n")
                if activity == "active"
                else remote_health.CommandResult(3, "inactive\n", "failed")
            )
        if command == ("fwconsole", "--version"):
            if self.freepbx == "not_installed":
                return remote_health.CommandResult(127, "", "not_installed")
            if self.freepbx == "unknown":
                return remote_health.CommandResult(1, "malformed", "failed")
            return self._ok("17.0\n")
        return remote_health.CommandResult(127, "", "not_installed")

    @staticmethod
    def _ok(stdout: str) -> remote_health.CommandResult:
        return remote_health.CommandResult(0, stdout, "ok")


@pytest.mark.parametrize("distribution", ["ubuntu", "debian", "rocky", "almalinux"])
def test_artifact_collects_supported_distributions_without_credentials(
    distribution: str,
) -> None:
    document = remote_health.collect(FixtureRunner(distribution=distribution))
    rendered = remote_health.render(document)

    assert document["schema_version"] == remote_health.SCHEMA_VERSION
    assert document["collector_version"] == remote_health.COLLECTOR_VERSION
    assert document["host"]["operating_system"]["id"] == distribution
    assert document["host"]["memory"]["total_bytes"] == 1000
    assert document["host"]["interfaces"][0]["addresses"] == ["192.0.2.10"]
    assert "PASSWORD" not in rendered
    assert "must-not-appear" not in rendered
    assert "credential" not in rendered
    assert len(rendered.encode()) <= remote_health.MAX_DOCUMENT_BYTES
    assert json.loads(rendered) == document


def test_artifact_distinguishes_optional_service_states() -> None:
    document = remote_health.collect(FixtureRunner())
    services = document["services"]
    assert services["docker"] == {
        "installation": "installed",
        "activity": "active",
    }
    assert services["mysql"] == {
        "installation": "not_installed",
        "activity": "not_applicable",
    }
    assert services["mariadb"] == {
        "installation": "installed",
        "activity": "inactive",
    }
    assert services["redis"]["activity"] == "active"


def test_artifact_handles_absent_systemd_docker_and_freepbx() -> None:
    runner = FixtureRunner()
    runner.systemd_absent = True
    document = remote_health.collect(runner)
    assert document["services"]["docker"] == {
        "installation": "unknown",
        "activity": "unknown",
    }
    assert document["services"]["freepbx"] == {
        "installation": "not_installed",
        "activity": "not_applicable",
    }


def test_artifact_tolerates_malformed_optional_results() -> None:
    runner = FixtureRunner()
    runner.freepbx = "unknown"
    document = remote_health.collect(runner)
    assert document["services"]["freepbx"] == {
        "installation": "unknown",
        "activity": "unknown",
    }


def test_artifact_fails_only_when_required_host_identity_is_unavailable() -> None:
    def unavailable(command: tuple[str, ...]) -> remote_health.CommandResult:
        return remote_health.CommandResult(127, "", "not_installed")

    with pytest.raises(RuntimeError):
        remote_health.collect(unavailable)
    assert remote_health.main(["unexpected"]) == 2


def test_artifact_render_enforces_document_bound() -> None:
    with pytest.raises(RuntimeError):
        remote_health.render({"value": "x" * remote_health.MAX_DOCUMENT_BYTES})


def test_artifact_command_runner_bounds_output_and_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remote_health, "MAX_COMMAND_BYTES", 8)
    oversized = remote_health.run_command((sys.executable, "-c", "print('x' * 1000)"))
    assert oversized.status == "unknown"
    assert oversized.stdout == ""

    monkeypatch.setattr(remote_health, "COMMAND_TIMEOUT_SECONDS", 0.01)
    timed_out = remote_health.run_command(
        (sys.executable, "-c", "import time; time.sleep(1)")
    )
    assert timed_out.status == "unknown"
    assert timed_out.stdout == ""


def test_artifact_remains_python_39_grammar_compatible() -> None:
    source = Path(remote_health.__file__).read_text(encoding="utf-8")

    ast.parse(source, filename=remote_health.__file__, feature_version=(3, 9))
