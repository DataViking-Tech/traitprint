"""Tests for vault directory resolution (per-project vault paths)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from traitprint.cli import cli
from traitprint.vault import (
    DEFAULT_VAULT_DIR,
    VAULT_DIR_ENV_VAR,
    VaultStore,
    resolve_vault_dir,
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure TRAITPRINT_VAULT_DIR is unset during a test."""
    monkeypatch.delenv(VAULT_DIR_ENV_VAR, raising=False)


class TestResolveVaultDir:
    def test_explicit_wins_over_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(VAULT_DIR_ENV_VAR, str(tmp_path / "env-vault"))
        explicit = tmp_path / "explicit-vault"
        assert resolve_vault_dir(explicit) == explicit

    def test_env_used_when_no_explicit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env_dir = tmp_path / "env-vault"
        monkeypatch.setenv(VAULT_DIR_ENV_VAR, str(env_dir))
        # Point cwd at an empty dir so walk-up finds nothing.
        monkeypatch.chdir(tmp_path)
        assert resolve_vault_dir() == env_dir

    def test_walk_up_finds_local_vault(
        self, tmp_path: Path, clean_env: None
    ) -> None:
        project = tmp_path / "proj"
        nested = project / "a" / "b"
        nested.mkdir(parents=True)
        (project / ".traitprint").mkdir()
        assert resolve_vault_dir(start=nested) == (project / ".traitprint").resolve()

    def test_walk_up_finds_vault_in_cwd_itself(
        self, tmp_path: Path, clean_env: None
    ) -> None:
        (tmp_path / ".traitprint").mkdir()
        assert resolve_vault_dir(start=tmp_path) == (
            tmp_path / ".traitprint"
        ).resolve()

    def test_falls_back_to_home_default(
        self, tmp_path: Path, clean_env: None
    ) -> None:
        # tmp_path has no .traitprint/ anywhere up the tree (under pytest tmp).
        # But the real filesystem might — so only assert that when nothing
        # is found we get the home default. Build a self-contained isolated
        # tree under tmp_path and verify by monkeypatching home-side checks.
        deep = tmp_path / "isolated" / "inner"
        deep.mkdir(parents=True)
        resolved = resolve_vault_dir(start=deep)
        # Walk up from `deep` could hit a real .traitprint somewhere above
        # tmp_path in CI — guard by only asserting fall-through when none
        # exists between `deep` and the root.
        current = deep.resolve()
        found_ancestor = any(
            (p / ".traitprint").is_dir() for p in (current, *current.parents)
        )
        if found_ancestor:
            pytest.skip("Ambient .traitprint/ on filesystem breaks isolation")
        assert resolved == DEFAULT_VAULT_DIR

    def test_env_var_empty_string_is_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(VAULT_DIR_ENV_VAR, "")
        monkeypatch.chdir(tmp_path)
        # Empty env var should be treated as "not set" and fall through.
        # In an isolated tmp, walk-up finds nothing → home default.
        current = tmp_path.resolve()
        found_ancestor = any(
            (p / ".traitprint").is_dir() for p in (current, *current.parents)
        )
        if found_ancestor:
            pytest.skip("Ambient .traitprint/ on filesystem breaks isolation")
        assert resolve_vault_dir() == DEFAULT_VAULT_DIR


class TestVaultStoreResolution:
    def test_store_honors_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_dir = tmp_path / "env-vault"
        monkeypatch.setenv(VAULT_DIR_ENV_VAR, str(env_dir))
        store = VaultStore()
        assert store.directory == env_dir

    def test_store_honors_explicit_path_over_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(VAULT_DIR_ENV_VAR, str(tmp_path / "env"))
        explicit = tmp_path / "explicit"
        store = VaultStore(explicit)
        assert store.directory == explicit


class TestCliVaultDirFlag:
    def test_vault_dir_flag_initializes_explicit_path(
        self, tmp_path: Path, clean_env: None
    ) -> None:
        vault_dir = tmp_path / "proj-vault"
        runner = CliRunner()
        result = runner.invoke(cli, ["--vault-dir", str(vault_dir), "init"])
        assert result.exit_code == 0, result.output
        assert (vault_dir / "vault.json").is_file()

    def test_path_alias_still_works(
        self, tmp_path: Path, clean_env: None
    ) -> None:
        vault_dir = tmp_path / "proj-vault"
        runner = CliRunner()
        result = runner.invoke(cli, ["--path", str(vault_dir), "init"])
        assert result.exit_code == 0, result.output
        assert (vault_dir / "vault.json").is_file()

    def test_env_var_drives_cli_when_no_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault_dir = tmp_path / "env-vault"
        monkeypatch.setenv(VAULT_DIR_ENV_VAR, str(vault_dir))
        runner = CliRunner()
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0, result.output
        assert (vault_dir / "vault.json").is_file()

    def test_walk_up_finds_local_project_vault(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        clean_env: None,
    ) -> None:
        # Create a project with a pre-initialized local vault.
        project = tmp_path / "proj"
        local_vault = project / ".traitprint"
        local_vault.mkdir(parents=True)
        runner = CliRunner()
        # Bootstrap the local vault using an explicit path first.
        init_result = runner.invoke(cli, ["--vault-dir", str(local_vault), "init"])
        assert init_result.exit_code == 0, init_result.output

        # Now, with cwd somewhere under the project, running without
        # any flag or env should resolve to the local .traitprint/.
        nested = project / "src" / "deep"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)
        show_result = runner.invoke(cli, ["vault", "show"])
        assert show_result.exit_code == 0, show_result.output
        # When the global fallback is used we'd see "No vault found" only if
        # ~/.traitprint is missing — so to assert walk-up worked, check the
        # verbose output points at the local vault directory.
        verbose = runner.invoke(cli, ["vault", "show", "--verbose"])
        assert verbose.exit_code == 0, verbose.output
        assert str(local_vault.resolve()) in verbose.output


class TestWalkUpPrecedence:
    def test_explicit_beats_walk_up(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        clean_env: None,
    ) -> None:
        project = tmp_path / "proj"
        (project / ".traitprint").mkdir(parents=True)
        explicit = tmp_path / "other"
        monkeypatch.chdir(project)
        assert resolve_vault_dir(explicit) == explicit

    def test_env_beats_walk_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = tmp_path / "proj"
        (project / ".traitprint").mkdir(parents=True)
        env_dir = tmp_path / "env"
        monkeypatch.setenv(VAULT_DIR_ENV_VAR, str(env_dir))
        monkeypatch.chdir(project)
        assert resolve_vault_dir() == env_dir
