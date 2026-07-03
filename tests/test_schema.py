"""Tests for vault schema validation (v1)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from traitprint.schema import (
    MAX_LENSES,
    ExperienceSchema,
    LensSchema,
    PhilosophyCategory,
    PhilosophySchema,
    ProfileLink,
    ProfileSchema,
    SkillLink,
    SkillSchema,
    StorySchema,
    VaultSchema,
)


class TestEmptyVault:
    def test_empty_vault_validates(self) -> None:
        vault = VaultSchema()
        assert vault.schema_version == 1
        assert isinstance(vault.vault_id, UUID)
        assert vault.skills == []
        assert vault.experiences == []
        assert vault.stories == []
        assert vault.philosophies == []
        assert vault.education == []

    def test_empty_vault_has_empty_profile(self) -> None:
        vault = VaultSchema()
        assert vault.profile.display_name == ""
        assert vault.profile.headline == ""

    def test_vault_ids_are_unique(self) -> None:
        assert VaultSchema().vault_id != VaultSchema().vault_id


class TestProfileSchema:
    def test_rev_1_3_fields_default_empty(self) -> None:
        profile = ProfileSchema()
        assert profile.phone == ""
        assert profile.url == ""
        assert profile.profiles == []

    def test_rev_1_3_fields_round_trip(self) -> None:
        profile = ProfileSchema(
            phone="+44 20 7946 0000",
            url="https://ada.example.com",
            profiles=[
                ProfileLink(
                    network="github",
                    username="ada",
                    url="https://github.com/ada",
                )
            ],
        )
        dumped = profile.model_dump(mode="json")
        assert ProfileSchema.model_validate(dumped) == profile

    def test_profiles_accept_plain_dicts(self) -> None:
        profile = ProfileSchema.model_validate(
            {"profiles": [{"network": "linkedin", "url": "https://l.example"}]}
        )
        assert profile.profiles[0].network == "linkedin"
        assert profile.profiles[0].username == ""

    def test_profile_link_username_and_url_optional(self) -> None:
        link = ProfileLink(network="mastodon")
        assert link.username == ""
        assert link.url == ""

    def test_profile_link_requires_network(self) -> None:
        with pytest.raises(ValidationError):
            ProfileLink.model_validate({"url": "https://example.com"})

    def test_profile_link_rejects_blank_network(self) -> None:
        with pytest.raises(ValidationError):
            ProfileLink(network="   ")


class TestSkillValidation:
    def test_vault_with_one_skill(self) -> None:
        vault = VaultSchema(
            skills=[
                SkillSchema(name="Python", proficiency=4, category="technical"),
            ],
        )
        assert len(vault.skills) == 1
        assert vault.skills[0].name == "Python"
        assert vault.skills[0].proficiency == 4

    def test_proficiency_min_valid(self) -> None:
        skill = SkillSchema(name="Test", proficiency=1)
        assert skill.proficiency == 1

    def test_proficiency_max_valid(self) -> None:
        skill = SkillSchema(name="Test", proficiency=5)
        assert skill.proficiency == 5

    def test_proficiency_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SkillSchema(name="Test", proficiency=0)

    def test_proficiency_six_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SkillSchema(name="Test", proficiency=6)

    def test_proficiency_ten_rejected(self) -> None:
        # The v0 scale topped at 10; v1 is 1-5 (migration maps ceil(x/2)).
        with pytest.raises(ValidationError):
            SkillSchema(name="Test", proficiency=10)

    def test_proficiency_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SkillSchema(name="Test", proficiency=-1)

    def test_skill_has_uuid(self) -> None:
        skill = SkillSchema(name="Python", proficiency=3)
        assert skill.id is not None

    def test_skill_has_timestamps(self) -> None:
        skill = SkillSchema(name="Python", proficiency=3)
        assert skill.created_at is not None
        assert skill.updated_at is not None


class TestExperienceSchema:
    def test_skill_ids_default_empty(self) -> None:
        # Contract revision 1.1 is additive — pre-1.1 payloads omit the
        # key and must keep validating.
        exp = ExperienceSchema(title="Staff Engineer")
        assert exp.skill_ids == []

    def test_skill_ids_round_trip(self) -> None:
        skill = SkillSchema(name="Python", proficiency=4)
        exp = ExperienceSchema(title="Staff Engineer", skill_ids=[skill.id])
        assert exp.skill_ids == [skill.id]

    def test_skill_ids_accept_uuid_strings(self) -> None:
        sid = uuid4()
        exp = ExperienceSchema.model_validate(
            {"title": "Eng", "skill_ids": [str(sid)]}
        )
        assert exp.skill_ids == [sid]

    def test_invalid_skill_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExperienceSchema(title="Eng", skill_ids=["not-a-uuid"])  # type: ignore[list-item]

    def test_skill_links_default_empty(self) -> None:
        # Contract revision 1.2 is additive — pre-1.2 payloads omit the
        # key and must keep validating.
        exp = ExperienceSchema(title="Staff Engineer")
        assert exp.skill_links == []

    def test_skill_links_round_trip(self) -> None:
        sid = uuid4()
        exp = ExperienceSchema.model_validate(
            {
                "title": "Eng",
                "skill_ids": [str(sid)],
                "skill_links": [{"skill_id": str(sid), "proficiency": 4}],
            }
        )
        assert exp.skill_links == [SkillLink(skill_id=sid, proficiency=4)]

    def test_skill_link_proficiency_optional(self) -> None:
        sid = uuid4()
        link = SkillLink.model_validate({"skill_id": str(sid)})
        assert link.proficiency is None

    @pytest.mark.parametrize("bad", [0, 6, -1, 10])
    def test_skill_link_proficiency_out_of_range_rejected(self, bad: int) -> None:
        with pytest.raises(ValidationError):
            SkillLink(skill_id=uuid4(), proficiency=bad)

    def test_skill_link_requires_skill_id(self) -> None:
        with pytest.raises(ValidationError):
            SkillLink.model_validate({"proficiency": 3})


class TestStorySchema:
    def test_new_fields_default_empty(self) -> None:
        story = StorySchema(title="T")
        assert story.lesson == ""
        assert story.outcome == ""
        assert story.theme_tags == []

    @pytest.mark.parametrize("outcome", ["", "win", "failure", "learning"])
    def test_valid_outcomes(self, outcome: str) -> None:
        assert StorySchema(title="T", outcome=outcome).outcome == outcome

    def test_invalid_outcome_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StorySchema(title="T", outcome="victory")

    def test_lesson_and_tags_round_trip(self) -> None:
        story = StorySchema(
            title="T", lesson="Always dual-write.", theme_tags=["migration", "cost"]
        )
        assert story.lesson == "Always dual-write."
        assert story.theme_tags == ["migration", "cost"]


class TestPhilosophyCategory:
    def test_category_optional(self) -> None:
        phil = PhilosophySchema(title="Ship small")
        assert phil.category == ""

    def test_enum_value_accepted(self) -> None:
        phil = PhilosophySchema(title="T", category="leadership")
        assert phil.category == "leadership"
        assert phil.category == PhilosophyCategory.LEADERSHIP

    def test_enum_instance_accepted(self) -> None:
        phil = PhilosophySchema(title="T", category=PhilosophyCategory.CULTURE)
        assert phil.category == "culture"

    def test_unknown_category_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PhilosophySchema(title="T", category="vibes")


class TestLensCap:
    """The documented 5-lens cap is enforced at vault load (MAX_LENSES)."""

    @staticmethod
    def _lenses(count: int) -> list[LensSchema]:
        return [LensSchema(slug=f"lens-{i}", name=f"Lens {i}") for i in range(count)]

    def test_max_lenses_is_five(self) -> None:
        assert MAX_LENSES == 5

    def test_exactly_max_lenses_validates(self) -> None:
        vault = VaultSchema(lenses=self._lenses(MAX_LENSES))
        assert len(vault.lenses) == MAX_LENSES

    def test_over_cap_rejected_at_load(self) -> None:
        with pytest.raises(ValidationError, match="at most 5 lenses"):
            VaultSchema(lenses=self._lenses(MAX_LENSES + 1))


class TestReservedLensSlug:
    """The 'none' slug is reserved as the canonical-rendering keyword (Q6)."""

    def test_none_slug_rejected(self) -> None:
        with pytest.raises(ValidationError, match="reserved"):
            LensSchema(slug="none", name="Canonical")

    def test_none_slug_rejected_in_vault(self) -> None:
        with pytest.raises(ValidationError, match="reserved"):
            VaultSchema(lenses=[{"slug": "none", "name": "X"}])

    def test_other_slugs_still_allowed(self) -> None:
        assert LensSchema(slug="none-of-your-business", name="X").slug == (
            "none-of-your-business"
        )
