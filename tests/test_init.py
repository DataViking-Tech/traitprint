"""Tests for the traitprint init command."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from traitprint import __version__
from traitprint.cli import cli
from traitprint.vault import VaultStore


class TestInit:
    def test_init_creates_directory_and_vault(self, tmp_path: Path) -> None:
        vault_dir = tmp_path / "test-vault"
        runner = CliRunner()
        result = runner.invoke(cli, ["--path", str(vault_dir), "init"])

        assert result.exit_code == 0
        assert vault_dir.is_dir()
        # v1 file tree, no legacy vault.json
        assert (vault_dir / "traitprint.json").is_file()
        assert (vault_dir / "profile.json").is_file()
        assert (vault_dir / "skills.json").is_file()
        assert (vault_dir / "education.json").is_file()
        assert (vault_dir / "experiences").is_dir()
        assert (vault_dir / "stories").is_dir()
        assert (vault_dir / "philosophies").is_dir()
        assert not (vault_dir / "vault.json").exists()
        assert (vault_dir / ".git").is_dir()
        assert (vault_dir / ".gitignore").is_file()

    def test_manifest_is_schema_v1(self, tmp_path: Path) -> None:
        vault_dir = tmp_path / "test-vault"
        runner = CliRunner()
        runner.invoke(cli, ["--path", str(vault_dir), "init"])

        manifest = json.loads((vault_dir / "traitprint.json").read_text())
        assert manifest["schema_version"] == 1
        assert manifest["vault_id"]
        assert manifest["updated_at"]

        vault = VaultStore(vault_dir).load()
        assert vault.schema_version == 1
        assert vault.skills == []

    def test_double_init_is_safe(self, tmp_path: Path) -> None:
        vault_dir = tmp_path / "test-vault"
        runner = CliRunner()

        # First init
        result1 = runner.invoke(cli, ["--path", str(vault_dir), "init"])
        assert result1.exit_code == 0

        # Capture state to verify it's not overwritten
        manifest_path = vault_dir / "traitprint.json"
        original_content = manifest_path.read_text()

        # Second init
        result2 = runner.invoke(cli, ["--path", str(vault_dir), "init"])
        assert result2.exit_code == 0
        assert "already exists" in result2.output

        # Content should be unchanged
        assert manifest_path.read_text() == original_content

    def test_gitignore_contains_credentials(self, tmp_path: Path) -> None:
        vault_dir = tmp_path / "test-vault"
        runner = CliRunner()
        runner.invoke(cli, ["--path", str(vault_dir), "init"])

        gitignore = (vault_dir / ".gitignore").read_text()
        assert ".credentials" in gitignore

    def test_version_flag(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output
