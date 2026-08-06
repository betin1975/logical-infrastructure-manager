"""Redact secrets and sensitive values from collected logs."""

from __future__ import annotations

import re

_PATTERNS = (
    re.compile(r"(?i)\b(password|passwd|pwd)\s*[=:]\s*\S+"),
    re.compile(r"(?i)\b(token|api[_-]?key|secret)\s*[=:]\s*\S+"),
    re.compile(r"(?i)\bauthorization:\s*\S+(?:\s+\S+)?"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
)


def redact_text(value: str, *, maximum: int = 4096) -> str:
    text = value[:maximum]
    for pattern in _PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text
