"""Validation helpers for local keys and safe remote bootstrap configuration."""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path, PurePosixPath

from app.ssh.validation import normalize_username

from .exceptions import BootstrapConfigurationError, BootstrapValidationError

_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MARKER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_KEY_TYPES = frozenset(
    {"ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384"}
)


def validate_remote_path(value: str, *, field: str) -> str:
    """Require a normalized absolute path without traversal or control text."""
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 1024:
        raise BootstrapConfigurationError(f"{field} must be an absolute path")
    path = PurePosixPath(value)
    if (
        str(path) != value
        or ".." in path.parts
        or any(character in value for character in ("\x00", "\n", "\r", '"'))
    ):
        raise BootstrapConfigurationError(f"{field} is unsafe")
    return value


def validate_username(value: str, *, field: str) -> str:
    """Apply the SSH boundary's conservative account-name policy."""
    try:
        return normalize_username(value)
    except Exception as exc:
        raise BootstrapConfigurationError(f"{field} is invalid") from exc


def validate_mode(value: str, *, field: str, directory: bool) -> str:
    """Require a restrictive four-digit octal file mode."""
    if not isinstance(value, str) or not re.fullmatch(r"0[0-7]{3}", value):
        raise BootstrapConfigurationError(f"{field} must be a four-digit octal mode")
    mode = int(value, 8)
    if mode & 0o022 or (directory and not mode & 0o100):
        raise BootstrapConfigurationError(f"{field} is not restrictive")
    return value


def validate_version(value: str, *, field: str) -> str:
    """Validate a bounded version or marker token."""
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise BootstrapConfigurationError(f"{field} is invalid")
    return value


def validate_marker(value: str) -> str:
    """Validate the recognizable authorized-key ownership marker."""
    if not isinstance(value, str) or not _MARKER.fullmatch(value):
        raise BootstrapConfigurationError("bootstrap key marker is invalid")
    return value


def load_public_key(path: Path) -> tuple[str, str]:
    """Load one bounded OpenSSH public key and return type/body only."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
            raise BootstrapValidationError("monitor public key is unavailable")
        content = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise BootstrapValidationError("monitor public key is unavailable") from exc
    if "PRIVATE KEY" in content or len(content.splitlines()) != 1:
        raise BootstrapValidationError("monitor public key is invalid")
    fields = content.split()
    if len(fields) < 2 or fields[0] not in _KEY_TYPES:
        raise BootstrapValidationError("monitor public key is invalid")
    try:
        decoded = base64.b64decode(fields[1], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BootstrapValidationError("monitor public key is invalid") from exc
    if len(decoded) < 16 or len(decoded) > 16_384:
        raise BootstrapValidationError("monitor public key is invalid")
    return fields[0], fields[1]
