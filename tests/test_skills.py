"""Tests for the Agent Skills (skills/*/SKILL.md) and their MCP wrappers.

Covers:
- Frontmatter validity (name + description) for every published skill.
- MCP prompts serving non-empty text that contains the skill body, so the
  thin wrappers cannot drift from the canonical SKILL.md files.
- Every ``traitprint <subcommand>`` mention in a skill resolving against
  the real click command tree.
- The vault v1 vocabulary: no skill may mention the legacy single-file
  vault or the retired proficiency scale.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import click
import pytest

from traitprint.cli import cli
from traitprint.git_ops import init_repo
from traitprint.mcp_server import create_server
from traitprint.schema import VaultSchema
from traitprint.skills import (
    SKILL_NAMES,
    SkillNotFoundError,
    load_skill,
    skill_body,
    skills_root,
)
from traitprint.vault import VaultStore

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"

#: prompt name -> skill folder name (the MCP wrapper mapping).
PROMPT_TO_SKILL = {
    "fill_vault": "traitprint-fill-vault",
    "mine_story_gaps": "traitprint-mine-story-gaps",
    "discover_skills": "traitprint-discover-skills",
    "draft_star_story": "traitprint-draft-star-story",
    "audit_coherence": "traitprint-audit-coherence",
    "position_lens": "traitprint-position-lens",
    "deepen_story": "traitprint-deepen-story",
    "improve_profile": "traitprint-improve-profile",
}


def _all_skill_markdown_files() -> list[Path]:
    """Every .md under skills/ — SKILL.md files plus shared reference docs."""
    found = sorted(SKILLS_DIR.rglob("*.md"))
    assert found, f"no markdown files under {SKILLS_DIR}"
    return found


@pytest.fixture()
def store(tmp_path: Path) -> VaultStore:
    d = tmp_path / "vault"
    d.mkdir()
    init_repo(d)
    s = VaultStore(d)
    s.save(VaultSchema())
    return s


# ── (a) Frontmatter validity ────────────────────────────────────────


class TestSkillFrontmatter:
    def test_skill_folders_match_registry(self) -> None:
        folders = {
            p.name for p in SKILLS_DIR.iterdir() if p.is_dir() and p.name != "shared"
        }
        assert folders == set(SKILL_NAMES)

    @pytest.mark.parametrize("name", SKILL_NAMES)
    def test_frontmatter_has_name_and_description(self, name: str) -> None:
        fm, body = load_skill(name)
        assert fm["name"] == name  # folder name == frontmatter name
        description = fm["description"]
        assert isinstance(description, str)
        # Specific enough to route on (~1-2 sentences), not an essay.
        assert 50 <= len(description) <= 500
        assert "Use when" in description
        assert body.strip()

    @pytest.mark.parametrize("name", SKILL_NAMES)
    def test_repo_skill_md_exists(self, name: str) -> None:
        assert (SKILLS_DIR / name / "SKILL.md").is_file()

    def test_shared_reference_exists_and_is_linked(self) -> None:
        assert (SKILLS_DIR / "shared" / "cli-reference.md").is_file()
        for name in SKILL_NAMES:
            assert "../shared/cli-reference.md" in skill_body(name), (
                f"{name} does not link the shared CLI reference"
            )

    def test_loader_resolves_a_root(self) -> None:
        root = skills_root()
        assert root.joinpath("shared").joinpath("cli-reference.md").is_file()

    def test_unknown_skill_raises(self) -> None:
        with pytest.raises(SkillNotFoundError):
            load_skill("traitprint-does-not-exist")


# ── (b) MCP prompts serve the skill bodies ──────────────────────────


class TestPromptsServeSkillBodies:
    @pytest.mark.parametrize(("prompt", "skill"), sorted(PROMPT_TO_SKILL.items()))
    def test_prompt_text_contains_skill_body(
        self, store: VaultStore, prompt: str, skill: str
    ) -> None:
        server = create_server(store)
        got = asyncio.run(server.get_prompt(prompt, {}))
        text = got.messages[0].content.text  # type: ignore[union-attr]
        assert text.strip()
        assert skill_body(skill) in text

    def test_prompt_arguments_are_appended(self, store: VaultStore) -> None:
        server = create_server(store)
        fill = asyncio.run(server.get_prompt("fill_vault", {"focus": "education"}))
        assert "education" in fill.messages[0].content.text  # type: ignore[union-attr]
        draft = asyncio.run(
            server.get_prompt("draft_star_story", {"experience": "the 2021 rewrite"})
        )
        assert "the 2021 rewrite" in draft.messages[0].content.text  # type: ignore[union-attr]


# ── (c) CLI mentions resolve against the click tree ─────────────────

# "traitprint" followed by one or more lowercase words (subcommand tokens).
# Stops at flags (--json), uppercase, backticks, and punctuation.
_CLI_MENTION = re.compile(r"\btraitprint((?:[ \t]+[a-z][a-z0-9-]*)+)")


class TestCliMentionsExist:
    @pytest.mark.parametrize(
        "path",
        _all_skill_markdown_files(),
        ids=lambda p: str(p.relative_to(SKILLS_DIR)),
    )
    def test_mentions_resolve(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        mentions = _CLI_MENTION.findall(text)
        assert mentions, f"{path} never shows a traitprint command"
        for raw in mentions:
            tokens = raw.split()
            first = tokens[0]
            assert first in cli.commands, (
                f"{path}: unknown command 'traitprint {first}'"
            )
            node = cli.commands[first]
            # Group mentions must name a real subcommand (prose trailing
            # words after a resolved command are fine; a bad second token
            # right after a group is not).
            if isinstance(node, click.Group):
                assert len(tokens) >= 2 and tokens[1] in node.commands, (
                    f"{path}: unknown subcommand 'traitprint {first} "
                    f"{tokens[1] if len(tokens) > 1 else ''}'"
                )


# ── (d) Vault v1 vocabulary only ────────────────────────────────────

_FORBIDDEN = (
    "vault.json",
    "1-10",
    "1–10",
    "/10",
    "out of 10",
)


class TestNoLegacyVocabulary:
    @pytest.mark.parametrize(
        "path",
        _all_skill_markdown_files(),
        ids=lambda p: str(p.relative_to(SKILLS_DIR)),
    )
    def test_no_v0_or_ten_point_scale(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        for needle in _FORBIDDEN:
            assert needle not in text, f"{path} mentions legacy {needle!r}"


# ── (e) The external-tool sync skill + docs page (issue #67) ─────────

SYNC_SKILL = "traitprint-agent-vault-sync"
SYNC_DOCS_PAGE = REPO_ROOT / "docs" / "external-tool-sync.md"


class TestAgentVaultSyncSkill:
    """The four-step external-tool sync loop and its safety framing."""

    def test_registered_and_loadable(self) -> None:
        assert SYNC_SKILL in SKILL_NAMES
        fm, body = load_skill(SYNC_SKILL)
        assert fm["name"] == SYNC_SKILL
        assert body.strip()

    def test_four_step_loop_present(self) -> None:
        body = skill_body(SYNC_SKILL)
        # export → judgment gaps → proposals write-back → audit
        assert "traitprint vault export -f markdown" in body
        assert "traitprint vault export -f json" in body
        assert "config/profile.yml" in body
        assert "traitprint proposals add" in body
        assert "traitprint vault audit --json" in body

    def test_no_unshipped_cli_surface_referenced(self) -> None:
        # #62 (`-f career-bundle`) and #63 (`vault import-story-bank`)
        # are in flight; until the click tree actually ships those
        # tokens, neither the skill nor the docs page may reference
        # them. _CLI_MENTION cannot catch a flag *value* or a command
        # written without the `traitprint` prefix, so guard the tokens
        # directly. Once the surface lands, these assertions self-relax.
        vault_group = cli.commands["vault"]
        assert isinstance(vault_group, click.Group)
        fmt_choices: set[str] = set()
        for param in vault_group.commands["export"].params:
            if isinstance(param.type, click.Choice):
                fmt_choices.update(str(c) for c in param.type.choices)
        texts = {
            "skill body": skill_body(SYNC_SKILL),
            "docs page": SYNC_DOCS_PAGE.read_text(encoding="utf-8"),
        }
        for where, text in texts.items():
            if "career-bundle" not in fmt_choices:
                assert "career-bundle" not in text, (
                    f"{where} references the unshipped export format "
                    "'career-bundle'"
                )
            if "import-story-bank" not in vault_group.commands:
                assert "import-story-bank" not in text, (
                    f"{where} references the unshipped command "
                    "'vault import-story-bank'"
                )

    def test_writeback_is_proposals_only(self) -> None:
        body = skill_body(SYNC_SKILL)
        assert "never as direct writes" in body
        assert "Never approve on" in body  # the USER approves, not the agent
        assert "Never invent taxonomy IDs or UUIDs" in body

    def test_mcp_serve_alternative_documented(self) -> None:
        body = skill_body(SYNC_SKILL)
        assert "traitprint mcp-serve" in body
        assert "get_profile_summary" in body
        assert "find_story" in body

    def test_reserved_brand_not_in_any_skill_identifier(self) -> None:
        # "career-ops" is a reserved brand: nominative references in
        # descriptions/docs are fine, product identifiers must stay
        # neutral (this guards future skills too).
        for name in SKILL_NAMES:
            assert "careerops" not in name.replace("-", "").replace("_", "")


class TestExternalToolSyncDocsPage:
    def test_docs_page_exists(self) -> None:
        assert SYNC_DOCS_PAGE.is_file()

    def test_custom_workflow_snippet_and_mcp_note(self) -> None:
        text = SYNC_DOCS_PAGE.read_text(encoding="utf-8")
        # the user-layer Custom Workflows snippet
        assert "modes/_custom.md" in text
        assert "sync traitprint" in text
        assert "traitprint proposals add" in text
        # the skill cross-reference and the grounded-MCP alternative
        assert SYNC_SKILL in text
        assert "traitprint mcp-serve" in text
        assert "get_profile_summary" in text
        assert "find_story" in text

    def test_brand_reference_carries_attribution_note(self) -> None:
        text = SYNC_DOCS_PAGE.read_text(encoding="utf-8")
        assert "nominative" in text
        assert "MIT" in text


# ── (f) The positioning-lens skill + MCP prompt (Phase 9 / Q9) ───────

LENS_SKILL = "traitprint-position-lens"


class TestPositionLensSkill:
    """The lens-curation workflow pack and its MCP prompt wrapper."""

    def test_registered_and_loadable(self) -> None:
        assert LENS_SKILL in SKILL_NAMES
        fm, body = load_skill(LENS_SKILL)
        assert fm["name"] == LENS_SKILL
        assert body.strip()

    def test_prompt_serves_the_live_lens_body(self, store: VaultStore) -> None:
        # The MCP `position_lens` prompt is a thin wrapper over the SKILL.md,
        # so the served text must contain the canonical skill body verbatim.
        server = create_server(store)
        got = asyncio.run(server.get_prompt("position_lens", {}))
        text = got.messages[0].content.text  # type: ignore[union-attr]
        assert text.strip()
        assert skill_body(LENS_SKILL) in text

    def test_teaches_the_curation_workflow(self) -> None:
        body = skill_body(LENS_SKILL)
        # salience discipline (the 3-level vocabulary)
        assert "core" in body
        assert "supporting" in body
        assert "suppressed" in body
        # the cap and the reserved keyword
        assert "5 lenses" in body or "five lenses" in body
        # override rule — a lens never invents a fact
        assert "never" in body.lower()
        # the CLI write surface
        assert "traitprint vault lens add" in body
        assert "traitprint vault lens set-default" in body

    def test_writes_route_through_staged_machinery(self) -> None:
        body = skill_body(LENS_SKILL)
        # local: direct CLI; cloud: staged via vault_propose (never direct)
        assert "vault_propose" in body
        assert "add_lens" in body


# ── (g) The story/profile quality skills + MCP prompts ───────────────

DEEPEN_SKILL = "traitprint-deepen-story"
IMPROVE_SKILL = "traitprint-improve-profile"


class TestDeepenStorySkill:
    """The one-story cross-examination protocol (deepen_story prompt)."""

    def test_registered_and_loadable(self) -> None:
        assert DEEPEN_SKILL in SKILL_NAMES
        fm, body = load_skill(DEEPEN_SKILL)
        assert fm["name"] == DEEPEN_SKILL
        assert body.strip()

    def test_target_selection_uses_the_audit(self) -> None:
        body = skill_body(DEEPEN_SKILL)
        assert "traitprint vault audit --json" in body
        assert "story_scores" in body
        assert "signature" in body  # weakest signature story first

    def test_cross_examination_thrusts_present(self) -> None:
        body = skill_body(DEEPEN_SKILL)
        # a metric needs a baseline/denominator, and a source
        assert "baseline or denominator" in body
        assert "unsourced percentage" in body.lower()
        # attribution split — you vs the team, and the seat held
        assert "seat" in body
        # the counterfactual
        assert "without you" in body
        # scope anchors
        assert "Team size, data scale, duration, stakes" in body
        # honest outcome labels — a real learning beats an inflated win
        for label in ("`win`", "`failure`", "`learning`"):
            assert label in body
        assert "inflated win" in body
        # lesson extraction + skill pairing
        assert "Lesson" in body
        assert "skill_ids" in body

    def test_changes_stage_through_proposals_and_report_delta(self) -> None:
        body = skill_body(DEEPEN_SKILL)
        assert "traitprint proposals add --kind update_story" in body
        assert "vault_propose" in body  # the hosted staging path
        assert "never a silent write" in body
        assert "never approve on their behalf" in body
        # closes with the re-audit + score delta
        assert "delta" in body.lower()


class TestImproveProfileSkill:
    """Vault-wide triage: rank by leverage, route, re-audit
    (improve_profile prompt)."""

    def test_registered_and_loadable(self) -> None:
        assert IMPROVE_SKILL in SKILL_NAMES
        fm, body = load_skill(IMPROVE_SKILL)
        assert fm["name"] == IMPROVE_SKILL
        assert body.strip()

    def test_gathers_state_from_the_read_surface(self) -> None:
        body = skill_body(IMPROVE_SKILL)
        assert "traitprint doctor" in body
        assert "traitprint vault audit --json" in body
        assert "traitprint vault show --json" in body
        # the hosted (shell-less) alternative + market signal
        assert "get_profile_summary" in body
        assert "jobs_match" in body
        assert "missing_skills" in body

    def test_leverage_heuristic_is_encoded_in_order(self) -> None:
        body = skill_body(IMPROVE_SKILL)
        rungs = (
            "contradicts itself undermines everything",  # 1 disputes/dangling
            "zero evidencing",                           # 2 unbacked expert claims
            "traitprint-deepen-story",                   # 3 weak signature stories
            "traitprint-mine-story-gaps",                # 4 story-less roles
            "cheapest win",                              # 5 missing_skills labeling
            "Stale basics or headline",                  # 6 stale identity
            "traitprint-position-lens",                  # 7 lens coverage
        )
        positions = [body.find(r) for r in rungs]
        missing = [r for r, p in zip(rungs, positions, strict=True) if p == -1]
        assert not missing, f"missing rung(s): {missing}"
        assert positions == sorted(positions), (
            "leverage rungs are out of order — the ranking heuristic must "
            "read the same for every agent"
        )

    def test_presents_payoff_and_routes_through_channels(self) -> None:
        body = skill_body(IMPROVE_SKILL)
        assert "payoff" in body
        assert "traitprint proposals add" in body
        assert "vault_propose" in body
        assert "never a silent write" in body
