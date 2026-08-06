"""Log collection and analysis application service."""

from __future__ import annotations

import json
from typing import Protocol
from uuid import UUID

from .models import LogAnalysisResult, LogSeverity
from .rules import analyze_events


class LogInventory(Protocol):
    def resolve_server(self, identifier): ...


class LogSSH(Protocol):
    def create_target(self, host, username, *, server_uuid: UUID): ...
    def run(self, request): ...


class LogAnalysisService:
    def __init__(
        self,
        inventory,
        ssh_manager,
        *,
        monitor_username: str,
        timeout_seconds: float = 45,
    ) -> None:
        self._inventory = inventory
        self._ssh = ssh_manager
        self._monitor_username = monitor_username
        self._timeout_seconds = timeout_seconds

    def analyze(self, server_uuid: UUID) -> LogAnalysisResult:
        from app.ssh import SSHCommandRequest, SSHIdentity

        server = self._inventory.resolve_server(server_uuid)
        address = server.management_address or server.primary_address
        target = self._ssh.create_target(
            address, self._monitor_username, server_uuid=server.uuid
        )
        request = SSHCommandRequest(
            target=target,
            command=("collect-logs",),
            identity=SSHIdentity.MONITOR,
            timeout_seconds=self._timeout_seconds,
        )
        result = self._ssh.run(request)
        if not result.succeeded:
            raise RuntimeError("remote log collection failed")
        document = json.loads(result.stdout)
        events = document.get("events", ())
        if not isinstance(events, list):
            raise RuntimeError("remote log document is invalid")
        findings = analyze_events(events)
        if any(item.severity is LogSeverity.CRITICAL for item in findings):
            status = LogSeverity.CRITICAL
        elif findings:
            status = LogSeverity.WARNING
        else:
            status = LogSeverity.INFO
        summary = (
            "No warning or critical patterns found."
            if not findings
            else f"{len(findings)} notable finding(s) detected."
        )
        return LogAnalysisResult(
            server_uuid=server.uuid,
            hostname=server.hostname,
            status=status,
            event_count=len(events),
            findings=findings,
            summary=summary,
        )
