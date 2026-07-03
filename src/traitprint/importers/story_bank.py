"""Deterministic importer for agent-CLI job-search working directories.

Detects the ``config/profile.yml`` + ``interview-prep/*.md`` layout by
*shape* (never by brand) and stages everything through the proposals
channel — nothing is written to the vault directly:

- story-bank markdown blocks -> one ``add_story`` proposal each
  (STAR + Reflection -> ``## Lesson`` via ``payload.body``; tags resolve
  to existing vault skill UUIDs where possible, the rest become
  ``theme_tags``; a ``Source:`` line matches a vault experience).
- ``config/profile.yml`` -> one ``update_profile`` proposal
  (candidate/narrative keys mapped onto JSON Resume basics).
- ``cv.md`` is NOT parsed here — the existing
  ``traitprint vault import-resume --propose`` pipeline handles it; the
  CLI prints that pointer when a cv.md is present.

The layout is compatible with career-ops-style working dirs (nominative
reference only). The tolerant STAR field-line regex family is adapted
from career-ops's match-star.mjs (MIT); attribution lives in this
docstring — the brand name must not appear in command or module names.

No LLM, no network: parsing is regex + YAML, resolution is exact-match
against the vault and the bundled O*NET taxonomy. UUIDs are never
invented — an unresolvable tag or source is simply left unlinked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from traitprint.schema import StorySchema, VaultSchema
from traitprint.taxonomy import find_exact
from traitprint.vault_io import render_story_body

#: ``source`` stamped on every staged proposal.
IMPORT_SOURCE = "story-bank-import"

_STAR_FIELDS = ("situation", "task", "action", "result")

#: Tolerant field-line matcher: optional list bullet, optional bold,
#: canonical or shorthand labels, case-insensitive.
_FIELD_RE = re.compile(
    r"^\s*(?:[-*]\s+)?\*{0,2}"
    r"(?P<label>situation|task|action|result|reflection|lesson|source|"
    r"best for questions about|tags)"
    # The colon may sit inside or outside the closing bold marker:
    # "Situation:", "**Situation:**", "**Situation**:".
    r"\*{0,2}\s*:\s*\*{0,2}\s*(?P<value>.*)$",
    re.IGNORECASE,
)

_HEADING_RE = re.compile(r"^###\s+(?:\[(?P<theme>[^\]]*)\]\s*)?(?P<title>.+?)\s*$")

_LABEL_TO_FIELD = {
    "situation": "situation",
    "task": "task",
    "action": "action",
    "result": "result",
    "reflection": "lesson",
    "lesson": "lesson",
    "source": "source",
    "best for questions about": "tags",
    "tags": "tags",
}


@dataclass
class ParsedStory:
    """One story block, as parsed (pre-resolution)."""

    title: str
    theme: str = ""
    situation: str = ""
    task: str = ""
    action: str = ""
    result: str = ""
    lesson: str = ""
    source: str = ""
    tags: list[str] = field(default_factory=list)
    origin: str = ""  # file the block came from


def parse_story_bank(text: str, *, origin: str = "") -> list[ParsedStory]:
    """Parse ``### [theme] Title`` STAR blocks from markdown, tolerantly.

    Unlabeled lines continue the previous field; blocks with a title but
    no recognizable STAR field at all are skipped (they are headings of
    some other document section, not stories).
    """
    stories: list[ParsedStory] = []
    current: ParsedStory | None = None
    current_field: str | None = None

    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            if current is not None and _has_star_content(current):
                stories.append(current)
            current = ParsedStory(
                title=heading.group("title").strip(),
                theme=(heading.group("theme") or "").strip(),
                origin=origin,
            )
            current_field = None
            continue
        if current is None:
            continue
        matched = _FIELD_RE.match(line)
        if matched:
            fld = _LABEL_TO_FIELD[matched.group("label").lower()]
            value = matched.group("value").strip()
            if fld == "tags":
                current.tags.extend(
                    t.strip() for t in value.split(",") if t.strip()
                )
                current_field = None
            elif fld == "source":
                current.source = value
                current_field = None
            else:
                setattr(current, fld, value)
                current_field = fld
            continue
        # Continuation line for a narrative field.
        if current_field and line.strip() and not line.startswith("#"):
            existing = getattr(current, current_field)
            setattr(
                current, current_field, f"{existing}\n{line.strip()}".strip()
            )

    if current is not None and _has_star_content(current):
        stories.append(current)
    return stories


def _has_star_content(story: ParsedStory) -> bool:
    return any(getattr(story, fld) for fld in _STAR_FIELDS)


# ── resolution against the vault ────────────────────────────────────


def _resolve_skill_ids(
    tags: list[str], vault: VaultSchema
) -> tuple[list[str], list[str]]:
    """Split tags into (resolved vault skill UUIDs, remaining theme tags).

    Resolution is exact-match only: a tag matches a vault skill by
    casefolded name, or via the taxonomy's canonical name/aliases when
    the vault skill is linked to that taxonomy entry. Never fuzzy,
    never invented.
    """
    by_name = {s.name.casefold(): s for s in vault.skills}
    by_taxonomy = {
        str(s.taxonomy_id): s for s in vault.skills if s.taxonomy_id is not None
    }
    skill_ids: list[str] = []
    themes: list[str] = []
    for tag in tags:
        skill = by_name.get(tag.casefold())
        if skill is None:
            entry = find_exact(tag)
            if entry is not None:
                skill = by_taxonomy.get(str(entry.id)) or by_name.get(
                    entry.name.casefold()
                )
        if skill is not None:
            if str(skill.id) not in skill_ids:
                skill_ids.append(str(skill.id))
        elif tag not in themes:
            themes.append(tag)
    return skill_ids, themes


def _resolve_experience_id(source: str, vault: VaultSchema) -> str | None:
    """Match a ``Source: Title — Company`` line to a vault experience."""
    if not source.strip():
        return None
    raw = source.strip()
    title, _, company = raw.partition("—")
    if not _:
        title, _, company = raw.partition(" - ")
    title = title.strip().casefold()
    company = company.strip().casefold()
    for exp in vault.experiences:
        if exp.title.casefold() == title and (
            not company or exp.company.casefold() == company
        ):
            return str(exp.id)
    # Title-only fallback when the line carried no separator.
    if not company:
        for exp in vault.experiences:
            if exp.title.casefold() == title:
                return str(exp.id)
    return None


def story_proposal_payload(
    parsed: ParsedStory, vault: VaultSchema
) -> dict[str, Any]:
    """Build the ``add_story`` proposal payload for one parsed block."""
    skill_ids, extra_themes = _resolve_skill_ids(parsed.tags, vault)
    theme_tags: list[str] = []
    for theme in [parsed.theme, *extra_themes]:
        if theme and theme not in theme_tags:
            theme_tags.append(theme)

    body_story = StorySchema(
        title=parsed.title,
        situation=parsed.situation,
        task=parsed.task,
        action=parsed.action,
        result=parsed.result,
        lesson=parsed.lesson,
    )
    payload: dict[str, Any] = {
        "title": parsed.title,
        "body": render_story_body(body_story),
    }
    if theme_tags:
        payload["theme_tags"] = theme_tags
    if skill_ids:
        payload["skill_ids"] = skill_ids
    experience_id = _resolve_experience_id(parsed.source, vault)
    if experience_id:
        payload["experience_id"] = experience_id
    return payload


# ── profile.yml ─────────────────────────────────────────────────────

#: profile.yml key -> JSON Resume basics key.
_PROFILE_KEY_MAP = (
    (("candidate", "name"), "name"),
    (("candidate", "email"), "email"),
    (("candidate", "location"), "location"),
    (("narrative", "headline"), "label"),
    (("narrative", "summary"), "summary"),
)


def profile_proposal(
    raw: str,
) -> tuple[dict[str, Any] | None, str]:
    """Map a profile.yml document onto an ``update_profile`` payload.

    Returns ``(payload | None, rationale)``. ``target_roles.archetypes``
    are positioning data, not profile facts — they land in a separate
    ``add_lens`` proposal via :func:`lens_proposal`, not stashed here.
    """
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None, ""
    if not isinstance(doc, dict):
        return None, ""

    basics: dict[str, Any] = {}
    for path, key in _PROFILE_KEY_MAP:
        node: Any = doc
        for part in path:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, str) and node.strip():
            basics[key] = node.strip()

    rationale = "Imported from config/profile.yml"
    if not basics:
        return None, rationale
    return {"basics": basics}, rationale


_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    """Kebab-case slug from free text (empty if nothing survives)."""
    return _SLUG_STRIP_RE.sub("-", text.strip().lower()).strip("-")


def _archetype_names(doc: Any) -> list[str]:
    """Pull ``target_roles.archetypes`` names (dict-with-name or bare string)."""
    if not isinstance(doc, dict):
        return []
    target_roles = doc.get("target_roles")
    if not isinstance(target_roles, dict):
        return []
    archetypes = target_roles.get("archetypes")
    names: list[str] = []
    if isinstance(archetypes, list):
        for item in archetypes:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.append(item["name"].strip())
            elif isinstance(item, str):
                names.append(item.strip())
    return [n for n in names if n]


def lens_proposal(raw: str) -> dict[str, Any] | None:
    """Build an ``add_lens`` payload from ``target_roles.archetypes``.

    The primary (first) archetype names the lens and derives its slug;
    every archetype is carried in ``target_archetypes`` so the reviewer
    can curate the positioning. Returns ``None`` when the document has no
    usable archetypes or no derivable slug. The lens carries no signature
    refs or salience — it is a positioning stub for the reviewer to flesh
    out, never asserting a fact absent from the vault.
    """
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None
    names = _archetype_names(doc)
    if not names:
        return None
    slug = _slugify(names[0])
    if not slug:
        return None
    return {
        "slug": slug,
        "name": names[0],
        "target_archetypes": names,
    }


# ── directory-level import ──────────────────────────────────────────


@dataclass(frozen=True)
class ImportPlan:
    """Everything found in a working dir, ready to stage."""

    stories: list[tuple[ParsedStory, dict[str, Any]]]
    profile_payload: dict[str, Any] | None
    profile_rationale: str
    lens_payload: dict[str, Any] | None
    has_cv: bool


def detect_and_plan(directory: Path, vault: VaultSchema) -> ImportPlan:
    """Detect the working-dir layout by shape and build the import plan.

    Raises ``ValueError`` when the directory holds neither a
    ``config/profile.yml`` nor any parseable ``interview-prep/*.md``
    story blocks.
    """
    profile_path = directory / "config" / "profile.yml"
    prep_dir = directory / "interview-prep"

    parsed_stories: list[ParsedStory] = []
    if prep_dir.is_dir():
        for md in sorted(prep_dir.glob("*.md")):
            parsed_stories.extend(
                parse_story_bank(
                    md.read_text(encoding="utf-8"), origin=md.name
                )
            )

    profile_payload: dict[str, Any] | None = None
    profile_rationale = ""
    lens_payload: dict[str, Any] | None = None
    if profile_path.is_file():
        raw = profile_path.read_text(encoding="utf-8")
        profile_payload, profile_rationale = profile_proposal(raw)
        lens_payload = lens_proposal(raw)

    if not parsed_stories and not profile_path.is_file():
        raise ValueError(
            f"{directory} does not look like a career working dir "
            "(expected config/profile.yml and/or interview-prep/*.md "
            "with '### Title' STAR blocks)."
        )

    stories = [(p, story_proposal_payload(p, vault)) for p in parsed_stories]
    has_cv = (directory / "cv.md").is_file()
    return ImportPlan(
        stories, profile_payload, profile_rationale, lens_payload, has_cv
    )


__all__ = [
    "IMPORT_SOURCE",
    "ImportPlan",
    "ParsedStory",
    "detect_and_plan",
    "lens_proposal",
    "parse_story_bank",
    "profile_proposal",
    "story_proposal_payload",
]
