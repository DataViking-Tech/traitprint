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
from typing import Any
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)


def _now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


class ProfileLink(BaseModel):
    """One social/professional profile link (JSON Resume ``basics.profiles[]``).

    Contract revision 1.3 (additive). ``network`` identifies the site
    (e.g. ``linkedin``, ``github``); ``username`` and ``url`` are optional
    so entries can carry either or both, matching JSON Resume.
    """

    network: str
    username: str = ""
    url: str = ""

    @field_validator("network")
    @classmethod
    def _validate_network(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("network must be a non-empty string")
        return value


class ProfileSchema(BaseModel):
    """User profile information.

    ``phone``, ``url`` and ``profiles`` (contract revision 1.3, additive)
    follow the JSON Resume ``basics`` vocabulary: ``url`` is the personal
    website/portfolio, ``profiles`` are social links. Vaults written
    before 1.3 omit the keys; they default to empty and are not emitted
    into ``profile.json`` while empty.
    """

    display_name: str = ""
    headline: str = ""
    summary: str = ""
    location: str = ""
    contact_email: str = ""
    phone: str = ""
    url: str = ""
    profiles: list[ProfileLink] = Field(default_factory=list)


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


# Length cap for the scope block's short-text fields (and each
# ``functions_owned`` entry). Scope values are card-sized facts, not
# narrative — narrative belongs in the experience body.
SCOPE_TEXT_MAX = 200


class ExperienceScope(BaseModel):
    """The scope block of an experience (contract revision 1.5, additive).

    Quantified role scope for recruiter-grade calibration: reporting line,
    headcount, budget/hiring/decision authority, platform scale, and org
    context. Every field is optional; only set fields are serialized, and
    an all-empty scope normalizes to "no scope" (see
    :class:`ExperienceSchema`). Attestation-ready: each field is
    addressable as ``(experience_id, field_name)`` so future single-field
    attestations can attach without a schema migration.

    Integer headcounts are non-negative; short-text fields are capped at
    ``SCOPE_TEXT_MAX`` characters.
    """

    reporting_line: str | None = Field(default=None, max_length=SCOPE_TEXT_MAX)
    direct_reports: int | None = Field(default=None, ge=0)
    indirect_reports: int | None = Field(default=None, ge=0)
    managers_led: int | None = Field(default=None, ge=0)
    functions_owned: list[str] = Field(default_factory=list)
    budget_authority: str | None = Field(default=None, max_length=SCOPE_TEXT_MAX)
    hiring_authority: bool | None = None
    decision_rights: str | None = Field(default=None, max_length=SCOPE_TEXT_MAX)
    platform_scale: str | None = Field(default=None, max_length=SCOPE_TEXT_MAX)
    org_context: str | None = Field(default=None, max_length=SCOPE_TEXT_MAX)

    @field_validator("functions_owned")
    @classmethod
    def _validate_functions_owned(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) > SCOPE_TEXT_MAX:
                raise ValueError(
                    f"functions_owned entries must be at most "
                    f"{SCOPE_TEXT_MAX} characters"
                )
        return value

    @model_serializer(mode="wrap")
    def _serialize_set_fields_only(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        """Serialize only the set fields (never ``null``/empty-list keys).

        Keeps written scope objects minimal and stable: absent facts stay
        absent instead of accreting ``null`` entries, matching the
        omit-while-empty convention of the other additive revisions.
        ``False``/``0`` are set values and are kept.
        """
        data: dict[str, Any] = handler(self)
        return {k: v for k, v in data.items() if v is not None and v != []}


# Caps for artifact links (contract revision 1.6). Links are evidence
# pointers (repo, PR, talk, press, published artifact) — card-sized, not
# narrative, and never more than a handful per entity.
ARTIFACT_URL_MAX = 500
ARTIFACT_LABEL_MAX = 120
MAX_ARTIFACT_LINKS = 8


class ArtifactLink(BaseModel):
    """One evidence link on a story or experience (revision 1.6, additive).

    Provenance ladder rung 1 ("artifact-supported"): a URL pointing at a
    public artifact that evidences the parent entity — a repo, PR, talk,
    press mention, or published work. ``url`` must be ``https://`` (plain
    ``http`` and every other scheme are rejected) and is capped at
    ``ARTIFACT_URL_MAX`` characters; the optional ``label`` is capped at
    ``ARTIFACT_LABEL_MAX``. Only set fields are serialized: an unset
    label never appears as ``label: null``.
    """

    url: str = Field(min_length=len("https://") + 1, max_length=ARTIFACT_URL_MAX)
    label: str | None = Field(default=None, max_length=ARTIFACT_LABEL_MAX)

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError(
                "artifact link urls must use the https:// scheme "
                f"(got {value.split(':', 1)[0]!r})"
            )
        return value

    @model_serializer(mode="wrap")
    def _serialize_set_fields_only(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        """Serialize only the set fields (an unset label stays absent)."""
        data: dict[str, Any] = handler(self)
        return {k: v for k, v in data.items() if v is not None}


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

    ``scope`` (contract revision 1.5, additive) is the optional
    :class:`ExperienceScope` block — quantified role scope for
    recruiter-grade calibration. Vaults written before 1.5 omit the key;
    it defaults to ``None`` and an absent scope is never serialized (no
    ``null``/empty object on write), so pre-1.5 vaults round-trip
    byte-identically. A scope with no set fields normalizes to ``None``.

    ``artifact_links`` (contract revision 1.6, additive) carries up to
    ``MAX_ARTIFACT_LINKS`` :class:`ArtifactLink` evidence URLs for the
    role (provenance ladder rung 1). Vaults written before 1.6 omit the
    key; it defaults to an empty list and is not emitted into
    frontmatter when empty, so pre-1.6 vaults round-trip
    byte-identically.
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
    scope: ExperienceScope | None = None
    artifact_links: list[ArtifactLink] = Field(
        default_factory=list, max_length=MAX_ARTIFACT_LINKS
    )
    source: str = "manual"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("scope", mode="after")
    @classmethod
    def _collapse_empty_scope(
        cls, value: ExperienceScope | None
    ) -> ExperienceScope | None:
        """Normalize an all-empty scope to ``None`` (absent = omitted).

        A scope object with no set fields carries no information; keeping
        it would serialize as an empty mapping and break the byte-identical
        round trip (an omitted key must not reappear as ``scope: {}``).
        """
        if value is not None and value == ExperienceScope():
            return None
        return value

    @model_serializer(mode="wrap")
    def _omit_absent_scope(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        """Drop the ``scope`` key entirely when unset.

        Revision 1.5 is additive: an experience without scope must
        serialize exactly as it did before the field existed — omitted,
        never ``scope: null`` — wherever the model is dumped (frontmatter,
        JSON export, sync payloads).
        """
        data: dict[str, Any] = handler(self)
        if data.get("scope") is None:
            data.pop("scope", None)
        return data


# Valid story outcomes; empty string means "not classified".
STORY_OUTCOMES = ("", "win", "failure", "learning")


class StorySchema(BaseModel):
    """A STAR-format story entry.

    ``artifact_links`` (contract revision 1.6, additive) carries up to
    ``MAX_ARTIFACT_LINKS`` :class:`ArtifactLink` evidence URLs for the
    story (provenance ladder rung 1). Vaults written before 1.6 omit the
    key; it defaults to an empty list and is not emitted into frontmatter
    when empty, so pre-1.6 vaults round-trip byte-identically.
    """

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
    artifact_links: list[ArtifactLink] = Field(
        default_factory=list, max_length=MAX_ARTIFACT_LINKS
    )
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


# Maximum number of lenses a single vault may hold (PR #56 decision "E").
# Enforced at every write surface: vault load (below), proposal apply, cloud
# ingest, and commit-through. A lens is a curation object, not bulk data — the
# cap keeps the set small enough to reason about.
MAX_LENSES = 20

# Reserved lens slug: the canonical-rendering escape hatch. ``lens="none"`` on
# the read tools requests the un-lensed (canonical) rendering even when a default
# lens exists, so no user lens may claim it (Q6).
RESERVED_LENS_SLUG = "none"

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
        if value == RESERVED_LENS_SLUG:
            raise ValueError(
                f"slug {RESERVED_LENS_SLUG!r} is reserved (canonical-rendering keyword)"
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
        ``lenses.json`` can be hand-edited or synced. The 20-lens cap
        (``MAX_LENSES``) is enforced here too, so a vault carrying too many
        lenses fails to load rather than silently rendering an over-cap set.
        """
        if len(self.lenses) > MAX_LENSES:
            raise ValueError(
                f"a vault may hold at most {MAX_LENSES} lenses, got {len(self.lenses)}"
            )
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
