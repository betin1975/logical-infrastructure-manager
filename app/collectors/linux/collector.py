"""Linux collection orchestration through LIM's sole SSH boundary."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.discovery import (
    DiscoveryKernel,
    DiscoveryMetadata,
    DiscoveryObservation,
    DiscoveryStatus,
    ObservationSource,
)
from app.discovery.exceptions import DiscoveryValidationError
from app.inventory import Server
from app.ssh import (
    SSHCommandRequest,
    SSHConnectionTarget,
    SSHIdentity,
    SSHManager,
    SSHManagerError,
)

from .commands import COMMANDS, HOSTNAME_FALLBACK, LinuxCommand, LinuxCommandSpec
from .exceptions import LinuxCommandError, LinuxParserError
from .models import CollectionIssue, CollectionIssueKind, LinuxFacts
from .parser import (
    active_product,
    freepbx_product,
    merge_unique,
    parse_cpu,
    parse_df,
    parse_docker_containers,
    parse_docker_version,
    parse_hostname,
    parse_ip_address,
    parse_listening_services,
    parse_lsblk,
    parse_memory,
    parse_os_release,
    parse_systemd_services,
    parse_text_line,
)
from .validation import classify_failure, usable_output, validate_server

COLLECTOR_VERSION = "1.0.0"
SUPPORTED_DISTRIBUTIONS = frozenset({"ubuntu", "debian", "rocky", "almalinux"})


class CollectorLogger(Protocol):
    """Narrow structured logger accepted by the collector."""

    def bind(self, **context: Any) -> CollectorLogger: ...

    def info(self, message: object, *args: object, **kwargs: object) -> None: ...

    def warning(self, message: object, *args: object, **kwargs: object) -> None: ...


class LinuxCollector:
    """Collect immutable Linux observations without persistence or scheduling."""

    def __init__(
        self,
        ssh_manager: SSHManager,
        logger: CollectorLogger,
        *,
        username: str,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        if not isinstance(username, str) or not username.strip():
            from .exceptions import LinuxCollectorValidationError

            raise LinuxCollectorValidationError("collector SSH username is required")
        self._ssh = ssh_manager
        self._logger = logger
        self._username = username.strip()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._uuid_factory = uuid_factory or uuid4

    def collect(self, server: Server) -> DiscoveryObservation:
        """Collect one host and return an unpersisted discovery observation."""
        server = validate_server(server)
        started_at = self._clock()
        started_tick = self._monotonic()
        logger = self._logger.bind(
            server_id=str(server.uuid),
            server_name=server.hostname,
            operation="collect_linux",
        )
        logger.info("Linux collection started collector_version=%s", COLLECTOR_VERSION)
        target = self._ssh.create_target(
            server.management_address or server.primary_address,
            self._username,
            server_uuid=server.uuid,
        )
        outputs: dict[LinuxCommand, str] = {}
        issues: list[CollectionIssue] = []
        required_failures: set[LinuxCommand] = set()

        for spec in COMMANDS:
            self._execute(spec, target, logger, outputs, issues, required_failures)

        if LinuxCommand.HOSTNAMECTL not in outputs:
            self._execute(
                HOSTNAME_FALLBACK,
                target,
                logger,
                outputs,
                issues,
                required_failures,
            )

        facts = self._parse(outputs, issues, required_failures, logger)
        elapsed_ms = max(0, round((self._monotonic() - started_tick) * 1000))
        finished_at = self._clock()
        observation = self._observation(
            server,
            facts,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=elapsed_ms,
            is_partial=bool(required_failures),
        )
        logger.info(
            "Linux collection completed collector_version=%s duration_ms=%d "
            "status=%s issue_count=%d",
            COLLECTOR_VERSION,
            elapsed_ms,
            "partial" if observation.status is DiscoveryStatus.PARTIAL else "complete",
            len(facts.issues),
        )
        return observation

    def _execute(
        self,
        spec: LinuxCommandSpec,
        target: SSHConnectionTarget,
        logger: CollectorLogger,
        outputs: dict[LinuxCommand, str],
        issues: list[CollectionIssue],
        required_failures: set[LinuxCommand],
    ) -> None:
        request = SSHCommandRequest(
            target=target,
            command=spec.argv,
            identity=SSHIdentity.MONITOR,
            timeout_seconds=spec.timeout_seconds,
        )
        try:
            result = self._ssh.run(request)
        except SSHManagerError:
            kind = CollectionIssueKind.COMMAND_FAILED
            issues.append(CollectionIssue(spec.name.value, kind))
            if spec.required:
                required_failures.add(spec.name)
            logger.warning(
                "Linux command unavailable command=%s failure=%s collector_version=%s",
                spec.name.value,
                kind.value,
                COLLECTOR_VERSION,
            )
            return
        logger.info(
            "Linux command completed command=%s duration=%.3f exit_code=%s "
            "collector_version=%s attempts=%d",
            spec.name.value,
            result.duration_seconds,
            result.exit_code,
            COLLECTOR_VERSION,
            result.attempts,
        )
        try:
            outputs[spec.name] = usable_output(result, spec)
        except LinuxCommandError:
            kind = classify_failure(result)
            issues.append(CollectionIssue(spec.name.value, kind))
            if spec.required:
                required_failures.add(spec.name)
            logger.warning(
                "Linux command unavailable command=%s failure=%s collector_version=%s",
                spec.name.value,
                kind.value,
                COLLECTOR_VERSION,
            )

    def _parse(
        self,
        outputs: Mapping[LinuxCommand, str],
        issues: list[CollectionIssue],
        required_failures: set[LinuxCommand],
        logger: CollectorLogger,
    ) -> LinuxFacts:
        values: dict[str, Any] = {}

        hostname_output = outputs.get(LinuxCommand.HOSTNAMECTL) or outputs.get(
            LinuxCommand.HOSTNAME
        )
        values["hostname"] = parse_hostname(hostname_output or "")
        values["fqdn"] = parse_hostname(outputs.get(LinuxCommand.FQDN, ""))
        if values["hostname"] is None:
            required_failures.add(LinuxCommand.HOSTNAME)

        self._parse_one(
            LinuxCommand.OS_RELEASE,
            outputs,
            issues,
            required_failures,
            logger,
            lambda value: values.update(
                zip(
                    ("operating_system", "os_id", "os_id_like"),
                    parse_os_release(value),
                    strict=True,
                )
            ),
        )
        values["kernel_version"] = parse_text_line(
            outputs.get(LinuxCommand.KERNEL, ""), 255
        )
        values["architecture"] = parse_text_line(
            outputs.get(LinuxCommand.ARCHITECTURE, ""), 64
        )
        for command, key in (
            (LinuxCommand.KERNEL, "kernel_version"),
            (LinuxCommand.ARCHITECTURE, "architecture"),
        ):
            if command in outputs and values[key] is None:
                self._parser_issue(command, issues, required_failures, logger)
        try:
            values["cpu"] = parse_cpu(
                outputs.get(LinuxCommand.NPROC), outputs.get(LinuxCommand.LSCPU)
            )
        except (DiscoveryValidationError, LinuxParserError):
            self._parser_issue(LinuxCommand.LSCPU, issues, required_failures, logger)
        if values.get("cpu") is None and LinuxCommand.NPROC in outputs:
            self._parser_issue(LinuxCommand.NPROC, issues, required_failures, logger)

        self._parse_assign(
            LinuxCommand.MEMORY,
            "memory",
            parse_memory,
            outputs,
            values,
            issues,
            required_failures,
            logger,
        )
        disks: list[Any] = []
        for command, parser in (
            (LinuxCommand.FILESYSTEM, parse_df),
            (LinuxCommand.BLOCK_DEVICES, parse_lsblk),
        ):
            self._parse_one(
                command,
                outputs,
                issues,
                required_failures,
                logger,
                lambda value, parser=parser: disks.extend(parser(value)),
            )
        values["disks"] = merge_unique(
            disks, lambda item: (item.name, item.mount_point)
        )

        def parse_interfaces(value: str) -> None:
            values["interfaces"], values["addresses"] = parse_ip_address(value)

        self._parse_one(
            LinuxCommand.INTERFACES,
            outputs,
            issues,
            required_failures,
            logger,
            parse_interfaces,
        )
        services: list[Any] = []
        for command, parser in (
            (LinuxCommand.LISTENING, parse_listening_services),
            (LinuxCommand.RUNNING_SERVICES, parse_systemd_services),
        ):
            self._parse_one(
                command,
                outputs,
                issues,
                required_failures,
                logger,
                lambda value, parser=parser: services.extend(parser(value)),
            )
        values["services"] = merge_unique(services, lambda item: (item.name, item.port))
        self._parse_assign(
            LinuxCommand.DOCKER_CONTAINERS,
            "containers",
            parse_docker_containers,
            outputs,
            values,
            issues,
            required_failures,
            logger,
        )
        self._parse_assign(
            LinuxCommand.DOCKER_VERSION,
            "docker_metadata",
            parse_docker_version,
            outputs,
            values,
            issues,
            required_failures,
            logger,
        )
        values["mysql_metadata"] = self._mysql_metadata(outputs)
        values["redis_metadata"] = self._active_metadata(
            "redis", LinuxCommand.REDIS, outputs
        )
        values["prometheus_metadata"] = self._active_metadata(
            "prometheus", LinuxCommand.PROMETHEUS, outputs
        )
        values["freepbx_metadata"] = freepbx_product(
            outputs.get(LinuxCommand.FREEPBX), outputs.get(LinuxCommand.ASTERISK)
        )
        values["issues"] = tuple(issues)
        return LinuxFacts(**values)

    def _parse_assign(
        self,
        command: LinuxCommand,
        key: str,
        parser: Callable[[str], Any],
        outputs: Mapping[LinuxCommand, str],
        values: dict[str, Any],
        issues: list[CollectionIssue],
        required_failures: set[LinuxCommand],
        logger: CollectorLogger,
    ) -> None:
        self._parse_one(
            command,
            outputs,
            issues,
            required_failures,
            logger,
            lambda output: values.__setitem__(key, parser(output)),
        )

    def _parse_one(
        self,
        command: LinuxCommand,
        outputs: Mapping[LinuxCommand, str],
        issues: list[CollectionIssue],
        required_failures: set[LinuxCommand],
        logger: CollectorLogger,
        action: Callable[[str], None],
    ) -> None:
        output = outputs.get(command)
        if output is None:
            return
        try:
            action(output)
        except (DiscoveryValidationError, LinuxParserError, TypeError, ValueError):
            self._parser_issue(command, issues, required_failures, logger)

    @staticmethod
    def _parser_issue(
        command: LinuxCommand,
        issues: list[CollectionIssue],
        required_failures: set[LinuxCommand],
        logger: CollectorLogger,
    ) -> None:
        issues.append(
            CollectionIssue(command.value, CollectionIssueKind.MALFORMED_OUTPUT)
        )
        spec = next((item for item in COMMANDS if item.name is command), None)
        if spec and spec.required:
            required_failures.add(command)
        logger.warning(
            "Linux command output rejected command=%s collector_version=%s",
            command.value,
            COLLECTOR_VERSION,
        )

    @staticmethod
    def _active_metadata(
        product: str,
        command: LinuxCommand,
        outputs: Mapping[LinuxCommand, str],
    ) -> tuple[tuple[str, str], ...]:
        output = outputs.get(command)
        return active_product(product, output) if output is not None else ()

    @staticmethod
    def _mysql_metadata(
        outputs: Mapping[LinuxCommand, str],
    ) -> tuple[tuple[str, str], ...]:
        mysql = outputs.get(LinuxCommand.MYSQL)
        if mysql is not None and (metadata := active_product("mysql", mysql)):
            return metadata
        mariadb = outputs.get(LinuxCommand.MARIADB)
        return active_product("mariadb", mariadb) if mariadb is not None else ()

    def _observation(
        self,
        server: Server,
        facts: LinuxFacts,
        *,
        started_at: datetime,
        finished_at: datetime,
        duration_ms: int,
        is_partial: bool,
    ) -> DiscoveryObservation:
        hostname = facts.hostname or server.hostname
        os_id = (facts.os_id or "").lower()
        supported = os_id in SUPPORTED_DISTRIBUTIONS
        raw_metadata = DiscoveryMetadata(
            (
                ("commands_degraded", str(len(facts.issues))),
                ("distribution_supported", str(supported).lower()),
                ("os_id", os_id or "unknown"),
            )
        )
        return DiscoveryObservation(
            uuid=self._uuid_factory(),
            server_uuid=server.uuid,
            source=ObservationSource.SSH,
            discovered_at=started_at,
            collection_duration_ms=duration_ms,
            collector_version=COLLECTOR_VERSION,
            hostname=hostname,
            fqdn=facts.fqdn,
            operating_system=facts.operating_system,
            kernel=(
                DiscoveryKernel("Linux", facts.kernel_version)
                if facts.kernel_version
                else None
            ),
            architecture=facts.architecture,
            cpu=facts.cpu,
            memory=facts.memory,
            disks=facts.disks,
            interfaces=facts.interfaces,
            addresses=facts.addresses,
            services=facts.services,
            containers=facts.containers,
            docker=DiscoveryMetadata(facts.docker_metadata),
            redis=DiscoveryMetadata(facts.redis_metadata),
            mysql=DiscoveryMetadata(facts.mysql_metadata),
            freepbx=DiscoveryMetadata(facts.freepbx_metadata),
            prometheus=DiscoveryMetadata(facts.prometheus_metadata),
            raw_metadata=raw_metadata,
            status=DiscoveryStatus.PARTIAL if is_partial else DiscoveryStatus.UNKNOWN,
            created_at=finished_at,
            updated_at=finished_at,
        )
