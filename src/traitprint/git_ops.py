"""Git versioning helpers for the vault directory.

The vault is a v1 file tree (manifest + JSON arrays + markdown files),
so every operation works on the whole tree rather than a single
``vault.json``: commits stage everything (``.credentials`` is
gitignored), log/diff cover the full repo, and rollback restores all
tracked files to the previous commit.
"""

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
        # Vault commits are local bookkeeping; a global signing setup
        # (hardware key, signing server) must not be able to block them.
        _run(["git", "config", "commit.gpgsign", "false"], cwd=path)
        _run(["git", "config", "tag.gpgsign", "false"], cwd=path)


def commit(path: Path, message: str) -> None:
    """Stage all vault content and commit.

    ``.credentials`` is gitignored at init time, so ``git add -A`` never
    stages secrets.
    """
    _run(["git", "add", "-A"], cwd=path)
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
    """Return the last *n* log entries (whole tree) as one-line strings."""
    result = _run(["git", "log", f"-{n}", "--oneline"], cwd=path)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.strip().splitlines() if line]


def diff(path: Path) -> str:
    """Return the diff between HEAD~1 and the working tree."""
    result = _run(["git", "diff", "HEAD~1"], cwd=path)
    return result.stdout if result.returncode == 0 else ""


def rollback(path: Path) -> None:
    """Revert the whole vault tree to the previous commit.

    Restores every tracked file from HEAD~1 and removes files that were
    added in the last commit, then commits the result.
    """
    # Files added in the latest commit don't exist in HEAD~1; a plain
    # ``checkout HEAD~1 -- .`` would leave them behind, so delete them.
    added = _run(
        ["git", "diff", "--diff-filter=A", "--name-only", "HEAD~1", "HEAD"],
        cwd=path,
    )
    if added.returncode == 0:
        for name in added.stdout.splitlines():
            target = path / name
            if name and target.is_file():
                target.unlink()
    _run(["git", "checkout", "HEAD~1", "--", "."], cwd=path)
    _run(["git", "add", "-A"], cwd=path)
    _run(["git", "commit", "-m", "Rollback to previous state"], cwd=path)
