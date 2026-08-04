"""Pure validation helpers for discovery observations."""

from __future__ import annotations

import ipaddress
import re
from datetime import UTC, datetime
from uuid import UUID

from .exceptions import DiscoveryValidationError

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_MAC = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")
MAX_METADATA_BYTES = 64 * 1024


def normalize_uuid(value: UUID | str, *, field: str) -> UUID:
    """Return a non-nil UUID or raise a discovery validation error."""
    try:
        result = value if isinstance(value, UUID) else UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise DiscoveryValidationError(f"{field} must be a valid UUID") from exc
    if result.int == 0:
        raise DiscoveryValidationError(f"{field} must not be the nil UUID")
    return result


def normalize_timestamp(value: datetime, *, field: str) -> datetime:
    """Normalize an aware timestamp to UTC."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DiscoveryValidationError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def normalize_hostname(value: str, *, field: str = "hostname") -> str:
    """Validate and normalize an ASCII hostname or FQDN."""
    text = normalize_required_text(value, field=field, maximum=253).lower().rstrip(".")
    if not text or any(not _HOST_LABEL.fullmatch(label) for label in text.split(".")):
        raise DiscoveryValidationError(f"{field} is invalid")
    return text


def normalize_address(value: str, *, field: str = "address") -> str:
    """Return the canonical form of an IPv4 or IPv6 address."""
    try:
        return str(ipaddress.ip_address(value.strip()))
    except (AttributeError, ValueError) as exc:
        raise DiscoveryValidationError(f"{field} must be a valid IP address") from exc


def normalize_mac(value: str | None) -> str | None:
    """Normalize a colon-separated MAC address when present."""
    if value is None:
        return None
    text = value.strip().lower().replace("-", ":")
    if not _MAC.fullmatch(text):
        raise DiscoveryValidationError("MAC address is invalid")
    return text


def normalize_required_text(value: str, *, field: str, maximum: int) -> str:
    """Validate required bounded text."""
    if not isinstance(value, str) or not value.strip():
        raise DiscoveryValidationError(f"{field} is required")
    text = value.strip()
    if len(text) > maximum:
        raise DiscoveryValidationError(f"{field} exceeds {maximum} characters")
    return text


def normalize_optional_text(
    value: str | None, *, field: str, maximum: int
) -> str | None:
    """Validate optional bounded text, converting blank values to ``None``."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise DiscoveryValidationError(f"{field} must be text")
    if not value.strip():
        return None
    return normalize_required_text(value, field=field, maximum=maximum)


def validate_metadata_size(entries: tuple[tuple[str, str], ...]) -> None:
    """Reject raw metadata whose normalized UTF-8 representation is too large."""
    size = sum(len(key.encode()) + len(value.encode()) for key, value in entries)
    if size > MAX_METADATA_BYTES:
        raise DiscoveryValidationError(
            f"metadata exceeds the {MAX_METADATA_BYTES}-byte limit"
        )
