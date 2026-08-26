"""Bounded Hermes CLI runner for LIM log explanations."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from .hermes import HermesAnalysisError, HermesInsight
from .models import LogAnalysisResult


@dataclass(frozen=True, slots=True)
class HermesCLISettings:
    executable: str = "/usr/local/libexec/lim/hermes-oneshot"
    timeout_seconds: float = 120.0


class HermesCLIRunner:
    def __init__(self, settings: HermesCLISettings) -> None:
        self._settings = settings

    def analyze(
        self,
        result: LogAnalysisResult,
    ) -> HermesInsight:
        prompt = self._build_prompt(result)

        try:
            completed = subprocess.run(
                [
                    "sudo",
                    "-n",
                    "-H",
                    "-u",
                    "hermes",
                    self._settings.executable,
                ],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._settings.timeout_seconds,
                check=False,
            )
        except (
            OSError,
            subprocess.TimeoutExpired,
        ) as exc:
            raise HermesAnalysisError("Hermes invocation failed") from exc

        if completed.returncode != 0:
            error = completed.stderr.strip()
            raise HermesAnalysisError(
                f"Hermes exited with status {completed.returncode}: {error[:500]}"
            )

        try:
            data = json.loads(completed.stdout.strip())

            confidence = float(data["confidence"])
            if not 0 <= confidence <= 1:
                raise ValueError("confidence outside 0..1")

            recommendations = tuple(
                str(item).strip()
                for item in data["recommendations"]
                if str(item).strip()
            )

            return HermesInsight(
                summary=str(data["summary"]).strip(),
                probable_cause=str(data["probable_cause"]).strip(),
                recommendations=recommendations[:10],
                confidence=confidence,
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise HermesAnalysisError(
                "Hermes returned invalid structured analysis"
            ) from exc

    @staticmethod
    def _build_prompt(
        result: LogAnalysisResult,
    ) -> str:
        payload = {
            "hostname": result.hostname,
            "status": result.status.value,
            "event_count": result.event_count,
            "local_summary": result.summary,
            "findings": [
                {
                    "severity": finding.severity.value,
                    "source": finding.source,
                    "category": finding.category,
                    "summary": finding.summary,
                    "evidence": finding.evidence,
                    "local_confidence": (finding.confidence),
                }
                for finding in result.findings
            ],
        }

        return (
            "You are LIM's read-only infrastructure "
            "log analyst. The following JSON contains "
            "already-redacted findings. Do not execute "
            "commands, request credentials, or claim "
            "access to anything outside this input. "
            "Return JSON only with exactly the keys "
            "summary, probable_cause, recommendations, "
            "confidence. recommendations must be an "
            "array of short operator checks. confidence "
            "must be a number from 0 to 1. Input: "
            + json.dumps(
                payload,
                separators=(",", ":"),
            )
        )
