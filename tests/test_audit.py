"""Tests for the vault-level coherence audit and ``vault audit`` CLI command."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from traitprint.audit import (
    STALE_DAYS_DEFAULT,
    STRONG_PROFICIENCY,
    Finding,
    audit_vault,
    compute_phase,
    freshness_findings,
    severity_rank,
    summarize,
)
from traitprint.cli import cli
from traitprint.git_ops import commit, init_repo
from traitprint.schema import (
    BulletSchema,
    EducationSchema,
    ExperienceSchema,
    LensSchema,
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

    def test_bullet_dangling_story_is_major_warning(self) -> None:
        # Contract rule 11 (revision 1.7): bullet cross-references follow the
        # same Layer 1 rules — the CLI audit must report them, not only the
        # MCP dispute layer.
        ghost = StorySchema(title="ghost", situation="s")
        v = _coherent_vault()
        bullet = BulletSchema(text="Cut spend 45%", story_ids=[ghost.id])
        v.experiences[0].bullets = [bullet]
        f = next(
            f for f in audit_vault(v).findings if f.code == "bullet.dangling_story"
        )
        assert f.severity == "major"
        assert f.item_id == str(bullet.id)

    def test_bullet_dangling_skill_is_major_warning(self) -> None:
        ghost = SkillSchema(name="ghost", category="x", proficiency=1)
        v = _coherent_vault()
        bullet = BulletSchema(text="Shipped the platform", skill_ids=[ghost.id])
        v.experiences[0].bullets = [bullet]
        f = next(
            f for f in audit_vault(v).findings if f.code == "bullet.dangling_skill"
        )
        assert f.severity == "major"
        assert f.item_id == str(bullet.id)

    def test_resolving_bullet_refs_not_flagged(self) -> None:
        v = _coherent_vault()
        v.experiences[0].bullets = [
            BulletSchema(
                text="Shipped the migration",
                story_ids=[v.stories[0].id],
                skill_ids=[v.skills[0].id],
            )
        ]
        codes = _codes(v)
        assert "bullet.dangling_story" not in codes
        assert "bullet.dangling_skill" not in codes

    def test_bulleted_experience_is_not_thin(self) -> None:
        # Bullets supersede accomplishments as structured role content: a role
        # migrated to bullets-only must not read as title-and-date-only.
        v = _coherent_vault()
        v.experiences[0].description = ""
        v.experiences[0].accomplishments = []
        v.experiences[0].bullets = [BulletSchema(text="Owned the data platform")]
        assert "experience.thin" not in _codes(v)

    def test_empty_experience_is_still_thin(self) -> None:
        v = _coherent_vault()
        v.experiences[0].description = ""
        v.experiences[0].accomplishments = []
        v.experiences[0].bullets = []
        assert "experience.thin" in _codes(v)

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


class TestPhaseAndFreshness:
    """traitprint doctor internals: compute_phase + freshness findings."""

    NOW = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)

    def _aged(self, days: int) -> datetime:
        return self.NOW - timedelta(days=days)

    def _established_vault(self, age_days: int = 0) -> VaultSchema:
        stamp = self._aged(age_days)
        skills = [
            SkillSchema(
                name=f"Skill{i}",
                category="technical",
                proficiency=3,
                created_at=stamp,
                updated_at=stamp,
            )
            for i in range(5)
        ]
        exp = ExperienceSchema(
            title="Staff Engineer",
            company="Acme",
            start_date="2020-01",
            description="d",
            created_at=stamp,
            updated_at=stamp,
        )
        stories = [
            _strong_story(title=f"Story {i}", created_at=stamp, updated_at=stamp)
            for i in range(3)
        ]
        return VaultSchema(
            profile=ProfileSchema(headline="h", summary="s"),
            skills=skills,
            experiences=[exp],
            stories=stories,
        )

    def test_first_run_phase(self) -> None:
        report = compute_phase(VaultSchema(), now=self.NOW)
        assert report.phase == "first-run"
        assert report.days_since_activity is None

    def test_growing_phase_below_bar(self) -> None:
        v = VaultSchema(
            skills=[SkillSchema(name="Go", category="x", proficiency=3)],
        )
        assert compute_phase(v, now=self.NOW).phase == "growing"

    def test_established_phase(self) -> None:
        report = compute_phase(self._established_vault(age_days=10), now=self.NOW)
        assert report.phase == "established"
        assert report.days_since_activity == 10

    def test_stale_phase_past_threshold(self) -> None:
        report = compute_phase(self._established_vault(age_days=120), now=self.NOW)
        assert report.phase == "stale"

    def test_stale_threshold_configurable(self) -> None:
        v = self._established_vault(age_days=40)
        assert compute_phase(v, now=self.NOW).phase == "established"
        assert compute_phase(v, stale_days=30, now=self.NOW).phase == "stale"

    def test_phase_report_to_dict(self) -> None:
        d = compute_phase(self._established_vault(), now=self.NOW).to_dict()
        assert d["phase"] == "established"
        assert d["counts"] == {"skills": 5, "experiences": 1, "stories": 3}

    def test_stale_story_bank_aggregate_finding(self) -> None:
        v = self._established_vault(age_days=120)
        found = freshness_findings(v, now=self.NOW)
        codes = {f.code for f in found}
        assert "vault.stale_stories" in codes
        f = next(f for f in found if f.code == "vault.stale_stories")
        assert f.severity == "minor"
        assert f.fix_skill == "traitprint-capture-story"
        # Aggregate: exactly one finding regardless of story count.
        assert sum(1 for x in found if x.code == "vault.stale_stories") == 1

    def test_current_experience_stale(self) -> None:
        v = self._established_vault(age_days=120)
        # end_date empty -> current role.
        found = freshness_findings(v, now=self.NOW)
        f = next(f for f in found if f.code == "experience.current_stale")
        assert f.fix_skill == "traitprint-fill-vault"
        assert f.item_id == str(v.experiences[0].id)

    def test_ended_experience_not_flagged_stale(self) -> None:
        v = self._established_vault(age_days=120)
        v.experiences[0].end_date = "2024-06"
        codes = {f.code for f in freshness_findings(v, now=self.NOW)}
        assert "experience.current_stale" not in codes

    def test_strong_skill_with_only_stale_evidence(self) -> None:
        v = self._established_vault(age_days=120)
        v.skills[0].proficiency = 5
        v.stories[0].skill_ids = [v.skills[0].id]
        found = freshness_findings(v, now=self.NOW)
        f = next(f for f in found if f.code == "skill.stale_evidence")
        assert f.fix_skill == "traitprint-mine-story-gaps"

    def test_fresh_evidence_not_flagged(self) -> None:
        v = self._established_vault(age_days=120)
        v.skills[0].proficiency = 5
        fresh_story = _strong_story(
            title="Fresh",
            skill_ids=[v.skills[0].id],
            created_at=self._aged(5),
            updated_at=self._aged(5),
        )
        v.stories.append(fresh_story)
        codes = {f.code for f in freshness_findings(v, now=self.NOW)}
        assert "skill.stale_evidence" not in codes

    def test_skill_without_evidence_not_double_flagged(self) -> None:
        # No evidence at all is skill.unsupported_strength's job.
        v = self._established_vault(age_days=120)
        v.skills[0].proficiency = 5
        codes = {f.code for f in freshness_findings(v, now=self.NOW)}
        assert "skill.stale_evidence" not in codes

    def test_lens_draft_signature_story(self) -> None:
        v = self._established_vault()
        draft = StorySchema(title="Thin", situation="short one")
        v.stories.append(draft)
        v.lenses = [
            LensSchema(
                slug="data-lead",
                name="Data Lead",
                signature_story_ids=[draft.id],
            )
        ]
        found = freshness_findings(v, now=self.NOW)
        f = next(f for f in found if f.code == "lens.draft_signature")
        assert f.fix_skill == "traitprint-draft-star-story"
        assert f.related_id == str(draft.id)

    def test_fresh_vault_has_no_freshness_findings(self) -> None:
        assert freshness_findings(
            self._established_vault(age_days=1), now=self.NOW
        ) == []

    def test_audit_vault_includes_freshness(self) -> None:
        v = self._established_vault(age_days=120)
        report = audit_vault(v, now=self.NOW)
        codes = {f.code for f in report.findings}
        assert "vault.stale_stories" in codes

    def test_finding_to_dict_carries_fix_skill(self) -> None:
        v = self._established_vault(age_days=120)
        f = next(
            x
            for x in freshness_findings(v, now=self.NOW)
            if x.code == "vault.stale_stories"
        )
        assert f.to_dict()["fix_skill"] == "traitprint-capture-story"
        assert STALE_DAYS_DEFAULT == 90


class TestStyleLint:
    """Warning-only style findings (story.buzzword, experience.weak_bullet,
    story.polished_no_lesson) — all at minor severity."""

    def test_buzzword_flagged(self) -> None:
        v = _coherent_vault()
        v.stories[0].action = (
            "I leveraged synergy as a rockstar thought leader to move the needle."
        )
        report = audit_vault(v)
        found = [f for f in report.findings if f.code == "story.buzzword"]
        assert len(found) == 1
        assert found[0].severity == "minor"
        assert "'leveraged'" in found[0].message
        assert found[0].item_id == str(v.stories[0].id)

    def test_clean_story_not_flagged_for_buzzwords(self) -> None:
        assert "story.buzzword" not in _codes(_coherent_vault())

    def test_weak_bullet_vague_phrasing(self) -> None:
        v = _coherent_vault()
        v.experiences[0].accomplishments = [
            "Responsible for maintaining various stuff",
            "Migrated 12 services to Kubernetes, cutting deploy time 40 percent",
        ]
        report = audit_vault(v)
        found = [f for f in report.findings if f.code == "experience.weak_bullet"]
        assert len(found) == 1
        assert found[0].severity == "minor"
        assert "1 of 2" in found[0].message
        assert "vague phrasing" in found[0].message

    def test_weak_bullet_no_lead_verb(self) -> None:
        v = _coherent_vault()
        v.experiences[0].accomplishments = ["Was part of the platform team"]
        report = audit_vault(v)
        found = [f for f in report.findings if f.code == "experience.weak_bullet"]
        assert len(found) == 1
        assert "active verb" in found[0].message

    def test_weak_bullet_no_metric_or_tool(self) -> None:
        v = _coherent_vault()
        v.experiences[0].accomplishments = ["Improved the pipeline reliability"]
        report = audit_vault(v)
        found = [f for f in report.findings if f.code == "experience.weak_bullet"]
        assert len(found) == 1
        assert "no metric or concrete tool" in found[0].message

    def test_strong_bullets_not_flagged(self) -> None:
        v = _coherent_vault()
        v.experiences[0].accomplishments = [
            "Migrated 12 services to Kubernetes, cutting deploy time 40 percent",
            "Cut warehouse spend 45 percent by moving to BigQuery",
            "Led a team of 6 engineers",
        ]
        assert "experience.weak_bullet" not in _codes(v)

    @staticmethod
    def _polished_vault() -> VaultSchema:
        """A vault whose story genuinely scores Polished (verified: strong
        causal overlap, metrics, active language, substantial fields)."""
        v = _coherent_vault()
        story = v.stories[0]
        story.situation = (
            "Our Redshift warehouse costs were ballooning every month as "
            "pipeline volume grew quickly."
        )
        story.task = (
            "I needed to migrate the warehouse pipelines to BigQuery "
            "without any downtime for analysts."
        )
        story.action = (
            "I migrated the warehouse pipelines to BigQuery using "
            "dual-writes and verified downtime stayed zero."
        )
        story.result = (
            "The migrated warehouse pipelines cut BigQuery costs 45 percent "
            "with zero downtime across six weeks."
        )
        return v

    def test_polished_story_without_lesson_flagged(self) -> None:
        v = self._polished_vault()
        report = audit_vault(v)
        labels = {s.story_id: s.label for s in report.story_scores}
        assert labels[str(v.stories[0].id)] == "Polished"
        found = [
            f for f in report.findings if f.code == "story.polished_no_lesson"
        ]
        assert len(found) == 1
        assert found[0].severity == "minor"

    def test_polished_story_with_lesson_not_flagged(self) -> None:
        v = self._polished_vault()
        v.stories[0].lesson = "Dual-writes made the cutover boring — repeat that."
        assert "story.polished_no_lesson" not in _codes(v)

    def test_non_polished_story_without_lesson_not_flagged(self) -> None:
        # The fixture story scores Strong, not Polished — no lesson nag.
        assert "story.polished_no_lesson" not in _codes(_coherent_vault())

    def test_style_findings_never_exceed_minor(self) -> None:
        v = _coherent_vault()
        v.stories[0].action = "I leveraged synergy to hit the ground running."
        v.experiences[0].accomplishments = ["Responsible for stuff"]
        report = audit_vault(v)
        style_codes = {
            "story.buzzword",
            "experience.weak_bullet",
            "story.polished_no_lesson",
        }
        for finding in report.findings:
            if finding.code in style_codes:
                assert finding.severity == "minor"

    def test_no_brand_name_in_messages(self) -> None:
        v = _coherent_vault()
        v.stories[0].action = "I leveraged synergy."
        v.experiences[0].accomplishments = ["Responsible for stuff"]
        for finding in audit_vault(v).findings:
            assert "career-ops" not in finding.message.lower()


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


class TestDoctorCLI:
    def test_no_vault(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(cli, ["--path", str(tmp_path / "x"), "doctor"])
        assert result.exit_code == 0
        assert "No vault found" in result.output

    def test_reports_phase_and_next_step(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        d = _seed(tmp_path, _coherent_vault())
        result = runner.invoke(cli, ["--path", str(d), "doctor"])
        assert result.exit_code == 0, result.output
        assert "Vault phase: growing" in result.output
        assert "Next:" in result.output
        assert "No staleness detected" in result.output

    def test_json_shape(self, runner: CliRunner, tmp_path: Path) -> None:
        d = _seed(tmp_path, _coherent_vault())
        result = runner.invoke(cli, ["--path", str(d), "doctor", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["phase"]["phase"] == "growing"
        assert payload["stale_days"] == STALE_DAYS_DEFAULT
        assert payload["findings"] == []
        assert "next" in payload

    def test_stale_days_flag(self, runner: CliRunner, tmp_path: Path) -> None:
        # With a 1-day threshold even a just-seeded vault's current role is
        # fresh; use a story-less current-role vault aged by construction —
        # simplest deterministic path: assert the flag is accepted and echoed
        # through the JSON payload.
        d = _seed(tmp_path, _coherent_vault())
        result = runner.invoke(
            cli, ["--path", str(d), "doctor", "--json", "--stale-days", "7"]
        )
        payload = json.loads(result.output)
        assert payload["stale_days"] == 7

    def test_first_run_phase_on_empty_vault(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        d = _seed(tmp_path, VaultSchema())
        result = runner.invoke(cli, ["--path", str(d), "doctor"])
        assert "Vault phase: first-run" in result.output
        assert "import-resume" in result.output

    def test_vault_show_surfaces_phase(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        d = _seed(tmp_path, _coherent_vault())
        result = runner.invoke(cli, ["--path", str(d), "vault", "show"])
        assert result.exit_code == 0
        assert "Phase: growing" in result.output


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
