"""Pure validation and normalization helpers for inventory values."""

from __future__ import annotations

import ipaddress
import re
from datetime import UTC, datetime
from uuid import UUID

from .exceptions import InventoryValidationError

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TAG_NAME = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
_LABEL_KEY = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


def normalize_hostname(value: str) -> str:
    """Return a lowercase RFC-style hostname or raise a domain error."""
    if not isinstance(value, str):
        raise InventoryValidationError("hostname must be a string")
    hostname = value.strip().rstrip(".").lower()
    if not hostname or len(hostname) > 253:
        raise InventoryValidationError("hostname must contain 1 to 253 characters")
    labels = hostname.split(".")
    if any(not _HOST_LABEL.fullmatch(label) for label in labels):
        raise InventoryValidationError("hostname contains an invalid label")
    return hostname


def normalize_address(value: str, *, field: str = "address") -> str:
    """Return a canonical IPv4 or IPv6 address without a zone identifier."""
    if not isinstance(value, str) or not value.strip() or "%" in value:
        raise InventoryValidationError(f"{field} must be a valid IP address")
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError as exc:
        raise InventoryValidationError(
            f"{field} must be a valid IP address"
        ) from exc


def normalize_uuid(value: UUID | str) -> UUID:
    """Return a UUID value without accepting ambiguous identifiers."""
    if isinstance(value, UUID):
        normalized = value
    else:
        if not isinstance(value, str):
            raise InventoryValidationError("server UUID is invalid")
        try:
            normalized = UUID(value)
        except ValueError as exc:
            raise InventoryValidationError("server UUID is invalid") from exc
    if normalized.int == 0:
        raise InventoryValidationError("server UUID cannot be nil")
    return normalized


def normalize_timestamp(value: datetime, *, field: str) -> datetime:
    """Require a timezone-aware timestamp and normalize it to UTC."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise InventoryValidationError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def normalize_optional_timestamp(
    value: datetime | None,
    *,
    field: str,
) -> datetime | None:
    """Normalize an optional timezone-aware timestamp to UTC."""
    if value is None:
        return None
    return normalize_timestamp(value, field=field)


def normalize_required_text(value: str, *, field: str, maximum: int) -> str:
    """Return trimmed required text bounded by ``maximum`` characters."""
    if not isinstance(value, str):
        raise InventoryValidationError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise InventoryValidationError(
            f"{field} must contain 1 to {maximum} characters"
        )
    return normalized


def normalize_optional_text(
    value: str | None,
    *,
    field: str,
    maximum: int,
) -> str | None:
    """Return trimmed optional text or ``None`` for a blank value."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InventoryValidationError(f"{field} must be a string or null")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise InventoryValidationError(
            f"{field} cannot exceed {maximum} characters"
        )
    return normalized


def normalize_tag_name(value: str) -> str:
    """Return a stable lowercase tag name."""
    if not isinstance(value, str):
        raise InventoryValidationError("tag name must be a string")
    normalized = value.strip().lower()
    if not _TAG_NAME.fullmatch(normalized):
        raise InventoryValidationError("tag name is invalid")
    return normalized


def normalize_label_key(value: str) -> str:
    """Return a stable lowercase label key."""
    if not isinstance(value, str):
        raise InventoryValidationError("label key must be a string")
    normalized = value.strip().lower()
    if not _LABEL_KEY.fullmatch(normalized):
        raise InventoryValidationError("label key is invalid")
    return normalized
