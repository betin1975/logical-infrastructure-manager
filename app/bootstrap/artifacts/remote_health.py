#!/usr/bin/python3
# ruff: noqa: UP017, UP045 -- deployed artifact supports remote Python 3.9.
"""Standalone, bounded, read-only LIM Linux health collector.

This file is deployed verbatim to managed hosts. It deliberately imports no LIM
application package and requires only Python's standard library.
"""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

SCHEMA_VERSION = 1
COLLECTOR_VERSION = "1.0.0"
COMMAND_TIMEOUT_SECONDS = 5
MAX_COMMAND_BYTES = 65_536
MAX_DOCUMENT_BYTES = 262_144


@dataclass(frozen=True)
class CommandResult:
    """Bounded local command result used by the standalone artifact."""

    exit_code: Optional[int]
    stdout: str
    status: str


Runner = Callable[[tuple[str, ...]], CommandResult]


def run_command(command: tuple[str, ...]) -> CommandResult:
    """Run one fixed local command without a shell and retain bounded stdout."""
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
            close_fds=True,
            start_new_session=True,
        )
    except FileNotFoundError:
        return CommandResult(127, "", "not_installed")
    except OSError:
        return CommandResult(None, "", "unknown")
    assert process.stdout is not None
    os.set_blocking(process.stdout.fileno(), False)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output = bytearray()
    exceeded = False
    timed_out = False
    deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
    try:
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                available = MAX_COMMAND_BYTES - len(output)
                if available > 0:
                    output.extend(chunk[:available])
                exceeded |= len(chunk) > available
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        with suppress(OSError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        return CommandResult(None, "", "unknown")
    finally:
        selector.close()
        process.stdout.close()
    if timed_out or exceeded:
        return CommandResult(process.returncode, "", "unknown")
    return CommandResult(
        process.returncode,
        bytes(output).decode("utf-8", errors="replace"),
        "ok" if process.returncode == 0 else "failed",
    )


def collect(runner: Runner = run_command) -> dict[str, object]:
    """Collect approved health facts and explicit optional-service states."""
    hostname = _first(runner(("hostname", "--fqdn"))) or _first(runner(("hostname",)))
    if not hostname:
        raise RuntimeError("hostname unavailable")
    os_release = _os_release(runner(("cat", "/etc/os-release")))
    kernel = _first(runner(("uname", "-r")))
    architecture = _first(runner(("uname", "-m")))
    cpu_count = _integer(_first(runner(("nproc",))))
    memory = _memory(runner(("free", "-b")))
    filesystems = _filesystems(runner(("df", "-P", "-B1")))
    interfaces = _interfaces(runner(("ip", "-j", "address")))
    services = {
        name: _systemd_service(name, runner)
        for name in (
            "docker",
            "mysql",
            "mariadb",
            "redis",
            "prometheus",
            "asterisk",
        )
    }
    services["freepbx"] = _freepbx(runner)
    return {
        "schema_version": SCHEMA_VERSION,
        "collector_version": COLLECTOR_VERSION,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "host": {
            "hostname": hostname[:253],
            "operating_system": os_release,
            "kernel": (kernel or "unknown")[:255],
            "architecture": (architecture or "unknown")[:64],
            "logical_cpus": cpu_count,
            "memory": memory,
            "filesystems": filesystems,
            "interfaces": interfaces,
        },
        "services": services,
    }


def render(document: dict[str, object]) -> str:
    """Serialize one bounded JSON document without banners or secret fields."""
    output = json.dumps(document, sort_keys=True, separators=(",", ":"))
    if len(output.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise RuntimeError("collector document exceeds limit")
    return output


def _os_release(result: CommandResult) -> dict[str, str]:
    values: dict[str, str] = {}
    if result.status != "ok":
        return values
    allowed = {"NAME", "PRETTY_NAME", "ID", "ID_LIKE", "VERSION", "VERSION_ID"}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in allowed:
            values[key.lower()] = value.strip().strip("\"'")[:255]
    return values


def _memory(result: CommandResult) -> dict[str, Optional[int]]:
    if result.status == "ok":
        for line in result.stdout.splitlines():
            fields = line.split()
            if fields and fields[0].rstrip(":").lower() == "mem" and len(fields) >= 2:
                total = _integer(fields[1])
                available = _integer(fields[6]) if len(fields) > 6 else None
                return {"total_bytes": total, "available_bytes": available}
    return {"total_bytes": None, "available_bytes": None}


def _filesystems(result: CommandResult) -> list[dict[str, object]]:
    filesystems: list[dict[str, object]] = []
    if result.status != "ok":
        return filesystems
    for line in result.stdout.splitlines()[1:257]:
        fields = line.split(maxsplit=5)
        if len(fields) != 6:
            continue
        total = _integer(fields[1])
        available = _integer(fields[3])
        if total is None or available is None:
            continue
        filesystems.append(
            {
                "name": fields[0][:255],
                "total_bytes": total,
                "available_bytes": available,
                "mount_point": fields[5][:1024],
            }
        )
    return filesystems


def _interfaces(result: CommandResult) -> list[dict[str, object]]:
    if result.status != "ok":
        return []
    try:
        document = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(document, list):
        return []
    interfaces: list[dict[str, object]] = []
    for item in document[:256]:
        if not isinstance(item, dict) or not isinstance(item.get("ifname"), str):
            continue
        addresses = []
        address_info = item.get("addr_info", [])
        if isinstance(address_info, list):
            for address in address_info[:256]:
                if isinstance(address, dict) and isinstance(address.get("local"), str):
                    addresses.append(address["local"][:128])
        interfaces.append(
            {
                "name": item["ifname"][:128],
                "up": "UP" in item.get("flags", [])
                if isinstance(item.get("flags"), list)
                else None,
                "addresses": addresses,
            }
        )
    return interfaces


def _systemd_service(name: str, runner: Runner) -> dict[str, str]:
    unit = runner(
        ("systemctl", "list-unit-files", f"{name}.service", "--no-legend", "--no-pager")
    )
    if unit.exit_code == 127:
        return {"installation": "unknown", "activity": "unknown"}
    if unit.status != "ok":
        return {"installation": "unknown", "activity": "unknown"}
    installed = any(
        line.split() and line.split()[0] == f"{name}.service"
        for line in unit.stdout.splitlines()
    )
    if not installed:
        if name == "docker":
            docker = runner(("docker", "--version"))
            if docker.exit_code != 127:
                return {"installation": "installed", "activity": "unknown"}
        return {"installation": "not_installed", "activity": "not_applicable"}
    active = runner(("systemctl", "is-active", name))
    state = (_first_output(active.stdout) or "unknown").lower()
    activity = (
        "active"
        if state == "active"
        else "inactive"
        if state
        in {
            "inactive",
            "failed",
            "deactivating",
        }
        else "unknown"
    )
    return {"installation": "installed", "activity": activity}


def _freepbx(runner: Runner) -> dict[str, str]:
    version = runner(("fwconsole", "--version"))
    if version.exit_code == 127:
        return {"installation": "not_installed", "activity": "not_applicable"}
    if version.status != "ok":
        return {"installation": "unknown", "activity": "unknown"}
    return {
        "installation": "installed",
        "activity": "unknown",
        "version": (_first(version) or "unknown")[:128],
    }


def _first(result: CommandResult) -> Optional[str]:
    if result.status != "ok":
        return None
    return _first_output(result.stdout)


def _first_output(output: str) -> Optional[str]:
    for line in output.splitlines():
        if line.strip():
            return line.strip()
    return None


def _integer(value: Optional[str]) -> Optional[int]:
    if value is None or not value.strip().isdigit():
        return None
    return int(value.strip())


def main(argv: Optional[list[str]] = None) -> int:
    """Emit exactly one JSON document and reject all caller arguments."""
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        return 2
    try:
        output = render(collect())
    except Exception:
        return 1
    os.write(sys.stdout.fileno(), output.encode("utf-8") + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
