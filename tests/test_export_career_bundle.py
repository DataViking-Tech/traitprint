"""Tests for the career-bundle exporter (multi-file working-dir bundle)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from traitprint.cli import cli
from traitprint.export import BUNDLE_FORMATS, export_vault, export_vault_bundle
from traitprint.exporters.career_bundle import vault_to_career_bundle
from traitprint.git_ops import commit, init_repo
from traitprint.schema import (
    ExperienceSchema,
    LensSchema,
    ProfileSchema,
    SalienceLevel,
    SkillSchema,
    StorySchema,
    VaultSchema,
)
from traitprint.vault import VaultStore


@pytest.fixture()
def sample_vault() -> VaultSchema:
    python = SkillSchema(name="Python", proficiency=4, category="technical")
    sql = SkillSchema(name="SQL", proficiency=5, category="technical")
    mentoring = SkillSchema(name="Mentoring", proficiency=3, category="soft")
    acme = ExperienceSchema(
        title="Staff Engineer",
        company="Acme",
        start_date="2020-01",
        end_date="",
        description="Led the data platform.",
        accomplishments=["Cut warehouse spend 45 percent"],
        skill_ids=[python.id, sql.id],
    )
    globex = ExperienceSchema(
        title="Engineer",
        company="Globex",
        start_date="2016-05",
        end_date="2019-12",
        description="Built pipelines.",
    )
    story = StorySchema(
        title="Warehouse migration",
        situation="Redshift costs ballooned.",
        task="I owned the migration.",
        action="I designed dual-writes and cut over.",
        result="Spend down 45 percent, zero downtime.",
        lesson="Dual-writes made the cutover boring.",
        theme_tags=["cost", "migration"],
        skill_ids=[sql.id],
        experience_id=acme.id,
    )
    lens = LensSchema(
        slug="data-lead",
        name="Data Lead",
        target_archetypes=["Data Platform Lead", "Analytics Engineering Manager"],
        headline_override="Data Platform Lead",
        bio_override="I build boring, reliable data platforms.",
        signature_experience_ids=[globex.id],
        skill_salience={
            mentoring.id: SalienceLevel.CORE,
            python.id: SalienceLevel.SUPPRESSED,
        },
        is_default=True,
    )
    other_lens = LensSchema(
        slug="ic-track",
        name="IC Track",
        target_archetypes=["Senior Data Engineer", "Data Platform Lead"],
    )
    return VaultSchema(
        profile=ProfileSchema(
            display_name="Ada Lovelace",
            headline="Engineer",
            summary="A decade of data.",
            location="London",
            contact_email="ada@example.com",
        ),
        skills=[python, sql, mentoring],
        experiences=[acme, globex],
        stories=[story],
        lenses=[lens, other_lens],
    )


class TestBundleShape:
    def test_three_files(self, sample_vault: VaultSchema) -> None:
        bundle = vault_to_career_bundle(sample_vault)
        assert set(bundle) == {
            "cv.md",
            "interview-prep/story-bank.md",
            "config/profile.yml",
        }
        assert all(content.endswith("\n") for content in bundle.values())

    def test_export_vault_bundle_routes(self, sample_vault: VaultSchema) -> None:
        assert BUNDLE_FORMATS == ("career-bundle",)
        bundle = export_vault_bundle(sample_vault, "career-bundle")
        assert "cv.md" in bundle

    def test_string_api_rejects_bundle_format(
        self, sample_vault: VaultSchema
    ) -> None:
        with pytest.raises(ValueError, match="multi-file bundle"):
            export_vault(sample_vault, "career-bundle")

    def test_unknown_bundle_format_rejected(
        self, sample_vault: VaultSchema
    ) -> None:
        with pytest.raises(ValueError, match="Unknown bundle format"):
            export_vault_bundle(sample_vault, "nope-bundle")


class TestCvMarkdown:
    def test_ats_header_renames_and_dropped_sections(
        self, sample_vault: VaultSchema
    ) -> None:
        cv = vault_to_career_bundle(sample_vault)["cv.md"]
        assert "## Professional Summary" in cv
        assert "## Work Experience" in cv
        assert "## Summary" not in cv.replace("## Professional Summary", "")
        # Only recruiter-assertable content: no stories, no philosophy.
        assert "## Stories" not in cv
        assert "## Philosophy" not in cv
        assert "## Skills" in cv
        assert "Cut warehouse spend 45 percent" in cv

    def test_canonical_skills_order_without_lens(
        self, sample_vault: VaultSchema
    ) -> None:
        cv = vault_to_career_bundle(sample_vault)["cv.md"]
        # Proficiency desc: SQL (5) before Python (4) before Mentoring (3).
        assert cv.index("**SQL**") < cv.index("**Python**") < cv.index("**Mentoring**")

    def test_lens_projection(self, sample_vault: VaultSchema) -> None:
        lens = sample_vault.lenses[0]
        cv = vault_to_career_bundle(sample_vault, lens=lens)["cv.md"]
        # Overrides applied.
        assert "_Data Platform Lead_" in cv
        assert "I build boring, reliable data platforms." in cv
        # Suppressed skill hidden; core skill leads.
        assert "**Python**" not in cv
        assert cv.index("**Mentoring**") < cv.index("**SQL**")
        # Signature experience (Globex) renders before Acme.
        assert cv.index("### Engineer — Globex") < cv.index("### Staff Engineer — Acme")

    def test_unlensed_cv_matches_canonical_content(
        self, sample_vault: VaultSchema
    ) -> None:
        cv = vault_to_career_bundle(sample_vault)["cv.md"]
        assert cv.startswith("# Ada Lovelace")
        assert "London · ada@example.com" in cv


class TestStoryBank:
    def test_star_block_fields(self, sample_vault: VaultSchema) -> None:
        bank = vault_to_career_bundle(sample_vault)["interview-prep/story-bank.md"]
        assert "### [cost] Warehouse migration" in bank
        assert "**Situation:** Redshift costs ballooned." in bank
        assert "**Task:** I owned the migration." in bank
        assert "**Action:** I designed dual-writes and cut over." in bank
        assert "**Result:** Spend down 45 percent, zero downtime." in bank
        assert "**Reflection:** Dual-writes made the cutover boring." in bank
        assert "**Source:** Staff Engineer — Acme" in bank

    def test_tags_include_theme_tags_and_resolved_skills(
        self, sample_vault: VaultSchema
    ) -> None:
        bank = vault_to_career_bundle(sample_vault)["interview-prep/story-bank.md"]
        assert "**Best for questions about:** cost, migration, SQL" in bank

    def test_dangling_skill_ref_skipped(self, sample_vault: VaultSchema) -> None:
        ghost = SkillSchema(name="Ghost", proficiency=1, category="x")
        sample_vault.stories[0].skill_ids.append(ghost.id)
        bank = vault_to_career_bundle(sample_vault)["interview-prep/story-bank.md"]
        assert "Ghost" not in bank

    def test_story_without_theme_or_lesson(self, sample_vault: VaultSchema) -> None:
        sample_vault.stories = [
            StorySchema(title="Bare", situation="s", task="t", action="a", result="r")
        ]
        bank = vault_to_career_bundle(sample_vault)["interview-prep/story-bank.md"]
        assert "### Bare" in bank
        assert "**Reflection:**" not in bank
        assert "**Source:**" not in bank


class TestProfileYaml:
    def test_round_trips_as_valid_yaml(self, sample_vault: VaultSchema) -> None:
        raw = vault_to_career_bundle(sample_vault)["config/profile.yml"]
        parsed = yaml.safe_load(raw)
        assert parsed["candidate"] == {
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "location": "London",
        }
        assert parsed["narrative"]["headline"] == "Engineer"
        assert parsed["narrative"]["summary"] == "A decade of data."

    def test_archetypes_default_lens_primary_others_secondary(
        self, sample_vault: VaultSchema
    ) -> None:
        raw = vault_to_career_bundle(sample_vault)["config/profile.yml"]
        parsed = yaml.safe_load(raw)
        archetypes = parsed["target_roles"]["archetypes"]
        by_name = {a["name"]: a["fit"] for a in archetypes}
        # Default lens's archetypes are primary; first occurrence wins.
        assert by_name["Data Platform Lead"] == "primary"
        assert by_name["Analytics Engineering Manager"] == "primary"
        assert by_name["Senior Data Engineer"] == "secondary"

    def test_placeholder_keys_are_comments(self, sample_vault: VaultSchema) -> None:
        raw = vault_to_career_bundle(sample_vault)["config/profile.yml"]
        assert "# phone:" in raw
        assert "# links:" in raw
        assert "# compensation:" in raw
        parsed = yaml.safe_load(raw)
        assert "links" not in parsed
        assert "compensation" not in parsed

    def test_no_lenses_emits_commented_target_roles(self) -> None:
        raw = vault_to_career_bundle(VaultSchema())["config/profile.yml"]
        parsed = yaml.safe_load(raw)
        assert parsed is not None
        assert "target_roles" not in parsed
        assert "# target_roles:" in raw

    def test_quoting_survives_special_characters(self) -> None:
        v = VaultSchema(
            profile=ProfileSchema(
                display_name='Ada "The Countess": Lovelace',
                summary="Line one\nLine two: with colon",
            )
        )
        raw = vault_to_career_bundle(v)["config/profile.yml"]
        parsed = yaml.safe_load(raw)
        assert parsed["candidate"]["name"] == 'Ada "The Countess": Lovelace'
        assert parsed["narrative"]["summary"] == "Line one\nLine two: with colon"


# ── CLI ─────────────────────────────────────────────────────────────


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def vault_dir(tmp_path: Path, sample_vault: VaultSchema) -> Path:
    d = tmp_path / "vault"
    d.mkdir()
    init_repo(d)
    VaultStore(d).save(sample_vault)
    commit(d, "seed")
    return d


class TestCliBundleExport:
    def test_writes_directory_tree(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "bundle"
        result = runner.invoke(
            cli,
            [
                "--path", str(vault_dir),
                "vault", "export", "-f", "career-bundle", "-o", str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        assert (out / "cv.md").is_file()
        assert (out / "interview-prep" / "story-bank.md").is_file()
        assert (out / "config" / "profile.yml").is_file()
        assert "3 files" in result.output

    def test_zip_archive(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "bundle.zip"
        result = runner.invoke(
            cli,
            [
                "--path", str(vault_dir),
                "vault", "export", "-f", "career-bundle", "--zip", "-o", str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        with zipfile.ZipFile(out) as zf:
            assert set(zf.namelist()) == {
                "cv.md",
                "interview-prep/story-bank.md",
                "config/profile.yml",
            }

    def test_requires_output(self, runner: CliRunner, vault_dir: Path) -> None:
        result = runner.invoke(
            cli, ["--path", str(vault_dir), "vault", "export", "-f", "career-bundle"]
        )
        assert result.exit_code == 2
        assert "-o DIR" in result.output

    def test_lens_flag(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "bundle"
        result = runner.invoke(
            cli,
            [
                "--path", str(vault_dir),
                "vault", "export", "-f", "career-bundle",
                "-o", str(out), "--lens", "data-lead",
            ],
        )
        assert result.exit_code == 0, result.output
        cv = (out / "cv.md").read_text()
        assert "_Data Platform Lead_" in cv

    def test_lens_flag_resolves_8hex_id_prefix(
        self,
        runner: CliRunner,
        vault_dir: Path,
        tmp_path: Path,
        sample_vault: VaultSchema,
    ) -> None:
        # CLI-only sugar (Q11.1): --lens accepts the first 8 hex chars of a
        # lens id, mirroring the strict slug / full-id grammar the MCP layer
        # uses. The default lens here is "data-lead".
        prefix = sample_vault.lenses[0].id.hex[:8]
        out = tmp_path / "bundle"
        result = runner.invoke(
            cli,
            [
                "--path", str(vault_dir),
                "vault", "export", "-f", "career-bundle",
                "-o", str(out), "--lens", prefix,
            ],
        )
        assert result.exit_code == 0, result.output
        cv = (out / "cv.md").read_text()
        # The data-lead lens's headline override proves the prefix resolved.
        assert "_Data Platform Lead_" in cv

    def test_unknown_lens_lists_available(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path", str(vault_dir),
                "vault", "export", "-f", "career-bundle",
                "-o", str(tmp_path / "b"), "--lens", "nope",
            ],
        )
        assert result.exit_code == 1
        assert "data-lead" in result.output

    def test_lens_rejected_for_string_formats(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--path", str(vault_dir),
                "vault", "export", "-f", "markdown", "--lens", "data-lead",
            ],
        )
        assert result.exit_code == 2
        assert "only supported with -f career-bundle" in result.output

    def test_top_level_alias_supports_bundle(
        self, runner: CliRunner, vault_dir: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "bundle"
        result = runner.invoke(
            cli,
            [
                "--path", str(vault_dir),
                "export", "-f", "career-bundle", "-o", str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        assert (out / "cv.md").is_file()

    def test_json_export_unchanged(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = runner.invoke(cli, ["--path", str(vault_dir), "vault", "export"])
        assert result.exit_code == 0
        assert json.loads(result.output)["profile"]["display_name"] == "Ada Lovelace"
