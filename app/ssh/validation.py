"""Input and filesystem validation for the SSH boundary."""

from __future__ import annotations

import ipaddress
import os
import re
import stat
from pathlib import Path
from uuid import UUID

from .exceptions import SSHIdentityError, SSHValidationError

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_USERNAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_REMOTE_PATH = re.compile(r"^/[A-Za-z0-9_./-]+$")


def normalize_host(value: str) -> str:
    """Normalize a hostname or IP address without DNS resolution."""
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 253:
        raise SSHValidationError("SSH host is invalid")
    text = value.strip()
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        hostname = text.lower().rstrip(".")
        if not hostname or any(
            not _HOST_LABEL.fullmatch(label) for label in hostname.split(".")
        ):
            raise SSHValidationError("SSH host is invalid") from None
        return hostname


def normalize_username(value: str) -> str:
    """Reject usernames containing whitespace or shell syntax."""
    if not isinstance(value, str) or not _USERNAME.fullmatch(value):
        raise SSHValidationError("SSH username is invalid")
    return value


def normalize_port(value: int) -> int:
    """Validate a TCP port without accepting booleans."""
    if type(value) is not int or not 1 <= value <= 65535:
        raise SSHValidationError("SSH port must be an integer from 1 to 65535")
    return value


def normalize_timeout(value: float | int, *, field: str) -> float:
    """Validate a positive bounded timeout."""
    if type(value) not in (int, float) or not 0 < float(value) <= 86400:
        raise SSHValidationError(f"{field} must be greater than zero")
    return float(value)


def normalize_uuid(value: UUID | str | None) -> UUID | None:
    """Validate an optional non-nil server UUID."""
    if value is None:
        return None
    try:
        result = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise SSHValidationError("server UUID is invalid") from exc
    if result.int == 0:
        raise SSHValidationError("server UUID must not be nil")
    return result


def normalize_command(command: tuple[str, ...]) -> tuple[str, ...]:
    """Validate a structured remote executable and argument vector."""
    if not isinstance(command, tuple) or not command:
        raise SSHValidationError("remote command must be a non-empty tuple")
    if len(command) > 256:
        raise SSHValidationError("remote command contains too many arguments")
    normalized: list[str] = []
    for argument in command:
        if (
            not isinstance(argument, str)
            or not argument
            or "\x00" in argument
            or "\n" in argument
            or "\r" in argument
            or len(argument) > 16384
        ):
            raise SSHValidationError("remote command contains an invalid argument")
        normalized.append(argument)
    if sum(len(item.encode("utf-8")) for item in normalized) > 65536:
        raise SSHValidationError("remote command exceeds its total size limit")
    if normalized[0].startswith("-"):
        raise SSHValidationError("remote executable is invalid")
    return tuple(normalized)


def normalize_remote_path(value: str) -> str:
    """Validate an explicit absolute POSIX remote file path."""
    if (
        not isinstance(value, str)
        or not _REMOTE_PATH.fullmatch(value)
        or "\x00" in value
        or "\n" in value
        or "\r" in value
        or len(value) > 4096
        or any(part == ".." for part in value.split("/"))
    ):
        raise SSHValidationError("remote path must be an absolute safe path")
    return value


def validate_private_key(path: Path, credential_root: Path) -> Path:
    """Validate a private key is contained, regular, non-symlinked, and read-only."""
    candidate = path.absolute()
    root = credential_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise SSHIdentityError("SSH identity is outside the credential root") from exc
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise SSHIdentityError("SSH identity is missing or inaccessible") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SSHIdentityError("SSH identity must be a regular non-symlink file")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise SSHIdentityError("SSH identity permits group or world access")
    if stat.S_IMODE(metadata.st_mode) & 0o200:
        raise SSHIdentityError("SSH identity must be mounted read-only")
    if not os.access(candidate, os.R_OK):
        raise SSHIdentityError("SSH identity is not readable")
    return candidate


def validate_executable(path: Path, *, label: str) -> Path:
    """Validate an explicit executable path without searching ambient PATH."""
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SSHValidationError(f"{label} executable is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or not os.access(path, os.X_OK)
    ):
        raise SSHValidationError(f"{label} executable is unsafe or not executable")
    return path.absolute()
