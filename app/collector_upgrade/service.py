"""Bulk collector upgrade coordination."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode
from uuid import UUID

from .models import CollectorUpgradeResult, CollectorUpgradeStatus


class UpgradeInventory(Protocol):
    def list_servers(self, *, limit: int = 1000): ...


class UpgradeSSH(Protocol):
    def create_target(
        self,
        host: str,
        username: str,
        *,
        server_uuid: UUID,
    ): ...

    def run(self, request): ...


@dataclass(frozen=True, slots=True)
class CollectorRelease:
    version: str
    sha256: str
    artifact_url: str


class CollectorUpgradeService:
    """Upgrade eligible servers through the restricted monitor channel."""

    def __init__(
        self,
        inventory: UpgradeInventory,
        ssh_manager: UpgradeSSH,
        *,
        monitor_username: str,
        artifact_path: Path,
        artifact_base_url: str,
    ) -> None:
        self._inventory = inventory
        self._ssh = ssh_manager
        self._monitor_username = monitor_username
        self._artifact_path = artifact_path
        self._artifact_base_url = artifact_base_url.rstrip("/")

    def release(self, version: str) -> CollectorRelease:
        digest = sha256(self._artifact_path.read_bytes()).hexdigest()
        query = urlencode({"version": version, "sha256": digest})
        return CollectorRelease(
            version=version,
            sha256=digest,
            artifact_url=f"{self._artifact_base_url}/internal/collector?{query}",
        )

    def eligible_servers(self) -> tuple[object, ...]:
        page = self._inventory.list_servers(limit=1000)
        return tuple(
            server
            for server in page.items
            if getattr(server, "managed", False)
            and getattr(server, "enabled", False)
            and getattr(server, "last_bootstrap_at", None) is not None
        )

    def upgrade_all(
        self,
        *,
        version: str,
        concurrency: int = 10,
        dry_run: bool = False,
    ) -> tuple[CollectorUpgradeResult, ...]:
        release = self.release(version)
        servers = self.eligible_servers()
        if dry_run:
            return tuple(
                CollectorUpgradeResult(
                    server_uuid=server.uuid,
                    hostname=server.hostname,
                    address=str(
                        server.management_address or server.primary_address
                    ),
                    status=CollectorUpgradeStatus.PENDING,
                    message="Eligible for collector upgrade.",
                    target_version=version,
                )
                for server in servers
            )

        workers = max(1, min(concurrency, 32))
        results: list[CollectorUpgradeResult] = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self._upgrade_one, server, release): server
                for server in servers
            }
            for future in as_completed(futures):
                results.append(future.result())

        return tuple(sorted(results, key=lambda item: item.hostname))

    def _upgrade_one(
        self,
        server: object,
        release: CollectorRelease,
    ) -> CollectorUpgradeResult:
        # Import locally so the foundation remains decoupled from SSH internals.
        from app.ssh import SSHCommandRequest, SSHIdentity

        address = str(server.management_address or server.primary_address)
        target = self._ssh.create_target(
            address,
            self._monitor_username,
            server_uuid=server.uuid,
        )
        command = (
            "upgrade-collector",
            release.version,
            release.sha256,
            release.artifact_url,
        )
        request = SSHCommandRequest(
            target=target,
            command=command,
            identity=SSHIdentity.MONITOR,
            timeout_seconds=120,
        )

        try:
            result = self._ssh.run(request)
        except Exception as exc:
            return CollectorUpgradeResult(
                server_uuid=server.uuid,
                hostname=server.hostname,
                address=address,
                status=CollectorUpgradeStatus.FAILED,
                message=f"SSH upgrade failed: {type(exc).__name__}",
                target_version=release.version,
            )

        if not result.succeeded:
            return CollectorUpgradeResult(
                server_uuid=server.uuid,
                hostname=server.hostname,
                address=address,
                status=CollectorUpgradeStatus.FAILED,
                message="Remote updater returned a failure.",
                target_version=release.version,
            )

        return CollectorUpgradeResult(
            server_uuid=server.uuid,
            hostname=server.hostname,
            address=address,
            status=CollectorUpgradeStatus.SUCCEEDED,
            message="Collector upgraded successfully.",
            target_version=release.version,
        )
