"""Deterministic first-pass classifiers for Linux system logs."""

from __future__ import annotations

from collections.abc import Iterable

from .models import LogFinding, LogSeverity
from .redaction import redact_text

_RULES = (
    (
        LogSeverity.CRITICAL,
        "memory",
        ("out of memory", "oom-killer", "killed process"),
        "Kernel memory exhaustion detected.",
    ),
    (
        LogSeverity.CRITICAL,
        "storage",
        ("i/o error", "filesystem error", "read-only file system"),
        "Storage or filesystem failure detected.",
    ),
    (
        LogSeverity.CRITICAL,
        "service",
        ("failed with result", "entered failed state", "segmentation fault"),
        "A service or process entered a failed state.",
    ),
    (
        LogSeverity.WARNING,
        "authentication",
        ("failed password", "authentication failure", "invalid user"),
        "Repeated authentication failures were observed.",
    ),
    (
        LogSeverity.WARNING,
        "container",
        ("docker", "containerd", "restart loop", "back-off restarting"),
        "Container runtime warnings or restart activity detected.",
    ),
    (
        LogSeverity.WARNING,
        "time",
        ("clock skew", "time jump", "ntp", "not synchronized"),
        "Time synchronization warning detected.",
    ),
)


def analyze_events(events: Iterable[dict[str, str]]) -> tuple[LogFinding, ...]:
    findings = []
    seen = set()
    for event in events:
        message = redact_text(str(event.get("message", "")))
        lowered = message.lower()
        source = redact_text(str(event.get("source", "system")), maximum=128)
        for severity, category, needles, summary in _RULES:
            if not any(needle in lowered for needle in needles):
                continue
            key = (category, message)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                LogFinding(
                    severity=severity,
                    source=source,
                    category=category,
                    summary=summary,
                    evidence=message,
                    confidence=0.9 if severity is LogSeverity.CRITICAL else 0.75,
                )
            )
    return tuple(findings)
