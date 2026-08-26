"""Hermes result models and errors for LIM log analysis."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HermesInsight:
    summary: str
    probable_cause: str
    recommendations: tuple[str, ...]
    confidence: float


class HermesAnalysisError(RuntimeError):
    """Raised when Hermes cannot produce a valid LIM explanation."""
