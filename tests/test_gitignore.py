import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def is_ignored(path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", path],
        cwd=PROJECT_ROOT,
        check=False,
    )
    return result.returncode == 0


def test_all_runtime_contents_are_ignored() -> None:
    runtime_files = (
        "runtime/data/inventory.json",
        "runtime/data/nested/inventory.json",
        "runtime/jobs/output.txt",
        "runtime/jobs/nested/output.txt",
        "runtime/logs/lim.txt",
        "runtime/logs/nested/lim.txt",
        "runtime/backups/archive.tar.gz",
        "runtime/backups/nested/archive.tar.gz",
        "runtime/unexpected.txt",
    )

    assert all(is_ignored(path) for path in runtime_files)


def test_only_runtime_placeholders_are_not_ignored() -> None:
    placeholders = (
        "runtime/data/.gitkeep",
        "runtime/jobs/.gitkeep",
        "runtime/logs/.gitkeep",
        "runtime/backups/.gitkeep",
    )

    assert all(not is_ignored(path) for path in placeholders)
    assert is_ignored("runtime/data/nested/.gitkeep")
