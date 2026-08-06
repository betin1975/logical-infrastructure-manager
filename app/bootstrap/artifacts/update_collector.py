#!/usr/bin/env python3
"""Root-owned, checksum-enforcing collector updater."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.request import Request, urlopen

DESTINATION = Path("/usr/local/libexec/lim/remote-health-json")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def main() -> int:
    if os.geteuid() != 0 or len(sys.argv) != 4:
        return 64

    version, expected_sha, url = sys.argv[1:]
    if not VERSION_PATTERN.fullmatch(version):
        return 64
    if not SHA_PATTERN.fullmatch(expected_sha):
        return 64
    if not url.startswith(("http://", "https://")):
        return 64

    request = Request(url, headers={"User-Agent": "LIM-Collector-Updater/1"})
    with urlopen(request, timeout=30) as response:
        payload = response.read(1024 * 1024 + 1)

    if len(payload) > 1024 * 1024:
        return 65
    if hashlib.sha256(payload).hexdigest() != expected_sha:
        return 65
    if f'COLLECTOR_VERSION = "{version}"'.encode() not in payload:
        return 65

    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=DESTINATION.parent,
        prefix=".remote-health-json.",
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)

    temporary.chmod(0o755)
    os.replace(temporary, DESTINATION)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
