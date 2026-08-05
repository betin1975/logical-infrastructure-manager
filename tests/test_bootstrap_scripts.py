"""Execution tests for the fixed atomic remote file-management helper."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from app.bootstrap.scripts import remote_file_manager_command


def _run(action: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        remote_file_manager_command(sys.executable, action, *arguments),
        check=False,
        capture_output=True,
        text=True,
    )


def test_remote_helper_installs_and_merges_files_idempotently(tmp_path: Path) -> None:
    uid = os.getuid()
    gid = os.getgid()
    managed = tmp_path / "managed"
    directory = _run("ensure_dir", str(managed), str(uid), str(gid), "0700")
    assert directory.returncode == 0
    assert json.loads(directory.stdout)["changed"] is True
    assert stat.S_IMODE(managed.stat().st_mode) == 0o700

    staged = tmp_path / "staged"
    staged.write_text("artifact-v1", encoding="utf-8")
    destination = managed / "collector"
    first = _run(
        "install",
        str(staged),
        str(destination),
        str(uid),
        str(gid),
        "0755",
    )
    second = _run(
        "install",
        str(staged),
        str(destination),
        str(uid),
        str(gid),
        "0755",
    )
    assert json.loads(first.stdout)["changed"] is True
    assert json.loads(second.stdout)["changed"] is False
    assert destination.read_text(encoding="utf-8") == "artifact-v1"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o755

    authorized = managed / "authorized_keys"
    authorized.write_text("ssh-ed25519 unrelated keep-me\n", encoding="utf-8")
    key_stage = tmp_path / "key"
    key_stage.write_text(
        'restrict,command="/collector" ssh-ed25519 synthetic lim-managed\n',
        encoding="utf-8",
    )
    merged = _run(
        "merge_key",
        str(key_stage),
        str(authorized),
        str(uid),
        str(gid),
        "0600",
        "lim-managed",
    )
    repeated = _run(
        "merge_key",
        str(key_stage),
        str(authorized),
        str(uid),
        str(gid),
        "0600",
        "lim-managed",
    )
    content = authorized.read_text(encoding="utf-8")
    assert json.loads(merged.stdout)["changed"] is True
    assert json.loads(repeated.stdout)["changed"] is False
    assert "keep-me" in content
    assert content.count("lim-managed") == 1


def test_remote_helper_rejects_symlinks_and_unsafe_destinations(tmp_path: Path) -> None:
    uid = os.getuid()
    gid = os.getgid()
    outside = tmp_path / "outside"
    outside.write_text("preserve", encoding="utf-8")
    symlink = tmp_path / "destination"
    symlink.symlink_to(outside)
    staged = tmp_path / "staged"
    staged.write_text("replacement", encoding="utf-8")

    result = _run(
        "install",
        str(staged),
        str(symlink),
        str(uid),
        str(gid),
        "0600",
    )

    assert result.returncode != 0
    assert json.loads(result.stdout)["message"] == "unsafe_destination"
    assert outside.read_text(encoding="utf-8") == "preserve"


def test_remote_helper_rejects_invalid_key_entry_and_unknown_action(
    tmp_path: Path,
) -> None:
    bad_key = tmp_path / "bad-key"
    bad_key.write_text("first\nsecond\n", encoding="utf-8")
    result = _run(
        "merge_key",
        str(bad_key),
        str(tmp_path / "authorized_keys"),
        str(os.getuid()),
        str(os.getgid()),
        "0600",
        "lim-managed",
    )
    unknown = _run("unknown")

    assert result.returncode != 0
    assert json.loads(result.stdout)["message"] == "invalid_key_entry"
    assert unknown.returncode != 0
    assert json.loads(unknown.stdout)["message"] == "invalid_action"
