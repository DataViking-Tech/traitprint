"""Pydantic v2 models for the vault.json v0 schema."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


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
    proficiency: int = Field(ge=1, le=10)
    source: str = "manual"
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ExperienceSchema(BaseModel):
    """A work experience entry."""

    id: UUID = Field(default_factory=uuid4)
    title: str
    company: str = ""
    start_date: str = ""
    end_date: str = ""
    description: str = ""
    accomplishments: list[str] = Field(default_factory=list)
    source: str = "manual"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class StorySchema(BaseModel):
    """A STAR-format story entry."""

    id: UUID = Field(default_factory=uuid4)
    title: str
    situation: str = ""
    task: str = ""
    action: str = ""
    result: str = ""
    skill_ids: list[UUID] = Field(default_factory=list)
    experience_id: UUID | None = None
    source: str = "manual"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class PhilosophyCategory(str, enum.Enum):
    """Valid philosophy categories."""

    LEADERSHIP = "leadership"
    COLLABORATION = "collaboration"
    TECHNICAL_APPROACH = "technical-approach"
    CULTURE = "culture"
    DECISION_MAKING = "decision-making"


class PhilosophySchema(BaseModel):
    """A work philosophy entry."""

    id: UUID = Field(default_factory=uuid4)
    title: str
    description: str = ""
    category: PhilosophyCategory
    evidence_story_ids: list[UUID] = Field(default_factory=list)
    source: str = "manual"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


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
    """Top-level vault.json schema (v0)."""

    schema_version: int = 0
    profile: ProfileSchema = Field(default_factory=ProfileSchema)
    skills: list[SkillSchema] = Field(default_factory=list)
    experiences: list[ExperienceSchema] = Field(default_factory=list)
    stories: list[StorySchema] = Field(default_factory=list)
    philosophies: list[PhilosophySchema] = Field(default_factory=list)
    education: list[EducationSchema] = Field(default_factory=list)
