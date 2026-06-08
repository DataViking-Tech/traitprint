"""STAR story coherence scoring and cross-story contradiction detection.

A faithful Python port of the Traitprint Cloud engine
(``src/lib/vault/story-coherence.ts``) so Local and Cloud agree on how a
story is scored, what "demonstrates" vs "mentions" vs "weak" evidence means,
and how conflicting claims between two stories are flagged. All heuristics
are LLM-free and deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

IssueSeverity = Literal["critical", "major", "minor"]
ContradictionSeverity = Literal["critical", "major"]
StarField = Literal["situation", "task", "action", "result", "overall"]
EvidenceLevel = Literal["demonstrates", "mentions", "weak"]
CoherenceLabel = Literal["Polished", "Strong", "Solid", "Draft"]


@dataclass(frozen=True)
class CoherenceIssue:
    """An individual issue found while scoring a single story."""

    field: StarField
    severity: IssueSeverity
    message: str


@dataclass(frozen=True)
class CoherenceScore:
    """Result of scoring one STAR story."""

    overall: float  # 0..1
    evidence_level: EvidenceLevel
    label: CoherenceLabel
    issues: list[CoherenceIssue] = field(default_factory=list)


@dataclass(frozen=True)
class CrossStoryContradiction:
    """A contradiction found between two stories in the same experience."""

    story_id_a: str
    story_id_b: str
    field: Literal["situation", "action", "result", "overall"]
    severity: ContradictionSeverity
    message: str


@dataclass(frozen=True)
class CrossValidationResult:
    experience_id: str
    contradictions: list[CrossStoryContradiction]


# --- Heuristic helpers (no LLM needed) ---

MIN_WORDS = 5
GOOD_WORDS = 10


def _word_count(text: str) -> int:
    return len([w for w in text.strip().split() if w])


_METRIC_PATTERN = re.compile(
    r"\d+\s*(%|percent|x\b|times|days?|weeks?|hours?|ms|seconds?|minutes?|"
    r"\$|users?|customers?|reduction|improvement|increase|decrease)",
    re.IGNORECASE,
)


def _has_metrics(text: str) -> bool:
    return bool(_METRIC_PATTERN.search(text))


_FIRST_PERSON_PATTERN = re.compile(r"\b(I |my |we |our )\b", re.IGNORECASE)

# Active verbs only count when NOT in passive voice. Python's ``re`` forbids the
# variable-width lookbehind the TS source uses, so we capture an optional leading
# auxiliary and treat a match with one present as passive.
_ACTIVE_VERBS = (
    "led|designed|built|created|developed|implemented|wrote|coordinated|managed|"
    "established|drove|initiated|proposed|architected|shipped|delivered|launched|"
    "deployed|automated|refactored|migrated|optimized"
)
_ACTIVE_VERB_PATTERN = re.compile(
    rf"(?:\b(?P<aux>was|were|been|is|are|being)\s+)?\b(?:{_ACTIVE_VERBS})\b",
    re.IGNORECASE,
)


def _has_active_language(text: str) -> bool:
    if _FIRST_PERSON_PATTERN.search(text):
        return True
    return any(m.group("aux") is None for m in _ACTIVE_VERB_PATTERN.finditer(text))


_VAGUE_PATTERNS = [
    re.compile(
        r"\b(helped with|worked on|was involved|participated|assisted|"
        r"contributed to)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^(did|made|got|had) ", re.IGNORECASE),
    re.compile(r"\b(stuff|things|various|etc\.?|and so on)\b", re.IGNORECASE),
]


def _has_vague_language(text: str) -> bool:
    return any(p.search(text) for p in _VAGUE_PATTERNS)


_STEM_SUFFIX = re.compile(
    r"(ization|isation|ation|ment|ness|ting|tion|sion|ance|ence|ible|able|"
    r"ized|ised|ful|less|ous|ive|ity|ate|ing|ted|ed|ly|er|es|s)$",
    re.IGNORECASE,
)


def _stem(word: str) -> str:
    """Crude stemmer: reduce words to a common root so related forms match."""
    return _STEM_SUFFIX.sub("", word)


_STOP_WORDS = [
    "team", "teams", "project", "projects", "work", "worked", "working",
    "process", "processes", "processed", "system", "systems", "company",
    "companies", "business", "businesses", "manage", "managed", "management",
    "managing", "organize", "organized", "organization", "organizations",
    "department", "departments", "group", "groups", "role", "roles",
    "ensure", "ensured", "ensuring", "involve", "involved", "involving",
    "help", "helped", "helping", "make", "makes", "making", "need", "needed",
    "plan", "planned", "planning", "operate", "operated", "operation",
    "operations", "also", "within", "without", "through", "about", "between",
    "over", "other", "their", "these", "those", "several", "well", "across",
    "overall", "task", "tasks", "issue", "issues", "goal", "goals",
]
_PROFESSIONAL_STOP_STEMS = {w for word in _STOP_WORDS for w in (word, _stem(word))}


def _extract_stems(text: str) -> set[str]:
    cleaned = re.sub(r"[^\w\s]", "", text.lower())
    out: set[str] = set()
    for w in cleaned.split():
        if len(w) > 3:
            s = _stem(w)
            if len(s) > 2 and s not in _PROFESSIONAL_STOP_STEMS:
                out.add(s)
    return out


def _compute_field_overlap(field_a: str, field_b: str) -> float:
    """Stemmed keyword overlap as a lightweight proxy for causal linkage."""
    stems_a = _extract_stems(field_a)
    stems_b = _extract_stems(field_b)
    if not stems_a or not stems_b:
        return 0.0
    overlap = sum(1 for s in stems_a if s in stems_b)
    return overlap / min(len(stems_a), len(stems_b))


def _capitalize(s: str) -> str:
    return s[:1].upper() + s[1:]


def score_story_coherence(
    situation: str, task: str, action: str, result: str
) -> CoherenceScore:
    """Score a STAR story's coherence without an LLM.

    Mirrors the cloud point system exactly (100 max): field substance (40),
    action specificity (20), result quality (20), causal chain (20).
    """
    issues: list[CoherenceIssue] = []
    total_points = 0
    max_points = 100

    # --- 1. Field substance (40 points) ---
    fields: list[tuple[StarField, str]] = [
        ("situation", situation),
        ("task", task),
        ("action", action),
        ("result", result),
    ]
    for name, text in fields:
        wc = _word_count(text)
        if not text.strip():
            issues.append(
                CoherenceIssue(name, "critical", f"{_capitalize(name)} is empty")
            )
        elif wc < MIN_WORDS:
            issues.append(
                CoherenceIssue(
                    name,
                    "major",
                    f"{_capitalize(name)} is too brief ({wc} words) — "
                    "add specific details",
                )
            )
            total_points += 3
        elif wc < GOOD_WORDS:
            issues.append(
                CoherenceIssue(
                    name, "minor", f"{_capitalize(name)} could be more detailed"
                )
            )
            total_points += 7
        else:
            total_points += 10

    # --- 2. Action specificity (20 points) ---
    if action.strip():
        if _has_active_language(action):
            total_points += 10
        else:
            issues.append(
                CoherenceIssue(
                    "action",
                    "major",
                    "Action lacks active language — describe what YOU "
                    "specifically did",
                )
            )
            total_points += 3
        if _has_vague_language(action):
            issues.append(
                CoherenceIssue(
                    "action",
                    "major",
                    "Action uses vague phrasing — replace with concrete steps",
                )
            )
        else:
            total_points += 10

    # --- 3. Result quality (20 points) ---
    if result.strip():
        if _has_metrics(result):
            total_points += 15
        else:
            issues.append(
                CoherenceIssue(
                    "result",
                    "major",
                    "Result lacks measurable outcomes — add numbers, "
                    "percentages, or metrics",
                )
            )
            total_points += 5
        if _word_count(result) >= MIN_WORDS:
            total_points += 5

    # --- 4. Causal chain coherence (20 points) ---
    if task.strip() and action.strip():
        if _compute_field_overlap(task, action) >= 0.2:
            total_points += 10
        else:
            issues.append(
                CoherenceIssue(
                    "overall",
                    "major",
                    "Action doesn't clearly address the Task — ensure they connect",
                )
            )
            total_points += 3
    if action.strip() and result.strip():
        if _compute_field_overlap(action, result) >= 0.2:
            total_points += 10
        else:
            issues.append(
                CoherenceIssue(
                    "result",
                    "major",
                    "Result doesn't clearly follow from the Action — "
                    "strengthen the causal link",
                )
            )
            total_points += 3

    overall = min(1.0, total_points / max_points)

    has_critical = any(i.severity == "critical" for i in issues)
    major_count = sum(1 for i in issues if i.severity == "major")
    if has_critical or overall < 0.3:
        evidence_level: EvidenceLevel = "weak"
    elif major_count >= 2 or overall < 0.6:
        evidence_level = "mentions"
    else:
        evidence_level = "demonstrates"

    label: CoherenceLabel
    if evidence_level == "weak":
        label = "Solid" if overall >= 0.4 else "Draft"
    elif evidence_level == "mentions":
        label = "Strong" if overall >= 0.7 else "Solid"
    else:
        label = "Polished" if overall >= 0.8 else "Strong"

    return CoherenceScore(overall, evidence_level, label, issues)


# --- Cross-story contradiction detection ---


@dataclass(frozen=True)
class _NumericClaim:
    value: float
    unit: str
    context: str


_NUMERIC_CLAIM_PATTERN = re.compile(
    r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(%|percent|people|members|engineers|developers|reports?|"
    r"team\s*(?:members?|of)|x\b|times|days?|weeks?|months?|hours?|minutes?|"
    r"seconds?|ms|\$|dollars?|users?|customers?|employees?)",
    re.IGNORECASE,
)


def _extract_numeric_claims(text: str) -> list[_NumericClaim]:
    claims: list[_NumericClaim] = []
    for match in _NUMERIC_CLAIM_PATTERN.finditer(text):
        value = float(match.group(1).replace(",", ""))
        unit = re.sub(r"s$", "", match.group(2).lower())
        start = max(0, match.start() - 30)
        end = min(len(text), match.end() + 30)
        claims.append(_NumericClaim(value, unit, text[start:end].strip()))
    return claims


_LEADERSHIP_PATTERN = re.compile(
    r"\b(led|managed|supervised|directed|oversaw|headed|coordinated)\b.*"
    r"\b(team|group|department|reports?)\b",
    re.IGNORECASE,
)
_IC_PATTERN = re.compile(
    r"\b(individual contributor|sole developer|worked independently|"
    r"no direct reports|worked alone|solo)\b",
    re.IGNORECASE,
)

_RoleClaim = Literal["leader", "ic"]


def _extract_role_claim(text: str) -> _RoleClaim | None:
    has_leadership = bool(_LEADERSHIP_PATTERN.search(text))
    has_ic = bool(_IC_PATTERN.search(text))
    if has_leadership and not has_ic:
        return "leader"
    if has_ic and not has_leadership:
        return "ic"
    return None


_PEOPLE_UNITS = {
    "people", "member", "engineer", "developer", "report",
    "team member", "team of", "employee",
}


def _normalize_unit(unit: str) -> str:
    clean = re.sub(r"s$", "", unit.lower())
    if clean in _PEOPLE_UNITS:
        return "people"
    if clean in ("percent", "%"):
        return "%"
    if clean in ("dollar", "$"):
        return "$"
    return clean


def _claims_contradict(a: _NumericClaim, b: _NumericClaim) -> bool:
    if _normalize_unit(a.unit) != _normalize_unit(b.unit):
        return False
    if a.value == b.value:
        return False
    hi = max(a.value, b.value)
    lo = min(a.value, b.value)
    return hi > lo * 2 or (hi - lo) / hi > 0.5


@dataclass(frozen=True)
class StoryInput:
    """Minimal story shape for cross-validation (all ids as strings)."""

    id: str
    title: str
    situation: str
    task: str
    action: str
    result: str
    experience_id: str | None


def cross_validate_stories(stories: list[StoryInput]) -> list[CrossValidationResult]:
    """Detect metric and role contradictions among stories sharing an experience."""
    groups: dict[str, list[StoryInput]] = {}
    for story in stories:
        if not story.experience_id:
            continue
        groups.setdefault(story.experience_id, []).append(story)

    results: list[CrossValidationResult] = []
    for experience_id, group in groups.items():
        if len(group) < 2:
            continue
        contradictions: list[CrossStoryContradiction] = []
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                text_a = f"{a.situation} {a.task} {a.action} {a.result}"
                text_b = f"{b.situation} {b.task} {b.action} {b.result}"

                for ca in _extract_numeric_claims(text_a):
                    for cb in _extract_numeric_claims(text_b):
                        if _claims_contradict(ca, cb):
                            contradictions.append(
                                CrossStoryContradiction(
                                    a.id,
                                    b.id,
                                    "result",
                                    "major",
                                    f'Conflicting metrics: "{ca.value} {ca.unit}" '
                                    f'vs "{cb.value} {cb.unit}" in stories '
                                    f'"{a.title}" and "{b.title}"',
                                )
                            )

                role_a = _extract_role_claim(text_a)
                role_b = _extract_role_claim(text_b)
                if role_a and role_b and role_a != role_b:

                    def _role_word(role: _RoleClaim) -> str:
                        return "leadership" if role == "leader" else (
                            "individual contributor"
                        )

                    contradictions.append(
                        CrossStoryContradiction(
                            a.id,
                            b.id,
                            "overall",
                            "critical",
                            f'Contradicting roles: "{a.title}" claims '
                            f'{_role_word(role_a)} while "{b.title}" claims '
                            f"{_role_word(role_b)}",
                        )
                    )
        if contradictions:
            results.append(CrossValidationResult(experience_id, contradictions))
    return results


__all__ = [
    "CoherenceIssue",
    "CoherenceLabel",
    "CoherenceScore",
    "CrossStoryContradiction",
    "CrossValidationResult",
    "EvidenceLevel",
    "cross_validate_stories",
    "score_story_coherence",
]
