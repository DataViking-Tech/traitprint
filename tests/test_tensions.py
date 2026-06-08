"""Tests for the ported philosophy-tension detection engine."""

from __future__ import annotations

from traitprint.schema import PhilosophyCategory, PhilosophySchema
from traitprint.tensions import (
    detect_all_tensions,
    detect_tensions,
    format_tension_insight,
)


def _phil(
    title: str, description: str, category: PhilosophyCategory
) -> PhilosophySchema:
    return PhilosophySchema(title=title, description=description, category=category)


AUTONOMY = _phil(
    "Empowerment",
    "I empower the team and trust autonomous ownership and independence.",
    PhilosophyCategory.LEADERSHIP,
)
STRUCTURE = _phil(
    "Clear command",
    "I rely on hierarchy and top-down control with clear roles and oversight.",
    PhilosophyCategory.LEADERSHIP,
)


class TestDetectTensions:
    def test_opposing_poles_same_category_detected(self) -> None:
        tensions = detect_tensions(AUTONOMY, [STRUCTURE])
        assert len(tensions) >= 1
        t = tensions[0]
        assert t.confidence >= 0.3
        assert {t.pole_a_label, t.pole_b_label} == {"Autonomy", "Structure"}

    def test_same_lean_no_tension(self) -> None:
        other_autonomy = _phil(
            "Trust",
            "I delegate and empower people with freedom and ownership.",
            PhilosophyCategory.LEADERSHIP,
        )
        assert detect_tensions(AUTONOMY, [other_autonomy]) == []

    def test_different_category_no_tension(self) -> None:
        data_driven = _phil(
            "Metrics first",
            "I am data-driven, measure everything, trust analytics and numbers.",
            PhilosophyCategory.DECISION_MAKING,
        )
        assert detect_tensions(AUTONOMY, [data_driven]) == []

    def test_self_excluded(self) -> None:
        assert detect_tensions(AUTONOMY, [AUTONOMY]) == []


class TestDetectAllTensions:
    def test_pair_reported_once(self) -> None:
        tensions = detect_all_tensions([AUTONOMY, STRUCTURE])
        pairs = {
            frozenset((t.philosophy_a_id, t.philosophy_b_id)) for t in tensions
        }
        assert len(tensions) == len(pairs)  # no duplicate pairs
        assert len(pairs) >= 1

    def test_no_philosophies_no_tensions(self) -> None:
        assert detect_all_tensions([]) == []


class TestFormatTensionInsight:
    def test_frames_as_nuance(self) -> None:
        t = detect_tensions(AUTONOMY, [STRUCTURE])[0]
        insight = format_tension_insight(t)
        assert "nuance" in insight.lower()
        assert "leadership" in insight.lower()
