"""FastMCP stdio server exposing the local vault to AI agents.

Response schemas mirror the cloud MCP server
(``supabase/functions/mcp-server/index.ts``) so switching an agent between
local and cloud is a URL swap. Four tools are exposed:

- ``get_profile_summary``
- ``search_skills``
- ``find_story``
- ``get_philosophy``

Every tool returns a ``{"result": <payload>, "meta": {...}}`` envelope.

Every record a tool emits carries a structured ``dispute`` object (``None``
when the record is clean) alongside the kept-for-compatibility ``disputed``
boolean. A dispute is a dangling UUID cross-reference (Decision D10 / Layer-1
referential integrity) recomputed locally from the same check
:mod:`traitprint.audit` performs — see :func:`_compute_disputes`.
``get_profile_summary`` additionally rolls every disputed entity up into a
vault-wide ``disputes`` inventory.

It also exposes five *prompts* — ``fill_vault``, ``mine_story_gaps``,
``discover_skills``, ``draft_star_story``, and ``audit_coherence``. They hand
an agent a ready-made workflow for helping the user build out and tighten
their vault. Their canonical text lives in the Agent Skills
(``skills/<name>/SKILL.md``); each prompt is a thin wrapper that reads the
skill body at serve time so prompt and skill cannot drift. Prompts are
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
from traitprint.skills import skill_body
from traitprint.taxonomy import TaxonomyEntry, build_neighbor_index, load_taxonomy
from traitprint.vault import VaultStore

SERVER_NAME = "traitprint-local"
SERVER_VERSION = __version__

# MCP response-contract version reported as ``meta.server_version``. This is the
# shape-of-the-response version, NOT the package version (``SERVER_VERSION``,
# still reported as ``serverInfo.version``): both servers report the SAME
# contract version so a consumer cannot distinguish them by it. Bumped to 1.1.0
# with the canonical ``flags[]`` dispute schema (docs/schema/dispute-v1/).
RESPONSE_CONTRACT_VERSION = "1.1.0"

# Five-label proficiency vocabulary, one label per 1-5 level (the vault
# contract: 1 familiar, 2 working, 3 proficient, 4 expert, 5 authority).
# NOTE: this intentionally diverges from the cloud MCP server's current
# 4-label enum (which has no "proficient"); cloud parity catch-up is
# tracked as a cloud-side follow-up — until it lands, this server is
# deliberately ahead of the hosted one.
PROFICIENCY_LABELS = ("familiar", "working", "proficient", "expert", "authority")
PROFICIENCY_ORDER = {label: i for i, label in enumerate(PROFICIENCY_LABELS)}

ProficiencyLabel = Literal["familiar", "working", "proficient", "expert", "authority"]

# Mirrors traitprint.schema.PhilosophyCategory (Literal so FastMCP emits
# a clean enum in the tool schema).
PhilosophyCategoryLabel = Literal[
    "leadership",
    "collaboration",
    "technical-approach",
    "culture",
    "decision-making",
]

# Disputes (Decision D10 / Layer-1 referential integrity). A record is
# *disputed* when it holds a UUID cross-reference (``skill_ids[]``,
# ``experience_id``, ``evidence_story_ids[]``) that no longer resolves to a
# vault entity — the same dangling-reference check :mod:`traitprint.audit`
# performs. The MCP layer recomputes this locally and attaches a structured
# ``dispute`` object to every record. ``source`` marks the dispute as a local
# recompute so a consumer can tell it apart from a cloud "trust-layer" one.
DISPUTE_SOURCE = "local-referential-integrity"


def _map_proficiency(level: int) -> str:
    """Map a 1-5 proficiency onto the five-label vocabulary."""
    index = min(max(int(level), 1), 5) - 1
    return PROFICIENCY_LABELS[index]


def _coerce_min_proficiency(value: str | int | None) -> str | None:
    """Normalize ``min_proficiency``: the five labels or integers 1-5."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("min_proficiency must be a label or an integer 1-5")
    if isinstance(value, int):
        if not 1 <= value <= 5:
            raise ValueError(
                f"min_proficiency integer must be 1-5, got {value}"
            )
        return _map_proficiency(value)
    label = value.strip().lower()
    if label not in PROFICIENCY_ORDER:
        raise ValueError(
            "min_proficiency must be one of "
            f"{', '.join(PROFICIENCY_LABELS)} or an integer 1-5; got {value!r}"
        )
    return label


def _meets_proficiency(level: str, minimum: str) -> bool:
    return PROFICIENCY_ORDER.get(level, 0) >= PROFICIENCY_ORDER.get(minimum, 0)


def _iso(dt: datetime) -> str:
    """Render ``dt`` as ISO-8601 UTC with a ``Z`` suffix (cloud format).

    A naive datetime is assumed to already be UTC: vault timestamps are
    written UTC-aware, so this only tolerates a legacy naive value rather
    than shifting it by the host's local offset.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (
        dt.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _now_iso() -> str:
    """Return UTC time in ISO-8601 with a ``Z`` suffix (cloud format)."""
    return _iso(datetime.now(timezone.utc))


def _make_meta() -> dict[str, Any]:
    return {
        "server_version": RESPONSE_CONTRACT_VERSION,
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


# ── Disputes (canonical schema, see docs/schema/dispute-v1/) ─────────
#
# Both MCP servers emit the same ``dispute`` shape:
#
#   {flags: [{type, reason, detail}], sources: [...], reason, since[, file]}
#
# Local produces two flag types: ``dangling_reference`` (Decision D10 /
# Layer-1 referential integrity, mirroring :mod:`traitprint.audit`) and
# ``contradiction`` of kind ``date_overlap`` (overlapping full-time roles).
# Cloud produces the same shape from its trust layer; ``sources`` is the only
# intended distinguisher. See the schema doc for the full registry.

# Part-time / non-overlapping role markers: a pair is only a date_overlap
# contradiction when NEITHER role looks part-time. Mirrors the cloud scanner.
_PART_TIME_RE = re.compile(
    r"\b(intern|part[\s-]?time|freelance|contract|consulting|volunteer|advisor)\b",
    re.IGNORECASE,
)


def _dangling_flag(
    field: str, ref: UUID, index: int | None, target: str
) -> dict[str, Any]:
    """Build a ``dangling_reference`` flag for one unresolved cross-reference.

    ``index`` is the array position (``None`` for a scalar field such as
    ``experience_id``); ``target`` is the kind the field points at.
    """
    path = field if index is None else f"{field}[{index}]"
    return {
        "type": "dangling_reference",
        "reason": f"dangling reference: {path} does not resolve",
        "detail": {
            "field": field,
            "index": index,
            "id": str(ref),
            "target": target,
        },
    }


def _dangling_flags(
    refs: list[UUID], valid: set[UUID], field: str, target: str
) -> list[dict[str, Any]]:
    """Return a ``dangling_reference`` flag for every entry of ``refs`` missing
    from ``valid``."""
    return [
        _dangling_flag(field, ref, i, target)
        for i, ref in enumerate(refs)
        if ref not in valid
    ]


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_year_month(date_str: str) -> tuple[int, int] | None:
    """Parse a free-text vault date to ``(year, month)``.

    Supports the formats the vault column allows (the MCP reader's
    ``experienceStartKey``): ``YYYY``, ``YYYY-MM``, ``YYYY-MM-DD``, ``YYYY/M``,
    and month-name forms (``Jan 2020`` / ``January 2020``). ``None`` for
    empty/``present``; a bare year (or any other year-bearing string) defaults
    to January.
    """
    if not date_str:
        return None
    s = date_str.strip()
    if not s or s.lower() == "present":
        return None
    m = re.match(r"(\d{4})[-/](\d{1,2})\b", s)
    if m:
        month = int(m.group(2))
        if not 1 <= month <= 12:
            month = 1
        return int(m.group(1)), month
    m = re.match(r"([A-Za-z]{3,})\.?\s+(\d{4})$", s)
    if m:
        named = _MONTHS.get(m.group(1)[:3].lower())
        if named:
            return int(m.group(2)), named
    m = re.search(r"(\d{4})", s)
    if m:
        return int(m.group(1)), 1
    return None


def _month_index(date_str: str) -> int | None:
    """Month ordinal (``year * 12 + (month - 1)``), or ``None`` when
    unparseable / open-ended (``present``)."""
    ym = _parse_year_month(date_str)
    return ym[0] * 12 + (ym[1] - 1) if ym else None


def _range_label(date_str: str) -> str:
    """Normalize a date to ``YYYY-MM`` (or ``present`` for an open/empty end)."""
    if not date_str or date_str.strip().lower() == "present":
        return "present"
    ym = _parse_year_month(date_str)
    if ym is None:
        return date_str.strip() or "present"
    return f"{ym[0]}-{ym[1]:02d}"


def _current_month_index() -> int:
    now = datetime.now(timezone.utc)
    return now.year * 12 + (now.month - 1)


def _is_part_time(exp: Any) -> bool:
    # Title + description only (per docs/schema/dispute-v1/): role metadata, not
    # accomplishments — a full-time role must not be suppressed by an
    # accomplishment that merely mentions "contract"/"consulting" work.
    return bool(_PART_TIME_RE.search(f"{exp.title or ''} {exp.description or ''}"))


def _overlap_flag(
    other_id: UUID,
    self_range: tuple[str, str],
    other_range: tuple[str, str],
    self_id: UUID,
) -> dict[str, Any]:
    """Build a ``contradiction``/``date_overlap`` flag for the *self* record.

    ``entities``/``ranges`` list the self record first so each side of an
    overlapping pair carries its own perspective (symmetric flagging).
    """
    return {
        "type": "contradiction",
        "reason": (
            "These two roles overlap in time "
            f"({self_range[0]} to {self_range[1]} and "
            f"{other_range[0]} to {other_range[1]}). "
            "An interviewer might notice if both were full-time positions."
        ),
        "detail": {
            "kind": "date_overlap",
            "entities": [str(self_id), str(other_id)],
            "ranges": [list(self_range), list(other_range)],
        },
    }


def _date_overlap_flags(experiences: list[Any]) -> dict[UUID, list[dict[str, Any]]]:
    """Symmetric ``date_overlap`` flags for full-time experiences that overlap.

    Ranges are compared at month granularity with a STRICT ``<`` boundary, so a
    shared transition month (one role's end == the next's start) is adjacency,
    not overlap. Every overlapping pair flags BOTH roles (order-independent).
    """
    # An open end date ("present") extends strictly PAST the current month, so a
    # role started this very month still has positive width and two ongoing
    # roles overlap under the strict-< comparison.
    present_idx = _current_month_index() + 1
    parsed: list[tuple[Any, int, int, tuple[str, str], bool] | None] = []
    for exp in experiences:
        start = _month_index(exp.start_date)
        if start is None:
            parsed.append(None)
            continue
        end = _month_index(exp.end_date) if exp.end_date else present_idx
        if end is None:
            end = present_idx
        label = (_range_label(exp.start_date), _range_label(exp.end_date))
        parsed.append((exp, start, end, label, _is_part_time(exp)))

    flags: dict[UUID, list[dict[str, Any]]] = {}
    for i in range(len(parsed)):
        for j in range(i + 1, len(parsed)):
            a, b = parsed[i], parsed[j]
            if a is None or b is None:
                continue
            exp_a, start_a, end_a, range_a, pt_a = a
            exp_b, start_b, end_b, range_b, pt_b = b
            if pt_a or pt_b:
                continue
            if start_a < end_b and start_b < end_a:  # strict overlap
                flags.setdefault(exp_a.id, []).append(
                    _overlap_flag(exp_b.id, range_a, range_b, exp_a.id)
                )
                flags.setdefault(exp_b.id, []).append(
                    _overlap_flag(exp_a.id, range_b, range_a, exp_b.id)
                )
    return flags


def _entity_updated_at(vault: VaultSchema) -> dict[UUID, datetime]:
    updated: dict[UUID, datetime] = {}
    for group in (vault.skills, vault.experiences, vault.stories, vault.philosophies):
        for item in group:
            updated[item.id] = item.updated_at
    return updated


def _dispute(flags: list[dict[str, Any]], since: datetime | None) -> dict[str, Any]:
    """Build the canonical ``dispute`` object from a record's flags."""
    return {
        "flags": flags,
        "sources": [DISPUTE_SOURCE],
        "reason": "; ".join(str(f["reason"]) for f in flags),
        "since": _iso(since) if since is not None else None,
    }


# Canonical dangling-field rank, shared verbatim with the hosted server
# (traitprint-cloud mcp-server DANGLING_FIELD_RANK).
_DANGLING_FIELD_RANK = {"skill_ids": 0, "experience_id": 1, "evidence_story_ids": 2}


def _flag_order_key(flag: dict[str, Any]) -> tuple[int, int, int, str]:
    """Canonical per-entity flag order, shared verbatim with the hosted server
    (``flagOrderKey``), so a record's ``flags[]`` and the derived ``reason`` are
    byte-identical across both servers regardless of discovery order.

    Order: ``dangling_reference`` first (by field rank, then array index), then
    contradictions (``date_overlap`` before other; ``date_overlap`` ordered by
    the PARTNER entity id — a key both servers reproduce from shared data,
    independent of vault list position), then any other type (by reason).
    """
    detail = flag["detail"]
    if flag["type"] == "dangling_reference":
        field = str(detail.get("field", ""))
        index = detail.get("index")
        idx = index if isinstance(index, int) and not isinstance(index, bool) else -1
        return (0, _DANGLING_FIELD_RANK.get(field, 9), idx, "")
    if flag["type"] == "contradiction":
        if detail.get("kind") == "date_overlap":
            entities = detail.get("entities") or []
            partner = str(entities[1]) if len(entities) > 1 else ""
            return (1, 0, 0, partner)
        return (1, 1, 0, str(flag["reason"]))
    return (2, 0, 0, str(flag["reason"]))


def _compute_disputes(vault: VaultSchema) -> dict[UUID, dict[str, Any]]:
    """Recompute per-entity disputes (canonical ``dispute`` objects).

    Produces ``dangling_reference`` flags (mirroring
    :mod:`traitprint.audit`'s dangling-ref checks) and ``contradiction`` /
    ``date_overlap`` flags, so the MCP layer reports the same state without a
    cloud round-trip. Keyed by entity id; only flagged entities appear. Skills
    carry no outgoing references and have no dates, so they are never disputed.
    """
    skill_ids = {s.id for s in vault.skills}
    experience_ids = {e.id for e in vault.experiences}
    story_ids = {s.id for s in vault.stories}

    flags_by_entity: dict[UUID, list[dict[str, Any]]] = {}

    def add(entity_id: UUID, flags: list[dict[str, Any]]) -> None:
        if flags:
            flags_by_entity.setdefault(entity_id, []).extend(flags)

    for story in vault.stories:
        flags = _dangling_flags(story.skill_ids, skill_ids, "skill_ids", "skill")
        if (
            story.experience_id is not None
            and story.experience_id not in experience_ids
        ):
            flags.append(
                _dangling_flag("experience_id", story.experience_id, None, "experience")
            )
        add(story.id, flags)

    for exp in vault.experiences:
        add(exp.id, _dangling_flags(exp.skill_ids, skill_ids, "skill_ids", "skill"))

    for phil in vault.philosophies:
        add(
            phil.id,
            _dangling_flags(
                phil.evidence_story_ids, story_ids, "evidence_story_ids", "story"
            ),
        )

    for entity_id, overlap_flags in _date_overlap_flags(vault.experiences).items():
        add(entity_id, overlap_flags)

    updated = _entity_updated_at(vault)
    return {
        entity_id: _dispute(sorted(flags, key=_flag_order_key), updated.get(entity_id))
        for entity_id, flags in flags_by_entity.items()
    }


def _entity_labels(vault: VaultSchema) -> dict[UUID, tuple[str, str]]:
    """Map every entity id to its ``(kind, label)`` for the disputes roll-up."""
    labels: dict[UUID, tuple[str, str]] = {}
    for skill in vault.skills:
        labels[skill.id] = ("skill", skill.name)
    for exp in vault.experiences:
        labels[exp.id] = ("experience", exp.title)
    for story in vault.stories:
        labels[story.id] = ("story", story.title)
    for phil in vault.philosophies:
        labels[phil.id] = ("philosophy", phil.title)
    return labels


def _disputes_rollup(
    vault: VaultSchema, disputes: dict[UUID, dict[str, Any]]
) -> dict[str, Any]:
    """Inventory every disputed entity vault-wide for ``get_profile_summary``.

    Mirrors the inventory style of the rest of the summary: a count, the
    de-duped ``sources``, and one entry per disputed entity carrying its id,
    kind, human label, flag types, and reason. Entries sort by
    ``(kind, label, id)`` for deterministic output.
    """
    labels = _entity_labels(vault)
    sources: set[str] = set()
    entities: list[dict[str, Any]] = []
    for entity_id, dispute in disputes.items():
        kind, label = labels.get(entity_id, ("unknown", ""))
        sources.update(dispute["sources"])
        entities.append(
            {
                "entity_id": str(entity_id),
                "kind": kind,
                "label": label,
                "flag_types": sorted({str(f["type"]) for f in dispute["flags"]}),
                "reason": dispute["reason"],
            }
        )
    entities.sort(key=lambda e: (e["kind"], e["label"], e["entity_id"]))
    return {
        "count": len(entities),
        "sources": sorted(sources),
        "entities": entities,
    }


# ── Tool handlers ───────────────────────────────────────────────────


def _handle_get_profile_summary(vault: VaultSchema, depth: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "headline": vault.profile.headline or vault.profile.display_name or "",
        "bio": vault.profile.summary or "",
    }
    if depth == "brief":
        return result

    disputes = _compute_disputes(vault)
    result["disputes"] = _disputes_rollup(vault, disputes)

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
            "disputed": s.id in disputes,
            "dispute": disputes.get(s.id),
        }
        for s in skills
    ]
    if depth != "detailed":
        return result

    experiences = sorted(vault.experiences, key=lambda e: -e.created_at.timestamp())[:3]
    # related_skills mirrors find_story's related_skills (names, dangling
    # refs skipped). Contract revision 1.1; the cloud MCP server catches
    # up separately — until then this server is deliberately ahead.
    skill_name_by_id = {s.id: s.name for s in vault.skills}
    result["signature_experiences"] = [
        {
            "title": e.title,
            "organization": e.company or None,
            "period": _period(e.start_date, e.end_date),
            "related_skills": [
                skill_name_by_id[sid]
                for sid in e.skill_ids
                if sid in skill_name_by_id
            ],
            "evidence": None,
            "disputed": e.id in disputes,
            "dispute": disputes.get(e.id),
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
            "disputed": p.id in disputes,
            "dispute": disputes.get(p.id),
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
    min_proficiency: str | int | None,
    limit: int,
) -> dict[str, Any]:
    min_label = _coerce_min_proficiency(min_proficiency)
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
        if min_label and not _meets_proficiency(prof, min_label):
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
                # Skills hold no outgoing vault references, so a skill is
                # never disputed under referential integrity.
                "disputed": False,
                "dispute": None,
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


def _theme_score(theme: str, story: StorySchema, content: str) -> float:
    """Score a story against a theme filter, theme_tags first.

    Strictly tiered so tagged stories outrank body-text hits:
    - exact ``theme_tags`` match → 1.0
    - keyword/substring hit inside the tags → (0.6, 0.9]
    - keyword hit in the STAR body text only → (0.0, 0.5]
    """
    normalized = theme.strip().lower()
    tags = [t.strip().lower() for t in story.theme_tags]
    if normalized and normalized in tags:
        return 1.0
    kws = _keywords(theme)
    if tags and kws:
        tag_score = _keyword_score(kws, " ".join(tags))
        if tag_score > 0:
            return 0.6 + 0.3 * tag_score
    return 0.5 * _keyword_score(kws, content)


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
    has_theme = bool(theme and theme.strip())
    has_text_filter = bool(sit_kw) or has_theme

    disputes = _compute_disputes(vault)
    skill_name_by_id = {skill.id: skill.name for skill in vault.skills}

    scored: list[tuple[StorySchema, float, str]] = []
    for story in complete:
        # Prefer the user-recorded outcome; fall back to the heuristic.
        story_outcome = story.outcome or _infer_outcome(story.result)
        if outcome and story_outcome != outcome:
            continue
        content = " ".join(
            [story.title, story.situation, story.task, story.action, story.result]
        )
        score = 0.0
        if sit_kw:
            score += _keyword_score(sit_kw, content)
        if has_theme and theme is not None:
            # Theme matching covers theme_tags (exact tag highest, then
            # keyword-in-tags) before falling back to STAR body text.
            score += _theme_score(theme, story, content)
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
                "lesson": story.lesson or None,
                "outcome": story_outcome,
                "related_skills": related_skills,
                "related_experience_id": (
                    str(story.experience_id) if story.experience_id else None
                ),
                "match_score": _round3(score),
                "evidence": None,
                "disputed": story.id in disputes,
                "dispute": disputes.get(story.id),
            }
        )
    return {"stories": stories_out}


def _handle_get_philosophy(
    vault: VaultSchema, topic: str, limit: int, category: str | None = None
) -> dict[str, Any]:
    topic = topic.strip()
    cat = (category or "").strip().lower()

    # The category argument is a hard filter: non-matching philosophies
    # (including uncategorized ones) are excluded, never returned at 0.0.
    pool = vault.philosophies
    if cat:
        pool = [p for p in pool if p.category == cat]
    if not pool:
        return {"philosophies": []}

    topic_kw = _keywords(topic)
    scored: list[tuple[PhilosophySchema, float]] = []
    for p in pool:
        text_score = _keyword_score(topic_kw, f"{p.title} {p.description}")
        if topic:
            if cat:
                # Category already matched; topic relevance ranks on top
                # of a category-match floor so scores stay meaningful.
                score = 0.5 + 0.5 * text_score if text_score > 0 else 0.0
            else:
                score = text_score
        elif cat:
            score = 1.0  # exact category match is the whole query
        else:
            score = 0.0  # unfiltered browse
        scored.append((p, score))

    if topic:
        top = [x for x in scored if x[1] > 0]
        top.sort(key=lambda x: -x[1])
    else:
        top = sorted(scored, key=lambda x: -x[0].created_at.timestamp())
    top = top[:limit]

    story_by_id = {s.id: s for s in vault.stories}
    disputes = _compute_disputes(vault)

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
                "disputed": phil.id in disputes,
                "dispute": disputes.get(phil.id),
            }
        )
    return {"philosophies": philosophies_out}


# ── Prompt builders ─────────────────────────────────────────────────
#
# These power the MCP *prompts* an agent pulls to help a user fill out and
# tighten their vault. The canonical text lives in the Agent Skills under
# ``skills/<name>/SKILL.md`` (repo root; packaged as
# ``traitprint/data/skills/`` in wheels). Each builder reads the SKILL.md
# body at serve time so the MCP prompts can never drift from the published
# skills, then appends prompt arguments and an MCP-context note. Pure
# string builders so they unit-test without an MCP client.

# Appended to every prompt: skill bodies assume a shell; MCP clients may
# not have one, but they do have the four read tools.
_MCP_SERVING_NOTE = """

---
Serving context: you received this workflow over MCP. If you have shell
access, run the `traitprint` commands above yourself. If not, do reads with
the MCP tools (`get_profile_summary` with depth="detailed", `search_skills`,
`find_story`, `get_philosophy`) and hand the user the exact `traitprint`
command for every write instead of running it."""


def _skill_prompt(skill_name: str, extra: str = "") -> str:
    body = skill_body(skill_name)
    if extra:
        body = f"{body}\n\n{extra}"
    return body + _MCP_SERVING_NOTE


def _fill_vault_prompt(focus: str = "") -> str:
    extra = ""
    if focus.strip():
        extra = (
            f"FOCUS OVERRIDE: the user asked to concentrate on the "
            f"**{focus.strip()}** section — interview and write only that "
            "section this session."
        )
    return _skill_prompt("traitprint-fill-vault", extra)


def _mine_story_gaps_prompt() -> str:
    return _skill_prompt("traitprint-mine-story-gaps")


def _discover_skills_prompt() -> str:
    return _skill_prompt("traitprint-discover-skills")


def _draft_star_story_prompt(experience: str = "") -> str:
    extra = ""
    if experience.strip():
        extra = f"The story is about: {experience.strip()}."
    return _skill_prompt("traitprint-draft-star-story", extra)


def _audit_coherence_prompt() -> str:
    return _skill_prompt("traitprint-audit-coherence")


# ── Server factory ──────────────────────────────────────────────────


def create_server(store: VaultStore) -> FastMCP:
    """Build a FastMCP instance bound to ``store``.

    Each tool loads the vault fresh so that edits to the vault file
    tree between tool calls are picked up without restarting the server.
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
            "and optionally signature experiences and core philosophies. "
            "depth: 'brief' = headline + bio only; 'standard' (default) = "
            "+ top 5 skills; 'detailed' = + top 10 skills, signature "
            "experiences, and core philosophies."
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
            "ranked matches with proficiency and evidence. Proficiency "
            "labels: familiar (1), working (2), proficient (3), expert "
            "(4), authority (5); min_proficiency accepts any of the five "
            "labels or an integer 1-5."
        )
    )
    def search_skills(
        query: str,
        min_proficiency: ProficiencyLabel | int | None = None,
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
        description=(
            "Query stated beliefs and positions. 'What's their stance on "
            "X?' Filter by topic (keyword match against title and stance) "
            "and/or category (exact match: leadership, collaboration, "
            "technical-approach, culture, decision-making)."
        )
    )
    def get_philosophy(
        topic: str | None = None,
        category: PhilosophyCategoryLabel | None = None,
        limit: int = 3,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 5))
        vault = store.load()
        return _envelope(
            _handle_get_philosophy(vault, topic or "", limit, category=category)
        )

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
    "DISPUTE_SOURCE",
    "SERVER_NAME",
    "SERVER_VERSION",
    "_audit_coherence_prompt",
    "_compute_disputes",
    "_discover_skills_prompt",
    "_draft_star_story_prompt",
    "_fill_vault_prompt",
    "_mine_story_gaps_prompt",
    "create_server",
    "run_stdio",
]
