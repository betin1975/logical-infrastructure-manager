from types import SimpleNamespace

from app.readiness import ReadinessState
from app.readiness.service import ReadinessService


def test_ready_server():
    server = SimpleNamespace(last_bootstrap_at="2026-08-26T17:00:00Z")

    result = ReadinessService().assess(
        server=server,
        latest=object(),
        log_analysis=object(),
        health_security=object(),
    )

    assert result.state is ReadinessState.READY
    assert result.ready is True


def test_missing_bootstrap_is_not_ready():
    server = SimpleNamespace(last_bootstrap_at=None)

    result = ReadinessService().assess(
        server=server,
        latest=object(),
        log_analysis=object(),
        health_security=object(),
    )

    assert result.state is ReadinessState.NOT_READY


def test_unverified_capabilities_require_attention():
    server = SimpleNamespace(last_bootstrap_at="2026-08-26T17:00:00Z")

    result = ReadinessService().assess(
        server=server,
        latest=object(),
    )

    assert result.state is ReadinessState.ATTENTION
