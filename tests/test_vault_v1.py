"""Tests for the vault v1 file-tree format, the v0 reader, and migration.

Covers the contract in ``docs/schema/vault-v1/``: layout, frontmatter
key allowlists, markdown body conventions (STAR headings), slug
collision handling, filename stability across renames, v0→v1 migration
(including the 1-10 → 1-5 proficiency remap and link preservation), and
git history/rollback over the whole tree.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from click.testing import CliRunner

from traitprint.cli import cli
from traitprint.git_ops import commit, init_repo
from traitprint.git_ops import log as git_log
from traitprint.schema import (
    EducationSchema,
    ExperienceSchema,
    ExperienceScope,
    PhilosophySchema,
    ProfileLink,
    ProfileSchema,
    SkillSchema,
    StorySchema,
    VaultSchema,
)
from traitprint.vault import VaultStore
from traitprint.vault_io import (
    EXPERIENCE_FRONTMATTER_KEYS,
    PHILOSOPHY_FRONTMATTER_KEYS,
    STORY_FRONTMATTER_KEYS,
    parse_markdown,
    parse_story_body,
    remap_proficiency,
    render_markdown,
    render_story_body,
    slugify,
)

# ── Fixtures / helpers ──────────────────────────────────────────────


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    d = tmp_path / "vault"
    d.mkdir()
    init_repo(d)
    return d


def _full_vault() -> VaultSchema:
    skill = SkillSchema(name="Python", proficiency=5, category="technical")
    exp = ExperienceSchema(
        title="Staff Engineer",
        company="Acme",
        start_date="2020-01",
        end_date="2024-06",
        description="Led the data platform.\n\nOwned cost and reliability.",
        accomplishments=["Cut spend 45%", "Zero-downtime migration"],
        skill_ids=[skill.id],
    )
    story = StorySchema(
        title="Redshift to BigQuery",
        situation="Costs were ballooning on growing volume.",
        task="Migrate without downtime.",
        action="Ran dual-writes and cut over carefully.",
        result="Cut warehouse spend 45 percent.",
        lesson="Dual-writes beat big-bang cutovers.",
        outcome="win",
        theme_tags=["migration", "cost"],
        skill_ids=[skill.id],
        experience_id=exp.id,
    )
    phil = PhilosophySchema(
        title="Boring releases",
        description="Prefer small, reversible changes.",
        category="technical-approach",
        evidence_story_ids=[story.id],
    )
    edu = EducationSchema(
        institution="State U", degree="BS", field_of_study="CS",
        start_date="2010", end_date="2014",
    )
    return VaultSchema(
        profile=ProfileSchema(
            display_name="Ada Lovelace",
            headline="Engineer",
            summary="A decade of data.",
            location="London",
            contact_email="ada@example.com",
        ),
        skills=[skill],
        experiences=[exp],
        stories=[story],
        philosophies=[phil],
        education=[edu],
    )


def _tree_bytes(directory: Path) -> dict[str, bytes]:
    """Every tracked vault file's exact bytes, keyed by relative path."""
    return {
        str(p.relative_to(directory)): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    }


# ── slugify / remap unit tests ──────────────────────────────────────


class TestSlugify:
    def test_kebab_case(self) -> None:
        assert slugify("Redshift to BigQuery Migration") == (
            "redshift-to-bigquery-migration"
        )

    def test_strips_punctuation(self) -> None:
        assert slugify("Ship it! (v2.0)") == "ship-it-v2-0"

    def test_never_empty(self) -> None:
        assert slugify("???") == "untitled"
        assert slugify("") == "untitled"


class TestRemapProficiency:
    @pytest.mark.parametrize(
        ("v0", "v1"),
        [(1, 1), (2, 1), (3, 2), (4, 2), (5, 3), (6, 3), (7, 4), (8, 4), (9, 5),
         (10, 5)],
    )
    def test_ceil_halving(self, v0: int, v1: int) -> None:
        assert remap_proficiency(v0) == v1


# ── v1 round-trip ───────────────────────────────────────────────────


class TestV1RoundTrip:
    def test_save_load_equality(self, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        vault = _full_vault()
        store.save(vault)
        loaded = store.load()
        assert loaded.model_dump(mode="json") == vault.model_dump(mode="json")

    def test_layout_matches_contract(self, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        store.save(_full_vault())
        assert (vault_dir / "traitprint.json").is_file()
        assert (vault_dir / "profile.json").is_file()
        assert (vault_dir / "skills.json").is_file()
        assert (vault_dir / "education.json").is_file()
        assert (vault_dir / "experiences" / "staff-engineer-acme.md").is_file()
        assert (vault_dir / "stories" / "redshift-to-bigquery.md").is_file()
        assert (vault_dir / "philosophies" / "boring-releases.md").is_file()
        assert not (vault_dir / "vault.json").exists()

    def test_manifest_shape(self, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        vault = _full_vault()
        store.save(vault)
        manifest = json.loads((vault_dir / "traitprint.json").read_text())
        assert set(manifest) == {"schema_version", "vault_id", "updated_at"}
        assert manifest["schema_version"] == 1
        assert UUID(manifest["vault_id"]) == vault.vault_id
        assert manifest["updated_at"].endswith("Z")

    def test_profile_json_resume_basics(self, vault_dir: Path) -> None:
        # No rev-1.3 fields set: profile.json stays byte-compatible with
        # pre-1.3 output (phone/url/profiles keys omitted while empty).
        store = VaultStore(vault_dir)
        store.save(_full_vault())
        profile = json.loads((vault_dir / "profile.json").read_text())
        assert profile == {
            "basics": {
                "name": "Ada Lovelace",
                "label": "Engineer",
                "summary": "A decade of data.",
                "email": "ada@example.com",
                "location": "London",
            }
        }

    def test_profile_rev_1_3_fields_round_trip(self, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        vault = _full_vault()
        vault.profile.phone = "+44 20 7946 0000"
        vault.profile.url = "https://ada.example.com"
        vault.profile.profiles = [
            ProfileLink(
                network="github", username="ada", url="https://github.com/ada"
            ),
            ProfileLink(network="linkedin", url="https://linkedin.com/in/ada"),
        ]
        store.save(vault)

        on_disk = json.loads((vault_dir / "profile.json").read_text())
        assert on_disk["basics"]["phone"] == "+44 20 7946 0000"
        assert on_disk["basics"]["url"] == "https://ada.example.com"
        assert on_disk["basics"]["profiles"] == [
            {
                "network": "github",
                "username": "ada",
                "url": "https://github.com/ada",
            },
            {
                "network": "linkedin",
                "username": "",
                "url": "https://linkedin.com/in/ada",
            },
        ]

        loaded = store.load()
        assert loaded.profile == vault.profile

    def test_pre_1_3_profile_json_reads_with_defaults(
        self, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        store.save(_full_vault())
        # Rewrite profile.json exactly as a pre-1.3 writer would emit it.
        (vault_dir / "profile.json").write_text(
            json.dumps(
                {
                    "basics": {
                        "name": "Ada Lovelace",
                        "label": "Engineer",
                        "summary": "A decade of data.",
                        "email": "ada@example.com",
                        "location": "London",
                    }
                },
                indent=2,
            )
            + "\n"
        )
        profile = store.load().profile
        assert profile.phone == ""
        assert profile.url == ""
        assert profile.profiles == []

    def test_vault_without_scope_round_trips_byte_identically(
        self, vault_dir: Path
    ) -> None:
        """Contract revision 1.5 is additive: a vault that never set an
        experience scope must read+write byte-identically — no scope key
        (and no ``scope: null``) may appear on rewrite."""
        store = VaultStore(vault_dir)
        store.save(_full_vault())
        before = _tree_bytes(vault_dir)
        loaded = store.load()
        assert loaded.experiences[0].scope is None
        store.save(loaded, bump_updated_at=False)
        assert _tree_bytes(vault_dir) == before
        exp_text = (
            vault_dir / "experiences" / "staff-engineer-acme.md"
        ).read_text()
        assert "scope" not in exp_text

    def test_experience_scope_round_trips_byte_identically(
        self, vault_dir: Path
    ) -> None:
        """A full scope block survives save→load→save with exact bytes."""
        store = VaultStore(vault_dir)
        vault = _full_vault()
        vault.experiences[0].scope = ExperienceScope(
            reporting_line="VP of Data",
            direct_reports=6,
            indirect_reports=24,
            managers_led=2,
            functions_owned=["analytics eng", "data platform"],
            budget_authority="co-managed $2M vendor budget",
            hiring_authority=True,
            decision_rights="architecture + tooling standards",
            platform_scale="2,500+ dbt models, 30-person data org",
            org_context="public co, ~7k employees, 40-person data org",
        )
        store.save(vault)
        before = _tree_bytes(vault_dir)
        loaded = store.load()
        assert loaded.experiences[0].scope == vault.experiences[0].scope
        store.save(loaded, bump_updated_at=False)
        assert _tree_bytes(vault_dir) == before

    def test_partial_scope_frontmatter_carries_only_set_fields(
        self, vault_dir: Path
    ) -> None:
        """Only set scope fields reach the frontmatter — unset fields are
        absent, never ``null`` (0/False are set values and are kept)."""
        store = VaultStore(vault_dir)
        vault = _full_vault()
        vault.experiences[0].scope = ExperienceScope(
            reporting_line="CTO", direct_reports=0, hiring_authority=False
        )
        store.save(vault)
        fm, _ = parse_markdown(
            (vault_dir / "experiences" / "staff-engineer-acme.md").read_text()
        )
        assert fm["scope"] == {
            "reporting_line": "CTO",
            "direct_reports": 0,
            "hiring_authority": False,
        }
        loaded = store.load()
        assert loaded.experiences[0].scope == vault.experiences[0].scope

    def test_hand_edited_scope_loads(self, vault_dir: Path) -> None:
        """Agents hand-edit these files; a scope mapping added by hand
        must load (and an all-empty one normalizes to absent)."""
        store = VaultStore(vault_dir)
        store.save(_full_vault())
        path = vault_dir / "experiences" / "staff-engineer-acme.md"
        fm, body = parse_markdown(path.read_text(), path=path)
        fm["scope"] = {"reporting_line": "VP of Data", "direct_reports": 6}
        path.write_text(render_markdown(fm, body))
        loaded = store.load()
        assert loaded.experiences[0].scope == ExperienceScope(
            reporting_line="VP of Data", direct_reports=6
        )

    def test_deleting_entity_deletes_its_file(self, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        vault = _full_vault()
        store.save(vault)
        story_file = vault_dir / "stories" / "redshift-to-bigquery.md"
        assert story_file.is_file()
        vault.stories = []
        vault.philosophies[0].evidence_story_ids = []
        store.save(vault)
        assert not story_file.exists()

    def test_unchanged_files_not_rewritten(self, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        vault = _full_vault()
        store.save(vault)
        story_file = vault_dir / "stories" / "redshift-to-bigquery.md"
        before = story_file.stat().st_mtime_ns
        # Mutate an unrelated section and save again.
        vault.skills.append(SkillSchema(name="Go", proficiency=3, category="technical"))
        store.save(vault)
        assert story_file.stat().st_mtime_ns == before

    def test_custom_md_survives_save_and_load(self, vault_dir: Path) -> None:
        """A user-owned custom.md at the vault root is never touched.

        The package treats custom.md as read-only (user customization
        layer): saves must not rewrite or delete it, and reads must not
        choke on it.
        """
        store = VaultStore(vault_dir)
        vault = _full_vault()
        store.save(vault)
        custom = vault_dir / "custom.md"
        custom.write_text("## House Rules\n\nAlways be terse.\n")
        before = custom.stat().st_mtime_ns
        vault.skills.append(
            SkillSchema(name="Rust", proficiency=2, category="technical")
        )
        store.save(vault)
        assert custom.read_text() == "## House Rules\n\nAlways be terse.\n"
        assert custom.stat().st_mtime_ns == before
        store.load()  # a root-level custom.md is not a vault entity


# ── markdown conventions ────────────────────────────────────────────


class TestMarkdownBodies:
    def test_star_headings_in_order(self, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        store.save(_full_vault())
        text = (vault_dir / "stories" / "redshift-to-bigquery.md").read_text()
        idx = [text.index(f"## {h}") for h in ("Situation", "Task", "Action", "Result")]
        assert idx == sorted(idx)
        assert "## Lesson" in text
        assert text.index("## Lesson") > idx[-1]

    def test_star_body_round_trip(self) -> None:
        story = StorySchema(
            title="T",
            situation="Line one.\n\nLine two.",
            task="The task.",
            action="The action.",
            result="The result.",
            lesson="The lesson.",
        )
        fields = parse_story_body(render_story_body(story))
        assert fields["situation"] == "Line one.\n\nLine two."
        assert fields["task"] == "The task."
        assert fields["action"] == "The action."
        assert fields["result"] == "The result."
        assert fields["lesson"] == "The lesson."

    def test_lesson_omitted_when_empty(self) -> None:
        story = StorySchema(title="T", situation="s", task="t", action="a", result="r")
        body = render_story_body(story)
        assert "## Lesson" not in body
        # All four required headings present even for terse stories.
        for heading in ("Situation", "Task", "Action", "Result"):
            assert f"## {heading}" in body

    def test_experience_body_is_description(self, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        store.save(_full_vault())
        _, body = parse_markdown(
            (vault_dir / "experiences" / "staff-engineer-acme.md").read_text()
        )
        assert body == "Led the data platform.\n\nOwned cost and reliability."

    def test_philosophy_body_is_stance(self, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        store.save(_full_vault())
        _, body = parse_markdown(
            (vault_dir / "philosophies" / "boring-releases.md").read_text()
        )
        assert body == "Prefer small, reversible changes."

    def test_experience_skill_ids_in_frontmatter_round_trip(
        self, vault_dir: Path
    ) -> None:
        """Contract revision 1.1: experience skill_ids live in frontmatter
        and survive a save→load round trip."""
        store = VaultStore(vault_dir)
        vault = _full_vault()
        store.save(vault)
        fm, _ = parse_markdown(
            (vault_dir / "experiences" / "staff-engineer-acme.md").read_text()
        )
        assert fm["skill_ids"] == [str(vault.skills[0].id)]
        loaded = store.load()
        assert loaded.experiences[0].skill_ids == [vault.skills[0].id]

    def test_experience_without_skill_ids_key_still_loads(
        self, vault_dir: Path
    ) -> None:
        """Pre-1.1 experience files omit skill_ids — they must keep loading
        (additive contract change; missing key reads as empty list)."""
        store = VaultStore(vault_dir)
        store.save(_full_vault())
        path = vault_dir / "experiences" / "staff-engineer-acme.md"
        # Strip the skill_ids key from the frontmatter, as a pre-1.1 file.
        fm, body = parse_markdown(path.read_text(), path=path)
        fm.pop("skill_ids")
        path.write_text(render_markdown(fm, body))
        loaded = store.load()
        assert loaded.experiences[0].skill_ids == []

    def test_hand_edited_unquoted_date_still_loads(self, vault_dir: Path) -> None:
        """Agents hand-edit these files; YAML-native dates must not break."""
        store = VaultStore(vault_dir)
        vault = _full_vault()
        store.save(vault)
        path = vault_dir / "experiences" / "staff-engineer-acme.md"
        text = path.read_text().replace("start_date: 2020-01", "start_date: 2020-01-15")
        path.write_text(text)
        loaded = store.load()
        assert loaded.experiences[0].start_date == "2020-01-15"


class TestFrontmatterContract:
    def test_only_allowed_keys_per_contract(self, vault_dir: Path) -> None:
        """additionalProperties is false in the contract — frontmatter must
        contain exactly the allowed keys; narrative lives in the body."""
        store = VaultStore(vault_dir)
        store.save(_full_vault())
        allowed = {
            "experiences": set(EXPERIENCE_FRONTMATTER_KEYS),
            "stories": set(STORY_FRONTMATTER_KEYS),
            "philosophies": set(PHILOSOPHY_FRONTMATTER_KEYS),
        }
        # Optional, additive collections are omitted when empty (revision
        # 1.2's skill_links keeps 1.1 vaults byte-identical), so a saved
        # file may lack them — every present key must still be allowed.
        from traitprint.vault_io import _OMIT_WHEN_EMPTY

        omittable = set(_OMIT_WHEN_EMPTY)
        for section, keys in allowed.items():
            for file in (vault_dir / section).glob("*.md"):
                fm, body = parse_markdown(file.read_text(), path=file)
                assert set(fm) <= keys, f"{file} frontmatter has disallowed keys"
                assert keys - set(fm) <= omittable, (
                    f"{file} frontmatter missing required keys"
                )
                # Narrative text must not leak into frontmatter.
                for narrative_key in ("description", "situation", "stance", "lesson"):
                    assert narrative_key not in fm


# ── slugs: collisions and stability ─────────────────────────────────


class TestSlugCollisions:
    def test_collision_appends_id_prefix(self, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        vault = VaultSchema()
        s1 = StorySchema(
            title="Launch", situation="a", task="b", action="c", result="d"
        )
        s2 = StorySchema(
            title="Launch", situation="e", task="f", action="g", result="h"
        )
        vault.stories = [s1, s2]
        store.save(vault)
        names = sorted(p.name for p in (vault_dir / "stories").glob("*.md"))
        assert "launch.md" in names
        assert f"launch-{s2.id.hex[:8]}.md" in names
        loaded = store.load()
        assert {st.id for st in loaded.stories} == {s1.id, s2.id}

    def test_experience_slug_includes_company(self, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        vault = VaultSchema(
            experiences=[
                ExperienceSchema(title="Engineer", company="Acme"),
                ExperienceSchema(title="Engineer", company="Globex"),
            ]
        )
        store.save(vault)
        names = {p.name for p in (vault_dir / "experiences").glob("*.md")}
        assert names == {"engineer-acme.md", "engineer-globex.md"}


class TestFilenameStability:
    def test_title_change_keeps_filename(self, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        vault = _full_vault()
        store.save(vault)
        original = vault_dir / "stories" / "redshift-to-bigquery.md"
        assert original.is_file()

        vault.stories[0].title = "The Great Warehouse Migration"
        store.save(vault)

        # Same file, updated frontmatter; no rename churn.
        assert original.is_file()
        assert not (vault_dir / "stories" / "the-great-warehouse-migration.md").exists()
        fm, _ = parse_markdown(original.read_text())
        assert fm["title"] == "The Great Warehouse Migration"
        assert store.load().stories[0].title == "The Great Warehouse Migration"


# ── v0 reading and migration ────────────────────────────────────────


def _seed_v0(vault_dir: Path) -> dict[str, str]:
    """Write a v0 vault.json with cross-links; returns the ids used."""
    skill_id = str(uuid4())
    exp_id = str(uuid4())
    story_id = str(uuid4())
    ts = "2026-01-01T00:00:00Z"
    v0 = {
        "schema_version": 0,
        "updated_at": ts,
        "profile": {
            "display_name": "Ada",
            "headline": "Engineer",
            "summary": "s",
            "location": "London",
            "contact_email": "ada@example.com",
        },
        "skills": [
            {
                "id": skill_id, "name": "Python", "taxonomy_id": None,
                "category": "technical", "proficiency": 9, "source": "manual",
                "notes": "", "created_at": ts, "updated_at": ts,
            },
            {
                "id": str(uuid4()), "name": "SQL", "taxonomy_id": None,
                "category": "technical", "proficiency": 6, "source": "manual",
                "notes": "", "created_at": ts, "updated_at": ts,
            },
        ],
        "experiences": [
            {
                "id": exp_id, "title": "Staff Engineer", "company": "Acme",
                "start_date": "2020-01", "end_date": "", "description": "Led things.",
                "accomplishments": ["Shipped v2"], "source": "manual",
                "created_at": ts, "updated_at": ts,
            }
        ],
        "stories": [
            {
                "id": story_id, "title": "Migration", "situation": "s", "task": "t",
                "action": "a", "result": "r", "skill_ids": [skill_id],
                "experience_id": exp_id, "source": "manual",
                "created_at": ts, "updated_at": ts,
            }
        ],
        "philosophies": [
            {
                "id": str(uuid4()), "title": "Boring releases", "description": "d",
                "category": "technical-approach", "evidence_story_ids": [story_id],
                "source": "manual", "created_at": ts, "updated_at": ts,
            }
        ],
        "education": [],
    }
    (vault_dir / "vault.json").write_text(json.dumps(v0, indent=2))
    commit(vault_dir, "seed v0")
    return {"skill": skill_id, "experience": exp_id, "story": story_id}


class TestV0Reader:
    def test_read_only_load_remaps_proficiency_in_memory(
        self, vault_dir: Path
    ) -> None:
        _seed_v0(vault_dir)
        store = VaultStore(vault_dir)
        assert not store.is_v1()
        loaded = store.load()
        by_name = {s.name: s.proficiency for s in loaded.skills}
        assert by_name == {"Python": 5, "SQL": 3}  # ceil(9/2), ceil(6/2)
        # The file on disk is untouched — remap happens at write time only.
        raw = json.loads((vault_dir / "vault.json").read_text())
        assert raw["skills"][0]["proficiency"] == 9

    def test_first_write_converts_to_v1(self, vault_dir: Path) -> None:
        _seed_v0(vault_dir)
        store = VaultStore(vault_dir)
        store.add_skill(name="Go", proficiency=3, category="technical")
        assert store.is_v1()
        assert not (vault_dir / "vault.json").exists()
        assert {s.name for s in store.load().skills} == {"Python", "SQL", "Go"}


class TestMigrateCommand:
    def test_migrate_writes_tree_and_remaps(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        ids = _seed_v0(vault_dir)
        result = runner.invoke(cli, ["--path", str(vault_dir), "vault", "migrate"])
        assert result.exit_code == 0, result.output
        assert "Migrated vault to schema v1" in result.output

        assert (vault_dir / "traitprint.json").is_file()
        assert not (vault_dir / "vault.json").exists()

        loaded = VaultStore(vault_dir).load()
        assert loaded.schema_version == 1
        by_name = {s.name: s.proficiency for s in loaded.skills}
        assert by_name == {"Python": 5, "SQL": 3}

        # Cross-links survive the split into files.
        story = loaded.stories[0]
        # v0 predates experience skill links (contract 1.1) — empty, valid.
        assert loaded.experiences[0].skill_ids == []
        assert [str(x) for x in story.skill_ids] == [ids["skill"]]
        assert str(story.experience_id) == ids["experience"]
        assert [str(x) for x in loaded.philosophies[0].evidence_story_ids] == [
            ids["story"]
        ]

        # Single migration commit, vault.json removed from the tree.
        history = git_log(vault_dir, n=3)
        assert any("Migrate vault to schema v1" in line for line in history)

    def test_migrate_is_idempotent(self, runner: CliRunner, vault_dir: Path) -> None:
        _seed_v0(vault_dir)
        first = runner.invoke(cli, ["--path", str(vault_dir), "vault", "migrate"])
        assert first.exit_code == 0, first.output
        second = runner.invoke(cli, ["--path", str(vault_dir), "vault", "migrate"])
        assert second.exit_code == 0, second.output
        assert "already schema v1" in second.output

    def test_migrate_json_already_v1_keeps_full_contract(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        # The documented payload always carries all four keys, so
        # consumers can read payload["files"] without a KeyError even
        # when there was nothing to migrate.
        _seed_v0(vault_dir)
        first = runner.invoke(cli, ["--path", str(vault_dir), "vault", "migrate"])
        assert first.exit_code == 0, first.output
        second = runner.invoke(
            cli, ["--path", str(vault_dir), "vault", "migrate", "--json"]
        )
        assert second.exit_code == 0, second.output
        payload = json.loads(second.output)
        assert set(payload) == {"status", "migrated", "files", "proficiency_remaps"}
        assert payload["status"] == "already-v1"
        assert payload["migrated"] is False
        assert payload["files"] == []
        assert payload["proficiency_remaps"] == []

    def test_dry_run_json_reports_plan(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        _seed_v0(vault_dir)
        result = runner.invoke(
            cli, ["--path", str(vault_dir), "vault", "migrate", "--dry-run", "--json"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["status"] == "planned"
        assert payload["migrated"] is False
        assert "skills.json" in payload["files"]
        assert "traitprint.json" in payload["files"]
        assert any(f.startswith("stories/") for f in payload["files"])
        remaps = {
            r["name"]: (r["from"], r["to"]) for r in payload["proficiency_remaps"]
        }
        assert remaps == {"Python": (9, 5), "SQL": (6, 3)}
        # Dry run: nothing written, v0 file untouched.
        assert not (vault_dir / "traitprint.json").exists()
        assert (vault_dir / "vault.json").is_file()

    def test_migrate_no_vault(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            cli, ["--path", str(tmp_path / "nope"), "vault", "migrate"]
        )
        assert result.exit_code == 0
        assert "No vault found" in result.output


# ── git history / rollback over the tree ────────────────────────────


class TestGitOverTree:
    def test_history_covers_all_files(self, runner: CliRunner, vault_dir: Path) -> None:
        store = VaultStore(vault_dir)
        store.save(store.create_empty())
        commit(vault_dir, "init")
        store.add_skill(name="Go", proficiency=4, category="technical")
        store.add_story(title="S", situation="s", task="t", action="a", result="r")
        result = runner.invoke(cli, ["--path", str(vault_dir), "vault", "history"])
        assert result.exit_code == 0
        assert "Add skill: Go (4/5)" in result.output
        assert "Add story: S" in result.output

    def test_rollback_removes_file_added_in_last_commit(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        store.save(store.create_empty())
        commit(vault_dir, "init")
        store.add_skill(name="Go", proficiency=4, category="technical")
        store.add_story(title="Oops", situation="s", task="t", action="a", result="r")
        story_files = list((vault_dir / "stories").glob("*.md"))
        assert len(story_files) == 1

        result = runner.invoke(
            cli, ["--path", str(vault_dir), "vault", "rollback", "--yes"]
        )
        assert result.exit_code == 0
        # The story's file is gone; the earlier skill survives.
        assert list((vault_dir / "stories").glob("*.md")) == []
        loaded = store.load()
        assert loaded.stories == []
        assert [s.name for s in loaded.skills] == ["Go"]

    def test_diff_covers_markdown_files(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        store = VaultStore(vault_dir)
        store.save(store.create_empty())
        commit(vault_dir, "init")
        store.add_story(title="S", situation="s", task="t", action="a", result="r")
        result = runner.invoke(cli, ["--path", str(vault_dir), "vault", "diff"])
        assert result.exit_code == 0
        assert "stories/" in result.output
