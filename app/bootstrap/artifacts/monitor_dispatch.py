#!/usr/bin/env python3
"""Restricted SSH dispatcher for LIM monitor operations."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys

COLLECTOR = "/usr/local/libexec/lim/remote-health-json"
UPDATER = "/usr/local/libexec/lim/update-collector"
LOG_COLLECTOR = "/usr/local/libexec/lim/collect-logs"


def main() -> int:
    original = os.environ.get("SSH_ORIGINAL_COMMAND", "").strip()
    if not original or original == "true":
        return subprocess.call((COLLECTOR,))

    try:
        command = shlex.split(original)
    except ValueError:
        return 64

    if command == ["collect-logs"]:
        return subprocess.call((LOG_COLLECTOR,))

    if len(command) == 4 and command[0] == "upgrade-collector":
        version, digest, url = command[1:]
        return subprocess.call(
            ("sudo", "-n", UPDATER, version, digest, url)
        )

    return 64


if __name__ == "__main__":
    raise SystemExit(main())
