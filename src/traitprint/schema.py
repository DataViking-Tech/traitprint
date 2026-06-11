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
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


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


class ExperienceSchema(BaseModel):
    """A work experience entry.

    ``skill_ids`` (contract revision 1.1, additive) links the skills
    exercised in this role — same UUID-array reference style as story
    ``skill_ids``. Vaults written before 1.1 omit the key; it defaults
    to an empty list. Dangling references are a Layer 1 audit warning,
    never a parse error.
    """

    id: UUID = Field(default_factory=uuid4)
    title: str
    company: str = ""
    start_date: str = ""
    end_date: str = ""
    description: str = ""
    accomplishments: list[str] = Field(default_factory=list)
    skill_ids: list[UUID] = Field(default_factory=list)
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
