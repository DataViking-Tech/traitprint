"""Tests for vault export formats (tp-h8m)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from traitprint.cli import cli
from traitprint.export import SUPPORTED_FORMATS, export_vault
from traitprint.git_ops import commit, init_repo
from traitprint.schema import (
    EducationSchema,
    ExperienceSchema,
    PhilosophyCategory,
    PhilosophySchema,
    ProfileLink,
    ProfileSchema,
    SkillSchema,
    StorySchema,
    VaultSchema,
)
from traitprint.vault import VaultStore


@pytest.fixture()
def sample_vault() -> VaultSchema:
    return VaultSchema(
        profile=ProfileSchema(
            display_name="Ada Lovelace",
            headline="Analytical Engine Programmer",
            summary="Pioneering computer scientist with a knack for poetic science.",
            location="London, UK",
            contact_email="ada@example.com",
        ),
        skills=[
            SkillSchema(name="Analytical thinking", proficiency=5, category="soft"),
            SkillSchema(name="Python", proficiency=4, category="technical"),
            SkillSchema(name="Mentoring", proficiency=3, category="soft"),
        ],
        experiences=[
            ExperienceSchema(
                title="Collaborator",
                company="Babbage Works",
                start_date="1842",
                end_date="1843",
                description="Translated and annotated the Analytical Engine paper.",
                accomplishments=["Wrote the first algorithm"],
            ),
            ExperienceSchema(
                title="Independent Researcher",
                company="Self",
                start_date="1844",
                end_date="",
                description="Ongoing research on computation.",
                accomplishments=[],
            ),
        ],
        stories=[
            StorySchema(
                title="First Algorithm",
                situation="Notes on Babbage's Analytical Engine",
                task="Describe how it could compute Bernoulli numbers",
                action="Wrote detailed table of operations",
                result="Laid foundation for computer programming",
            )
        ],
        philosophies=[
            PhilosophySchema(
                title="Poetic Science",
                description="Rigorous analysis benefits from imagination.",
                category=PhilosophyCategory.TECHNICAL_APPROACH,
            ),
            PhilosophySchema(
                title="Teach By Example",
                description="Clear worked examples beat abstract rules.",
                category=PhilosophyCategory.COLLABORATION,
            ),
        ],
        education=[
            EducationSchema(
                institution="Private Tutors",
                degree="Mathematics",
                field_of_study="Analysis",
                start_date="1833",
                end_date="1840",
                description="Studied under De Morgan.",
            )
        ],
    )


class TestExportJson:
    def test_round_trips_via_schema(self, sample_vault: VaultSchema) -> None:
        rendered = export_vault(sample_vault, "json")
        payload = json.loads(rendered)
        restored = VaultSchema.model_validate(payload)
        assert restored.profile.display_name == "Ada Lovelace"
        assert len(restored.skills) == 3
        assert len(restored.experiences) == 2
        assert (
            restored.philosophies[0].category == PhilosophyCategory.TECHNICAL_APPROACH
        )


class TestExportMarkdown:
    def test_renders_all_sections(self, sample_vault: VaultSchema) -> None:
        md = export_vault(sample_vault, "markdown")
        assert "# Ada Lovelace" in md
        assert "_Analytical Engine Programmer_" in md
        assert "London, UK · ada@example.com" in md
        assert "## Summary" in md
        assert "## Experience" in md
        assert "### Collaborator — Babbage Works" in md
        assert "1842 – 1843" in md
        assert "- Wrote the first algorithm" in md
        # Current role shows "Present"
        assert "1844 – Present" in md
        assert "## Education" in md
        assert "## Skills" in md
        # Skills sorted by proficiency desc
        first_skill_idx = md.index("Analytical thinking")
        second_skill_idx = md.index("Python")
        assert first_skill_idx < second_skill_idx
        assert "## Stories" in md
        assert "**Situation.**" in md
        assert "## Philosophy" in md
        assert "### Poetic Science (technical-approach)" in md

    def test_empty_vault_renders(self) -> None:
        md = export_vault(VaultSchema(), "markdown")
        assert md.startswith("# Traitprint\n")


class TestExportJsonResume:
    def test_structure_matches_spec(self, sample_vault: VaultSchema) -> None:
        rendered = export_vault(sample_vault, "jsonresume")
        payload = json.loads(rendered)
        assert "jsonresume" in payload["$schema"]
        basics = payload["basics"]
        assert basics["name"] == "Ada Lovelace"
        assert basics["label"] == "Analytical Engine Programmer"
        assert basics["email"] == "ada@example.com"
        assert basics["location"] == {"address": "London, UK"}

        work = payload["work"]
        assert work[0]["name"] == "Babbage Works"
        assert work[0]["position"] == "Collaborator"
        assert work[0]["startDate"] == "1842-01-01"
        assert work[0]["endDate"] == "1843-01-01"
        assert work[0]["highlights"] == ["Wrote the first algorithm"]
        # Current role end date is empty
        assert work[1]["endDate"] == ""

        edu = payload["education"][0]
        assert edu["institution"] == "Private Tutors"
        assert edu["studyType"] == "Mathematics"
        assert edu["area"] == "Analysis"

        skills = payload["skills"]
        assert skills[0]["name"] == "Analytical thinking"
        assert skills[0]["level"] == "Master"
        assert skills[0]["keywords"] == ["soft"]

        projects = payload["projects"]
        assert projects[0]["name"] == "First Algorithm"
        assert projects[0]["highlights"][0].startswith("Wrote detailed")

    def test_normalizes_yyyy_mm_dates(self) -> None:
        vault = VaultSchema(
            experiences=[
                ExperienceSchema(
                    title="Engineer",
                    company="Acme",
                    start_date="2020-03",
                    end_date="2022-11",
                )
            ]
        )
        payload = json.loads(export_vault(vault, "jsonresume"))
        assert payload["work"][0]["startDate"] == "2020-03-01"
        assert payload["work"][0]["endDate"] == "2022-11-01"

    def test_emits_phone_url_and_profiles(
        self, sample_vault: VaultSchema
    ) -> None:
        # Contract rev 1.3: basics.phone/url/profiles come from the vault
        # instead of the pre-1.3 hardcoded empty profiles array.
        sample_vault.profile.phone = "+44 20 7946 0000"
        sample_vault.profile.url = "https://ada.example.com"
        sample_vault.profile.profiles = [
            ProfileLink(
                network="github", username="ada", url="https://github.com/ada"
            ),
            ProfileLink(network="mastodon", username="ada"),
        ]
        basics = json.loads(export_vault(sample_vault, "jsonresume"))["basics"]
        assert basics["phone"] == "+44 20 7946 0000"
        assert basics["url"] == "https://ada.example.com"
        assert basics["profiles"] == [
            {
                "network": "github",
                "username": "ada",
                "url": "https://github.com/ada",
            },
            {"network": "mastodon", "username": "ada"},
        ]

    def test_profiles_empty_without_links(
        self, sample_vault: VaultSchema
    ) -> None:
        basics = json.loads(export_vault(sample_vault, "jsonresume"))["basics"]
        assert basics["profiles"] == []
        assert basics["phone"] == ""
        assert basics["url"] == ""


class TestExportSynthpanelPersona:
    """``export_vault(..., "synthpanel-persona")`` delegates to
    ``vault_to_synthpanel_pack``. These tests verify the YAML pack
    contract; deeper persona-derivation tests live in
    ``tests/test_export_synthpanel.py``."""

    def test_emits_valid_yaml_pack(self, sample_vault: VaultSchema) -> None:
        import yaml

        rendered = export_vault(sample_vault, "synthpanel-persona")
        parsed = yaml.safe_load(rendered)
        assert parsed["source"] == "traitprint"
        assert parsed["personas"][0]["name"] == "Ada Lovelace"

    def test_pack_name_kwarg_overrides_default(self, sample_vault: VaultSchema) -> None:
        import yaml

        rendered = export_vault(
            sample_vault, "synthpanel-persona", pack_name="custom-pack"
        )
        parsed = yaml.safe_load(rendered)
        assert parsed["name"] == "custom-pack"

    def test_anonymous_empty_vault(self) -> None:
        import yaml

        rendered = export_vault(VaultSchema(), "synthpanel-persona")
        parsed = yaml.safe_load(rendered)
        assert parsed["personas"][0]["name"] == "Anonymous"


class TestUnknownFormat:
    def test_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown export format"):
            export_vault(VaultSchema(), "xml")

    def test_supported_formats_constant(self) -> None:
        assert set(SUPPORTED_FORMATS) == {
            "json",
            "markdown",
            "jsonresume",
            "synthpanel-persona",
        }


# ------------------------------------------------------------------
# CLI integration
# ------------------------------------------------------------------


@pytest.fixture()
def vault_dir(tmp_path: Path, sample_vault: VaultSchema) -> Path:
    d = tmp_path / "vault"
    d.mkdir()
    init_repo(d)
    store = VaultStore(d)
    store.save(sample_vault)
    commit(d, "test seed")
    return d


class TestCliExport:
    def test_default_json_to_stdout(self, vault_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--path", str(vault_dir), "vault", "export"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["profile"]["display_name"] == "Ada Lovelace"

    def test_markdown_stdout(self, vault_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "export",
                "--format",
                "markdown",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "# Ada Lovelace" in result.output

    def test_writes_to_output_file(self, vault_dir: Path, tmp_path: Path) -> None:
        out = tmp_path / "resume.json"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "export",
                "-f",
                "jsonresume",
                "-o",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        assert out.is_file()
        payload = json.loads(out.read_text())
        assert payload["basics"]["name"] == "Ada Lovelace"
        assert "Wrote jsonresume export to" in result.output

    def test_persona_format_via_cli(self, vault_dir: Path) -> None:
        import yaml

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "export",
                "-f",
                "synthpanel-persona",
            ],
        )
        assert result.exit_code == 0, result.output
        parsed = yaml.safe_load(result.output)
        assert parsed["source"] == "traitprint"
        assert parsed["personas"][0]["name"] == "Ada Lovelace"

    def test_missing_vault_shows_hint(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--path", str(tmp_path / "nope"), "vault", "export"]
        )
        assert result.exit_code == 0
        assert "No vault found" in result.output

    def test_json_resume_alias_for_jsonresume(self, vault_dir: Path) -> None:
        """`--format json-resume` is normalized to `jsonresume`."""
        runner = CliRunner()
        canonical = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "export", "-f", "jsonresume"],
        )
        aliased = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "export", "-f", "json-resume"],
        )
        assert canonical.exit_code == 0, canonical.output
        assert aliased.exit_code == 0, aliased.output
        assert canonical.output == aliased.output

    def test_top_level_export_aliases_vault_export(self, vault_dir: Path) -> None:
        """Top-level `traitprint export` and `traitprint vault export`
        produce identical output for every supported format."""
        runner = CliRunner()
        for fmt in ("json", "markdown", "jsonresume", "synthpanel-persona"):
            top = runner.invoke(cli, ["--path", str(vault_dir), "export", "-f", fmt])
            vault = runner.invoke(
                cli, ["--path", str(vault_dir), "vault", "export", "-f", fmt]
            )
            assert top.exit_code == 0, top.output
            assert vault.exit_code == 0, vault.output
            assert top.output == vault.output, f"mismatch for {fmt}"

    def test_top_level_export_json_resume_alias(self, vault_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["--path", str(vault_dir), "export", "-f", "json-resume"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["basics"]["name"] == "Ada Lovelace"

    def test_invalid_format_rejected(self, vault_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "export",
                "-f",
                "xml",
            ],
        )
        assert result.exit_code != 0
        lowered = result.output.lower()
        assert "invalid value" in lowered or "invalid choice" in lowered
