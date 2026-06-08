"""Vault-level narrative-coherence audit.

This is the umbrella over the ported Cloud engines: per-story STAR coherence
scoring (:mod:`traitprint.coherence`), cross-story contradiction detection,
philosophy tension detection (:mod:`traitprint.tensions`), plus structural
gap checks that have no home in the per-story scorers (dangling references,
unsupported strong skills, roles with no story).

Vocabulary matches Cloud: severities are ``critical`` / ``major`` / ``minor``.
Tensions are surfaced separately as *insights*, never as blocking findings —
they are nuance, not bugs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from traitprint.coherence import (
    CoherenceScore,
    StoryInput,
    cross_validate_stories,
    score_story_coherence,
)
from traitprint.schema import VaultSchema
from traitprint.tensions import (
    DetectedTension,
    detect_all_tensions,
    format_tension_insight,
)

Severity = Literal["critical", "major", "minor"]

# Higher rank = more severe. Used for sorting and ``--severity`` filtering.
_SEVERITY_RANK: dict[Severity, int] = {"minor": 0, "major": 1, "critical": 2}

# Proficiency (1-10) at or above which a skill is a "strong claim" that should
# be backed by a story. Maps to the MCP server's "expert"/"authority" buckets.
STRONG_PROFICIENCY = 7


@dataclass(frozen=True)
class Finding:
    """A single coherence issue. ``related_id`` links a second item (e.g. the
    other story in a contradiction)."""

    severity: Severity
    code: str
    section: str
    message: str
    item_id: str | None = None
    related_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "section": self.section,
            "message": self.message,
            "item_id": self.item_id,
            "related_id": self.related_id,
        }


@dataclass(frozen=True)
class StoryScore:
    """A per-story coherence score for display."""

    story_id: str
    title: str
    overall: float
    evidence_level: str
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "story_id": self.story_id,
            "title": self.title,
            "overall": round(self.overall, 3),
            "evidence_level": self.evidence_level,
            "label": self.label,
        }


@dataclass(frozen=True)
class AuditReport:
    findings: list[Finding] = field(default_factory=list)
    story_scores: list[StoryScore] = field(default_factory=list)
    tensions: list[DetectedTension] = field(default_factory=list)
    overall_coherence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "story_scores": [s.to_dict() for s in self.story_scores],
            "tensions": [
                {
                    "philosophy_a_id": t.philosophy_a_id,
                    "philosophy_b_id": t.philosophy_b_id,
                    "category": t.category,
                    "insight": format_tension_insight(t),
                    "confidence": round(t.confidence, 3),
                }
                for t in self.tensions
            ],
            "overall_coherence": (
                round(self.overall_coherence, 3)
                if self.overall_coherence is not None
                else None
            ),
            "summary": summarize(self.findings),
        }


def severity_rank(severity: Severity) -> int:
    """Return the numeric rank of a severity (higher = more severe)."""
    return _SEVERITY_RANK.get(severity, 0)


def _audit_profile(vault: VaultSchema, out: list[Finding]) -> None:
    p = vault.profile
    if not p.display_name:
        out.append(
            Finding(
                "minor",
                "profile.no_name",
                "profile",
                "Profile has no display name. Set one with "
                "'traitprint vault set-profile --name'.",
            )
        )
    if not p.headline:
        out.append(
            Finding(
                "major",
                "profile.no_headline",
                "profile",
                "Profile has no headline — the one-line identity primer agents "
                "read first. Set one with 'traitprint vault set-profile --headline'.",
            )
        )
    if not p.summary:
        out.append(
            Finding(
                "major",
                "profile.no_summary",
                "profile",
                "Profile has no summary/bio. Add one with "
                "'traitprint vault set-profile --summary'.",
            )
        )


def _audit_story_references(
    vault: VaultSchema,
    skill_ids: set[str],
    experience_ids: set[str],
    out: list[Finding],
) -> None:
    for story in vault.stories:
        sid = str(story.id)
        for ref in story.skill_ids:
            if str(ref) not in skill_ids:
                out.append(
                    Finding(
                        "critical",
                        "story.dangling_skill",
                        "stories",
                        f"Story {story.title!r} references skill {ref} that no "
                        "longer exists in the vault.",
                        sid,
                    )
                )
        if story.experience_id and str(story.experience_id) not in experience_ids:
            out.append(
                Finding(
                    "critical",
                    "story.dangling_experience",
                    "stories",
                    f"Story {story.title!r} references experience "
                    f"{story.experience_id} that no longer exists in the vault.",
                    sid,
                )
            )
        if not story.skill_ids:
            out.append(
                Finding(
                    "minor",
                    "story.no_skills",
                    "stories",
                    f"Story {story.title!r} is not linked to any skill, so it "
                    "cannot serve as evidence in 'search_skills'.",
                    sid,
                )
            )


def _audit_skills(
    vault: VaultSchema, skills_with_evidence: set[str], out: list[Finding]
) -> None:
    for skill in vault.skills:
        if skill.proficiency >= STRONG_PROFICIENCY and str(skill.id) not in (
            skills_with_evidence
        ):
            out.append(
                Finding(
                    "major",
                    "skill.unsupported_strength",
                    "skills",
                    f"Skill {skill.name!r} is claimed at {skill.proficiency}/10 "
                    "but no story demonstrates it. Strong claims read as more "
                    "credible with a STAR story behind them.",
                    str(skill.id),
                )
            )


def _audit_philosophies(
    vault: VaultSchema, story_ids: set[str], out: list[Finding]
) -> None:
    for phil in vault.philosophies:
        pid = str(phil.id)
        if not phil.evidence_story_ids:
            out.append(
                Finding(
                    "major",
                    "philosophy.no_evidence",
                    "philosophies",
                    f"Philosophy {phil.title!r} cites no evidence story. A stance "
                    "lands harder when a story shows you living it.",
                    pid,
                )
            )
            continue
        for ref in phil.evidence_story_ids:
            if str(ref) not in story_ids:
                out.append(
                    Finding(
                        "critical",
                        "philosophy.dangling_evidence",
                        "philosophies",
                        f"Philosophy {phil.title!r} references evidence story "
                        f"{ref} that no longer exists in the vault.",
                        pid,
                    )
                )


def _audit_experiences(
    vault: VaultSchema, experiences_with_story: set[str], out: list[Finding]
) -> None:
    for exp in vault.experiences:
        eid = str(exp.id)
        if eid not in experiences_with_story:
            out.append(
                Finding(
                    "major",
                    "experience.no_story",
                    "experiences",
                    f"Experience {exp.title!r} at {exp.company or 'unknown'} has "
                    "no story attached. Every role reads stronger with at least "
                    "one STAR story.",
                    eid,
                )
            )
        if not exp.description and not exp.accomplishments:
            out.append(
                Finding(
                    "minor",
                    "experience.thin",
                    "experiences",
                    f"Experience {exp.title!r} has no description and no "
                    "accomplishments — it is just a title and a date range.",
                    eid,
                )
            )


def _score_stories(vault: VaultSchema, out: list[Finding]) -> list[StoryScore]:
    """Score each story and fold its coherence issues into findings."""
    scores: list[StoryScore] = []
    for story in vault.stories:
        sid = str(story.id)
        score: CoherenceScore = score_story_coherence(
            story.situation, story.task, story.action, story.result
        )
        scores.append(
            StoryScore(
                sid, story.title, score.overall, score.evidence_level, score.label
            )
        )
        for issue in score.issues:
            out.append(
                Finding(
                    issue.severity,
                    f"story.{issue.field}",
                    "stories",
                    f"{story.title!r}: {issue.message}",
                    sid,
                )
            )
    return scores


def _contradiction_findings(vault: VaultSchema, out: list[Finding]) -> None:
    story_inputs = [
        StoryInput(
            id=str(s.id),
            title=s.title,
            situation=s.situation,
            task=s.task,
            action=s.action,
            result=s.result,
            experience_id=str(s.experience_id) if s.experience_id else None,
        )
        for s in vault.stories
    ]
    for group in cross_validate_stories(story_inputs):
        for c in group.contradictions:
            out.append(
                Finding(
                    c.severity,
                    "story.contradiction",
                    "stories",
                    c.message,
                    c.story_id_a,
                    c.story_id_b,
                )
            )


def audit_vault(vault: VaultSchema) -> AuditReport:
    """Run every coherence check and return a full report."""
    findings: list[Finding] = []

    skill_ids = {str(s.id) for s in vault.skills}
    experience_ids = {str(e.id) for e in vault.experiences}
    story_ids = {str(s.id) for s in vault.stories}
    skills_with_evidence = {
        str(ref) for story in vault.stories for ref in story.skill_ids
    }
    experiences_with_story = {
        str(story.experience_id) for story in vault.stories if story.experience_id
    }

    if not vault.skills and not vault.experiences:
        findings.append(
            Finding(
                "major",
                "vault.empty",
                "vault",
                "Vault has no skills and no experiences. Start with "
                "'traitprint vault import-resume' or 'traitprint vault add-skill'.",
            )
        )

    _audit_profile(vault, findings)
    _audit_skills(vault, skills_with_evidence, findings)
    _audit_experiences(vault, experiences_with_story, findings)
    _audit_story_references(vault, skill_ids, experience_ids, findings)
    _audit_philosophies(vault, story_ids, findings)

    story_scores = _score_stories(vault, findings)
    _contradiction_findings(vault, findings)

    tensions = detect_all_tensions(vault.philosophies)

    # Most severe first; stable within a severity for deterministic output.
    findings.sort(key=lambda f: -severity_rank(f.severity))

    overall = (
        sum(s.overall for s in story_scores) / len(story_scores)
        if story_scores
        else None
    )

    return AuditReport(
        findings=findings,
        story_scores=story_scores,
        tensions=tensions,
        overall_coherence=overall,
    )


def summarize(findings: list[Finding]) -> dict[str, int]:
    """Return per-severity counts plus a total."""
    counts = {"critical": 0, "major": 0, "minor": 0}
    for finding in findings:
        counts[finding.severity] += 1
    counts["total"] = len(findings)
    return counts


__all__ = [
    "STRONG_PROFICIENCY",
    "AuditReport",
    "Finding",
    "Severity",
    "StoryScore",
    "audit_vault",
    "severity_rank",
    "summarize",
]
