"""Typed log-analysis result models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class LogSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class LogFinding:
    severity: LogSeverity
    source: str
    category: str
    summary: str
    evidence: str
    confidence: float


@dataclass(frozen=True, slots=True)
class LogAnalysisResult:
    server_uuid: UUID
    hostname: str
    status: LogSeverity
    event_count: int
    findings: tuple[LogFinding, ...]
    summary: str
