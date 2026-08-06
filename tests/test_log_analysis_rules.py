"""Log analysis rule tests."""

from app.log_analysis import LogSeverity
from app.log_analysis.rules import analyze_events


def test_critical_and_warning_patterns_are_classified() -> None:
    findings = analyze_events(
        (
            {"source": "kernel", "message": "Out of memory: Killed process 42"},
            {"source": "sshd", "message": "Failed password for invalid user admin"},
        )
    )
    assert findings[0].severity is LogSeverity.CRITICAL
    assert findings[0].category == "memory"
    assert findings[1].severity is LogSeverity.WARNING
    assert findings[1].category == "authentication"
