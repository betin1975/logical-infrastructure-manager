"""Trust-boundary validation for bounded Linux command output."""

from __future__ import annotations

from app.inventory import Server
from app.ssh import SSHCommandResult, SSHFailureType

from .commands import LinuxCommandSpec
from .exceptions import LinuxCollectorValidationError, LinuxCommandError
from .models import CollectionIssueKind

MAX_PARSE_CHARACTERS = 1_048_576


def validate_server(server: Server) -> Server:
    """Require a validated immutable inventory server."""
    if not isinstance(server, Server):
        raise LinuxCollectorValidationError("collector requires an inventory Server")
    return server


def usable_output(result: SSHCommandResult, spec: LinuxCommandSpec) -> str:
    """Return bounded successful stdout or raise a safe classified error."""
    if not isinstance(result, SSHCommandResult):
        raise LinuxCommandError(f"{spec.name.value} returned an invalid SSH result")
    if result.timed_out or result.failure_type is SSHFailureType.COMMAND_TIMEOUT:
        raise LinuxCommandError(f"{spec.name.value} timed out")
    if result.stdout_truncated or len(result.stdout) > MAX_PARSE_CHARACTERS:
        raise LinuxCommandError(f"{spec.name.value} exceeded the output limit")
    if not result.succeeded:
        raise LinuxCommandError(f"{spec.name.value} did not complete successfully")
    return result.stdout


def classify_failure(result: SSHCommandResult) -> CollectionIssueKind:
    """Classify a failed result without inspecting or exposing stderr."""
    if result.timed_out or result.failure_type is SSHFailureType.COMMAND_TIMEOUT:
        return CollectionIssueKind.TIMEOUT
    if result.stdout_truncated or result.stderr_truncated:
        return CollectionIssueKind.OUTPUT_LIMIT
    if result.exit_code == 127:
        return CollectionIssueKind.MISSING_COMMAND
    if result.exit_code == 126:
        return CollectionIssueKind.PERMISSION_DENIED
    return CollectionIssueKind.COMMAND_FAILED
