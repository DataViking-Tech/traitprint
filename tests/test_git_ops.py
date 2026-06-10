"""Tests for git auto-commit robustness (silent-failure fixes).

Covers:
- A vault written into a plain directory (no ``.git``) gets a repo and a
  commit on the first CLI write.
- An adopted pre-existing repo has its vault-local config (identity +
  ``commit.gpgsign=false``) ensured even though ``.git`` already exists.
- A failing ``git commit`` produces a prominent stderr warning while the
  data write succeeds (exit code 0, vault intact).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from traitprint import git_ops
from traitprint.cli import cli
from traitprint.git_ops import commit, head_sha, init_repo
from traitprint.vault import VaultStore


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


def _plain_dir_vault(tmp_path: Path) -> Path:
    """A valid v1 vault in a plain directory — no .git, no .gitignore."""
    d = tmp_path / "plain-vault"
    d.mkdir()
    store = VaultStore(d)
    store.save(store.create_empty())
    assert not (d / ".git").exists()
    return d


class TestPlainDirAdoption:
    def test_first_write_creates_repo_and_commit(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        d = _plain_dir_vault(tmp_path)
        result = runner.invoke(
            cli,
            ["--path", str(d), "vault", "add-skill", "Go", "-p", "3", "-c", "tech"],
        )
        assert result.exit_code == 0, result.output
        assert (d / ".git").is_dir()
        assert head_sha(d)  # the write was committed
        # .credentials must be ignored before the adopting `git add -A`.
        assert ".credentials" in (d / ".gitignore").read_text()

    def test_migrate_initializes_repo_when_missing(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        d = tmp_path / "v0-vault"
        d.mkdir()
        (d / "vault.json").write_text(
            json.dumps(
                {
                    "schema_version": 0,
                    "skills": [
                        {
                            "id": "8f59b8ab-9a0b-4bd5-bd25-7a4d85a0a8a4",
                            "name": "Python",
                            "proficiency": 9,
                            "category": "technical",
                        }
                    ],
                }
            )
        )
        result = runner.invoke(cli, ["--path", str(d), "vault", "migrate"])
        assert result.exit_code == 0, result.output
        assert (d / ".git").is_dir()
        log = _git(["log", "--oneline"], d).stdout
        assert "Migrate vault to schema v1" in log
        # Singular grammar for a single remap.
        assert "Remapped 1 skill proficiency from" in result.output

    def test_commit_returns_true_on_success(self, tmp_path: Path) -> None:
        d = _plain_dir_vault(tmp_path)
        assert commit(d, "adopt") is True


class TestAdoptedRepoConfig:
    def test_init_repo_fixes_config_of_existing_repo(self, tmp_path: Path) -> None:
        d = tmp_path / "vault"
        d.mkdir()
        _git(["init"], d)
        # Broken commit config: signing forced on with a bogus gpg binary.
        _git(["config", "commit.gpgsign", "true"], d)
        _git(["config", "gpg.program", "/nonexistent/definitely-not-gpg"], d)

        init_repo(d)

        gpgsign = _git(["config", "--local", "commit.gpgsign"], d).stdout.strip()
        assert gpgsign == "false"
        assert _git(["config", "--local", "user.email"], d).stdout.strip() == (
            "vault@traitprint.local"
        )
        assert _git(["config", "--local", "user.name"], d).stdout.strip() == (
            "Traitprint Vault"
        )

    def test_write_into_broken_adopted_repo_commits(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        d = tmp_path / "vault"
        d.mkdir()
        _git(["init"], d)
        _git(["config", "commit.gpgsign", "true"], d)
        _git(["config", "gpg.program", "/nonexistent/definitely-not-gpg"], d)
        store = VaultStore(d)
        store.save(store.create_empty())

        result = runner.invoke(
            cli,
            ["--path", str(d), "vault", "add-skill", "Go", "-p", "3", "-c", "tech"],
        )
        assert result.exit_code == 0, result.output
        assert head_sha(d)  # commit went through despite the broken signing setup
        assert "git commit failed" not in result.stderr

    def test_existing_gitignore_gains_credentials_entry(self, tmp_path: Path) -> None:
        d = tmp_path / "vault"
        d.mkdir()
        _git(["init"], d)
        (d / ".gitignore").write_text("*.log\n")
        init_repo(d)
        lines = (d / ".gitignore").read_text().splitlines()
        assert "*.log" in lines
        assert ".credentials" in lines


class TestCommitFailureWarning:
    @pytest.fixture()
    def failing_git(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Make `git commit` fail while every other git call works."""
        real_run = git_ops._run

        def fake_run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            if args[:2] == ["git", "commit"]:
                return subprocess.CompletedProcess(
                    args, 1, stdout="", stderr="fatal: simulated commit failure"
                )
            return real_run(args, cwd)

        monkeypatch.setattr(git_ops, "_run", fake_run)

    def test_warning_printed_and_vault_intact(
        self, runner: CliRunner, tmp_path: Path, failing_git: None
    ) -> None:
        d = tmp_path / "vault"
        d.mkdir()
        init_repo(d)
        store = VaultStore(d)
        store.save(store.create_empty())

        result = runner.invoke(
            cli,
            ["--path", str(d), "vault", "add-skill", "Go", "-p", "3", "-c", "tech"],
        )
        # Data write succeeded → exit 0, but the warning is prominent.
        assert result.exit_code == 0, result.output
        assert "vault saved but git commit failed" in result.stderr
        assert "simulated commit failure" in result.stderr
        assert "history/rollback will not include this change" in result.stderr
        assert [s.name for s in VaultStore(d).load().skills] == ["Go"]

    def test_migrate_warns_loudly_on_commit_failure(
        self, runner: CliRunner, tmp_path: Path, failing_git: None
    ) -> None:
        d = tmp_path / "vault"
        d.mkdir()
        (d / "vault.json").write_text(json.dumps({"schema_version": 0, "skills": []}))
        result = runner.invoke(cli, ["--path", str(d), "vault", "migrate"])
        assert result.exit_code == 0, result.output
        assert "Migrated vault to schema v1" in result.output
        assert "vault saved but git commit failed" in result.stderr
        assert "could NOT be recorded as a git commit" in result.stderr
        # The tree migration itself stands.
        assert (d / "traitprint.json").is_file()
        assert not (d / "vault.json").exists()

    def test_commit_returns_false(self, tmp_path: Path, failing_git: None) -> None:
        d = tmp_path / "vault"
        d.mkdir()
        assert commit(d, "nope") is False
