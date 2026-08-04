"""Automated checks for permanent LIM dependency boundaries."""

from __future__ import annotations

import ast
from pathlib import Path


def test_sqlite_access_is_confined_to_persistence_and_composition() -> None:
    """Prevent business, SSH, plugin, and job code from bypassing repositories."""
    project_root = Path(__file__).resolve().parent.parent
    application_root = project_root / "app"
    persistence_root = application_root / "persistence"
    candidates = [*application_root.rglob("*.py")]
    plugins_root = project_root / "plugins"
    if plugins_root.is_dir():
        candidates.extend(plugins_root.rglob("*.py"))

    violations: list[str] = []
    composition_roots = {
        application_root / "__main__.py",
        application_root / "bootstrap.py",
    }
    low_level_managers = {
        "BackupManager",
        "BaseRepository",
        "DatabaseManager",
        "MigrationManager",
        "SQLiteDiscoveryRepository",
        "SQLiteInventoryRepository",
        "TransactionManager",
    }
    for path in candidates:
        if path.is_relative_to(persistence_root):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == "sqlite3" or alias.name.startswith("sqlite3.")
                for alias in node.names
            ):
                violations.append(str(path.relative_to(project_root)))
            if isinstance(node, ast.ImportFrom) and node.module and (
                node.module == "sqlite3" or node.module.startswith("sqlite3.")
            ):
                violations.append(str(path.relative_to(project_root)))
            if (
                isinstance(node, ast.ImportFrom)
                and path not in composition_roots
                and node.module
                and (
                    node.module.lstrip(".") == "persistence"
                    or node.module.lstrip(".").startswith("persistence.")
                    or node.module == "app.persistence"
                    or node.module.startswith("app.persistence.")
                )
                and any(alias.name in low_level_managers for alias in node.names)
            ):
                violations.append(str(path.relative_to(project_root)))

    assert not violations, (
        "SQLite and low-level persistence managers are restricted to "
        "app.persistence and composition roots; domain consumers must use "
        f"repository interfaces: {sorted(set(violations))}"
    )
