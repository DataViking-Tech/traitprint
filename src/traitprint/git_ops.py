"""Git versioning helpers for the vault directory."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git command in the given directory."""
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def init_repo(path: Path) -> None:
    """Initialize a git repo if one does not already exist."""
    if not (path / ".git").is_dir():
        _run(["git", "init"], cwd=path)
        # Configure identity for the vault repo so commits work
        # even without a global git config.
        _run(["git", "config", "user.email", "vault@traitprint.local"], cwd=path)
        _run(["git", "config", "user.name", "Traitprint Vault"], cwd=path)


def commit(path: Path, message: str) -> None:
    """Stage vault.json (and .gitignore if present) then commit."""
    _run(["git", "add", "vault.json"], cwd=path)
    if (path / ".gitignore").is_file():
        _run(["git", "add", ".gitignore"], cwd=path)
    _run(["git", "commit", "-m", message, "--allow-empty"], cwd=path)


def head_sha(path: Path, *, short: bool = True) -> str:
    """Return the current HEAD commit SHA for the repo at *path*.

    Returns an empty string if the repo has no commits or is not a repo.
    """
    args = (
        ["git", "rev-parse", "--short", "HEAD"]
        if short
        else ["git", "rev-parse", "HEAD"]
    )
    result = _run(args, cwd=path)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def log(path: Path, n: int = 10) -> list[str]:
    """Return the last *n* log entries as one-line strings."""
    result = _run(
        ["git", "log", f"-{n}", "--oneline", "--", "vault.json"],
        cwd=path,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.strip().splitlines() if line]


def diff(path: Path) -> str:
    """Return the diff between HEAD and HEAD~1 for vault.json."""
    result = _run(
        ["git", "diff", "HEAD~1", "--", "vault.json"],
        cwd=path,
    )
    return result.stdout if result.returncode == 0 else ""


def rollback(path: Path) -> None:
    """Revert vault.json to the previous commit."""
    _run(["git", "checkout", "HEAD~1", "--", "vault.json"], cwd=path)
    _run(["git", "add", "vault.json"], cwd=path)
    _run(["git", "commit", "-m", "Rollback to previous state"], cwd=path)
