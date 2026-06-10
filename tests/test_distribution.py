"""Truthfulness tests for the distribution artifacts (D7 wave 1).

Covers:
- ``gemini-extension.json`` — valid manifest at the repo root (the Gemini
  CLI installer and gallery crawler both require the absolute root), with
  a version that tracks ``pyproject.toml`` and an MCP wiring that points
  at the real hosted server.
- ``GEMINI.md`` — the extension context file exists and obeys the same
  truthfulness rules as the skills: every ``traitprint <subcommand>``
  mention resolves against the real click command tree, and no legacy
  (v0 vault / ten-point scale) vocabulary appears.
- ``docs/distribution-runbook.md`` and the README "connect" section agree
  with the manifest on the hosted MCP server URL and the extension
  install source.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import click
import pytest

from tests.test_skills import _CLI_MENTION, _FORBIDDEN
from traitprint.cli import cli

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "gemini-extension.json"
GEMINI_MD = REPO_ROOT / "GEMINI.md"
RUNBOOK = REPO_ROOT / "docs" / "distribution-runbook.md"
README = REPO_ROOT / "README.md"

HOSTED_MCP_URL = "https://api.traitprint.com/functions/v1/mcp-server"
INSTALL_URL = "https://github.com/DataViking-Tech/traitprint"


def _pyproject_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, flags=re.MULTILINE)
    assert match, "pyproject.toml has no version line"
    return match.group(1)


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class TestGeminiExtensionManifest:
    def test_manifest_lives_at_repo_root(self) -> None:
        # `gemini extensions install <github url>` and the gallery crawler
        # both require gemini-extension.json in the absolute repo root.
        assert MANIFEST.is_file()

    def test_required_fields(self, manifest: dict) -> None:
        assert manifest["name"] == "traitprint"  # lowercase, dashes only
        assert re.fullmatch(r"[a-z][a-z0-9-]*", manifest["name"])
        assert manifest["description"].strip()

    def test_version_tracks_pyproject(self, manifest: dict) -> None:
        assert manifest["version"] == _pyproject_version()

    def test_wires_the_hosted_mcp_server(self, manifest: dict) -> None:
        servers = manifest["mcpServers"]
        assert servers["traitprint"]["httpUrl"] == HOSTED_MCP_URL

    def test_context_file_exists(self, manifest: dict) -> None:
        assert (REPO_ROOT / manifest["contextFileName"]).is_file()

    def test_bundled_skills_layout(self) -> None:
        # Gemini auto-discovers skills/<name>/SKILL.md from the extension
        # root — the repo's skills/ directory must keep that shape.
        skill_dirs = [
            p
            for p in (REPO_ROOT / "skills").iterdir()
            if p.is_dir() and p.name != "shared"
        ]
        assert skill_dirs
        for d in skill_dirs:
            assert (d / "SKILL.md").is_file(), f"{d} has no SKILL.md"


class TestGeminiContextFile:
    def test_cli_mentions_resolve(self) -> None:
        text = GEMINI_MD.read_text(encoding="utf-8")
        mentions = _CLI_MENTION.findall(text)
        assert mentions, "GEMINI.md never shows a traitprint command"
        for raw in mentions:
            tokens = raw.split()
            first = tokens[0]
            assert first in cli.commands, f"unknown command 'traitprint {first}'"
            node = cli.commands[first]
            if isinstance(node, click.Group):
                assert len(tokens) >= 2 and tokens[1] in node.commands, (
                    f"unknown subcommand 'traitprint {first} "
                    f"{tokens[1] if len(tokens) > 1 else ''}'"
                )

    def test_no_legacy_vocabulary(self) -> None:
        text = GEMINI_MD.read_text(encoding="utf-8")
        for needle in _FORBIDDEN:
            assert needle not in text, f"GEMINI.md mentions legacy {needle!r}"

    def test_mentions_hosted_server_and_proficiency_scale(self) -> None:
        text = GEMINI_MD.read_text(encoding="utf-8")
        assert HOSTED_MCP_URL in text
        for label in ("familiar", "working", "proficient", "expert", "authority"):
            assert label in text


class TestDistributionDocsAgree:
    def test_runbook_exists_and_names_the_surfaces(self) -> None:
        text = RUNBOOK.read_text(encoding="utf-8")
        assert HOSTED_MCP_URL in text
        # All five wave-1 surfaces are covered.
        for needle in (
            "Claude connector",
            "ChatGPT",
            "registry.modelcontextprotocol.io",
            "gemini extensions install",
            "skills.sh",
            # The hosted-server registry artifact lives cloud-side.
            "docs/mcp-registry-submission",
        ):
            assert needle in text, f"runbook missing {needle!r}"

    def test_readme_connect_section(self) -> None:
        text = README.read_text(encoding="utf-8")
        assert HOSTED_MCP_URL in text
        assert f"gemini extensions install {INSTALL_URL}" in text
