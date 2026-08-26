"""Persistence for Hermes explanations."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import UUID

from .hermes import HermesInsight


class HermesInsightStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def save(
        self,
        server_uuid: UUID,
        insight: HermesInsight,
    ) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)

        path = self._root / f"{server_uuid}.hermes.json"

        document = {
            "summary": insight.summary,
            "probable_cause": insight.probable_cause,
            "recommendations": list(insight.recommendations),
            "confidence": insight.confidence,
        }

        descriptor, temp_name = tempfile.mkstemp(
            dir=self._root,
            prefix=".hermes-",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)

        try:
            with os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    document,
                    handle,
                    separators=(",", ":"),
                )
                handle.flush()
                os.fsync(handle.fileno())

            os.chmod(temp_path, 0o600)
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def latest(
        self,
        server_uuid: UUID,
    ) -> HermesInsight | None:
        path = self._root / f"{server_uuid}.hermes.json"

        if not path.exists():
            return None

        try:
            document = json.loads(path.read_text(encoding="utf-8"))

            return HermesInsight(
                summary=str(document["summary"]),
                probable_cause=str(document["probable_cause"]),
                recommendations=tuple(
                    str(item) for item in document["recommendations"]
                ),
                confidence=float(document["confidence"]),
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
