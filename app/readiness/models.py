"""Readiness models for managed LIM servers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReadinessState(StrEnum):
    READY = "ready"
    ATTENTION = "attention"
    NOT_READY = "not_ready"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    name: str
    state: ReadinessState
    detail: str


@dataclass(frozen=True, slots=True)
class ServerReadiness:
    state: ReadinessState
    checks: tuple[ReadinessCheck, ...]

    @property
    def ready(self) -> bool:
        return self.state is ReadinessState.READY
