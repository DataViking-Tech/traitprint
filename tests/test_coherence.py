"""Tests for the ported STAR coherence + cross-story contradiction engine."""

from __future__ import annotations

from traitprint.coherence import (
    StoryInput,
    cross_validate_stories,
    score_story_coherence,
)

STRONG = dict(
    situation="Redshift costs were ballooning on growing pipeline volume "
    "across the whole organization.",
    task="I was responsible for leading the migration to BigQuery with no "
    "pipeline downtime.",
    action="I designed a dual-write path, backfilled historical data, and "
    "cut over the pipelines carefully.",
    result="Cut warehouse spend 45 percent with zero downtime over six weeks.",
)


class TestScoreStoryCoherence:
    def test_strong_story_scores_high(self) -> None:
        score = score_story_coherence(**STRONG)
        assert score.overall >= 0.8
        assert score.evidence_level in ("demonstrates", "mentions")
        assert score.label in ("Polished", "Strong")

    def test_empty_fields_are_critical_and_weak(self) -> None:
        score = score_story_coherence("only this", "", "", "")
        assert score.evidence_level == "weak"
        assert score.label in ("Draft", "Solid")
        criticals = [i for i in score.issues if i.severity == "critical"]
        # task, action, result are all empty
        fields = {i.field for i in criticals}
        assert {"task", "action", "result"} <= fields

    def test_result_without_metrics_flagged(self) -> None:
        score = score_story_coherence(
            "A tricky situation that needed handling carefully and well.",
            "My job was to resolve the production incident quickly.",
            "I led the incident response and coordinated the rollback steps.",
            "The situation was resolved and everyone was happy in the end.",
        )
        msgs = " ".join(i.message for i in score.issues).lower()
        assert "measurable" in msgs

    def test_vague_action_flagged(self) -> None:
        score = score_story_coherence(
            "There was a big project with many moving parts to handle.",
            "I needed to make sure the project shipped on time and well.",
            "I helped with various things and worked on stuff across teams.",
            "It went well and we shipped 3 weeks early.",
        )
        msgs = " ".join(i.message for i in score.issues).lower()
        assert "vague" in msgs


class TestCrossValidateStories:
    def _exp(self, **kw: object) -> StoryInput:
        base: dict[str, object] = dict(
            id="x", title="T", situation="s", task="t", action="a",
            result="r", experience_id="exp1",
        )
        base.update(kw)
        return StoryInput(**base)  # type: ignore[arg-type]

    def test_role_contradiction_is_critical(self) -> None:
        results = cross_validate_stories(
            [
                self._exp(id="a", title="Lead", action="I led the team of engineers"),
                self._exp(
                    id="b",
                    title="Solo",
                    action="I worked independently with no direct reports",
                ),
            ]
        )
        contradictions = [c for r in results for c in r.contradictions]
        assert any(c.severity == "critical" for c in contradictions)
        assert any("role" in c.message.lower() for c in contradictions)

    def test_metric_contradiction_is_major(self) -> None:
        results = cross_validate_stories(
            [
                self._exp(id="a", title="A", result="cut costs by 10 percent"),
                self._exp(id="b", title="B", result="cut costs by 80 percent"),
            ]
        )
        contradictions = [c for r in results for c in r.contradictions]
        assert any(
            c.severity == "major" and "metric" in c.message.lower()
            for c in contradictions
        )

    def test_different_experiences_not_compared(self) -> None:
        results = cross_validate_stories(
            [
                self._exp(id="a", experience_id="e1", action="I led the team"),
                self._exp(
                    id="b", experience_id="e2", action="I worked alone solo"
                ),
            ]
        )
        assert results == []

    def test_single_story_in_experience_skipped(self) -> None:
        results = cross_validate_stories([self._exp(id="a")])
        assert results == []

    def test_stories_without_experience_skipped(self) -> None:
        results = cross_validate_stories(
            [
                self._exp(id="a", experience_id=None, action="I led the team"),
                self._exp(id="b", experience_id=None, action="I worked alone"),
            ]
        )
        assert results == []
