from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import timedelta
from uuid import UUID

import pytest

from app.inventory import (
    InventoryValidationError,
    Label,
    RepositoryResult,
    ServerStatus,
    SynchronizationState,
    Tag,
)
from app.inventory.validation import (
    normalize_address,
    normalize_hostname,
    normalize_uuid,
)
from tests.helpers import (
    INVENTORY_NOW as NOW,
)
from tests.helpers import (
    INVENTORY_SERVER_ID as SERVER_ID,
)
from tests.helpers import (
    make_inventory_server as make_server,
)


def test_server_normalizes_domain_values_and_is_immutable() -> None:
    server = make_server(
        hostname="SERVER-01.Example.Test.",
        primary_address="192.0.2.010".replace("010", "10"),
        distribution="  Example Linux  ",
    )

    assert server.hostname == "server-01.example.test"
    assert server.primary_address == "192.0.2.10"
    assert server.management_address == "2001:db8::10"
    assert server.distribution == "Example Linux"
    assert {tag.name for tag in server.tags} == {"linux", "web"}
    assert {label.key for label in server.labels} == {"owner", "tier"}
    with pytest.raises(FrozenInstanceError):
        server.hostname = "changed.example.test"  # type: ignore[misc]


@pytest.mark.parametrize(
    "hostname",
    ["", "-bad.example", "bad-.example", "bad_name", "a" * 254],
)
def test_hostname_validation_rejects_invalid_values(hostname: str) -> None:
    with pytest.raises(InventoryValidationError, match="hostname"):
        normalize_hostname(hostname)


@pytest.mark.parametrize(
    "address",
    ["", "999.1.1.1", "server.example.test", "fe80::1%en0"],
)
def test_address_validation_rejects_invalid_values(address: str) -> None:
    with pytest.raises(InventoryValidationError, match="valid IP address"):
        normalize_address(address)


def test_uuid_validation_accepts_canonical_values_and_rejects_invalid() -> None:
    assert normalize_uuid(str(SERVER_ID)) == SERVER_ID
    assert normalize_uuid(SERVER_ID) is SERVER_ID
    with pytest.raises(InventoryValidationError, match="UUID"):
        normalize_uuid("not-a-uuid")
    with pytest.raises(InventoryValidationError, match="cannot be nil"):
        normalize_uuid(UUID(int=0))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"display_name": ""}, "display name"),
        ({"management_address": "192.0.2.10"}, "must differ"),
        ({"failure_count": -1}, "failure count"),
        ({"inventory_version": 0}, "inventory version"),
        ({"created_at": NOW.replace(tzinfo=None)}, "timezone-aware"),
        ({"updated_at": NOW - timedelta(seconds=1)}, "cannot precede"),
        ({"platform": "linux"}, "invalid enum"),
        ({"enabled": 1}, "must be booleans"),
        (
            {"status": ServerStatus.DELETED, "deleted_at": None},
            "deleted servers require",
        ),
        ({"enabled": False, "status": ServerStatus.ACTIVE}, "must be enabled"),
    ],
)
def test_server_rejects_invalid_domain_state(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(InventoryValidationError, match=message):
        make_server(**changes)


def test_server_rejects_duplicate_label_keys() -> None:
    with pytest.raises(InventoryValidationError, match="label keys must be unique"):
        make_server(
            labels=frozenset({Label("Owner", "one"), Label("owner", "two")})
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"tags": frozenset({"linux"})}, "Tag values"),
        ({"labels": frozenset({("owner", "platform")})}, "Label values"),
    ],
)
def test_server_requires_typed_tag_and_label_values(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(InventoryValidationError, match=message):
        make_server(**changes)


def test_server_evolve_versions_state_and_protects_identity() -> None:
    server = make_server()
    updated = server.evolve(now=NOW + timedelta(minutes=1), location="lab-b")

    assert updated.location == "lab-b"
    assert updated.inventory_version == 2
    assert updated.updated_at == NOW + timedelta(minutes=1)
    assert updated.synchronization_state is SynchronizationState.PENDING
    assert server.location == "lab-a"
    with pytest.raises(InventoryValidationError, match="identity"):
        server.evolve(now=NOW, uuid=UUID(int=0))


def test_server_soft_delete_and_restore_are_explicit_transitions() -> None:
    deleted = make_server().soft_delete(now=NOW + timedelta(minutes=1))

    assert deleted.status is ServerStatus.DELETED
    assert not deleted.enabled
    assert deleted.deleted_at == NOW + timedelta(minutes=1)
    with pytest.raises(InventoryValidationError, match="restored first"):
        deleted.evolve(now=NOW + timedelta(minutes=2), notes="blocked")

    restored = deleted.restore(now=NOW + timedelta(minutes=2))
    assert restored.status is ServerStatus.DISABLED
    assert restored.deleted_at is None
    assert not restored.enabled
    assert restored.inventory_version == 3


def test_poll_timestamps_follow_creation_and_latest_poll() -> None:
    with pytest.raises(InventoryValidationError, match="cannot precede creation"):
        make_server(last_poll_at=NOW - timedelta(seconds=1))
    with pytest.raises(InventoryValidationError, match="after the latest poll"):
        make_server(
            last_poll_at=NOW + timedelta(minutes=1),
            last_successful_poll_at=NOW + timedelta(minutes=2),
        )
    with pytest.raises(InventoryValidationError, match="after the latest poll"):
        make_server(last_failure_at=NOW + timedelta(minutes=1))


def test_tag_label_and_repository_result_validation() -> None:
    assert Tag(" Web.Prod ").name == "web.prod"
    assert Label(" Owner ", " Platform ") == Label("owner", "Platform")
    result = RepositoryResult(items=(1, 2), total=3, limit=2, offset=0)
    assert result.has_more
    with pytest.raises(InventoryValidationError, match="pagination"):
        RepositoryResult(items=(), total=-1, limit=0, offset=-1)
