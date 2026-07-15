"""Vault storage operations — load, save, create, and CRUD.

The native on-disk format is the v1 file tree (``traitprint.json`` +
``profile.json`` + JSON arrays + markdown files; see
``docs/schema/vault-v1/``). Legacy v0 single-file ``vault.json`` vaults
are still readable; any write emits v1. Serialization details live in
:mod:`traitprint.vault_io`.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from traitprint.schema import (
    MAX_LENSES,
    EducationSchema,
    ExperienceSchema,
    LensSchema,
    PhilosophyCategory,
    PhilosophySchema,
    ProfileLink,
    ProfileSchema,
    SalienceLevel,
    SkillLink,
    SkillSchema,
    StorySchema,
    VaultSchema,
)
from traitprint.vault_io import (
    MANIFEST_FILENAME,
    V0_FILENAME,
    read_vault_tree,
    read_vault_v0,
    write_vault_tree,
)

DEFAULT_VAULT_DIR = Path.home() / ".traitprint"
VAULT_DIR_ENV_VAR = "TRAITPRINT_VAULT_DIR"
LOCAL_VAULT_DIRNAME = ".traitprint"

# Valid top-level section names for CRUD operations.
SECTIONS = ("skills", "experiences", "stories", "philosophies", "education")


def _find_local_vault(start: Path) -> Path | None:
    """Walk up from ``start`` looking for a ``.traitprint/`` directory.

    Mirrors how git locates ``.git/``. Returns the discovered vault directory,
    or None if none exists between ``start`` and the filesystem root.
    """
    current = start.resolve()
    for candidate in (current, *current.parents):
        vault_dir = candidate / LOCAL_VAULT_DIRNAME
        if vault_dir.is_dir():
            return vault_dir
    return None


def resolve_vault_dir(
    explicit: str | Path | None = None,
    *,
    start: Path | None = None,
) -> Path:
    """Resolve the vault directory using explicit → env → walk-up → home.

    1. ``explicit`` (e.g. ``--vault-dir``) wins if provided.
    2. ``TRAITPRINT_VAULT_DIR`` env var is honored next.
    3. Walks up from ``start`` (default: cwd) looking for ``.traitprint/``.
    4. Falls back to ``~/.traitprint``.
    """
    if explicit is not None:
        return Path(explicit)

    env_value = os.environ.get(VAULT_DIR_ENV_VAR)
    if env_value:
        return Path(env_value)

    origin = start if start is not None else Path.cwd()
    local = _find_local_vault(origin)
    if local is not None:
        return local

    return DEFAULT_VAULT_DIR


class DuplicateSkillError(ValueError):
    """Raised when adding a skill whose name already exists in the vault."""

    def __init__(self, name: str, existing_id: UUID) -> None:
        super().__init__(f"Skill already exists: {name!r} ({existing_id})")
        self.name = name
        self.existing_id = existing_id


class LensNotFoundError(ValueError):
    """Raised when a lens ref (slug or id) does not resolve in the vault."""

    def __init__(self, ref: str) -> None:
        super().__init__(f"lens not found: {ref!r}")
        self.ref = ref


class DuplicateLensSlugError(ValueError):
    """Raised when a lens write would collide with an existing lens slug."""

    def __init__(self, slug: str, existing_id: UUID) -> None:
        super().__init__(f"lens slug already exists: {slug!r} ({existing_id})")
        self.slug = slug
        self.existing_id = existing_id


class LensCapError(ValueError):
    """Raised when adding a lens would exceed :data:`MAX_LENSES`."""

    def __init__(self) -> None:
        super().__init__(
            f"a vault may hold at most {MAX_LENSES} lenses; remove one first"
        )


class VaultStore:
    """Manages reading and writing the vault directory.

    Reads both formats (v1 file tree, legacy v0 ``vault.json``); writes
    emit the v1 file tree only.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.directory = resolve_vault_dir(path)

    @property
    def manifest_path(self) -> Path:
        """Path to the v1 manifest (traitprint.json)."""
        return self.directory / MANIFEST_FILENAME

    @property
    def vault_path(self) -> Path:
        """Path to the legacy v0 vault.json inside the vault directory."""
        return self.directory / V0_FILENAME

    def exists(self) -> bool:
        """Check whether a vault (v1 tree or v0 vault.json) exists."""
        return self.manifest_path.is_file() or self.vault_path.is_file()

    def is_v1(self) -> bool:
        """True when the vault is stored as a v1 file tree."""
        return self.manifest_path.is_file()

    def load(self) -> VaultSchema:
        """Read and validate the vault (v1 file tree, or legacy v0).

        A v0 vault has its proficiency values remapped 1-10 → 1-5
        (``ceil(x/2)``) in memory; see :func:`traitprint.vault_io.read_vault_v0`.
        """
        if self.manifest_path.is_file():
            return read_vault_tree(self.directory)
        return read_vault_v0(self.vault_path)

    def save(self, vault: VaultSchema, *, bump_updated_at: bool = True) -> None:
        """Write the vault as a v1 file tree (only changed files touched).

        When ``bump_updated_at`` is True (the default), ``vault.updated_at`` is
        refreshed to now. Pass False when persisting a vault received from the
        cloud so its server timestamp is preserved. Writing always emits v1;
        a legacy ``vault.json`` is removed so the two formats never coexist.
        """
        if bump_updated_at:
            vault.updated_at = datetime.now(timezone.utc)
        self.directory.mkdir(parents=True, exist_ok=True)
        write_vault_tree(self.directory, vault)
        if self.vault_path.is_file():
            self.vault_path.unlink()

    def create_empty(self) -> VaultSchema:
        """Return an empty vault (schema v1)."""
        return VaultSchema()

    # ------------------------------------------------------------------
    # CRUD helpers
    # ------------------------------------------------------------------

    def _save_and_commit(self, vault: VaultSchema, message: str) -> None:
        """Save the vault and auto-commit via git_ops."""
        from traitprint.git_ops import commit

        self.save(vault)
        commit(self.directory, message)

    def set_profile(
        self,
        *,
        display_name: str | None = None,
        headline: str | None = None,
        summary: str | None = None,
        location: str | None = None,
        contact_email: str | None = None,
        phone: str | None = None,
        url: str | None = None,
        profiles: list[ProfileLink] | None = None,
    ) -> ProfileSchema:
        """Update profile fields, save, and auto-commit.

        Only fields passed as non-None are updated; existing values are
        preserved for fields left as None. Pass an empty string to clear
        a field explicitly. ``profiles`` replaces the whole link list
        when passed (an empty list clears it).
        """
        vault = self.load()
        current = vault.profile.model_dump()
        updates: dict[str, Any] = {}
        for key, value in (
            ("display_name", display_name),
            ("headline", headline),
            ("summary", summary),
            ("location", location),
            ("contact_email", contact_email),
            ("phone", phone),
            ("url", url),
            ("profiles", profiles),
        ):
            if value is not None:
                updates[key] = value
        current.update(updates)
        vault.profile = ProfileSchema.model_validate(current)
        self._save_and_commit(vault, "Update profile")
        return vault.profile

    def add_skill(
        self,
        name: str,
        proficiency: int,
        category: str,
        notes: str | None = None,
        taxonomy_id: UUID | None = None,
    ) -> SkillSchema:
        """Add a skill to the vault, save, and auto-commit.

        Raises ``DuplicateSkillError`` if a skill with the same name (case-
        and whitespace-insensitive) already exists in the vault.
        """
        vault = self.load()
        normalized = name.strip().casefold()
        for existing in vault.skills:
            if existing.name.strip().casefold() == normalized:
                raise DuplicateSkillError(existing.name, existing.id)
        skill = SkillSchema(
            name=name,
            proficiency=proficiency,
            category=category,
            notes=notes or "",
            taxonomy_id=taxonomy_id,
        )
        vault.skills.append(skill)
        self._save_and_commit(vault, f"Add skill: {name} ({proficiency}/5)")
        return skill

    def add_experience(
        self,
        title: str,
        company: str,
        start_date: str,
        end_date: str | None = None,
        description: str = "",
        accomplishments: list[str] | None = None,
        skill_ids: list[UUID] | None = None,
        skill_links: list[SkillLink] | list[dict[str, Any]] | None = None,
    ) -> ExperienceSchema:
        """Add an experience to the vault, save, and auto-commit.

        ``skill_ids`` links the skills exercised in this role (contract
        revision 1.1); dangling references surface as audit warnings.

        ``skill_links`` (contract revision 1.2) optionally annotates a
        per-skill proficiency (1-5) for skills already in ``skill_ids``;
        ``skill_ids`` stays authoritative for membership. Entries whose
        ``skill_id`` is not in ``skill_ids`` are dropped here (they carry
        no membership effect and would only be dangling annotations).
        """
        vault = self.load()
        links = [
            sl if isinstance(sl, SkillLink) else SkillLink.model_validate(sl)
            for sl in (skill_links or [])
        ]
        ids = set(skill_ids or [])
        links = [sl for sl in links if sl.skill_id in ids]
        experience = ExperienceSchema(
            title=title,
            company=company,
            start_date=start_date,
            end_date=end_date or "",
            description=description,
            accomplishments=accomplishments or [],
            skill_ids=skill_ids or [],
            skill_links=links,
        )
        vault.experiences.append(experience)
        self._save_and_commit(vault, f"Add experience: {title} at {company}")
        return experience

    def add_story(
        self,
        title: str,
        situation: str,
        task: str,
        action: str,
        result: str,
        skill_ids: list[UUID] | None = None,
        experience_id: UUID | None = None,
        lesson: str = "",
        outcome: str = "",
        theme_tags: list[str] | None = None,
    ) -> StorySchema:
        """Add a STAR-format story to the vault, save, and auto-commit.

        ``outcome`` must be empty or one of ``win``/``failure``/``learning``
        (validated by the schema); ``lesson`` renders as a ``## Lesson``
        section in the story markdown.
        """
        vault = self.load()
        story = StorySchema(
            title=title,
            situation=situation,
            task=task,
            action=action,
            result=result,
            lesson=lesson,
            outcome=outcome,
            theme_tags=theme_tags or [],
            skill_ids=skill_ids or [],
            experience_id=experience_id,
        )
        vault.stories.append(story)
        self._save_and_commit(vault, f"Add story: {title}")
        return story

    def add_philosophy(
        self,
        title: str,
        description: str,
        category: PhilosophyCategory | str = "",
        evidence_story_ids: list[UUID] | None = None,
    ) -> PhilosophySchema:
        """Add a philosophy to the vault, save, and auto-commit.

        ``category`` is optional; when provided it must be empty or one
        of the :class:`PhilosophyCategory` values (validated by the schema).
        """
        vault = self.load()
        philosophy = PhilosophySchema(
            title=title,
            description=description,
            category=category,
            evidence_story_ids=evidence_story_ids or [],
        )
        vault.philosophies.append(philosophy)
        self._save_and_commit(vault, f"Add philosophy: {title}")
        return philosophy

    def add_education(
        self,
        institution: str,
        degree: str,
        field_of_study: str,
        start_date: str,
        end_date: str | None = None,
        description: str = "",
    ) -> EducationSchema:
        """Add an education entry to the vault, save, and auto-commit."""
        vault = self.load()
        edu = EducationSchema(
            institution=institution,
            degree=degree,
            field_of_study=field_of_study,
            start_date=start_date,
            end_date=end_date or "",
            description=description,
        )
        vault.education.append(edu)
        self._save_and_commit(vault, f"Add education: {degree} at {institution}")
        return edu

    def remove_item(self, section: str, item_id: UUID) -> bool:
        """Remove an item by UUID from the given section.

        Returns True if removed, False if not found.
        """
        if section not in SECTIONS:
            return False
        vault = self.load()
        items: list[object] = getattr(vault, section)
        for i, item in enumerate(items):
            if getattr(item, "id", None) == item_id:
                items.pop(i)
                self._save_and_commit(vault, f"Remove {section[:-1]}: {item_id}")
                return True
        return False

    # ------------------------------------------------------------------
    # Lens mutation surface (Q2: the `vault lens` group owns lens writes;
    # generic `vault remove`/SECTIONS deliberately excludes lenses).
    # ------------------------------------------------------------------

    @staticmethod
    def _find_lens(vault: VaultSchema, ref: str) -> LensSchema | None:
        """Resolve a lens by exact slug or full id string (no prefix sugar).

        CLI-only 8-hex-prefix resolution happens in the CLI layer before it
        reaches the store; the store keeps the strict slug/full-id grammar.
        """
        needle = ref.strip()
        for lens in vault.lenses:
            if lens.slug == needle or str(lens.id) == needle:
                return lens
        return None

    def add_lens(
        self,
        *,
        slug: str,
        name: str,
        target_archetypes: list[str] | None = None,
        headline_override: str = "",
        bio_override: str = "",
        signature_experience_ids: list[UUID] | None = None,
        signature_story_ids: list[UUID] | None = None,
        skill_salience: dict[UUID, SalienceLevel] | None = None,
        is_default: bool = False,
    ) -> LensSchema:
        """Create a lens, save, and auto-commit.

        Enforces the 20-lens cap and slug-uniqueness up front (the schema
        re-checks both at load). When ``is_default`` is set, any previous
        default is cleared so at most one lens is default.
        """
        vault = self.load()
        if len(vault.lenses) >= MAX_LENSES:
            raise LensCapError()
        existing = self._find_lens(vault, slug)
        if existing is not None and existing.slug == slug:
            raise DuplicateLensSlugError(slug, existing.id)
        lens = LensSchema(
            slug=slug,
            name=name,
            target_archetypes=target_archetypes or [],
            headline_override=headline_override,
            bio_override=bio_override,
            signature_experience_ids=signature_experience_ids or [],
            signature_story_ids=signature_story_ids or [],
            skill_salience=skill_salience or {},
            is_default=is_default,
        )
        if is_default:
            for other in vault.lenses:
                other.is_default = False
        vault.lenses.append(lens)
        self._save_and_commit(vault, f"Add lens: {name} ({slug})")
        return lens

    def update_lens(
        self,
        ref: str,
        *,
        slug: str | None = None,
        name: str | None = None,
        target_archetypes: list[str] | None = None,
        headline_override: str | None = None,
        bio_override: str | None = None,
        signature_experience_ids: list[UUID] | None = None,
        signature_story_ids: list[UUID] | None = None,
        skill_salience: dict[UUID, SalienceLevel] | None = None,
        is_default: bool | None = None,
    ) -> LensSchema:
        """Update an existing lens, save, and auto-commit.

        Only fields passed as non-None are changed. Renaming the slug
        re-checks uniqueness; setting ``is_default`` True clears the flag on
        every other lens.
        """
        vault = self.load()
        lens = self._find_lens(vault, ref)
        if lens is None:
            raise LensNotFoundError(ref)
        if slug is not None and slug != lens.slug:
            clash = self._find_lens(vault, slug)
            if clash is not None and clash.id != lens.id:
                raise DuplicateLensSlugError(slug, clash.id)
        updates: dict[str, Any] = {}
        for key, value in (
            ("slug", slug),
            ("name", name),
            ("target_archetypes", target_archetypes),
            ("headline_override", headline_override),
            ("bio_override", bio_override),
            ("signature_experience_ids", signature_experience_ids),
            ("signature_story_ids", signature_story_ids),
            ("skill_salience", skill_salience),
            ("is_default", is_default),
        ):
            if value is not None:
                updates[key] = value
        for field, value in updates.items():
            setattr(lens, field, value)
        lens.updated_at = datetime.now(timezone.utc)
        # Re-validate the field values (slug regex etc.) after mutation.
        lens = LensSchema.model_validate(lens.model_dump())
        for i, other in enumerate(vault.lenses):
            if other.id == lens.id:
                vault.lenses[i] = lens
        if is_default:
            for other in vault.lenses:
                if other.id != lens.id:
                    other.is_default = False
        self._save_and_commit(vault, f"Update lens: {lens.slug}")
        return lens

    def set_default_lens(self, ref: str) -> LensSchema:
        """Make ``ref`` the sole default lens, save, and auto-commit."""
        vault = self.load()
        lens = self._find_lens(vault, ref)
        if lens is None:
            raise LensNotFoundError(ref)
        for other in vault.lenses:
            other.is_default = other.id == lens.id
        self._save_and_commit(vault, f"Set default lens: {lens.slug}")
        return lens

    def remove_lens(self, ref: str) -> LensSchema | None:
        """Remove a lens by slug or id. Returns the removed lens or None.

        The generic ``vault remove`` excludes lenses (SECTIONS); the
        ``vault lens remove`` command owns lens deletion (Q2). When the last
        lens goes, ``lenses.json`` disappears (byte-identical invariant).
        """
        vault = self.load()
        lens = self._find_lens(vault, ref)
        if lens is None:
            return None
        vault.lenses = [x for x in vault.lenses if x.id != lens.id]
        self._save_and_commit(vault, f"Remove lens: {lens.slug}")
        return lens

    def import_from_draft(
        self,
        *,
        profile: ProfileSchema | dict[str, object] | None = None,
        skills: list[SkillSchema] | None = None,
        experiences: list[ExperienceSchema] | None = None,
        education: list[EducationSchema] | None = None,
        commit_message: str = "Import resume",
        update_profile: bool = True,
    ) -> dict[str, int]:
        """Bulk-append LLM-extracted items into the vault in a single commit.

        ``profile`` is merged field-by-field: non-empty incoming fields
        overwrite existing ones, empty strings are preserved. Skills,
        experiences, and education are appended — caller is responsible for
        deduplication.

        Returns a count dict like ``{"skills": 12, "experiences": 3}``.
        """
        vault = self.load()

        if profile is not None and update_profile:
            if isinstance(profile, ProfileSchema):
                incoming: dict[str, object] = profile.model_dump()
            else:
                incoming = dict(profile)
            current = vault.profile.model_dump()
            for key, value in incoming.items():
                if value:
                    current[key] = value
            vault.profile = ProfileSchema(**current)

        counts: dict[str, int] = {}
        if skills:
            vault.skills.extend(skills)
            counts["skills"] = len(skills)
        if experiences:
            vault.experiences.extend(experiences)
            counts["experiences"] = len(experiences)
        if education:
            vault.education.extend(education)
            counts["education"] = len(education)

        self._save_and_commit(vault, commit_message)
        return counts

    def get_item(
        self, section: str, item_id: UUID
    ) -> (
        SkillSchema
        | ExperienceSchema
        | StorySchema
        | PhilosophySchema
        | EducationSchema
        | None
    ):
        """Look up an item by UUID in the given section."""
        if section not in SECTIONS:
            return None
        vault = self.load()
        items: list[object] = getattr(vault, section)
        for item in items:
            if getattr(item, "id", None) == item_id:
                return item  # type: ignore[return-value]
        return None
