"""Tests for the proposals review CLI (tp-an-021) and the proposal store.

Covers: store round-trip + contract validation, every ``traitprint
proposals`` subcommand (list/show/approve/reject/add), the approve --all
batch commit, reject persistence, dangling-target errors,
``proposals add`` rejecting bad kinds/keys, the ``import-resume
--propose`` payload rendering (assist + BYOK paths), and the pending-
proposals audit finding.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from click.testing import CliRunner

from traitprint.audit import audit_vault
from traitprint.cli import cli
from traitprint.git_ops import commit, init_repo
from traitprint.git_ops import log as git_log
from traitprint.proposals import (
    PROPOSAL_KINDS,
    PROPOSAL_PAYLOAD_KEYS,
    PROPOSAL_STATUSES,
    ProposalApplyError,
    ProposalLookupError,
    ProposalSchema,
    ProposalStore,
    ProposalValidationError,
    apply_proposal,
    is_update_kind,
    proposal_contract,
    proposal_diff,
    validate_proposal_document,
    validate_proposal_fields,
)
from traitprint.schema import (
    MAX_LENSES,
    LensSchema,
    SalienceLevel,
    VaultSchema,
)
from traitprint.vault import VaultStore

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RESUME_TXT = FIXTURES / "resume_jordan_vance.txt"


@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    d = tmp_path / "vault"
    d.mkdir()
    init_repo(d)
    store = VaultStore(d)
    store.save(store.create_empty())
    commit(d, "test init")
    return d


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def tp(runner: CliRunner, vault_dir: Path, *args: str, inp: str | None = None):
    return runner.invoke(cli, ["--vault-dir", str(vault_dir), *args], input=inp)


def add_proposal(
    vault_dir: Path,
    kind: str,
    payload: dict[str, Any],
    *,
    target_id: UUID | None = None,
    rationale: str = "test",
) -> ProposalSchema:
    lp = ProposalStore(vault_dir).create(
        kind, payload, target_id=target_id, rationale=rationale, source="test"
    )
    return lp.proposal


def add_skill(runner: CliRunner, vault_dir: Path, name: str = "Python") -> UUID:
    result = tp(
        runner, vault_dir, "vault", "add-skill", name, "--proficiency", "3"
    )
    assert result.exit_code == 0, result.output
    return UUID(result.output.rsplit("[", 1)[1].rstrip("]\n"))


# ── store round-trip & validation ───────────────────────────────────


class TestProposalStore:
    def test_round_trip(self, vault_dir: Path) -> None:
        store = ProposalStore(vault_dir)
        lp = store.create(
            "add_skill",
            {"name": "Rust", "proficiency": 2},
            rationale="saw it in the resume",
            source="mcp:sk_abc1234",
        )
        assert lp.path.name == f"add-skill-{lp.proposal.id.hex[:8]}.json"
        loaded, issues = store.load_all()
        assert issues == []
        assert len(loaded) == 1
        got = loaded[0].proposal
        assert got.id == lp.proposal.id
        assert got.kind == "add_skill"
        assert got.payload == {"name": "Rust", "proficiency": 2}
        assert got.rationale == "saw it in the resume"
        assert got.source == "mcp:sk_abc1234"
        assert got.status == "pending"
        assert got.resolved_at is None

    def test_file_matches_contract_shape(self, vault_dir: Path) -> None:
        store = ProposalStore(vault_dir)
        lp = store.create("add_skill", {"name": "Rust", "proficiency": 2})
        doc = json.loads(lp.path.read_text(encoding="utf-8"))
        # $defs/proposal: required fields present, no extras.
        assert {"id", "kind", "payload", "status", "created_at"} <= set(doc)
        allowed = {
            "id",
            "kind",
            "target_id",
            "payload",
            "rationale",
            "source",
            "status",
            "created_at",
            "resolved_at",
        }
        assert set(doc) <= allowed

    def test_invalid_files_load_as_findings_not_crashes(
        self, vault_dir: Path
    ) -> None:
        pdir = vault_dir / "proposals"
        pdir.mkdir()
        (pdir / "broken.json").write_text("{not json", encoding="utf-8")
        (pdir / "wrong-shape.json").write_text("[1, 2]", encoding="utf-8")
        (pdir / "bad-kind.json").write_text(
            json.dumps(
                {
                    "id": str(uuid4()),
                    "kind": "add_unicorn",
                    "payload": {"name": "x"},
                    "status": "pending",
                    "created_at": "2026-06-10T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        store = ProposalStore(vault_dir)
        lp = store.create("add_skill", {"name": "Rust", "proficiency": 2})
        loaded, issues = store.load_all()
        assert [x.proposal.id for x in loaded] == [lp.proposal.id]
        assert {i.file for i in issues} == {
            "broken.json",
            "wrong-shape.json",
            "bad-kind.json",
        }
        assert all(i.problem for i in issues)

    def test_contract_invalid_files_load_as_findings(self, vault_dir: Path) -> None:
        # Hand-edited/synced files that parse but violate the contract
        # (update_* without target_id, unknown payload keys) must surface
        # as findings, never as approvable pending items (Codex P2 on #40).
        pdir = vault_dir / "proposals"
        pdir.mkdir()
        (pdir / "no-target.json").write_text(
            json.dumps(
                {
                    "id": str(uuid4()),
                    "kind": "update_skill",
                    "payload": {"proficiency": 4},
                    "status": "pending",
                    "created_at": "2026-06-10T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        (pdir / "bad-keys.json").write_text(
            json.dumps(
                {
                    "id": str(uuid4()),
                    "kind": "add_skill",
                    "payload": {"name": "Rust", "proficiency": 2, "level": 9},
                    "status": "pending",
                    "created_at": "2026-06-10T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        store = ProposalStore(vault_dir)
        loaded, issues = store.load_all()
        assert loaded == []
        assert {i.file for i in issues} == {"no-target.json", "bad-keys.json"}

    def test_find_by_prefix_and_full_uuid(self, vault_dir: Path) -> None:
        store = ProposalStore(vault_dir)
        lp = store.create("add_skill", {"name": "Rust", "proficiency": 2})
        pid = lp.proposal.id
        assert store.find(str(pid)).proposal.id == pid
        assert store.find(pid.hex[:8]).proposal.id == pid
        with pytest.raises(ProposalLookupError, match="no proposal matches"):
            store.find("ffffffff")

    def test_create_rejects_contract_violations(self, vault_dir: Path) -> None:
        store = ProposalStore(vault_dir)
        with pytest.raises(ProposalValidationError, match="keys outside"):
            store.create("add_skill", {"name": "X", "proficiency": 2, "level": 9})
        with pytest.raises(ProposalValidationError, match="target_id"):
            store.create("update_skill", {"proficiency": 4})
        with pytest.raises(ProposalValidationError, match="payload.name"):
            store.create("add_skill", {"proficiency": 2})
        # No files written for invalid proposals.
        loaded, issues = store.load_all()
        assert loaded == [] and issues == []


class TestValidateProposalFields:
    def test_every_kind_accepts_a_minimal_valid_payload(self) -> None:
        minimal: dict[str, dict[str, Any]] = {
            "add_skill": {"name": "X", "proficiency": 2},
            "add_experience": {"title": "Engineer"},
            "add_story": {"title": "The incident"},
            "add_philosophy": {"title": "Code review"},
            "add_education": {"institution": "State U"},
            "update_profile": {"basics": {"name": "Ada"}},
            "add_lens": {"slug": "pm", "name": "Product"},
        }
        for kind, payload in minimal.items():
            assert validate_proposal_fields(kind, None, payload) == []
        # Every update_* kind (except the singleton profile) accepts a
        # minimal changed-fields payload when a target_id is given.
        update_payloads: dict[str, dict[str, Any]] = {
            "update_skill": {"proficiency": 4},
            "update_experience": {"company": "Acme"},
            "update_story": {"outcome": "win"},
            "update_philosophy": {"category": "leadership"},
            "update_education": {"degree": "M.S."},
            "update_lens": {"name": "Product Leadership"},
        }
        entity_update_kinds = {
            k
            for k in PROPOSAL_KINDS
            if k.startswith("update_") and k != "update_profile"
        }
        assert set(update_payloads) == entity_update_kinds
        tid = uuid4()
        for kind, payload in update_payloads.items():
            assert validate_proposal_fields(kind, tid, payload) == []

    def test_unknown_kind(self) -> None:
        problems = validate_proposal_fields("add_unicorn", None, {"name": "x"})
        assert problems and "kind" in problems[0]

    def test_target_id_forbidden_for_add(self) -> None:
        problems = validate_proposal_fields(
            "add_skill", uuid4(), {"name": "X", "proficiency": 2}
        )
        assert any("only valid for update_*" in p for p in problems)

    def test_empty_payload(self) -> None:
        assert validate_proposal_fields("add_skill", None, {}) == [
            "payload: must not be empty"
        ]

    def test_profile_basics_keys_checked(self) -> None:
        problems = validate_proposal_fields(
            "update_profile", None, {"basics": {"display_name": "Ada"}}
        )
        assert any("basics" in p and "display_name" in p for p in problems)


# ── applying proposals ──────────────────────────────────────────────


class TestApplyProposal:
    def test_add_story_body_maps_to_star_fields(self) -> None:
        vault = VaultSchema()
        p = ProposalSchema(
            kind="add_story",
            payload={
                "title": "The migration",
                "body": "## Situation\nS\n\n## Task\nT\n\n## Action\nA\n\n"
                "## Result\nR\n\n## Lesson\nL",
            },
        )
        apply_proposal(vault, p)
        story = vault.stories[0]
        assert (story.situation, story.task, story.action, story.result) == (
            "S",
            "T",
            "A",
            "R",
        )
        assert story.lesson == "L"
        assert story.source == "proposal"  # default provenance

    def test_update_story_partial_body_keeps_other_sections(self) -> None:
        vault = VaultSchema()
        add = ProposalSchema(
            kind="add_story",
            payload={
                "title": "The migration",
                "body": "## Situation\nS\n\n## Task\nT\n\n## Action\nA\n\n"
                "## Result\nR",
            },
        )
        apply_proposal(vault, add)
        update = ProposalSchema(
            kind="update_story",
            target_id=vault.stories[0].id,
            payload={"body": "## Result\nActually 60% latency improvement."},
        )
        apply_proposal(vault, update)
        story = vault.stories[0]
        assert story.result == "Actually 60% latency improvement."
        assert story.situation == "S"  # untouched sections survive

    def test_layer0_hard_reject(self) -> None:
        from traitprint.proposals import ProposalApplyError

        vault = VaultSchema()
        p = ProposalSchema(
            kind="add_skill", payload={"name": "X", "proficiency": 9}
        )
        with pytest.raises(ProposalApplyError, match="proficiency"):
            apply_proposal(vault, p)
        assert vault.skills == []  # nothing applied

    def test_duplicate_skill_rejected(self) -> None:
        from traitprint.proposals import ProposalApplyError

        vault = VaultSchema()
        apply_proposal(
            vault,
            ProposalSchema(
                kind="add_skill", payload={"name": "Python", "proficiency": 2}
            ),
        )
        with pytest.raises(ProposalApplyError, match="already exists"):
            apply_proposal(
                vault,
                ProposalSchema(
                    kind="add_skill",
                    payload={"name": "  python ", "proficiency": 3},
                ),
            )

    def test_experience_skill_ids_accepted_and_applied(self) -> None:
        # Contract revision 1.1: skill_ids is part of the experience
        # entity shape, so experience proposals may carry it.
        vault = VaultSchema()
        sid = uuid4()
        assert (
            validate_proposal_fields(
                "add_experience",
                None,
                {"title": "Staff Engineer", "skill_ids": [str(sid)]},
            )
            == []
        )
        apply_proposal(
            vault,
            ProposalSchema(
                kind="add_experience",
                payload={"title": "Staff Engineer", "skill_ids": [str(sid)]},
            ),
        )
        assert vault.experiences[0].skill_ids == [sid]
        # update_experience can change the links too.
        sid2 = uuid4()
        apply_proposal(
            vault,
            ProposalSchema(
                kind="update_experience",
                target_id=vault.experiences[0].id,
                payload={"skill_ids": [str(sid2)]},
            ),
        )
        assert vault.experiences[0].skill_ids == [sid2]

    def test_experience_skill_links_accepted_and_applied(self) -> None:
        # Contract revision 1.2: skill_links is part of the experience
        # entity shape, so experience proposals may carry it; stories
        # are unchanged.
        vault = VaultSchema()
        sid = uuid4()
        assert (
            validate_proposal_fields(
                "add_experience",
                None,
                {
                    "title": "Staff Engineer",
                    "skill_ids": [str(sid)],
                    "skill_links": [{"skill_id": str(sid), "proficiency": 4}],
                },
            )
            == []
        )
        apply_proposal(
            vault,
            ProposalSchema(
                kind="add_experience",
                payload={
                    "title": "Staff Engineer",
                    "skill_ids": [str(sid)],
                    "skill_links": [{"skill_id": str(sid), "proficiency": 4}],
                },
            ),
        )
        assert vault.experiences[0].skill_links[0].skill_id == sid
        assert vault.experiences[0].skill_links[0].proficiency == 4
        # update_experience can change the annotations too.
        apply_proposal(
            vault,
            ProposalSchema(
                kind="update_experience",
                target_id=vault.experiences[0].id,
                payload={
                    "skill_links": [{"skill_id": str(sid), "proficiency": 2}],
                },
            ),
        )
        assert vault.experiences[0].skill_links[0].proficiency == 2

    def test_skill_links_rejected_on_story_proposals(self) -> None:
        # skill_links is experience-only; story payloads must not accept it.
        sid = uuid4()
        problems = validate_proposal_fields(
            "add_story",
            None,
            {
                "title": "A Story",
                "skill_links": [{"skill_id": str(sid), "proficiency": 4}],
            },
        )
        assert any("skill_links" in p for p in problems)

    def test_update_profile_maps_basics(self) -> None:
        vault = VaultSchema()
        p = ProposalSchema(
            kind="update_profile",
            payload={"basics": {"name": "Ada", "label": "Engineer"}},
        )
        apply_proposal(vault, p)
        assert vault.profile.display_name == "Ada"
        assert vault.profile.headline == "Engineer"
        assert vault.profile.summary == ""  # untouched

    def test_update_profile_rev_1_3_keys_validate(self) -> None:
        # phone/url/profiles are contract rev 1.3 basics keys.
        problems = validate_proposal_fields(
            "update_profile",
            None,
            {
                "basics": {
                    "phone": "+1 555 0100",
                    "url": "https://ada.example.com",
                    "profiles": [
                        {"network": "github", "url": "https://github.com/ada"}
                    ],
                }
            },
        )
        assert problems == []

    def test_update_profile_applies_rev_1_3_keys(self) -> None:
        vault = VaultSchema()
        apply_proposal(
            vault,
            ProposalSchema(
                kind="update_profile",
                payload={
                    "basics": {
                        "phone": "+1 555 0100",
                        "url": "https://ada.example.com",
                        "profiles": [
                            {
                                "network": "github",
                                "username": "ada",
                                "url": "https://github.com/ada",
                            }
                        ],
                    }
                },
            ),
        )
        assert vault.profile.phone == "+1 555 0100"
        assert vault.profile.url == "https://ada.example.com"
        assert vault.profile.profiles[0].network == "github"
        assert vault.profile.profiles[0].username == "ada"

    def test_update_profile_profiles_replace_and_clear(self) -> None:
        vault = VaultSchema()
        apply_proposal(
            vault,
            ProposalSchema(
                kind="update_profile",
                payload={"basics": {"profiles": [{"network": "github"}]}},
            ),
        )
        assert len(vault.profile.profiles) == 1
        apply_proposal(
            vault,
            ProposalSchema(
                kind="update_profile",
                payload={"basics": {"profiles": []}},
            ),
        )
        assert vault.profile.profiles == []

    def test_update_profile_bad_profiles_shape_rejected(self) -> None:
        problems = validate_proposal_fields(
            "update_profile",
            None,
            {"basics": {"profiles": [{"url": "https://no-network.example"}]}},
        )
        assert any("network" in p for p in problems)

        problems = validate_proposal_fields(
            "update_profile",
            None,
            {"basics": {"profiles": [{"network": "github", "site": "x"}]}},
        )
        assert any("site" in p for p in problems)

        problems = validate_proposal_fields(
            "update_profile",
            None,
            {"basics": {"profiles": "github"}},
        )
        assert any("must be a JSON array" in p for p in problems)

    def test_update_profile_apply_rejects_bad_profiles(self) -> None:
        vault = VaultSchema()
        with pytest.raises(ProposalApplyError):
            apply_proposal(
                vault,
                ProposalSchema(
                    kind="update_profile",
                    payload={"basics": {"profiles": [{"network": ""}]}},
                ),
            )
        assert vault.profile.profiles == []  # nothing mutated

    def test_skill_rename_reresolves_taxonomy(self) -> None:
        from traitprint.taxonomy import find_exact

        vault = VaultSchema()
        apply_proposal(
            vault,
            ProposalSchema(
                kind="add_skill", payload={"name": "Python", "proficiency": 3}
            ),
        )
        skill = vault.skills[0]
        python_entry = find_exact("Python")
        assert python_entry is not None
        assert skill.taxonomy_id == python_entry.id
        apply_proposal(
            vault,
            ProposalSchema(
                kind="update_skill",
                target_id=skill.id,
                payload={"name": "Definitely Not In Taxonomy"},
            ),
        )
        assert vault.skills[0].taxonomy_id is None

    def test_diff_rows_for_update(self) -> None:
        vault = VaultSchema()
        apply_proposal(
            vault,
            ProposalSchema(
                kind="add_skill", payload={"name": "Python", "proficiency": 3}
            ),
        )
        p = ProposalSchema(
            kind="update_skill",
            target_id=vault.skills[0].id,
            payload={"proficiency": 4},
        )
        rows = proposal_diff(vault, p)
        assert rows == [{"field": "proficiency", "current": 3, "proposed": 4}]


# ── lens proposal kinds ─────────────────────────────────────────────


def _lens_vault(*slugs: str) -> VaultSchema:
    """A vault carrying one lens per slug (first is default)."""
    return VaultSchema(
        lenses=[
            LensSchema(slug=s, name=s.title(), is_default=(i == 0))
            for i, s in enumerate(slugs)
        ]
    )


class TestLensProposalValidation:
    def test_add_lens_requires_slug_and_name(self) -> None:
        problems = validate_proposal_fields("add_lens", None, {"slug": "pm"})
        assert any("payload.name" in p for p in problems)
        problems = validate_proposal_fields("add_lens", None, {"name": "PM"})
        assert any("payload.slug" in p for p in problems)

    def test_add_lens_minimal_payload_valid(self) -> None:
        assert (
            validate_proposal_fields(
                "add_lens", None, {"slug": "pm", "name": "Product"}
            )
            == []
        )

    def test_unknown_lens_key_rejected(self) -> None:
        problems = validate_proposal_fields(
            "add_lens", None, {"slug": "pm", "name": "PM", "bogus": 1}
        )
        assert any("bogus" in p for p in problems)

    def test_update_lens_requires_target_id(self) -> None:
        problems = validate_proposal_fields(
            "update_lens", None, {"name": "Renamed"}
        )
        assert any("target_id" in p for p in problems)

    def test_full_lens_payload_valid(self) -> None:
        sid = uuid4()
        assert (
            validate_proposal_fields(
                "add_lens",
                None,
                {
                    "slug": "pm",
                    "name": "Product",
                    "target_archetypes": ["Product Manager"],
                    "headline_override": "PM leader",
                    "bio_override": "Ships product.",
                    "signature_experience_ids": [str(uuid4())],
                    "signature_story_ids": [str(uuid4())],
                    "skill_salience": {str(sid): "core"},
                    "is_default": True,
                },
            )
            == []
        )


class TestApplyLensProposal:
    def test_add_lens_creates_lens(self) -> None:
        vault = VaultSchema()
        msg = apply_proposal(
            vault,
            ProposalSchema(
                kind="add_lens", payload={"slug": "pm", "name": "Product"}
            ),
        )
        assert vault.lenses[0].slug == "pm"
        assert "Added lens" in msg

    def test_add_lens_salience_applied(self) -> None:
        vault = VaultSchema()
        sid = uuid4()
        apply_proposal(
            vault,
            ProposalSchema(
                kind="add_lens",
                payload={
                    "slug": "pm",
                    "name": "Product",
                    "skill_salience": {str(sid): "core"},
                },
            ),
        )
        assert vault.lenses[0].salience_for(sid) is SalienceLevel.CORE

    def test_add_lens_new_default_clears_others(self) -> None:
        vault = _lens_vault("eng")  # eng is default
        apply_proposal(
            vault,
            ProposalSchema(
                kind="add_lens",
                payload={"slug": "pm", "name": "Product", "is_default": True},
            ),
        )
        by_slug = {lens.slug: lens for lens in vault.lenses}
        assert by_slug["pm"].is_default is True
        assert by_slug["eng"].is_default is False

    def test_add_lens_cap_rejected_at_apply(self) -> None:
        vault = _lens_vault(*[f"l{i}" for i in range(MAX_LENSES)])
        assert len(vault.lenses) == MAX_LENSES
        with pytest.raises(ProposalApplyError, match="at most"):
            apply_proposal(
                vault,
                ProposalSchema(
                    kind="add_lens", payload={"slug": "over", "name": "Over"}
                ),
            )
        assert len(vault.lenses) == MAX_LENSES  # nothing applied

    def test_add_lens_duplicate_slug_rejected(self) -> None:
        vault = _lens_vault("pm")
        with pytest.raises(ProposalApplyError, match="already exists"):
            apply_proposal(
                vault,
                ProposalSchema(
                    kind="add_lens", payload={"slug": "pm", "name": "Dup"}
                ),
            )

    def test_add_lens_reserved_slug_rejected(self) -> None:
        vault = VaultSchema()
        with pytest.raises(ProposalApplyError, match="reserved"):
            apply_proposal(
                vault,
                ProposalSchema(
                    kind="add_lens", payload={"slug": "none", "name": "Nope"}
                ),
            )

    def test_add_lens_dangling_refs_warn_not_block(self) -> None:
        # Per Layer 1, a dangling signature/salience ref does not block the
        # apply — it applies and is surfaced later by the dispute machinery.
        vault = VaultSchema()
        missing = uuid4()
        apply_proposal(
            vault,
            ProposalSchema(
                kind="add_lens",
                payload={
                    "slug": "pm",
                    "name": "Product",
                    "signature_experience_ids": [str(missing)],
                    "skill_salience": {str(uuid4()): "suppressed"},
                },
            ),
        )
        assert vault.lenses[0].signature_experience_ids == [missing]

    def test_update_lens_changes_fields(self) -> None:
        vault = _lens_vault("pm")
        target = vault.lenses[0]
        apply_proposal(
            vault,
            ProposalSchema(
                kind="update_lens",
                target_id=target.id,
                payload={"headline_override": "Product leader"},
            ),
        )
        assert vault.lenses[0].headline_override == "Product leader"
        assert vault.lenses[0].slug == "pm"  # untouched fields survive

    def test_update_lens_missing_target_rejected(self) -> None:
        vault = _lens_vault("pm")
        with pytest.raises(ProposalApplyError, match="does not exist"):
            apply_proposal(
                vault,
                ProposalSchema(
                    kind="update_lens",
                    target_id=uuid4(),
                    payload={"name": "Ghost"},
                ),
            )

    def test_update_lens_sets_default_and_clears_others(self) -> None:
        vault = _lens_vault("eng", "pm")  # eng default
        pm = next(lens for lens in vault.lenses if lens.slug == "pm")
        apply_proposal(
            vault,
            ProposalSchema(
                kind="update_lens",
                target_id=pm.id,
                payload={"is_default": True},
            ),
        )
        by_slug = {lens.slug: lens for lens in vault.lenses}
        assert by_slug["pm"].is_default is True
        assert by_slug["eng"].is_default is False

    def test_update_lens_duplicate_slug_rejected(self) -> None:
        vault = _lens_vault("eng", "pm")
        pm = next(lens for lens in vault.lenses if lens.slug == "pm")
        with pytest.raises(ProposalApplyError, match="already exists"):
            apply_proposal(
                vault,
                ProposalSchema(
                    kind="update_lens",
                    target_id=pm.id,
                    payload={"slug": "eng"},
                ),
            )


class TestLensProposalContract:
    def test_new_kinds_in_contract(self) -> None:
        doc = proposal_contract()
        assert "add_lens" in doc["kinds"]
        assert "update_lens" in doc["kinds"]
        assert doc["required_payload_keys"]["add_lens"] == ["slug", "name"]
        assert "slug" in doc["payload_keys"]["add_lens"]
        assert "update_lens" in doc["target_id_required_for"]
        assert "add_lens" not in doc["target_id_required_for"]


# ── CLI: list / show ────────────────────────────────────────────────


class TestProposalsListCLI:
    def test_empty(self, runner: CliRunner, vault_dir: Path) -> None:
        result = tp(runner, vault_dir, "proposals", "list")
        assert result.exit_code == 0
        assert "No proposals found" in result.output

    def test_table_and_status_filter(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        add_proposal(vault_dir, "add_skill", {"name": "Rust", "proficiency": 2})
        p2 = add_proposal(
            vault_dir, "add_experience", {"title": "Engineer", "company": "Acme"}
        )
        result = tp(runner, vault_dir, "proposals", "reject", str(p2.id), "-y")
        assert result.exit_code == 0, result.output

        result = tp(runner, vault_dir, "proposals", "list")
        assert result.exit_code == 0
        assert "add_skill" in result.output and "Rust" in result.output
        assert "rejected" in result.output

        result = tp(runner, vault_dir, "proposals", "list", "--status", "pending")
        assert "add_skill" in result.output
        assert "add_experience" not in result.output

    def test_json_array(self, runner: CliRunner, vault_dir: Path) -> None:
        p = add_proposal(vault_dir, "add_skill", {"name": "Rust", "proficiency": 2})
        result = tp(runner, vault_dir, "proposals", "list", "--json")
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert len(rows) == 1
        assert rows[0]["id"] == str(p.id)
        assert rows[0]["kind"] == "add_skill"
        assert rows[0]["status"] == "pending"
        assert rows[0]["file"] == f"add-skill-{p.id.hex[:8]}.json"

    def test_invalid_file_is_warning_not_crash(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        pdir = vault_dir / "proposals"
        pdir.mkdir()
        (pdir / "broken.json").write_text("{nope", encoding="utf-8")
        result = tp(runner, vault_dir, "proposals", "list")
        assert result.exit_code == 0, result.output
        assert "[warn] proposals/broken.json" in result.output

    def test_json_stays_parseable_with_invalid_file(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        pdir = vault_dir / "proposals"
        pdir.mkdir()
        (pdir / "broken.json").write_text("{nope", encoding="utf-8")
        result = tp(runner, vault_dir, "proposals", "list", "--json")
        assert result.exit_code == 0
        # The warning goes to stderr; stdout stays a clean JSON array.
        # (click >= 8.2 separates the streams; fall back to the combined
        # output on older click, where the test is weaker but still runs.)
        stdout = getattr(result, "stdout", result.output)
        assert json.loads(stdout) == []
        assert "[warn] proposals/broken.json" in result.stderr


class TestProposalsShowCLI:
    def test_show_add_kind(self, runner: CliRunner, vault_dir: Path) -> None:
        p = add_proposal(
            vault_dir,
            "add_skill",
            {"name": "Rust", "proficiency": 2},
            rationale="mentioned in three stories",
        )
        result = tp(runner, vault_dir, "proposals", "show", p.id.hex[:8])
        assert result.exit_code == 0, result.output
        assert str(p.id) in result.output
        assert "mentioned in three stories" in result.output
        assert "Diff (new entity):" in result.output
        assert "name: Rust" in result.output

    def test_show_update_kind_renders_current_to_proposed(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        skill_id = add_skill(runner, vault_dir, "Python")
        p = add_proposal(
            vault_dir, "update_skill", {"proficiency": 4}, target_id=skill_id
        )
        result = tp(runner, vault_dir, "proposals", "show", str(p.id))
        assert result.exit_code == 0, result.output
        assert "Diff (current → proposed):" in result.output
        assert "proficiency: 3 → 4" in result.output

    def test_show_json_shape(self, runner: CliRunner, vault_dir: Path) -> None:
        skill_id = add_skill(runner, vault_dir, "Python")
        p = add_proposal(
            vault_dir, "update_skill", {"proficiency": 4}, target_id=skill_id
        )
        result = tp(runner, vault_dir, "proposals", "show", str(p.id), "--json")
        assert result.exit_code == 0, result.output
        doc = json.loads(result.output)
        assert set(doc) == {"proposal", "file", "diff"}
        assert doc["proposal"]["kind"] == "update_skill"
        assert doc["proposal"]["target_id"] == str(skill_id)
        assert doc["diff"] == [
            {"field": "proficiency", "current": 3, "proposed": 4}
        ]

    def test_show_unknown_id_exits_one(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = tp(runner, vault_dir, "proposals", "show", "deadbeef")
        assert result.exit_code == 1
        assert "no proposal matches" in result.output


# ── CLI: approve ────────────────────────────────────────────────────


class TestProposalsApproveCLI:
    def test_approve_applies_and_deletes_in_same_commit(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        p = add_proposal(vault_dir, "add_skill", {"name": "Rust", "proficiency": 2})
        pfile = vault_dir / "proposals" / f"add-skill-{p.id.hex[:8]}.json"
        assert pfile.is_file()
        before = len(git_log(vault_dir, n=50))

        result = tp(runner, vault_dir, "proposals", "approve", str(p.id), "-y")
        assert result.exit_code == 0, result.output
        assert "Approved" in result.output

        # Applied to the vault…
        vault = VaultStore(vault_dir).load()
        assert [s.name for s in vault.skills] == ["Rust"]
        # …file deleted, and exactly ONE new commit covers both (rule 7).
        assert not pfile.exists()
        entries = git_log(vault_dir, n=50)
        assert len(entries) == before + 1
        assert "Approve proposal: add_skill Rust" in entries[0]

    def test_approve_update_partial(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        skill_id = add_skill(runner, vault_dir, "Python")
        p = add_proposal(
            vault_dir,
            "update_skill",
            {"proficiency": 4, "notes": "battle-tested"},
            target_id=skill_id,
        )
        result = tp(runner, vault_dir, "proposals", "approve", str(p.id), "-y")
        assert result.exit_code == 0, result.output
        skill = VaultStore(vault_dir).load().skills[0]
        assert skill.id == skill_id  # identity preserved
        assert skill.proficiency == 4
        assert skill.notes == "battle-tested"
        assert skill.name == "Python"  # untouched field survives

    def test_approve_dangling_target_errors_and_stays_pending(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        p = add_proposal(
            vault_dir, "update_skill", {"proficiency": 4}, target_id=uuid4()
        )
        result = tp(runner, vault_dir, "proposals", "approve", str(p.id), "-y")
        assert result.exit_code == 1
        assert "does not exist" in result.output
        # Proposal file kept, still pending; vault untouched.
        loaded, _ = ProposalStore(vault_dir).load_all()
        assert loaded[0].proposal.status == "pending"
        assert VaultStore(vault_dir).load().skills == []

    def test_approve_layer0_violation_exits_one(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        p = add_proposal(vault_dir, "add_skill", {"name": "X", "proficiency": 9})
        result = tp(runner, vault_dir, "proposals", "approve", str(p.id), "-y")
        assert result.exit_code == 1
        assert "proficiency" in result.output
        assert VaultStore(vault_dir).load().skills == []

    def test_approve_non_pending_errors(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        p = add_proposal(vault_dir, "add_skill", {"name": "Rust", "proficiency": 2})
        tp(runner, vault_dir, "proposals", "reject", str(p.id), "-y")
        result = tp(runner, vault_dir, "proposals", "approve", str(p.id), "-y")
        assert result.exit_code == 1
        assert "rejected, not pending" in result.output

    def test_approve_without_id_or_all_is_usage_error(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = tp(runner, vault_dir, "proposals", "approve")
        assert result.exit_code == 2

    def test_approve_all_is_one_batch_commit(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        add_proposal(vault_dir, "add_skill", {"name": "Rust", "proficiency": 2})
        add_proposal(vault_dir, "add_experience", {"title": "Engineer"})
        add_proposal(
            vault_dir, "update_profile", {"basics": {"name": "Jordan Vance"}}
        )
        before = len(git_log(vault_dir, n=50))

        result = tp(runner, vault_dir, "proposals", "approve", "--all", "-y")
        assert result.exit_code == 0, result.output
        assert result.output.count("[ok]") == 3
        assert "Summary: approved 3, errors 0" in result.output

        vault = VaultStore(vault_dir).load()
        assert [s.name for s in vault.skills] == ["Rust"]
        assert [e.title for e in vault.experiences] == ["Engineer"]
        assert vault.profile.display_name == "Jordan Vance"
        assert ProposalStore(vault_dir).pending() == []

        entries = git_log(vault_dir, n=50)
        assert len(entries) == before + 1  # ONE batch commit
        assert "Approve 3 proposals" in entries[0]

    def test_approve_all_partial_failure(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        add_proposal(vault_dir, "add_skill", {"name": "Rust", "proficiency": 2})
        bad = add_proposal(
            vault_dir, "update_skill", {"proficiency": 4}, target_id=uuid4()
        )
        result = tp(runner, vault_dir, "proposals", "approve", "--all", "-y")
        assert result.exit_code == 1
        assert "[ok]" in result.output and "[err]" in result.output
        assert "Summary: approved 1, errors 1" in result.output
        # Good one applied + deleted; bad one left pending for review.
        assert [s.name for s in VaultStore(vault_dir).load().skills] == ["Rust"]
        pending = ProposalStore(vault_dir).pending()
        assert [lp.proposal.id for lp in pending] == [bad.id]

    def test_approve_all_with_none_pending(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = tp(runner, vault_dir, "proposals", "approve", "--all", "-y")
        assert result.exit_code == 0
        assert "No pending proposals" in result.output

    def test_approve_all_with_id_is_usage_error(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = tp(
            runner, vault_dir, "proposals", "approve", "abcd1234", "--all", "-y"
        )
        assert result.exit_code == 2
        assert "--all cannot be combined" in result.output


# ── CLI: reject ─────────────────────────────────────────────────────


class TestProposalsRejectCLI:
    def test_reject_persists_status_and_keeps_file(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        p = add_proposal(vault_dir, "add_skill", {"name": "Rust", "proficiency": 2})
        pfile = vault_dir / "proposals" / f"add-skill-{p.id.hex[:8]}.json"
        result = tp(runner, vault_dir, "proposals", "reject", str(p.id), "-y")
        assert result.exit_code == 0, result.output
        assert "Rejected" in result.output

        assert pfile.is_file()  # file kept
        doc = json.loads(pfile.read_text(encoding="utf-8"))
        assert doc["status"] == "rejected"
        assert doc["resolved_at"] is not None
        # Vault untouched; rejection committed.
        assert VaultStore(vault_dir).load().skills == []
        assert any("Reject proposal" in line for line in git_log(vault_dir, n=5))

    def test_reject_twice_errors(self, runner: CliRunner, vault_dir: Path) -> None:
        p = add_proposal(vault_dir, "add_skill", {"name": "Rust", "proficiency": 2})
        tp(runner, vault_dir, "proposals", "reject", str(p.id), "-y")
        result = tp(runner, vault_dir, "proposals", "reject", str(p.id), "-y")
        assert result.exit_code == 1
        assert "rejected, not pending" in result.output


# ── CLI: add (the local propose write path) ─────────────────────────


class TestProposalsAddCLI:
    def test_creates_pending_proposal(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = tp(
            runner,
            vault_dir,
            "proposals",
            "add",
            "--kind",
            "add_skill",
            "--rationale",
            "demo",
            "--payload-json",
            "-",
            inp=json.dumps({"name": "Rust", "proficiency": 2}),
        )
        assert result.exit_code == 0, result.output
        assert "Created pending proposal add_skill" in result.output
        pending = ProposalStore(vault_dir).pending()
        assert len(pending) == 1
        assert pending[0].proposal.source == "cli"
        assert pending[0].proposal.rationale == "demo"
        # Staging is committed but the vault itself is untouched.
        assert VaultStore(vault_dir).load().skills == []
        assert any("Add proposal" in line for line in git_log(vault_dir, n=5))

    def test_json_output(self, runner: CliRunner, vault_dir: Path) -> None:
        result = tp(
            runner,
            vault_dir,
            "proposals",
            "add",
            "--kind",
            "add_education",
            "--payload-json",
            "-",
            "--json",
            inp=json.dumps({"institution": "State U"}),
        )
        assert result.exit_code == 0, result.output
        doc = json.loads(result.output)
        assert doc["kind"] == "add_education"
        assert doc["status"] == "pending"
        assert doc["file"].startswith("add-education-")

    def test_rejects_unknown_kind_usage_error(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = tp(
            runner,
            vault_dir,
            "proposals",
            "add",
            "--kind",
            "add_unicorn",
            "--payload-json",
            "-",
            inp="{}",
        )
        assert result.exit_code == 2  # click.Choice usage error

    def test_rejects_bad_payload_keys(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = tp(
            runner,
            vault_dir,
            "proposals",
            "add",
            "--kind",
            "add_skill",
            "--payload-json",
            "-",
            inp=json.dumps({"name": "X", "proficiency": 2, "level": 9}),
        )
        assert result.exit_code == 1
        assert "keys outside the add_skill entity shape: level" in result.output
        assert "Allowed keys:" in result.output
        assert ProposalStore(vault_dir).pending() == []

    def test_update_kind_requires_target_id(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = tp(
            runner,
            vault_dir,
            "proposals",
            "add",
            "--kind",
            "update_skill",
            "--payload-json",
            "-",
            inp=json.dumps({"proficiency": 4}),
        )
        assert result.exit_code == 1
        assert "target_id: required for update_skill" in result.output

    def test_target_id_forbidden_for_add_kind(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = tp(
            runner,
            vault_dir,
            "proposals",
            "add",
            "--kind",
            "add_skill",
            "--target-id",
            str(uuid4()),
            "--payload-json",
            "-",
            inp=json.dumps({"name": "X", "proficiency": 2}),
        )
        assert result.exit_code == 1
        assert "only valid for update_* kinds" in result.output

    def test_non_object_payload_errors(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        result = tp(
            runner,
            vault_dir,
            "proposals",
            "add",
            "--kind",
            "add_skill",
            "--payload-json",
            "-",
            inp="[1, 2]",
        )
        assert result.exit_code == 1
        assert "must be a JSON object" in result.output

    def test_full_propose_review_loop(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        """proposals add → show → approve mirrors the remote staged path."""
        result = tp(
            runner,
            vault_dir,
            "proposals",
            "add",
            "--kind",
            "add_experience",
            "--source",
            "agent:test",
            "--rationale",
            "mentioned in conversation",
            "--payload-json",
            "-",
            "--json",
            inp=json.dumps(
                {
                    "title": "Senior Platform Engineer",
                    "company": "Northwind Robotics",
                    "start_date": "2021-03",
                    "body": "Platform team lead for deploy tooling.",
                }
            ),
        )
        assert result.exit_code == 0, result.output
        pid = json.loads(result.output)["id"]

        result = tp(runner, vault_dir, "proposals", "approve", pid, "-y")
        assert result.exit_code == 0, result.output
        exp = VaultStore(vault_dir).load().experiences[0]
        assert exp.title == "Senior Platform Engineer"
        assert exp.description == "Platform team lead for deploy tooling."
        assert exp.source == "agent:test"


# ── audit integration ───────────────────────────────────────────────


class TestAuditProposalFinding:
    def test_pending_proposals_surface_as_finding(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        add_proposal(vault_dir, "add_skill", {"name": "Rust", "proficiency": 2})
        add_proposal(vault_dir, "add_experience", {"title": "Engineer"})
        result = tp(runner, vault_dir, "vault", "audit", "--json")
        assert result.exit_code == 0, result.output
        report = json.loads(result.output)
        codes = {f["code"]: f for f in report["findings"]}
        finding = codes["proposals.pending"]
        assert finding["severity"] == "minor"
        assert finding["section"] == "proposals"
        assert "2 proposals awaiting review" in finding["message"]
        assert "traitprint proposals list" in finding["message"]

    def test_resolved_proposals_do_not_count(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        p = add_proposal(vault_dir, "add_skill", {"name": "Rust", "proficiency": 2})
        tp(runner, vault_dir, "proposals", "reject", str(p.id), "-y")
        result = tp(runner, vault_dir, "vault", "audit", "--json")
        report = json.loads(result.output)
        assert not any(
            f["code"] == "proposals.pending" for f in report["findings"]
        )

    def test_invalid_proposal_file_is_a_finding(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        pdir = vault_dir / "proposals"
        pdir.mkdir()
        (pdir / "broken.json").write_text("{nope", encoding="utf-8")
        result = tp(runner, vault_dir, "vault", "audit", "--json")
        assert result.exit_code == 0, result.output
        report = json.loads(result.output)
        assert any(
            f["code"] == "proposals.invalid_file" and "broken.json" in f["message"]
            for f in report["findings"]
        )

    def test_audit_vault_defaults_unchanged(self) -> None:
        report = audit_vault(VaultSchema())
        assert not any(f.section == "proposals" for f in report.findings)


# ── import-resume --propose ─────────────────────────────────────────


PROVIDER_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "OLLAMA_HOST",
)


@pytest.fixture()
def no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "traitprint.providers.base.load_credentials", lambda path=None: {}
    )


class TestImportResumePropose:
    def test_assist_payload_switches_to_proposals_add(
        self, runner: CliRunner, vault_dir: Path, no_provider: None
    ) -> None:
        result = tp(
            runner,
            vault_dir,
            "vault",
            "import-resume",
            str(RESUME_TXT),
            "--propose",
        )
        assert result.exit_code == 0, result.output
        out = result.output
        prefix = f"traitprint --vault-dir {vault_dir.resolve()} proposals add"
        assert f"{prefix} --kind update_profile" in out
        assert f"{prefix} --kind add_skill" in out
        assert f"{prefix} --kind add_experience" in out
        assert f"{prefix} --kind add_education" in out
        # No direct write commands in propose mode.
        assert "add-skill --from-json" not in out
        assert "set-profile --name" not in out
        # The user reviews; the agent never approves.
        assert "proposals approve" in out
        assert "proposals list" in out

    def test_assist_payload_json_propose_shape(
        self, runner: CliRunner, vault_dir: Path, no_provider: None
    ) -> None:
        result = tp(
            runner,
            vault_dir,
            "vault",
            "import-resume",
            str(RESUME_TXT),
            "--propose",
            "--json",
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["mode"] == "agent-assist"
        assert payload["propose"] is True
        steps = payload["write_back"]["steps"]
        assert [s["section"] for s in steps] == [
            "profile",
            "skills",
            "experiences",
            "education",
        ]
        for step in steps:
            assert "proposals add --kind" in step["command"]
            assert "--payload-json -" in step["command"]
            assert "stdin" in step
        prefix = f"traitprint --vault-dir {vault_dir.resolve()}"
        assert (
            payload["write_back"]["verify"]
            == f"{prefix} proposals list --json"
        )

    def test_default_assist_payload_unchanged(
        self, runner: CliRunner, vault_dir: Path, no_provider: None
    ) -> None:
        result = tp(
            runner, vault_dir, "vault", "import-resume", str(RESUME_TXT), "--json"
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["propose"] is False
        steps = payload["write_back"]["steps"]
        assert any("--from-json -" in s["command"] for s in steps)
        assert all("proposals add" not in s["command"] for s in steps)

    def test_byok_propose_stages_proposals_not_writes(
        self,
        runner: CliRunner,
        vault_dir: Path,
        no_provider: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from traitprint.providers.base import LLMResponse

        draft_json = json.dumps(
            {
                "profile": {"display_name": "Jordan Vance"},
                "skills": [{"name": "Kubernetes", "proficiency": 3}],
                "experiences": [
                    {
                        "title": "Senior Platform Engineer",
                        "company": "Northwind Robotics",
                        "start_date": "2021-03",
                        "description": "Platform team lead.",
                    }
                ],
                "education": [{"institution": "Lakeview State University"}],
            }
        )

        class FakeProvider:
            name = "fake"
            model = "fake-1"

            def complete(
                self,
                system: str,
                user: str,
                *,
                max_tokens: int = 4096,
                temperature: float = 0.0,
            ) -> LLMResponse:
                return LLMResponse(
                    content=draft_json,
                    input_tokens=1,
                    output_tokens=1,
                    model=self.model,
                    provider=self.name,
                )

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(
            "traitprint.providers.detect_provider",
            lambda **kwargs: FakeProvider(),
        )

        result = tp(
            runner,
            vault_dir,
            "vault",
            "import-resume",
            str(RESUME_TXT),
            "--propose",
            "-y",
        )
        assert result.exit_code == 0, result.output
        assert "Staged 4 proposals" in result.output
        assert "vault is unchanged" in result.output

        # Vault untouched; four pending proposals staged in one commit.
        vault = VaultStore(vault_dir).load()
        assert vault.skills == [] and vault.experiences == []
        assert vault.profile.display_name == ""
        pending = ProposalStore(vault_dir).pending()
        kinds = sorted(lp.proposal.kind for lp in pending)
        assert kinds == [
            "add_education",
            "add_experience",
            "add_skill",
            "update_profile",
        ]
        assert all(
            lp.proposal.source == "import-resume" for lp in pending
        )
        assert any(
            "Propose resume import" in line for line in git_log(vault_dir, n=5)
        )

        # Approve-all lands everything (the D9 one-step path).
        result = tp(runner, vault_dir, "proposals", "approve", "--all", "-y")
        assert result.exit_code == 0, result.output
        vault = VaultStore(vault_dir).load()
        assert [s.name for s in vault.skills] == ["Kubernetes"]
        assert vault.experiences[0].description == "Platform team lead."
        assert vault.profile.display_name == "Jordan Vance"
        assert vault.education[0].institution == "Lakeview State University"


class TestDraftToProposals:
    def test_mapping_shapes(self) -> None:
        from traitprint.mining import draft_from_dict, draft_to_proposals

        draft = draft_from_dict(
            {
                "profile": {"display_name": "Ada", "headline": "Engineer"},
                "skills": [
                    {
                        "name": "Python",
                        "proficiency": 3,
                        "category": "technical",
                        "notes": "n",
                    }
                ],
                "experiences": [
                    {
                        "title": "Engineer",
                        "company": "Acme",
                        "start_date": "2020-01",
                        "description": "Did things.",
                        "accomplishments": ["Shipped"],
                    }
                ],
                "education": [{"institution": "State U", "degree": "B.S."}],
            }
        )
        pairs = draft_to_proposals(draft)
        kinds = [k for k, _ in pairs]
        assert kinds == [
            "update_profile",
            "add_skill",
            "add_experience",
            "add_education",
        ]
        payloads = dict(pairs)
        assert payloads["update_profile"] == {
            "basics": {"name": "Ada", "label": "Engineer"}
        }
        assert payloads["add_skill"] == {
            "name": "Python",
            "proficiency": 3,
            "category": "technical",
            "notes": "n",
        }
        exp = payloads["add_experience"]
        assert exp["body"] == "Did things."  # narrative travels in body
        assert "description" not in exp
        assert payloads["add_education"] == {
            "institution": "State U",
            "degree": "B.S.",
        }
        # Every payload passes the proposal contract validation.
        for kind, payload in pairs:
            assert validate_proposal_fields(kind, None, payload) == []

    def test_empty_draft_yields_nothing(self) -> None:
        from traitprint.mining import draft_from_dict, draft_to_proposals

        assert draft_to_proposals(draft_from_dict({})) == []


# ── document validation + contract dump (external-proposer surface) ─


class TestValidateProposalDocument:
    def test_round_trip_document_is_valid(self) -> None:
        doc = ProposalSchema(
            kind="add_skill",
            payload={"name": "Rust", "proficiency": 2},
            source="my-exporter",
        ).model_dump(mode="json")
        assert validate_proposal_document(doc) == []

    def test_not_an_object(self) -> None:
        assert validate_proposal_document([1, 2]) == [
            "proposal must be a JSON object"
        ]
        assert validate_proposal_document("nope") == [
            "proposal must be a JSON object"
        ]

    def test_schema_shape_error_reported_with_location(self) -> None:
        problems = validate_proposal_document(
            {
                "id": "not-a-uuid",
                "kind": "add_skill",
                "payload": {"name": "Rust", "proficiency": 2},
            }
        )
        assert problems
        assert any(p.startswith("id:") for p in problems)

    def test_unknown_kind(self) -> None:
        problems = validate_proposal_document(
            {"kind": "add_unicorn", "payload": {"name": "x"}}
        )
        assert problems
        assert "kind" in problems[0]

    def test_add_lens_is_now_a_known_kind(self) -> None:
        # add_lens graduated from "rejected" to a first-class kind — a
        # well-formed lens proposal document validates clean.
        problems = validate_proposal_document(
            {"kind": "add_lens", "payload": {"slug": "pm", "name": "Product"}}
        )
        assert problems == []

    def test_field_rules_collected(self) -> None:
        problems = validate_proposal_document(
            {"kind": "add_skill", "payload": {"name": "Rust", "bogus": 1}}
        )
        assert any("bogus" in p for p in problems)
        assert any("payload.proficiency" in p for p in problems)

    def test_update_kind_requires_target_id(self) -> None:
        problems = validate_proposal_document(
            {"kind": "update_skill", "payload": {"proficiency": 4}}
        )
        assert any("target_id" in p for p in problems)

    def test_agrees_with_load_all(self, vault_dir: Path) -> None:
        # The pre-flight verdict must match the review queue's: a file
        # load_all accepts validates clean, a file load_all flags fails.
        store = ProposalStore(vault_dir)
        lp = store.create(
            "add_skill", {"name": "Rust", "proficiency": 2}, source="test"
        )
        good = json.loads(lp.path.read_text(encoding="utf-8"))
        assert validate_proposal_document(good) == []

        bad = dict(good)
        bad["payload"] = {"name": "Go", "bogus": True}
        bad_file = store.proposals_dir / "add-skill-deadbeef.json"
        bad_file.write_text(json.dumps(bad), encoding="utf-8")
        loaded, issues = store.load_all()
        assert len(loaded) == 1 and len(issues) == 1
        assert validate_proposal_document(bad) != []


class TestProposalContract:
    def test_matches_module_constants(self) -> None:
        doc = proposal_contract()
        assert doc["contract"] == "vault-v1"
        assert doc["definition"] == "$defs/proposal"
        assert doc["kinds"] == list(PROPOSAL_KINDS)
        assert doc["statuses"] == list(PROPOSAL_STATUSES)
        assert set(doc["payload_keys"]) == set(PROPOSAL_KINDS)
        for kind, keys in doc["payload_keys"].items():
            assert keys == list(PROPOSAL_PAYLOAD_KEYS[kind])
        assert doc["target_id_required_for"] == [
            k for k in PROPOSAL_KINDS if is_update_kind(k)
        ]
        # update_profile targets the singleton profile — never a target_id.
        assert "update_profile" not in doc["target_id_required_for"]

    def test_required_keys_are_a_subset_of_allowed(self) -> None:
        doc = proposal_contract()
        for kind, required in doc["required_payload_keys"].items():
            assert set(required) <= set(doc["payload_keys"][kind]), kind

    def test_json_serializable(self) -> None:
        json.dumps(proposal_contract())


class TestProposalsValidateCLI:
    """`traitprint proposals validate` — read-only, vault-independent."""

    def _write(self, directory: Path, name: str, doc: Any) -> Path:
        file = directory / name
        file.write_text(json.dumps(doc), encoding="utf-8")
        return file

    def _valid_doc(self) -> dict[str, Any]:
        return {"kind": "add_skill", "payload": {"name": "Rust", "proficiency": 2}}

    def test_valid_file_without_any_vault(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        file = self._write(tmp_path, "add-skill-1.json", self._valid_doc())
        result = runner.invoke(
            cli,
            [
                "--vault-dir",
                str(tmp_path / "does-not-exist"),
                "proposals",
                "validate",
                str(file),
            ],
        )
        assert result.exit_code == 0, result.output
        assert f"[ok] {file}" in result.output
        assert "Summary: 1 valid, 0 invalid" in result.output

    def test_invalid_file_exits_one_with_err_lines(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        file = self._write(
            tmp_path,
            "add-skill-1.json",
            {"kind": "add_skill", "payload": {"name": "Rust", "bogus": 1}},
        )
        result = runner.invoke(cli, ["proposals", "validate", str(file)])
        assert result.exit_code == 1
        assert f"[err] {file}: " in result.output
        assert "bogus" in result.output
        assert "Summary: 0 valid, 1 invalid" in result.output

    def test_invalid_json_is_a_finding_not_a_crash(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        file = tmp_path / "broken.json"
        file.write_text("{not json", encoding="utf-8")
        result = runner.invoke(cli, ["proposals", "validate", str(file)])
        assert result.exit_code == 1
        assert "invalid JSON" in result.output

    def test_directory_expands_to_json_files(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        self._write(tmp_path, "a.json", self._valid_doc())
        self._write(
            tmp_path, "b.json", {"kind": "update_skill", "payload": {"name": "Go"}}
        )
        (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
        result = runner.invoke(cli, ["proposals", "validate", str(tmp_path)])
        assert result.exit_code == 1
        assert "Summary: 1 valid, 1 invalid" in result.output

    def test_no_paths_is_a_usage_error(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["proposals", "validate"])
        assert result.exit_code == 2

    def test_empty_directory_is_a_usage_error(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result = runner.invoke(cli, ["proposals", "validate", str(tmp_path)])
        assert result.exit_code == 2

    def test_json_report(self, runner: CliRunner, tmp_path: Path) -> None:
        good = self._write(tmp_path, "good.json", self._valid_doc())
        bad = self._write(
            tmp_path, "bad.json", {"kind": "add_skill", "payload": {"name": "Go"}}
        )
        result = runner.invoke(
            cli, ["proposals", "validate", "--json", str(bad), str(good)]
        )
        assert result.exit_code == 1
        report = json.loads(result.output)
        assert report["valid"] is False
        assert report["checked"] == 2
        by_file = {row["file"]: row for row in report["results"]}
        assert by_file[str(good)]["valid"] is True
        assert by_file[str(good)]["problems"] == []
        assert by_file[str(bad)]["valid"] is False
        assert by_file[str(bad)]["problems"]

    def test_validates_files_the_store_wrote(
        self, runner: CliRunner, vault_dir: Path
    ) -> None:
        add_proposal(vault_dir, "add_skill", {"name": "Rust", "proficiency": 2})
        result = runner.invoke(
            cli, ["proposals", "validate", str(vault_dir / "proposals")]
        )
        assert result.exit_code == 0, result.output
        assert "Summary: 1 valid, 0 invalid" in result.output

    def test_writes_nothing(self, runner: CliRunner, tmp_path: Path) -> None:
        file = self._write(tmp_path, "a.json", self._valid_doc())
        before = sorted(p.name for p in tmp_path.iterdir())
        result = runner.invoke(cli, ["proposals", "validate", str(file)])
        assert result.exit_code == 0
        assert sorted(p.name for p in tmp_path.iterdir()) == before


class TestProposalsContractCLI:
    def test_json_matches_library_document(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            cli,
            [
                "--vault-dir",
                str(tmp_path / "does-not-exist"),
                "proposals",
                "contract",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == proposal_contract()

    def test_human_output_lists_every_kind(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["proposals", "contract"])
        assert result.exit_code == 0
        for kind in PROPOSAL_KINDS:
            assert kind in result.output
        assert "target_id required" in result.output
        assert "target_id forbidden" in result.output
