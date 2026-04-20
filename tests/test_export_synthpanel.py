"""Tests for the synthpanel-persona exporter and ``traitprint export``."""

from __future__ import annotations

from pathlib import Path

import yaml
from click.testing import CliRunner

from traitprint.cli import cli
from traitprint.exporters.synthpanel import (
    SYNTHPANEL_EXPORT_VERSION,
    vault_to_synthpanel_pack,
    vault_to_synthpanel_persona,
)
from traitprint.schema import (
    EducationSchema,
    ExperienceSchema,
    PhilosophyCategory,
    PhilosophySchema,
    ProfileSchema,
    SkillSchema,
    VaultSchema,
)


def _populated_vault() -> VaultSchema:
    return VaultSchema(
        profile=ProfileSchema(
            display_name="Jordan Rivera",
            headline="Staff Engineer",
            summary="Systems generalist with a soft spot for compilers.",
            location="Portland, OR",
            contact_email="jordan@example.com",
        ),
        skills=[
            SkillSchema(name="Rust", proficiency=9, category="technical"),
            SkillSchema(name="Python", proficiency=8, category="technical"),
            SkillSchema(name="Go", proficiency=6, category="technical"),
        ],
        experiences=[
            ExperienceSchema(
                title="Senior Engineer",
                company="Acme",
                start_date="2018-03",
                end_date="2021-08",
                description="Led platform migration.",
            ),
            ExperienceSchema(
                title="Staff Engineer",
                company="Globex",
                start_date="2021-09",
                end_date="",
                description="Owns identity and billing surfaces.",
            ),
        ],
        philosophies=[
            PhilosophySchema(
                title="Ship small, ship often",
                description="Prefer boring releases.",
                category=PhilosophyCategory.TECHNICAL_APPROACH,
            ),
            PhilosophySchema(
                title="Trust, then verify",
                description="Assume good faith, then measure.",
                category=PhilosophyCategory.LEADERSHIP,
            ),
        ],
        education=[
            EducationSchema(
                institution="MIT",
                degree="BS",
                field_of_study="Computer Science",
                start_date="2010",
                end_date="2014",
            )
        ],
    )


class TestVaultToPersona:
    def test_required_name_field_always_present(self) -> None:
        empty = VaultSchema()
        persona = vault_to_synthpanel_persona(empty)
        assert persona["name"] == "Anonymous"

    def test_display_name_used_when_set(self) -> None:
        persona = vault_to_synthpanel_persona(_populated_vault())
        assert persona["name"] == "Jordan Rivera"

    def test_occupation_prefers_headline(self) -> None:
        persona = vault_to_synthpanel_persona(_populated_vault())
        assert persona["occupation"] == "Staff Engineer"

    def test_occupation_falls_back_to_recent_experience(self) -> None:
        vault = _populated_vault()
        vault.profile.headline = ""
        persona = vault_to_synthpanel_persona(vault)
        assert persona["occupation"] == "Staff Engineer at Globex"

    def test_background_combines_summary_and_experience(self) -> None:
        persona = vault_to_synthpanel_persona(_populated_vault())
        bg = persona["background"]
        assert "Systems generalist" in bg
        assert "Staff Engineer at Globex" in bg
        assert "Owns identity and billing surfaces." in bg

    def test_personality_traits_derived_from_philosophies(self) -> None:
        traits = vault_to_synthpanel_persona(_populated_vault())["personality_traits"]
        assert "technical approach" in traits
        assert "leadership" in traits
        assert "ship small, ship often" in traits
        assert all(t == t.lower() for t in traits)

    def test_traits_omitted_when_no_philosophies(self) -> None:
        vault = _populated_vault()
        vault.philosophies = []
        persona = vault_to_synthpanel_persona(vault)
        assert "personality_traits" not in persona

    def test_top_skills_sorted_by_proficiency(self) -> None:
        skills = vault_to_synthpanel_persona(_populated_vault())["skills"]
        assert [s["name"] for s in skills] == ["Rust", "Python", "Go"]
        assert skills[0]["proficiency"] == 9

    def test_education_summary_uses_degree_and_institution(self) -> None:
        edu = vault_to_synthpanel_persona(_populated_vault())["education"]
        assert edu == ["BS Computer Science, MIT"]

    def test_empty_vault_still_valid_persona(self) -> None:
        persona = vault_to_synthpanel_persona(VaultSchema())
        # SynthPanel validate_persona_pack only requires `name`.
        assert set(persona.keys()) == {"name"}


class TestPackEnvelope:
    def test_pack_contains_single_persona(self) -> None:
        pack = vault_to_synthpanel_pack(_populated_vault())
        assert pack["personas"] and len(pack["personas"]) == 1
        assert pack["name"] == "Jordan Rivera"
        assert pack["source"] == "traitprint"
        assert pack["export_version"] == SYNTHPANEL_EXPORT_VERSION

    def test_pack_name_override(self) -> None:
        pack = vault_to_synthpanel_pack(_populated_vault(), pack_name="custom-pack")
        assert pack["name"] == "custom-pack"

    def test_pack_validates_against_synthpanel_contract(self) -> None:
        """Enforce the contract from SynthPanel's ``validate_persona_pack``.

        Required per persona: ``name``. ``personality_traits`` must be a
        list of non-empty strings when present. Reproducing the check
        here keeps the two products in lockstep without importing
        synthpanel as a test dep.
        """
        pack = vault_to_synthpanel_pack(_populated_vault())
        personas = pack["personas"]
        assert isinstance(personas, list) and personas
        for p in personas:
            assert isinstance(p, dict)
            assert str(p.get("name", "")).strip(), "name required"
            if "personality_traits" in p:
                traits = p["personality_traits"]
                assert isinstance(traits, list)
                assert all(isinstance(t, str) and t.strip() for t in traits)


class TestExportCli:
    def _init_vault(self, tmp_path: Path) -> Path:
        vault_dir = tmp_path / "vault"
        CliRunner().invoke(cli, ["--path", str(vault_dir), "init"])
        return vault_dir

    def test_export_requires_vault(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--path",
                str(tmp_path / "missing"),
                "export",
                "--format",
                "synthpanel-persona",
            ],
        )
        assert result.exit_code != 0
        assert "No vault found" in result.output

    def test_export_to_stdout(self, tmp_path: Path) -> None:
        vault_dir = self._init_vault(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "export",
                "--format",
                "synthpanel-persona",
            ],
        )
        assert result.exit_code == 0, result.output
        parsed = yaml.safe_load(result.output)
        assert parsed["source"] == "traitprint"
        assert parsed["personas"][0]["name"] == "Anonymous"

    def test_export_to_file(self, tmp_path: Path) -> None:
        vault_dir = self._init_vault(tmp_path)
        out = tmp_path / "persona.yaml"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "export",
                "--format",
                "synthpanel-persona",
                "--output",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        assert out.is_file()
        data = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert data["personas"][0]["name"] == "Anonymous"
        assert f"Wrote synthpanel-persona export to {out}" in result.output

    def test_unknown_format_rejected(self, tmp_path: Path) -> None:
        vault_dir = self._init_vault(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "export",
                "--format",
                "notreal",
            ],
        )
        assert result.exit_code != 0
        assert "notreal" in result.output or "Invalid value" in result.output
