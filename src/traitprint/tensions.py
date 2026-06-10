"""Philosophy tension (contradiction) detection.

A faithful Python port of the Traitprint Cloud engine
(``src/lib/vault/philosophy-contradictions.ts``). Tensions between two
philosophies in the same category are treated as *nuance* — context-dependent
thinking — not bugs. Detection is heuristic keyword opposition, LLM-free.

The cloud persistence layer (``philosophy_tensions`` table, dismiss/resolve)
is intentionally not ported: Local computes tensions on the fly from the vault.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from traitprint.schema import PhilosophyCategory, PhilosophySchema


@dataclass(frozen=True)
class DetectedTension:
    philosophy_a_id: str
    philosophy_b_id: str
    category: str
    tension_description: str
    pole_a_label: str
    pole_b_label: str
    confidence: float  # 0..1


@dataclass(frozen=True)
class _Pole:
    label: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class _Opposition:
    pole_a: _Pole
    pole_b: _Pole
    template: str  # {{poleA}} / {{poleB}} replaced at runtime


CATEGORY_OPPOSITIONS: dict[str, list[_Opposition]] = {
    PhilosophyCategory.LEADERSHIP.value: [
        _Opposition(
            _Pole(
                "Autonomy",
                ("autonomous", "empower", "trust", "delegate", "self-directed",
                 "ownership", "independence", "freedom", "decentralized"),
            ),
            _Pole(
                "Structure",
                ("hierarchy", "structured", "oversight", "control", "clear roles",
                 "top-down", "chain of command", "directive", "centralized"),
            ),
            "You value both {{poleA}} and {{poleB}} in leadership — this suggests "
            "you adapt your style to the team's maturity and context",
        ),
        _Opposition(
            _Pole(
                "Hands-on",
                ("hands-on", "in the code", "technical lead", "coding",
                 "pair programming", "code review", "architecture hands-on"),
            ),
            _Pole(
                "Delegation",
                ("delegate", "step back", "let the team", "trust the team",
                 "strategic", "high-level", "remove myself", "enabler"),
            ),
            "You believe in being both {{poleA}} and practicing {{poleB}} — you "
            "likely adjust based on the situation's complexity",
        ),
    ],
    PhilosophyCategory.COLLABORATION.value: [
        _Opposition(
            _Pole(
                "Deep collaboration",
                ("pair", "mob", "together", "synchronous", "real-time", "workshop",
                 "co-create", "brainstorm", "collaborative"),
            ),
            _Pole(
                "Focused independence",
                ("async", "independent", "solo", "heads-down", "focus time",
                 "deep work", "uninterrupted", "autonomous", "written"),
            ),
            "You value both {{poleA}} and {{poleB}} — recognizing that different "
            "tasks benefit from different collaboration modes",
        ),
        _Opposition(
            _Pole(
                "Consensus-building",
                ("consensus", "alignment", "buy-in", "inclusive", "democratic",
                 "everyone's input", "collaborative decision", "group agreement"),
            ),
            _Pole(
                "Decisive action",
                ("decisive", "quick decision", "move fast", "bias for action",
                 "disagree and commit", "strong opinion", "take charge",
                 "unilateral"),
            ),
            "You appreciate both {{poleA}} and {{poleB}} — likely depending on the "
            "stakes and reversibility of the decision",
        ),
    ],
    PhilosophyCategory.TECHNICAL_APPROACH.value: [
        _Opposition(
            _Pole(
                "Moving fast",
                ("move fast", "ship quickly", "iterate", "prototype", "mvp",
                 "speed", "velocity", "done is better", "pragmatic", "quick"),
            ),
            _Pole(
                "Thoroughness",
                ("thorough", "careful", "quality", "robust", "production-ready",
                 "test coverage", "reliability", "correctness", "rigorous",
                 "solid"),
            ),
            "You believe in both {{poleA}} and {{poleB}} in your technical work — "
            "this tension often drives good engineering judgment",
        ),
        _Opposition(
            _Pole(
                "Simplicity",
                ("simple", "minimal", "kiss", "yagni", "less is more",
                 "straightforward", "lean", "avoid over-engineering",
                 "boring technology"),
            ),
            _Pole(
                "Comprehensive design",
                ("scalable", "extensible", "future-proof", "abstraction",
                 "architecture", "design patterns", "modular", "reusable",
                 "flexible"),
            ),
            "You value both {{poleA}} and {{poleB}} — the best engineers navigate "
            "this tension contextually",
        ),
    ],
    PhilosophyCategory.CULTURE.value: [
        _Opposition(
            _Pole(
                "Startup energy",
                ("startup", "scrappy", "fast-paced", "hustle", "wear many hats",
                 "chaotic", "exciting", "dynamic", "high-growth", "ambiguous"),
            ),
            _Pole(
                "Stability",
                ("stable", "established", "mature", "predictable",
                 "work-life balance", "sustainable", "process", "well-defined",
                 "organized", "calm"),
            ),
            "You're drawn to both {{poleA}} and {{poleB}} — perhaps seeking "
            "different things at different career stages or in different aspects "
            "of work",
        ),
        _Opposition(
            _Pole(
                "Transparency",
                ("transparent", "open", "radical candor", "direct", "honest",
                 "no politics", "blunt", "straightforward", "open door"),
            ),
            _Pole(
                "Diplomatic care",
                ("diplomatic", "tactful", "careful", "measured", "sensitive",
                 "empathetic", "considerate", "thoughtful communication",
                 "psychological safety"),
            ),
            "You value both {{poleA}} and {{poleB}} in communication — this nuance "
            "shows emotional intelligence",
        ),
    ],
    PhilosophyCategory.DECISION_MAKING.value: [
        _Opposition(
            _Pole(
                "Data-driven",
                ("data-driven", "metrics", "evidence", "quantitative", "measure",
                 "analytics", "numbers", "objective", "empirical", "a/b test"),
            ),
            _Pole(
                "Intuition",
                ("intuition", "gut feeling", "experience", "judgment", "instinct",
                 "qualitative", "feel", "sense", "taste", "vision"),
            ),
            "You trust both {{poleA}} reasoning and {{poleB}} — effective "
            "decision-makers blend both depending on data availability and stakes",
        ),
        _Opposition(
            _Pole(
                "Reversible speed",
                ("reversible", "two-way door", "experiment", "try and learn",
                 "fail fast", "iterate", "low risk", "easy to change"),
            ),
            _Pole(
                "Careful deliberation",
                ("irreversible", "one-way door", "careful", "deliberate",
                 "thorough analysis", "risk assessment", "due diligence",
                 "measured"),
            ),
            "You apply both {{poleA}} and {{poleB}} — distinguishing between "
            "one-way and two-way doors is a hallmark of mature decision-making",
        ),
    ],
}

MIN_CONFIDENCE = 0.3

_CATEGORY_LABELS = {
    PhilosophyCategory.LEADERSHIP.value: "Leadership",
    PhilosophyCategory.COLLABORATION.value: "Collaboration",
    PhilosophyCategory.TECHNICAL_APPROACH.value: "Technical Approach",
    PhilosophyCategory.CULTURE.value: "Culture",
    PhilosophyCategory.DECISION_MAKING.value: "Decision Making",
}


def _count_pole_hits(text: str, pole: _Pole) -> int:
    """Number of distinct pole keywords found in ``text``."""
    lower = text.lower()
    return sum(1 for kw in pole.keywords if kw.lower() in lower)


def detect_tensions(
    new_philosophy: PhilosophySchema,
    existing_philosophies: list[PhilosophySchema],
) -> list[DetectedTension]:
    """Detect tensions between ``new_philosophy`` and same-category existing ones.

    Returns tensions sorted by confidence (descending).
    """
    category = new_philosophy.category
    if not category:
        # Uncategorized philosophies have no opposition table to compare on.
        return []
    same_category = [
        p
        for p in existing_philosophies
        if p.category == category and p.id != new_philosophy.id
    ]
    if not same_category:
        return []
    oppositions = CATEGORY_OPPOSITIONS.get(category)
    if not oppositions:
        return []

    tensions: list[DetectedTension] = []
    new_text = f"{new_philosophy.title} {new_philosophy.description}"

    for existing in same_category:
        existing_text = f"{existing.title} {existing.description}"
        for opp in oppositions:
            new_a = _count_pole_hits(new_text, opp.pole_a)
            new_b = _count_pole_hits(new_text, opp.pole_b)
            ex_a = _count_pole_hits(existing_text, opp.pole_a)
            ex_b = _count_pole_hits(existing_text, opp.pole_b)

            new_lean_a = new_a > 0 and new_a > new_b
            new_lean_b = new_b > 0 and new_b > new_a
            ex_lean_a = ex_a > 0 and ex_a > ex_b
            ex_lean_b = ex_b > 0 and ex_b > ex_a

            is_tension = (new_lean_a and ex_lean_b) or (new_lean_b and ex_lean_a)
            if not is_tension:
                continue

            total_hits = new_a + new_b + ex_a + ex_b
            confidence = min(1.0, math.log2(total_hits + 1) / 4)
            if confidence < MIN_CONFIDENCE:
                continue

            pole_a_phil, pole_b_phil = (
                (new_philosophy, existing) if new_lean_a else (existing, new_philosophy)
            )
            description = opp.template.replace(
                "{{poleA}}", opp.pole_a.label.lower()
            ).replace("{{poleB}}", opp.pole_b.label.lower())

            tensions.append(
                DetectedTension(
                    philosophy_a_id=str(pole_a_phil.id),
                    philosophy_b_id=str(pole_b_phil.id),
                    category=category,
                    tension_description=description,
                    pole_a_label=opp.pole_a.label,
                    pole_b_label=opp.pole_b.label,
                    confidence=confidence,
                )
            )

    tensions.sort(key=lambda t: -t.confidence)
    return tensions


def detect_all_tensions(
    philosophies: list[PhilosophySchema],
) -> list[DetectedTension]:
    """Scan the whole vault for tensions, returning each unordered pair once."""
    seen: set[frozenset[str]] = set()
    out: list[DetectedTension] = []
    for i, phil in enumerate(philosophies):
        rest = philosophies[:i] + philosophies[i + 1 :]
        for tension in detect_tensions(phil, rest):
            key = frozenset((tension.philosophy_a_id, tension.philosophy_b_id))
            if key in seen:
                continue
            seen.add(key)
            out.append(tension)
    out.sort(key=lambda t: -t.confidence)
    return out


def format_tension_insight(tension: DetectedTension) -> str:
    """Frame a tension as nuanced, context-dependent thinking (cloud parity)."""
    label = _CATEGORY_LABELS.get(tension.category, tension.category)
    return (
        f"Your philosophy on {label.lower()} shows nuance — "
        f"{tension.tension_description}"
    )


__all__ = [
    "CATEGORY_OPPOSITIONS",
    "MIN_CONFIDENCE",
    "DetectedTension",
    "detect_all_tensions",
    "detect_tensions",
    "format_tension_insight",
]
