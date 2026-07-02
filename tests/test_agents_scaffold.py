"""Tests for `traitprint agents init` (agent-runtime entrypoint scaffolder).

Covers:
- The runtime table: expected runtimes only (no Gemini — the published
  extension covers it), thin wrappers that delegate to AGENTS.md, and
  MCP snippets that all invoke ``traitprint mcp-serve``.
- Scaffolding a directory: canonical AGENTS.md copy, wrapper files,
  project-scoped MCP configs, and the shipped skills under both
  ``.agents/skills/`` and ``.claude/skills/``.
- Safety: idempotent re-runs, existing files never overwritten, nothing
  written outside the target directory, no reserved branding in output.
- The ``--json`` report contract and the post-scaffold checklist that
  replaces the (nonexistent) doctor step.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner, Result

from tests.test_skills import _CLI_MENTION, _FORBIDDEN
from traitprint.agents_scaffold import (
    MCP_REGISTRATIONS,
    SKILL_DESTINATIONS,
    WRAPPERS,
    agents_manual,
    scaffold,
)
from traitprint.cli import cli
from traitprint.skills import SKILL_NAMES

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_RUNTIMES = {"claude", "codex", "opencode", "qwen", "grok", "kimi"}

#: Every non-skill file a fresh scaffold writes.
EXPECTED_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "QWEN.md",
    ".grok/GROK.md",
    ".mcp.json",
    "opencode.json",
    ".qwen/settings.json",
    ".grok/settings.json",
)


def _scaffold(tmp_path: Path, *extra: str) -> tuple[Path, Result]:
    target = tmp_path / "project"
    runner = CliRunner()
    result = runner.invoke(cli, ["agents", "init", str(target), *extra])
    return target, result


# ── The runtime table ────────────────────────────────────────────────


class TestRuntimeTable:
    def test_registrations_cover_expected_runtimes(self) -> None:
        assert {reg.runtime for reg in MCP_REGISTRATIONS} == EXPECTED_RUNTIMES

    def test_gemini_is_not_scaffolded(self) -> None:
        # The published Gemini extension (gemini-extension.json) already
        # wires the hosted MCP server and bundles the skills.
        assert "gemini" not in {reg.runtime for reg in MCP_REGISTRATIONS}
        assert "gemini" not in {w.runtime for w in WRAPPERS}

    def test_every_snippet_invokes_mcp_serve(self) -> None:
        for reg in MCP_REGISTRATIONS:
            assert "mcp-serve" in reg.snippet, reg.runtime
            assert "traitprint" in reg.snippet, reg.runtime

    def test_json_snippets_parse(self) -> None:
        for reg in MCP_REGISTRATIONS:
            if reg.path.endswith(".json"):
                parsed = json.loads(reg.snippet)
                assert isinstance(parsed, dict), reg.runtime

    def test_codex_snippet_is_a_toml_table(self) -> None:
        codex = next(r for r in MCP_REGISTRATIONS if r.runtime == "codex")
        assert "[mcp_servers.traitprint]" in codex.snippet
        assert 'command = "traitprint"' in codex.snippet

    def test_home_directory_configs_are_never_project_files(self) -> None:
        for reg in MCP_REGISTRATIONS:
            assert reg.path.startswith("~") == (not reg.in_project), reg.runtime

    def test_wrappers_are_thin_and_delegate(self) -> None:
        # Issue #66 risk mitigation: wrappers stay a few lines pointing at
        # one AGENTS.md, so runtime additions are additive files not logic.
        for w in WRAPPERS:
            lines = [ln for ln in w.text.splitlines() if ln.strip()]
            assert len(lines) <= 5, f"{w.runtime} wrapper is not thin"
            assert "AGENTS.md" in w.text

    def test_wrapper_cli_mentions_resolve(self) -> None:
        for w in WRAPPERS:
            for raw in _CLI_MENTION.findall(w.text):
                first = raw.split()[0]
                assert first in cli.commands, (
                    f"{w.runtime}: unknown command 'traitprint {first}'"
                )

    def test_no_legacy_vocabulary_or_reserved_brand(self) -> None:
        texts = [w.text for w in WRAPPERS] + [r.snippet for r in MCP_REGISTRATIONS]
        for text in texts:
            for needle in _FORBIDDEN:
                assert needle not in text
            assert "career-ops" not in text.lower()

    def test_manual_matches_repo_root(self) -> None:
        source = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        assert agents_manual() == source


# ── Scaffolding a directory ──────────────────────────────────────────


class TestAgentsInit:
    def test_creates_all_entrypoint_files(self, tmp_path: Path) -> None:
        target, result = _scaffold(tmp_path)
        assert result.exit_code == 0
        for rel in EXPECTED_FILES:
            assert (target / rel).is_file(), rel

    def test_copies_skills_to_both_destinations(self, tmp_path: Path) -> None:
        target, result = _scaffold(tmp_path)
        assert result.exit_code == 0
        for dest in SKILL_DESTINATIONS:
            for name in SKILL_NAMES:
                assert (target / dest / name / "SKILL.md").is_file()
            # ../shared/cli-reference.md links inside SKILL.md must resolve.
            assert (target / dest / "shared" / "cli-reference.md").is_file()

    def test_agents_md_is_a_verbatim_copy(self, tmp_path: Path) -> None:
        target, _ = _scaffold(tmp_path)
        source = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        assert (target / "AGENTS.md").read_text(encoding="utf-8") == source

    def test_mcp_json_registers_mcp_serve(self, tmp_path: Path) -> None:
        target, _ = _scaffold(tmp_path)
        config = json.loads((target / ".mcp.json").read_text(encoding="utf-8"))
        server = config["mcpServers"]["traitprint"]
        assert server["command"] == "traitprint"
        assert server["args"] == ["mcp-serve"]

    def test_opencode_json_registers_local_server(self, tmp_path: Path) -> None:
        target, _ = _scaffold(tmp_path)
        config = json.loads((target / "opencode.json").read_text(encoding="utf-8"))
        server = config["mcp"]["traitprint"]
        assert server["type"] == "local"
        assert server["command"] == ["traitprint", "mcp-serve"]

    def test_creates_missing_target_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "deep" / "nested" / "project"
        runner = CliRunner()
        result = runner.invoke(cli, ["agents", "init", str(target)])
        assert result.exit_code == 0
        assert (target / "AGENTS.md").is_file()

    def test_default_directory_is_cwd(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["agents", "init"])
            assert result.exit_code == 0
            assert Path("AGENTS.md").is_file()
            assert Path(".mcp.json").is_file()

    def test_target_that_is_a_file_is_a_usage_error(self, tmp_path: Path) -> None:
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("occupied", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["agents", "init", str(blocker)])
        assert result.exit_code == 2

    def test_human_output_lists_files_and_notes(self, tmp_path: Path) -> None:
        _, result = _scaffold(tmp_path)
        assert "[ok]" in result.output
        assert "read AGENTS.md natively" in result.output
        assert "gemini-extension.json" in result.output
        assert "traitprint-fill-vault" in result.output


# ── Safety: never overwrite, never write outside the target ─────────


class TestScaffoldSafety:
    def test_second_run_skips_everything(self, tmp_path: Path) -> None:
        target, first = _scaffold(tmp_path)
        assert first.exit_code == 0
        before = (target / "AGENTS.md").read_text(encoding="utf-8")

        runner = CliRunner()
        second = runner.invoke(cli, ["agents", "init", str(target), "--json"])
        assert second.exit_code == 0
        payload = json.loads(second.output)
        assert payload["written"] == []
        assert set(EXPECTED_FILES) <= set(payload["skipped"])
        assert (target / "AGENTS.md").read_text(encoding="utf-8") == before

    def test_existing_files_are_never_overwritten(self, tmp_path: Path) -> None:
        target = tmp_path / "project"
        target.mkdir()
        sentinel = "# my own rules — do not touch\n"
        (target / "CLAUDE.md").write_text(sentinel, encoding="utf-8")
        (target / ".mcp.json").write_text("{}\n", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(cli, ["agents", "init", str(target)])
        assert result.exit_code == 0
        assert (target / "CLAUDE.md").read_text(encoding="utf-8") == sentinel
        assert (target / ".mcp.json").read_text(encoding="utf-8") == "{}\n"
        assert "exists — kept" in result.output

    def test_skipped_project_config_gets_its_snippet_printed(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "project"
        target.mkdir()
        (target / ".mcp.json").write_text("{}\n", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["agents", "init", str(target)])
        assert result.exit_code == 0
        # The user must merge by hand, so the snippet is emitted.
        assert "Claude Code — add to .mcp.json" in result.output

    def test_fresh_scaffold_prints_only_home_config_snippets(
        self, tmp_path: Path
    ) -> None:
        _, result = _scaffold(tmp_path)
        assert "Codex CLI — add to ~/.codex/config.toml" in result.output
        assert "Kimi CLI — add to ~/.kimi/mcp.json" in result.output
        # Project-scoped configs were written, so no snippet to paste.
        assert "add to .mcp.json" not in result.output

    def test_writes_only_inside_the_target_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "project"
        target.mkdir()
        report = scaffold(target)
        outside = {
            p.relative_to(tmp_path)
            for p in tmp_path.rglob("*")
            if p.is_file() and not str(p).startswith(str(target))
        }
        assert outside == set()
        for f in report.files:
            assert not f.path.startswith(("~", "/", ".."))

    def test_no_reserved_brand_in_output_or_files(self, tmp_path: Path) -> None:
        target, result = _scaffold(tmp_path)
        assert "career-ops" not in result.output.lower()
        for p in target.rglob("*"):
            if p.is_file():
                text = p.read_text(encoding="utf-8").lower()
                assert "career-ops" not in text, p


# ── The --json report contract ───────────────────────────────────────


class TestJsonReport:
    def test_report_shape(self, tmp_path: Path) -> None:
        target, result = _scaffold(tmp_path, "--json")
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert set(payload) == {"directory", "written", "skipped", "mcp", "next_steps"}
        assert payload["directory"] == str(target.resolve())
        assert set(EXPECTED_FILES) <= set(payload["written"])
        assert payload["skipped"] == []

    def test_report_includes_every_mcp_registration(self, tmp_path: Path) -> None:
        _, result = _scaffold(tmp_path, "--json")
        payload = json.loads(result.output)
        entries = {e["runtime"]: e for e in payload["mcp"]}
        assert set(entries) == EXPECTED_RUNTIMES
        for entry in entries.values():
            assert "mcp-serve" in entry["snippet"]
            assert entry["written"] == entry["in_project"]

    def test_report_lists_skill_copies(self, tmp_path: Path) -> None:
        _, result = _scaffold(tmp_path, "--json")
        payload = json.loads(result.output)
        for dest in SKILL_DESTINATIONS:
            for name in SKILL_NAMES:
                assert f"{dest}/{name}/SKILL.md" in payload["written"]


# ── Post-scaffold checklist (replaces the cut doctor step) ───────────


class TestNextSteps:
    def test_prompts_vault_init_when_no_vault(self, tmp_path: Path) -> None:
        target = tmp_path / "project"
        vault_dir = tmp_path / "no-such-vault"
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--vault-dir", str(vault_dir), "agents", "init", str(target)]
        )
        assert result.exit_code == 0
        assert "Create your vault: traitprint init" in result.output

    def test_no_vault_init_prompt_when_vault_exists(self, tmp_path: Path) -> None:
        vault_dir = tmp_path / "vault"
        runner = CliRunner()
        created = runner.invoke(cli, ["--vault-dir", str(vault_dir), "init"])
        assert created.exit_code == 0
        target = tmp_path / "project"
        result = runner.invoke(
            cli, ["--vault-dir", str(vault_dir), "agents", "init", str(target)]
        )
        assert result.exit_code == 0
        assert "Create your vault" not in result.output

    def test_checklist_ends_with_fill_vault(self, tmp_path: Path) -> None:
        _, result = _scaffold(tmp_path, "--json")
        payload = json.loads(result.output)
        assert payload["next_steps"]
        assert "traitprint-fill-vault" in payload["next_steps"][-1]
