"""Tests for the narrative-coherence audit and ``vault audit`` CLI command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from traitprint.audit import (
    STRONG_PROFICIENCY,
    Finding,
    audit_vault,
    severity_rank,
    summarize,
)
from traitprint.cli import cli
from traitprint.git_ops import commit, init_repo
from traitprint.schema import (
    EducationSchema,
    ExperienceSchema,
    PhilosophyCategory,
    PhilosophySchema,
    ProfileSchema,
    SkillSchema,
    StorySchema,
    VaultSchema,
)
from traitprint.vault import VaultStore

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _complete_story(**kwargs: object) -> StorySchema:
    base: dict[str, object] = {
        "title": "A Story",
        "situation": "It was broken.",
        "task": "Fix it.",
        "action": "Fixed it.",
        "result": "Cut latency 40 percent.",
    }
    base.update(kwargs)
    return StorySchema(**base)  # type: ignore[arg-type]


def _coherent_vault() -> VaultSchema:
    """A small but internally consistent vault with no findings."""
    skill = SkillSchema(name="Python", category="technical", proficiency=9)
    exp = ExperienceSchema(
        title="Staff Engineer",
        company="Acme",
        start_date="2020-01",
        description="Led the data platform.",
        accomplishments=["Cut warehouse spend"],
    )
    story = _complete_story(
        title="Migration",
        skill_ids=[skill.id],
        experience_id=exp.id,
    )
    phil = PhilosophySchema(
        title="Delegation",
        description="Trust senior engineers.",
        category=PhilosophyCategory.LEADERSHIP,
        evidence_story_ids=[story.id],
    )
    return VaultSchema(
        profile=ProfileSchema(
            display_name="W", headline="Engineer", summary="A decade of data."
        ),
        skills=[skill],
        experiences=[exp],
        stories=[story],
        philosophies=[phil],
        education=[EducationSchema(institution="State U", degree="BS")],
    )


# ── Pure audit logic ────────────────────────────────────────────────


class TestAuditVault:
    def test_coherent_vault_has_no_findings(self) -> None:
        assert audit_vault(_coherent_vault()) == []

    def test_empty_vault_flags_emptiness(self) -> None:
        codes = {f.code for f in audit_vault(VaultSchema())}
        assert "vault.empty" in codes

    def test_unsupported_strong_skill(self) -> None:
        v = VaultSchema(
            profile=ProfileSchema(headline="h", summary="s"),
            skills=[SkillSchema(name="Rust", category="technical", proficiency=9)],
            experiences=[ExperienceSchema(title="Eng", description="d")],
        )
        findings = audit_vault(v)
        codes = {f.code for f in findings}
        assert "skill.unsupported_strength" in codes
        # The experience has no story, which is its own warning.
        assert "experience.no_story" in codes

    def test_weak_skill_without_evidence_not_flagged(self) -> None:
        v = VaultSchema(
            profile=ProfileSchema(headline="h", summary="s"),
            skills=[
                SkillSchema(
                    name="Bash",
                    category="technical",
                    proficiency=STRONG_PROFICIENCY - 1,
                )
            ],
            experiences=[
                ExperienceSchema(title="Eng", description="d", accomplishments=["x"])
            ],
        )
        codes = {f.code for f in audit_vault(v)}
        assert "skill.unsupported_strength" not in codes

    def test_incomplete_star_is_error(self) -> None:
        v = VaultSchema(
            profile=ProfileSchema(headline="h", summary="s"),
            skills=[SkillSchema(name="Go", category="technical", proficiency=4)],
            experiences=[ExperienceSchema(title="Eng", description="d")],
            stories=[StorySchema(title="Half", situation="only this")],
        )
        f = next(f for f in audit_vault(v) if f.code == "story.incomplete_star")
        assert f.severity == "error"
        assert "situation" not in f.message  # situation is present
        assert "task" in f.message and "action" in f.message and "result" in f.message

    def test_dangling_skill_reference_is_error(self) -> None:
        ghost_skill = SkillSchema(name="ghost", category="x", proficiency=1)
        story = _complete_story(title="S", skill_ids=[ghost_skill.id])
        v = VaultSchema(
            profile=ProfileSchema(headline="h", summary="s"),
            experiences=[ExperienceSchema(title="Eng", description="d")],
            stories=[story],
        )
        codes = {(f.code, f.severity) for f in audit_vault(v)}
        assert ("story.dangling_skill", "error") in codes

    def test_dangling_experience_reference_is_error(self) -> None:
        ghost = ExperienceSchema(title="ghost")
        story = _complete_story(title="S", experience_id=ghost.id)
        v = VaultSchema(
            profile=ProfileSchema(headline="h", summary="s"),
            skills=[SkillSchema(name="Go", category="x", proficiency=3)],
            stories=[story],
        )
        codes = {(f.code, f.severity) for f in audit_vault(v)}
        assert ("story.dangling_experience", "error") in codes

    def test_philosophy_without_evidence_is_warning(self) -> None:
        v = _coherent_vault()
        v.philosophies[0].evidence_story_ids = []
        f = next(
            f for f in audit_vault(v) if f.code == "philosophy.no_evidence"
        )
        assert f.severity == "warning"

    def test_philosophy_dangling_evidence_is_error(self) -> None:
        ghost = _complete_story(title="ghost")
        v = _coherent_vault()
        v.philosophies[0].evidence_story_ids = [ghost.id]
        codes = {(f.code, f.severity) for f in audit_vault(v)}
        assert ("philosophy.dangling_evidence", "error") in codes

    def test_experience_without_story_is_warning(self) -> None:
        v = _coherent_vault()
        # Detach the story from its experience.
        v.stories[0].experience_id = None
        f = next(f for f in audit_vault(v) if f.code == "experience.no_story")
        assert f.severity == "warning"

    def test_profile_missing_headline_and_summary(self) -> None:
        v = _coherent_vault()
        v.profile.headline = ""
        v.profile.summary = ""
        codes = {f.code for f in audit_vault(v)}
        assert {"profile.no_headline", "profile.no_summary"} <= codes

    def test_findings_sorted_most_severe_first(self) -> None:
        v = VaultSchema(
            profile=ProfileSchema(),
            skills=[SkillSchema(name="Go", category="x", proficiency=9)],
            experiences=[ExperienceSchema(title="Eng")],
            stories=[StorySchema(title="Half", situation="only")],
        )
        findings = audit_vault(v)
        ranks = [severity_rank(f.severity) for f in findings]
        assert ranks == sorted(ranks, reverse=True)


class TestSummarize:
    def test_counts(self) -> None:
        findings = [
            Finding("error", "a", "s", "m"),
            Finding("warning", "b", "s", "m"),
            Finding("warning", "c", "s", "m"),
            Finding("info", "d", "s", "m"),
        ]
        assert summarize(findings) == {
            "error": 1,
            "warning": 2,
            "info": 1,
            "total": 4,
        }

    def test_empty(self) -> None:
        assert summarize([]) == {"error": 0, "warning": 0, "info": 0, "total": 0}


# ── CLI: traitprint vault audit ─────────────────────────────────────


def _seed(tmp_path: Path, vault: VaultSchema) -> Path:
    d = tmp_path / "vault"
    d.mkdir()
    init_repo(d)
    store = VaultStore(d)
    store.save(vault)
    commit(d, "seed")
    return d


class TestAuditCLI:
    def test_no_vault(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(cli, ["--path", str(tmp_path / "x"), "vault", "audit"])
        assert result.exit_code == 0
        assert "No vault found" in result.output

    def test_clean_vault_reports_clear(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        d = _seed(tmp_path, _coherent_vault())
        result = runner.invoke(cli, ["--path", str(d), "vault", "audit"])
        assert result.exit_code == 0
        assert "No coherence issues" in result.output

    def test_human_output_lists_findings(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        v = _coherent_vault()
        v.philosophies[0].evidence_story_ids = []
        d = _seed(tmp_path, v)
        result = runner.invoke(cli, ["--path", str(d), "vault", "audit"])
        assert result.exit_code == 0
        assert "[warn]" in result.output
        assert "philosophies:" in result.output
        assert "Summary:" in result.output

    def test_json_output(self, runner: CliRunner, tmp_path: Path) -> None:
        v = _coherent_vault()
        v.philosophies[0].evidence_story_ids = []
        d = _seed(tmp_path, v)
        result = runner.invoke(cli, ["--path", str(d), "vault", "audit", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert "findings" in payload and "summary" in payload
        codes = {f["code"] for f in payload["findings"]}
        assert "philosophy.no_evidence" in codes

    def test_severity_filter_hides_info(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        v = _coherent_vault()
        # Add an info-only issue (story with no skills) and a warning.
        v.stories.append(
            _complete_story(title="Orphan", experience_id=v.experiences[0].id)
        )
        d = _seed(tmp_path, v)
        result = runner.invoke(
            cli,
            ["--path", str(d), "vault", "audit", "--json", "--severity", "warning"],
        )
        payload = json.loads(result.output)
        severities = {f["severity"] for f in payload["findings"]}
        assert "info" not in severities

    def test_strict_exits_nonzero_on_warning(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        v = _coherent_vault()
        v.philosophies[0].evidence_story_ids = []
        d = _seed(tmp_path, v)
        result = runner.invoke(
            cli, ["--path", str(d), "vault", "audit", "--strict"]
        )
        assert result.exit_code == 1

    def test_strict_exits_zero_when_clean(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        d = _seed(tmp_path, _coherent_vault())
        result = runner.invoke(
            cli, ["--path", str(d), "vault", "audit", "--strict"]
        )
        assert result.exit_code == 0
