"""Operational overview derived from the latest server observation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def build_server_overview(server, observation, system_checks):
    checks = tuple(system_checks or ())
    index = {item["name"]: item for item in checks}
    score = max(
        0,
        100
        - sum(
            {"critical": 25, "warning": 10, "unknown": 3}.get(
                item.get("status", "unknown"), 0
            )
            for item in checks
        ),
    )
    health_status = (
        "healthy" if score >= 90 else "warning" if score >= 75 else "critical"
    )
    health_label = (
        "Excellent" if score >= 90 else "Needs attention" if score >= 75 else "Critical"
    )

    disks = tuple(getattr(observation, "disks", ()) or ()) if observation else ()
    interfaces = (
        tuple(getattr(observation, "interfaces", ()) or ()) if observation else ()
    )
    addresses = (
        tuple(getattr(observation, "addresses", ()) or ()) if observation else ()
    )
    services = tuple(getattr(observation, "services", ()) or ()) if observation else ()
    containers = (
        tuple(getattr(observation, "containers", ()) or ()) if observation else ()
    )

    storage_total = 0
    storage_available = 0
    highest_mount = None
    highest_percent = None
    for disk in disks:
        total = _integer(getattr(disk, "total_bytes", None))
        available = _integer(getattr(disk, "available_bytes", None))
        if not total or available is None or available > total:
            continue
        storage_total += total
        storage_available += available
        percent = round((total - available) * 100 / total, 1)
        if highest_percent is None or percent > highest_percent:
            highest_percent = percent
            highest_mount = _text(
                getattr(disk, "mount_point", None)
                or getattr(disk, "mount", None)
                or getattr(disk, "name", None)
            )

    up = sum(getattr(item, "up", None) is True for item in interfaces)
    down = sum(getattr(item, "up", None) is False for item in interfaces)
    ipv4 = 0
    ipv6 = 0
    for item in addresses:
        value = _text(getattr(item, "address", None) or item)
        if ":" in value:
            ipv6 += 1
        elif value:
            ipv4 += 1

    active = 0
    inactive = 0
    unknown = 0
    for service in services:
        state = _enum_text(
            getattr(service, "activity", None)
            or getattr(service, "status", None)
            or getattr(service, "state", None)
        )
        if state in {"active", "running", "healthy"}:
            active += 1
        elif state in {"inactive", "failed", "stopped", "dead"}:
            inactive += 1
        else:
            unknown += 1

    memory = getattr(observation, "memory", None) if observation else None
    memory_total = _integer(getattr(memory, "total_bytes", None)) if memory else None
    memory_available = (
        _integer(getattr(memory, "available_bytes", None)) if memory else None
    )
    memory_percent = None
    if (
        memory_total
        and memory_available is not None
        and memory_available <= memory_total
    ):
        memory_percent = round(
            (memory_total - memory_available) * 100 / memory_total, 1
        )

    root_percent = None
    for disk in disks:
        mount = _text(
            getattr(disk, "mount_point", None) or getattr(disk, "mount", None)
        )
        if mount == "/":
            total = _integer(getattr(disk, "total_bytes", None))
            available = _integer(getattr(disk, "available_bytes", None))
            if total and available is not None and available <= total:
                root_percent = round((total - available) * 100 / total, 1)
            break

    operating_system = (
        getattr(observation, "operating_system", None) if observation else None
    )
    os_name = (
        _text(
            getattr(operating_system, "pretty_name", None)
            or getattr(operating_system, "name", None)
        )
        or "Unknown OS"
    )

    last_poll = getattr(server, "last_poll_at", None) or getattr(
        server, "last_successful_poll_at", None
    )

    return {
        "health": {
            "score": score,
            "label": health_label,
            "status": health_status,
            "warnings": sum(
                item.get("status") in {"critical", "warning", "unknown"}
                for item in checks
            ),
        },
        "storage": {
            "count": len(disks),
            "total_bytes": storage_total,
            "used_bytes": storage_total - storage_available if storage_total else 0,
            "used_percent": round(
                (storage_total - storage_available) * 100 / storage_total, 1
            )
            if storage_total
            else None,
            "highest_mount": highest_mount,
            "highest_percent": highest_percent,
            "status": _threshold(highest_percent, 80, 90),
        },
        "network": {
            "interfaces": len(interfaces),
            "up": up,
            "down": down,
            "addresses": len(addresses),
            "ipv4": ipv4,
            "ipv6": ipv6,
            "status": "warning" if down else "healthy",
        },
        "services": {
            "total": len(services),
            "active": active,
            "inactive": inactive,
            "unknown": unknown,
            "status": "critical" if inactive else "healthy",
            "highlights": tuple(
                index[name]
                for name in ("Docker", "Prometheus", "Syslog", "Time synchronization")
                if name in index
            ),
        },
        "platform": {
            "os": os_name,
            "kernel": _text(getattr(observation, "kernel", None))
            if observation
            else "Unknown",
            "architecture": _text(getattr(observation, "architecture", None))
            if observation
            else "Unknown",
            "collector_version": _text(getattr(observation, "collector_version", None))
            if observation
            else "Unknown",
            "last_poll": _relative_time(last_poll),
        },
        "resources": {
            "memory": {
                "used_percent": memory_percent,
                "total_bytes": memory_total or 0,
                "status": _threshold(memory_percent, 85, 95),
            },
            "root_disk": {
                "used_percent": root_percent,
                "status": _threshold(root_percent, 80, 90),
            },
            "containers": {
                "count": len(containers),
                "docker_status": index.get("Docker", {}).get("status", "unknown"),
            },
            "collector": {
                "version": _text(getattr(observation, "collector_version", None))
                if observation
                else "Unknown",
                "status": "healthy" if observation else "unknown",
            },
        },
    }


def _threshold(value, warning, critical):
    if value is None:
        return "unknown"
    if value >= critical:
        return "critical"
    if value >= warning:
        return "warning"
    return "healthy"


def _relative_time(value):
    if not isinstance(value, datetime):
        return "Never"
    current = datetime.now(UTC)
    stamp = value if value.tzinfo else value.replace(tzinfo=UTC)
    seconds = max(0, int((current - stamp).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _enum_text(value: Any) -> str:
    return (
        str(getattr(value, "value", value)).strip().lower() if value is not None else ""
    )


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _integer(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None
