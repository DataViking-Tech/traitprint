"""Tests for the story-bank working-dir importer (vault import-story-bank)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from traitprint.cli import cli
from traitprint.git_ops import commit, init_repo
from traitprint.importers.story_bank import (
    detect_and_plan,
    parse_story_bank,
    profile_proposal,
    story_proposal_payload,
)
from traitprint.proposals import ProposalStore, apply_proposal
from traitprint.schema import (
    ExperienceSchema,
    ProfileSchema,
    SkillSchema,
    VaultSchema,
)
from traitprint.vault import VaultStore
from traitprint.vault_io import parse_story_body

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "story_bank_workdir"

CANONICAL = (FIXTURE_DIR / "interview-prep" / "story-bank.md").read_text()
DRIFT = (FIXTURE_DIR / "interview-prep" / "drift-variant.md").read_text()


def _vault() -> VaultSchema:
    python = SkillSchema(name="Python", proficiency=4, category="technical")
    mentoring = SkillSchema(name="Mentoring", proficiency=3, category="soft")
    acme = ExperienceSchema(title="Staff Engineer", company="Acme")
    return VaultSchema(
        profile=ProfileSchema(display_name="Old Name"),
        skills=[python, mentoring],
        experiences=[acme],
    )


class TestParseStoryBank:
    def test_canonical_blocks(self) -> None:
        stories = parse_story_bank(CANONICAL, origin="story-bank.md")
        assert [s.title for s in stories] == [
            "Warehouse migration under deadline",
            "The silent pipeline failure",
        ]
        first = stories[0]
        assert first.theme == "cost"
        assert first.situation.startswith("Redshift costs")
        # Multi-line Action continuation joined.
        assert "column by column" in first.action
        assert first.lesson.startswith("Dual-writes")
        assert first.source == "Staff Engineer — Acme"
        assert first.tags == ["cost", "migration", "Python"]
        assert first.origin == "story-bank.md"

    def test_drift_variant_plain_labels_and_bullets(self) -> None:
        stories = parse_story_bank(DRIFT)
        assert [s.title for s in stories] == ["Mentoring a struggling junior"]
        story = stories[0]
        assert story.theme == ""
        assert "one-day" in story.action  # bullet continuation
        assert story.lesson == "Confidence compounds faster than skill."
        assert story.tags == ["mentoring", "leadership"]

    def test_blocks_without_star_fields_skipped(self) -> None:
        stories = parse_story_bank(DRIFT)
        assert all(s.title != "Empty block that should be skipped" for s in stories)

    def test_empty_input(self) -> None:
        assert parse_story_bank("") == []
        assert parse_story_bank("# just a doc\n\nprose") == []


class TestStoryPayload:
    def test_tags_split_into_skill_ids_and_theme_tags(self) -> None:
        vault = _vault()
        parsed = parse_story_bank(CANONICAL)[0]
        payload = story_proposal_payload(parsed, vault)
        python_id = str(vault.skills[0].id)
        # 'Python' resolves to the vault skill; cost/migration stay themes.
        assert payload["skill_ids"] == [python_id]
        assert payload["theme_tags"] == ["cost", "migration"]

    def test_source_resolves_experience(self) -> None:
        vault = _vault()
        parsed = parse_story_bank(CANONICAL)[0]
        payload = story_proposal_payload(parsed, vault)
        assert payload["experience_id"] == str(vault.experiences[0].id)

    def test_unresolvable_source_left_unlinked(self) -> None:
        vault = _vault()
        parsed = parse_story_bank(CANONICAL)[0]
        parsed.source = "Unknown Role — Nowhere Corp"
        payload = story_proposal_payload(parsed, vault)
        assert "experience_id" not in payload

    def test_body_round_trips_star_and_lesson(self) -> None:
        vault = _vault()
        parsed = parse_story_bank(CANONICAL)[0]
        payload = story_proposal_payload(parsed, vault)
        star = parse_story_body(payload["body"])
        assert star["situation"].startswith("Redshift costs")
        assert star["lesson"].startswith("Dual-writes")

    def test_payload_applies_cleanly_as_proposal(self) -> None:
        # The staged payload must survive the real proposal apply path.
        from traitprint.proposals import ProposalSchema

        vault = _vault()
        parsed = parse_story_bank(CANONICAL)[0]
        payload = story_proposal_payload(parsed, vault)
        apply_proposal(vault, ProposalSchema(kind="add_story", payload=payload))
        assert vault.stories[0].title == "Warehouse migration under deadline"
        assert vault.stories[0].lesson.startswith("Dual-writes")
        assert vault.stories[0].theme_tags == ["cost", "migration"]


class TestProfileProposal:
    def test_maps_keys_to_basics(self) -> None:
        raw = (FIXTURE_DIR / "config" / "profile.yml").read_text()
        payload, rationale = profile_proposal(raw)
        assert payload == {
            "basics": {
                "name": "Jordan Vance",
                "email": "jordan@example.com",
                "location": "Portland, OR",
                "label": "Data Platform Engineer",
                "summary": "Eight years building boring, reliable data platforms.",
            }
        }
        # Archetypes have no lens proposal kind — stashed in the rationale.
        assert "Data Platform Lead" in rationale
        assert "Senior Data Engineer" in rationale

    def test_invalid_yaml_returns_none(self) -> None:
        payload, _ = profile_proposal(":\n  - not valid: [")
        assert payload is None

    def test_empty_doc_returns_none(self) -> None:
        payload, _ = profile_proposal("just: {}\n")
        assert payload is None


class TestDetectAndPlan:
    def test_full_fixture_dir(self) -> None:
        plan = detect_and_plan(FIXTURE_DIR, _vault())
        # 2 canonical + 1 drift-variant story.
        assert len(plan.stories) == 3
        assert plan.profile_payload is not None
        assert plan.has_cv is True

    def test_rejects_non_workdir(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="does not look like"):
            detect_and_plan(tmp_path, _vault())


# ── CLI ─────────────────────────────────────────────────────────────


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    d = tmp_path / "vault"
    d.mkdir()
    init_repo(d)
    VaultStore(d).save(_vault())
    commit(d, "seed")
    return d


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    dest = tmp_path / "workdir"
    shutil.copytree(FIXTURE_DIR, dest)
    return dest


class TestCliImportStoryBank:
    def test_stages_proposals(
        self, runner: CliRunner, vault_dir: Path, workdir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "import-story-bank", str(workdir)],
        )
        assert result.exit_code == 0, result.output
        assert result.output.count("[ok] add_story") == 3
        assert "[ok] update_profile" in result.output
        assert "import-resume" in result.output  # cv.md pointer
        assert "Summary: staged 4, errors 0" in result.output

        loaded, issues = ProposalStore(vault_dir).load_all()
        assert issues == []
        assert len(loaded) == 4
        assert all(lp.proposal.source == "story-bank-import" for lp in loaded)
        # Nothing touched the vault itself.
        assert VaultStore(vault_dir).load().stories == []

    def test_dry_run_stages_nothing(
        self, runner: CliRunner, vault_dir: Path, workdir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path", str(vault_dir),
                "vault", "import-story-bank", str(workdir), "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert result.output.count("[plan] add_story") == 3
        loaded, _ = ProposalStore(vault_dir).load_all()
        assert loaded == []

    def test_dry_run_json_plan(
        self, runner: CliRunner, vault_dir: Path, workdir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path", str(vault_dir),
                "vault", "import-story-bank", str(workdir),
                "--dry-run", "--json",
            ],
        )
        payload = json.loads(result.output)
        assert len(payload["stories"]) == 3
        assert payload["profile"]["basics"]["name"] == "Jordan Vance"
        assert payload["has_cv"] is True

    def test_non_workdir_errors(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "import-story-bank", str(empty)],
        )
        assert result.exit_code == 1
        assert "does not look like" in result.output

    def test_stories_only_dir(
        self, runner: CliRunner, vault_dir: Path, workdir: Path
    ) -> None:
        (workdir / "config" / "profile.yml").unlink()
        (workdir / "cv.md").unlink()
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "import-story-bank", str(workdir)],
        )
        assert result.exit_code == 0, result.output
        assert "update_profile" not in result.output
        assert "Summary: staged 3, errors 0" in result.output
