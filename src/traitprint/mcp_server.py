"""FastMCP stdio server exposing the local vault to AI agents.

Response schemas mirror the cloud MCP server
(``supabase/functions/mcp-server/index.ts``) so switching an agent between
local and cloud is a URL swap. Four tools are exposed:

- ``get_profile_summary``
- ``search_skills``
- ``find_story``
- ``get_philosophy``

Every tool returns a ``{"result": <payload>, "meta": {...}}`` envelope.

It also exposes five *prompts* — ``fill_vault``, ``mine_story_gaps``,
``discover_skills``, ``draft_star_story``, and ``audit_coherence`` — adapted
from the Cloud Experience Mining engine. They hand an agent a ready-made
workflow for helping the user build out and tighten their vault. Prompts are
local-only (the cloud server mirrors the four tools, not these prompts).
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from traitprint import __version__
from traitprint.schema import PhilosophySchema, StorySchema, VaultSchema
from traitprint.taxonomy import TaxonomyEntry, build_neighbor_index, load_taxonomy
from traitprint.vault import VaultStore

SERVER_NAME = "traitprint-local"
SERVER_VERSION = __version__

PROFICIENCY_LABELS = ("familiar", "working", "expert", "authority")
PROFICIENCY_ORDER = {label: i for i, label in enumerate(PROFICIENCY_LABELS)}


def _map_proficiency(level: int) -> str:
    """Bucket a 1-10 proficiency into the cloud's 4-label enum."""
    if level <= 2:
        return "familiar"
    if level <= 5:
        return "working"
    if level <= 8:
        return "expert"
    return "authority"


def _meets_proficiency(level: str, minimum: str) -> bool:
    return PROFICIENCY_ORDER.get(level, 0) >= PROFICIENCY_ORDER.get(minimum, 0)


def _now_iso() -> str:
    """Return UTC time in ISO-8601 with a ``Z`` suffix (cloud format)."""
    now = datetime.now(timezone.utc)
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _make_meta() -> dict[str, Any]:
    return {
        "server_version": SERVER_VERSION,
        "trust_layer_status": "active",
        "generated_at": _now_iso(),
    }


def _envelope(payload: Any) -> dict[str, Any]:
    return {"result": payload, "meta": _make_meta()}


def _keywords(text: str) -> list[str]:
    return [w for w in text.lower().split() if len(w) >= 3]


def _keyword_score(kws: list[str], content: str) -> float:
    if not kws:
        return 0.0
    lc = content.lower()
    hits = sum(1 for kw in kws if kw in lc)
    return hits / len(kws)


_WIN_MARKERS = (
    "cut ",
    "saved",
    "shipped",
    "delivered",
    "increased",
    "improved",
    "reduced",
    "achieved",
    "grew",
    "won",
    "exceeded",
    "percent",
    "%",
)
_FAILURE_MARKERS = (
    "failed",
    "missed",
    "rolled back",
    "rollback",
    "outage",
    "broke",
    "abandoned",
    "cancelled",
    "canceled",
    "regressed",
)


def _infer_outcome(result: str) -> str:
    """Classify a story result as win/failure/learning via keyword heuristic."""
    lc = result.lower()
    if any(m in lc for m in _FAILURE_MARKERS):
        return "failure"
    if any(m in lc for m in _WIN_MARKERS):
        return "win"
    return "learning"


def _round3(value: float) -> float:
    return round(min(max(value, 0.0), 1.0) * 1000) / 1000


def _normalize_query(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


# Short noise words dropped before token matching so queries like
# "Python programming" do not require every word to land a hit.
_QUERY_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "by",
        "for",
        "from",
        "in",
        "my",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)


def _tokenize(text: str) -> list[str]:
    """Split ``text`` into lowercase word tokens, dropping stopwords."""
    if not text:
        return []
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return [t for t in cleaned.split() if len(t) >= 2 and t not in _QUERY_STOPWORDS]


def _period(start: str, end: str) -> str:
    tail = end if end else "present"
    parts = [p for p in (start, tail) if p]
    return " – ".join(parts)


def _first_sentence(text: str) -> str | None:
    if not text:
        return None
    return text.split(". ")[0] or None


def _match_taxonomy(
    query: str, taxonomy: list[TaxonomyEntry]
) -> tuple[set[UUID], bool]:
    """Return (matching taxonomy IDs, used_alias flag).

    Matches in two ways:
    1. Full-query substring/alias match (as before) — handles multi-word
       aliases like "structured query language".
    2. Per-token match — any query token equal to an alias or present as
       a token in the entry name. Enables "Python programming" to hit
       the Python entry via its "python" token.

    Alias hits (full or token) set ``used_alias=True``.
    """
    normalized = _normalize_query(query)
    if not normalized:
        return set(), False

    tokens = set(_tokenize(query))
    direct_ids: set[UUID] = set()
    used_alias = False
    for entry in taxonomy:
        entry_name_lower = entry.name.lower()
        entry_name_tokens = set(_tokenize(entry.name))
        aliases_lower = [a.lower() for a in entry.aliases]

        full_alias_hit = any(a == normalized for a in aliases_lower)
        full_name_hit = normalized in entry_name_lower

        token_alias_hit = bool(tokens) and any(a in tokens for a in aliases_lower)
        token_name_hit = bool(tokens) and bool(tokens & entry_name_tokens)

        if full_alias_hit or token_alias_hit:
            used_alias = True
        if full_alias_hit or full_name_hit or token_alias_hit or token_name_hit:
            direct_ids.add(entry.id)
    return direct_ids, used_alias


def _expand_query_tokens(
    query: str, taxonomy: list[TaxonomyEntry], matched_ids: set[UUID]
) -> set[str]:
    """Return the query's tokens augmented with synonyms from matched taxonomy entries.

    For every taxonomy entry the query hit (``matched_ids``), add each
    alias and every token of the canonical name. This lets a query like
    "python3" (alias) also match skills named "Python" or "python
    scripting" through the shared ``python`` token.
    """
    expanded: set[str] = set(_tokenize(query))
    if not matched_ids:
        return expanded
    for entry in taxonomy:
        if entry.id not in matched_ids:
            continue
        expanded.update(_tokenize(entry.name))
        for alias in entry.aliases:
            expanded.update(_tokenize(alias))
    return expanded


def _skill_matches(
    skill_name: str, expanded_tokens: set[str], normalized_query: str
) -> tuple[bool, float]:
    """Decide whether ``skill_name`` matches and return (hit, distance).

    ``distance`` is ``1 - token_overlap_fraction`` (bounded to [0, 1]).
    Zero tokens matched returns ``(False, 1.0)``. A full normalized
    query substring hit also counts as a match (for multi-word skill
    names that do not share tokens with the query directly).
    """
    name_lower = skill_name.lower()
    name_tokens = set(_tokenize(skill_name))

    overlap = expanded_tokens & name_tokens
    substring_hits = {t for t in expanded_tokens - overlap if t in name_lower}
    hits = overlap | substring_hits
    full_hit = bool(normalized_query) and normalized_query in name_lower

    if not hits and not full_hit:
        return False, 1.0

    denom = max(len(expanded_tokens), 1)
    distance = max(0.0, 1.0 - len(hits) / denom) if hits else 0.5
    return True, distance


def _story_evidence_by_skill(
    stories: list[StorySchema],
) -> dict[UUID, dict[str, Any]]:
    """Return {skill_id: {count, top}} aggregated across stories."""
    evidence: dict[UUID, dict[str, Any]] = {}
    for story in stories:
        snippet = _first_sentence(story.result) or _first_sentence(story.situation)
        for sid in story.skill_ids:
            entry = evidence.setdefault(sid, {"count": 0, "top": None})
            entry["count"] += 1
            if entry["top"] is None and snippet:
                entry["top"] = snippet
    return evidence


# ── Tool handlers ───────────────────────────────────────────────────


def _handle_get_profile_summary(vault: VaultSchema, depth: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "headline": vault.profile.headline or vault.profile.display_name or "",
        "bio": vault.profile.summary or "",
    }
    if depth == "brief":
        return result

    skill_limit = 10 if depth == "detailed" else 5
    skills = sorted(
        vault.skills,
        key=lambda s: (-s.proficiency, -s.created_at.timestamp()),
    )[:skill_limit]
    result["top_skills"] = [
        {
            "name": s.name,
            "proficiency": _map_proficiency(s.proficiency),
            "evidence": None,
            "disputed": False,
        }
        for s in skills
    ]
    if depth != "detailed":
        return result

    experiences = sorted(vault.experiences, key=lambda e: -e.created_at.timestamp())[:3]
    result["signature_experiences"] = [
        {
            "title": e.title,
            "organization": e.company or None,
            "period": _period(e.start_date, e.end_date),
            "evidence": None,
            "disputed": False,
        }
        for e in experiences
    ]
    philosophies = sorted(vault.philosophies, key=lambda p: -p.created_at.timestamp())[
        :3
    ]
    result["core_philosophies"] = [
        {
            "topic": p.title,
            "stance": p.description,
            "evidence": None,
            "disputed": False,
        }
        for p in philosophies
    ]
    return result


def _graph_expansion(
    direct_ids: set[UUID],
    neighbor_index: dict[UUID, dict[UUID, float]],
) -> dict[UUID, float]:
    """Return ``{neighbor_id: best_distance}`` reachable from ``direct_ids``.

    Only one-hop neighbors are included. Neighbors that are themselves in
    ``direct_ids`` are excluded so a direct match always wins over a graph
    match. When multiple ``direct_ids`` reach the same neighbor, the smaller
    distance is kept.
    """
    expansion: dict[UUID, float] = {}
    for direct_id in direct_ids:
        for neighbor_id, distance in neighbor_index.get(direct_id, {}).items():
            if neighbor_id in direct_ids:
                continue
            existing = expansion.get(neighbor_id)
            if existing is None or distance < existing:
                expansion[neighbor_id] = distance
    return expansion


def _handle_search_skills(
    vault: VaultSchema,
    taxonomy: list[TaxonomyEntry],
    query: str,
    min_proficiency: str | None,
    limit: int,
) -> dict[str, Any]:
    direct_ids, used_alias = _match_taxonomy(query, taxonomy)
    neighbor_index = build_neighbor_index(taxonomy)
    graph_distances = _graph_expansion(direct_ids, neighbor_index)
    normalized = _normalize_query(query)
    expanded_tokens = _expand_query_tokens(query, taxonomy, direct_ids)

    evidence = _story_evidence_by_skill(vault.stories)

    used_distance_graph = False
    matches: list[dict[str, Any]] = []
    for skill in vault.skills:
        tax_hit = bool(skill.taxonomy_id and skill.taxonomy_id in direct_ids)
        graph_hit = bool(
            not tax_hit and skill.taxonomy_id and skill.taxonomy_id in graph_distances
        )
        name_hit, name_distance = _skill_matches(
            skill.name, expanded_tokens, normalized
        )
        if not (tax_hit or graph_hit or name_hit):
            continue

        if tax_hit:
            distance = 0.0
        elif graph_hit and skill.taxonomy_id is not None:
            distance = graph_distances[skill.taxonomy_id]
            used_distance_graph = True
        else:
            distance = name_distance

        prof = _map_proficiency(skill.proficiency)
        if min_proficiency and not _meets_proficiency(prof, min_proficiency):
            continue

        ev = evidence.get(skill.id, {"count": 0, "top": None})
        matches.append(
            {
                "name": skill.name,
                "canonical_name": skill.name,
                "proficiency": prof,
                "years_active": None,
                "evidence_count": ev["count"],
                "top_evidence": ev["top"],
                "match_distance": distance,
                "evidence": skill.notes or None,
                "disputed": False,
            }
        )

    matches.sort(
        key=lambda m: (
            m["match_distance"],
            -PROFICIENCY_ORDER.get(m["proficiency"], 0),
            -m["evidence_count"],
        )
    )

    return {
        "matches": matches[:limit],
        "query_interpretation": {
            "matched_taxonomy_ids": [str(t) for t in direct_ids],
            "used_alias": used_alias,
            "used_distance_graph": used_distance_graph,
        },
    }


def _handle_find_story(
    vault: VaultSchema,
    situation: str | None,
    theme: str | None,
    outcome: str | None,
    limit: int,
    query: str | None = None,
) -> dict[str, Any]:
    if not situation and not theme and not outcome and not query:
        raise ValueError(
            "Provide at least one filter: query (free-text), "
            "situation, theme, or outcome."
        )

    # Structured params take precedence over free-text query.
    if query and not (situation or theme or outcome):
        situation = query

    complete = [
        s for s in vault.stories if s.situation and s.task and s.action and s.result
    ]
    if not complete:
        return {"stories": []}

    sit_kw = _keywords(situation) if situation else []
    theme_kw = _keywords(theme) if theme else []
    has_text_filter = bool(sit_kw or theme_kw)

    skill_name_by_id = {skill.id: skill.name for skill in vault.skills}

    scored: list[tuple[StorySchema, float, str]] = []
    for story in complete:
        story_outcome = _infer_outcome(story.result)
        if outcome and story_outcome != outcome:
            continue
        content = " ".join(
            [story.title, story.situation, story.task, story.action, story.result]
        )
        score = 0.0
        if sit_kw:
            score += _keyword_score(sit_kw, content)
        if theme_kw:
            score += _keyword_score(theme_kw, content)
        scored.append((story, score, story_outcome))

    if has_text_filter:
        scored = [x for x in scored if x[1] > 0]

    scored.sort(key=lambda x: -x[1])

    stories_out: list[dict[str, Any]] = []
    for story, score, story_outcome in scored[:limit]:
        related_skills = [
            skill_name_by_id[sid] for sid in story.skill_ids if sid in skill_name_by_id
        ]
        stories_out.append(
            {
                "id": str(story.id),
                "title": story.title,
                "situation": story.situation,
                "task": story.task,
                "action": story.action,
                "result": story.result,
                "lesson": None,
                "outcome": story_outcome,
                "related_skills": related_skills,
                "related_experience_id": (
                    str(story.experience_id) if story.experience_id else None
                ),
                "match_score": _round3(score),
                "evidence": None,
                "disputed": False,
            }
        )
    return {"stories": stories_out}


def _handle_get_philosophy(
    vault: VaultSchema, topic: str, limit: int
) -> dict[str, Any]:
    topic = topic.strip()
    if not vault.philosophies:
        return {"philosophies": []}

    topic_kw = _keywords(topic)
    scored: list[tuple[PhilosophySchema, float]] = [
        (p, _keyword_score(topic_kw, f"{p.title} {p.description}"))
        for p in vault.philosophies
    ]

    if topic:
        top = [x for x in scored if x[1] > 0]
        top.sort(key=lambda x: -x[1])
    else:
        top = sorted(scored, key=lambda x: -x[0].created_at.timestamp())
    top = top[:limit]

    story_by_id = {s.id: s for s in vault.stories}

    philosophies_out: list[dict[str, Any]] = []
    for phil, score in top:
        supporting: list[str] = []
        for sid in phil.evidence_story_ids:
            story = story_by_id.get(sid)
            if story is None:
                continue
            snippet = _first_sentence(story.result) or _first_sentence(story.situation)
            if snippet:
                supporting.append(snippet)
        philosophies_out.append(
            {
                "id": str(phil.id),
                "topic": phil.title,
                "stance": phil.description,
                "supporting_examples": supporting,
                "related_story_ids": [str(sid) for sid in phil.evidence_story_ids],
                "match_score": _round3(score),
                "evidence": None,
                "disputed": False,
            }
        )
    return {"philosophies": philosophies_out}


# ── Prompt builders ─────────────────────────────────────────────────
#
# These power the MCP *prompts* an agent pulls to help a user fill out and
# tighten their vault. They are adapted from the Traitprint Cloud Experience
# Mining engine (``src/lib/vault/mining.ts``) — the canonical Socratic system
# prompt plus its STORY OPPORTUNITY, SKILL DISCOVERY, and FOCUSED SKILL DEEP
# DIVE modes — re-pointed at the local CLI as the write path (Cloud parses
# [SKILLS_EXTRACTED] blocks from a chat; Local has the agent run the CLI
# directly). Pure string builders so they unit-test without an MCP client.

_SECTIONS_HINT = "skills, experiences, stories, philosophies, education"

# Shared coaching contract, ported from the Cloud mining system prompt.
_COACH_CONTRACT = """\
You are an expert career coach conducting a Socratic interview to help the \
user discover and articulate their professional experience. Your role:
1. Ask thoughtful follow-up questions that dig deeper into their experiences.
2. Help them identify specific skills they demonstrated (technical and soft).
3. Draw out concrete examples and measurable outcomes.
4. Be encouraging but thorough — don't accept vague answers.
5. Structure their stories in STAR format (Situation, Task, Action, Result).
6. Welcome clarifying questions — this is a two-way conversation.

VOICE: Always use second person. Address the user as "you" ("Tell me about…", \
"Walk me through how you…", "What was your role in…"). Keep follow-ups concise \
(2-3 sentences). Rate proficiency on a 1-10 scale from DEMONSTRATED evidence, \
not self-report — if they claim expertise but describe basic usage, rate at \
the evidence-supported level."""

# How the agent writes what it learns. Cloud emits tagged blocks a parser
# ingests; Local has the agent run these CLI commands (itself if it has a
# shell, otherwise by handing the user the exact command).
_WRITE_PATH = """\
Write each thing you learn to the vault with the CLI:
- Skill: `traitprint vault add-skill "Postgres" --proficiency 8 --category technical`
- Experience: `traitprint vault add-experience --title ... --company ... \
--start-date YYYY-MM`
- Story (STAR): `traitprint vault add-story --title ... --situation ... \
--task ... --action ... --result ... --experience-id <UUID> --skill-id <UUID>`
- Philosophy: `traitprint vault add-philosophy --title ... --category \
leadership --description ... --evidence-id <STORY_UUID>`
- Profile: `traitprint vault set-profile --name ... --headline ... --summary ...`
Link aggressively: a story names the skills it proves (`--skill-id`) and the \
experience it came from (`--experience-id`); a philosophy points at an \
evidence story (`--evidence-id`). Those links are what make the vault cohere."""


def _fill_vault_prompt(focus: str = "") -> str:
    target = (
        f"Concentrate on the **{focus}** section."
        if focus.strip()
        else f"Cover every section: {_SECTIONS_HINT}."
    )
    return f"""\
{_COACH_CONTRACT}

You are helping build out the user's Traitprint vault. {target}

Start by reading what already exists so you don't re-ask: call \
`get_profile_summary` (depth="detailed"), then `search_skills` for their main \
areas. Then interview one topic at a time. Extract skills eagerly — if they \
mention a skill, tool, or capability even once, add it (start conservative on \
proficiency; refine as evidence emerges). When you have enough detail for all \
four STAR components, capture the story too.

{_WRITE_PATH}

When you've added a batch, run `traitprint vault audit` to see what's still \
thin or unsupported, and close the gaps it reports."""


def _mine_story_gaps_prompt() -> str:
    return f"""\
{_COACH_CONTRACT}

STORY OPPORTUNITY MODE — you are mining specifically for STAR stories to \
strengthen the user's profile.

First find the gaps: run `traitprint vault audit` and look for \
`skill.unsupported_strength` findings (strong skills with no story) and \
`experience.no_story` findings (roles with nothing attached). Those are your \
worklist.

YOUR MISSION:
- Work through the gaps one at a time, highest-proficiency / most-important \
first.
- For each, ask targeted questions to elicit a complete STAR story.
- When you have all four components, save it and link it to the skill and \
experience it belongs to (`--skill-id`, `--experience-id`).
- Track progress out loud: "Great, that's 3 of 8 skills with stories now."
- If the user genuinely can't recall a story for something, note it and move \
on — don't force it.

{_WRITE_PATH}"""


def _discover_skills_prompt() -> str:
    return f"""\
{_COACH_CONTRACT}

SKILL DISCOVERY MODE — you are mining for LATENT skills the user has but \
hasn't added to their vault yet.

Read their current skills first (`search_skills` across their domains, or \
`get_profile_summary` depth="detailed") so you probe for what's missing, not \
what's already there.

YOUR MISSION:
- Probe for experience adjacent to what they've listed — if they have \
"Docker", ask about orchestration, CI/CD, infra automation; if they have \
"React", ask about state management, testing, accessibility.
- When you find real evidence of a skill, add it at an evidence-supported \
proficiency, and mine for a STAR story that demonstrates it.
- If they truly don't have experience with something, acknowledge it and move \
on — don't pad the vault.

{_WRITE_PATH}"""


def _draft_star_story_prompt(experience: str = "") -> str:
    about = (
        f"The story is about: {experience.strip()}."
        if experience.strip()
        else "Ask the user which experience or accomplishment the story is about."
    )
    return f"""\
{_COACH_CONTRACT}

FOCUSED STORY DEEP DIVE — help the user turn one raw accomplishment into a \
crisp, well-linked STAR story. {about}

Draw it out one field at a time, pushing for specifics:
- **Situation**: the context and the stakes — what was at risk or broken?
- **Task**: what *you specifically* were responsible for (not "the team").
- **Action**: the concrete steps and the key decision you made. Use active, \
first-person language; replace vague phrasing ("helped with", "worked on") \
with what you actually did.
- **Result**: the measurable outcome — a number, a delta, a shipped thing. If \
they only restate the task, push for the actual effect.

Before saving, find which existing skills this proves (`search_skills` for \
their UUIDs) and which experience it happened during, then save and link it:

`traitprint vault add-story --title "..." --situation "..." --task "..." \
--action "..." --result "..." --experience-id <UUID> --skill-id <UUID>`

A complete, linked story scores as "demonstrates"-level evidence — that's what \
makes `find_story` and `search_skills` return proof instead of empty results."""


def _audit_coherence_prompt() -> str:
    return """\
You are reviewing the user's Traitprint vault for narrative coherence — does \
the story it tells hang together and back up its own claims?

FRAMING: never say "you failed." Say "your profile doesn't yet demonstrate X." \
Philosophy *tensions* are nuance (context-dependent thinking), not bugs — \
present them as a strength, not a contradiction to fix.

1. Run the mechanical pass: `traitprint vault audit` (add `--json` to parse). \
It scores each STAR story (Polished/Strong/Solid/Draft + evidence level), and \
flags unsupported skill claims, philosophies with no evidence, broken stories, \
dangling references, roles with no story, and contradictions between stories \
(conflicting metrics or leader-vs-IC role claims). With no shell, reconstruct \
the view from `get_profile_summary` (depth="detailed"), `search_skills`, \
`find_story`, and `get_philosophy`.

2. Then apply judgment the mechanical pass can't:
   - **Consistency**: do the headline, summary, top skills, and stories \
describe the same person?
   - **Voice**: is the tone consistent across stories?
   - **Arc**: do the experiences form a coherent trajectory?
   - **Evidence quality**: are STAR "results" real outcomes (with numbers), or \
restatements of the task?

3. Report findings grouped by severity (critical → major → minor), each with \
the concrete fix — the exact `traitprint vault ...` command or the missing \
detail to ask for. Don't edit the vault without confirming with the user."""


# ── Server factory ──────────────────────────────────────────────────


def create_server(store: VaultStore) -> FastMCP:
    """Build a FastMCP instance bound to ``store``.

    Each tool loads the vault fresh so that edits to ``vault.json``
    between tool calls are picked up without restarting the server.
    """
    taxonomy = load_taxonomy()
    mcp = FastMCP(SERVER_NAME)
    # FastMCP does not forward a version to the underlying MCPServer,
    # so it falls back to the MCP-SDK package version.  Set it
    # explicitly so ``serverInfo.version`` reports *our* version.
    mcp._mcp_server.version = SERVER_VERSION

    @mcp.tool(
        description=(
            "One-shot identity primer. Returns headline, bio, top skills, "
            "and optionally signature experiences and core philosophies."
        )
    )
    def get_profile_summary(
        depth: Literal["brief", "standard", "detailed"] = "standard",
    ) -> dict[str, Any]:
        vault = store.load()
        return _envelope(_handle_get_profile_summary(vault, depth))

    @mcp.tool(
        description=(
            "Search the vault for skills matching a query. Returns "
            "ranked matches with proficiency and evidence."
        )
    )
    def search_skills(
        query: str,
        min_proficiency: (
            Literal["familiar", "working", "expert", "authority"] | None
        ) = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("query must be non-empty")
        limit = max(1, min(limit, 25))
        vault = store.load()
        return _envelope(
            _handle_search_skills(vault, taxonomy, query, min_proficiency, limit)
        )

    @mcp.tool(
        description=(
            "STAR-pattern narrative retrieval. 'Tell me about a time "
            "when…' Provide at least one filter: query (free-text, "
            "searches across all STAR fields), situation, theme, or "
            "outcome. Structured filters take precedence over query."
        )
    )
    def find_story(
        query: str | None = None,
        situation: str | None = None,
        theme: str | None = None,
        outcome: Literal["win", "failure", "learning"] | None = None,
        limit: int = 3,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 5))
        vault = store.load()
        return _envelope(
            _handle_find_story(vault, situation, theme, outcome, limit, query=query)
        )

    @mcp.tool(
        description="Query stated beliefs and positions. 'What's their stance on X?'"
    )
    def get_philosophy(topic: str | None = None, limit: int = 3) -> dict[str, Any]:
        limit = max(1, min(limit, 5))
        vault = store.load()
        return _envelope(_handle_get_philosophy(vault, topic or "", limit))

    @mcp.prompt(
        description=(
            "Socratic interview that populates the vault (skills, experiences, "
            "STAR stories, philosophies, education) via the traitprint CLI. "
            "Optional 'focus' narrows to one section."
        )
    )
    def fill_vault(focus: str = "") -> str:
        return _fill_vault_prompt(focus)

    @mcp.prompt(
        description=(
            "STORY OPPORTUNITY mode: mine STAR stories for the skills and "
            "experiences the audit flags as having no story behind them."
        )
    )
    def mine_story_gaps() -> str:
        return _mine_story_gaps_prompt()

    @mcp.prompt(
        description=(
            "SKILL DISCOVERY mode: probe for latent skills the user has but "
            "hasn't added to their vault yet."
        )
    )
    def discover_skills() -> str:
        return _discover_skills_prompt()

    @mcp.prompt(
        description=(
            "FOCUSED deep dive: draft one well-formed STAR story and link it "
            "to the right skills and experience. Optional 'experience' seeds "
            "the topic."
        )
    )
    def draft_star_story(experience: str = "") -> str:
        return _draft_star_story_prompt(experience)

    @mcp.prompt(
        description=(
            "Review the vault for narrative coherence: run 'traitprint vault "
            "audit' (story scores, gaps, contradictions, tensions), then apply "
            "judgment on consistency, voice, and evidence quality."
        )
    )
    def audit_coherence() -> str:
        return _audit_coherence_prompt()

    # Expose handles so tests can reach the raw logic without stdio.
    mcp._traitprint_store = store  # type: ignore[attr-defined]
    mcp._traitprint_taxonomy = taxonomy  # type: ignore[attr-defined]
    return mcp


def run_stdio(store: VaultStore) -> None:
    """Run the MCP server over stdio (blocking).

    Forces stdout into line-buffered mode so every JSON-RPC response
    (each terminated by ``\\n``) flushes immediately. Without this,
    Python's default block buffering on non-TTY stdout can hang the
    client waiting for a response that is sitting in a buffer.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    create_server(store).run(transport="stdio")


__all__ = [
    "SERVER_NAME",
    "SERVER_VERSION",
    "_audit_coherence_prompt",
    "_discover_skills_prompt",
    "_draft_star_story_prompt",
    "_fill_vault_prompt",
    "_mine_story_gaps_prompt",
    "create_server",
    "run_stdio",
]
