from pathlib import Path
from typing import Any

import yaml


def write_yaml(path: Path, data: Any) -> None:
    """Write test data as safe YAML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
