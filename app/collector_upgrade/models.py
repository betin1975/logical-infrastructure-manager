"""Collector upgrade result models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class CollectorUpgradeStatus(StrEnum):
    PENDING = "pending"
    SKIPPED = "skipped"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CollectorUpgradeResult:
    server_uuid: UUID
    hostname: str
    address: str
    status: CollectorUpgradeStatus
    message: str
    previous_version: str | None = None
    target_version: str | None = None
