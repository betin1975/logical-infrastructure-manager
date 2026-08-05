"""Atomic application-owned OpenSSH host trust management."""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import tempfile
import threading
from contextlib import suppress
from pathlib import Path

from .command import OpenSSHProcessRunner
from .exceptions import (
    SSHFingerprintMismatchError,
    SSHTrustStoreError,
)
from .models import (
    SSHConnectionTarget,
    SSHHostKey,
    SSHTrustResult,
    SSHTrustStatus,
)

_MAX_TRUST_STORE_BYTES = 1024 * 1024


class SSHTrustStore:
    """Inspect and explicitly mutate LIM's isolated ``known_hosts`` file."""

    def __init__(
        self,
        known_hosts: Path,
        keyscan_executable: Path,
        runner: OpenSSHProcessRunner,
        *,
        scan_timeout_seconds: float,
        max_scan_bytes: int = 65536,
    ) -> None:
        self.path = known_hosts
        self._keyscan = keyscan_executable
        self._runner = runner
        self._scan_timeout = scan_timeout_seconds
        self._max_scan_bytes = max_scan_bytes
        self._lock = threading.RLock()

    def initialize(self) -> None:
        """Create or validate a restrictive, writable, non-symlink trust file."""
        with self._lock:
            parent = self.path.parent
            if parent.is_symlink() or not parent.is_dir():
                raise SSHTrustStoreError("SSH trust-store directory is unsafe")
            if stat.S_IMODE(parent.stat().st_mode) & 0o022:
                raise SSHTrustStoreError(
                    "SSH trust-store directory permits group or world writes"
                )
            if not os.access(parent, os.W_OK | os.X_OK):
                raise SSHTrustStoreError("SSH trust-store directory is not writable")
            if self.path.exists() or self.path.is_symlink():
                try:
                    metadata = self.path.lstat()
                except OSError as exc:
                    raise SSHTrustStoreError("SSH trust store is inaccessible") from exc
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                    raise SSHTrustStoreError("SSH trust store must be a regular file")
                if not os.access(self.path, os.W_OK):
                    raise SSHTrustStoreError("SSH trust store is not writable")
                os.chmod(self.path, 0o600)
            else:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                os.close(descriptor)

    def inspect(self, target: SSHConnectionTarget) -> SSHTrustResult:
        """Scan and compare keys without modifying trust."""
        presented = self.scan(target)
        trusted = self.list_for_host(target)
        if not presented:
            status = SSHTrustStatus.UNREACHABLE
        elif not trusted:
            status = SSHTrustStatus.UNKNOWN
        else:
            presented_values = {(key.algorithm, key.public_key) for key in presented}
            trusted_values = {(key.algorithm, key.public_key) for key in trusted}
            status = (
                SSHTrustStatus.TRUSTED
                if trusted_values.issubset(presented_values)
                else SSHTrustStatus.CHANGED
            )
        return SSHTrustResult(target, status, presented, trusted)

    def scan(self, target: SSHConnectionTarget) -> tuple[SSHHostKey, ...]:
        """Return currently presented public host keys via ``ssh-keyscan``."""
        outcome = self._runner.run(
            (
                str(self._keyscan),
                "-T",
                str(max(1, int(self._scan_timeout))),
                "-p",
                str(target.port),
                target.host,
            ),
            timeout_seconds=self._scan_timeout,
            max_stdout_bytes=self._max_scan_bytes,
            max_stderr_bytes=4096,
        )
        if outcome.timed_out:
            raise SSHTrustStoreError("SSH host-key scan timed out")
        if outcome.stdout_truncated:
            raise SSHTrustStoreError("SSH host-key scan exceeded its output limit")
        if outcome.exit_code not in (0, 1):
            raise SSHTrustStoreError("SSH host-key scan failed")
        try:
            text = outcome.stdout.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise SSHTrustStoreError(
                "SSH host-key scan returned malformed data"
            ) from exc
        return _parse_keys(text, target)

    def list_for_host(self, target: SSHConnectionTarget) -> tuple[SSHHostKey, ...]:
        """List application-trusted keys for one exact host token."""
        token = _host_token(target)
        self._validate_paths()
        try:
            content = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise SSHTrustStoreError("SSH trust store could not be read") from exc
        return tuple(
            key for key in _parse_known_hosts(content) if key.host_token == token
        )

    def trust(
        self, target: SSHConnectionTarget, expected_fingerprint: str
    ) -> SSHTrustResult:
        """Explicitly trust one currently presented key for an unknown host."""
        with self._lock:
            original = self._read_lines()
            result = self.inspect(target)
            if result.status is not SSHTrustStatus.UNKNOWN:
                raise SSHTrustStoreError("new trust requires an unknown host")
            selected = _select_fingerprint(result.presented_keys, expected_fingerprint)
            lines = self._lines_without_host(target)
            lines.append(_render_key(selected))
            self._write_atomic(lines)
            confirmed = self.inspect(target)
            if confirmed.status is not SSHTrustStatus.TRUSTED:
                self._write_atomic(original)
                raise SSHTrustStoreError(
                    "presented host key changed during trust update"
                )
            return confirmed

    def replace(
        self, target: SSHConnectionTarget, expected_fingerprint: str
    ) -> SSHTrustResult:
        """Explicitly replace changed trust after fingerprint confirmation."""
        with self._lock:
            original = self._read_lines()
            result = self.inspect(target)
            if result.status is not SSHTrustStatus.CHANGED:
                raise SSHTrustStoreError("trust replacement requires a changed key")
            selected = _select_fingerprint(result.presented_keys, expected_fingerprint)
            lines = self._lines_without_host(target)
            lines.append(_render_key(selected))
            self._write_atomic(lines)
            confirmed = self.inspect(target)
            if confirmed.status is not SSHTrustStatus.TRUSTED:
                self._write_atomic(original)
                raise SSHTrustStoreError(
                    "presented host key changed during trust update"
                )
            return confirmed

    def remove(self, target: SSHConnectionTarget) -> int:
        """Explicitly remove all application trust for a host."""
        with self._lock:
            original = self._read_lines()
            token = _host_token(target)
            retained = [
                line for line in original if not _line_matches_host(line, token)
            ]
            removed = len(original) - len(retained)
            if removed:
                self._write_atomic(retained)
            return removed

    def _lines_without_host(self, target: SSHConnectionTarget) -> list[str]:
        token = _host_token(target)
        return [
            line for line in self._read_lines() if not _line_matches_host(line, token)
        ]

    def _read_lines(self) -> list[str]:
        self._validate_paths()
        try:
            return self.path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise SSHTrustStoreError("SSH trust store could not be read") from exc

    def _write_atomic(self, lines: list[str]) -> None:
        self._validate_paths()
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=".known-hosts-", dir=self.path.parent
            )
            temporary = Path(name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                if lines:
                    stream.write("\n".join(lines) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except OSError as exc:
            raise SSHTrustStoreError("SSH trust store update failed") from exc
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink()

    def _validate_paths(self) -> None:
        parent = self.path.parent
        try:
            parent_metadata = parent.lstat()
            file_metadata = self.path.lstat()
        except OSError as exc:
            raise SSHTrustStoreError("SSH trust-store path is inaccessible") from exc
        if (
            stat.S_ISLNK(parent_metadata.st_mode)
            or not stat.S_ISDIR(parent_metadata.st_mode)
            or stat.S_IMODE(parent_metadata.st_mode) & 0o022
        ):
            raise SSHTrustStoreError("SSH trust-store directory is unsafe")
        if stat.S_ISLNK(file_metadata.st_mode) or not stat.S_ISREG(
            file_metadata.st_mode
        ):
            raise SSHTrustStoreError("SSH trust store must be a regular file")
        if file_metadata.st_size > _MAX_TRUST_STORE_BYTES:
            raise SSHTrustStoreError("SSH trust store exceeds its size limit")


def _host_token(target: SSHConnectionTarget) -> str:
    return target.host if target.port == 22 else f"[{target.host}]:{target.port}"


def _parse_keys(content: str, target: SSHConnectionTarget) -> tuple[SSHHostKey, ...]:
    token = _host_token(target)
    keys: list[SSHHostKey] = []
    for line in content.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            raise SSHTrustStoreError("SSH host-key scan returned malformed data")
        keys.append(_make_key(token, parts[1], parts[2]))
    return tuple(keys)


def _parse_known_hosts(content: str) -> tuple[SSHHostKey, ...]:
    keys: list[SSHHostKey] = []
    for line in content.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3 or "," in parts[0] or parts[0].startswith("|"):
            raise SSHTrustStoreError("SSH trust store contains unsupported data")
        keys.append(_make_key(parts[0], parts[1], parts[2]))
    return tuple(keys)


def _make_key(host: str, algorithm: str, public_key: str) -> SSHHostKey:
    if not algorithm.startswith("ssh-") and not algorithm.startswith("ecdsa-"):
        raise SSHTrustStoreError("SSH host-key algorithm is unsupported")
    try:
        decoded = base64.b64decode(public_key, validate=True)
    except ValueError as exc:
        raise SSHTrustStoreError("SSH host key is malformed") from exc
    digest = base64.b64encode(hashlib.sha256(decoded).digest()).decode().rstrip("=")
    return SSHHostKey(host, algorithm, public_key, f"SHA256:{digest}")


def _select_fingerprint(keys: tuple[SSHHostKey, ...], expected: str) -> SSHHostKey:
    matches = [key for key in keys if key.fingerprint == expected]
    if len(matches) != 1:
        raise SSHFingerprintMismatchError("presented host fingerprint did not match")
    return matches[0]


def _render_key(key: SSHHostKey) -> str:
    return f"{key.host_token} {key.algorithm} {key.public_key}"


def _line_matches_host(line: str, token: str) -> bool:
    parts = line.split()
    return len(parts) == 3 and parts[0] == token
