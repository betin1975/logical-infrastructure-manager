"""Bounded, environment-independent local process execution for OpenSSH only."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import BinaryIO

from .exceptions import SSHLocalProcessError


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    """Bounded local OpenSSH process outcome."""

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    cancelled: bool
    stdout_truncated: bool
    stderr_truncated: bool
    duration_seconds: float


class OpenSSHProcessRunner:
    """Run only preconstructed OpenSSH argument arrays without a shell."""

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        max_stdout_bytes: int,
        max_stderr_bytes: int,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> ProcessOutcome:
        """Drain output concurrently, retaining only configured byte limits."""
        started = time.monotonic()
        environment = {
            "PATH": os.defpath,
            "LANG": "C",
            "LC_ALL": "C",
        }
        try:
            process = subprocess.Popen(  # noqa: S603 - validated explicit executables
                list(arguments),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                env=environment,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise SSHLocalProcessError(
                f"OpenSSH process could not start: {type(exc).__name__}"
            ) from exc

        assert process.stdout is not None
        assert process.stderr is not None
        stdout_result: list[tuple[bytes, bool]] = []
        stderr_result: list[tuple[bytes, bool]] = []
        readers = (
            threading.Thread(
                target=_read_bounded,
                args=(process.stdout, max_stdout_bytes, stdout_result),
                daemon=True,
            ),
            threading.Thread(
                target=_read_bounded,
                args=(process.stderr, max_stderr_bytes, stderr_result),
                daemon=True,
            ),
        )
        for reader in readers:
            reader.start()

        timed_out = False
        cancelled = False
        deadline = started + timeout_seconds
        while process.poll() is None:
            try:
                cancellation = (
                    cancellation_requested is not None and cancellation_requested()
                )
            except Exception:
                cancellation = True
            if cancellation:
                cancelled = True
                _kill_process_group(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                _kill_process_group(process)
                break
            time.sleep(0.01)
        process.wait()
        for reader in readers:
            reader.join()
        stdout, stdout_truncated = stdout_result[0]
        stderr, stderr_truncated = stderr_result[0]
        return ProcessOutcome(
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            cancelled=cancelled,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            duration_seconds=time.monotonic() - started,
        )


def _read_bounded(
    stream: BinaryIO,
    limit: int,
    destination: list[tuple[bytes, bool]],
) -> None:
    retained = bytearray()
    truncated = False
    while chunk := stream.read(65536):
        remaining = limit - len(retained)
        if remaining > 0:
            retained.extend(chunk[:remaining])
        if len(chunk) > max(remaining, 0):
            truncated = True
    destination.append((bytes(retained), truncated))


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
