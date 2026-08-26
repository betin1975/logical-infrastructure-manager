"""Atomic file-backed storage for health/security assessments."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from .models import HealthSecurityAssessment


class HealthSecurityStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def save(self, assessment: HealthSecurityAssessment) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)
        path = self._path(assessment.server_uuid)

        document = asdict(assessment)
        document["server_uuid"] = str(assessment.server_uuid)

        descriptor, name = tempfile.mkstemp(
            dir=self._root,
            prefix=".health-security-",
            suffix=".tmp",
        )
        temp = Path(name)

        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()

    def latest(self, server_uuid: UUID) -> HealthSecurityAssessment | None:
        path = self._path(server_uuid)
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return HealthSecurityAssessment(
                server_uuid=UUID(str(data["server_uuid"])),
                hostname=str(data["hostname"]),
                generated_at=str(data["generated_at"]),
                collector_version=str(data["collector_version"]),
                overall_status=str(data["overall_status"]),
                available_updates=int(data["available_updates"]),
                security_updates=int(data["security_updates"]),
                security_packages=tuple(data["security_packages"]),
                attention_security_packages=tuple(data["attention_security_packages"]),
                reboot_required=bool(data["reboot_required"]),
                apt_lists_age_seconds=data["apt_lists_age_seconds"],
                apt_lists_stale=data["apt_lists_stale"],
                failed_units=tuple(data["failed_units"]),
                critical_logs=int(data["critical_logs"]),
                error_logs=int(data["error_logs"]),
                warning_logs=int(data["warning_logs"]),
                security_logs=int(data["security_logs"]),
                findings=tuple(data["findings"]),
            )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ):
            return None

    def _path(self, server_uuid: UUID) -> Path:
        return self._root / f"{server_uuid}.json"
