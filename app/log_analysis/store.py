"""Atomic file-backed persistence for LIM log-analysis results."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from .models import LogAnalysisResult, LogFinding, LogSeverity


class LogAnalysisStore:
    def __init__(self, root: Path, *, history_limit: int = 50) -> None:
        self._root = root
        self._history_limit = max(1, history_limit)

    def initialize(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)

    def save(self, result: LogAnalysisResult) -> None:
        self.initialize()
        path = self._root / f"{result.server_uuid}.json"
        document = self._read(path)
        history = list(document.get("history", ()))

        data = asdict(result)
        data["server_uuid"] = str(result.server_uuid)
        data["status"] = result.status.value
        data["findings"] = [
            {**item, "severity": item["severity"].value} for item in data["findings"]
        ]

        history.insert(0, data)
        history = history[: self._history_limit]
        self._write(path, {"schema_version": 1, "history": history})

    def latest(self, server_uuid: UUID) -> LogAnalysisResult | None:
        history = self.history(server_uuid, limit=1)
        return history[0] if history else None

    def history(
        self,
        server_uuid: UUID,
        *,
        limit: int = 20,
    ) -> tuple[LogAnalysisResult, ...]:
        path = self._root / f"{server_uuid}.json"
        output = []

        for item in self._read(path).get("history", ())[:limit]:
            try:
                findings = tuple(
                    LogFinding(
                        severity=LogSeverity(str(finding["severity"])),
                        source=str(finding["source"]),
                        category=str(finding["category"]),
                        summary=str(finding["summary"]),
                        evidence=str(finding["evidence"]),
                        confidence=float(finding["confidence"]),
                    )
                    for finding in item.get("findings", ())
                )
                output.append(
                    LogAnalysisResult(
                        server_uuid=UUID(str(item["server_uuid"])),
                        hostname=str(item["hostname"]),
                        status=LogSeverity(str(item["status"])),
                        event_count=int(item["event_count"]),
                        findings=findings,
                        summary=str(item["summary"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue

        return tuple(output)

    @staticmethod
    def _read(path: Path) -> dict[str, object]:
        if not path.exists():
            return {"history": []}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {"history": []}

    @staticmethod
    def _write(path: Path, document: dict[str, object]) -> None:
        descriptor, name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".log-analysis-",
            suffix=".tmp",
        )
        temp_path = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
