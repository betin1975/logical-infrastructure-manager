"""Derived system checks for dashboard observations."""

from __future__ import annotations

from typing import Any, Iterable


def build_system_checks(observation: object | None) -> tuple[dict[str, str], ...]:
    if observation is None:
        return ()

    services = _service_index(getattr(observation, "services", ()))
    return (
        _service_check("Docker", services, ("docker",)),
        _service_check("Prometheus", services, ("prometheus",)),
        _service_check("Syslog", services, ("rsyslog", "syslog-ng", "syslog")),
        _service_check(
            "System journal",
            services,
            ("systemd-journald", "journald"),
        ),
        _memory_check(getattr(observation, "memory", None)),
        _root_disk_check(getattr(observation, "disks", ())),
        {
            "name": "Time synchronization",
            "status": "unknown",
            "detail": "Not collected by the current remote artifact.",
        },
    )


def _service_index(services: Iterable[object]) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for service in services or ():
        name = _text(
            getattr(service, "name", None)
            or getattr(service, "service_name", None)
            or getattr(service, "product", None)
        )
        if not name:
            continue
        installation = _enum_text(
            getattr(service, "installation", None)
            or getattr(service, "installation_state", None)
            or getattr(service, "installed", None)
        )
        activity = _enum_text(
            getattr(service, "activity", None)
            or getattr(service, "activity_state", None)
            or getattr(service, "state", None)
            or getattr(service, "status", None)
        )
        result[name.lower()] = (installation, activity)
    return result


def _service_check(
    label: str,
    services: dict[str, tuple[str, str]],
    aliases: tuple[str, ...],
) -> dict[str, str]:
    match = next((services[name] for name in aliases if name in services), None)
    if match is None:
        return {
            "name": label,
            "status": "unknown",
            "detail": "No positive service evidence was collected.",
        }

    installation, activity = match
    if activity in {"active", "running", "healthy"}:
        return {
            "name": label,
            "status": "healthy",
            "detail": "Installed and active.",
        }
    if installation in {"not_installed", "not-installed", "absent"}:
        return {
            "name": label,
            "status": "not-installed",
            "detail": "Not installed.",
        }
    if activity in {"inactive", "failed", "stopped", "dead"}:
        return {
            "name": label,
            "status": "critical",
            "detail": f"Installed but {activity.replace('_', ' ')}.",
        }
    return {
        "name": label,
        "status": "unknown",
        "detail": "State could not be determined.",
    }


def _memory_check(memory: object | None) -> dict[str, str]:
    total = _integer(getattr(memory, "total_bytes", None)) if memory else None
    available = _integer(getattr(memory, "available_bytes", None)) if memory else None
    if not total or available is None or available > total:
        return {
            "name": "Memory",
            "status": "unknown",
            "detail": "Memory usage is unavailable.",
        }

    used_percent = round((total - available) * 100 / total, 1)
    status = (
        "critical"
        if used_percent >= 95
        else "warning"
        if used_percent >= 85
        else "healthy"
    )
    return {
        "name": "Memory",
        "status": status,
        "detail": f"{used_percent:.1f}% used (warning 85%, critical 95%).",
    }


def _root_disk_check(disks: Iterable[object]) -> dict[str, str]:
    root = None
    for disk in disks or ():
        mount = _text(
            getattr(disk, "mount_point", None)
            or getattr(disk, "mount", None)
        )
        if mount == "/":
            root = disk
            break

    if root is None:
        return {
            "name": "Root filesystem",
            "status": "unknown",
            "detail": "Root filesystem usage is unavailable.",
        }

    total = _integer(getattr(root, "total_bytes", None))
    available = _integer(getattr(root, "available_bytes", None))
    if not total or available is None or available > total:
        return {
            "name": "Root filesystem",
            "status": "unknown",
            "detail": "Root filesystem usage is unavailable.",
        }

    used_percent = round((total - available) * 100 / total, 1)
    status = (
        "critical"
        if used_percent >= 90
        else "warning"
        if used_percent >= 80
        else "healthy"
    )
    return {
        "name": "Root filesystem",
        "status": status,
        "detail": f"{used_percent:.1f}% used (warning 80%, critical 90%).",
    }


def _enum_text(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value)).strip().lower()


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _integer(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None
