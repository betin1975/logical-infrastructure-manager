"""Bulk collector upgrade coordination."""

from __future__ import annotations

import json
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


class UpgradePolling(Protocol):
    def poll(self, server_uuid: UUID): ...


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
        polling_service: UpgradePolling,
        *,
        monitor_username: str,
        artifact_path: Path,
    ) -> None:
        self._inventory = inventory
        self._ssh = ssh_manager
        self._polling = polling_service
        self._monitor_username = monitor_username
        self._artifact_path = artifact_path

    def release(
        self,
        version: str,
        *,
        artifact_base_url: str,
    ) -> CollectorRelease:
        digest = sha256(self._artifact_path.read_bytes()).hexdigest()
        query = urlencode({"version": version, "sha256": digest})
        return CollectorRelease(
            version=version,
            sha256=digest,
            artifact_url=(
                f"{artifact_base_url.rstrip('/')}/internal/collector?{query}"
            ),
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
        artifact_base_url: str,
        server_uuids: set[UUID] | None = None,
    ) -> tuple[CollectorUpgradeResult, ...]:
        release = self.release(
            version,
            artifact_base_url=artifact_base_url,
        )
        servers = self.eligible_servers()
        if server_uuids is not None:
            servers = tuple(
                server for server in servers if server.uuid in server_uuids
            )
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
        from app.ssh import SSHCommandRequest, SSHIdentity

        address = str(server.management_address or server.primary_address)
        target = self._ssh.create_target(
            address,
            self._monitor_username,
            server_uuid=server.uuid,
        )

        previous_version = self._read_version(target)
        if previous_version == release.version:
            poll_message = self._poll_after_upgrade(server.uuid)
            return CollectorUpgradeResult(
                server_uuid=server.uuid,
                hostname=server.hostname,
                address=address,
                status=CollectorUpgradeStatus.ALREADY_CURRENT,
                message=f"Already current. {poll_message}",
                previous_version=previous_version,
                target_version=release.version,
            )

        request = SSHCommandRequest(
            target=target,
            command=(
                "upgrade-collector",
                release.version,
                release.sha256,
                release.artifact_url,
            ),
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
                previous_version=previous_version,
                target_version=release.version,
            )

        if not result.succeeded:
            return CollectorUpgradeResult(
                server_uuid=server.uuid,
                hostname=server.hostname,
                address=address,
                status=CollectorUpgradeStatus.FAILED,
                message="Remote updater returned a failure.",
                previous_version=previous_version,
                target_version=release.version,
            )

        installed_version = self._read_version(target)
        if installed_version != release.version:
            return CollectorUpgradeResult(
                server_uuid=server.uuid,
                hostname=server.hostname,
                address=address,
                status=CollectorUpgradeStatus.FAILED,
                message="Upgrade succeeded, but version verification failed.",
                previous_version=previous_version,
                target_version=release.version,
            )

        poll_message = self._poll_after_upgrade(server.uuid)
        return CollectorUpgradeResult(
            server_uuid=server.uuid,
            hostname=server.hostname,
            address=address,
            status=CollectorUpgradeStatus.SUCCEEDED,
            message=f"Collector upgraded and verified. {poll_message}",
            previous_version=previous_version,
            target_version=release.version,
        )

    def _read_version(self, target: object) -> str | None:
        from app.ssh import SSHCommandRequest, SSHIdentity

        request = SSHCommandRequest(
            target=target,
            command=("true",),
            identity=SSHIdentity.MONITOR,
            timeout_seconds=30,
        )
        try:
            result = self._ssh.run(request)
        except Exception:
            return None
        if not result.succeeded:
            return None
        try:
            document = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            return None
        version = document.get("collector_version")
        return version.strip() if isinstance(version, str) else None

    def _poll_after_upgrade(self, server_uuid: UUID) -> str:
        try:
            result = self._polling.poll(server_uuid)
        except Exception as exc:
            return f"Post-upgrade poll failed: {type(exc).__name__}."
        status = getattr(getattr(result, "status", None), "value", None)
        if status == "succeeded":
            return "Post-upgrade poll succeeded."
        return "Post-upgrade poll did not succeed."
