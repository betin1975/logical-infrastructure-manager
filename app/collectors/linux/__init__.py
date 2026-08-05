"""Public API for the read-only Linux collector."""

from .collector import COLLECTOR_VERSION, SUPPORTED_DISTRIBUTIONS, LinuxCollector
from .commands import COMMANDS, HOSTNAME_FALLBACK, LinuxCommand, LinuxCommandSpec
from .exceptions import (
    LinuxCollectorError,
    LinuxCollectorValidationError,
    LinuxCommandError,
    LinuxParserError,
)
from .forced_command import ForcedCommandLinuxCollector
from .models import CollectionIssue, CollectionIssueKind, LinuxFacts

__all__ = [
    "COLLECTOR_VERSION",
    "COMMANDS",
    "HOSTNAME_FALLBACK",
    "SUPPORTED_DISTRIBUTIONS",
    "CollectionIssue",
    "CollectionIssueKind",
    "ForcedCommandLinuxCollector",
    "LinuxCollector",
    "LinuxCollectorError",
    "LinuxCollectorValidationError",
    "LinuxCommand",
    "LinuxCommandError",
    "LinuxCommandSpec",
    "LinuxFacts",
    "LinuxParserError",
]
