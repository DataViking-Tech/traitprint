"""Vault storage serialization: the v1 file tree (read+write) and the v0 reader.

The v1 format (contract: ``docs/schema/vault-v1/``) is a directory of
plain files versioned by git:

- ``traitprint.json`` — manifest (schema_version, vault_id, updated_at)
- ``profile.json`` — identity block, JSON Resume-compatible ``basics`` keys
- ``skills.json`` / ``education.json`` — JSON arrays
- ``experiences/*.md`` / ``stories/*.md`` / ``philosophies/*.md`` —
  YAML frontmatter + markdown body

Identity lives in the frontmatter/JSON ``id`` (UUID); filenames are
kebab-case slugs and stay stable across renames (an entity that already
has a file keeps its filename even when the title changes).

The legacy v0 format is a single ``vault.json``. The v0 reader remaps
proficiency from the old 1-10 scale to 1-5 via ``ceil(x/2)`` **in
memory** on every load so downstream logic always sees 1-5; the remap
is only persisted when the vault is written (``traitprint vault
migrate`` or any mutating command).
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml

from traitprint.schema import (
    EducationSchema,
    ExperienceSchema,
    PhilosophySchema,
    ProfileSchema,
    SkillSchema,
    StorySchema,
    VaultSchema,
)

MANIFEST_FILENAME = "traitprint.json"
V0_FILENAME = "vault.json"
PROFILE_FILENAME = "profile.json"
SKILLS_FILENAME = "skills.json"
EDUCATION_FILENAME = "education.json"
EXPERIENCES_DIR = "experiences"
STORIES_DIR = "stories"
PHILOSOPHIES_DIR = "philosophies"

# Frontmatter keys allowed by the contract's $defs (additionalProperties
# is false). Narrative text lives in the markdown body, never here.
EXPERIENCE_FRONTMATTER_KEYS = (
    "id",
    "title",
    "company",
    "start_date",
    "end_date",
    "accomplishments",
    "source",
    "created_at",
    "updated_at",
)
STORY_FRONTMATTER_KEYS = (
    "id",
    "title",
    "skill_ids",
    "experience_id",
    "outcome",
    "theme_tags",
    "source",
    "created_at",
    "updated_at",
)
PHILOSOPHY_FRONTMATTER_KEYS = (
    "id",
    "title",
    "category",
    "evidence_story_ids",
    "source",
    "created_at",
    "updated_at",
)

_STAR_HEADINGS = ("Situation", "Task", "Action", "Result")
_STORY_HEADINGS = (*_STAR_HEADINGS, "Lesson")


class VaultFormatError(ValueError):
    """Raised when a vault file cannot be parsed."""


def remap_proficiency(value: int) -> int:
    """Map a v0 proficiency (1-10) onto the v1 1-5 scale via ``ceil(x/2)``."""
    return max(1, min(5, (int(value) + 1) // 2))


# ── slugs ───────────────────────────────────────────────────────────


def slugify(text: str) -> str:
    """Kebab-case slug for filenames; never empty."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


# ── markdown (frontmatter + body) ──────────────────────────────────


def render_markdown(frontmatter: dict[str, Any], body: str) -> str:
    """Render a YAML-frontmatter markdown document."""
    fm = yaml.safe_dump(
        frontmatter, sort_keys=False, default_flow_style=False, allow_unicode=True
    )
    doc = f"---\n{fm}---\n"
    if body.strip():
        doc += f"\n{body.strip()}\n"
    return doc


def parse_markdown(
    text: str, *, path: Path | None = None
) -> tuple[dict[str, Any], str]:
    """Split a markdown document into (frontmatter dict, body)."""
    where = f" in {path}" if path else ""
    if not text.startswith("---\n"):
        raise VaultFormatError(f"Missing YAML frontmatter{where}")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise VaultFormatError(f"Unterminated YAML frontmatter{where}")
    raw_fm = text[4:end]
    body = text[end + len("\n---\n") :]
    try:
        fm = yaml.safe_load(raw_fm)
    except yaml.YAMLError as exc:
        raise VaultFormatError(f"Invalid YAML frontmatter{where}: {exc}") from exc
    if not isinstance(fm, dict):
        raise VaultFormatError(f"Frontmatter must be a mapping{where}")
    return {k: _normalize_yaml_value(v) for k, v in fm.items()}, body.strip()


def _normalize_yaml_value(value: Any) -> Any:
    """Coerce YAML-native dates back to ISO strings.

    Hand-edited files may carry unquoted dates (``start_date: 2020-01-15``)
    that PyYAML loads as ``datetime.date``/``datetime.datetime``; the
    schema's date fields are plain strings, so normalize here. Pydantic
    parses the ISO strings for the real timestamp fields either way.
    """
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, list):
        return [_normalize_yaml_value(v) for v in value]
    return value


def render_story_body(story: StorySchema) -> str:
    """Render STAR fields (+ optional Lesson) as ``##`` sections."""
    sections = [
        ("Situation", story.situation),
        ("Task", story.task),
        ("Action", story.action),
        ("Result", story.result),
    ]
    if story.lesson.strip():
        sections.append(("Lesson", story.lesson))
    parts: list[str] = []
    for heading, content in sections:
        parts.append(f"## {heading}")
        if content.strip():
            parts.append(content.strip())
    return "\n\n".join(parts)


def parse_story_body(body: str, *, path: Path | None = None) -> dict[str, str]:
    """Parse ``## Situation/Task/Action/Result`` (+ optional ``## Lesson``).

    Returns a dict with lowercase field names. Missing headings yield
    empty strings (the coherence audit flags them; parsing stays lenient).
    """
    fields = {h.lower(): "" for h in _STORY_HEADINGS}
    current: str | None = None
    chunks: dict[str, list[str]] = {h.lower(): [] for h in _STORY_HEADINGS}
    for line in body.splitlines():
        match = re.match(r"^##\s+(\w+)\s*$", line)
        if match and match.group(1).capitalize() in _STORY_HEADINGS:
            current = match.group(1).lower()
            continue
        if current is not None:
            chunks[current].append(line)
    for key, lines in chunks.items():
        fields[key] = "\n".join(lines).strip()
    return fields


# ── building the file tree ─────────────────────────────────────────


def _json_doc(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _profile_doc(profile: ProfileSchema) -> str:
    return _json_doc(
        {
            "basics": {
                "name": profile.display_name,
                "label": profile.headline,
                "summary": profile.summary,
                "email": profile.contact_email,
                "location": profile.location,
            }
        }
    )


def _frontmatter(item: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    dump = item.model_dump(mode="json")
    return {k: dump[k] for k in keys}


def _scan_markdown_dir(dirpath: Path) -> dict[UUID, Path]:
    """Map frontmatter ``id`` → file for every parseable ``*.md`` in a dir."""
    found: dict[UUID, Path] = {}
    if not dirpath.is_dir():
        return found
    for file in sorted(dirpath.glob("*.md")):
        try:
            fm, _ = parse_markdown(file.read_text(encoding="utf-8"), path=file)
            found[UUID(str(fm.get("id")))] = file
        except (VaultFormatError, ValueError, TypeError, OSError):
            continue
    return found


def _assign_filenames(
    items: list[Any],
    slugs: dict[UUID, str],
    existing: dict[UUID, Path],
) -> dict[UUID, str]:
    """Assign a filename to each item.

    Items that already have a file (matched by frontmatter id) keep
    their filename — even if the title changed — so renames never churn
    git history. New items get ``<slug>.md``; on collision the first 8
    hex chars of the id are appended.
    """
    names: dict[UUID, str] = {}
    used: set[str] = set()
    pending: list[Any] = []
    for item in items:
        path = existing.get(item.id)
        if path is not None:
            names[item.id] = path.name
            used.add(path.name)
        else:
            pending.append(item)
    for item in pending:
        base = slugs[item.id]
        candidate = f"{base}.md"
        if candidate in used:
            candidate = f"{base}-{item.id.hex[:8]}.md"
        names[item.id] = candidate
        used.add(candidate)
    return names


def build_tree(vault: VaultSchema, directory: Path) -> dict[str, str]:
    """Compute the full v1 file tree as ``{relative_path: content}``.

    Filenames for markdown entities are stabilized against what already
    exists in ``directory`` (matching by frontmatter id).
    """
    tree: dict[str, str] = {}

    tree[MANIFEST_FILENAME] = _json_doc(
        {
            "schema_version": 1,
            "vault_id": str(vault.vault_id),
            "updated_at": vault.updated_at.isoformat().replace("+00:00", "Z"),
        }
    )
    tree[PROFILE_FILENAME] = _profile_doc(vault.profile)
    tree[SKILLS_FILENAME] = _json_doc(
        [s.model_dump(mode="json") for s in vault.skills]
    )
    tree[EDUCATION_FILENAME] = _json_doc(
        [e.model_dump(mode="json") for e in vault.education]
    )

    # experiences/*.md — slug from title + company; body = description.
    exp_existing = _scan_markdown_dir(directory / EXPERIENCES_DIR)
    exp_slugs = {
        e.id: slugify(f"{e.title} {e.company}".strip()) for e in vault.experiences
    }
    exp_names = _assign_filenames(list(vault.experiences), exp_slugs, exp_existing)
    for exp in vault.experiences:
        fm = _frontmatter(exp, EXPERIENCE_FRONTMATTER_KEYS)
        tree[f"{EXPERIENCES_DIR}/{exp_names[exp.id]}"] = render_markdown(
            fm, exp.description
        )

    # stories/*.md — body = STAR sections (+ optional Lesson).
    story_existing = _scan_markdown_dir(directory / STORIES_DIR)
    story_slugs = {s.id: slugify(s.title) for s in vault.stories}
    story_names = _assign_filenames(list(vault.stories), story_slugs, story_existing)
    for story in vault.stories:
        fm = _frontmatter(story, STORY_FRONTMATTER_KEYS)
        tree[f"{STORIES_DIR}/{story_names[story.id]}"] = render_markdown(
            fm, render_story_body(story)
        )

    # philosophies/*.md — body = the stance (description).
    phil_existing = _scan_markdown_dir(directory / PHILOSOPHIES_DIR)
    phil_slugs = {p.id: slugify(p.title) for p in vault.philosophies}
    phil_names = _assign_filenames(
        list(vault.philosophies), phil_slugs, phil_existing
    )
    for phil in vault.philosophies:
        fm = _frontmatter(phil, PHILOSOPHY_FRONTMATTER_KEYS)
        tree[f"{PHILOSOPHIES_DIR}/{phil_names[phil.id]}"] = render_markdown(
            fm, phil.description
        )

    return tree


def write_vault_tree(directory: Path, vault: VaultSchema) -> None:
    """Write the v1 file tree, touching only what changed.

    Files for entities that no longer exist are deleted; unchanged files
    are left alone so git diffs stay minimal.
    """
    vault.schema_version = 1
    tree = build_tree(vault, directory)

    directory.mkdir(parents=True, exist_ok=True)
    for subdir in (EXPERIENCES_DIR, STORIES_DIR, PHILOSOPHIES_DIR):
        (directory / subdir).mkdir(exist_ok=True)

    # Delete orphaned markdown files (entities removed from the vault).
    wanted = set(tree)
    for subdir in (EXPERIENCES_DIR, STORIES_DIR, PHILOSOPHIES_DIR):
        dirpath = directory / subdir
        for file in sorted(dirpath.glob("*.md")):
            if f"{subdir}/{file.name}" not in wanted:
                file.unlink()

    for rel_path, content in tree.items():
        target = directory / rel_path
        if target.is_file() and target.read_text(encoding="utf-8") == content:
            continue
        target.write_text(content, encoding="utf-8")


# ── reading ────────────────────────────────────────────────────────


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VaultFormatError(f"Invalid JSON in {path}: {exc}") from exc


def _read_markdown_items(dirpath: Path) -> list[tuple[dict[str, Any], str, Path]]:
    """Read every ``*.md`` in a dir as (frontmatter, body, path), sorted by name."""
    items: list[tuple[dict[str, Any], str, Path]] = []
    if not dirpath.is_dir():
        return items
    for file in sorted(dirpath.glob("*.md")):
        fm, body = parse_markdown(file.read_text(encoding="utf-8"), path=file)
        items.append((fm, body, file))
    return items


def _sort_key(item: Any) -> tuple[str, str]:
    created = getattr(item, "created_at", None)
    return (created.isoformat() if created is not None else "", str(item.id))


def read_vault_tree(directory: Path) -> VaultSchema:
    """Read a v1 file tree into the canonical :class:`VaultSchema`."""
    manifest = _read_json(directory / MANIFEST_FILENAME)
    if not isinstance(manifest, dict):
        raise VaultFormatError(f"Manifest must be a JSON object in {directory}")

    profile = ProfileSchema()
    profile_path = directory / PROFILE_FILENAME
    if profile_path.is_file():
        raw = _read_json(profile_path)
        basics = raw.get("basics") or {} if isinstance(raw, dict) else {}
        profile = ProfileSchema(
            display_name=str(basics.get("name") or ""),
            headline=str(basics.get("label") or ""),
            summary=str(basics.get("summary") or ""),
            contact_email=str(basics.get("email") or ""),
            location=str(basics.get("location") or ""),
        )

    skills: list[SkillSchema] = []
    skills_path = directory / SKILLS_FILENAME
    if skills_path.is_file():
        skills = [SkillSchema.model_validate(s) for s in _read_json(skills_path)]

    education: list[EducationSchema] = []
    education_path = directory / EDUCATION_FILENAME
    if education_path.is_file():
        education = [
            EducationSchema.model_validate(e) for e in _read_json(education_path)
        ]

    experiences: list[ExperienceSchema] = []
    for fm, body, _path in _read_markdown_items(directory / EXPERIENCES_DIR):
        experiences.append(ExperienceSchema.model_validate({**fm, "description": body}))

    stories: list[StorySchema] = []
    for fm, body, path in _read_markdown_items(directory / STORIES_DIR):
        star = parse_story_body(body, path=path)
        stories.append(StorySchema.model_validate({**fm, **star}))

    philosophies: list[PhilosophySchema] = []
    for fm, body, _path in _read_markdown_items(directory / PHILOSOPHIES_DIR):
        philosophies.append(
            PhilosophySchema.model_validate({**fm, "description": body})
        )

    # Order markdown-backed sections by creation time (then id) so a
    # save→load round-trip preserves the in-memory ordering.
    experiences.sort(key=_sort_key)
    stories.sort(key=_sort_key)
    philosophies.sort(key=_sort_key)

    return VaultSchema.model_validate(
        {
            "schema_version": manifest.get("schema_version", 1),
            "vault_id": manifest.get("vault_id"),
            "updated_at": manifest.get("updated_at"),
            "profile": profile,
            "skills": skills,
            "experiences": experiences,
            "stories": stories,
            "philosophies": philosophies,
            "education": education,
        }
    )


def read_vault_v0(path: Path) -> VaultSchema:
    """Read a legacy v0 ``vault.json``.

    Proficiency values are remapped from the v0 1-10 scale to 1-5 via
    ``ceil(x/2)`` **in memory** so all downstream 1-5 logic works on a
    read-only v0 vault. The remap reaches disk only when the vault is
    written (which always emits v1) — ``traitprint vault migrate`` is
    the explicit way to do that.
    """
    data = _read_json(path)
    if not isinstance(data, dict):
        raise VaultFormatError(f"Vault must be a JSON object in {path}")
    return validate_vault_payload(data)


def validate_vault_payload(data: Any) -> VaultSchema:
    """Validate a raw vault document, remapping v0 proficiencies first.

    Applies the same in-memory ``ceil(x/2)`` remap as ``read_vault_v0``
    when the document declares ``schema_version: 0``. Cloud payloads
    uploaded by <=0.6 clients are exactly such documents, so every
    consumer of a raw vault dict (local v0 file, cloud pull) must come
    through here rather than ``VaultSchema.model_validate`` directly.
    """
    if isinstance(data, dict) and int(data.get("schema_version", 0)) == 0:
        for skill in data.get("skills") or []:
            if isinstance(skill, dict) and isinstance(skill.get("proficiency"), int):
                skill["proficiency"] = remap_proficiency(skill["proficiency"])
    return VaultSchema.model_validate(data)


__all__ = [
    "EDUCATION_FILENAME",
    "EXPERIENCES_DIR",
    "EXPERIENCE_FRONTMATTER_KEYS",
    "MANIFEST_FILENAME",
    "PHILOSOPHIES_DIR",
    "PHILOSOPHY_FRONTMATTER_KEYS",
    "PROFILE_FILENAME",
    "SKILLS_FILENAME",
    "STORIES_DIR",
    "STORY_FRONTMATTER_KEYS",
    "V0_FILENAME",
    "VaultFormatError",
    "build_tree",
    "parse_markdown",
    "parse_story_body",
    "read_vault_tree",
    "read_vault_v0",
    "remap_proficiency",
    "validate_vault_payload",
    "render_markdown",
    "render_story_body",
    "slugify",
    "write_vault_tree",
]
