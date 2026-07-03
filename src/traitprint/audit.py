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

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from traitprint.coherence import (
    _ACTIVE_VERBS,
    CoherenceScore,
    StoryInput,
    _has_metrics,
    _has_vague_language,
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

# Proficiency (1-5) at or above which a skill is a "strong claim" that should
# be backed by a story. Maps to the MCP server's "expert"/"authority" buckets.
STRONG_PROFICIENCY = 4

# Severity for dangling cross-link references (skill_ids, experience_id,
# evidence_story_ids). Contract rule 2 (docs/schema/vault-v1/README.md) and
# the architecture's D10/Layer 1 mandate that dangling UUIDs are a *warning*
# locally — they surface as findings but do not block a default push
# (which blocks on critical only; --strict opts in to blocking on
# warnings/major too). Cloud ingest accepts and quarantines them.
DANGLING_REFERENCE_SEVERITY: Severity = "major"


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
                        DANGLING_REFERENCE_SEVERITY,
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
                    DANGLING_REFERENCE_SEVERITY,
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
                    f"Skill {skill.name!r} is claimed at {skill.proficiency}/5 "
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
                        DANGLING_REFERENCE_SEVERITY,
                        "philosophy.dangling_evidence",
                        "philosophies",
                        f"Philosophy {phil.title!r} references evidence story "
                        f"{ref} that no longer exists in the vault.",
                        pid,
                    )
                )


def _audit_experiences(
    vault: VaultSchema,
    skill_ids: set[str],
    experiences_with_story: set[str],
    out: list[Finding],
) -> None:
    for exp in vault.experiences:
        eid = str(exp.id)
        for ref in exp.skill_ids:
            if str(ref) not in skill_ids:
                out.append(
                    Finding(
                        DANGLING_REFERENCE_SEVERITY,
                        "experience.dangling_skill",
                        "experiences",
                        f"Experience {exp.title!r} references skill {ref} that "
                        "no longer exists in the vault.",
                        eid,
                    )
                )
        if not exp.skill_ids:
            out.append(
                Finding(
                    "minor",
                    "experience.no_skills",
                    "experiences",
                    f"Experience {exp.title!r} is not linked to any skill. "
                    "Link the skills exercised in this role so the role "
                    "backs up your skill claims.",
                    eid,
                )
            )
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


# ── Style lint (warning-only findings) ──────────────────────────────
#
# Cliché/buzzword blacklist and weak-bullet heuristics adapted from
# career-ops's style lint (MIT). Attribution lives in this comment only —
# that brand name must never appear in finding messages. These checks
# live HERE rather than in coherence.py on purpose: coherence.py is a
# faithful port of cloud's story-coherence.ts and must stay in lockstep,
# so the audit-side lint composes its helpers without changing its
# scoring. All style findings ship at "minor" severity so the pre-push
# audit gate and --severity filtering are untouched.

_BUZZWORD_PATTERN = re.compile(
    r"\b(synerg(?:y|ies|ize[ds]?|izing)|leverag(?:e[ds]?|ing)|passionate|"
    r"results?-driven|detail-oriented|team player|go-getter|"
    r"thought leader(?:ship)?|self-starter|dynamic individual|"
    r"guru|ninja|rockstar|wheelhouse|paradigm shift|best-in-class|"
    r"cutting-edge|world-class|state-of-the-art|low-hanging fruit|"
    r"move[d]? the needle|think(?:ing)? outside the box|"
    r"hit the ground running|w(?:ear|ore)(?:ing)? many hats|"
    r"value[- ]add(?:ed)?|game[- ]chang(?:er|ing))\b",
    re.IGNORECASE,
)

#: The one weak-verb addition on top of coherence.py's _VAGUE_PATTERNS
#: (kept out of coherence.py to preserve cloud lockstep).
_RESPONSIBLE_FOR = re.compile(r"\bresponsible for\b", re.IGNORECASE)

#: Resume-bullet lead verbs beyond coherence.py's _ACTIVE_VERBS (which is
#: tuned for story prose, not bullets). Audit-side only, same lockstep
#: rule as _RESPONSIBLE_FOR.
_EXTRA_LEAD_VERBS = (
    "cut|reduced|grew|increased|decreased|improved|saved|scaled|"
    "streamlined|negotiated|mentored|hired|trained|presented|authored|"
    "published|owned|founded|rebuilt|consolidated|eliminated|accelerated"
)

#: A bullet that *leads* with an active verb ("Migrated …", "I led …").
_LEADS_WITH_ACTIVE_VERB = re.compile(
    rf"^(?:i\s+)?(?:{_ACTIVE_VERBS}|{_EXTRA_LEAD_VERBS})\b", re.IGNORECASE
)

#: Tokens that are capitalized mid-line but are not concrete nouns.
_PRONOUN_TOKENS = frozenset({"i", "i'm", "i'd", "i've", "i'll"})


def _found_buzzwords(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in _BUZZWORD_PATTERN.finditer(text)})


def _is_vague_bullet(text: str) -> bool:
    return _has_vague_language(text) or bool(_RESPONSIBLE_FOR.search(text))


def _has_concrete_noun(line: str) -> bool:
    """True when the line names something specific after its lead verb —
    a capitalized tool/product/company, an acronym, or a token with a
    digit (Postgres, AWS, v2, GPT-4). Deliberately forgiving: this backs
    a warning-only finding."""
    for token in line.split()[1:]:
        t = token.strip(".,;:()[]'\"")
        if not t or t.lower() in _PRONOUN_TOKENS:
            continue
        if any(ch.isdigit() for ch in t) or t[0].isupper():
            return True
    return False


def _weak_bullet_reason(line: str) -> str | None:
    """Why an accomplishment bullet reads weak, or None if it passes.

    A strong bullet leads with an active verb and contains a metric or a
    concrete tool noun; vague phrasing fails it outright.
    """
    stripped = line.strip().lstrip("-*• ").strip()
    if not stripped:
        return None
    if _is_vague_bullet(stripped):
        return "vague phrasing"
    if not _LEADS_WITH_ACTIVE_VERB.search(stripped):
        return "doesn't lead with an active verb"
    if not (_has_metrics(stripped) or _has_concrete_noun(stripped)):
        return "no metric or concrete tool"
    return None


def _audit_style(
    vault: VaultSchema, story_scores: list[StoryScore], out: list[Finding]
) -> None:
    """Warning-only style lint: buzzwords in stories, weak experience
    bullets, and Polished stories missing a lesson."""
    for story in vault.stories:
        text = "\n".join(
            (story.situation, story.task, story.action, story.result, story.lesson)
        )
        buzzwords = _found_buzzwords(text)
        if buzzwords:
            listed = ", ".join(repr(b) for b in buzzwords[:3])
            out.append(
                Finding(
                    "minor",
                    "story.buzzword",
                    "stories",
                    f"Story {story.title!r} uses filler phrasing ({listed}) — "
                    "replace with what you concretely did and measured.",
                    str(story.id),
                )
            )

    labels = {s.story_id: s.label for s in story_scores}
    for story in vault.stories:
        if labels.get(str(story.id)) == "Polished" and not story.lesson.strip():
            out.append(
                Finding(
                    "minor",
                    "story.polished_no_lesson",
                    "stories",
                    f"Story {story.title!r} scores Polished but has no lesson — "
                    "capture what you'd repeat or do differently to make it "
                    "interview-ready.",
                    str(story.id),
                )
            )

    for exp in vault.experiences:
        weak: list[tuple[str, str]] = []
        for line in exp.accomplishments:
            reason = _weak_bullet_reason(line)
            if reason is not None:
                weak.append((line, reason))
        if not weak:
            continue
        example, reason = weak[0]
        excerpt = example if len(example) <= 60 else example[:59] + "…"
        out.append(
            Finding(
                "minor",
                "experience.weak_bullet",
                "experiences",
                f"Experience {exp.title!r}: {len(weak)} of "
                f"{len(exp.accomplishments)} accomplishment bullets read weak — "
                f"e.g. {excerpt!r} ({reason}). Lead with an active verb and "
                "include a metric or a concrete tool.",
                str(exp.id),
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


def audit_vault(
    vault: VaultSchema,
    *,
    pending_proposals: int = 0,
    proposal_issues: list[str] | None = None,
) -> AuditReport:
    """Run every coherence check and return a full report.

    ``pending_proposals`` and ``proposal_issues`` describe the vault's
    ``proposals/`` directory (staged writes awaiting review): pending
    proposals surface as an info-level (minor) finding, and unreadable
    proposal files surface as findings rather than crashes (Layer 1
    spirit). Callers with only a :class:`VaultSchema` may omit both.
    """
    findings: list[Finding] = []

    if pending_proposals:
        noun = "proposal" if pending_proposals == 1 else "proposals"
        findings.append(
            Finding(
                "minor",
                "proposals.pending",
                "proposals",
                f"{pending_proposals} {noun} awaiting review. Run "
                "'traitprint proposals list' to review, then "
                "'traitprint proposals approve <id>' or "
                "'traitprint proposals reject <id>'.",
            )
        )
    for issue in proposal_issues or []:
        findings.append(
            Finding(
                "minor",
                "proposals.invalid_file",
                "proposals",
                f"Unreadable proposal file: {issue}",
            )
        )

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
    _audit_experiences(vault, skill_ids, experiences_with_story, findings)
    _audit_story_references(vault, skill_ids, experience_ids, findings)
    _audit_philosophies(vault, story_ids, findings)

    story_scores = _score_stories(vault, findings)
    _contradiction_findings(vault, findings)
    _audit_style(vault, story_scores, findings)

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
    "DANGLING_REFERENCE_SEVERITY",
    "STRONG_PROFICIENCY",
    "AuditReport",
    "Finding",
    "Severity",
    "StoryScore",
    "audit_vault",
    "severity_rank",
    "summarize",
]
