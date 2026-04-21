"""Tests for the FastMCP stdio server.

Covers:
- Tool registration (list_tools returns the 4 cloud-parity tools).
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
from uuid import UUID

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from traitprint.git_ops import commit, init_repo
from traitprint.mcp_server import (
    SERVER_NAME,
    SERVER_VERSION,
    _envelope,
    _handle_find_story,
    _handle_get_philosophy,
    _handle_get_profile_summary,
    _handle_search_skills,
    _map_proficiency,
    _meets_proficiency,
    create_server,
)
from traitprint.schema import (
    PhilosophyCategory,
    PhilosophySchema,
    ProfileSchema,
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
        schema_version=0,
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
        proficiency=9,
        taxonomy_id=python_tax.id,
        notes="Primary language",
    )
    sql_skill = SkillSchema(name="SQL", category="technical", proficiency=7)
    leadership_skill = SkillSchema(
        name="Team Leadership", category="soft", proficiency=6
    )
    vault.skills = [python_skill, sql_skill, leadership_skill]

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
        assert _map_proficiency(1) == "familiar"
        assert _map_proficiency(2) == "familiar"
        assert _map_proficiency(3) == "working"
        assert _map_proficiency(5) == "working"
        assert _map_proficiency(6) == "expert"
        assert _map_proficiency(8) == "expert"
        assert _map_proficiency(9) == "authority"
        assert _map_proficiency(10) == "authority"

    def test_meets_proficiency(self) -> None:
        assert _meets_proficiency("expert", "working")
        assert _meets_proficiency("authority", "authority")
        assert not _meets_proficiency("familiar", "working")


class TestEnvelope:
    def test_shape(self) -> None:
        env = _envelope({"foo": "bar"})
        assert set(env) == {"result", "meta"}
        assert env["result"] == {"foo": "bar"}
        assert env["meta"]["server_version"] == SERVER_VERSION
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
            assert set(skill) == {"name", "proficiency", "evidence", "disputed"}
            assert skill["disputed"] is False
        assert "signature_experiences" not in out

    def test_detailed_includes_experiences_and_philosophies(
        self, populated_store: VaultStore
    ) -> None:
        out = _handle_get_profile_summary(populated_store.load(), "detailed")
        assert "signature_experiences" in out
        assert "core_philosophies" in out
        phil = out["core_philosophies"][0]
        assert set(phil) == {"topic", "stance", "evidence", "disputed"}
        assert phil["topic"] == "Delegation as Leverage"


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
        assert set(top) == {
            "name",
            "canonical_name",
            "proficiency",
            "years_active",
            "evidence_count",
            "top_evidence",
            "match_distance",
            "evidence",
            "disputed",
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
        # SQL is 7/10 → expert, which meets expert.
        assert any(m["name"] == "SQL" for m in out["matches"])

        out = _handle_search_skills(
            populated_store.load(), load_taxonomy(), "sql", "authority", 10
        )
        assert all(m["name"] != "SQL" for m in out["matches"])

    def test_name_fallback_without_taxonomy(self, populated_store: VaultStore) -> None:
        out = _handle_search_skills(
            populated_store.load(), load_taxonomy(), "leadership", None, 10
        )
        assert any(m["name"] == "Team Leadership" for m in out["matches"])

    def test_multiword_query_token_match(self, vault_dir: Path) -> None:
        """'Python programming' finds both Python and user-added 'python scripting'."""
        store = VaultStore(vault_dir)
        vault = VaultSchema(
            schema_version=0,
            profile=ProfileSchema(display_name="t"),
        )
        taxonomy = load_taxonomy()
        python_tax = next(e for e in taxonomy if e.name == "Python")
        vault.skills = [
            SkillSchema(
                name="Python",
                category="technical",
                proficiency=9,
                taxonomy_id=python_tax.id,
            ),
            SkillSchema(name="python scripting", category="technical", proficiency=5),
            SkillSchema(name="Team Leadership", category="soft", proficiency=6),
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
            schema_version=0,
            profile=ProfileSchema(display_name="t"),
        )
        vault.skills = [
            SkillSchema(name="Go services", category="technical", proficiency=7),
            SkillSchema(name="React", category="technical", proficiency=6),
        ]
        store.save(vault)

        out = _handle_search_skills(store.load(), load_taxonomy(), "golang", None, 10)
        names = {m["name"] for m in out["matches"]}
        assert "Go services" in names
        assert out["query_interpretation"]["used_alias"] is True


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
            "related_experience_id",
            "match_score",
            "evidence",
            "disputed",
        }
        assert story["outcome"] == "win"
        assert story["match_score"] > 0
        # Related skills include Python and SQL
        assert set(story["related_skills"]) == {"Python", "SQL"}
        # id round-trips as a UUID string
        UUID(story["id"])

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


# ── In-process server: tool registration ────────────────────────────


class TestServerRegistration:
    def test_name_and_four_tools(self, populated_store: VaultStore) -> None:
        server = create_server(populated_store)
        assert server.name == SERVER_NAME
        tools = asyncio.run(server.list_tools())
        names = {t.name for t in tools}
        assert names == {
            "get_profile_summary",
            "search_skills",
            "find_story",
            "get_philosophy",
        }

    def test_server_version_in_init_options(self, populated_store: VaultStore) -> None:
        """serverInfo.version must report *our* version, not the MCP SDK."""
        server = create_server(populated_store)
        opts = server._mcp_server.create_initialization_options()
        assert opts.server_version == SERVER_VERSION


# ── JSON-RPC round-trip over stdio ──────────────────────────────────


async def _stdio_roundtrip(vault_dir: Path) -> tuple[list[str], dict[str, str]]:
    """Spawn ``traitprint mcp-serve`` and invoke each tool once.

    Returns (tool names, {tool_name: envelope_result_json}).
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
        await session.initialize()
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

    return names, results


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="stdio_client subprocess behavior is unreliable on Windows",
)
class TestStdioRoundTrip:
    def test_four_tools_callable_via_jsonrpc(self, populated_store: VaultStore) -> None:
        names, results = asyncio.run(_stdio_roundtrip(populated_store.directory))
        assert set(names) == {
            "get_profile_summary",
            "search_skills",
            "find_story",
            "get_philosophy",
        }

        summary = json.loads(results["get_profile_summary"])
        assert set(summary) == {"result", "meta"}
        assert summary["result"]["headline"] == "Data Engineering Leader"
        assert summary["meta"]["server_version"] == SERVER_VERSION

        skills = json.loads(results["search_skills"])
        assert skills["result"]["query_interpretation"]["used_distance_graph"] is False
        assert any(m["name"] == "Python" for m in skills["result"]["matches"])

        stories = json.loads(results["find_story"])
        assert len(stories["result"]["stories"]) == 1
        assert stories["result"]["stories"][0]["outcome"] == "win"

        phils = json.loads(results["get_philosophy"])
        assert len(phils["result"]["philosophies"]) == 1
        assert phils["result"]["philosophies"][0]["topic"] == "Delegation as Leverage"
