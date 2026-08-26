"""Collect and classify remote health/security capability data."""

from __future__ import annotations

import json
from uuid import UUID

from app.ssh import SSHCommandRequest, SSHIdentity

from .models import HealthSecurityAssessment
from .store import HealthSecurityStore


class HealthSecurityService:
    def __init__(
        self,
        inventory,
        ssh_manager,
        *,
        monitor_username: str,
        store: HealthSecurityStore,
        timeout_seconds: float = 60,
    ) -> None:
        self._inventory = inventory
        self._ssh = ssh_manager
        self._monitor_username = monitor_username
        self._store = store
        self._timeout_seconds = timeout_seconds

    def collect(self, server_uuid: UUID) -> HealthSecurityAssessment:
        server = self._inventory.resolve_server(server_uuid)
        address = server.management_address or server.primary_address
        target = self._ssh.create_target(
            address,
            self._monitor_username,
            server_uuid=server.uuid,
        )

        request = SSHCommandRequest(
            target=target,
            identity=SSHIdentity.MONITOR,
            command=("health-security",),
            timeout_seconds=self._timeout_seconds,
        )
        result = self._ssh.run(request)

        if result.exit_code != 0:
            raise RuntimeError("health/security collection failed")

        try:
            document = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("health/security document is invalid") from exc

        assessment = self._parse(server.uuid, server.hostname, document)
        self._store.save(assessment)
        return assessment

    def latest(self, server_uuid: UUID) -> HealthSecurityAssessment | None:
        return self._store.latest(server_uuid)

    @staticmethod
    def _parse(
        server_uuid: UUID,
        hostname: str,
        document: dict[str, object],
    ) -> HealthSecurityAssessment:
        updates = dict(document.get("updates", {}))
        systemd = dict(document.get("systemd", {}))
        logs = dict(document.get("logs", {}))

        security_updates = int(updates.get("security_total", 0))
        failed_units = tuple(
            str(item.get("unit", ""))
            for item in systemd.get("failed_units", ())
            if item.get("unit")
        )
        critical_logs = int(logs.get("critical_count", 0))
        error_logs = int(logs.get("error_count", 0))
        security_logs = int(logs.get("security_count", 0))
        reboot_required = bool(updates.get("reboot_required", False))

        if critical_logs > 0 or security_updates > 0:
            overall = "critical"
        elif (
            error_logs > 0
            or failed_units
            or reboot_required
            or bool(updates.get("apt_lists_stale"))
        ):
            overall = "warning"
        else:
            overall = "healthy"

        return HealthSecurityAssessment(
            server_uuid=server_uuid,
            hostname=hostname,
            generated_at=str(document.get("generated_at", "")),
            collector_version=str(document.get("collector_version", "")),
            overall_status=overall,
            available_updates=int(updates.get("available_total", 0)),
            security_updates=security_updates,
            security_packages=tuple(updates.get("security_packages", ())),
            attention_security_packages=tuple(
                updates.get("attention_security_packages", ())
            ),
            reboot_required=reboot_required,
            apt_lists_age_seconds=updates.get("apt_lists_age_seconds"),
            apt_lists_stale=updates.get("apt_lists_stale"),
            failed_units=failed_units,
            critical_logs=critical_logs,
            error_logs=error_logs,
            warning_logs=int(logs.get("warning_count", 0)),
            security_logs=security_logs,
            findings=tuple(logs.get("findings", ())),
        )
