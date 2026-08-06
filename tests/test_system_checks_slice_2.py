"""Focused checks for system-checks slice 2."""
from __future__ import annotations

from types import SimpleNamespace

from app.bootstrap.artifacts import remote_health
from app.dashboard.checks import build_system_checks


class TimeRunner:
    def __init__(self, value: str, *, exit_code: int = 0) -> None:
        self.value = value
        self.exit_code = exit_code
    def __call__(self, command: tuple[str, ...]) -> remote_health.CommandResult:
        assert command == (
            'timedatectl', 'show', '--property=NTPSynchronized', '--value'
        )
        status = 'ok' if self.exit_code == 0 else 'not_installed'
        return remote_health.CommandResult(self.exit_code, self.value, status)

def test_time_sync_reports_active_inactive_and_unknown() -> None:
    assert remote_health._time_sync(TimeRunner('yes\n')) == {
        'installation': 'installed', 'activity': 'active'
    }
    assert remote_health._time_sync(TimeRunner('no\n')) == {
        'installation': 'installed', 'activity': 'inactive'
    }
    assert remote_health._time_sync(TimeRunner('', exit_code=127)) == {
        'installation': 'unknown', 'activity': 'unknown'
    }

def test_dashboard_builds_new_service_checks() -> None:
    observation = SimpleNamespace(
        services=(
            SimpleNamespace(name='rsyslog', status='active'),
            SimpleNamespace(name='systemd-journald', status='active'),
            SimpleNamespace(name='node_exporter', status='not_installed'),
            SimpleNamespace(name='time-sync', status='inactive'),
            SimpleNamespace(name='docker', status='active'),
            SimpleNamespace(name='prometheus', status='not_installed'),
        ),
        memory=None,
        disks=(),
    )
    checks = {item['name']: item for item in build_system_checks(observation)}
    assert checks['Syslog']['status'] == 'healthy'
    assert checks['System journal']['status'] == 'healthy'
    assert checks['Node exporter']['status'] == 'not-installed'
    assert checks['Time synchronization']['status'] == 'critical'
    assert checks['Docker']['status'] == 'healthy'
    assert checks['Prometheus']['status'] == 'not-installed'
