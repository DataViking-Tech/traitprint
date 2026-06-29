"""Pydantic v2 models for the vault schema (v1).

These models are the canonical in-memory representation of the vault.
On disk the native format is the v1 file tree (see
``docs/schema/vault-v1/``); the legacy single-file ``vault.json`` (v0)
is still readable. :mod:`traitprint.vault_io` handles both
serializations of this model.

Schema-unification notes (v1):

- Proficiency is **1-5** (1 familiar, 2 working, 3 proficient, 4 expert,
  5 authority). v0 vaults used 1-10; the v0 reader remaps via
  ``ceil(x/2)`` in memory so downstream logic always sees 1-5, and the
  remap is persisted by ``traitprint vault migrate`` (or any write).
- Stories carry ``lesson``, ``outcome`` and ``theme_tags`` (from Cloud)
  in addition to the STAR fields and the Local cross-links.
- Philosophy ``category`` is optional (empty string allowed); the five
  enum values are the only non-empty options.
"""

from __future__ import annotations

import enum
import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


def _now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


class ProfileSchema(BaseModel):
    """User profile information."""

    display_name: str = ""
    headline: str = ""
    summary: str = ""
    location: str = ""
    contact_email: str = ""


class SkillSchema(BaseModel):
    """A single skill entry in the vault."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    taxonomy_id: UUID | None = None
    category: str = ""
    proficiency: int = Field(ge=1, le=5)
    source: str = "manual"
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class SkillLink(BaseModel):
    """A per-skill proficiency annotation on an experience.

    Contract revision 1.2 (additive). ``skill_id`` must reference a skill
    that is *also* present in the experience's ``skill_ids`` — that array
    stays authoritative for membership. A ``skill_links`` entry only
    annotates the proficiency demonstrated for that skill in this role;
    ``proficiency`` is optional (unset means "no annotation").
    """

    skill_id: UUID
    proficiency: int | None = Field(default=None, ge=1, le=5)


class ExperienceSchema(BaseModel):
    """A work experience entry.

    ``skill_ids`` (contract revision 1.1, additive) links the skills
    exercised in this role — same UUID-array reference style as story
    ``skill_ids``. Vaults written before 1.1 omit the key; it defaults
    to an empty list. Dangling references are a Layer 1 audit warning,
    never a parse error.

    ``skill_links`` (contract revision 1.2, additive) optionally annotates
    a per-skill ``proficiency`` for skills already listed in ``skill_ids``.
    ``skill_ids`` remains authoritative for membership: an entry whose
    ``skill_id`` is not in ``skill_ids`` carries no membership effect, and
    a skill in ``skill_ids`` with no matching link simply has unset
    proficiency. Vaults written before 1.2 omit the key; it defaults to an
    empty list and is not emitted into frontmatter when empty.
    """

    id: UUID = Field(default_factory=uuid4)
    title: str
    company: str = ""
    start_date: str = ""
    end_date: str = ""
    description: str = ""
    accomplishments: list[str] = Field(default_factory=list)
    skill_ids: list[UUID] = Field(default_factory=list)
    skill_links: list[SkillLink] = Field(default_factory=list)
    source: str = "manual"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# Valid story outcomes; empty string means "not classified".
STORY_OUTCOMES = ("", "win", "failure", "learning")


class StorySchema(BaseModel):
    """A STAR-format story entry."""

    id: UUID = Field(default_factory=uuid4)
    title: str
    situation: str = ""
    task: str = ""
    action: str = ""
    result: str = ""
    lesson: str = ""
    outcome: str = ""
    theme_tags: list[str] = Field(default_factory=list)
    skill_ids: list[UUID] = Field(default_factory=list)
    experience_id: UUID | None = None
    source: str = "manual"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("outcome")
    @classmethod
    def _validate_outcome(cls, value: str) -> str:
        if value not in STORY_OUTCOMES:
            raise ValueError(
                f"outcome must be one of {STORY_OUTCOMES!r}, got {value!r}"
            )
        return value


class PhilosophyCategory(str, enum.Enum):
    """Valid (non-empty) philosophy categories."""

    LEADERSHIP = "leadership"
    COLLABORATION = "collaboration"
    TECHNICAL_APPROACH = "technical-approach"
    CULTURE = "culture"
    DECISION_MAKING = "decision-making"


# "" (uncategorized) plus the five enum values.
PHILOSOPHY_CATEGORIES = ("", *(c.value for c in PhilosophyCategory))


class PhilosophySchema(BaseModel):
    """A work philosophy entry.

    ``category`` is optional (empty string by default); when set it must
    be one of the :class:`PhilosophyCategory` values.
    """

    id: UUID = Field(default_factory=uuid4)
    title: str
    description: str = ""
    category: str = ""
    evidence_story_ids: list[UUID] = Field(default_factory=list)
    source: str = "manual"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("category", mode="before")
    @classmethod
    def _validate_category(cls, value: object) -> str:
        if isinstance(value, PhilosophyCategory):
            return value.value
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError(f"category must be a string, got {type(value).__name__}")
        if value not in PHILOSOPHY_CATEGORIES:
            raise ValueError(
                f"category must be one of {PHILOSOPHY_CATEGORIES!r}, got {value!r}"
            )
        return value


class EducationSchema(BaseModel):
    """An education entry."""

    id: UUID = Field(default_factory=uuid4)
    institution: str
    degree: str = ""
    field_of_study: str = ""
    start_date: str = ""
    end_date: str = ""
    description: str = ""


class SalienceLevel(str, enum.Enum):
    """Per-skill emphasis within a lens (decision A: a 3-level enum for v1).

    ``core`` foregrounds a skill (top of the list, boosted in scoring);
    ``supporting`` is the default (normal order/weight); ``suppressed`` hides
    the skill from the rendered profile and drops it from the lensed skill set.
    """

    CORE = "core"
    SUPPORTING = "supporting"
    SUPPRESSED = "suppressed"


_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class LensSchema(BaseModel):
    """A named, non-destructive projection over the one vault (a *lens*).

    A lens only **selects, orders, and re-weights** existing vault content, plus
    optional ``headline``/``bio`` overrides — it never asserts a fact absent from
    the vault (the trust layer enforces this; see ``docs/schema/lens-v1/``).
    Omitting a lens everywhere preserves the canonical (un-lensed) rendering.
    """

    id: UUID = Field(default_factory=uuid4)
    slug: str
    name: str
    target_archetypes: list[str] = Field(default_factory=list)
    headline_override: str = ""
    bio_override: str = ""
    signature_experience_ids: list[UUID] = Field(default_factory=list)
    signature_story_ids: list[UUID] = Field(default_factory=list)
    # skill_id -> salience; unspecified skills are SUPPORTING (see salience_for).
    skill_salience: dict[UUID, SalienceLevel] = Field(default_factory=dict)
    # Exactly one lens may be default; the bare (un-lensed) profile renders it.
    is_default: bool = False
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, value: str) -> str:
        if not _SLUG_RE.match(value):
            raise ValueError(
                f"slug must be lowercase kebab-case ([a-z0-9-]), got {value!r}"
            )
        return value

    def salience_for(self, skill_id: UUID) -> SalienceLevel:
        """Salience of ``skill_id`` under this lens (default SUPPORTING)."""
        return self.skill_salience.get(skill_id, SalienceLevel.SUPPORTING)


class VaultSchema(BaseModel):
    """Top-level vault model (schema v1).

    The file tree (``traitprint.json`` + ``profile.json`` + JSON arrays +
    markdown files) is a storage serialization of this model; the legacy
    v0 ``vault.json`` was a direct JSON dump of it.
    """

    schema_version: int = 1
    vault_id: UUID = Field(default_factory=uuid4)
    updated_at: datetime = Field(default_factory=_now)
    profile: ProfileSchema = Field(default_factory=ProfileSchema)
    skills: list[SkillSchema] = Field(default_factory=list)
    experiences: list[ExperienceSchema] = Field(default_factory=list)
    stories: list[StorySchema] = Field(default_factory=list)
    philosophies: list[PhilosophySchema] = Field(default_factory=list)
    education: list[EducationSchema] = Field(default_factory=list)
    # Positioning lenses (emphasis-only projections; see LensSchema). Empty by
    # default, so an un-lensed vault renders exactly as before.
    lenses: list[LensSchema] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_lenses(self) -> VaultSchema:
        """Vault-level lens invariants: unique slugs and at most one default.

        A lens is resolved by slug (and the bare profile renders the default),
        so a duplicate slug would shadow other lenses and two defaults would make
        the bare rendering depend on list order. Enforce both at load time —
        ``lenses.json`` can be hand-edited or synced.
        """
        slugs = [lens.slug for lens in self.lenses]
        duplicates = sorted({s for s in slugs if slugs.count(s) > 1})
        if duplicates:
            raise ValueError(f"duplicate lens slug(s): {duplicates}")
        defaults = [lens.slug for lens in self.lenses if lens.is_default]
        if len(defaults) > 1:
            raise ValueError(
                f"at most one lens may be is_default, got {len(defaults)}: {defaults}"
            )
        return self
