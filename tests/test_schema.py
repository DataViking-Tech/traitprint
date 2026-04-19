"""Tests for vault schema validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from traitprint.schema import SkillSchema, VaultSchema


class TestEmptyVault:
    def test_empty_vault_validates(self) -> None:
        vault = VaultSchema(schema_version=0)
        assert vault.schema_version == 0
        assert vault.skills == []
        assert vault.experiences == []
        assert vault.stories == []
        assert vault.philosophies == []
        assert vault.education == []

    def test_empty_vault_has_empty_profile(self) -> None:
        vault = VaultSchema()
        assert vault.profile.display_name == ""
        assert vault.profile.headline == ""


class TestSkillValidation:
    def test_vault_with_one_skill(self) -> None:
        vault = VaultSchema(
            schema_version=0,
            skills=[
                SkillSchema(name="Python", proficiency=8, category="technical"),
            ],
        )
        assert len(vault.skills) == 1
        assert vault.skills[0].name == "Python"
        assert vault.skills[0].proficiency == 8

    def test_proficiency_min_valid(self) -> None:
        skill = SkillSchema(name="Test", proficiency=1)
        assert skill.proficiency == 1

    def test_proficiency_max_valid(self) -> None:
        skill = SkillSchema(name="Test", proficiency=10)
        assert skill.proficiency == 10

    def test_proficiency_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SkillSchema(name="Test", proficiency=0)

    def test_proficiency_eleven_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SkillSchema(name="Test", proficiency=11)

    def test_proficiency_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SkillSchema(name="Test", proficiency=-1)

    def test_skill_has_uuid(self) -> None:
        skill = SkillSchema(name="Python", proficiency=5)
        assert skill.id is not None

    def test_skill_has_timestamps(self) -> None:
        skill = SkillSchema(name="Python", proficiency=5)
        assert skill.created_at is not None
        assert skill.updated_at is not None
