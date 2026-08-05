"""Fixed, read-only command catalog for Linux discovery."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LinuxCommand(StrEnum):
    """Stable command identifiers used for parsing and safe logging."""

    HOSTNAMECTL = "hostnamectl"
    HOSTNAME = "hostname"
    FQDN = "fqdn"
    OS_RELEASE = "os_release"
    KERNEL = "kernel"
    ARCHITECTURE = "architecture"
    NPROC = "nproc"
    LSCPU = "lscpu"
    MEMORY = "memory"
    FILESYSTEM = "filesystem"
    BLOCK_DEVICES = "block_devices"
    INTERFACES = "interfaces"
    LISTENING = "listening"
    RUNNING_SERVICES = "running_services"
    DOCKER_VERSION = "docker_version"
    DOCKER_CONTAINERS = "docker_containers"
    MYSQL = "mysql"
    MARIADB = "mariadb"
    REDIS = "redis"
    PROMETHEUS = "prometheus"
    ASTERISK = "asterisk"
    FREEPBX = "freepbx"


@dataclass(frozen=True, slots=True)
class LinuxCommandSpec:
    """One validated command request and its collection policy."""

    name: LinuxCommand
    argv: tuple[str, ...]
    timeout_seconds: float
    required: bool = False


COMMANDS: tuple[LinuxCommandSpec, ...] = (
    LinuxCommandSpec(LinuxCommand.HOSTNAMECTL, ("hostnamectl", "--static"), 5),
    LinuxCommandSpec(LinuxCommand.FQDN, ("hostname", "--fqdn"), 5),
    LinuxCommandSpec(LinuxCommand.OS_RELEASE, ("cat", "/etc/os-release"), 5, True),
    LinuxCommandSpec(LinuxCommand.KERNEL, ("uname", "-r"), 5, True),
    LinuxCommandSpec(LinuxCommand.ARCHITECTURE, ("uname", "-m"), 5, True),
    LinuxCommandSpec(LinuxCommand.NPROC, ("nproc",), 5, True),
    LinuxCommandSpec(LinuxCommand.LSCPU, ("lscpu",), 10),
    LinuxCommandSpec(LinuxCommand.MEMORY, ("free", "-b"), 5, True),
    LinuxCommandSpec(LinuxCommand.FILESYSTEM, ("df", "-P", "-B1"), 15, True),
    LinuxCommandSpec(LinuxCommand.BLOCK_DEVICES, ("lsblk", "-J"), 15, True),
    LinuxCommandSpec(LinuxCommand.INTERFACES, ("ip", "-j", "address"), 10, True),
    LinuxCommandSpec(LinuxCommand.LISTENING, ("ss", "-tuln"), 10, True),
    LinuxCommandSpec(
        LinuxCommand.RUNNING_SERVICES,
        (
            "systemctl",
            "list-units",
            "--type=service",
            "--state=running",
            "--no-pager",
        ),
        15,
    ),
    LinuxCommandSpec(
        LinuxCommand.DOCKER_VERSION,
        ("docker", "version", "--format", "{{json .}}"),
        10,
    ),
    LinuxCommandSpec(
        LinuxCommand.DOCKER_CONTAINERS,
        ("docker", "ps", "--format", "{{json .}}"),
        15,
    ),
    LinuxCommandSpec(LinuxCommand.MYSQL, ("systemctl", "is-active", "mysql"), 5),
    LinuxCommandSpec(LinuxCommand.MARIADB, ("systemctl", "is-active", "mariadb"), 5),
    LinuxCommandSpec(LinuxCommand.REDIS, ("systemctl", "is-active", "redis"), 5),
    LinuxCommandSpec(
        LinuxCommand.PROMETHEUS,
        ("systemctl", "is-active", "prometheus"),
        5,
    ),
    LinuxCommandSpec(LinuxCommand.ASTERISK, ("systemctl", "is-active", "asterisk"), 5),
    LinuxCommandSpec(LinuxCommand.FREEPBX, ("fwconsole", "--version"), 10),
)

HOSTNAME_FALLBACK = LinuxCommandSpec(
    LinuxCommand.HOSTNAME,
    ("hostname",),
    5,
    True,
)

COMMAND_BY_NAME = {spec.name: spec for spec in (*COMMANDS, HOSTNAME_FALLBACK)}
