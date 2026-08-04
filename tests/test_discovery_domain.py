from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.discovery import (
    DiscoveryAddress,
    DiscoveryConflictError,
    DiscoveryContainer,
    DiscoveryCPU,
    DiscoveryDisk,
    DiscoveryInterface,
    DiscoveryMemory,
    DiscoveryMetadata,
    DiscoveryNetwork,
    DiscoveryPackage,
    DiscoveryProcess,
    DiscoveryResult,
    DiscoveryStatus,
    DiscoveryValidationError,
    ObservationSource,
    ObservationState,
    ObservedService,
)
from tests.helpers import INVENTORY_NOW as NOW
from tests.helpers import make_discovery_observation as make_observation


def test_complete_observation_is_immutable_and_normalized() -> None:
    observation = make_observation(hostname="SERVER-01.EXAMPLE.TEST.")

    assert observation.hostname == "server-01.example.test"
    assert observation.addresses[0].address == "192.0.2.10"
    assert observation.created_at.tzinfo is UTC
    with pytest.raises(FrozenInstanceError):
        observation.hostname = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: DiscoveryAddress("invalid"), "valid IP"),
        (lambda: DiscoveryInterface("eth0", "not-a-mac"), "MAC"),
        (lambda: DiscoveryInterface("eth0", mtu=-1), "MTU"),
        (lambda: DiscoveryCPU(logical_cores=-1), "logical cores"),
        (lambda: DiscoveryMemory(10, 11), "available memory"),
        (lambda: DiscoveryDisk("disk", 10, 11), "available bytes"),
        (lambda: ObservedService("web", "up", port=0), "port"),
        (lambda: DiscoveryPackage("", "1"), "package name"),
        (lambda: DiscoveryContainer("", "c", "i", "up"), "identifier"),
        (lambda: DiscoveryProcess(-1, "p"), "PID"),
        (lambda: DiscoveryNetwork(default_gateway="bad"), "gateway"),
        (
            lambda: DiscoveryMetadata(
                tuple((str(index), "x" * 4096) for index in range(17))
            ),
            "metadata exceeds",
        ),
        (lambda: DiscoveryMetadata((("a", "1"), ("a", "2"))), "unique"),
        (lambda: DiscoveryMetadata((("api-token", "synthetic"),)), "credential"),
        (lambda: DiscoveryMetadata(("invalid",)), "key/value pairs"),
    ],
)
def test_value_objects_reject_invalid_collected_facts(
    factory: object, message: str
) -> None:
    with pytest.raises(DiscoveryValidationError, match=message):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize(
    "changes",
    [
        {"uuid": UUID(int=0)},
        {"server_uuid": "bad"},
        {"source": "manual"},
        {"hostname": "bad host"},
        {"collection_duration_ms": -1},
        {"created_at": datetime(2026, 1, 1)},
        {"discovered_at": NOW + timedelta(seconds=1)},
        {"interfaces": ("eth0",)},
        {"cpu": "eight cores"},
        {"raw_metadata": object()},
        {"version": 0},
    ],
)
def test_observation_rejects_invalid_identity_state_and_timestamps(
    changes: dict[str, object],
) -> None:
    with pytest.raises(DiscoveryValidationError):
        make_observation(**changes)


def test_observation_lifecycle_enforces_legal_transitions() -> None:
    successful = make_observation().transition(
        ObservationState.SUCCESSFUL, now=NOW + timedelta(seconds=1)
    )
    expired = successful.transition(
        ObservationState.EXPIRED, now=NOW + timedelta(seconds=2)
    )
    failed = make_observation().transition(
        ObservationState.FAILED,
        now=NOW + timedelta(seconds=1),
        failure_reason="collector unavailable",
    )

    assert successful.status is DiscoveryStatus.COMPLETE
    assert expired.state is ObservationState.EXPIRED
    assert failed.status is DiscoveryStatus.FAILED
    assert failed.failure_reason == "collector unavailable"
    with pytest.raises(DiscoveryConflictError):
        expired.transition(ObservationState.SUCCESSFUL, now=NOW)


def test_failed_state_invariants_are_complete() -> None:
    with pytest.raises(DiscoveryValidationError, match="require"):
        make_observation(state=ObservationState.FAILED, status=DiscoveryStatus.FAILED)
    with pytest.raises(DiscoveryValidationError, match="only valid"):
        make_observation(failure_reason="unexpected")


def test_observation_rejects_duplicate_facts_and_backward_transition() -> None:
    address = make_observation().addresses[0]
    with pytest.raises(DiscoveryValidationError, match="duplicate facts"):
        make_observation(addresses=(address, address))
    with pytest.raises(DiscoveryValidationError, match="backwards"):
        make_observation().transition(
            ObservationState.SUCCESSFUL, now=NOW - timedelta(seconds=1)
        )


def test_discovery_result_reports_pagination() -> None:
    item = make_observation()
    result = DiscoveryResult((item,), total=2, limit=1, offset=0)
    assert result.has_more
    with pytest.raises(DiscoveryValidationError):
        DiscoveryResult((), total=-1, limit=1, offset=0)
    with pytest.raises(DiscoveryValidationError, match="invalid items"):
        DiscoveryResult((object(),), total=1, limit=1, offset=0)  # type: ignore[arg-type]


def test_discovery_enums_reject_magic_strings() -> None:
    assert ObservationSource("plugin") is ObservationSource.PLUGIN
    assert ObservationState("pending") is ObservationState.PENDING
    with pytest.raises(ValueError):
        ObservationSource("unknown-source")
