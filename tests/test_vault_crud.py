"""Tests for Slice B — Vault CRUD operations."""

from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from traitprint.cli import cli
from traitprint.git_ops import commit, init_repo
from traitprint.schema import PhilosophyCategory, SkillSchema
from traitprint.taxonomy import find_exact, suggest_matches
from traitprint.vault import DuplicateSkillError, VaultStore

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
            name="Python", proficiency=8, category="technical", notes="Primary lang"
        )
        assert skill.name == "Python"
        assert skill.proficiency == 8
        assert skill.category == "technical"
        assert skill.notes == "Primary lang"
        assert isinstance(skill.id, UUID)
        assert skill.created_at is not None

    def test_skill_persisted_to_disk(self, store: VaultStore) -> None:
        store.add_skill(name="Go", proficiency=6, category="technical")
        vault = store.load()
        assert len(vault.skills) == 1
        assert vault.skills[0].name == "Go"

    def test_auto_commits_to_git(self, store: VaultStore) -> None:
        store.add_skill(name="SQL", proficiency=9, category="technical")
        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=str(store.directory),
            capture_output=True,
            text=True,
            check=False,
        )
        assert "Add skill: SQL (9/10)" in result.stdout

    def test_multiple_skills_accumulate(self, store: VaultStore) -> None:
        store.add_skill(name="A", proficiency=1, category="x")
        store.add_skill(name="B", proficiency=2, category="y")
        vault = store.load()
        assert len(vault.skills) == 2

    def test_taxonomy_id_set_when_provided(self, store: VaultStore) -> None:
        tid = uuid4()
        skill = store.add_skill(
            name="Test", proficiency=5, category="x", taxonomy_id=tid
        )
        assert skill.taxonomy_id == tid

    def test_duplicate_name_rejected(self, store: VaultStore) -> None:
        first = store.add_skill(name="Python", proficiency=8, category="technical")
        with pytest.raises(DuplicateSkillError) as exc_info:
            store.add_skill(name="Python", proficiency=5, category="technical")
        assert exc_info.value.existing_id == first.id
        # Duplicate was not appended.
        assert len(store.load().skills) == 1

    def test_duplicate_name_case_insensitive(self, store: VaultStore) -> None:
        store.add_skill(name="Python", proficiency=8, category="technical")
        with pytest.raises(DuplicateSkillError):
            store.add_skill(name="python", proficiency=5, category="technical")
        with pytest.raises(DuplicateSkillError):
            store.add_skill(name="  PYTHON  ", proficiency=5, category="technical")
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
        skill = store.add_skill(name="Python", proficiency=8, category="tech")
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
        skill = store.add_skill(name="Rust", proficiency=7, category="technical")
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

    def test_proficiency_10_ok(self) -> None:
        s = SkillSchema(name="Test", proficiency=10)
        assert s.proficiency == 10

    def test_proficiency_0_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SkillSchema(name="Test", proficiency=0)

    def test_proficiency_11_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SkillSchema(name="Test", proficiency=11)


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
        assert "2 skills" in result.output
        assert "0 experiences" in result.output

    def test_no_vault(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(cli, ["--path", str(tmp_path / "nope"), "vault", "show"])
        assert "No vault found" in result.output

    def test_vault_show_verbose_contents(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        store.add_skill(
            name="Python",
            proficiency=8,
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
        assert "proficiency: 8/10" in out
        assert "Primary lang" in out
        assert "Experiences (1)" in out
        assert "Staff Engineer" in out
        assert "Acme" in out
        assert "Shipped v2" in out
        assert "Philosophies (1)" in out
        assert "Small PRs" in out
        assert "technical-approach" in out
        assert "Git:" in out

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
        store.add_skill(name="Python", proficiency=8, category="technical")
        result = runner.invoke(
            cli, ["--path", str(vault_dir), "vault", "list", "skills"]
        )
        assert result.exit_code == 0
        assert "Python" in result.output
        assert "technical" in result.output

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
                "8",
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
            "8",
            "--category",
            "technical",
        ]
        first = runner.invoke(cli, args)
        assert first.exit_code == 0
        second = runner.invoke(cli, args)
        assert second.exit_code == 1
        assert "already exists" in second.output
        assert len(VaultStore(vault_dir).load().skills) == 1

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
        skill = store.add_skill(name="Go", proficiency=8, category="technical")
        exp = store.add_experience(
            title="Eng", company="Co", start_date="2020-01"
        )
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

    def test_non_interactive_missing_category_errors(
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
                "Incomplete",
                "--description",
                "No category provided.",
            ],
        )
        assert result.exit_code != 0
        assert "--category is required" in result.output

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
# CLI: vault history / diff / rollback
# ------------------------------------------------------------------


class TestHistoryDiffRollback:
    def test_history_shows_commits(self, runner: CliRunner, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        store.add_skill(name="Go", proficiency=7, category="technical")
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
# CLI: vault remove
# ------------------------------------------------------------------


class TestRemoveCLI:
    def test_remove_with_confirm(self, runner: CliRunner, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        skill = store.add_skill(name="Rust", proficiency=7, category="technical")
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "remove", str(skill.id), "--yes"],
        )
        assert result.exit_code == 0
        assert "Removed from skills" in result.output
        assert len(store.load().skills) == 0

    def test_remove_not_found(self, runner: CliRunner, vault_dir: Path) -> None:
        fake_id = str(uuid4())
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "remove", fake_id, "--yes"],
        )
        assert "Item not found" in result.output


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
