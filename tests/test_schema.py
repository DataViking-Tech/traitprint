"""Tests for vault schema validation (v1)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from traitprint.schema import (
    ARTIFACT_LABEL_MAX,
    ARTIFACT_URL_MAX,
    MAX_ARTIFACT_LINKS,
    MAX_LENSES,
    SCOPE_TEXT_MAX,
    ArtifactLink,
    ExperienceSchema,
    ExperienceScope,
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


class TestExperienceScope:
    """Contract revision 1.5: the optional quantified role-scope block."""

    def test_scope_defaults_to_none(self) -> None:
        # Additive — pre-1.5 payloads omit the key and must keep validating.
        exp = ExperienceSchema(title="Staff Engineer")
        assert exp.scope is None

    def test_scope_round_trip(self) -> None:
        exp = ExperienceSchema.model_validate(
            {
                "title": "Head of Data",
                "scope": {
                    "reporting_line": "VP of Data",
                    "direct_reports": 6,
                    "indirect_reports": 24,
                    "managers_led": 2,
                    "functions_owned": ["analytics eng", "data platform"],
                    "budget_authority": "co-managed $2M vendor budget",
                    "hiring_authority": True,
                    "decision_rights": "architecture + tooling standards",
                    "platform_scale": "2,500+ dbt models, 30-person data org",
                    "org_context": "public co, ~7k employees",
                },
            }
        )
        assert exp.scope is not None
        assert exp.scope.direct_reports == 6
        assert ExperienceSchema.model_validate(exp.model_dump(mode="json")) == exp

    @pytest.mark.parametrize(
        "field", ["direct_reports", "indirect_reports", "managers_led"]
    )
    def test_negative_headcounts_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError):
            ExperienceScope.model_validate({field: -1})

    @pytest.mark.parametrize(
        "field", ["direct_reports", "indirect_reports", "managers_led"]
    )
    def test_zero_headcount_valid_and_serialized(self, field: str) -> None:
        # 0 is a set value (an IC role can truthfully claim zero reports).
        scope = ExperienceScope.model_validate({field: 0})
        assert scope.model_dump(mode="json") == {field: 0}

    @pytest.mark.parametrize(
        "field",
        [
            "reporting_line",
            "budget_authority",
            "decision_rights",
            "platform_scale",
            "org_context",
        ],
    )
    def test_short_text_length_cap(self, field: str) -> None:
        ExperienceScope.model_validate({field: "x" * SCOPE_TEXT_MAX})
        with pytest.raises(ValidationError):
            ExperienceScope.model_validate({field: "x" * (SCOPE_TEXT_MAX + 1)})

    def test_functions_owned_entry_length_cap(self) -> None:
        ExperienceScope(functions_owned=["x" * SCOPE_TEXT_MAX])
        with pytest.raises(ValidationError):
            ExperienceScope(functions_owned=["x" * (SCOPE_TEXT_MAX + 1)])

    def test_serializes_only_set_fields(self) -> None:
        scope = ExperienceScope(reporting_line="CTO", hiring_authority=False)
        # False is a set value; every unset field stays absent (no nulls).
        assert scope.model_dump(mode="json") == {
            "reporting_line": "CTO",
            "hiring_authority": False,
        }

    def test_absent_scope_omitted_from_experience_dump(self) -> None:
        # Additive revision 1.5: an experience without scope must serialize
        # exactly as before the field existed — no ``scope: null``.
        exp = ExperienceSchema(title="Staff Engineer")
        assert "scope" not in exp.model_dump(mode="json")

    def test_all_empty_scope_normalizes_to_none(self) -> None:
        exp = ExperienceSchema.model_validate({"title": "Eng", "scope": {}})
        assert exp.scope is None
        exp = ExperienceSchema(title="Eng", scope=ExperienceScope())
        assert exp.scope is None


class TestArtifactLink:
    """Contract revision 1.6: optional evidence links (provenance rung 1)."""

    def test_artifact_links_default_empty(self) -> None:
        # Additive — pre-1.6 payloads omit the key and must keep validating.
        assert ExperienceSchema(title="Staff Engineer").artifact_links == []
        assert StorySchema(title="T").artifact_links == []

    def test_full_and_partial_links_round_trip(self) -> None:
        for cls, kwargs in (
            (ExperienceSchema, {"title": "Head of Data"}),
            (StorySchema, {"title": "T"}),
        ):
            entity = cls.model_validate(
                {
                    **kwargs,
                    "artifact_links": [
                        {"url": "https://github.com/org/repo", "label": "repo"},
                        {"url": "https://example.com/talk"},
                    ],
                }
            )
            assert entity.artifact_links[0].label == "repo"
            assert entity.artifact_links[1].label is None
            assert cls.model_validate(entity.model_dump(mode="json")) == entity

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com",
            "ftp://example.com/file",
            "javascript:alert(1)",
            "//example.com",
            "example.com",
            "HTTPS://example.com",
        ],
    )
    def test_non_https_urls_rejected(self, url: str) -> None:
        with pytest.raises(ValidationError):
            ArtifactLink.model_validate({"url": url})

    def test_url_length_cap(self) -> None:
        base = "https://example.com/"
        ok = base + "x" * (ARTIFACT_URL_MAX - len(base))
        ArtifactLink(url=ok)
        with pytest.raises(ValidationError):
            ArtifactLink(url=ok + "x")

    def test_label_length_cap(self) -> None:
        url = "https://example.com"
        ArtifactLink(url=url, label="x" * ARTIFACT_LABEL_MAX)
        with pytest.raises(ValidationError):
            ArtifactLink(url=url, label="x" * (ARTIFACT_LABEL_MAX + 1))

    def test_max_links_per_entity(self) -> None:
        links = [
            {"url": f"https://example.com/{i}"} for i in range(MAX_ARTIFACT_LINKS)
        ]
        ExperienceSchema.model_validate({"title": "Eng", "artifact_links": links})
        StorySchema.model_validate({"title": "T", "artifact_links": links})
        over = [*links, {"url": "https://example.com/one-too-many"}]
        with pytest.raises(ValidationError):
            ExperienceSchema.model_validate(
                {"title": "Eng", "artifact_links": over}
            )
        with pytest.raises(ValidationError):
            StorySchema.model_validate({"title": "T", "artifact_links": over})

    def test_unknown_keys_rejected(self) -> None:
        # Matches the JSON schema's additionalProperties: false — a typo'd
        # ``label`` must fail loudly, not silently drop the label.
        with pytest.raises(ValidationError):
            ArtifactLink.model_validate(
                {"url": "https://example.com", "lable": "typo"}
            )
        with pytest.raises(ValidationError):
            ArtifactLink.model_validate(
                {"url": "https://example.com", "verified": True}
            )

    def test_scheme_only_url_rejected(self) -> None:
        # ``https://`` alone carries no destination; the JSON schema pins
        # the same floor via minLength.
        with pytest.raises(ValidationError):
            ArtifactLink.model_validate({"url": "https://"})

    def test_serializes_only_set_fields(self) -> None:
        # An unset label stays absent — never ``label: null``.
        link = ArtifactLink(url="https://example.com/talk")
        assert link.model_dump(mode="json") == {"url": "https://example.com/talk"}
        link = ArtifactLink(url="https://example.com/talk", label="talk")
        assert link.model_dump(mode="json") == {
            "url": "https://example.com/talk",
            "label": "talk",
        }


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
    """The documented 20-lens cap is enforced at vault load (MAX_LENSES)."""

    @staticmethod
    def _lenses(count: int) -> list[LensSchema]:
        return [LensSchema(slug=f"lens-{i}", name=f"Lens {i}") for i in range(count)]

    def test_max_lenses_is_twenty(self) -> None:
        assert MAX_LENSES == 20

    def test_exactly_max_lenses_validates(self) -> None:
        vault = VaultSchema(lenses=self._lenses(MAX_LENSES))
        assert len(vault.lenses) == MAX_LENSES

    def test_over_cap_rejected_at_load(self) -> None:
        with pytest.raises(ValidationError, match="at most 20 lenses"):
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
