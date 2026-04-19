"""Tests for the traitprint init command."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from traitprint.cli import cli
from traitprint.schema import VaultSchema


class TestInit:
    def test_init_creates_directory_and_vault(self, tmp_path: Path) -> None:
        vault_dir = tmp_path / "test-vault"
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--path", str(vault_dir)])

        assert result.exit_code == 0
        assert vault_dir.is_dir()
        assert (vault_dir / "vault.json").is_file()
        assert (vault_dir / ".git").is_dir()
        assert (vault_dir / ".gitignore").is_file()

    def test_vault_json_validates_against_schema(self, tmp_path: Path) -> None:
        vault_dir = tmp_path / "test-vault"
        runner = CliRunner()
        runner.invoke(cli, ["init", "--path", str(vault_dir)])

        raw = (vault_dir / "vault.json").read_text()
        data = json.loads(raw)
        vault = VaultSchema.model_validate(data)
        assert vault.schema_version == 0
        assert vault.skills == []

    def test_double_init_is_safe(self, tmp_path: Path) -> None:
        vault_dir = tmp_path / "test-vault"
        runner = CliRunner()

        # First init
        result1 = runner.invoke(cli, ["init", "--path", str(vault_dir)])
        assert result1.exit_code == 0

        # Write something to verify it's not overwritten
        vault_path = vault_dir / "vault.json"
        original_content = vault_path.read_text()

        # Second init
        result2 = runner.invoke(cli, ["init", "--path", str(vault_dir)])
        assert result2.exit_code == 0
        assert "already exists" in result2.output

        # Content should be unchanged
        assert vault_path.read_text() == original_content

    def test_gitignore_contains_credentials(self, tmp_path: Path) -> None:
        vault_dir = tmp_path / "test-vault"
        runner = CliRunner()
        runner.invoke(cli, ["init", "--path", str(vault_dir)])

        gitignore = (vault_dir / ".gitignore").read_text()
        assert ".credentials" in gitignore

    def test_version_flag(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output
