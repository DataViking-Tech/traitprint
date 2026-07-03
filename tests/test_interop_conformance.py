"""Interop conformance suite against the vendored career-ops fixtures.

These are the two GATED test classes from the fixture-suite work (#64):

- ``TestRoundTripConformance`` — exporter conformance. Requires the
  career-bundle exporter (#62): our emitted bundle must be fully parsed
  by the regex family vendored from upstream ``match-star.mjs``, our
  ``config/profile.yml`` must stay structurally compatible with the
  vendored ``config/profile.example.yml``, and importing our own bundle
  back through the story-bank importer must round-trip with full
  fidelity.
- ``TestImportConformance`` — importer conformance. Requires the
  story-bank importer (#63): a story bank written in the exact dialect
  ``match-star.mjs`` documents and parses must be accepted block-for-
  block, and the verbatim vendored profile example must map onto
  ``update_profile`` basics correctly.

The fixture-pin checks (files vendored, license preserved) live in
``tests/test_interop_fixtures.py``; this module is the behavioural half.
"career-ops" appears here only as a nominative reference to the external
project the fixtures pin — it is not a Traitprint product identifier.
The vendored source is MIT-licensed; the upstream LICENSE file sits next
to it under ``tests/fixtures/careerops/career-ops-v1.16.0/``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

from traitprint.export import export_vault_bundle
from traitprint.importers.story_bank import (
    detect_and_plan,
    parse_story_bank,
    profile_proposal,
)
from traitprint.schema import (
    ExperienceSchema,
    LensSchema,
    ProfileSchema,
    SkillSchema,
    StorySchema,
    VaultSchema,
)

PINNED_TAG = "career-ops-v1.16.0"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "careerops" / PINNED_TAG

# ── Vendored parser port ─────────────────────────────────────────────
#
# A faithful Python port of parseStories() from the vendored
# tests/fixtures/careerops/career-ops-v1.16.0/match-star.mjs (MIT).
# The load-bearing regexes, with their upstream source lines:
#
#   line 50: content.split(/^### /m).slice(1)     -> _MS_BLOCK_SPLIT
#   line 57: /^\[([^\]]+)\]\s*(.+)/               -> _MS_THEME_RE
#   line 62: `\\*\\*${label}:\\*\\*\\s*(.+)`      -> _ms_get()
#   lines 68-70: split(/[,;]/) + lowercase        -> tags handling
#   line 72: skip blocks without a title or an
#            'A (Action)'/'Action' field          -> template-skip rule
#
# test_ported_regexes_still_verbatim_in_vendored_source pins the port to
# the fixture so a silent re-vendor cannot leave this port stale.

_MS_BLOCK_SPLIT = re.compile(r"^### ", re.MULTILINE)  # match-star.mjs:50
_MS_THEME_RE = re.compile(r"^\[([^\]]+)\]\s*(.+)")  # match-star.mjs:57


def _ms_get(block: str, label: str) -> str:
    """match-star.mjs:61-65 — first ``**<label>:** value`` line in the
    block; ``label`` is a regex fragment, exactly as upstream."""
    hit = re.search(rf"\*\*{label}:\*\*\s*(.+)", block)
    return hit.group(1).strip() if hit else ""


@dataclass
class MatchStarStory:
    """One story as the vendored parser sees it."""

    title: str
    theme: str
    source: str
    situation: str
    task: str
    action: str
    result: str
    reflection: str
    tags: list[str] = field(default_factory=list)


def _ms_parse_stories(content: str) -> list[MatchStarStory]:
    """Port of parseStories() (match-star.mjs:47-88)."""
    stories: list[MatchStarStory] = []
    for block in _MS_BLOCK_SPLIT.split(content)[1:]:
        header = block.strip().split("\n")[0].strip()
        theme_match = _MS_THEME_RE.match(header)
        theme = theme_match.group(1).strip() if theme_match else ""
        title = theme_match.group(2).strip() if theme_match else header

        tags_raw = _ms_get(block, "Best for questions about")
        tags = [
            t.strip().lower() for t in re.split(r"[,;]", tags_raw) if t.strip()
        ]

        # match-star.mjs:72 — skip template/empty blocks.
        if not title or (
            not _ms_get(block, r"A \(Action\)") and not _ms_get(block, "Action")
        ):
            continue

        stories.append(
            MatchStarStory(
                title=title,
                theme=theme,
                source=_ms_get(block, "Source"),
                situation=_ms_get(block, r"S \(Situation\)")
                or _ms_get(block, "Situation"),
                task=_ms_get(block, r"T \(Task\)") or _ms_get(block, "Task"),
                action=_ms_get(block, r"A \(Action\)")
                or _ms_get(block, "Action"),
                result=_ms_get(block, r"R \(Result\)")
                or _ms_get(block, "Result"),
                reflection=_ms_get(block, "Reflection"),
                tags=tags,
            )
        )
    return stories


# ── Round-trip vault fixture ─────────────────────────────────────────


@pytest.fixture()
def rich_vault() -> VaultSchema:
    """A vault exercising every story-bank surface: themed and bare
    headings, lesson present/absent, experience links with and without
    a company, mixed theme/skill tags, and a multi-line field."""
    python = SkillSchema(name="Python", proficiency=4, category="technical")
    sql = SkillSchema(name="SQL", proficiency=5, category="technical")
    k8s = SkillSchema(name="Kubernetes", proficiency=3, category="technical")
    acme = ExperienceSchema(
        title="Staff Engineer",
        company="Acme",
        start_date="2020-01",
        description="Led the data platform.",
        accomplishments=["Cut warehouse spend 45 percent"],
    )
    solo = ExperienceSchema(title="Founder", start_date="2016-01")
    stories = [
        StorySchema(
            title="Warehouse migration under deadline",
            situation="Redshift costs ballooned: spend doubled in a quarter.",
            task="I owned the migration with a hard six-week deadline.",
            action="I designed dual-writes and cut over table by table.",
            result='Spend down 45%, zero downtime; the CFO called it "boring".',
            lesson="Dual-writes made the cutover boring.",
            theme_tags=["cost", "migration"],
            skill_ids=[sql.id, python.id],
            experience_id=acme.id,
        ),
        StorySchema(
            title="Shipping the deploy pipeline",
            situation="Every release was a hand-run script.",
            task="Automate the path to production.",
            action="I built the pipeline and gated it on smoke tests.",
            result="Releases went from monthly to daily.",
            skill_ids=[k8s.id],
            experience_id=solo.id,
        ),
        StorySchema(
            title="Recovering a failed launch",
            situation="Our flagship client threatened to churn.",
            task="Lead the recovery with two weeks of runway.",
            action=(
                "I triaged the defect list and cut scope to three fixes.\n"
                "Then I ran daily demos until trust recovered."
            ),
            result="The client renewed for two more years.",
            lesson="Visible progress rebuilds trust faster than promises.",
            theme_tags=["incident-response"],
        ),
    ]
    return VaultSchema(
        profile=ProfileSchema(
            display_name="Jordan Vance",
            headline="Data Platform Engineer",
            summary="Eight years building boring, reliable data platforms.",
            location="Portland, OR",
            contact_email="jordan@example.com",
        ),
        skills=[python, sql, k8s],
        experiences=[acme, solo],
        stories=stories,
        lenses=[
            LensSchema(
                slug="platform-lead",
                name="Platform Lead",
                target_archetypes=[
                    "Data Platform Lead",
                    "Infrastructure Engineering Manager",
                ],
                is_default=True,
            ),
            LensSchema(
                slug="ic-track",
                name="IC Track",
                target_archetypes=["Senior Data Engineer"],
            ),
        ],
    )


@pytest.fixture()
def bundle(rich_vault: VaultSchema) -> dict[str, str]:
    return export_vault_bundle(rich_vault, "career-bundle")


@pytest.fixture()
def bundle_workdir(bundle: dict[str, str], tmp_path: Path) -> Path:
    for rel, content in bundle.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp_path


# ── Class 1: exporter round-trip conformance ─────────────────────────


class TestRoundTripConformance:
    """Our exported bundle must satisfy the vendored consumer verbatim,
    and re-import through our own importer with full fidelity."""

    def test_ported_regexes_still_verbatim_in_vendored_source(self) -> None:
        """Pin the Python port above to the vendored source so a
        re-vendor that changes the parser fails here first."""
        src = (FIXTURES / "match-star.mjs").read_text(encoding="utf-8")
        assert "content.split(/^### /m).slice(1)" in src  # line 50
        assert r"/^\[([^\]]+)\]\s*(.+)/" in src  # line 57
        assert r"\\*\\*${label}:\\*\\*\\s*(.+)" in src  # line 62
        assert "tagsRaw.split(/[,;]/)" in src  # line 69
        assert r"!get('A \\(Action\\)') && !get('Action')" in src  # line 72

    def test_every_story_extracted_no_template_skips(
        self, rich_vault: VaultSchema, bundle: dict[str, str]
    ) -> None:
        parsed = _ms_parse_stories(bundle["interview-prep/story-bank.md"])
        assert [p.title for p in parsed] == [s.title for s in rich_vault.stories]
        # The vendored skip rule (line 72) drops blocks without an
        # Action — none of our emitted blocks may look like templates.
        assert all(p.action for p in parsed)

    def test_extracted_fields_match_vault_content(
        self, rich_vault: VaultSchema, bundle: dict[str, str]
    ) -> None:
        parsed = _ms_parse_stories(bundle["interview-prep/story-bank.md"])
        skill_names = {s.id: s.name for s in rich_vault.skills}
        for p, story in zip(parsed, rich_vault.stories, strict=True):
            assert p.theme == (story.theme_tags[0] if story.theme_tags else "")
            # The vendored field regex (line 62) captures a single line;
            # multi-line fields are emitted as continuation lines, so
            # the vendored matcher sees the first line of each field.
            assert p.situation == story.situation.split("\n")[0]
            assert p.task == story.task.split("\n")[0]
            assert p.action == story.action.split("\n")[0]
            assert p.result == story.result.split("\n")[0]
            assert p.reflection == story.lesson.split("\n")[0]
            expected_tags = [
                t.lower()
                for t in [
                    *story.theme_tags,
                    *(skill_names[i] for i in story.skill_ids),
                ]
            ]
            assert p.tags == expected_tags

    def test_source_lines_extracted(self, bundle: dict[str, str]) -> None:
        parsed = _ms_parse_stories(bundle["interview-prep/story-bank.md"])
        assert [p.source for p in parsed] == [
            "Staff Engineer — Acme",  # experience with a company
            "Founder",  # experience without one
            "",  # story with no experience link
        ]

    def test_profile_yaml_structure_matches_vendored_example(
        self, bundle: dict[str, str]
    ) -> None:
        ours = yaml.safe_load(bundle["config/profile.yml"])
        vendored = yaml.safe_load(
            (FIXTURES / "config" / "profile.example.yml").read_text(
                encoding="utf-8"
            )
        )
        for section in ("candidate", "narrative", "target_roles"):
            assert isinstance(ours[section], dict), section
            assert isinstance(vendored[section], dict), section
        # Keys shared with the vendored example must keep its types.
        for section in ("candidate", "narrative"):
            for key, value in ours[section].items():
                if key in vendored[section]:
                    assert type(value) is type(vendored[section][key]), (
                        f"{section}.{key}"
                    )
        # target_roles.archetypes: same nesting — a list of mappings
        # whose keys and fit vocabulary are subsets of the vendored ones.
        ours_arch = ours["target_roles"]["archetypes"]
        vend_arch = vendored["target_roles"]["archetypes"]
        assert isinstance(ours_arch, list) and ours_arch
        assert isinstance(vend_arch, list) and vend_arch
        vend_keys = set().union(*(set(a) for a in vend_arch))
        vend_fits = {a["fit"] for a in vend_arch}  # primary/secondary/adjacent
        for entry in ours_arch:
            assert set(entry) <= vend_keys
            assert isinstance(entry["name"], str)
            assert entry["fit"] in vend_fits

    def test_reimport_round_trips_star_fields_with_full_fidelity(
        self, rich_vault: VaultSchema, bundle_workdir: Path
    ) -> None:
        plan = detect_and_plan(bundle_workdir, rich_vault)
        assert plan.has_cv is True
        assert [p.title for p, _ in plan.stories] == [
            s.title for s in rich_vault.stories
        ]
        for (parsed, _), story in zip(
            plan.stories, rich_vault.stories, strict=True
        ):
            assert parsed.situation == story.situation
            assert parsed.task == story.task
            assert parsed.action == story.action  # incl. the multi-line one
            assert parsed.result == story.result
            assert parsed.lesson == story.lesson

    def test_reimport_restores_links_themes_and_profile(
        self, rich_vault: VaultSchema, bundle_workdir: Path
    ) -> None:
        plan = detect_and_plan(bundle_workdir, rich_vault)
        for (_, payload), story in zip(
            plan.stories, rich_vault.stories, strict=True
        ):
            assert payload.get("theme_tags", []) == list(story.theme_tags)
            assert payload.get("skill_ids", []) == [
                str(i) for i in story.skill_ids
            ]
            if story.experience_id is not None:
                assert payload["experience_id"] == str(story.experience_id)
            else:
                assert "experience_id" not in payload
        assert plan.profile_payload == {
            "basics": {
                "name": "Jordan Vance",
                "email": "jordan@example.com",
                "location": "Portland, OR",
                "label": "Data Platform Engineer",
                "summary": "Eight years building boring, reliable data platforms.",
            }
        }
        # Archetypes ride in the rationale (no lens proposal kind yet).
        assert "Data Platform Lead" in plan.profile_rationale
        assert "Senior Data Engineer" in plan.profile_rationale


# ── Class 2: importer dialect conformance ────────────────────────────

# A story bank written in the exact dialect match-star.mjs documents and
# parses (header + field-line conventions from its parseStories(),
# lines 47-88; usage narrative from its module docstring, lines 3-15):
# '### [theme] Title' headings, bold '**Label:** value' field lines with
# the canonical Situation/Task/Action/Result/Reflection/Source labels,
# a comma-separated '**Best for questions about:**' tag line, and a
# trailing template block that the line-72 skip rule drops.
DIALECT_STORY_BANK = """\
# Story Bank

Reusable STAR stories for behavioural interviews.

### [leadership] Led a project under pressure

**Situation:** Our flagship client threatened to churn after a failed launch.
**Task:** I was asked to lead the recovery with two weeks of runway.
**Action:** I triaged the defects, cut scope to three fixes, ran daily demos.
**Result:** The client renewed for two more years.
**Reflection:** Visible progress rebuilds trust faster than promises.
**Source:** Staff Engineer — Acme
**Best for questions about:** leadership, pressure, stakeholder management

### [conflict] Resolved a schema design disagreement

**Situation:** Two teams deadlocked on the events schema for a quarter.
**Task:** I had to get both to commit to one contract.
**Action:** I wrote both proposals up as ADRs and benchmarked them.
**Result:** We shipped the merged schema in three weeks.
**Reflection:** Benchmarks end arguments that opinions start.
**Best for questions about:** conflict, Python, influence

### Handling ambiguity on day one

**Situation:** I joined a team with no roadmap and a flaky pipeline.
**Task:** Find the highest-leverage fix within a month.
**Action:** I instrumented the pipeline and fixed the top failure mode.
**Result:** On-call pages dropped by half.

### New story template

_Copy this block for each new story; delete this placeholder line._
"""


class TestImportConformance:
    """Our importer must accept the exact dialect the vendored matcher
    parses, block for block, and map the vendored profile keys."""

    def test_dialect_fixture_is_the_vendored_dialect(self) -> None:
        """Validity anchor: the constructed fixture parses under the
        vendored regexes exactly as intended."""
        parsed = _ms_parse_stories(DIALECT_STORY_BANK)
        assert [p.title for p in parsed] == [
            "Led a project under pressure",
            "Resolved a schema design disagreement",
            "Handling ambiguity on day one",
        ]
        assert parsed[0].theme == "leadership"
        assert parsed[2].theme == ""  # bare heading, no [theme] prefix

    def test_importer_parses_every_block(self) -> None:
        stories = parse_story_bank(DIALECT_STORY_BANK, origin="story-bank.md")
        assert [s.title for s in stories] == [
            "Led a project under pressure",
            "Resolved a schema design disagreement",
            "Handling ambiguity on day one",
        ]
        first = stories[0]
        assert first.theme == "leadership"
        assert first.source == "Staff Engineer — Acme"
        assert first.tags == ["leadership", "pressure", "stakeholder management"]
        assert first.lesson == (
            "Visible progress rebuilds trust faster than promises."
        )

    def test_importer_agrees_with_vendored_parser_field_by_field(self) -> None:
        """The strongest conformance statement: on canonical-dialect
        input, our parser and the vendored parser extract identical
        stories (tags compared casefolded — the vendored parser
        lowercases, ours preserves for skill-name resolution)."""
        ours = parse_story_bank(DIALECT_STORY_BANK)
        theirs = _ms_parse_stories(DIALECT_STORY_BANK)
        assert len(ours) == len(theirs)
        for mine, ms in zip(ours, theirs, strict=True):
            assert mine.title == ms.title
            assert mine.theme == ms.theme
            assert mine.situation == ms.situation
            assert mine.task == ms.task
            assert mine.action == ms.action
            assert mine.result == ms.result
            assert mine.lesson == ms.reflection
            assert mine.source == ms.source
            assert [t.lower() for t in mine.tags] == ms.tags

    def test_template_block_skipped_by_both_parsers(self) -> None:
        ms_titles = [p.title for p in _ms_parse_stories(DIALECT_STORY_BANK)]
        our_titles = [s.title for s in parse_story_bank(DIALECT_STORY_BANK)]
        assert "New story template" not in ms_titles
        assert "New story template" not in our_titles

    def test_vendored_profile_example_maps_keys_correctly(self) -> None:
        raw = (FIXTURES / "config" / "profile.example.yml").read_text(
            encoding="utf-8"
        )
        payload, rationale = profile_proposal(raw)
        assert payload is not None
        basics = payload["basics"]
        assert basics["email"] == "jane@example.com"
        assert basics["location"] == "San Francisco, CA"
        assert basics["label"] == "ML Engineer turned AI product builder"
        # Upstream's candidate.full_name / narrative.exit_story have no
        # exact counterpart in the importer's key map and must NOT be
        # guessed into basics (mappings are exact, never invented).
        assert "name" not in basics
        assert "summary" not in basics
        # Every archetype rides in the rationale for the reviewer.
        for archetype in (
            "AI/ML Engineer",
            "AI Product Manager",
            "Solutions Architect",
        ):
            assert archetype in rationale

    def test_full_workdir_import_plan(self, tmp_path: Path) -> None:
        (tmp_path / "config").mkdir()
        (tmp_path / "interview-prep").mkdir()
        (tmp_path / "config" / "profile.yml").write_text(
            (FIXTURES / "config" / "profile.example.yml").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )
        (tmp_path / "interview-prep" / "story-bank.md").write_text(
            DIALECT_STORY_BANK, encoding="utf-8"
        )
        python = SkillSchema(name="Python", proficiency=4, category="technical")
        acme = ExperienceSchema(title="Staff Engineer", company="Acme")
        vault = VaultSchema(skills=[python], experiences=[acme])

        plan = detect_and_plan(tmp_path, vault)
        assert len(plan.stories) == 3
        assert plan.has_cv is False
        # Source line resolves to the vault experience, exact match only.
        assert plan.stories[0][1]["experience_id"] == str(acme.id)
        # 'Python' resolves to the vault skill; the rest stay themes.
        second_payload = plan.stories[1][1]
        assert second_payload["skill_ids"] == [str(python.id)]
        assert second_payload["theme_tags"] == ["conflict", "influence"]
        assert plan.profile_payload is not None
        assert plan.profile_payload["basics"]["email"] == "jane@example.com"
