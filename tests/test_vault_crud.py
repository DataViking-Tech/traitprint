"""Tests for Slice B — Vault CRUD operations."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from traitprint.cli import cli
from traitprint.git_ops import commit, init_repo
from traitprint.schema import (
    MAX_LENSES,
    PhilosophyCategory,
    ProfileLink,
    SalienceLevel,
    SkillSchema,
)
from traitprint.taxonomy import find_exact, suggest_matches
from traitprint.vault import (
    DuplicateLensSlugError,
    DuplicateSkillError,
    LensCapError,
    LensNotFoundError,
    VaultStore,
)

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    """Create an initialized vault directory for testing."""
    d = tmp_path / "vault"
    d.mkdir()
    init_repo(d)
    store = VaultStore(d)
    vault = store.create_empty()
    store.save(vault)
    commit(d, "test init")
    return d


@pytest.fixture()
def store(vault_dir: Path) -> VaultStore:
    """Return a VaultStore pointing at the test vault."""
    return VaultStore(vault_dir)


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


# ------------------------------------------------------------------
# VaultStore.add_skill
# ------------------------------------------------------------------


class TestAddSkill:
    def test_creates_skill_with_correct_fields(self, store: VaultStore) -> None:
        skill = store.add_skill(
            name="Python", proficiency=4, category="technical", notes="Primary lang"
        )
        assert skill.name == "Python"
        assert skill.proficiency == 4
        assert skill.category == "technical"
        assert skill.notes == "Primary lang"
        assert isinstance(skill.id, UUID)
        assert skill.created_at is not None

    def test_skill_persisted_to_disk(self, store: VaultStore) -> None:
        store.add_skill(name="Go", proficiency=3, category="technical")
        vault = store.load()
        assert len(vault.skills) == 1
        assert vault.skills[0].name == "Go"

    def test_auto_commits_to_git(self, store: VaultStore) -> None:
        store.add_skill(name="SQL", proficiency=5, category="technical")
        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=str(store.directory),
            capture_output=True,
            text=True,
            check=False,
        )
        assert "Add skill: SQL (5/5)" in result.stdout

    def test_multiple_skills_accumulate(self, store: VaultStore) -> None:
        store.add_skill(name="A", proficiency=1, category="x")
        store.add_skill(name="B", proficiency=2, category="y")
        vault = store.load()
        assert len(vault.skills) == 2

    def test_taxonomy_id_set_when_provided(self, store: VaultStore) -> None:
        tid = uuid4()
        skill = store.add_skill(
            name="Test", proficiency=3, category="x", taxonomy_id=tid
        )
        assert skill.taxonomy_id == tid

    def test_duplicate_name_rejected(self, store: VaultStore) -> None:
        first = store.add_skill(name="Python", proficiency=4, category="technical")
        with pytest.raises(DuplicateSkillError) as exc_info:
            store.add_skill(name="Python", proficiency=3, category="technical")
        assert exc_info.value.existing_id == first.id
        # Duplicate was not appended.
        assert len(store.load().skills) == 1

    def test_duplicate_name_case_insensitive(self, store: VaultStore) -> None:
        store.add_skill(name="Python", proficiency=4, category="technical")
        with pytest.raises(DuplicateSkillError):
            store.add_skill(name="python", proficiency=3, category="technical")
        with pytest.raises(DuplicateSkillError):
            store.add_skill(name="  PYTHON  ", proficiency=3, category="technical")
        assert len(store.load().skills) == 1


# ------------------------------------------------------------------
# VaultStore.add_experience
# ------------------------------------------------------------------


class TestAddExperience:
    def test_with_all_fields(self, store: VaultStore) -> None:
        exp = store.add_experience(
            title="Senior Engineer",
            company="Acme",
            start_date="2020-01",
            end_date="2023-06",
            description="Built things",
            accomplishments=["Scaled team", "Led migration"],
        )
        assert exp.title == "Senior Engineer"
        assert exp.company == "Acme"
        assert exp.start_date == "2020-01"
        assert exp.end_date == "2023-06"
        assert len(exp.accomplishments) == 2
        assert isinstance(exp.id, UUID)

    def test_persisted_to_disk(self, store: VaultStore) -> None:
        store.add_experience(
            title="Dev",
            company="Corp",
            start_date="2019-01",
        )
        vault = store.load()
        assert len(vault.experiences) == 1

    def test_with_skill_ids_cross_reference(self, store: VaultStore) -> None:
        # Contract revision 1.1: experiences link the skills exercised
        # in the role, same reference style as story skill_ids.
        skill = store.add_skill(name="Python", proficiency=4, category="tech")
        exp = store.add_experience(
            title="Staff Engineer",
            company="Acme",
            start_date="2020-01",
            skill_ids=[skill.id],
        )
        assert skill.id in exp.skill_ids
        assert store.load().experiences[0].skill_ids == [skill.id]

    def test_skill_ids_default_empty(self, store: VaultStore) -> None:
        exp = store.add_experience(title="Dev", company="Co", start_date="2019-01")
        assert exp.skill_ids == []

    def test_skill_links_round_trip(self, store: VaultStore) -> None:
        # Contract revision 1.2: skill_links annotates per-skill
        # proficiency for skills already in skill_ids; skill_ids stays
        # authoritative for membership.
        skill = store.add_skill(name="Python", proficiency=4, category="tech")
        exp = store.add_experience(
            title="Staff Engineer",
            company="Acme",
            start_date="2020-01",
            skill_ids=[skill.id],
            skill_links=[{"skill_id": skill.id, "proficiency": 5}],
        )
        assert len(exp.skill_links) == 1
        assert exp.skill_links[0].skill_id == skill.id
        assert exp.skill_links[0].proficiency == 5
        # Survives a save → reload round-trip.
        reloaded = store.load().experiences[0]
        assert reloaded.skill_links[0].skill_id == skill.id
        assert reloaded.skill_links[0].proficiency == 5

    def test_skill_links_default_empty(self, store: VaultStore) -> None:
        exp = store.add_experience(title="Dev", company="Co", start_date="2019-01")
        assert exp.skill_links == []

    def test_skill_links_entry_without_matching_skill_id_dropped(
        self, store: VaultStore
    ) -> None:
        # skill_ids is authoritative: a link whose skill_id is not in
        # skill_ids carries no membership effect and is dropped.
        skill = store.add_skill(name="Python", proficiency=4, category="tech")
        stray = uuid4()
        exp = store.add_experience(
            title="Eng",
            company="Co",
            start_date="2019-01",
            skill_ids=[skill.id],
            skill_links=[
                {"skill_id": skill.id, "proficiency": 3},
                {"skill_id": stray, "proficiency": 2},
            ],
        )
        assert [sl.skill_id for sl in exp.skill_links] == [skill.id]

    def test_empty_skill_links_not_emitted_to_frontmatter(
        self, store: VaultStore
    ) -> None:
        # A v1.1-style experience (skill_ids only, no skill_links) must not
        # gain a `skill_links:` line — preserves byte-compat / clean diffs.
        skill = store.add_skill(name="Python", proficiency=4, category="tech")
        exp = store.add_experience(
            title="Engineer",
            company="Acme",
            start_date="2020-01",
            skill_ids=[skill.id],
        )
        md_files = list((store.directory / "experiences").glob("*.md"))
        assert len(md_files) == 1
        text = md_files[0].read_text(encoding="utf-8")
        assert "skill_links" not in text
        assert "skill_ids" in text
        # And it round-trips back with an empty list.
        assert store.load().experiences[0].skill_links == []
        assert exp.skill_links == []

    def test_skill_links_emitted_when_present(self, store: VaultStore) -> None:
        skill = store.add_skill(name="Python", proficiency=4, category="tech")
        store.add_experience(
            title="Engineer",
            company="Acme",
            start_date="2020-01",
            skill_ids=[skill.id],
            skill_links=[{"skill_id": skill.id, "proficiency": 5}],
        )
        md_files = list((store.directory / "experiences").glob("*.md"))
        text = md_files[0].read_text(encoding="utf-8")
        assert "skill_links" in text

    def test_auto_commits(self, store: VaultStore) -> None:
        store.add_experience(
            title="Manager",
            company="BigCo",
            start_date="2021-01",
        )
        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=str(store.directory),
            capture_output=True,
            text=True,
            check=False,
        )
        assert "Add experience: Manager at BigCo" in result.stdout


# ------------------------------------------------------------------
# VaultStore.add_story
# ------------------------------------------------------------------


class TestAddStory:
    def test_with_skill_ids_cross_reference(self, store: VaultStore) -> None:
        skill = store.add_skill(name="Python", proficiency=4, category="tech")
        story = store.add_story(
            title="Data Pipeline Redesign",
            situation="Legacy system failing",
            task="Rebuild from scratch",
            action="Designed new architecture",
            result="99.9% uptime",
            skill_ids=[skill.id],
        )
        assert story.title == "Data Pipeline Redesign"
        assert skill.id in story.skill_ids
        assert story.situation == "Legacy system failing"

    def test_persisted_and_committed(self, store: VaultStore) -> None:
        store.add_story(
            title="Test Story",
            situation="s",
            task="t",
            action="a",
            result="r",
        )
        vault = store.load()
        assert len(vault.stories) == 1
        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=str(store.directory),
            capture_output=True,
            text=True,
            check=False,
        )
        assert "Add story: Test Story" in result.stdout


# ------------------------------------------------------------------
# VaultStore.add_philosophy
# ------------------------------------------------------------------


class TestAddPhilosophy:
    def test_add_philosophy(self, store: VaultStore) -> None:
        p = store.add_philosophy(
            title="Delegation as Leverage",
            description="Strategic use of delegation",
            category="leadership",
        )
        assert p.title == "Delegation as Leverage"
        assert p.category == PhilosophyCategory.LEADERSHIP

    def test_with_evidence_ids(self, store: VaultStore) -> None:
        story = store.add_story(
            title="S", situation="s", task="t", action="a", result="r"
        )
        p = store.add_philosophy(
            title="P",
            description="D",
            category="collaboration",
            evidence_story_ids=[story.id],
        )
        assert story.id in p.evidence_story_ids


# ------------------------------------------------------------------
# VaultStore.add_education
# ------------------------------------------------------------------


class TestAddEducation:
    def test_add_education(self, store: VaultStore) -> None:
        edu = store.add_education(
            institution="MIT",
            degree="Master",
            field_of_study="CS",
            start_date="2018",
            end_date="2020",
            description="Focus on ML",
        )
        assert edu.institution == "MIT"
        assert edu.degree == "Master"
        vault = store.load()
        assert len(vault.education) == 1


# ------------------------------------------------------------------
# VaultStore.remove_item
# ------------------------------------------------------------------


class TestRemoveItem:
    def test_removes_the_right_item(self, store: VaultStore) -> None:
        s1 = store.add_skill(name="A", proficiency=1, category="x")
        s2 = store.add_skill(name="B", proficiency=2, category="y")
        removed = store.remove_item("skills", s1.id)
        assert removed is True
        vault = store.load()
        assert len(vault.skills) == 1
        assert vault.skills[0].id == s2.id

    def test_non_existent_id_returns_false(self, store: VaultStore) -> None:
        removed = store.remove_item("skills", uuid4())
        assert removed is False

    def test_invalid_section_returns_false(self, store: VaultStore) -> None:
        removed = store.remove_item("nonexistent", uuid4())
        assert removed is False


# ------------------------------------------------------------------
# VaultStore.get_item
# ------------------------------------------------------------------


class TestGetItem:
    def test_returns_correct_item(self, store: VaultStore) -> None:
        skill = store.add_skill(name="Rust", proficiency=4, category="technical")
        found = store.get_item("skills", skill.id)
        assert found is not None
        assert found.name == "Rust"  # type: ignore[union-attr]

    def test_returns_none_for_missing(self, store: VaultStore) -> None:
        found = store.get_item("skills", uuid4())
        assert found is None


# ------------------------------------------------------------------
# Proficiency validation (schema layer)
# ------------------------------------------------------------------


class TestProficiencyValidation:
    def test_proficiency_1_ok(self) -> None:
        s = SkillSchema(name="Test", proficiency=1)
        assert s.proficiency == 1

    def test_proficiency_5_ok(self) -> None:
        s = SkillSchema(name="Test", proficiency=5)
        assert s.proficiency == 5

    def test_proficiency_0_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SkillSchema(name="Test", proficiency=0)

    def test_proficiency_6_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SkillSchema(name="Test", proficiency=6)


# ------------------------------------------------------------------
# CLI: vault show
# ------------------------------------------------------------------


class TestVaultShowCLI:
    def test_vault_show_output(self, runner: CliRunner, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        store.add_skill(name="A", proficiency=5, category="x")
        store.add_skill(name="B", proficiency=3, category="y")
        result = runner.invoke(cli, ["--path", str(vault_dir), "vault", "show"])
        assert result.exit_code == 0
        out = result.output
        assert "Skills (2):" in out
        assert "A (5/5)" in out
        assert "B (3/5)" in out
        assert "Experiences (0)" in out
        assert "Stories (0)" in out
        assert "Philosophies (0)" in out
        assert "--verbose" in out

    def test_vault_show_sorts_skills_by_proficiency(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        for name, prof in [
            ("LowSkill", 2),
            ("TopSkill", 5),
            ("MidSkill", 3),
            ("Other1", 4),
            ("Other2", 3),
            ("HiddenSkill", 1),
        ]:
            store.add_skill(name=name, proficiency=prof, category="x")
        result = runner.invoke(cli, ["--path", str(vault_dir), "vault", "show"])
        assert result.exit_code == 0
        out = result.output
        assert "Skills (6):" in out
        assert "TopSkill (5/5)" in out
        # Top 5 shown, 6th (HiddenSkill, lowest) should be omitted
        assert "HiddenSkill" not in out
        assert "... 1 more" in out
        # Verify proficiency sort: TopSkill appears before MidSkill in output
        assert out.index("TopSkill") < out.index("MidSkill")

    def test_vault_show_profile_header(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        store.set_profile(
            display_name="Jane Doe",
            headline="Staff Engineer",
            location="San Francisco",
        )
        result = runner.invoke(cli, ["--path", str(vault_dir), "vault", "show"])
        assert result.exit_code == 0
        out = result.output
        assert "Jane Doe — Staff Engineer" in out
        assert "Location: San Francisco" in out

    def test_vault_show_experience_and_story(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        store.add_experience(
            title="Staff Engineer",
            company="Acme",
            start_date="2020-01",
            end_date="2024-06",
        )
        store.add_story(
            title="Scaling to 10M users",
            situation="s",
            task="t",
            action="a",
            result="r",
        )
        store.add_philosophy(
            title="Small PRs",
            description="d",
            category=PhilosophyCategory.TECHNICAL_APPROACH,
        )
        result = runner.invoke(cli, ["--path", str(vault_dir), "vault", "show"])
        assert result.exit_code == 0
        out = result.output
        assert "Staff Engineer @ Acme (2020-01 — 2024-06)" in out
        assert "Scaling to 10M users" in out
        assert "Philosophies (1): technical-approach (1)" in out

    def test_no_vault(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(cli, ["--path", str(tmp_path / "nope"), "vault", "show"])
        assert "No vault found" in result.output

    def test_vault_show_verbose_contents(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        store.add_skill(
            name="Python",
            proficiency=4,
            category="technical",
            notes="Primary lang",
        )
        store.add_experience(
            title="Staff Engineer",
            company="Acme",
            start_date="2020-01",
            end_date="2024-06",
            description="Led platform team.",
            accomplishments=["Shipped v2", "Grew team"],
        )
        store.add_philosophy(
            title="Small PRs",
            description="Small diffs are easier to review.",
            category=PhilosophyCategory.TECHNICAL_APPROACH,
        )
        result = runner.invoke(
            cli, ["--path", str(vault_dir), "vault", "show", "--verbose"]
        )
        assert result.exit_code == 0, result.output
        out = result.output
        assert "Schema version:" in out
        assert "Profile:" in out
        assert "Skills (1)" in out
        assert "Python" in out
        assert "proficiency: 4/5" in out
        assert "Primary lang" in out
        assert "Experiences (1)" in out
        assert "Staff Engineer" in out
        assert "Acme" in out
        assert "Shipped v2" in out
        assert "Philosophies (1)" in out
        assert "Small PRs" in out
        assert "technical-approach" in out
        assert "Git:" in out

    def test_vault_show_verbose_experience_skill_ids(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        # Experience skill links surface like story skill links do.
        store = VaultStore(vault_dir)
        skill = store.add_skill(name="Python", proficiency=4, category="technical")
        store.add_experience(
            title="Staff Engineer",
            company="Acme",
            start_date="2020-01",
            skill_ids=[skill.id],
        )
        result = runner.invoke(
            cli, ["--path", str(vault_dir), "vault", "show", "--verbose"]
        )
        assert result.exit_code == 0, result.output
        assert f"skill_ids:   {skill.id}" in result.output

    def test_vault_show_verbose_short_flag(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(cli, ["--path", str(vault_dir), "vault", "show", "-v"])
        assert result.exit_code == 0
        assert "Profile:" in result.output
        assert "Skills (0)" in result.output


# ------------------------------------------------------------------
# CLI: vault list
# ------------------------------------------------------------------


class TestVaultListCLI:
    def test_list_skills(self, runner: CliRunner, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        store.add_skill(name="Python", proficiency=4, category="technical")
        result = runner.invoke(
            cli, ["--path", str(vault_dir), "vault", "list", "skills"]
        )
        assert result.exit_code == 0
        assert "Python" in result.output
        assert "technical" in result.output

    def test_list_experiences_shows_skill_count(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        # Experiences surface a Skills count column like stories do.
        store = VaultStore(vault_dir)
        skill = store.add_skill(name="Python", proficiency=4, category="technical")
        exp = store.add_experience(
            title="Staff Engineer",
            company="Acme",
            start_date="2020-01",
            skill_ids=[skill.id],
        )
        result = runner.invoke(
            cli, ["--path", str(vault_dir), "vault", "list", "experiences"]
        )
        assert result.exit_code == 0
        assert "Skills" in result.output
        assert f"     1  {exp.id}" in result.output

    def test_list_empty_section(self, runner: CliRunner, vault_dir: Path) -> None:
        result = runner.invoke(
            cli, ["--path", str(vault_dir), "vault", "list", "skills"]
        )
        assert "No skills found" in result.output


# ------------------------------------------------------------------
# CLI: vault set-profile
# ------------------------------------------------------------------


class TestSetProfileCLI:
    def test_set_all_fields(self, runner: CliRunner, vault_dir: Path) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "set-profile",
                "--name",
                "Ada Lovelace",
                "--headline",
                "Mathematician & programmer",
                "--summary",
                "Wrote the first algorithm.",
                "--location",
                "London",
                "--email",
                "ada@example.com",
            ],
        )
        assert result.exit_code == 0
        assert "Updated profile" in result.output

        profile = VaultStore(vault_dir).load().profile
        assert profile.display_name == "Ada Lovelace"
        assert profile.headline == "Mathematician & programmer"
        assert profile.summary == "Wrote the first algorithm."
        assert profile.location == "London"
        assert profile.contact_email == "ada@example.com"

    def test_partial_update_preserves_existing(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        store.set_profile(display_name="Grace", headline="Engineer")

        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "set-profile",
                "--headline",
                "Rear Admiral",
            ],
        )
        assert result.exit_code == 0

        profile = store.load().profile
        assert profile.display_name == "Grace"
        assert profile.headline == "Rear Admiral"

    def test_no_fields_errors(self, runner: CliRunner, vault_dir: Path) -> None:
        result = runner.invoke(cli, ["--path", str(vault_dir), "vault", "set-profile"])
        assert result.exit_code == 1
        assert "No fields provided" in result.output

    def test_no_vault(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(tmp_path / "nope"),
                "vault",
                "set-profile",
                "--name",
                "X",
            ],
        )
        assert "No vault found" in result.output

    def test_empty_string_clears_field(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        store.set_profile(headline="Old headline")

        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "set-profile",
                "--headline",
                "",
            ],
        )
        assert result.exit_code == 0
        assert store.load().profile.headline == ""

    def test_set_phone_url_and_links(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "set-profile",
                "--phone",
                "+1 555 0100",
                "--url",
                "https://ada.example.com",
                "--link",
                "github=https://github.com/ada",
                "--link",
                "linkedin=https://linkedin.com/in/ada",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "github: https://github.com/ada" in result.output

        profile = VaultStore(vault_dir).load().profile
        assert profile.phone == "+1 555 0100"
        assert profile.url == "https://ada.example.com"
        assert [(p.network, p.url) for p in profile.profiles] == [
            ("github", "https://github.com/ada"),
            ("linkedin", "https://linkedin.com/in/ada"),
        ]

    def test_links_replace_existing_list(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        store.set_profile(
            profiles=[ProfileLink(network="github", url="https://github.com/old")]
        )
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "set-profile",
                "--link",
                "mastodon=https://hachyderm.io/@ada",
            ],
        )
        assert result.exit_code == 0
        profiles = store.load().profile.profiles
        assert [(p.network, p.url) for p in profiles] == [
            ("mastodon", "https://hachyderm.io/@ada")
        ]

    def test_empty_link_clears_list(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        store.set_profile(
            profiles=[ProfileLink(network="github", url="https://github.com/x")]
        )
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "set-profile", "--link", ""],
        )
        assert result.exit_code == 0
        assert store.load().profile.profiles == []

    def test_invalid_link_format_errors(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "set-profile",
                "--link",
                "github",
            ],
        )
        assert result.exit_code == 1
        assert "Invalid --link" in result.output
        # Nothing was written.
        assert VaultStore(vault_dir).load().profile.profiles == []


# ------------------------------------------------------------------
# CLI: vault add-skill (with taxonomy integration)
# ------------------------------------------------------------------


class TestAddSkillCLI:
    def test_add_skill_with_taxonomy_match(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "Python",
                "--proficiency",
                "4",
                "--category",
                "technical",
            ],
        )
        assert result.exit_code == 0
        assert "Matched taxonomy" in result.output
        assert "Added skill: Python" in result.output

        # Verify taxonomy_id was set
        store = VaultStore(vault_dir)
        vault = store.load()
        assert vault.skills[0].taxonomy_id is not None

    def test_category_optional_taxonomy_fills_it(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "add-skill", "Python", "-p", "4"],
        )
        assert result.exit_code == 0, result.output
        skill = VaultStore(vault_dir).load().skills[0]
        assert skill.category == "technical"  # from the taxonomy match

    def test_category_optional_defaults_empty_without_match(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "add-skill", "FooBarLang", "-p", "2"],
        )
        assert result.exit_code == 0, result.output
        assert VaultStore(vault_dir).load().skills[0].category == ""

    def test_missing_proficiency_is_usage_error(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        # No interactive prompt for missing required fields — exit 2 with
        # an honest message that --category is optional, on stderr like
        # click's own usage errors.
        result = runner.invoke(
            cli, ["--path", str(vault_dir), "vault", "add-skill", "Python"]
        )
        assert result.exit_code == 2
        assert "NAME and --proficiency are required" in result.stderr
        assert "--category is optional" in result.stderr

    def test_add_skill_duplicate_rejected_cli(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        args = [
            "--path",
            str(vault_dir),
            "vault",
            "add-skill",
            "Python",
            "--proficiency",
            "4",
            "--category",
            "technical",
        ]
        first = runner.invoke(cli, args)
        assert first.exit_code == 0
        second = runner.invoke(cli, args)
        assert second.exit_code == 1
        assert "already exists" in second.stderr
        assert len(VaultStore(vault_dir).load().skills) == 1

    def test_add_skill_suggestions_note_follows_the_add(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        # The skill is committed before any "did you mean" hint, so the
        # output must say so in that order and hand the agent the exact
        # remove/re-add swap — with the real UUID — instead of implying
        # a pending question (no prompt: agents run non-interactively).
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "add-skill", "script", "-p", "3"],
        )
        assert result.exit_code == 0
        skill = VaultStore(vault_dir).load().skills[0]

        added = result.stdout.index(f"Added skill: script (3/5) [{skill.id}]")
        note = result.stdout.index(
            "[note] added as a custom skill (no taxonomy match)."
        )
        assert added < note
        assert "If you meant one of: " in result.stdout
        assert "Script" in result.stdout  # real taxonomy suggestions
        assert (
            f"run: traitprint vault remove {skill.id} -y "
            '&& traitprint vault add-skill "<name>" -p 3'
        ) in result.stdout
        assert '-n "<notes>"' not in result.stdout  # no notes were passed

    def test_add_skill_suggestions_hint_keeps_notes(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        # When the add carried --notes, the re-add half of the swap must
        # remind about them (as a placeholder — raw notes could contain
        # quotes or newlines that break the paste), or following the hint
        # verbatim silently drops the notes.
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "script",
                "-p",
                "2",
                "-n",
                "used on the ETL rewrite",
            ],
        )
        assert result.exit_code == 0
        skill = VaultStore(vault_dir).load().skills[0]
        assert (
            f"run: traitprint vault remove {skill.id} -y "
            '&& traitprint vault add-skill "<name>" -p 2 -n "<notes>"'
        ) in result.stdout

    def test_add_skill_from_json_combined_is_usage_error(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "Python",
                "--from-json",
                "-",
            ],
            input="[]",
        )
        assert result.exit_code == 2
        assert "--from-json cannot be combined" in result.stderr

    def test_add_skill_no_taxonomy_match(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "FooBarLang",
                "--proficiency",
                "5",
                "--category",
                "technical",
            ],
        )
        assert result.exit_code == 0
        assert "Added skill: FooBarLang" in result.output

    def test_add_skill_category_mismatch_taxonomy_wins(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "Kubernetes",
                "--proficiency",
                "4",
                "--category",
                "technical",
            ],
        )
        assert result.exit_code == 0
        assert "Matched taxonomy: Kubernetes (tool)" in result.output
        assert "Overriding --category 'technical'" in result.output
        assert "--force-category" in result.output
        stored = VaultStore(vault_dir).load().skills[0]
        assert stored.category == "tool"

    def test_add_skill_category_mismatch_force_keeps_user_value(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "Kubernetes",
                "--proficiency",
                "4",
                "--category",
                "technical",
                "--force-category",
            ],
        )
        assert result.exit_code == 0
        assert "Matched taxonomy: Kubernetes (tool)" in result.output
        assert "Keeping --category 'technical'" in result.output
        stored = VaultStore(vault_dir).load().skills[0]
        assert stored.category == "technical"
        assert stored.taxonomy_id is not None

    def test_add_skill_category_agrees_no_warning(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "Python",
                "--proficiency",
                "4",
                "--category",
                "technical",
            ],
        )
        assert result.exit_code == 0
        assert "Overriding" not in result.output
        assert "Keeping" not in result.output
        stored = VaultStore(vault_dir).load().skills[0]
        assert stored.category == "technical"


# ------------------------------------------------------------------
# CLI: vault add-experience / add-story / add-philosophy (non-interactive)
# ------------------------------------------------------------------


class TestAddExperienceCLI:
    def test_non_interactive_required_only(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-experience",
                "--title",
                "Senior Engineer",
                "--company",
                "Acme",
                "--start-date",
                "2022-01",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Added experience: Senior Engineer at Acme" in result.output
        v = VaultStore(vault_dir).load()
        assert len(v.experiences) == 1
        exp = v.experiences[0]
        assert exp.title == "Senior Engineer"
        assert exp.company == "Acme"
        assert exp.start_date == "2022-01"

    def test_non_interactive_with_accomplishments(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-experience",
                "--title",
                "Lead",
                "--company",
                "Beta",
                "--start-date",
                "2021-06",
                "--end-date",
                "2023-03",
                "--description",
                "Built stuff",
                "--accomplishment",
                "Shipped X",
                "--accomplishment",
                "Reduced Y by 30%",
            ],
        )
        assert result.exit_code == 0, result.output
        v = VaultStore(vault_dir).load()
        exp = v.experiences[0]
        assert exp.end_date == "2023-03"
        assert exp.description == "Built stuff"
        assert exp.accomplishments == ["Shipped X", "Reduced Y by 30%"]

    def test_non_interactive_with_skill_ids(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        skill = store.add_skill(name="Go", proficiency=4, category="technical")
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-experience",
                "--title",
                "Platform Lead",
                "--company",
                "Acme",
                "--skill-id",
                str(skill.id),
            ],
        )
        assert result.exit_code == 0, result.output
        exp = store.load().experiences[0]
        assert exp.skill_ids == [skill.id]

    def test_batch_mode_rejects_skill_id_flag(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        skill = store.add_skill(name="Go", proficiency=4, category="technical")
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-experience",
                "--from-json",
                "-",
                "--skill-id",
                str(skill.id),
            ],
            input="[]",
        )
        assert result.exit_code == 2
        assert "--from-json cannot be combined with --skill-id" in result.output
        assert store.load().experiences == []


class TestAddEducationCLI:
    def test_non_interactive_required_only(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-education",
                "--institution",
                "MIT",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Added education" in result.output
        v = VaultStore(vault_dir).load()
        assert len(v.education) == 1
        assert v.education[0].institution == "MIT"

    def test_non_interactive_full_fields(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-education",
                "--institution",
                "MIT",
                "--degree",
                "Master",
                "--field",
                "Computer Science",
                "--start-date",
                "2018",
                "--end-date",
                "2020",
                "--description",
                "Focus on ML",
            ],
        )
        assert result.exit_code == 0, result.output
        v = VaultStore(vault_dir).load()
        edu = v.education[0]
        assert edu.institution == "MIT"
        assert edu.degree == "Master"
        assert edu.field_of_study == "Computer Science"
        assert edu.start_date == "2018"
        assert edu.end_date == "2020"
        assert edu.description == "Focus on ML"

    def test_interactive_still_works(self, runner: CliRunner, vault_dir: Path) -> None:
        # Without --institution, command falls back to prompts.
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "add-education"],
            input="Stanford\nPhD\nCS\n2010\n2015\nDistributed systems\n",
        )
        assert result.exit_code == 0, result.output
        v = VaultStore(vault_dir).load()
        assert len(v.education) == 1
        assert v.education[0].institution == "Stanford"
        assert v.education[0].degree == "PhD"


class TestAddStoryCLI:
    def test_non_interactive_star_fields(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-story",
                "--title",
                "Scaled checkout",
                "--situation",
                "High load",
                "--task",
                "Rearchitect",
                "--action",
                "Sharded DB",
                "--result",
                "10x throughput",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Added story: Scaled checkout" in result.output
        v = VaultStore(vault_dir).load()
        story = v.stories[0]
        assert story.title == "Scaled checkout"
        assert story.situation == "High load"
        assert story.task == "Rearchitect"
        assert story.action == "Sharded DB"
        assert story.result == "10x throughput"

    def test_non_interactive_with_skill_and_experience_refs(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        skill = store.add_skill(name="Go", proficiency=4, category="technical")
        exp = store.add_experience(title="Eng", company="Co", start_date="2020-01")
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-story",
                "--title",
                "Linked story",
                "--situation",
                "s",
                "--task",
                "t",
                "--action",
                "a",
                "--result",
                "r",
                "--skill-id",
                str(skill.id),
                "--experience-id",
                str(exp.id),
            ],
        )
        assert result.exit_code == 0, result.output
        v = store.load()
        story = v.stories[0]
        assert story.skill_ids == [skill.id]
        assert story.experience_id == exp.id


class TestAddPhilosophyCLI:
    def test_non_interactive_required_fields(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-philosophy",
                "--title",
                "Ship small",
                "--description",
                "Bias toward delivery.",
                "--category",
                PhilosophyCategory.LEADERSHIP.value,
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Added philosophy: Ship small" in result.output
        v = VaultStore(vault_dir).load()
        p = v.philosophies[0]
        assert p.title == "Ship small"
        assert p.category == PhilosophyCategory.LEADERSHIP

    def test_non_interactive_category_is_optional(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-philosophy",
                "--title",
                "Uncategorized stance",
                "--description",
                "No category provided.",
            ],
        )
        assert result.exit_code == 0, result.output
        v = VaultStore(vault_dir).load()
        assert v.philosophies[0].category == ""

    def test_non_interactive_with_evidence_ids(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        s = store.add_story(
            title="Evidence", situation="s", task="t", action="a", result="r"
        )
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-philosophy",
                "--title",
                "With evidence",
                "--description",
                "desc",
                "--category",
                PhilosophyCategory.COLLABORATION.value,
                "--evidence-id",
                str(s.id),
            ],
        )
        assert result.exit_code == 0, result.output
        v = store.load()
        p = v.philosophies[0]
        assert p.evidence_story_ids == [s.id]


# ------------------------------------------------------------------
# CLI: --json read surface (show / list / history / diff) — tp-an-002
# ------------------------------------------------------------------


class TestJsonReadSurface:
    @pytest.fixture()
    def seeded_dir(self, vault_dir: Path) -> Path:
        store = VaultStore(vault_dir)
        skill = store.add_skill(name="Python", proficiency=4, category="technical")
        store.add_experience(
            title="Staff Engineer",
            company="Acme",
            start_date="2020-01",
            skill_ids=[skill.id],
        )
        store.add_story(
            title="Migration",
            situation="s",
            task="t",
            action="a",
            result="r",
            skill_ids=[skill.id],
        )
        return vault_dir

    def test_show_json_emits_full_vault(
        self, runner: CliRunner, seeded_dir: Path
    ) -> None:
        result = runner.invoke(
            cli, ["--path", str(seeded_dir), "vault", "show", "--json"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["schema_version"] == 1
        assert {
            "vault_id",
            "updated_at",
            "profile",
            "skills",
            "experiences",
            "stories",
            "philosophies",
            "education",
        } <= set(payload)
        skill = payload["skills"][0]
        assert skill["name"] == "Python"
        assert skill["proficiency"] == 4
        UUID(skill["id"])
        story = payload["stories"][0]
        assert {"lesson", "outcome", "theme_tags", "skill_ids"} <= set(story)
        experience = payload["experiences"][0]
        assert "skill_ids" in experience
        assert experience["skill_ids"] == [payload["skills"][0]["id"]]

    def test_list_json_shape(self, runner: CliRunner, seeded_dir: Path) -> None:
        result = runner.invoke(
            cli, ["--path", str(seeded_dir), "vault", "list", "skills", "--json"]
        )
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert len(rows) == 1
        assert set(rows[0]) == {"id", "type", "name"}
        assert rows[0]["type"] == "skill"
        assert rows[0]["name"] == "Python"
        UUID(rows[0]["id"])

        result = runner.invoke(
            cli, ["--path", str(seeded_dir), "vault", "list", "stories", "--json"]
        )
        rows = json.loads(result.output)
        assert set(rows[0]) == {"id", "type", "title"}
        assert rows[0]["type"] == "story"
        assert rows[0]["title"] == "Migration"

    def test_list_json_empty_section_is_empty_array(
        self, runner: CliRunner, seeded_dir: Path
    ) -> None:
        result = runner.invoke(
            cli, ["--path", str(seeded_dir), "vault", "list", "education", "--json"]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == []

    def test_history_json_shape(self, runner: CliRunner, seeded_dir: Path) -> None:
        result = runner.invoke(
            cli, ["--path", str(seeded_dir), "vault", "history", "--json"]
        )
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert len(rows) >= 3
        for row in rows:
            assert set(row) == {"sha", "message"}
            assert row["sha"]
        assert any("Add skill: Python" in row["message"] for row in rows)

    def test_diff_json_shape(self, runner: CliRunner, seeded_dir: Path) -> None:
        result = runner.invoke(
            cli, ["--path", str(seeded_dir), "vault", "diff", "--json"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert set(payload) == {"from_sha", "to_sha", "diff_text"}
        assert payload["from_sha"]
        assert payload["to_sha"]
        # The last commit added a story file.
        assert "stories/migration.md" in payload["diff_text"]


# ------------------------------------------------------------------
# CLI: add-story lesson / outcome / theme tags write surface
# ------------------------------------------------------------------


class TestAddStoryExtendedFields:
    def test_flags_persist_lesson_outcome_theme_tags(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-story",
                "--title",
                "Pager Storm",
                "--situation",
                "Cascading outage.",
                "--task",
                "Restore service.",
                "--action",
                "Rolled back, ran the bridge.",
                "--result",
                "Restored in 40 minutes.",
                "--lesson",
                "Stage rollouts behind flags.",
                "--outcome",
                "learning",
                "--theme-tag",
                "incident-response",
                "--theme-tag",
                "process-change",
            ],
        )
        assert result.exit_code == 0, result.output
        story = VaultStore(vault_dir).load().stories[0]
        assert story.lesson == "Stage rollouts behind flags."
        assert story.outcome == "learning"
        assert story.theme_tags == ["incident-response", "process-change"]

    def test_invalid_outcome_is_usage_error(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-story",
                "--title",
                "T",
                "--outcome",
                "triumph",
            ],
        )
        assert result.exit_code == 2
        assert "triumph" in result.output

    def test_show_verbose_displays_extended_fields(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        store.add_story(
            title="Pager Storm",
            situation="s",
            task="t",
            action="a",
            result="r",
            lesson="Stage rollouts behind flags.",
            outcome="learning",
            theme_tags=["incident-response"],
        )
        result = runner.invoke(cli, ["--path", str(vault_dir), "vault", "show", "-v"])
        assert result.exit_code == 0, result.output
        assert "lesson:    Stage rollouts behind flags." in result.output
        assert "outcome:   learning" in result.output
        assert "theme_tags: incident-response" in result.output

    def test_batch_accepts_extended_keys(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        payload = tmp_path / "stories.json"
        payload.write_text(
            '[{"title":"Tagged","situation":"s","task":"t","action":"a",'
            '"result":"r","lesson":"L","outcome":"win",'
            '"theme_tags":["incident-response"]}]'
        )
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-story",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 0, result.output
        story = VaultStore(vault_dir).load().stories[0]
        assert story.lesson == "L"
        assert story.outcome == "win"
        assert story.theme_tags == ["incident-response"]

    def test_batch_rejects_invalid_outcome(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        payload = tmp_path / "stories.json"
        payload.write_text('[{"title":"Bad","outcome":"triumph"}]')
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-story",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 1
        assert "[err] Bad" in result.output
        assert "outcome" in result.output


# ------------------------------------------------------------------
# CLI: UUID flag validation (no raw tracebacks)
# ------------------------------------------------------------------


class TestUUIDFlagValidation:
    """Invalid UUIDs in flags are usage errors (exit 2), never tracebacks."""

    def _assert_uuid_usage_error(self, result: object, bad: str) -> None:
        assert result.exit_code == 2, result.output  # type: ignore[attr-defined]
        assert f"invalid UUID '{bad}'" in result.output  # type: ignore[attr-defined]
        assert result.exception is None or isinstance(  # type: ignore[attr-defined]
            result.exception,  # type: ignore[attr-defined]
            SystemExit,
        )

    def test_add_philosophy_evidence_id(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-philosophy",
                "--title",
                "T",
                "--evidence-id",
                "PLACEHOLDER",
            ],
        )
        self._assert_uuid_usage_error(result, "PLACEHOLDER")
        assert VaultStore(vault_dir).load().philosophies == []

    def test_add_story_skill_id(self, runner: CliRunner, vault_dir: Path) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-story",
                "--title",
                "T",
                "--skill-id",
                "not-a-uuid",
            ],
        )
        self._assert_uuid_usage_error(result, "not-a-uuid")
        assert VaultStore(vault_dir).load().stories == []

    def test_add_story_experience_id(self, runner: CliRunner, vault_dir: Path) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-story",
                "--title",
                "T",
                "--experience-id",
                "not-a-uuid",
            ],
        )
        self._assert_uuid_usage_error(result, "not-a-uuid")

    def test_remove_argument(self, runner: CliRunner, vault_dir: Path) -> None:
        result = runner.invoke(
            cli, ["--path", str(vault_dir), "vault", "remove", "nope", "-y"]
        )
        self._assert_uuid_usage_error(result, "nope")

    def test_valid_uuid_still_accepted(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        skill = store.add_skill(name="Go", proficiency=3, category="technical")
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-story",
                "--title",
                "T",
                "--situation",
                "s",
                "--task",
                "t",
                "--action",
                "a",
                "--result",
                "r",
                "--skill-id",
                str(skill.id),
            ],
        )
        assert result.exit_code == 0, result.output
        assert store.load().stories[0].skill_ids == [skill.id]


# ------------------------------------------------------------------
# CLI: vault history / diff / rollback
# ------------------------------------------------------------------


class TestHistoryDiffRollback:
    def test_history_shows_commits(self, runner: CliRunner, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        store.add_skill(name="Go", proficiency=4, category="technical")
        result = runner.invoke(cli, ["--path", str(vault_dir), "vault", "history"])
        assert result.exit_code == 0
        assert "Add skill: Go" in result.output

    def test_rollback_with_confirm(self, runner: CliRunner, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        store.add_skill(name="X", proficiency=1, category="z")
        assert len(store.load().skills) == 1

        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "rollback", "--yes"],
        )
        assert result.exit_code == 0
        assert "rolled back" in result.output
        assert len(store.load().skills) == 0


# ------------------------------------------------------------------
# CLI: vault add-* --from-json (batch mode)
# ------------------------------------------------------------------


class TestAddSkillBatch:
    def test_batch_adds_multiple_skills(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        payload = tmp_path / "skills.json"
        payload.write_text(
            '[{"name":"Go","proficiency":4,"category":"technical"},'
            '{"name":"Rust","proficiency":3,"category":"technical","notes":"WIP"}]'
        )
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "[ok] Go" in result.output
        assert "[ok] Rust" in result.output
        assert "Summary: added 2, errors 0" in result.output
        skills = VaultStore(vault_dir).load().skills
        assert {s.name for s in skills} == {"Go", "Rust"}
        rust = next(s for s in skills if s.name == "Rust")
        assert rust.notes == "WIP"

    def test_batch_continues_past_duplicate(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        store = VaultStore(vault_dir)
        store.add_skill(name="Python", proficiency=4, category="technical")
        payload = tmp_path / "skills.json"
        payload.write_text(
            '[{"name":"python","proficiency":3,"category":"technical"},'
            '{"name":"Go","proficiency":4,"category":"technical"}]'
        )
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 1
        assert "[dup] python" in result.output
        assert "[ok] Go" in result.output
        assert "Summary: added 1, errors 1" in result.output
        assert len(store.load().skills) == 2  # Python + Go

    def test_batch_reports_missing_fields(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        payload = tmp_path / "skills.json"
        payload.write_text(
            '[{"name":"Incomplete"},'
            '{"name":"Valid","proficiency":4,"category":"technical"}]'
        )
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 1
        # category is optional — only proficiency is reported missing.
        err_lines = [
            line for line in result.output.splitlines() if line.startswith("[err]")
        ]
        assert err_lines == ["[err] Incomplete: proficiency: missing required field"]
        assert "[ok] Valid" in result.output
        assert "Summary: added 1, errors 1" in result.output

    def test_batch_reports_all_violations_in_one_pass(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        # Missing field AND out-of-range proficiency reported together.
        payload = tmp_path / "skills.json"
        payload.write_text('[{"proficiency":99}]')
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 1
        assert "[err] item 0: name: missing required field" in result.output
        assert "[err] item 0: proficiency: must be between 1 and 5" in result.output
        # One failing item, regardless of how many violations it carries.
        assert "Summary: added 0, errors 1" in result.output

    def test_batch_never_leaks_pydantic_dumps(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        # An invalid philosophy category reaches the pydantic validator;
        # the output must stay in the normalized [err] style.
        payload = tmp_path / "phil.json"
        payload.write_text('[{"title":"Bad","category":"not-a-category"}]')
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-philosophy",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 1
        assert "[err] Bad: category:" in result.output
        assert "pydantic" not in result.output.lower()
        assert "https://" not in result.output
        assert "validation error" not in result.output.lower()

    def test_batch_skill_category_is_optional(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        payload = tmp_path / "skills.json"
        payload.write_text(
            '[{"name":"Python","proficiency":4},'
            '{"name":"FooBarLang","proficiency":2}]'
        )
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 0, result.output
        skills = {s.name: s for s in VaultStore(vault_dir).load().skills}
        # Taxonomy fills the category on a match; otherwise empty.
        assert skills["Python"].category == "technical"
        assert skills["FooBarLang"].category == ""

    def test_batch_reports_invalid_proficiency(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        payload = tmp_path / "skills.json"
        payload.write_text('[{"name":"X","proficiency":99,"category":"technical"}]')
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 1
        assert "[err] X" in result.output
        assert "Summary: added 0, errors 1" in result.output
        assert len(VaultStore(vault_dir).load().skills) == 0

    def test_batch_from_stdin(self, runner: CliRunner, vault_dir: Path) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "--from-json",
                "-",
            ],
            input='[{"name":"Bash","proficiency":5,"category":"tool"}]',
        )
        assert result.exit_code == 0, result.output
        assert "[ok] Bash" in result.output
        assert len(VaultStore(vault_dir).load().skills) == 1

    def test_batch_rejects_non_array(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        payload = tmp_path / "bad.json"
        payload.write_text('{"name":"Python","proficiency":4,"category":"technical"}')
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code != 0
        assert "array" in result.output.lower()

    def test_batch_rejects_invalid_json(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        payload = tmp_path / "bad.json"
        payload.write_text("{not json")
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code != 0
        assert "Invalid JSON" in result.output

    def test_from_json_rejects_mixed_flags(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        payload = tmp_path / "skills.json"
        payload.write_text('[{"name":"Go","proficiency":4,"category":"technical"}]')
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-skill",
                "Oops",
                "--proficiency",
                "5",
                "--category",
                "x",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 2
        assert "cannot be combined" in result.output


class TestAddExperienceBatch:
    def test_batch_adds_multiple(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        payload = tmp_path / "exp.json"
        payload.write_text(
            '[{"title":"Engineer","company":"Acme","start_date":"2020-01",'
            '"accomplishments":["Shipped X"]},'
            '{"title":"Lead","company":"Beta","start_date":"2022-03",'
            '"end_date":"2024-01","description":"Built team"}]'
        )
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-experience",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "[ok] Engineer at Acme" in result.output
        assert "[ok] Lead at Beta" in result.output
        exps = VaultStore(vault_dir).load().experiences
        assert len(exps) == 2
        eng = next(e for e in exps if e.title == "Engineer")
        assert eng.accomplishments == ["Shipped X"]

    def test_batch_missing_title(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        payload = tmp_path / "exp.json"
        payload.write_text('[{"company":"Acme"}]')
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-experience",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 1
        assert "title: missing required field" in result.output

    def test_batch_adds_with_skill_refs(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        store = VaultStore(vault_dir)
        skill = store.add_skill(name="Go", proficiency=4, category="technical")
        payload = tmp_path / "exp.json"
        payload.write_text(
            f'[{{"title":"Engineer","company":"Acme",'
            f'"skill_ids":["{skill.id}"]}}]'
        )
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-experience",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 0, result.output
        exp = store.load().experiences[0]
        assert exp.skill_ids == [skill.id]

    def test_batch_invalid_skill_uuid(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        payload = tmp_path / "exp.json"
        payload.write_text('[{"title":"Bad","skill_ids":["not-a-uuid"]}]')
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-experience",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 1
        assert "[err] Bad" in result.output
        assert "skill_ids" in result.output


class TestAddStoryBatch:
    def test_batch_adds_with_refs(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        store = VaultStore(vault_dir)
        skill = store.add_skill(name="Go", proficiency=4, category="technical")
        exp = store.add_experience(title="Eng", company="Co", start_date="2020-01")
        payload = tmp_path / "stories.json"
        payload.write_text(
            f'[{{"title":"Scaled API","situation":"load","task":"fix",'
            f'"action":"shard","result":"10x",'
            f'"skill_ids":["{skill.id}"],"experience_id":"{exp.id}"}},'
            f'{{"title":"Migrated DB","situation":"s","task":"t",'
            f'"action":"a","result":"r"}}]'
        )
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-story",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 0, result.output
        stories = store.load().stories
        assert len(stories) == 2
        scaled = next(s for s in stories if s.title == "Scaled API")
        assert scaled.skill_ids == [skill.id]
        assert scaled.experience_id == exp.id

    def test_batch_invalid_uuid(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        payload = tmp_path / "stories.json"
        payload.write_text('[{"title":"Bad","skill_ids":["not-a-uuid"]}]')
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-story",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 1
        assert "[err] Bad" in result.output


class TestAddPhilosophyBatch:
    def test_batch_adds_multiple(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        payload = tmp_path / "phil.json"
        payload.write_text(
            '[{"title":"Ship small","description":"Bias to delivery",'
            '"category":"leadership"},'
            '{"title":"Write it down","category":"collaboration"}]'
        )
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-philosophy",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "[ok] Ship small" in result.output
        assert "[ok] Write it down" in result.output
        phils = VaultStore(vault_dir).load().philosophies
        assert len(phils) == 2

    def test_batch_invalid_category(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        payload = tmp_path / "phil.json"
        payload.write_text('[{"title":"Bad","category":"not-a-real-category"}]')
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-philosophy",
                "--from-json",
                str(payload),
            ],
        )
        assert result.exit_code == 1
        assert "[err] Bad" in result.output


# ------------------------------------------------------------------
# CLI: vault remove
# ------------------------------------------------------------------


class TestRemoveCLI:
    def test_remove_with_confirm(self, runner: CliRunner, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        skill = store.add_skill(name="Rust", proficiency=4, category="technical")
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "remove", str(skill.id), "--yes"],
        )
        assert result.exit_code == 0
        assert "Removed from skills" in result.output
        assert len(store.load().skills) == 0

    def test_remove_not_found(self, runner: CliRunner, vault_dir: Path) -> None:
        # Exit 1 with the diagnosis on stderr: agents chain on exit codes
        # and must not read a missing item as success.
        fake_id = str(uuid4())
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "remove", fake_id, "--yes"],
        )
        assert result.exit_code == 1
        assert "Item not found" in result.stderr


# ------------------------------------------------------------------
# Taxonomy integration
# ------------------------------------------------------------------


class TestTaxonomyIntegration:
    def test_find_exact_python(self) -> None:
        entry = find_exact("Python")
        assert entry is not None
        assert entry.name == "Python"

    def test_find_exact_alias(self) -> None:
        entry = find_exact("python3")
        assert entry is not None
        assert entry.name == "Python"

    def test_find_exact_case_insensitive(self) -> None:
        entry = find_exact("PYTHON")
        assert entry is not None

    def test_find_exact_no_match(self) -> None:
        entry = find_exact("FooBarLang")
        assert entry is None

    def test_suggest_matches(self) -> None:
        suggestions = suggest_matches("script")
        # Should match JavaScript/TypeScript but not return exact
        names = [s.name for s in suggestions]
        assert any("Script" in n for n in names)


# ------------------------------------------------------------------
# VaultStore lens mutation surface (Phase 1)
# ------------------------------------------------------------------


class TestLensStore:
    def test_add_lens_creates_and_persists(self, store: VaultStore) -> None:
        lens = store.add_lens(slug="pm", name="Product")
        assert lens.slug == "pm"
        reloaded = store.load()
        assert [x.slug for x in reloaded.lenses] == ["pm"]
        assert (store.directory / "lenses.json").is_file()

    def test_add_lens_with_salience_and_signatures(self, store: VaultStore) -> None:
        skill = store.add_skill(name="Rust", proficiency=4, category="technical")
        exp = store.add_experience(title="Staff Eng", company="Acme", start_date="2020")
        lens = store.add_lens(
            slug="ic",
            name="IC",
            skill_salience={skill.id: SalienceLevel.CORE},
            signature_experience_ids=[exp.id],
            headline_override="Principal Engineer",
        )
        assert lens.salience_for(skill.id) == SalienceLevel.CORE
        assert lens.signature_experience_ids == [exp.id]
        assert lens.headline_override == "Principal Engineer"

    def test_add_lens_duplicate_slug_rejected(self, store: VaultStore) -> None:
        store.add_lens(slug="pm", name="Product")
        with pytest.raises(DuplicateLensSlugError):
            store.add_lens(slug="pm", name="Product Again")

    def test_add_lens_cap_enforced(self, store: VaultStore) -> None:
        for i in range(MAX_LENSES):
            store.add_lens(slug=f"lens-{i}", name=f"Lens {i}")
        with pytest.raises(LensCapError):
            store.add_lens(slug="one-too-many", name="Nope")
        assert len(store.load().lenses) == MAX_LENSES

    def test_add_default_flips_previous_default(self, store: VaultStore) -> None:
        store.add_lens(slug="a", name="A", is_default=True)
        store.add_lens(slug="b", name="B", is_default=True)
        vault = store.load()
        defaults = [x.slug for x in vault.lenses if x.is_default]
        assert defaults == ["b"]

    def test_update_lens_changes_fields(self, store: VaultStore) -> None:
        store.add_lens(slug="pm", name="Product")
        updated = store.update_lens("pm", name="Product Lead", bio_override="New bio")
        assert updated.name == "Product Lead"
        assert updated.bio_override == "New bio"
        assert store.load().lenses[0].name == "Product Lead"

    def test_update_lens_by_id(self, store: VaultStore) -> None:
        lens = store.add_lens(slug="pm", name="Product")
        store.update_lens(str(lens.id), name="Renamed")
        assert store.load().lenses[0].name == "Renamed"

    def test_update_lens_rename_slug_conflict_rejected(
        self, store: VaultStore
    ) -> None:
        store.add_lens(slug="a", name="A")
        store.add_lens(slug="b", name="B")
        with pytest.raises(DuplicateLensSlugError):
            store.update_lens("b", slug="a")

    def test_update_lens_set_default_flips_others(self, store: VaultStore) -> None:
        store.add_lens(slug="a", name="A", is_default=True)
        store.add_lens(slug="b", name="B")
        store.update_lens("b", is_default=True)
        vault = store.load()
        assert [x.slug for x in vault.lenses if x.is_default] == ["b"]

    def test_update_lens_not_found(self, store: VaultStore) -> None:
        with pytest.raises(LensNotFoundError):
            store.update_lens("ghost", name="X")

    def test_set_default_lens(self, store: VaultStore) -> None:
        store.add_lens(slug="a", name="A")
        store.add_lens(slug="b", name="B")
        store.set_default_lens("b")
        vault = store.load()
        assert [x.slug for x in vault.lenses if x.is_default] == ["b"]

    def test_remove_lens(self, store: VaultStore) -> None:
        store.add_lens(slug="pm", name="Product")
        removed = store.remove_lens("pm")
        assert removed is not None
        assert store.load().lenses == []

    def test_remove_last_lens_deletes_lenses_json(self, store: VaultStore) -> None:
        store.add_lens(slug="pm", name="Product")
        assert (store.directory / "lenses.json").is_file()
        store.remove_lens("pm")
        assert not (store.directory / "lenses.json").exists()

    def test_remove_lens_not_found_returns_none(self, store: VaultStore) -> None:
        assert store.remove_lens("ghost") is None


class TestLensCLI:
    def _add(self, runner: CliRunner, vault_dir: Path, *args: str):  # type: ignore[no-untyped-def]
        return runner.invoke(
            cli, ["--path", str(vault_dir), "vault", "lens", "add", *args]
        )

    def test_add_and_persist(self, runner: CliRunner, vault_dir: Path) -> None:
        result = self._add(runner, vault_dir, "--slug", "pm", "--name", "Product")
        assert result.exit_code == 0, result.output
        assert "Added lens: Product (pm)" in result.output
        assert [x.slug for x in VaultStore(vault_dir).load().lenses] == ["pm"]

    def test_add_requires_slug_and_name(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = self._add(runner, vault_dir, "--slug", "pm")
        assert result.exit_code == 2
        assert "--slug and --name are required" in result.output

    def test_add_sixth_lens_errors(self, runner: CliRunner, vault_dir: Path) -> None:
        for i in range(MAX_LENSES):
            assert (
                self._add(
                    runner, vault_dir, "--slug", f"lens-{i}", "--name", f"L{i}"
                ).exit_code
                == 0
            )
        result = self._add(runner, vault_dir, "--slug", "sixth", "--name", "Sixth")
        assert result.exit_code == 1
        assert "at most 5 lenses" in result.output

    def test_add_reserved_slug_rejected(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = self._add(runner, vault_dir, "--slug", "none", "--name", "Canonical")
        assert result.exit_code == 1
        assert "reserved" in result.output

    def test_add_with_salience(self, runner: CliRunner, vault_dir: Path) -> None:
        skill = VaultStore(vault_dir).add_skill(
            name="Rust", proficiency=4, category="technical"
        )
        result = self._add(
            runner,
            vault_dir,
            "--slug",
            "ic",
            "--name",
            "IC",
            "--salience",
            f"{skill.id}=core",
        )
        assert result.exit_code == 0, result.output
        lens = VaultStore(vault_dir).load().lenses[0]
        assert lens.salience_for(skill.id) == SalienceLevel.CORE

    def test_add_bad_salience_level(self, runner: CliRunner, vault_dir: Path) -> None:
        result = self._add(
            runner,
            vault_dir,
            "--slug",
            "ic",
            "--name",
            "IC",
            "--salience",
            f"{uuid4()}=turbo",
        )
        assert result.exit_code == 2
        assert "level must be one of" in result.output

    def test_add_batch_from_json(self, runner: CliRunner, vault_dir: Path) -> None:
        payload = json.dumps(
            [
                {"slug": "pm", "name": "Product"},
                {"slug": "ic", "name": "IC", "is_default": True},
            ]
        )
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "lens", "add", "--from-json", "-"],
            input=payload,
        )
        assert result.exit_code == 0, result.output
        assert "Summary: added 2, errors 0" in result.output
        vault = VaultStore(vault_dir).load()
        assert {x.slug for x in vault.lenses} == {"pm", "ic"}
        assert [x.slug for x in vault.lenses if x.is_default] == ["ic"]

    def test_add_batch_reports_cap(self, runner: CliRunner, vault_dir: Path) -> None:
        items = [{"slug": f"lens-{i}", "name": f"L{i}"} for i in range(MAX_LENSES + 1)]
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "lens", "add", "--from-json", "-"],
            input=json.dumps(items),
        )
        assert result.exit_code == 1
        assert "Summary: added 5, errors 1" in result.output

    def test_update(self, runner: CliRunner, vault_dir: Path) -> None:
        self._add(runner, vault_dir, "--slug", "pm", "--name", "Product")
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "lens",
                "update",
                "pm",
                "--name",
                "Product Lead",
            ],
        )
        assert result.exit_code == 0, result.output
        assert VaultStore(vault_dir).load().lenses[0].name == "Product Lead"

    def test_update_by_hex_prefix(self, runner: CliRunner, vault_dir: Path) -> None:
        lens = VaultStore(vault_dir).add_lens(slug="pm", name="Product")
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "lens",
                "update",
                lens.id.hex[:8],
                "--name",
                "Prefixed",
            ],
        )
        assert result.exit_code == 0, result.output
        assert VaultStore(vault_dir).load().lenses[0].name == "Prefixed"

    def test_update_unknown_lens(self, runner: CliRunner, vault_dir: Path) -> None:
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "lens",
                "update",
                "ghost",
                "--name",
                "X",
            ],
        )
        assert result.exit_code == 1
        assert "No lens matches" in result.output

    def test_set_default(self, runner: CliRunner, vault_dir: Path) -> None:
        self._add(runner, vault_dir, "--slug", "a", "--name", "A")
        self._add(runner, vault_dir, "--slug", "b", "--name", "B")
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "lens", "set-default", "b"],
        )
        assert result.exit_code == 0, result.output
        vault = VaultStore(vault_dir).load()
        assert [x.slug for x in vault.lenses if x.is_default] == ["b"]

    def test_remove_with_confirm(self, runner: CliRunner, vault_dir: Path) -> None:
        self._add(runner, vault_dir, "--slug", "pm", "--name", "Product")
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "lens", "remove", "pm", "--yes"],
        )
        assert result.exit_code == 0, result.output
        assert "Removed lens: pm" in result.output
        assert VaultStore(vault_dir).load().lenses == []
        assert not (vault_dir / "lenses.json").exists()

    def test_remove_unknown(self, runner: CliRunner, vault_dir: Path) -> None:
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "lens", "remove", "ghost", "--yes"],
        )
        assert result.exit_code == 1
        assert "No lens matches" in result.output
