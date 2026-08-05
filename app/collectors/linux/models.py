"""Immutable internal values used by the Linux collector."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

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


class CollectionIssueKind(StrEnum):
    """Safe categories for a degraded command or parser result."""

    MISSING_COMMAND = "missing_command"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    OUTPUT_LIMIT = "output_limit"
    COMMAND_FAILED = "command_failed"
    MALFORMED_OUTPUT = "malformed_output"


@dataclass(frozen=True, slots=True)
class CollectionIssue:
    """A command problem without stderr, stdout, or credentials."""

    command_name: str
    kind: CollectionIssueKind


@dataclass(frozen=True, slots=True)
class LinuxFacts:
    """Safely parsed facts before discovery-domain mapping."""

    hostname: str | None = None
    fqdn: str | None = None
    operating_system: DiscoveryOperatingSystem | None = None
    os_id: str | None = None
    os_id_like: str | None = None
    kernel_version: str | None = None
    architecture: str | None = None
    cpu: DiscoveryCPU | None = None
    memory: DiscoveryMemory | None = None
    disks: tuple[DiscoveryDisk, ...] = ()
    interfaces: tuple[DiscoveryInterface, ...] = ()
    addresses: tuple[DiscoveryAddress, ...] = ()
    services: tuple[ObservedService, ...] = ()
    containers: tuple[DiscoveryContainer, ...] = ()
    docker_metadata: tuple[tuple[str, str], ...] = ()
    mysql_metadata: tuple[tuple[str, str], ...] = ()
    redis_metadata: tuple[tuple[str, str], ...] = ()
    prometheus_metadata: tuple[tuple[str, str], ...] = ()
    freepbx_metadata: tuple[tuple[str, str], ...] = ()
    issues: tuple[CollectionIssue, ...] = field(default_factory=tuple)
