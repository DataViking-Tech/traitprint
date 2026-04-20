"""Vault storage operations — load, save, create, and CRUD."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from traitprint.schema import (
    EducationSchema,
    ExperienceSchema,
    PhilosophyCategory,
    PhilosophySchema,
    ProfileSchema,
    SkillSchema,
    StorySchema,
    VaultSchema,
)

DEFAULT_VAULT_DIR = Path.home() / ".traitprint"

# Valid top-level section names for CRUD operations.
SECTIONS = ("skills", "experiences", "stories", "philosophies", "education")


class VaultStore:
    """Manages reading and writing the vault.json file."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.directory = Path(path) if path else DEFAULT_VAULT_DIR

    @property
    def vault_path(self) -> Path:
        """Path to vault.json inside the vault directory."""
        return self.directory / "vault.json"

    def exists(self) -> bool:
        """Check whether vault.json exists."""
        return self.vault_path.is_file()

    def load(self) -> VaultSchema:
        """Read and validate vault.json."""
        raw = self.vault_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return VaultSchema.model_validate(data)

    def save(self, vault: VaultSchema, *, bump_updated_at: bool = True) -> None:
        """Write vault to disk with pretty formatting.

        When ``bump_updated_at`` is True (the default), ``vault.updated_at`` is
        refreshed to now. Pass False when persisting a vault received from the
        cloud so its server timestamp is preserved.
        """
        if bump_updated_at:
            vault.updated_at = datetime.now(timezone.utc)
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = vault.model_dump(mode="json")
        self.vault_path.write_text(
            json.dumps(payload, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    def create_empty(self) -> VaultSchema:
        """Return an empty vault with schema_version=0."""
        return VaultSchema(schema_version=0)

    # ------------------------------------------------------------------
    # CRUD helpers
    # ------------------------------------------------------------------

    def _save_and_commit(self, vault: VaultSchema, message: str) -> None:
        """Save the vault and auto-commit via git_ops."""
        from traitprint.git_ops import commit

        self.save(vault)
        commit(self.directory, message)

    def add_skill(
        self,
        name: str,
        proficiency: int,
        category: str,
        notes: str | None = None,
        taxonomy_id: UUID | None = None,
    ) -> SkillSchema:
        """Add a skill to the vault, save, and auto-commit."""
        vault = self.load()
        skill = SkillSchema(
            name=name,
            proficiency=proficiency,
            category=category,
            notes=notes or "",
            taxonomy_id=taxonomy_id,
        )
        vault.skills.append(skill)
        self._save_and_commit(vault, f"Add skill: {name} ({proficiency}/10)")
        return skill

    def add_experience(
        self,
        title: str,
        company: str,
        start_date: str,
        end_date: str | None = None,
        description: str = "",
        accomplishments: list[str] | None = None,
    ) -> ExperienceSchema:
        """Add an experience to the vault, save, and auto-commit."""
        vault = self.load()
        experience = ExperienceSchema(
            title=title,
            company=company,
            start_date=start_date,
            end_date=end_date or "",
            description=description,
            accomplishments=accomplishments or [],
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
    ) -> StorySchema:
        """Add a STAR-format story to the vault, save, and auto-commit."""
        vault = self.load()
        story = StorySchema(
            title=title,
            situation=situation,
            task=task,
            action=action,
            result=result,
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
        category: PhilosophyCategory | str,
        evidence_story_ids: list[UUID] | None = None,
    ) -> PhilosophySchema:
        """Add a philosophy to the vault, save, and auto-commit."""
        vault = self.load()
        if isinstance(category, str):
            category = PhilosophyCategory(category)
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
