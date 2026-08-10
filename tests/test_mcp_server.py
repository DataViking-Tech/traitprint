"""Tests for the MCP stdio server.

Covers:
- Tool registration (list_tools returns every registered tool; the
  canonical name set is pinned in TestServerRegistration).
- In-process tool invocation for each tool, asserting response schemas
  match the cloud shape (envelope + per-tool payload keys).
- End-to-end JSON-RPC round-trip over stdio against ``traitprint
  mcp-serve`` using the MCP Python client.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import ValidationError

from traitprint.git_ops import commit, init_repo
from traitprint.mcp_server import (
    DISPUTE_SOURCE,
    RESPONSE_CONTRACT_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    _audit_coherence_prompt,
    _bullet_salience,
    _coerce_min_proficiency,
    _compute_disputes,
    _deepen_story_prompt,
    _discover_skills_prompt,
    _draft_star_story_prompt,
    _envelope,
    _fill_vault_prompt,
    _flag_order_key,
    _handle_find_bullets,
    _handle_find_story,
    _handle_get_philosophy,
    _handle_get_profile_summary,
    _handle_search_skills,
    _handle_vault_lens_get,
    _handle_vault_lens_list,
    _handle_vault_sync,
    _improve_profile_prompt,
    _map_proficiency,
    _meets_proficiency,
    _mine_story_gaps_prompt,
    _position_lens_prompt,
    _resolve_lens,
    _story_evidence_by_skill,
    create_server,
)
from traitprint.schema import (
    MAX_LENSES,
    ArtifactLink,
    BulletSchema,
    ExperienceSchema,
    ExperienceScope,
    LensSchema,
    PhilosophyCategory,
    PhilosophySchema,
    ProfileSchema,
    SalienceLevel,
    SkillSchema,
    StorySchema,
    VaultSchema,
)
from traitprint.taxonomy import load_taxonomy
from traitprint.vault import VaultStore

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    d = tmp_path / "vault"
    d.mkdir()
    init_repo(d)
    return d


@pytest.fixture()
def populated_store(vault_dir: Path) -> VaultStore:
    """A VaultStore with a representative set of skills, stories, etc."""
    store = VaultStore(vault_dir)
    vault = VaultSchema(
        profile=ProfileSchema(
            display_name="Wesley Johnson",
            headline="Data Engineering Leader",
            summary="Shipping data products for a decade.",
        ),
    )
    taxonomy = load_taxonomy()
    python_tax = next(e for e in taxonomy if e.name == "Python")

    python_skill = SkillSchema(
        name="Python",
        category="technical",
        proficiency=5,
        taxonomy_id=python_tax.id,
        notes="Primary language",
    )
    sql_skill = SkillSchema(name="SQL", category="technical", proficiency=4)
    leadership_skill = SkillSchema(
        name="Team Leadership", category="soft", proficiency=3
    )
    vault.skills = [python_skill, sql_skill, leadership_skill]

    experience = ExperienceSchema(
        title="Staff Data Engineer",
        company="Acme",
        start_date="2020-01",
        description="Led the data platform.",
        skill_ids=[python_skill.id, sql_skill.id],
    )
    vault.experiences = [experience]

    story_win = StorySchema(
        title="Redshift to BigQuery Migration",
        situation="Redshift costs ballooning on growing pipeline volume.",
        task="Lead migration to BigQuery without pipeline downtime.",
        action="Ran dual-writes, backfilled historical data, cut over.",
        result="Cut warehouse spend 45 percent with zero downtime.",
        skill_ids=[python_skill.id, sql_skill.id],
    )
    story_incomplete = StorySchema(
        title="Incomplete Story",
        situation="Only situation filled in.",
    )
    vault.stories = [story_win, story_incomplete]

    phil = PhilosophySchema(
        title="Delegation as Leverage",
        description="Trust senior engineers to own outcomes end to end.",
        category=PhilosophyCategory.LEADERSHIP,
        evidence_story_ids=[story_win.id],
    )
    vault.philosophies = [phil]

    store.save(vault)
    commit(vault_dir, "seed test vault")
    return store


# ── Unit helpers ────────────────────────────────────────────────────


class TestProficiencyMapping:
    def test_bucket_edges(self) -> None:
        # Five-label contract, one label per level: 1 familiar, 2 working,
        # 3 proficient, 4 expert, 5 authority.
        assert _map_proficiency(1) == "familiar"
        assert _map_proficiency(2) == "working"
        assert _map_proficiency(3) == "proficient"
        assert _map_proficiency(4) == "expert"
        assert _map_proficiency(5) == "authority"

    def test_meets_proficiency(self) -> None:
        assert _meets_proficiency("expert", "working")
        assert _meets_proficiency("expert", "proficient")
        assert _meets_proficiency("authority", "authority")
        assert not _meets_proficiency("familiar", "working")
        assert not _meets_proficiency("working", "proficient")

    def test_coerce_accepts_all_five_labels(self) -> None:
        for label in ("familiar", "working", "proficient", "expert", "authority"):
            assert _coerce_min_proficiency(label) == label

    def test_coerce_accepts_integers_1_to_5(self) -> None:
        assert _coerce_min_proficiency(1) == "familiar"
        assert _coerce_min_proficiency(3) == "proficient"
        assert _coerce_min_proficiency(5) == "authority"

    def test_coerce_rejects_bad_values(self) -> None:
        with pytest.raises(ValueError, match="min_proficiency"):
            _coerce_min_proficiency(0)
        with pytest.raises(ValueError, match="min_proficiency"):
            _coerce_min_proficiency(6)
        with pytest.raises(ValueError, match="min_proficiency"):
            _coerce_min_proficiency("ninja")

    def test_coerce_none_passthrough(self) -> None:
        assert _coerce_min_proficiency(None) is None


class TestEnvelope:
    def test_shape(self) -> None:
        env = _envelope({"foo": "bar"})
        assert set(env) == {"result", "meta"}
        assert env["result"] == {"foo": "bar"}
        assert env["meta"]["server_version"] == RESPONSE_CONTRACT_VERSION == "1.1.0"
        assert env["meta"]["trust_layer_status"] == "active"
        # ISO-8601 UTC parseable
        datetime.fromisoformat(env["meta"]["generated_at"].replace("Z", "+00:00"))


# ── Tool handlers (direct) ──────────────────────────────────────────


class TestGetProfileSummary:
    def test_brief_returns_headline_and_bio_only(
        self, populated_store: VaultStore
    ) -> None:
        out = _handle_get_profile_summary(populated_store.load(), "brief")
        assert out == {
            "headline": "Data Engineering Leader",
            "bio": "Shipping data products for a decade.",
        }

    def test_standard_includes_top_skills(self, populated_store: VaultStore) -> None:
        out = _handle_get_profile_summary(populated_store.load(), "standard")
        assert "top_skills" in out
        # Highest proficiency first
        assert out["top_skills"][0]["name"] == "Python"
        assert out["top_skills"][0]["proficiency"] == "authority"
        for skill in out["top_skills"]:
            assert set(skill) == {
                "name",
                "id",
                "proficiency",
                "evidence",
                "disputed",
                "dispute",
            }
            # Hosted-mirror: the id is the vault skill UUID vault_propose's
            # skill_ids takes.
            assert UUID(skill["id"])
            assert skill["disputed"] is False
            assert skill["dispute"] is None
        assert "signature_experiences" not in out

    def test_detailed_includes_experiences_and_philosophies(
        self, populated_store: VaultStore
    ) -> None:
        out = _handle_get_profile_summary(populated_store.load(), "detailed")
        assert "signature_experiences" in out
        assert "core_philosophies" in out
        phil = out["core_philosophies"][0]
        assert set(phil) == {"topic", "stance", "evidence", "disputed", "dispute"}
        assert phil["topic"] == "Delegation as Leverage"

    def test_detailed_experiences_carry_related_skills(
        self, populated_store: VaultStore
    ) -> None:
        # Contract revision 1.1: experience skill links surface as skill
        # names, mirroring find_story's related_skills.
        out = _handle_get_profile_summary(populated_store.load(), "detailed")
        exp = out["signature_experiences"][0]
        assert set(exp) == {
            "title",
            "organization",
            "period",
            "related_skills",
            "related_skill_ids",
            "evidence",
            "disputed",
            "dispute",
        }
        assert exp["title"] == "Staff Data Engineer"
        assert set(exp["related_skills"]) == {"Python", "SQL"}
        # Index-aligned with related_skills (filtered once, hosted-mirror).
        assert len(exp["related_skill_ids"]) == len(exp["related_skills"])
        for sid in exp["related_skill_ids"]:
            assert UUID(sid)

    def test_related_skills_skip_dangling_refs(
        self, populated_store: VaultStore
    ) -> None:
        vault = populated_store.load()
        vault.experiences[0].skill_ids.append(uuid4())  # dangling
        out = _handle_get_profile_summary(vault, "detailed")
        exp = out["signature_experiences"][0]
        assert set(exp["related_skills"]) == {"Python", "SQL"}

    def test_detailed_experience_scope_included_when_present(
        self, populated_store: VaultStore
    ) -> None:
        # Contract revision 1.5: the scope block travels with the
        # experience, carrying only its set fields (0/False are set).
        vault = populated_store.load()
        vault.experiences[0].scope = ExperienceScope(
            reporting_line="VP of Data",
            direct_reports=6,
            functions_owned=["analytics eng", "data platform"],
            hiring_authority=False,
        )
        out = _handle_get_profile_summary(vault, "detailed")
        exp = out["signature_experiences"][0]
        assert exp["scope"] == {
            "reporting_line": "VP of Data",
            "direct_reports": 6,
            "functions_owned": ["analytics eng", "data platform"],
            "hiring_authority": False,
        }

    def test_detailed_experience_scope_key_absent_without_scope(
        self, populated_store: VaultStore
    ) -> None:
        # No scope on the role: the payload shape is exactly the pre-1.5
        # one — the key is absent, never null.
        out = _handle_get_profile_summary(populated_store.load(), "detailed")
        assert "scope" not in out["signature_experiences"][0]

    def test_detailed_experience_artifact_links_included_when_present(
        self, populated_store: VaultStore
    ) -> None:
        # Contract revision 1.6: evidence links ride the experience; each
        # entry carries only its set fields (no ``label: null``).
        vault = populated_store.load()
        vault.experiences[0].artifact_links = [
            ArtifactLink(url="https://github.com/acme/platform", label="repo"),
            ArtifactLink(url="https://acme.example.com/blog/migration"),
        ]
        out = _handle_get_profile_summary(vault, "detailed")
        exp = out["signature_experiences"][0]
        assert exp["artifact_links"] == [
            {"url": "https://github.com/acme/platform", "label": "repo"},
            {"url": "https://acme.example.com/blog/migration"},
        ]

    def test_detailed_experience_artifact_links_key_absent_when_empty(
        self, populated_store: VaultStore
    ) -> None:
        # No links on the role: the payload shape is exactly the pre-1.6
        # one — the key is absent, never an empty list.
        out = _handle_get_profile_summary(populated_store.load(), "detailed")
        assert "artifact_links" not in out["signature_experiences"][0]


class _StubSyncClient:
    """Stands in for GitSyncClient — no HTTP, just the context protocol."""

    @classmethod
    def from_credentials(cls, _creds: object) -> _StubSyncClient:
        return cls()

    def __enter__(self) -> _StubSyncClient:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class TestVaultSync:
    """vault_sync tool handler — wraps the CLI's gitsync engine."""

    @pytest.fixture(autouse=True)
    def _signed_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from traitprint.credentials import Credentials

        monkeypatch.setattr(
            "traitprint.credentials.resolve_credentials",
            lambda _d: Credentials(
                api_url="https://api.example", email="e@x", token="tok"
            ),
        )
        monkeypatch.setattr("traitprint.gitsync.GitSyncClient", _StubSyncClient)

    def test_rejects_unknown_action(self, populated_store: VaultStore) -> None:
        with pytest.raises(ValueError, match="status, push, pull"):
            _handle_vault_sync(populated_store, "force")

    def test_not_signed_in_is_a_tool_error(
        self, populated_store: VaultStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "traitprint.credentials.resolve_credentials", lambda _d: None
        )
        with pytest.raises(ValueError, match="traitprint login"):
            _handle_vault_sync(populated_store, "status")

    def test_status_maps_outcome(
        self, populated_store: VaultStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from traitprint import gitsync

        st = gitsync.StatusOutcome(
            local_head="a" * 40,
            server_head="b" * 40,
            relation="diverged",
            ingest=gitsync.IngestReport(
                status="quarantined",
                quarantined=[{"file": "stories/x.md", "reason": "dangling"}],
            ),
        )
        monkeypatch.setattr("traitprint.gitsync.sync_status", lambda _d, _c: st)
        out = _handle_vault_sync(populated_store, "status")
        assert out == {
            "action": "status",
            "local_head": "a" * 40,
            "server_head": "b" * 40,
            "relation": "diverged",
            "ingest_status": "quarantined",
            "quarantined": [{"file": "stories/x.md", "reason": "dangling"}],
        }

    def test_push_non_fast_forward_is_structured_not_raised(
        self, populated_store: VaultStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from traitprint import gitsync

        def _raise(_d: object, _c: object) -> object:
            raise gitsync.NonFastForwardError(
                "server is ahead", "b" * 40, "pull first, then push"
            )

        monkeypatch.setattr("traitprint.gitsync.sync_push", _raise)
        out = _handle_vault_sync(populated_store, "push")
        assert out["pushed"] is False
        assert out["server_head"] == "b" * 40
        assert out["error"]["code"] == "non_fast_forward"
        assert "pull first" in out["error"]["hint"]

    def test_push_maps_outcome_and_warnings(
        self, populated_store: VaultStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from traitprint import gitsync

        po = gitsync.PushOutcome(
            pushed=True,
            head="a" * 40,
            server_head="a" * 40,
            ingest=gitsync.IngestReport(status="clean"),
            commits=2,
            warnings=[
                gitsync.PushWarning(
                    file="proposals/add-skill.json",
                    pointer="/payload/proficiency",
                    message="m",
                    hint="h",
                )
            ],
        )
        monkeypatch.setattr("traitprint.gitsync.sync_push", lambda _d, _c: po)
        out = _handle_vault_sync(populated_store, "push")
        assert out["pushed"] is True
        assert out["commits"] == 2
        assert out["ingest_status"] == "clean"
        assert out["warnings"] == [
            {
                "file": "proposals/add-skill.json",
                "pointer": "/payload/proficiency",
                "message": "m",
                "hint": "h",
            }
        ]

    def test_pull_conflicts_carry_a_resolution_hint(
        self, populated_store: VaultStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from traitprint import gitsync

        pl = gitsync.PullOutcome(
            fetched=True,
            mode="conflicts",
            conflicts=["stories/x.md"],
            head="a" * 40,
            server_head="b" * 40,
        )
        monkeypatch.setattr("traitprint.gitsync.sync_pull", lambda _d, _c: pl)
        out = _handle_vault_sync(populated_store, "pull")
        assert out["result"] == "conflicts"
        assert out["conflicts"] == ["stories/x.md"]
        assert "resolve the listed files" in out["hint"]

    def test_auth_failure_raises_tool_error(
        self, populated_store: VaultStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from traitprint import gitsync

        def _raise(_d: object, _c: object) -> object:
            raise gitsync.SyncAuthError("token expired", "log in again")

        monkeypatch.setattr("traitprint.gitsync.sync_status", _raise)
        with pytest.raises(ValueError, match="token expired"):
            _handle_vault_sync(populated_store, "status")


class TestStoryEvidence:
    def test_draft_story_is_not_evidence(self) -> None:
        # Evidence is complete-STAR only (StorySchema.is_complete_star) —
        # a draft must not inflate evidence_count while find_story returns
        # nothing and the audit flags the skill unsupported.
        skill_id = uuid4()
        draft = StorySchema(title="Draft", situation="s", skill_ids=[skill_id])
        assert _story_evidence_by_skill([draft]) == {}

    def test_complete_story_counts_once_alongside_draft(self) -> None:
        skill_id = uuid4()
        draft = StorySchema(title="Draft", situation="s", skill_ids=[skill_id])
        complete = StorySchema(
            title="Full",
            situation="s",
            task="t",
            action="a",
            result="Shipped it.",
            skill_ids=[skill_id],
        )
        out = _story_evidence_by_skill([complete, draft])
        assert out[skill_id]["count"] == 1
        assert out[skill_id]["top"] == "Shipped it."


class TestSearchSkills:
    def test_taxonomy_match(self, populated_store: VaultStore) -> None:
        out = _handle_search_skills(
            populated_store.load(), load_taxonomy(), "python", None, 10
        )
        names = [m["name"] for m in out["matches"]]
        assert "Python" in names
        top = next(m for m in out["matches"] if m["name"] == "Python")
        assert top["proficiency"] == "authority"
        assert top["canonical_name"] == "Python"
        assert top["match_distance"] == 0.0
        assert top["evidence_count"] == 1
        assert top["top_evidence"]  # has a snippet from the story
        assert UUID(top["id"])  # vault skill id, hosted-mirror
        assert set(top) == {
            "name",
            "id",
            "canonical_name",
            "proficiency",
            "years_active",
            "evidence_count",
            "top_evidence",
            "match_distance",
            "evidence",
            "disputed",
            "dispute",
        }
        qi = out["query_interpretation"]
        assert set(qi) == {
            "matched_taxonomy_ids",
            "used_alias",
            "used_distance_graph",
        }
        assert qi["used_distance_graph"] is False
        assert len(qi["matched_taxonomy_ids"]) >= 1

    def test_alias_match_sets_used_alias(self, populated_store: VaultStore) -> None:
        out = _handle_search_skills(
            populated_store.load(), load_taxonomy(), "python3", None, 10
        )
        assert out["query_interpretation"]["used_alias"] is True

    def test_min_proficiency_filter(self, populated_store: VaultStore) -> None:
        out = _handle_search_skills(
            populated_store.load(), load_taxonomy(), "sql", "expert", 10
        )
        # SQL is 4/5 → expert, which meets expert.
        assert any(m["name"] == "SQL" for m in out["matches"])

        out = _handle_search_skills(
            populated_store.load(), load_taxonomy(), "sql", "authority", 10
        )
        assert all(m["name"] != "SQL" for m in out["matches"])

    def test_proficient_label_accepted_and_level_3_renders_proficient(
        self, populated_store: VaultStore
    ) -> None:
        # Team Leadership is 3/5 → "proficient" (not folded into working).
        out = _handle_search_skills(
            populated_store.load(), load_taxonomy(), "leadership", "proficient", 10
        )
        match = next(m for m in out["matches"] if m["name"] == "Team Leadership")
        assert match["proficiency"] == "proficient"

        # "expert" excludes the level-3 skill.
        out = _handle_search_skills(
            populated_store.load(), load_taxonomy(), "leadership", "expert", 10
        )
        assert all(m["name"] != "Team Leadership" for m in out["matches"])

    def test_min_proficiency_accepts_integers(
        self, populated_store: VaultStore
    ) -> None:
        out = _handle_search_skills(
            populated_store.load(), load_taxonomy(), "sql", 4, 10
        )
        assert any(m["name"] == "SQL" for m in out["matches"])
        out = _handle_search_skills(
            populated_store.load(), load_taxonomy(), "sql", 5, 10
        )
        assert all(m["name"] != "SQL" for m in out["matches"])

    def test_min_proficiency_invalid_raises_actionable_error(
        self, populated_store: VaultStore
    ) -> None:
        with pytest.raises(ValueError, match="proficient"):
            _handle_search_skills(
                populated_store.load(), load_taxonomy(), "sql", "wizard", 10
            )

    def test_name_fallback_without_taxonomy(self, populated_store: VaultStore) -> None:
        out = _handle_search_skills(
            populated_store.load(), load_taxonomy(), "leadership", None, 10
        )
        assert any(m["name"] == "Team Leadership" for m in out["matches"])

    def test_multiword_query_token_match(self, vault_dir: Path) -> None:
        """'Python programming' finds both Python and user-added 'python scripting'."""
        store = VaultStore(vault_dir)
        vault = VaultSchema(
            profile=ProfileSchema(display_name="t"),
        )
        taxonomy = load_taxonomy()
        python_tax = next(e for e in taxonomy if e.name == "Python")
        vault.skills = [
            SkillSchema(
                name="Python",
                category="technical",
                proficiency=5,
                taxonomy_id=python_tax.id,
            ),
            SkillSchema(name="python scripting", category="technical", proficiency=3),
            SkillSchema(name="Team Leadership", category="soft", proficiency=3),
        ]
        store.save(vault)

        out = _handle_search_skills(
            store.load(), taxonomy, "Python programming", None, 10
        )
        names = {m["name"] for m in out["matches"]}
        assert "Python" in names
        assert "python scripting" in names
        assert "Team Leadership" not in names

    def test_alias_expands_to_synonym_matches(self, vault_dir: Path) -> None:
        """Query via alias ('golang') matches user skill 'Go services'."""
        store = VaultStore(vault_dir)
        vault = VaultSchema(
            profile=ProfileSchema(display_name="t"),
        )
        vault.skills = [
            SkillSchema(name="Go services", category="technical", proficiency=4),
            SkillSchema(name="React", category="technical", proficiency=3),
        ]
        store.save(vault)

        out = _handle_search_skills(store.load(), load_taxonomy(), "golang", None, 10)
        names = {m["name"] for m in out["matches"]}
        assert "Go services" in names
        assert out["query_interpretation"]["used_alias"] is True

    def test_distance_graph_bridges_related_concepts(self, vault_dir: Path) -> None:
        """Querying 'machine learning' surfaces a Python-tagged skill via the graph."""
        store = VaultStore(vault_dir)
        vault = VaultSchema(
            profile=ProfileSchema(display_name="t"),
        )
        taxonomy = load_taxonomy()
        python_tax = next(e for e in taxonomy if e.name == "Python")
        vault.skills = [
            SkillSchema(
                name="Python",
                category="technical",
                proficiency=5,
                taxonomy_id=python_tax.id,
            ),
        ]
        store.save(vault)

        out = _handle_search_skills(
            store.load(), taxonomy, "machine learning", None, 10
        )
        names = [m["name"] for m in out["matches"]]
        assert "Python" in names
        qi = out["query_interpretation"]
        assert qi["used_distance_graph"] is True
        # Python is a one-hop neighbor of Machine Learning, so the match
        # distance must be strictly positive (graph) and strictly below 1.
        python_match = next(m for m in out["matches"] if m["name"] == "Python")
        assert 0.0 < python_match["match_distance"] < 1.0

    def test_distance_graph_prefers_direct_over_graph(self, vault_dir: Path) -> None:
        """Direct taxonomy hit outranks a graph-only hit on the same query."""
        store = VaultStore(vault_dir)
        vault = VaultSchema(
            profile=ProfileSchema(display_name="t"),
        )
        taxonomy = load_taxonomy()
        react_tax = next(e for e in taxonomy if e.name == "React")
        vue_tax = next(e for e in taxonomy if e.name == "Vue.js")
        vault.skills = [
            SkillSchema(
                name="React",
                category="technical",
                proficiency=4,
                taxonomy_id=react_tax.id,
            ),
            SkillSchema(
                name="Vue.js",
                category="technical",
                proficiency=4,
                taxonomy_id=vue_tax.id,
            ),
        ]
        store.save(vault)

        out = _handle_search_skills(store.load(), taxonomy, "react", None, 10)
        names = [m["name"] for m in out["matches"]]
        # React comes first (direct, distance 0); Vue is still present via graph.
        assert names[0] == "React"
        assert "Vue.js" in names
        react_match = next(m for m in out["matches"] if m["name"] == "React")
        vue_match = next(m for m in out["matches"] if m["name"] == "Vue.js")
        assert react_match["match_distance"] == 0.0
        assert vue_match["match_distance"] > 0.0
        assert out["query_interpretation"]["used_distance_graph"] is True

    def test_distance_graph_flag_false_when_only_direct_hits(
        self, vault_dir: Path
    ) -> None:
        """used_distance_graph stays False when no graph-only skill surfaces."""
        store = VaultStore(vault_dir)
        vault = VaultSchema(
            profile=ProfileSchema(display_name="t"),
        )
        taxonomy = load_taxonomy()
        python_tax = next(e for e in taxonomy if e.name == "Python")
        vault.skills = [
            SkillSchema(
                name="Python",
                category="technical",
                proficiency=5,
                taxonomy_id=python_tax.id,
            ),
        ]
        store.save(vault)

        out = _handle_search_skills(store.load(), taxonomy, "python", None, 10)
        assert out["query_interpretation"]["used_distance_graph"] is False


class TestFindStory:
    def test_requires_at_least_one_filter(self, populated_store: VaultStore) -> None:
        with pytest.raises(ValueError) as excinfo:
            _handle_find_story(populated_store.load(), None, None, None, 3)
        # Error message guides callers to the available filters (tp-7wo).
        msg = str(excinfo.value)
        for name in ("query", "situation", "theme", "outcome"):
            assert name in msg

    def test_query_param_matches_across_star_fields(
        self, populated_store: VaultStore
    ) -> None:
        # Free-text 'query' is the ergonomic fallback (tp-7wo).
        for kw in ("migration", "ballooning", "dual-writes", "warehouse"):
            out = _handle_find_story(
                populated_store.load(), None, None, None, 3, query=kw
            )
            assert len(out["stories"]) == 1, f"query={kw!r} should match"

    def test_query_no_match_returns_empty(self, populated_store: VaultStore) -> None:
        out = _handle_find_story(
            populated_store.load(), None, None, None, 3, query="cryptocurrency"
        )
        assert out["stories"] == []

    def test_structured_params_take_precedence_over_query(
        self, populated_store: VaultStore
    ) -> None:
        # query would match the story, but outcome='failure' must still filter it out.
        out = _handle_find_story(
            populated_store.load(), None, None, "failure", 3, query="migration"
        )
        assert out["stories"] == []

    def test_theme_match_returns_story(self, populated_store: VaultStore) -> None:
        out = _handle_find_story(populated_store.load(), None, "migration", None, 3)
        assert len(out["stories"]) == 1
        story = out["stories"][0]
        assert story["title"] == "Redshift to BigQuery Migration"
        assert set(story) == {
            "id",
            "title",
            "situation",
            "task",
            "action",
            "result",
            "lesson",
            "outcome",
            "related_skills",
            "related_skill_ids",
            "related_experience_id",
            "match_score",
            "evidence",
            "disputed",
            "dispute",
        }
        assert story["outcome"] == "win"
        assert story["match_score"] > 0
        # Related skills include Python and SQL
        assert set(story["related_skills"]) == {"Python", "SQL"}
        # id round-trips as a UUID string
        UUID(story["id"])

    def test_story_artifact_links_included_when_present(
        self, populated_store: VaultStore
    ) -> None:
        # Contract revision 1.6: evidence links ride the story; each entry
        # carries only its set fields (no ``label: null``).
        vault = populated_store.load()
        story = next(
            s for s in vault.stories if s.title == "Redshift to BigQuery Migration"
        )
        story.artifact_links = [
            ArtifactLink(url="https://youtube.com/watch?v=x", label="conf talk"),
            ArtifactLink(url="https://acme.example.com/blog/migration"),
        ]
        out = _handle_find_story(vault, None, "migration", None, 3)
        assert out["stories"][0]["artifact_links"] == [
            {"url": "https://youtube.com/watch?v=x", "label": "conf talk"},
            {"url": "https://acme.example.com/blog/migration"},
        ]

    def test_story_artifact_links_key_absent_when_empty(
        self, populated_store: VaultStore
    ) -> None:
        # No links on the story: the payload shape is exactly the pre-1.6
        # one — the key is absent, never an empty list (see
        # test_theme_match_returns_story's exact key-set assertion).
        out = _handle_find_story(populated_store.load(), None, "migration", None, 3)
        assert "artifact_links" not in out["stories"][0]

    def test_incomplete_stars_excluded(self, populated_store: VaultStore) -> None:
        out = _handle_find_story(populated_store.load(), "situation", None, None, 3)
        titles = [s["title"] for s in out["stories"]]
        assert "Incomplete Story" not in titles

    def test_no_match_returns_empty(self, populated_store: VaultStore) -> None:
        out = _handle_find_story(
            populated_store.load(), None, "cryptocurrency", None, 3
        )
        assert out["stories"] == []

    def test_theme_matches_non_title_fields(self, populated_store: VaultStore) -> None:
        # 'ballooning' lives only in the situation field, 'dual-writes' only in
        # action, 'warehouse' only in result. Each must match (tp-4tr).
        for kw in ("ballooning", "dual-writes", "warehouse"):
            out = _handle_find_story(populated_store.load(), None, kw, None, 3)
            assert len(out["stories"]) == 1, f"theme={kw!r} should match"

    def test_outcome_filter_is_applied(self, populated_store: VaultStore) -> None:
        # The seeded story's result reads like a win; 'failure' must exclude it.
        out = _handle_find_story(populated_store.load(), None, None, "failure", 3)
        assert out["stories"] == []
        out = _handle_find_story(populated_store.load(), None, None, "win", 3)
        assert len(out["stories"]) == 1
        assert out["stories"][0]["outcome"] == "win"


class TestFindStoryThemeTags:
    @pytest.fixture()
    def tagged_store(self, vault_dir: Path) -> VaultStore:
        store = VaultStore(vault_dir)
        vault = VaultSchema(profile=ProfileSchema(display_name="t"))
        tagged = StorySchema(
            title="Pager Storm",
            situation="A cascading outage hit the payment service overnight.",
            task="Restore service and prevent recurrence.",
            action="Coordinated the bridge, rolled back, wrote the postmortem.",
            result="Service restored in 40 minutes; on-call load halved.",
            theme_tags=["incident-response", "process-change"],
        )
        untagged = StorySchema(
            title="Quarterly Planning",
            situation="The team lacked a roadmap and incident priorities slipped.",
            task="Build the quarterly plan.",
            action="Ran planning workshops with stakeholders.",
            result="Shipped the plan; incident backlog triaged.",
        )
        vault.stories = [tagged, untagged]
        store.save(vault)
        return store

    def test_exact_tag_match_returns_story(self, tagged_store: VaultStore) -> None:
        out = _handle_find_story(
            tagged_store.load(), None, "incident-response", None, 3
        )
        titles = [s["title"] for s in out["stories"]]
        assert "Pager Storm" in titles
        top = next(s for s in out["stories"] if s["title"] == "Pager Storm")
        assert top["match_score"] == 1.0

    def test_exact_tag_outranks_body_text_match(
        self, tagged_store: VaultStore
    ) -> None:
        # "process-change" is an exact tag on Pager Storm only.
        out = _handle_find_story(tagged_store.load(), None, "process-change", None, 3)
        assert out["stories"][0]["title"] == "Pager Storm"

    def test_keyword_in_tags_beats_body_text(self, tagged_store: VaultStore) -> None:
        # "incident" hits Pager Storm's tag (substring) and Quarterly
        # Planning's body text; the tag hit must rank first.
        out = _handle_find_story(tagged_store.load(), None, "incident", None, 3)
        titles = [s["title"] for s in out["stories"]]
        assert titles[0] == "Pager Storm"
        scores = {s["title"]: s["match_score"] for s in out["stories"]}
        if "Quarterly Planning" in scores:
            assert scores["Pager Storm"] > scores["Quarterly Planning"]

    def test_body_text_fallback_still_matches(self, tagged_store: VaultStore) -> None:
        out = _handle_find_story(tagged_store.load(), None, "roadmap", None, 3)
        assert [s["title"] for s in out["stories"]] == ["Quarterly Planning"]


class TestGetPhilosophy:
    def test_topic_match(self, populated_store: VaultStore) -> None:
        out = _handle_get_philosophy(populated_store.load(), "delegation", 3)
        assert len(out["philosophies"]) == 1
        phil = out["philosophies"][0]
        assert set(phil) == {
            "id",
            "topic",
            "stance",
            "supporting_examples",
            "related_story_ids",
            "match_score",
            "evidence",
            "disputed",
            "dispute",
        }
        assert phil["topic"] == "Delegation as Leverage"
        assert phil["match_score"] > 0
        assert len(phil["supporting_examples"]) == 1
        assert len(phil["related_story_ids"]) == 1

    def test_empty_topic_returns_recent(self, populated_store: VaultStore) -> None:
        out = _handle_get_philosophy(populated_store.load(), "", 3)
        assert len(out["philosophies"]) == 1

    def test_empty_vault(self, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        store.save(VaultSchema())
        out = _handle_get_philosophy(store.load(), "anything", 3)
        assert out == {"philosophies": []}


class TestGetPhilosophyCategoryFilter:
    @pytest.fixture()
    def mixed_store(self, vault_dir: Path) -> VaultStore:
        store = VaultStore(vault_dir)
        vault = VaultSchema(profile=ProfileSchema(display_name="t"))
        vault.philosophies = [
            PhilosophySchema(
                title="Blameless Postmortems",
                description="Treat incidents as systems failures, not people.",
                category=PhilosophyCategory.CULTURE,
            ),
            PhilosophySchema(
                title="Write Things Down",
                description="Decisions live in documents, not meetings.",
            ),
        ]
        store.save(vault)
        return store

    def test_category_filter_excludes_non_matching(
        self, mixed_store: VaultStore
    ) -> None:
        # The trial bug: category='leadership' returned both entries at 0.0.
        out = _handle_get_philosophy(
            mixed_store.load(), "", 3, category="leadership"
        )
        assert out == {"philosophies": []}

    def test_category_match_scores_meaningfully(
        self, mixed_store: VaultStore
    ) -> None:
        out = _handle_get_philosophy(mixed_store.load(), "", 3, category="culture")
        assert [p["topic"] for p in out["philosophies"]] == ["Blameless Postmortems"]
        assert out["philosophies"][0]["match_score"] == 1.0

    def test_topic_query_ranks(self, mixed_store: VaultStore) -> None:
        out = _handle_get_philosophy(mixed_store.load(), "postmortems", 3)
        assert out["philosophies"][0]["topic"] == "Blameless Postmortems"
        assert out["philosophies"][0]["match_score"] > 0
        # Non-matching philosophy is excluded, not returned at 0.0.
        assert [p["topic"] for p in out["philosophies"]] == ["Blameless Postmortems"]

    def test_category_and_topic_combined(self, mixed_store: VaultStore) -> None:
        out = _handle_get_philosophy(
            mixed_store.load(), "incidents", 3, category="culture"
        )
        assert [p["topic"] for p in out["philosophies"]] == ["Blameless Postmortems"]
        assert out["philosophies"][0]["match_score"] >= 0.5


# ── Disputes (canonical flags[] schema, docs/schema/dispute-v1/) ─────


class TestDisputes:
    """Canonical ``dispute`` objects: dangling_reference + date_overlap flags."""

    def _disputed_vault(self) -> tuple[VaultSchema, UUID]:
        """A vault whose lone experience has a dangling ``skill_ids[2]``."""
        skill_a = SkillSchema(name="Python", category="technical", proficiency=5)
        skill_b = SkillSchema(name="SQL", category="technical", proficiency=4)
        exp = ExperienceSchema(
            title="Staff Engineer",
            company="Acme",
            start_date="2020-01",
            end_date="2021-12",
            # index 0 and 1 resolve; index 2 dangles.
            skill_ids=[skill_a.id, skill_b.id, uuid4()],
        )
        vault = VaultSchema(
            profile=ProfileSchema(
                display_name="Wesley Johnson",
                headline="Engineer",
                summary="Bio.",
            ),
            skills=[skill_a, skill_b],
            experiences=[exp],
        )
        return vault, exp.id

    def _two_roles(
        self,
        a_range: tuple[str, str],
        b_range: tuple[str, str],
        *,
        a_title: str = "Role A",
        b_title: str = "Role B",
    ) -> tuple[VaultSchema, ExperienceSchema, ExperienceSchema]:
        a = ExperienceSchema(
            title=a_title, company="A", start_date=a_range[0], end_date=a_range[1]
        )
        b = ExperienceSchema(
            title=b_title, company="B", start_date=b_range[0], end_date=b_range[1]
        )
        return VaultSchema(experiences=[a, b]), a, b

    def test_clean_vault_has_no_disputes(self, populated_store: VaultStore) -> None:
        # The seeded vault resolves every cross-reference (and has one role).
        assert _compute_disputes(populated_store.load()) == {}

    def test_dangling_reference_flag_shape(self) -> None:
        vault, exp_id = self._disputed_vault()
        disputes = _compute_disputes(vault)
        assert set(disputes) == {exp_id}
        dispute = disputes[exp_id]
        assert dispute["sources"] == [DISPUTE_SOURCE] == ["local-referential-integrity"]
        assert dispute["reason"] == "dangling reference: skill_ids[2] does not resolve"
        assert len(dispute["flags"]) == 1
        flag = dispute["flags"][0]
        assert flag["type"] == "dangling_reference"
        assert flag["reason"] == "dangling reference: skill_ids[2] does not resolve"
        assert flag["detail"] == {
            "field": "skill_ids",
            "index": 2,
            "id": str(vault.experiences[0].skill_ids[2]),
            "target": "skill",
        }
        # ``since`` is ISO-8601 UTC with a Z suffix (cloud format).
        datetime.fromisoformat(dispute["since"].replace("Z", "+00:00"))

    def test_dangling_scalar_experience_id_on_story(self) -> None:
        skill = SkillSchema(name="Python", category="technical", proficiency=5)
        story = StorySchema(
            title="Migration",
            situation="s",
            task="t",
            action="a",
            result="r",
            skill_ids=[skill.id],
            experience_id=uuid4(),  # dangling scalar reference
        )
        vault = VaultSchema(skills=[skill], stories=[story])
        flag = _compute_disputes(vault)[story.id]["flags"][0]
        # A scalar ref renders with the bare field name, no ``[index]``.
        assert flag["reason"] == "dangling reference: experience_id does not resolve"
        assert flag["detail"]["field"] == "experience_id"
        assert flag["detail"]["index"] is None
        assert flag["detail"]["target"] == "experience"

    def test_multiple_dangling_refs_are_separate_flags(self) -> None:
        story = StorySchema(
            title="Two gaps",
            situation="s",
            task="t",
            action="a",
            result="r",
            skill_ids=[uuid4(), uuid4()],  # both dangle
        )
        vault = VaultSchema(stories=[story])
        dispute = _compute_disputes(vault)[story.id]
        assert [f["detail"]["index"] for f in dispute["flags"]] == [0, 1]
        assert all(f["type"] == "dangling_reference" for f in dispute["flags"])
        # The derived reason joins the per-flag reasons with "; ".
        assert dispute["reason"] == (
            "dangling reference: skill_ids[0] does not resolve; "
            "dangling reference: skill_ids[1] does not resolve"
        )

    def test_dangling_philosophy_evidence_rides_on_record(self) -> None:
        phil = PhilosophySchema(
            title="Stance",
            description="A position with no surviving evidence.",
            evidence_story_ids=[uuid4()],  # dangling
        )
        vault = VaultSchema(philosophies=[phil])
        record = _handle_get_philosophy(vault, "stance", 3)["philosophies"][0]
        assert record["disputed"] is True
        flag = record["dispute"]["flags"][0]
        assert flag["detail"]["target"] == "story"
        assert flag["reason"] == (
            "dangling reference: evidence_story_ids[0] does not resolve"
        )

    def test_date_overlap_flags_both_roles_symmetrically(self) -> None:
        # Clear overlap: 2021-01..2022-06 and 2021-09..2023-01.
        vault, a, b = self._two_roles(("2021-01", "2022-06"), ("2021-09", "2023-01"))
        disputes = _compute_disputes(vault)
        assert set(disputes) == {a.id, b.id}

        fa = disputes[a.id]["flags"][0]
        assert fa["type"] == "contradiction"
        assert fa["detail"]["kind"] == "date_overlap"
        assert fa["detail"]["entities"] == [str(a.id), str(b.id)]
        assert fa["detail"]["ranges"] == [
            ["2021-01", "2022-06"],
            ["2021-09", "2023-01"],
        ]
        assert "overlap in time" in fa["reason"]

        # B's flag lists B first — symmetric perspective, order-independent.
        fb = disputes[b.id]["flags"][0]
        assert fb["detail"]["entities"] == [str(b.id), str(a.id)]
        assert fb["detail"]["ranges"] == [
            ["2021-09", "2023-01"],
            ["2021-01", "2022-06"],
        ]

    def test_shared_boundary_month_is_adjacency_not_overlap(self) -> None:
        # Back-to-back roles sharing transition month 2023-04 — NOT flagged.
        vault, _a, _b = self._two_roles(("2021-10", "2023-04"), ("2023-04", "2025-10"))
        assert _compute_disputes(vault) == {}

    def test_part_time_role_suppresses_overlap(self) -> None:
        # Genuine month overlap, but one role reads part-time — not flagged.
        vault, _a, _b = self._two_roles(
            ("2021-01", "2022-06"),
            ("2021-09", "2023-01"),
            b_title="Freelance Advisor",
        )
        assert _compute_disputes(vault) == {}

    def test_part_time_match_ignores_accomplishments(self) -> None:
        # "contract" appears only in an accomplishment, not the title/description.
        # The role is full-time, so the overlap must still be flagged.
        a = ExperienceSchema(
            title="Staff Engineer",
            company="A",
            start_date="2021-01",
            end_date="2022-06",
            accomplishments=["Built contract-renewal automation"],
        )
        b = ExperienceSchema(
            title="Principal Engineer",
            company="B",
            start_date="2021-09",
            end_date="2023-01",
        )
        vault = VaultSchema(experiences=[a, b])
        assert set(_compute_disputes(vault)) == {a.id, b.id}

    def test_ongoing_present_roles_overlap(self) -> None:
        # Two open-ended ("present") full-time roles overlap — the open end must
        # extend past the current month under the strict-< comparison.
        a = ExperienceSchema(title="Role A", company="A", start_date="2021-01")
        b = ExperienceSchema(title="Role B", company="B", start_date="2022-01")
        vault = VaultSchema(experiences=[a, b])
        disputes = _compute_disputes(vault)
        assert set(disputes) == {a.id, b.id}
        flag = disputes[a.id]["flags"][0]
        assert flag["detail"]["ranges"][0] == ["2021-01", "present"]

    def test_month_name_dates_parse(self) -> None:
        # "Dec 2021" must parse as December, not default to January, so a real
        # overlap with a numeric-dated role is still flagged (and normalized).
        a = ExperienceSchema(
            title="Data Engineer", company="A",
            start_date="2020-01", end_date="Dec 2021",
        )
        b = ExperienceSchema(
            title="Principal Engineer", company="B",
            start_date="2021-06", end_date="2023-01",
        )
        vault = VaultSchema(experiences=[a, b])
        disputes = _compute_disputes(vault)
        assert set(disputes) == {a.id, b.id}
        # "Dec 2021" normalizes to 2021-12 in the range label.
        ranges = disputes[a.id]["flags"][0]["detail"]["ranges"]
        assert ranges[0] == ["2020-01", "2021-12"]

    def test_flag_order_key_sorts_canonically(self) -> None:
        # The comparator is shared verbatim with the hosted server (flagOrderKey)
        # so a record's flags[] (and the derived reason) are byte-identical
        # across servers regardless of discovery order: dangling_reference first
        # (by field rank skill_ids<experience_id<evidence_story_ids, then array
        # index), then date_overlap (by partner id), then any other contradiction.
        def dangling(field: str, index: int | None) -> dict[str, Any]:
            return {
                "type": "dangling_reference",
                "reason": field,
                "detail": {"field": field, "index": index},
            }

        def overlap(partner: str) -> dict[str, Any]:
            return {
                "type": "contradiction",
                "reason": "overlap",
                "detail": {"kind": "date_overlap", "entities": ["self", partner]},
            }

        other = {
            "type": "contradiction",
            "reason": "z-other",
            "detail": {"kind": "other"},
        }

        scrambled = [
            overlap("zzz"),
            other,
            dangling("experience_id", None),
            overlap("aaa"),
            dangling("skill_ids", 1),
            dangling("skill_ids", 0),
            dangling("evidence_story_ids", 0),
        ]
        assert sorted(scrambled, key=_flag_order_key) == [
            dangling("skill_ids", 0),
            dangling("skill_ids", 1),
            dangling("experience_id", None),
            dangling("evidence_story_ids", 0),
            overlap("aaa"),
            overlap("zzz"),
            other,
        ]

    def test_compute_disputes_orders_dangling_before_overlap(self) -> None:
        # One role carries BOTH a dangling skill ref and a date overlap; the
        # canonical sort must place dangling_reference before the contradiction
        # in the emitted flags[] (and thus the derived reason).
        a = ExperienceSchema(
            title="Role A",
            company="A",
            start_date="2021-01",
            end_date="2022-06",
            skill_ids=[uuid4()],  # dangles
        )
        b = ExperienceSchema(
            title="Role B", company="B", start_date="2021-09", end_date="2023-01"
        )
        vault = VaultSchema(experiences=[a, b])
        flags = _compute_disputes(vault)[a.id]["flags"]
        assert [f["type"] for f in flags] == ["dangling_reference", "contradiction"]
        assert flags[1]["detail"]["kind"] == "date_overlap"
        # The derived reason concatenates in the same canonical order.
        assert flags[0]["reason"] in _compute_disputes(vault)[a.id]["reason"]

    def test_profile_summary_rollup(self) -> None:
        vault, exp_id = self._disputed_vault()
        out = _handle_get_profile_summary(vault, "detailed")
        rollup = out["disputes"]
        assert rollup["count"] == 1
        assert rollup["sources"] == [DISPUTE_SOURCE]
        assert rollup["entities"] == [
            {
                "entity_id": str(exp_id),
                "kind": "experience",
                "label": "Staff Engineer",
                "flag_types": ["dangling_reference"],
                "reason": "dangling reference: skill_ids[2] does not resolve",
            }
        ]
        # The same dispute also rides on the experience record itself, while a
        # clean skill record carries ``dispute: None``.
        exp_record = out["signature_experiences"][0]
        assert exp_record["disputed"] is True
        assert exp_record["dispute"]["reason"] == rollup["entities"][0]["reason"]
        assert out["top_skills"][0]["dispute"] is None


class TestDisputeGoldenFixture:
    """The shared cross-server fixture (docs/schema/dispute-v1/golden-fixture.json).

    The local server must reproduce ``expected_disputes`` (flags + derived
    reason) for this input. The cloud server carries the same fixture and
    asserts the same output, so the two cannot drift on detection or shape.
    """

    def test_local_matches_golden(self) -> None:
        path = (
            Path(__file__).resolve().parent.parent
            / "docs"
            / "schema"
            / "dispute-v1"
            / "golden-fixture.json"
        )
        data = json.loads(path.read_text())
        inp = data["input"]
        vault = VaultSchema(
            skills=[SkillSchema(**s) for s in inp["skills"]],
            experiences=[ExperienceSchema(**e) for e in inp["experiences"]],
            stories=[StorySchema(**s) for s in inp["stories"]],
            philosophies=[PhilosophySchema(**p) for p in inp["philosophies"]],
        )
        disputes = _compute_disputes(vault)
        normalized = {
            str(entity_id): {"reason": d["reason"], "flags": d["flags"]}
            for entity_id, d in disputes.items()
        }
        assert normalized == data["expected_disputes"]


# ── In-process server: tool registration ────────────────────────────


class TestServerRegistration:
    def test_name_and_tools_registered(self, populated_store: VaultStore) -> None:
        server = create_server(populated_store)
        assert server.name == SERVER_NAME
        tools = asyncio.run(server.list_tools())
        names = {t.name for t in tools}
        assert names == {
            "get_profile_summary",
            "search_skills",
            "find_story",
            "find_bullets",
            "get_philosophy",
            "vault_lens_list",
            "vault_lens_get",
            "doctor",
            "vault_sync",
        }

    def test_doctor_tool_reports_phase_and_findings(
        self, populated_store: VaultStore
    ) -> None:
        server = create_server(populated_store)
        result = asyncio.run(server.call_tool("doctor", {}))
        payload = json.loads(result.content[0].text)  # type: ignore[union-attr]
        body = payload["result"]
        assert body["phase"]["phase"] in (
            "first-run",
            "growing",
            "established",
            "stale",
        )
        assert body["stale_days"] == 90
        assert isinstance(body["findings"], list)

    def test_server_version_in_init_options(self, populated_store: VaultStore) -> None:
        """serverInfo.version must report *our* version, not the MCP SDK."""
        server = create_server(populated_store)
        assert server.version == SERVER_VERSION


# ── Prompts ─────────────────────────────────────────────────────────


class TestPrompts:
    def test_prompts_registered(self, populated_store: VaultStore) -> None:
        server = create_server(populated_store)
        prompts = asyncio.run(server.list_prompts())
        names = {p.name for p in prompts}
        assert names == {
            "fill_vault",
            "mine_story_gaps",
            "discover_skills",
            "draft_star_story",
            "audit_coherence",
            "position_lens",
            "deepen_story",
            "improve_profile",
        }

    def test_fill_vault_focus_argument(self, populated_store: VaultStore) -> None:
        server = create_server(populated_store)
        got = asyncio.run(server.get_prompt("fill_vault", {"focus": "stories"}))
        text = got.messages[0].content.text  # type: ignore[union-attr]
        assert "stories" in text
        # Canonical coach contract + the CLI write path.
        assert "Socratic" in text
        assert "traitprint vault add-story" in text

    def test_fill_vault_without_focus_covers_all_sections(
        self, populated_store: VaultStore
    ) -> None:
        server = create_server(populated_store)
        got = asyncio.run(server.get_prompt("fill_vault", {}))
        text = got.messages[0].content.text  # type: ignore[union-attr]
        assert "every section" in text

    def test_mode_prompts_reference_their_missions(
        self, populated_store: VaultStore
    ) -> None:
        server = create_server(populated_store)
        gaps = asyncio.run(server.get_prompt("mine_story_gaps", {}))
        assert "STORY OPPORTUNITY MODE" in gaps.messages[0].content.text  # type: ignore[union-attr]
        disc = asyncio.run(server.get_prompt("discover_skills", {}))
        assert "SKILL DISCOVERY MODE" in disc.messages[0].content.text  # type: ignore[union-attr]

    def test_audit_coherence_references_audit_command(
        self, populated_store: VaultStore
    ) -> None:
        server = create_server(populated_store)
        got = asyncio.run(server.get_prompt("audit_coherence", {}))
        text = got.messages[0].content.text  # type: ignore[union-attr]
        assert "traitprint vault audit" in text
        # Cloud framing: tensions are nuance, never say "you failed".
        assert "nuance" in text.lower()

    def test_draft_star_story_seeds_experience(
        self, populated_store: VaultStore
    ) -> None:
        server = create_server(populated_store)
        got = asyncio.run(
            server.get_prompt("draft_star_story", {"experience": "a tricky migration"})
        )
        text = got.messages[0].content.text  # type: ignore[union-attr]
        assert "a tricky migration" in text

    def test_deepen_story_seeds_target_story(
        self, populated_store: VaultStore
    ) -> None:
        server = create_server(populated_store)
        got = asyncio.run(
            server.get_prompt("deepen_story", {"story": "the billing migration"})
        )
        text = got.messages[0].content.text  # type: ignore[union-attr]
        assert "the billing migration" in text
        # Canonical protocol markers + the staged write path.
        assert "baseline or denominator" in text
        assert "update_story" in text

    def test_deepen_story_without_argument_selects_weakest(
        self, populated_store: VaultStore
    ) -> None:
        server = create_server(populated_store)
        got = asyncio.run(server.get_prompt("deepen_story", {}))
        text = got.messages[0].content.text  # type: ignore[union-attr]
        assert "story_scores" in text

    def test_improve_profile_focus_argument(
        self, populated_store: VaultStore
    ) -> None:
        server = create_server(populated_store)
        got = asyncio.run(server.get_prompt("improve_profile", {"focus": "stories"}))
        text = got.messages[0].content.text  # type: ignore[union-attr]
        assert "FOCUS OVERRIDE" in text
        assert "stories" in text
        # The leverage ranking travels with the prompt.
        assert "traitprint-mine-story-gaps" in text


class TestPromptCustomization:
    """An optional user-owned custom.md at the vault root rides along on
    every served prompt — appended after the skill body, clearly
    delimited, and impossible to break prompt serving with."""

    HEADER = "## User customization (custom.md)"

    def test_custom_md_appended_after_body_before_serving_note(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "custom.md").write_text(
            "Always answer in Norwegian.", encoding="utf-8"
        )
        text = _fill_vault_prompt(vault_dir=tmp_path)
        assert "Always answer in Norwegian." in text
        # Skill body, then the delimited custom block, then the MCP note.
        assert (
            text.index("Socratic")
            < text.index(self.HEADER)
            < text.index("Serving context")
        )
        # The delimiter states the precedence contract.
        assert (
            "cannot override safety invariants or bypass the proposals channel"
            in text
        )

    def test_all_builders_include_custom_md(self, tmp_path: Path) -> None:
        (tmp_path / "custom.md").write_text("HOUSE-RULE-MARKER", encoding="utf-8")
        for built in (
            _fill_vault_prompt(vault_dir=tmp_path),
            _mine_story_gaps_prompt(vault_dir=tmp_path),
            _discover_skills_prompt(vault_dir=tmp_path),
            _draft_star_story_prompt(vault_dir=tmp_path),
            _audit_coherence_prompt(vault_dir=tmp_path),
            _position_lens_prompt(vault_dir=tmp_path),
            _deepen_story_prompt(vault_dir=tmp_path),
            _improve_profile_prompt(vault_dir=tmp_path),
        ):
            assert "HOUSE-RULE-MARKER" in built
            assert self.HEADER in built

    def test_no_vault_dir_is_noop(self) -> None:
        assert self.HEADER not in _fill_vault_prompt()

    def test_missing_file_is_noop(self, tmp_path: Path) -> None:
        text = _fill_vault_prompt(vault_dir=tmp_path)
        assert self.HEADER not in text
        assert text == _fill_vault_prompt()

    def test_empty_file_is_noop(self, tmp_path: Path) -> None:
        (tmp_path / "custom.md").write_text("", encoding="utf-8")
        assert self.HEADER not in _fill_vault_prompt(vault_dir=tmp_path)

    def test_whitespace_only_file_is_noop(self, tmp_path: Path) -> None:
        (tmp_path / "custom.md").write_text("  \n\n\t\n", encoding="utf-8")
        assert self.HEADER not in _fill_vault_prompt(vault_dir=tmp_path)

    def test_unreadable_file_is_noop_not_a_crash(self, tmp_path: Path) -> None:
        # A directory named custom.md raises OSError on read regardless of
        # the uid the tests run under (chmod tricks don't stop root).
        (tmp_path / "custom.md").mkdir()
        text = _fill_vault_prompt(vault_dir=tmp_path)
        assert self.HEADER not in text
        assert text == _fill_vault_prompt()

    def test_oversized_file_is_capped(self, tmp_path: Path) -> None:
        big = ("x" * (40 * 1024)) + "TAIL-MARKER"
        (tmp_path / "custom.md").write_text(big, encoding="utf-8")
        text = _fill_vault_prompt(vault_dir=tmp_path)
        assert self.HEADER in text
        assert "TAIL-MARKER" not in text  # truncated at the 32 KiB cap

    def test_served_prompts_read_custom_md_from_the_store_dir(
        self, populated_store: VaultStore
    ) -> None:
        (populated_store.directory / "custom.md").write_text(
            "Prefer terse bullet answers.", encoding="utf-8"
        )
        server = create_server(populated_store)
        for name, args in (
            ("fill_vault", {"focus": "stories"}),
            ("audit_coherence", {}),
        ):
            got = asyncio.run(server.get_prompt(name, args))
            text = got.messages[0].content.text  # type: ignore[union-attr]
            assert "Prefer terse bullet answers." in text
            assert self.HEADER in text

    def test_served_prompt_reads_custom_md_at_serve_time(
        self, populated_store: VaultStore
    ) -> None:
        """custom.md is read per request — edits apply without a restart."""
        server = create_server(populated_store)
        got = asyncio.run(server.get_prompt("fill_vault", {}))
        assert self.HEADER not in got.messages[0].content.text  # type: ignore[union-attr]
        (populated_store.directory / "custom.md").write_text(
            "LATE-EDIT-MARKER", encoding="utf-8"
        )
        got = asyncio.run(server.get_prompt("fill_vault", {}))
        assert "LATE-EDIT-MARKER" in got.messages[0].content.text  # type: ignore[union-attr]


# ── JSON-RPC round-trip over stdio ──────────────────────────────────


async def _stdio_roundtrip(
    vault_dir: Path,
) -> tuple[list[str], dict[str, str], str]:
    """Spawn ``traitprint mcp-serve`` and invoke each tool once.

    Returns (tool names, {tool_name: envelope_result_json},
    serverInfo.version from the handshake).
    """
    venv_bin = Path(__file__).resolve().parent.parent / ".venv" / "bin"
    exe = venv_bin / "traitprint" if venv_bin.exists() else Path("traitprint")
    params = StdioServerParameters(
        command=str(exe),
        args=["--path", str(vault_dir), "mcp-serve"],
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        listed = await session.list_tools()
        names = [t.name for t in listed.tools]

        results: dict[str, str] = {}
        r = await session.call_tool("get_profile_summary", {"depth": "brief"})
        results["get_profile_summary"] = r.content[0].text  # type: ignore[union-attr]

        r = await session.call_tool("search_skills", {"query": "python"})
        results["search_skills"] = r.content[0].text  # type: ignore[union-attr]

        r = await session.call_tool("find_story", {"theme": "migration", "limit": 3})
        results["find_story"] = r.content[0].text  # type: ignore[union-attr]

        r = await session.call_tool("get_philosophy", {"topic": "delegation"})
        results["get_philosophy"] = r.content[0].text  # type: ignore[union-attr]

        r = await session.call_tool("vault_lens_list", {})
        results["vault_lens_list"] = r.content[0].text  # type: ignore[union-attr]

    return names, results, init.server_info.version


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="stdio_client subprocess behavior is unreliable on Windows",
)
class TestStdioRoundTrip:
    def test_tools_callable_via_jsonrpc(self, populated_store: VaultStore) -> None:
        names, results, server_version = asyncio.run(
            _stdio_roundtrip(populated_store.directory)
        )
        # serverInfo.version on the wire is *our* version, not the SDK's.
        assert server_version == SERVER_VERSION
        assert set(names) == {
            "get_profile_summary",
            "search_skills",
            "find_story",
            "find_bullets",
            "get_philosophy",
            "vault_lens_list",
            "vault_lens_get",
            "doctor",
            "vault_sync",
        }

        # The seeded vault carries no lenses, so the inventory is empty.
        lens_list = json.loads(results["vault_lens_list"])
        assert lens_list["result"] == {"lenses": [], "total": 0}

        summary = json.loads(results["get_profile_summary"])
        assert set(summary) == {"result", "meta"}
        assert summary["result"]["headline"] == "Data Engineering Leader"
        assert summary["meta"]["server_version"] == RESPONSE_CONTRACT_VERSION

        skills = json.loads(results["search_skills"])
        assert skills["result"]["query_interpretation"]["used_distance_graph"] is False
        assert any(m["name"] == "Python" for m in skills["result"]["matches"])

        stories = json.loads(results["find_story"])
        assert len(stories["result"]["stories"]) == 1
        assert stories["result"]["stories"][0]["outcome"] == "win"

        phils = json.loads(results["get_philosophy"])
        assert len(phils["result"]["philosophies"]) == 1
        assert phils["result"]["philosophies"][0]["topic"] == "Delegation as Leverage"


# ── Positioning Lenses (docs/schema/lens-v1/) ───────────────────────


class TestLenses:
    """Lens projection: one vault, multiple honest positionings."""

    def _two_lens_vault(self) -> tuple[VaultSchema, dict[str, SkillSchema]]:
        """A vault with four skills, two experiences, and IC + People lenses."""
        arch = SkillSchema(
            name="Data Architecture", category="technical", proficiency=4
        )
        prod = SkillSchema(
            name="Product Management", category="business", proficiency=5
        )
        lead = SkillSchema(
            name="Technical Leadership", category="leadership", proficiency=4
        )
        py = SkillSchema(name="Python", category="technical", proficiency=3)
        ic_exp = ExperienceSchema(
            title="Staff Engineer", company="Acme", start_date="2021-01"
        )
        mgr_exp = ExperienceSchema(
            title="Engineering Manager", company="Acme", start_date="2023-01"
        )
        ic_lens = LensSchema(
            slug="ic-architecture",
            name="IC / Architecture",
            headline_override="Staff/Principal Data & Platform Engineer",
            signature_experience_ids=[ic_exp.id],
            skill_salience={
                arch.id: SalienceLevel.CORE,
                prod.id: SalienceLevel.SUPPRESSED,
            },
            is_default=True,
        )
        people_lens = LensSchema(
            slug="people-leadership",
            name="People / Leadership",
            headline_override="Director of Data & Analytics",
            signature_experience_ids=[mgr_exp.id],
            skill_salience={
                prod.id: SalienceLevel.CORE,
                lead.id: SalienceLevel.CORE,
            },
        )
        vault = VaultSchema(
            profile=ProfileSchema(
                display_name="Wesley", headline="Engineer", summary="Bio."
            ),
            skills=[arch, prod, lead, py],
            experiences=[ic_exp, mgr_exp],
            lenses=[ic_lens, people_lens],
        )
        return vault, {"arch": arch, "prod": prod, "lead": lead, "py": py}

    def test_two_lenses_render_differently_from_one_vault(self) -> None:
        vault, sk = self._two_lens_vault()
        ic = _resolve_lens(vault, "ic-architecture")
        people = _resolve_lens(vault, "people-leadership")

        ic_out = _handle_get_profile_summary(vault, "detailed", ic)
        people_out = _handle_get_profile_summary(vault, "detailed", people)

        # Headline overrides differ.
        assert ic_out["headline"] == "Staff/Principal Data & Platform Engineer"
        assert people_out["headline"] == "Director of Data & Analytics"
        assert ic_out["lens"] == "ic-architecture"

        ic_skills = [s["name"] for s in ic_out["top_skills"]]
        people_skills = [s["name"] for s in people_out["top_skills"]]
        # IC: Data Architecture (core) leads; Product Management (suppressed) gone.
        assert ic_skills[0] == "Data Architecture"
        assert "Product Management" not in ic_skills
        # People: Product Management is core and present; nothing suppressed.
        assert "Product Management" in people_skills
        assert people_skills[0] in {"Product Management", "Technical Leadership"}

        # Signature experiences differ per lens.
        assert ic_out["signature_experiences"][0]["title"] == "Staff Engineer"
        assert people_out["signature_experiences"][0]["title"] == "Engineering Manager"

    def test_no_lens_is_byte_identical_to_pre_lens(self) -> None:
        # Acceptance §11.7: a vault with no lenses renders exactly as before —
        # no "lens" key, every skill present, proficiency-sorted.
        vault, _sk = self._two_lens_vault()
        bare = vault.model_copy(update={"lenses": []})
        out = _handle_get_profile_summary(bare, "detailed", _resolve_lens(bare, None))
        assert "lens" not in out
        names = [s["name"] for s in out["top_skills"]]
        assert "Product Management" in names  # nothing suppressed without a lens
        # Canonical sort: highest proficiency first (Product Management, prof 5).
        assert names[0] == "Product Management"

    def test_default_lens_applies_without_an_explicit_arg(self) -> None:
        # The IC lens is is_default=True, so the bare call renders through it.
        vault, _sk = self._two_lens_vault()
        out = _handle_get_profile_summary(vault, "standard", _resolve_lens(vault, None))
        assert out["lens"] == "ic-architecture"
        assert out["headline"] == "Staff/Principal Data & Platform Engineer"

    def test_unknown_lens_raises(self) -> None:
        vault, _sk = self._two_lens_vault()
        with pytest.raises(ValueError, match="lens not found"):
            _resolve_lens(vault, "nope")

    def test_none_escape_hatch_forces_canonical_over_default(self) -> None:
        # lens="none" is the reserved canonical-rendering escape hatch: even
        # with a default lens present, it returns the un-lensed profile,
        # byte-identical to the no-lens rendering. Shared verbatim with cloud.
        vault, _sk = self._two_lens_vault()
        assert _resolve_lens(vault, "none") is None
        canonical = _handle_get_profile_summary(
            vault, "detailed", _resolve_lens(vault, "none")
        )
        assert "lens" not in canonical
        # The default (IC) lens suppresses Product Management; "none" restores it.
        names = [s["name"] for s in canonical["top_skills"]]
        assert "Product Management" in names
        assert names[0] == "Product Management"  # canonical proficiency sort

    def test_none_slug_cannot_be_claimed_by_a_user_lens(self) -> None:
        # The reserved keyword can never shadow a real lens: the slug validator
        # rejects it at construction.
        with pytest.raises(ValidationError, match="reserved"):
            LensSchema(slug="none", name="Nope")

    def test_lens_list_and_get_shapes(self) -> None:
        vault, sk = self._two_lens_vault()
        listing = _handle_vault_lens_list(vault)
        assert listing["total"] == 2
        ic_row = next(x for x in listing["lenses"] if x["slug"] == "ic-architecture")
        assert ic_row["is_default"] is True
        assert ic_row["core_skills"] == 1
        assert ic_row["suppressed_skills"] == 1

        detail = _handle_vault_lens_get(vault, "ic-architecture")
        assert detail["name"] == "IC / Architecture"
        assert detail["signature_experiences"][0]["title"] == "Staff Engineer"
        sal = {row["name"]: row["salience"] for row in detail["skill_salience"]}
        assert sal["Data Architecture"] == "core"
        assert sal["Product Management"] == "suppressed"

    def test_lenses_round_trip_through_the_vault_store(self, vault_dir: Path) -> None:
        # Persistence: a vault with lenses saves to lenses.json and reloads with
        # skill_salience (UUID keys + enum values) intact.
        vault, sk = self._two_lens_vault()
        store = VaultStore(vault_dir)
        store.save(vault)
        assert (vault_dir / "lenses.json").is_file()
        reloaded = store.load()
        assert {lens.slug for lens in reloaded.lenses} == {
            "ic-architecture",
            "people-leadership",
        }
        ic = _resolve_lens(reloaded, "ic-architecture")
        assert ic is not None
        assert ic.salience_for(sk["arch"].id) == SalienceLevel.CORE
        assert ic.salience_for(sk["prod"].id) == SalienceLevel.SUPPRESSED
        # An unspecified skill defaults to SUPPORTING.
        assert ic.salience_for(sk["py"].id) == SalienceLevel.SUPPORTING

    def test_invalid_slug_rejected(self) -> None:
        with pytest.raises(ValueError, match="kebab-case"):
            LensSchema(slug="Not A Slug", name="x")

    def test_empty_lens_vault_omits_lenses_json(self, vault_dir: Path) -> None:
        # A vault that never opted into lenses keeps its exact file tree — no
        # new tracked lenses.json (Codex P2). And a missing file reads as [].
        store = VaultStore(vault_dir)
        store.save(VaultSchema(profile=ProfileSchema(display_name="Wesley")))
        assert not (vault_dir / "lenses.json").exists()
        assert store.load().lenses == []

    def test_removing_last_lens_deletes_stale_lenses_json(
        self, vault_dir: Path
    ) -> None:
        vault, _sk = self._two_lens_vault()
        store = VaultStore(vault_dir)
        store.save(vault)
        assert (vault_dir / "lenses.json").is_file()
        # Drop all lenses → the stale file must be removed, not left behind.
        store.save(store.load().model_copy(update={"lenses": []}))
        assert not (vault_dir / "lenses.json").exists()

    def test_duplicate_slug_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate lens slug"):
            VaultSchema(
                lenses=[
                    LensSchema(slug="dup", name="A"),
                    LensSchema(slug="dup", name="B"),
                ]
            )

    def test_multiple_defaults_rejected(self) -> None:
        with pytest.raises(ValueError, match="at most one lens may be is_default"):
            VaultSchema(
                lenses=[
                    LensSchema(slug="a", name="A", is_default=True),
                    LensSchema(slug="b", name="B", is_default=True),
                ]
            )

    def test_clean_lens_is_not_disputed(self) -> None:
        vault, _sk = self._two_lens_vault()
        listing = _handle_vault_lens_list(vault)
        assert all(row["disputed"] is False for row in listing["lenses"])
        assert all(row["dispute"] is None for row in listing["lenses"])

    def test_full_lens_vault_at_cap_loads_and_lists(self) -> None:
        # Read-tool coverage over a vault filled to the 20-lens cap: MAX_LENSES
        # lenses validate at load and all appear in the listing.
        vault, _sk = self._two_lens_vault()
        extra = [
            LensSchema(slug=f"extra-{i}", name=f"Extra {i}")
            for i in range(MAX_LENSES - len(vault.lenses))
        ]
        full = vault.model_copy(update={"lenses": [*vault.lenses, *extra]})
        assert len(full.lenses) == MAX_LENSES
        listing = _handle_vault_lens_list(full)
        assert listing["total"] == MAX_LENSES

    def test_over_cap_lens_vault_rejected(self) -> None:
        vault, _sk = self._two_lens_vault()
        overflow = [
            LensSchema(slug=f"extra-{i}", name=f"Extra {i}")
            for i in range(MAX_LENSES)
        ]
        with pytest.raises(ValidationError, match="at most 20 lenses"):
            VaultSchema(lenses=[*vault.lenses, *overflow])

    def test_dangling_signature_story_flags_lens_disputed(self) -> None:
        # §11.6: a lens whose signature_story_id no longer resolves is disputed
        # via the same dangling_reference mechanism as records.
        ghost = uuid4()
        lens = LensSchema(
            slug="ic", name="IC", signature_story_ids=[ghost]
        )
        vault = VaultSchema(lenses=[lens])
        detail = _handle_vault_lens_get(vault, "ic")
        assert detail["disputed"] is True
        flag = detail["dispute"]["flags"][0]
        assert flag["type"] == "dangling_reference"
        assert flag["detail"]["field"] == "signature_story_ids"
        assert flag["detail"]["target"] == "story"
        assert flag["reason"] == (
            "dangling reference: signature_story_ids[0] does not resolve"
        )

    def test_dangling_salience_skill_flags_lens_disputed(self) -> None:
        ghost = uuid4()
        lens = LensSchema(
            slug="ic", name="IC", skill_salience={ghost: SalienceLevel.CORE}
        )
        vault = VaultSchema(lenses=[lens])
        flag = _handle_vault_lens_get(vault, "ic")["dispute"]["flags"][0]
        assert flag["detail"]["field"] == "skill_salience"
        assert flag["detail"]["index"] is None  # keyed by id, not positional
        assert flag["detail"]["target"] == "skill"

    def test_disputed_lens_rides_in_profile_summary_rollup(self) -> None:
        ghost = uuid4()
        lens = LensSchema(slug="ic", name="IC Lens", signature_experience_ids=[ghost])
        vault = VaultSchema(lenses=[lens])
        rollup = _handle_get_profile_summary(vault, "detailed")["disputes"]
        entry = next(e for e in rollup["entities"] if e["entity_id"] == str(lens.id))
        assert entry["kind"] == "lens"
        assert entry["label"] == "IC Lens"
        assert entry["flag_types"] == ["dangling_reference"]


class TestLensRenderGoldenFixture:
    """The shared cross-server lens-render fixture (docs/schema/lens-v1/).

    The local server must reproduce ``expected_render`` (overrides, salience-
    ordered top-skill names, ordered signature-experience titles, the ``lens``
    key) for the fixture lens, and ``expected_canonical`` under the ``"none"``
    escape hatch. The cloud server carries the same fixture and asserts the same
    projection through its shared rendering helpers, so the two cannot drift.
    """

    @staticmethod
    def _load() -> dict[str, Any]:
        path = (
            Path(__file__).resolve().parent.parent
            / "docs"
            / "schema"
            / "lens-v1"
            / "golden-render-fixture.json"
        )
        return json.loads(path.read_text())

    def _vault(self, inp: dict[str, Any]) -> VaultSchema:
        return VaultSchema(
            profile=ProfileSchema(**inp["profile"]),
            skills=[SkillSchema(**s) for s in inp["skills"]],
            experiences=[ExperienceSchema(**e) for e in inp["experiences"]],
            lenses=[LensSchema(**inp["lens"])],
        )

    def test_lensed_render_matches_golden(self) -> None:
        data = self._load()
        vault = self._vault(data["input"])
        exp = data["expected_render"]
        lens = _resolve_lens(vault, data["input"]["lens"]["slug"])
        out = _handle_get_profile_summary(vault, "detailed", lens)
        assert out["lens"] == exp["lens"]
        assert out["headline"] == exp["headline"]
        assert out["bio"] == exp["bio"]
        assert [s["name"] for s in out["top_skills"]] == exp["top_skill_names"]
        assert [
            e["title"] for e in out["signature_experiences"]
        ] == exp["signature_experience_titles"]

    def test_none_escape_hatch_matches_canonical(self) -> None:
        data = self._load()
        vault = self._vault(data["input"])
        exp = data["expected_canonical"]
        # "none" forces the canonical rendering even though the lens is default.
        none_lens = _resolve_lens(vault, "none")
        out = _handle_get_profile_summary(vault, "detailed", none_lens)
        assert "lens" not in out
        assert out["headline"] == exp["headline"]
        assert out["bio"] == exp["bio"]
        assert [s["name"] for s in out["top_skills"]] == exp["top_skill_names"]
        assert [
            e["title"] for e in out["signature_experiences"]
        ] == exp["signature_experience_titles"]


# ── Resume-bullet inventory (contract revision 1.7) ─────────────────


class TestBullets:
    """The bullet inventory: evidence chain, lens emphasis, find_bullets."""

    def _vault(self) -> dict[str, Any]:
        """One vault, two roles, four bullets across the emphasis space."""
        arch = SkillSchema(
            name="Data Architecture", category="technical", proficiency=4
        )
        prod = SkillSchema(
            name="Product Management", category="business", proficiency=5
        )
        story = StorySchema(
            title="Big Migration", situation="s", task="t", action="a", result="r"
        )
        b_core = BulletSchema(
            text="Redesigned the platform architecture across 3 teams",
            story_ids=[story.id],
            skill_ids=[arch.id],
            theme_tags=["architecture"],
        )
        b_suppressed = BulletSchema(
            text="Drove the product roadmap for the data suite",
            skill_ids=[prod.id],
        )
        b_plain = BulletSchema(text="Ran the weekly ops review")
        b_dangling = BulletSchema(
            text="Cut spend 45% in the warehouse migration",
            story_ids=[uuid4()],  # dangling evidence link
        )
        exp_a = ExperienceSchema(
            title="Staff Engineer",
            company="Acme",
            start_date="2021-01",
            bullets=[b_core, b_suppressed, b_plain],
        )
        exp_b = ExperienceSchema(
            title="Engineering Manager",
            company="Acme",
            start_date="2023-01",
            bullets=[b_dangling],
        )
        lens = LensSchema(
            slug="ic-architecture",
            name="IC / Architecture",
            skill_salience={
                arch.id: SalienceLevel.CORE,
                prod.id: SalienceLevel.SUPPRESSED,
            },
            is_default=True,
        )
        vault = VaultSchema(
            profile=ProfileSchema(display_name="W"),
            skills=[arch, prod],
            stories=[story],
            experiences=[exp_a, exp_b],
            lenses=[lens],
        )
        return {
            "vault": vault,
            "lens": lens,
            "story": story,
            "bullets": {
                "core": b_core,
                "suppressed": b_suppressed,
                "plain": b_plain,
                "dangling": b_dangling,
            },
        }

    def test_bullet_salience_derives_through_skill_links(self) -> None:
        fx = self._vault()
        lens = fx["lens"]
        b = fx["bullets"]
        assert _bullet_salience(lens, b["core"]) is SalienceLevel.CORE
        assert _bullet_salience(lens, b["suppressed"]) is SalienceLevel.SUPPRESSED
        # Unlinked bullets are supporting under any lens; everything is
        # supporting with no lens at all.
        assert _bullet_salience(lens, b["plain"]) is SalienceLevel.SUPPORTING
        assert _bullet_salience(None, b["core"]) is SalienceLevel.SUPPORTING

    def test_mixed_links_core_wins_over_suppressed(self) -> None:
        fx = self._vault()
        lens = fx["lens"]
        arch_id = fx["vault"].skills[0].id
        prod_id = fx["vault"].skills[1].id
        mixed = BulletSchema(text="x", skill_ids=[arch_id, prod_id])
        # Any core link leads even when another link is suppressed —
        # all-suppressed is the only hiding condition.
        assert _bullet_salience(lens, mixed) is SalienceLevel.CORE

    def test_dangling_bullet_refs_flag_the_bullet_disputed(self) -> None:
        fx = self._vault()
        disputes = _compute_disputes(fx["vault"])
        dangling = fx["bullets"]["dangling"]
        assert dangling.id in disputes
        flag = disputes[dangling.id]["flags"][0]
        assert flag["type"] == "dangling_reference"
        assert flag["detail"]["field"] == "story_ids"
        assert flag["detail"]["target"] == "story"
        # Clean bullets stay undisputed; the roll-up labels the entity as a
        # bullet with its text.
        assert fx["bullets"]["core"].id not in disputes
        rollup = _handle_get_profile_summary(fx["vault"], "detailed")["disputes"]
        entry = next(
            e for e in rollup["entities"] if e["entity_id"] == str(dangling.id)
        )
        assert entry["kind"] == "bullet"
        assert entry["label"] == dangling.text

    def test_find_bullets_full_inventory_ignores_default_lens(self) -> None:
        # The vault's lens is is_default=True, but find_bullets must NOT
        # auto-apply it: the inventory stays complete (4 bullets, including
        # the one whose only skill is suppressed under that lens).
        fx = self._vault()
        out = _handle_find_bullets(fx["vault"], None, None, None, 20)
        assert out["total"] == 4
        assert "lens" not in out
        texts = {b["text"] for b in out["bullets"]}
        assert fx["bullets"]["suppressed"].text in texts
        # Every record carries the evidence signal.
        by_text = {b["text"]: b for b in out["bullets"]}
        assert by_text[fx["bullets"]["core"].text]["evidenced"] is True
        # A dangling-only evidence link is NOT evidenced (and is disputed).
        assert by_text[fx["bullets"]["dangling"].text]["evidenced"] is False
        assert by_text[fx["bullets"]["dangling"].text]["disputed"] is True

    def test_find_bullets_lens_projection(self) -> None:
        fx = self._vault()
        lens = _resolve_lens(fx["vault"], "ic-architecture")
        out = _handle_find_bullets(fx["vault"], None, None, lens, 20)
        assert out["lens"] == "ic-architecture"
        texts = [b["text"] for b in out["bullets"]]
        # The all-suppressed bullet is hidden; the core bullet leads.
        assert fx["bullets"]["suppressed"].text not in texts
        assert texts[0] == fx["bullets"]["core"].text
        assert out["bullets"][0]["emphasis"] == "core"
        assert all("emphasis" in b for b in out["bullets"])

    def test_find_bullets_filters(self) -> None:
        fx = self._vault()
        # skill: name substring over linked skills.
        out = _handle_find_bullets(fx["vault"], None, "architecture", None, 20)
        assert [b["text"] for b in out["bullets"]] == [fx["bullets"]["core"].text]
        # query: free text over bullet text + theme tags.
        out = _handle_find_bullets(fx["vault"], "warehouse", None, None, 20)
        assert [b["text"] for b in out["bullets"]] == [
            fx["bullets"]["dangling"].text
        ]
        out = _handle_find_bullets(fx["vault"], "architecture", None, None, 20)
        # matches the theme tag AND the other bullet's text substring
        assert fx["bullets"]["core"].text in [b["text"] for b in out["bullets"]]

    def test_profile_summary_bullets_are_lens_aware(self) -> None:
        fx = self._vault()
        vault = fx["vault"]
        lens = _resolve_lens(vault, "ic-architecture")
        out = _handle_get_profile_summary(vault, "detailed", lens)
        staff = next(
            e for e in out["signature_experiences"] if e["title"] == "Staff Engineer"
        )
        texts = [b["text"] for b in staff["bullets"]]
        # Suppressed hidden, core first, and each entry carries the trust
        # signals.
        assert fx["bullets"]["suppressed"].text not in texts
        assert texts[0] == fx["bullets"]["core"].text
        assert staff["bullets"][0]["evidenced"] is True
        assert staff["bullets"][0]["disputed"] is False

    def test_profile_summary_omits_bullets_when_none_visible(self) -> None:
        fx = self._vault()
        vault = fx["vault"]
        # An experience with no bullets never gains the key (canonical and
        # lens renderings alike).
        bare = ExperienceSchema(title="Old Role", company="X", start_date="2019-01")
        vault.experiences.append(bare)
        out = _handle_get_profile_summary(vault, "detailed")
        old = next(
            (e for e in out["signature_experiences"] if e["title"] == "Old Role"),
            None,
        )
        if old is not None:  # only 3 signature slots; assert only if present
            assert "bullets" not in old

    def test_bullet_text_validation(self) -> None:
        with pytest.raises(ValueError, match="non-blank"):
            BulletSchema(text="   ")
        with pytest.raises(ValueError):
            BulletSchema(text="x" * 301)
