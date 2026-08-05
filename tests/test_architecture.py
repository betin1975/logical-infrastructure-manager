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
    remote_bootstrap_artifact = (
        application_root / "bootstrap/artifacts/remote_health.py"
    )
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
        if imports_subprocess and path not in {
            command_module,
            remote_bootstrap_artifact,
        }:
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


def test_bootstrap_owns_no_ssh_process_persistence_or_discovery_behavior() -> None:
    """Keep bootstrap orchestration on SSHManager and InventoryService only."""
    project_root = Path(__file__).resolve().parent.parent
    bootstrap_root = project_root / "app/bootstrap"
    artifact_root = bootstrap_root / "artifacts"
    forbidden_modules = {
        "sqlite3",
        "subprocess",
        "app.persistence",
        "app.discovery",
        "app.collectors",
        "app.inventory.repository",
    }
    forbidden_names = {
        "DatabaseManager",
        "DiscoveryObservation",
        "DiscoveryRepository",
        "DiscoveryService",
        "InventoryRepository",
        "LinuxCollector",
        "SQLiteInventoryRepository",
        "TransactionManager",
    }
    violations: list[str] = []

    for path in bootstrap_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = str(path.relative_to(project_root))
        for node in ast.walk(tree):
            if path.is_relative_to(artifact_root):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = (
                        [alias.name for alias in node.names]
                        if isinstance(node, ast.Import)
                        else [node.module or ""]
                    )
                    if any(name == "app" or name.startswith("app.") for name in names):
                        violations.append(f"{relative}: artifact LIM import")
                continue
            if isinstance(node, ast.Import) and any(
                alias.name in forbidden_modules
                or any(
                    alias.name.startswith(f"{module}.") for module in forbidden_modules
                )
                for alias in node.names
            ):
                violations.append(relative)
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (
                    node.module in forbidden_modules
                    or any(
                        node.module.startswith(f"{module}.")
                        for module in forbidden_modules
                    )
                    or any(alias.name in forbidden_names for alias in node.names)
                )
            ):
                violations.append(relative)

    assert not violations, (
        "BootstrapService must use SSHManager and InventoryService only; it cannot "
        "own subprocess, persistence, discovery, or LinuxCollector behavior: "
        f"{sorted(set(violations))}"
    )


def test_collectors_use_services_and_sshmanager_boundaries_only() -> None:
    """Prevent collectors from gaining persistence or process ownership."""
    project_root = Path(__file__).resolve().parent.parent
    collector_root = project_root / "app/collectors"
    forbidden_modules = {
        "sqlite3",
        "subprocess",
        "app.persistence",
        "app.discovery.repository",
        "app.inventory.repository",
    }
    forbidden_names = {
        "DatabaseManager",
        "DiscoveryRepository",
        "InventoryRepository",
        "InventoryService",
        "SQLiteDiscoveryRepository",
        "SQLiteInventoryRepository",
        "TransactionManager",
    }
    violations: list[str] = []

    for path in collector_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name in forbidden_modules
                or any(
                    alias.name.startswith(f"{module}.") for module in forbidden_modules
                )
                for alias in node.names
            ):
                violations.append(str(path.relative_to(project_root)))
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (
                    node.module in forbidden_modules
                    or any(
                        node.module.startswith(f"{module}.")
                        for module in forbidden_modules
                    )
                    or any(alias.name in forbidden_names for alias in node.names)
                )
            ):
                violations.append(str(path.relative_to(project_root)))

    assert not violations, (
        "collectors may return observations but cannot own SQL, repositories, "
        f"inventory mutation, or subprocess execution: {sorted(set(violations))}"
    )


def test_polling_coordinates_services_without_infrastructure_ownership() -> None:
    """Keep on-demand polling above SSH, repositories, SQL, and subprocesses."""
    project_root = Path(__file__).resolve().parent.parent
    polling_root = project_root / "app/polling"
    forbidden_modules = {
        "sqlite3",
        "subprocess",
        "app.persistence",
        "app.ssh",
        "app.discovery.repository",
        "app.inventory.repository",
    }
    forbidden_names = {
        "DatabaseManager",
        "DiscoveryRepository",
        "InventoryRepository",
        "SQLiteDiscoveryRepository",
        "SQLiteInventoryRepository",
        "SSHManager",
        "TransactionManager",
    }
    violations: list[str] = []

    for path in polling_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = str(path.relative_to(project_root))
        for node in ast.walk(tree):
            modules: list[str] = []
            names: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
                names = [alias.name for alias in node.names]
            if any(
                module == forbidden
                or module.startswith(f"{forbidden}.")
                for module in modules
                for forbidden in forbidden_modules
            ) or set(names).intersection(forbidden_names):
                violations.append(relative)

    assert not violations, (
        "PollingService may coordinate InventoryService, DiscoveryService, and "
        "LinuxCollector only; it cannot own SSH, SQL, repositories, persistence, "
        f"or subprocess behavior: {sorted(set(violations))}"
    )
