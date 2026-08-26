"""Typed LIM health/security assessment models."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class HealthSecurityAssessment:
    server_uuid: UUID
    hostname: str
    generated_at: str
    collector_version: str
    overall_status: str
    available_updates: int
    security_updates: int
    security_packages: tuple[str, ...]
    attention_security_packages: tuple[str, ...]
    reboot_required: bool
    apt_lists_age_seconds: int | None
    apt_lists_stale: bool | None
    failed_units: tuple[str, ...]
    critical_logs: int
    error_logs: int
    warning_logs: int
    security_logs: int
    findings: tuple[dict[str, object], ...]
