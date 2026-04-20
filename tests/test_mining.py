"""Tests for Slice D — Resume import + BYOK skill mining."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from traitprint.cli import cli
from traitprint.git_ops import commit, init_repo
from traitprint.mining import (
    ResumeExtractionError,
    draft_from_dict,
    extract_resume_text,
    parse_llm_response,
    resume_to_draft,
)
from traitprint.providers import LLMError
from traitprint.providers.base import LLMResponse
from traitprint.vault import VaultStore

# ---- fixtures -------------------------------------------------------------


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
def store(vault_dir: Path) -> VaultStore:
    return VaultStore(vault_dir)


# A canned LLM response matching the prompt schema.
SAMPLE_LLM_JSON = {
    "profile": {
        "display_name": "Wesley Johnson",
        "headline": "Data Engineering Leader",
        "summary": "10+ years building analytics systems.",
        "location": "Kansas City, MO",
        "contact_email": "wesley@dataviking.tech",
    },
    "skills": [
        {"name": "Python", "category": "technical", "proficiency": 9, "notes": "10y"},
        {"name": "Redshift", "category": "tool", "proficiency": 7, "notes": ""},
        {"name": "Team Leadership", "category": "soft", "proficiency": 8, "notes": ""},
    ],
    "experiences": [
        {
            "title": "Senior Manager, Data Analytics",
            "company": "Peloton Interactive",
            "start_date": "2022-01",
            "end_date": "2025-12",
            "description": "Led analytics engineering team.",
            "accomplishments": ["Scaled team 0→6", "Led Redshift→BigQuery migration"],
        }
    ],
    "education": [
        {
            "institution": "Kansas State University",
            "degree": "Bachelor",
            "field_of_study": "Business Administration",
            "start_date": "2011",
            "end_date": "2015",
            "description": "",
        }
    ],
}


class FakeProvider:
    """Stand-in LLM that returns a canned JSON response."""

    name = "fake"
    model = "fake-1"

    def __init__(self, response_json: dict[str, Any] | None = None) -> None:
        self._body = response_json if response_json is not None else SAMPLE_LLM_JSON
        self.last_system: str = ""
        self.last_user: str = ""

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.last_system = system
        self.last_user = user
        return LLMResponse(
            content=json.dumps(self._body),
            input_tokens=500,
            output_tokens=200,
            model=self.model,
            provider=self.name,
        )


# ---- extract_resume_text --------------------------------------------------


class TestExtractResumeText:
    def test_txt_file(self, tmp_path: Path) -> None:
        p = tmp_path / "resume.txt"
        p.write_text("hello world", encoding="utf-8")
        assert extract_resume_text(p) == "hello world"

    def test_md_file(self, tmp_path: Path) -> None:
        p = tmp_path / "resume.md"
        p.write_text("# Wesley\nEngineer", encoding="utf-8")
        assert "Wesley" in extract_resume_text(p)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ResumeExtractionError, match="not found"):
            extract_resume_text(tmp_path / "nope.pdf")

    def test_unsupported_format_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "resume.rtf"
        p.write_text("x", encoding="utf-8")
        with pytest.raises(ResumeExtractionError, match="Unsupported"):
            extract_resume_text(p)


# ---- parse_llm_response ---------------------------------------------------


class TestParseLLMResponse:
    def test_plain_json(self) -> None:
        data = parse_llm_response('{"a": 1}')
        assert data == {"a": 1}

    def test_empty_raises(self) -> None:
        with pytest.raises(LLMError, match="empty"):
            parse_llm_response("")

    def test_invalid_raises(self) -> None:
        with pytest.raises(LLMError, match="not valid JSON"):
            parse_llm_response("this is not json at all")

    def test_strips_markdown_fence(self) -> None:
        data = parse_llm_response('```json\n{"a": 2}\n```')
        assert data == {"a": 2}

    def test_grabs_embedded_object(self) -> None:
        # Model adds prose around the JSON.
        data = parse_llm_response('Sure! Here is: {"a": 3} hope this helps')
        assert data == {"a": 3}


# ---- draft_from_dict ------------------------------------------------------


class TestDraftFromDict:
    def test_full_parse(self) -> None:
        draft = draft_from_dict(SAMPLE_LLM_JSON)
        assert draft.profile.display_name == "Wesley Johnson"
        assert len(draft.skills) == 3
        assert draft.skills[0].name == "Python"
        assert draft.skills[0].source == "mined"
        assert len(draft.experiences) == 1
        assert draft.experiences[0].source == "imported"
        assert draft.experiences[0].accomplishments == [
            "Scaled team 0→6",
            "Led Redshift→BigQuery migration",
        ]
        assert len(draft.education) == 1

    def test_empty_dict(self) -> None:
        draft = draft_from_dict({})
        assert draft.profile.display_name == ""
        assert draft.skills == []
        assert draft.experiences == []
        assert draft.education == []

    def test_proficiency_clamped(self) -> None:
        draft = draft_from_dict({"skills": [{"name": "X", "proficiency": 99}]})
        assert draft.skills[0].proficiency == 10

        draft = draft_from_dict({"skills": [{"name": "Y", "proficiency": -5}]})
        assert draft.skills[0].proficiency == 1

        draft = draft_from_dict({"skills": [{"name": "Z", "proficiency": "eight"}]})
        assert draft.skills[0].proficiency == 6  # default

    def test_skips_skills_without_names(self) -> None:
        draft = draft_from_dict({"skills": [{"name": "", "proficiency": 5}]})
        assert draft.skills == []

    def test_skips_experiences_without_titles(self) -> None:
        draft = draft_from_dict(
            {"experiences": [{"title": "", "company": "Acme"}]}
        )
        assert draft.experiences == []

    def test_skips_education_without_institution(self) -> None:
        draft = draft_from_dict({"education": [{"degree": "BS"}]})
        assert draft.education == []


# ---- resume_to_draft end-to-end ------------------------------------------


class TestResumeToDraft:
    def test_full_pipeline(self, tmp_path: Path) -> None:
        resume = tmp_path / "r.txt"
        resume.write_text("Wesley Johnson — Data Engineer. Python, Redshift.", "utf-8")

        provider = FakeProvider()
        draft = resume_to_draft(resume, provider)

        assert draft.profile.display_name == "Wesley Johnson"
        assert len(draft.skills) == 3
        assert draft.usage is not None
        assert draft.usage.input_tokens == 500
        assert "Wesley Johnson" in provider.last_user

    def test_empty_text_raises(self, tmp_path: Path) -> None:
        resume = tmp_path / "r.txt"
        resume.write_text("   \n  ", encoding="utf-8")
        with pytest.raises(ResumeExtractionError, match="zero text"):
            resume_to_draft(resume, FakeProvider())

    def test_truncates_long_resume(self, tmp_path: Path) -> None:
        resume = tmp_path / "r.txt"
        resume.write_text("x" * 100_000, encoding="utf-8")
        provider = FakeProvider()
        resume_to_draft(resume, provider, max_chars=1000)
        # The user prompt should contain at most ~1000 resume chars.
        assert provider.last_user.count("x") <= 1000


# ---- ResumeDraft.summary_lines -------------------------------------------


class TestSummaryLines:
    def test_shows_counts_and_items(self) -> None:
        draft = draft_from_dict(SAMPLE_LLM_JSON)
        lines = draft.summary_lines()
        joined = "\n".join(lines)
        assert "Wesley Johnson" in joined
        assert "Skills: 3" in joined
        assert "Experiences: 1" in joined
        assert "Education: 1" in joined
        assert "Python" in joined
        assert "Peloton Interactive" in joined

    def test_caps_long_skill_lists(self) -> None:
        data = {"skills": [{"name": f"S{i}", "proficiency": 5} for i in range(25)]}
        lines = draft_from_dict(data).summary_lines()
        assert any("and 15 more" in line for line in lines)


# ---- VaultStore.import_from_draft ----------------------------------------


class TestImportFromDraft:
    def test_appends_and_commits(self, store: VaultStore, vault_dir: Path) -> None:
        draft = draft_from_dict(SAMPLE_LLM_JSON)
        counts = store.import_from_draft(
            profile=draft.profile,
            skills=draft.skills,
            experiences=draft.experiences,
            education=draft.education,
            commit_message="Import resume: test.pdf",
        )
        assert counts == {"skills": 3, "experiences": 1, "education": 1}

        reloaded = store.load()
        assert reloaded.profile.display_name == "Wesley Johnson"
        assert len(reloaded.skills) == 3
        assert len(reloaded.experiences) == 1
        assert len(reloaded.education) == 1

        result = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=vault_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "Import resume: test.pdf" in result.stdout

    def test_profile_merges_only_nonempty(self, store: VaultStore) -> None:
        # Seed vault with an existing headline.
        v = store.load()
        v.profile.headline = "Existing Headline"
        v.profile.contact_email = "old@example.com"
        store.save(v)

        from traitprint.schema import ProfileSchema

        incoming = ProfileSchema(display_name="New Name", headline="")
        store.import_from_draft(
            profile=incoming, commit_message="test profile merge"
        )

        reloaded = store.load()
        assert reloaded.profile.display_name == "New Name"
        assert reloaded.profile.headline == "Existing Headline"  # preserved
        assert reloaded.profile.contact_email == "old@example.com"  # preserved

    def test_update_profile_false_leaves_profile_alone(
        self, store: VaultStore
    ) -> None:
        from traitprint.schema import ProfileSchema

        store.import_from_draft(
            profile=ProfileSchema(display_name="Nope"),
            update_profile=False,
            commit_message="test",
        )
        assert store.load().profile.display_name == ""


# ---- CLI integration -----------------------------------------------------


class TestImportResumeCLI:
    def test_end_to_end(
        self,
        tmp_path: Path,
        vault_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        resume = tmp_path / "resume.txt"
        resume.write_text(
            "Wesley Johnson — Senior Data Engineer.\nPython, SQL.", encoding="utf-8"
        )

        provider = FakeProvider()
        monkeypatch.setattr(
            "traitprint.providers.detect_provider", lambda **kwargs: provider
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "import-resume", str(resume), "-y"],
        )
        assert result.exit_code == 0, result.output
        assert "Wesley Johnson" in result.output
        assert "Imported" in result.output
        assert "Usage:" in result.output

        reloaded = VaultStore(vault_dir).load()
        assert len(reloaded.skills) == 3
        assert reloaded.profile.display_name == "Wesley Johnson"

    def test_dry_run_does_not_modify(
        self,
        tmp_path: Path,
        vault_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        resume = tmp_path / "resume.txt"
        resume.write_text("Wesley — Engineer. Python.", encoding="utf-8")

        monkeypatch.setattr(
            "traitprint.providers.detect_provider", lambda **kwargs: FakeProvider()
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "import-resume",
                str(resume),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert VaultStore(vault_dir).load().skills == []

    def test_declining_confirmation_cancels(
        self,
        tmp_path: Path,
        vault_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        resume = tmp_path / "resume.txt"
        resume.write_text("x" * 30, encoding="utf-8")

        monkeypatch.setattr(
            "traitprint.providers.detect_provider", lambda **kwargs: FakeProvider()
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "import-resume", str(resume)],
            input="n\n",
        )
        assert result.exit_code == 0
        assert "Cancelled" in result.output
        assert VaultStore(vault_dir).load().skills == []

    def test_missing_vault_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resume = tmp_path / "resume.txt"
        resume.write_text("x", encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--path",
                str(tmp_path / "nonexistent"),
                "vault",
                "import-resume",
                str(resume),
                "-y",
            ],
        )
        assert result.exit_code != 0
        assert "traitprint init" in result.output

    def test_provider_not_configured_error(
        self,
        tmp_path: Path,
        vault_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        resume = tmp_path / "resume.txt"
        resume.write_text("x", encoding="utf-8")

        from traitprint.providers import ProviderNotConfigured

        def boom(**kwargs: Any) -> None:
            raise ProviderNotConfigured("No keys set")

        monkeypatch.setattr("traitprint.providers.detect_provider", boom)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "import-resume", str(resume), "-y"],
        )
        assert result.exit_code != 0
        assert "No keys set" in result.output
