"""Narrative-coherence audit for the vault.

The audit is a deterministic, read-only pass over a :class:`VaultSchema`
that flags gaps and integrity problems an agent (or a human) should fix
before publishing or handing the vault to a recruiter's assistant.

It answers questions like:

- Do my strongest skills have a story that proves them?
- Does every stated philosophy point at evidence?
- Are any stories silently broken (incomplete STAR, dangling references)
  so the MCP ``find_story`` tool will never surface them?
- Does every role have at least one story attached to it?

``audit_vault`` returns a flat list of :class:`Finding` objects. The CLI
(``traitprint vault audit``) and the ``audit_coherence`` MCP prompt both
build on it. Nothing here writes to disk or hits the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from traitprint.schema import VaultSchema

Severity = Literal["error", "warning", "info"]

# Higher rank = more severe. Used for sorting and ``--severity`` filtering.
_SEVERITY_RANK: dict[Severity, int] = {"info": 0, "warning": 1, "error": 2}

# Proficiency at or above this (on the 1-10 scale) is a "strong claim" that
# narrative coherence says should be backed by at least one story. Maps to the
# MCP server's "expert"/"authority" buckets (proficiency >= 6 is "expert").
STRONG_PROFICIENCY = 7


@dataclass(frozen=True)
class Finding:
    """A single coherence issue discovered in the vault."""

    severity: Severity
    code: str
    section: str
    message: str
    item_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "section": self.section,
            "message": self.message,
            "item_id": self.item_id,
        }


def severity_rank(severity: Severity) -> int:
    """Return the numeric rank of a severity (higher = more severe)."""
    return _SEVERITY_RANK.get(severity, 0)


def _story_is_complete(situation: str, task: str, action: str, result: str) -> bool:
    return bool(situation and task and action and result)


def _audit_profile(vault: VaultSchema, out: list[Finding]) -> None:
    p = vault.profile
    if not p.display_name:
        out.append(
            Finding(
                "info",
                "profile.no_name",
                "profile",
                "Profile has no display name. Set one with "
                "'traitprint vault set-profile --name'.",
            )
        )
    if not p.headline:
        out.append(
            Finding(
                "warning",
                "profile.no_headline",
                "profile",
                "Profile has no headline — the one-line identity primer agents "
                "read first. Set one with 'traitprint vault set-profile --headline'.",
            )
        )
    if not p.summary:
        out.append(
            Finding(
                "warning",
                "profile.no_summary",
                "profile",
                "Profile has no summary/bio. Add one with "
                "'traitprint vault set-profile --summary'.",
            )
        )


def _audit_stories(
    vault: VaultSchema,
    skill_ids: set[str],
    experience_ids: set[str],
    out: list[Finding],
) -> None:
    for story in vault.stories:
        sid = str(story.id)
        if not _story_is_complete(
            story.situation, story.task, story.action, story.result
        ):
            missing = [
                field
                for field, value in (
                    ("situation", story.situation),
                    ("task", story.task),
                    ("action", story.action),
                    ("result", story.result),
                )
                if not value
            ]
            out.append(
                Finding(
                    "error",
                    "story.incomplete_star",
                    "stories",
                    f"Story {story.title!r} is missing STAR field(s): "
                    f"{', '.join(missing)}. Incomplete stories are silently "
                    "excluded from the 'find_story' MCP tool.",
                    sid,
                )
            )
        for ref in story.skill_ids:
            if str(ref) not in skill_ids:
                out.append(
                    Finding(
                        "error",
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
                    "error",
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
                    "info",
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
                    "warning",
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
                    "warning",
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
                        "error",
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
                    "warning",
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
                    "info",
                    "experience.thin",
                    "experiences",
                    f"Experience {exp.title!r} has no description and no "
                    "accomplishments — it is just a title and a date range.",
                    eid,
                )
            )


def audit_vault(vault: VaultSchema) -> list[Finding]:
    """Run every coherence check and return findings, most severe first."""
    out: list[Finding] = []

    skill_ids = {str(s.id) for s in vault.skills}
    experience_ids = {str(e.id) for e in vault.experiences}
    story_ids = {str(s.id) for s in vault.stories}

    # Cross-reference indexes built once and shared across checks.
    skills_with_evidence = {
        str(ref) for story in vault.stories for ref in story.skill_ids
    }
    experiences_with_story = {
        str(story.experience_id) for story in vault.stories if story.experience_id
    }

    if not vault.skills and not vault.experiences:
        out.append(
            Finding(
                "warning",
                "vault.empty",
                "vault",
                "Vault has no skills and no experiences. Start with "
                "'traitprint vault import-resume' or 'traitprint vault add-skill'.",
            )
        )

    _audit_profile(vault, out)
    _audit_skills(vault, skills_with_evidence, out)
    _audit_experiences(vault, experiences_with_story, out)
    _audit_stories(vault, skill_ids, experience_ids, out)
    _audit_philosophies(vault, story_ids, out)

    # Most severe first; stable within a severity so output is deterministic.
    out.sort(key=lambda f: -severity_rank(f.severity))
    return out


def summarize(findings: list[Finding]) -> dict[str, int]:
    """Return per-severity counts plus a total, e.g. ``{'error': 2, ...}``."""
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in findings:
        counts[finding.severity] += 1
    counts["total"] = len(findings)
    return counts


__all__ = [
    "STRONG_PROFICIENCY",
    "Finding",
    "Severity",
    "audit_vault",
    "severity_rank",
    "summarize",
]
