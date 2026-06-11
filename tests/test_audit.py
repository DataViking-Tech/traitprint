"""Tests for the vault-level coherence audit and ``vault audit`` CLI command."""

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


def _strong_story(**kwargs: object) -> StorySchema:
    base: dict[str, object] = {
        "title": "Migration",
        "situation": "Redshift costs were ballooning on growing pipeline volume.",
        "task": "I led the migration to BigQuery with no pipeline downtime.",
        "action": "I designed dual-writes and cut the pipelines over carefully.",
        "result": "Cut warehouse spend 45 percent with zero downtime in six weeks.",
    }
    base.update(kwargs)
    return StorySchema(**base)  # type: ignore[arg-type]


def _coherent_vault() -> VaultSchema:
    skill = SkillSchema(name="Python", category="technical", proficiency=5)
    exp = ExperienceSchema(
        title="Staff Engineer",
        company="Acme",
        start_date="2020-01",
        description="Led the data platform.",
        accomplishments=["Cut warehouse spend"],
        skill_ids=[skill.id],
    )
    story = _strong_story(skill_ids=[skill.id], experience_id=exp.id)
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


def _codes(vault: VaultSchema) -> set[str]:
    return {f.code for f in audit_vault(vault).findings}


# ── Pure audit logic ────────────────────────────────────────────────


class TestAuditVault:
    def test_coherent_vault_has_no_critical_findings(self) -> None:
        report = audit_vault(_coherent_vault())
        assert not [f for f in report.findings if f.severity == "critical"]

    def test_report_carries_story_scores_and_overall(self) -> None:
        report = audit_vault(_coherent_vault())
        assert len(report.story_scores) == 1
        assert report.story_scores[0].label in (
            "Polished", "Strong", "Solid", "Draft"
        )
        assert report.overall_coherence is not None
        assert 0.0 <= report.overall_coherence <= 1.0

    def test_no_stories_means_no_overall(self) -> None:
        v = VaultSchema(
            profile=ProfileSchema(headline="h", summary="s"),
            skills=[SkillSchema(name="Go", category="x", proficiency=3)],
            experiences=[
                ExperienceSchema(title="Eng", description="d", accomplishments=["x"])
            ],
        )
        assert audit_vault(v).overall_coherence is None

    def test_empty_vault_flags_emptiness_major(self) -> None:
        findings = audit_vault(VaultSchema()).findings
        f = next(f for f in findings if f.code == "vault.empty")
        assert f.severity == "major"

    def test_unsupported_strong_skill_is_major(self) -> None:
        v = VaultSchema(
            profile=ProfileSchema(headline="h", summary="s"),
            skills=[SkillSchema(name="Rust", category="technical", proficiency=5)],
            experiences=[ExperienceSchema(title="Eng", description="d")],
        )
        f = next(
            f for f in audit_vault(v).findings if f.code == "skill.unsupported_strength"
        )
        assert f.severity == "major"

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
        assert "skill.unsupported_strength" not in _codes(v)

    def test_incomplete_star_produces_critical_field_findings(self) -> None:
        v = VaultSchema(
            profile=ProfileSchema(headline="h", summary="s"),
            skills=[SkillSchema(name="Go", category="technical", proficiency=3)],
            experiences=[ExperienceSchema(title="Eng", description="d")],
            stories=[StorySchema(title="Half", situation="only the situation here")],
        )
        findings = audit_vault(v).findings
        critical_codes = {f.code for f in findings if f.severity == "critical"}
        assert {"story.task", "story.action", "story.result"} <= critical_codes

    def test_dangling_skill_reference_is_major_warning(self) -> None:
        # Contract rule 2 / D10: dangling refs are warnings locally — they
        # must surface, but never as push-blocking critical findings.
        ghost = SkillSchema(name="ghost", category="x", proficiency=1)
        story = _strong_story(skill_ids=[ghost.id])
        v = VaultSchema(
            profile=ProfileSchema(headline="h", summary="s"),
            experiences=[ExperienceSchema(title="Eng", description="d")],
            stories=[story],
        )
        f = next(f for f in audit_vault(v).findings if f.code == "story.dangling_skill")
        assert f.severity == "major"

    def test_dangling_experience_reference_is_major_warning(self) -> None:
        ghost = ExperienceSchema(title="ghost")
        story = _strong_story(experience_id=ghost.id)
        v = VaultSchema(
            profile=ProfileSchema(headline="h", summary="s"),
            skills=[SkillSchema(name="Go", category="x", proficiency=3)],
            stories=[story],
        )
        f = next(
            f for f in audit_vault(v).findings if f.code == "story.dangling_experience"
        )
        assert f.severity == "major"

    def test_philosophy_without_evidence_is_major(self) -> None:
        v = _coherent_vault()
        v.philosophies[0].evidence_story_ids = []
        f = next(
            f for f in audit_vault(v).findings if f.code == "philosophy.no_evidence"
        )
        assert f.severity == "major"

    def test_philosophy_dangling_evidence_is_major_warning(self) -> None:
        ghost = _strong_story(title="ghost")
        v = _coherent_vault()
        v.philosophies[0].evidence_story_ids = [ghost.id]
        f = next(
            f
            for f in audit_vault(v).findings
            if f.code == "philosophy.dangling_evidence"
        )
        assert f.severity == "major"

    def test_dangling_reference_never_critical(self) -> None:
        """No dangling-reference finding may reach critical (D10/rule 2)."""
        ghost_skill = SkillSchema(name="ghost", category="x", proficiency=1)
        ghost_exp = ExperienceSchema(title="ghost")
        ghost_story = _strong_story(title="ghost")
        story = _strong_story(
            skill_ids=[ghost_skill.id], experience_id=ghost_exp.id
        )
        v = VaultSchema(
            profile=ProfileSchema(headline="h", summary="s"),
            stories=[story],
            philosophies=[
                PhilosophySchema(
                    title="P", description="d", evidence_story_ids=[ghost_story.id]
                )
            ],
        )
        dangling = [
            f for f in audit_vault(v).findings if "dangling" in f.code
        ]
        assert len(dangling) == 3
        assert all(f.severity == "major" for f in dangling)

    def test_experience_without_story_is_major(self) -> None:
        v = _coherent_vault()
        v.stories[0].experience_id = None
        f = next(
            f for f in audit_vault(v).findings if f.code == "experience.no_story"
        )
        assert f.severity == "major"

    def test_experience_dangling_skill_reference_is_major_warning(self) -> None:
        # Contract rule 2 (revision 1.1) / D10: experience skill_ids follow
        # the same Layer 1 rules as story skill_ids — warn, never block.
        ghost = SkillSchema(name="ghost", category="x", proficiency=1)
        v = _coherent_vault()
        v.experiences[0].skill_ids = [ghost.id]
        f = next(
            f
            for f in audit_vault(v).findings
            if f.code == "experience.dangling_skill"
        )
        assert f.severity == "major"
        assert f.item_id == str(v.experiences[0].id)

    def test_experience_without_skills_is_minor(self) -> None:
        # Mirrors story.no_skills: a role with zero linked skills is a gap.
        v = _coherent_vault()
        v.experiences[0].skill_ids = []
        f = next(
            f for f in audit_vault(v).findings if f.code == "experience.no_skills"
        )
        assert f.severity == "minor"

    def test_experience_with_linked_skill_not_flagged(self) -> None:
        codes = _codes(_coherent_vault())
        assert "experience.no_skills" not in codes
        assert "experience.dangling_skill" not in codes

    def test_cross_story_contradiction_becomes_finding(self) -> None:
        exp = ExperienceSchema(title="Role")
        s1 = StorySchema(
            title="Lead",
            situation="The org was scaling fast and needed coordination.",
            task="Responsible for delivery of the platform roadmap.",
            action="I led the team of engineers and managed five reports.",
            result="Shipped the platform two weeks early.",
            experience_id=exp.id,
        )
        s2 = StorySchema(
            title="Solo",
            situation="A greenfield prototype needed building quickly.",
            task="Responsible for the prototype end to end.",
            action="I worked independently as the sole developer.",
            result="Delivered the prototype in three weeks.",
            experience_id=exp.id,
        )
        v = VaultSchema(
            profile=ProfileSchema(headline="h", summary="s"),
            experiences=[exp],
            stories=[s1, s2],
        )
        f = next(f for f in audit_vault(v).findings if f.code == "story.contradiction")
        assert f.severity == "critical"
        assert f.related_id is not None

    def test_philosophy_tensions_surface_as_insights(self) -> None:
        v = VaultSchema(
            profile=ProfileSchema(headline="h", summary="s"),
            skills=[SkillSchema(name="Go", category="x", proficiency=3)],
            philosophies=[
                PhilosophySchema(
                    title="Empower",
                    description="I empower the team and trust autonomous ownership.",
                    category=PhilosophyCategory.LEADERSHIP,
                ),
                PhilosophySchema(
                    title="Command",
                    description="Hierarchy, top-down control, and clear oversight.",
                    category=PhilosophyCategory.LEADERSHIP,
                ),
            ],
        )
        report = audit_vault(v)
        assert len(report.tensions) >= 1
        # Tensions are NOT findings — they never appear in the findings list.
        assert not any(f.code.startswith("tension") for f in report.findings)

    def test_findings_sorted_most_severe_first(self) -> None:
        v = VaultSchema(
            profile=ProfileSchema(),
            skills=[SkillSchema(name="Go", category="x", proficiency=5)],
            experiences=[ExperienceSchema(title="Eng")],
            stories=[StorySchema(title="Half", situation="only this here please")],
        )
        ranks = [severity_rank(f.severity) for f in audit_vault(v).findings]
        assert ranks == sorted(ranks, reverse=True)


class TestSummarize:
    def test_counts(self) -> None:
        findings = [
            Finding("critical", "a", "s", "m"),
            Finding("major", "b", "s", "m"),
            Finding("major", "c", "s", "m"),
            Finding("minor", "d", "s", "m"),
        ]
        assert summarize(findings) == {
            "critical": 1,
            "major": 2,
            "minor": 1,
            "total": 4,
        }

    def test_empty(self) -> None:
        assert summarize([]) == {"critical": 0, "major": 0, "minor": 0, "total": 0}


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

    def test_shows_story_coherence_and_summary(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        d = _seed(tmp_path, _coherent_vault())
        result = runner.invoke(cli, ["--path", str(d), "vault", "audit"])
        assert result.exit_code == 0
        assert "Story coherence:" in result.output
        assert "Summary:" in result.output

    def test_lists_findings_with_tags(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        v = _coherent_vault()
        v.philosophies[0].evidence_story_ids = []  # major finding
        d = _seed(tmp_path, v)
        result = runner.invoke(cli, ["--path", str(d), "vault", "audit"])
        assert "[major]" in result.output
        assert "philosophies:" in result.output

    def test_tensions_rendered(self, runner: CliRunner, tmp_path: Path) -> None:
        v = VaultSchema(
            profile=ProfileSchema(headline="h", summary="s"),
            skills=[SkillSchema(name="Go", category="x", proficiency=3)],
            philosophies=[
                PhilosophySchema(
                    title="Empower",
                    description="I empower the team and trust autonomous ownership.",
                    category=PhilosophyCategory.LEADERSHIP,
                ),
                PhilosophySchema(
                    title="Command",
                    description="Hierarchy, top-down control, and clear oversight.",
                    category=PhilosophyCategory.LEADERSHIP,
                ),
            ],
        )
        d = _seed(tmp_path, v)
        result = runner.invoke(cli, ["--path", str(d), "vault", "audit"])
        assert "Philosophy tensions" in result.output

    def test_json_output_has_full_report(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        d = _seed(tmp_path, _coherent_vault())
        result = runner.invoke(cli, ["--path", str(d), "vault", "audit", "--json"])
        payload = json.loads(result.output)
        assert {"findings", "story_scores", "tensions", "summary"} <= set(payload)
        assert payload["story_scores"][0]["label"] in (
            "Polished", "Strong", "Solid", "Draft"
        )

    def test_severity_filter_hides_minor(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        v = _coherent_vault()
        v.stories.append(
            _strong_story(title="Orphan", experience_id=v.experiences[0].id)
        )  # story.no_skills minor
        d = _seed(tmp_path, v)
        result = runner.invoke(
            cli,
            ["--path", str(d), "vault", "audit", "--json", "--severity", "major"],
        )
        payload = json.loads(result.output)
        assert all(f["severity"] != "minor" for f in payload["findings"])

    def test_strict_exits_nonzero_on_major(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        v = _coherent_vault()
        v.philosophies[0].evidence_story_ids = []
        d = _seed(tmp_path, v)
        result = runner.invoke(cli, ["--path", str(d), "vault", "audit", "--strict"])
        assert result.exit_code == 1

    def test_strict_json_exits_nonzero(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        v = _coherent_vault()
        v.philosophies[0].evidence_story_ids = []
        d = _seed(tmp_path, v)
        result = runner.invoke(
            cli, ["--path", str(d), "vault", "audit", "--json", "--strict"]
        )
        assert result.exit_code == 1
        json.loads(result.output)  # still valid JSON
