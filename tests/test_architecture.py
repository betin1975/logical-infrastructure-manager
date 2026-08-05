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
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module == "sqlite3" or node.module.startswith("sqlite3."))
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


def test_ssh_process_ownership_and_domain_isolation() -> None:
    """Keep OpenSSH invocation inside its runner and SSH free of domain mutation."""
    project_root = Path(__file__).resolve().parent.parent
    application_root = project_root / "app"
    ssh_root = application_root / "ssh"
    command_module = ssh_root / "command.py"
    violations: list[str] = []
    ssh_tools = {"ssh", "scp", "sftp", "ssh-keyscan", "ssh-keygen"}

    for path in application_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = str(path.relative_to(project_root))
        imports_subprocess = any(
            isinstance(node, ast.Import)
            and any(alias.name == "subprocess" for alias in node.names)
            or isinstance(node, ast.ImportFrom)
            and node.module == "subprocess"
            for node in ast.walk(tree)
        )
        if imports_subprocess and path != command_module:
            violations.append(f"{relative}: subprocess ownership")

        if path.is_relative_to(ssh_root):
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    module = node.module.lstrip(".")
                    if (
                        module.startswith("persistence")
                        or module.startswith("inventory")
                        or module.startswith("discovery")
                        or module.startswith("app.persistence")
                        or module.startswith("app.inventory")
                        or module.startswith("app.discovery")
                    ):
                        violations.append(f"{relative}: forbidden domain import")
                if isinstance(node, ast.Call):
                    for keyword in node.keywords:
                        if (
                            keyword.arg == "shell"
                            and isinstance(keyword.value, ast.Constant)
                            and keyword.value.value is not False
                        ):
                            violations.append(f"{relative}: shell execution")
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function_name = ""
            if isinstance(node.func, ast.Attribute):
                function_name = node.func.attr
            elif isinstance(node.func, ast.Name):
                function_name = node.func.id
            if function_name not in {
                "Popen",
                "run",
                "call",
                "check_call",
                "check_output",
                "system",
                "popen",
                "create_subprocess_exec",
                "create_subprocess_shell",
            }:
                continue
            literals = {
                child.value
                for child in ast.walk(node)
                if isinstance(child, ast.Constant) and isinstance(child.value, str)
            }
            if literals.intersection(ssh_tools):
                violations.append(f"{relative}: direct SSH tool invocation")

    assert not violations, (
        "OpenSSH subprocesses belong only to app.ssh.command; SSH cannot import "
        f"persistence or mutate inventory/discovery: {sorted(set(violations))}"
    )
