"""Defensive parsers for bounded Linux command results."""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Iterable, Mapping
from typing import Any

from app.discovery import (
    DiscoveryAddress,
    DiscoveryContainer,
    DiscoveryCPU,
    DiscoveryDisk,
    DiscoveryInterface,
    DiscoveryMemory,
    DiscoveryOperatingSystem,
    ObservedService,
)
from app.discovery.exceptions import DiscoveryValidationError
from app.discovery.validation import normalize_hostname

from .exceptions import LinuxParserError

_OS_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
_INTEGER = re.compile(r"^[0-9]+$")


def parse_hostname(output: str) -> str | None:
    """Return the first non-empty hostname line."""
    value = _first_line(output)
    if value is None:
        return None
    try:
        return normalize_hostname(value)
    except DiscoveryValidationError:
        return None


def parse_text_line(output: str, maximum: int) -> str | None:
    """Return one bounded non-empty line for a simple scalar fact."""
    value = _first_line(output)
    return _limited(value, maximum)


def parse_os_release(
    output: str,
) -> tuple[DiscoveryOperatingSystem, str | None, str | None]:
    """Parse standard os-release key/value content without evaluating it."""
    values: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if not _OS_KEY.fullmatch(key):
            continue
        try:
            parsed = shlex.split(raw_value, comments=False, posix=True)
        except ValueError:
            continue
        if len(parsed) == 1:
            values[key] = parsed[0]
        elif not parsed and raw_value == "":
            values[key] = ""
    name = values.get("PRETTY_NAME") or values.get("NAME") or "Linux"
    if not any(values.get(key) for key in ("PRETTY_NAME", "NAME", "ID")):
        raise LinuxParserError("os_release output is invalid")
    distribution = values.get("NAME") or values.get("ID")
    version = values.get("VERSION_ID") or values.get("VERSION")
    try:
        operating_system = DiscoveryOperatingSystem(name, distribution, version)
    except DiscoveryValidationError as exc:
        raise LinuxParserError("os_release output is invalid") from exc
    return (
        operating_system,
        _limited(values.get("ID"), 64),
        _limited(values.get("ID_LIKE"), 128),
    )


def parse_cpu(
    nproc_output: str | None, lscpu_output: str | None
) -> DiscoveryCPU | None:
    """Combine machine-readable counts and colon-delimited lscpu attributes."""
    fields: dict[str, str] = {}
    for line in (lscpu_output or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        if key and key not in fields:
            fields[key] = value.strip()
    logical = _positive_int(nproc_output) or _positive_int(fields.get("cpu(s)")) or 0
    sockets = _positive_int(fields.get("socket(s)"))
    cores_per_socket = _positive_int(fields.get("core(s) per socket"))
    physical = sockets * cores_per_socket if sockets and cores_per_socket else None
    model = _limited(fields.get("model name") or fields.get("model"), 255)
    if not logical and not model and physical is None:
        return None
    return DiscoveryCPU(model=model, logical_cores=logical, physical_cores=physical)


def parse_memory(output: str) -> DiscoveryMemory:
    """Parse the whitespace-delimited `free -b` Mem row."""
    for line in output.splitlines():
        fields = line.split()
        if fields and fields[0].rstrip(":").lower() == "mem" and len(fields) >= 2:
            total = _nonnegative_int(fields[1])
            available = _nonnegative_int(fields[6]) if len(fields) > 6 else None
            if total is not None and (available is None or available <= total):
                return DiscoveryMemory(total, available)
    raise LinuxParserError("memory output is invalid")


def parse_df(output: str) -> tuple[DiscoveryDisk, ...]:
    """Parse POSIX df rows using tokens rather than column positions."""
    disks: list[DiscoveryDisk] = []
    for line in output.splitlines()[1:]:
        fields = line.split(maxsplit=5)
        if len(fields) != 6:
            continue
        total = _nonnegative_int(fields[1])
        available = _nonnegative_int(fields[3])
        if total is None or available is None or available > total:
            continue
        try:
            disks.append(
                DiscoveryDisk(
                    name=fields[0],
                    total_bytes=total,
                    available_bytes=available,
                    mount_point=fields[5],
                )
            )
        except DiscoveryValidationError:
            continue
    if not disks:
        raise LinuxParserError("filesystem output is invalid")
    return _unique(disks, lambda item: (item.name, item.mount_point))


def parse_lsblk(output: str) -> tuple[DiscoveryDisk, ...]:
    """Parse recursive lsblk JSON and ignore unknown device fields."""
    document = _json_object(output, "block_devices")
    devices = document.get("blockdevices")
    if not isinstance(devices, list):
        raise LinuxParserError("block_devices output is invalid")
    parsed: list[DiscoveryDisk] = []
    for device in _walk_devices(devices):
        name = _text(device.get("name"), 255)
        size = _nonnegative_int(device.get("size"))
        if name is None or size is None:
            continue
        mounts = device.get("mountpoints")
        if not isinstance(mounts, list):
            mounts = [device.get("mountpoint")]
        valid_mounts = [_text(value, 1024) for value in mounts]
        valid_mounts = [value for value in valid_mounts if value is not None]
        if not valid_mounts:
            valid_mounts = [None]
        for mount in valid_mounts:
            try:
                parsed.append(
                    DiscoveryDisk(
                        name=name,
                        total_bytes=size,
                        mount_point=mount,
                        filesystem=_text(device.get("fstype"), 64),
                    )
                )
            except DiscoveryValidationError:
                continue
    return _unique(parsed, lambda item: (item.name, item.mount_point))


def parse_ip_address(
    output: str,
) -> tuple[tuple[DiscoveryInterface, ...], tuple[DiscoveryAddress, ...]]:
    """Parse iproute2 JSON interfaces and addresses defensively."""
    try:
        document = json.loads(output)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LinuxParserError("interfaces output is invalid") from exc
    if not isinstance(document, list):
        raise LinuxParserError("interfaces output is invalid")
    interfaces: list[DiscoveryInterface] = []
    addresses: list[DiscoveryAddress] = []
    for item in document:
        if not isinstance(item, Mapping):
            continue
        name = _text(item.get("ifname"), 128)
        if name is None:
            continue
        flags = item.get("flags")
        is_up = "UP" in flags if isinstance(flags, list) else None
        try:
            interfaces.append(
                DiscoveryInterface(
                    name=name,
                    mac_address=_text(item.get("address"), 64),
                    is_up=is_up,
                    mtu=_nonnegative_int(item.get("mtu")),
                )
            )
        except DiscoveryValidationError:
            continue
        address_info = item.get("addr_info")
        if not isinstance(address_info, list):
            continue
        for address_item in address_info:
            if not isinstance(address_item, Mapping):
                continue
            address = _text(address_item.get("local"), 128)
            if address is None:
                continue
            try:
                addresses.append(DiscoveryAddress(address, name))
            except DiscoveryValidationError:
                continue
    return (
        _unique(interfaces, lambda item: item.name),
        _unique(addresses, lambda item: (item.address, item.kind)),
    )


def parse_listening_services(output: str) -> tuple[ObservedService, ...]:
    """Parse ss endpoints without assuming fixed column widths."""
    services: list[ObservedService] = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[0].lower() in {"netid", "state"}:
            continue
        protocol = fields[0].lower()
        port_text = fields[4].rsplit(":", 1)[-1].rstrip("]")
        port = _positive_int(port_text)
        if port is None or port > 65535:
            continue
        try:
            services.append(
                ObservedService(f"listen/{protocol}", "listening", port=port)
            )
        except DiscoveryValidationError:
            continue
    return _unique(services, lambda item: (item.name, item.port))


def parse_systemd_services(output: str) -> tuple[ObservedService, ...]:
    """Parse systemd unit names while ignoring headers and summaries."""
    services: list[ObservedService] = []
    for line in output.splitlines():
        fields = line.strip().lstrip("●").split()
        if not fields or not fields[0].endswith(".service"):
            continue
        name = fields[0].removesuffix(".service")
        try:
            services.append(ObservedService(name, "running"))
        except DiscoveryValidationError:
            continue
    return _unique(services, lambda item: (item.name, item.port))


def parse_docker_version(output: str) -> tuple[tuple[str, str], ...]:
    """Extract a small allowlist from Docker's JSON version response."""
    document = _json_object(output, "docker_version")
    server = document.get("Server")
    client = document.get("Client")
    entries = [("detected", "true")]
    for prefix, section in (("server", server), ("client", client)):
        if not isinstance(section, Mapping):
            continue
        version = _text(section.get("Version"), 128)
        if version:
            entries.append((f"{prefix}_version", version))
    return tuple(entries)


def parse_docker_containers(output: str) -> tuple[DiscoveryContainer, ...]:
    """Parse newline-delimited Docker JSON, skipping malformed records."""
    containers: list[DiscoveryContainer] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, Mapping):
            continue
        identifier = _text(item.get("ID") or item.get("Id"), 255)
        name = _text(item.get("Names") or item.get("Name"), 255)
        image = _text(item.get("Image"), 512)
        status = _text(item.get("State") or item.get("Status"), 64)
        if not all((identifier, name, image, status)):
            continue
        try:
            containers.append(DiscoveryContainer(identifier, name, image, status))
        except DiscoveryValidationError:
            continue
    return _unique(containers, lambda item: item.identifier)


def active_product(name: str, output: str) -> tuple[tuple[str, str], ...]:
    """Return detection metadata only for an explicitly active service."""
    state = _first_line(output)
    if state is None or state.lower() != "active":
        return ()
    return (("detected", "true"), ("product", name), ("state", "active"))


def freepbx_product(
    version_output: str | None,
    asterisk_output: str | None,
) -> tuple[tuple[str, str], ...]:
    """Report Asterisk and best-effort FreePBX detection in one namespace."""
    entries: list[tuple[str, str]] = []
    if asterisk_output and _first_line(asterisk_output).lower() == "active":
        entries.extend((("asterisk_detected", "true"), ("asterisk_state", "active")))
    version = _first_line(version_output or "")
    if version:
        entries.append(("freepbx_detected", "true"))
        entries.append(("freepbx_version", _limited(version, 128) or "unknown"))
    return tuple(entries)


def merge_unique(items: Iterable[Any], key: Any) -> tuple[Any, ...]:
    """Expose stable first-wins merging for collector mapping."""
    return _unique(items, key)


def _json_object(output: str, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(output)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LinuxParserError(f"{name} output is invalid") from exc
    if not isinstance(value, Mapping):
        raise LinuxParserError(f"{name} output is invalid")
    return value


def _walk_devices(devices: list[Any]) -> Iterable[Mapping[str, Any]]:
    for device in devices:
        if not isinstance(device, Mapping):
            continue
        yield device
        children = device.get("children")
        if isinstance(children, list):
            yield from _walk_devices(children)


def _first_line(output: str) -> str | None:
    for line in output.splitlines():
        value = line.strip()
        if value:
            return _limited(value, 253)
    return None


def _limited(value: str | None, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:maximum] if text else None


def _text(value: object, maximum: int) -> str | None:
    return _limited(value if isinstance(value, str) else None, maximum)


def _positive_int(value: object) -> int | None:
    parsed = _nonnegative_int(value)
    return parsed if parsed and parsed > 0 else None


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value >= 0 else None
    if isinstance(value, str) and _INTEGER.fullmatch(value.strip()):
        return int(value.strip())
    return None


def _unique(items: Iterable[Any], key: Any) -> tuple[Any, ...]:
    result: list[Any] = []
    seen: set[Any] = set()
    for item in items:
        identity = key(item)
        if identity not in seen:
            seen.add(identity)
            result.append(item)
    return tuple(result)
