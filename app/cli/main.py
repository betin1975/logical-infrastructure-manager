"""Minimal argparse operator interface over composed LIM services."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TextIO

from app.bootstrap import BootstrapError, BootstrapRequest
from app.composition import (
    ApplicationServices,
    CompositionError,
    build_application_services,
)
from app.inventory import InventoryError, Server
from app.polling import PollingError
from app.ssh import SSHManagerError, SSHTrustStatus


class CLIError(RuntimeError):
    """Safe operator-facing command failure."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CLIError("invalid command arguments")


@dataclass(frozen=True, slots=True)
class _CommandContext:
    services: ApplicationServices
    stdout: TextIO


def build_parser() -> argparse.ArgumentParser:
    """Build the stable minimal CLI grammar without executing application code."""
    parser = _ArgumentParser(prog="lim", description="Logical Infrastructure Manager")
    commands = parser.add_subparsers(dest="command", required=True)

    server = commands.add_parser("server", help="manage inventory servers")
    server_commands = server.add_subparsers(dest="server_command", required=True)
    add = server_commands.add_parser("add", help="add an inventory server")
    add.add_argument("hostname")
    add.add_argument("address")
    add.add_argument("--user", required=True, help="administrative SSH username")
    server_commands.add_parser("list", help="list inventory servers")
    show = server_commands.add_parser("show", help="show one inventory server")
    show.add_argument("server")

    trust = commands.add_parser("trust", help="inspect or add SSH host trust")
    trust_commands = trust.add_subparsers(dest="trust_command", required=True)
    inspect = trust_commands.add_parser("inspect", help="inspect presented host trust")
    inspect.add_argument("server")
    add_trust = trust_commands.add_parser("add", help="trust a confirmed fingerprint")
    add_trust.add_argument("server")
    add_trust.add_argument("--fingerprint", required=True)

    bootstrap = commands.add_parser("bootstrap", help="bootstrap one Linux server")
    bootstrap.add_argument("server")
    poll = commands.add_parser("poll", help="poll one bootstrapped Linux server")
    poll.add_argument("server")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    services: ApplicationServices | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Parse and execute one command, returning a conventional process exit code."""
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    parser = build_parser()
    try:
        arguments = parser.parse_args(argv)
    except CLIError:
        parser.print_usage(errors)
        errors.write("lim: error: invalid command\n")
        return 2
    except SystemExit as exc:
        return int(exc.code)

    try:
        composed = services or build_application_services()
        return _dispatch(arguments, _CommandContext(composed, output))
    except CompositionError as exc:
        errors.write(f"error: initialization failed during {exc.stage}\n")
    except CLIError as exc:
        errors.write(f"error: {exc}\n")
    except InventoryError:
        errors.write("error: inventory operation failed\n")
    except SSHManagerError:
        errors.write("error: SSH operation failed\n")
    except BootstrapError:
        errors.write("error: bootstrap operation failed\n")
    except PollingError:
        errors.write("error: polling operation failed\n")
    return 1


def _dispatch(arguments: argparse.Namespace, context: _CommandContext) -> int:
    if arguments.command == "server":
        return _server_command(arguments, context)
    if arguments.command == "trust":
        return _trust_command(arguments, context)
    if arguments.command == "bootstrap":
        return _bootstrap_command(arguments.server, context)
    if arguments.command == "poll":
        return _poll_command(arguments.server, context)
    raise CLIError("unsupported command")


def _server_command(arguments: argparse.Namespace, context: _CommandContext) -> int:
    inventory = context.services.inventory_service
    if arguments.server_command == "add":
        server = inventory.register_server(
            arguments.hostname,
            arguments.address,
            labels={"ssh_user": arguments.user},
        )
        context.stdout.write(f"added {server.hostname} {server.uuid}\n")
        return 0
    if arguments.server_command == "list":
        result = inventory.list_servers()
        for server in result.items:
            context.stdout.write(
                f"{server.hostname}\t{server.primary_address}\t{server.status.value}\n"
            )
        return 0
    if arguments.server_command == "show":
        _write_server(inventory.resolve_server(arguments.server), context.stdout)
        return 0
    raise CLIError("unsupported server command")


def _trust_command(arguments: argparse.Namespace, context: _CommandContext) -> int:
    server = context.services.inventory_service.resolve_server(arguments.server)
    target = context.services.ssh_manager.create_target(
        server.management_address or server.primary_address,
        _admin_username(server),
        server_uuid=server.uuid,
    )
    if arguments.trust_command == "inspect":
        result = context.services.ssh_manager.inspect_host_key(target)
        context.stdout.write(f"trust {server.hostname} {result.status.value}\n")
        for key in result.presented_keys:
            context.stdout.write(f"fingerprint {key.algorithm} {key.fingerprint}\n")
        return 1 if result.status is SSHTrustStatus.UNREACHABLE else 0
    if arguments.trust_command == "add":
        result = context.services.ssh_manager.trust_host_key(
            target, arguments.fingerprint
        )
        context.stdout.write(f"trust {server.hostname} {result.status.value}\n")
        return 0 if result.status is SSHTrustStatus.TRUSTED else 1
    raise CLIError("unsupported trust command")


def _bootstrap_command(reference: str, context: _CommandContext) -> int:
    server = context.services.inventory_service.resolve_server(reference)
    result = context.services.bootstrap_service.bootstrap(
        BootstrapRequest(server.uuid, _admin_username(server))
    )
    context.stdout.write(
        f"bootstrap {server.hostname} "
        f"{'succeeded' if result.success else result.failure_type.value}\n"
    )
    return 0 if result.success else 1


def _poll_command(reference: str, context: _CommandContext) -> int:
    server = context.services.inventory_service.resolve_server(reference)
    result = context.services.polling_service.poll(server.uuid)
    context.stdout.write(
        f"poll {server.hostname} "
        f"{'succeeded' if result.succeeded else result.failure_type.value}\n"
    )
    return 0 if result.succeeded else 1


def _admin_username(server: Server) -> str:
    for label in server.labels:
        if label.key == "ssh_user":
            return label.value
    raise CLIError("server has no administrative SSH user")


def _write_server(server: Server, output: TextIO) -> None:
    output.write(f"uuid: {server.uuid}\n")
    output.write(f"hostname: {server.hostname}\n")
    output.write(f"primary_address: {server.primary_address}\n")
    if server.management_address:
        output.write(f"management_address: {server.management_address}\n")
    output.write(f"status: {server.status.value}\n")
    output.write(f"enabled: {str(server.enabled).lower()}\n")
    output.write(f"managed: {str(server.managed).lower()}\n")
    output.write(f"bootstrapped: {str(server.last_bootstrap_at is not None).lower()}\n")
