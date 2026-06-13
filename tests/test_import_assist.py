"""Tests for D11 — `vault extract-text` and agent-assist resume import.

Covers the deterministic extraction command across formats (TXT, MD, DOCX,
plus a minimal hand-built PDF), the D11 provider resolution order in
`import-resume` (flag → BYOK key → assist payload → actionable error), the
new `add-education --from-json` batch path, and a full simulated-agent
loop: assist payload → hardcoded plausible extraction → documented batch
write-back commands → vault assertions → `vault audit --json`.

The resume fixture is a SYNTHETIC person (Jordan Vance) — no real data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from traitprint.cli import cli
from traitprint.git_ops import commit, init_repo
from traitprint.providers.base import LLMResponse, has_provider_signal
from traitprint.taxonomy import find_exact
from traitprint.vault import VaultStore

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RESUME_TXT = FIXTURES / "resume_jordan_vance.txt"
RESUME_MD = FIXTURES / "resume_jordan_vance.md"

PROVIDER_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "OLLAMA_HOST",
)


# ---- fixtures & helpers -----------------------------------------------------


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
def no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every BYOK signal: env keys and the .credentials file."""
    for var in PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "traitprint.providers.base.load_credentials",
        lambda path=None: {},
    )


class FakeProvider:
    """Stand-in LLM returning a canned extraction (BYOK-path tests)."""

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
            content=json.dumps({"profile": {"display_name": "Jordan Vance"}}),
            input_tokens=10,
            output_tokens=10,
            model=self.model,
            provider=self.name,
        )


def _minimal_pdf(lines: list[str]) -> bytes:
    """Author a tiny valid PDF with raw PDF syntax (pypdf can't write).

    One page, one uncompressed content stream of Tj text show operators —
    enough for ``PdfReader.extract_text`` to recover the lines.
    """
    parts = ["BT /F1 12 Tf 72 720 Td 14 TL"]
    for line in lines:
        parts.append(f"({line}) Tj T*")
    parts.append("ET")
    stream = " ".join(parts).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_pos,
    )
    return bytes(out)


def _resume_file(fmt: str, tmp_path: Path) -> Path:
    """Materialize the synthetic resume in the requested format."""
    if fmt == "txt":
        return RESUME_TXT
    if fmt == "md":
        return RESUME_MD
    if fmt == "docx":
        docx = pytest.importorskip("docx")
        path = tmp_path / "resume_jordan_vance.docx"
        document = docx.Document()
        for line in RESUME_TXT.read_text(encoding="utf-8").splitlines():
            document.add_paragraph(line)
        document.save(str(path))
        return path
    if fmt == "pdf":
        pytest.importorskip("pypdf")
        path = tmp_path / "resume_jordan_vance.pdf"
        path.write_bytes(
            _minimal_pdf(
                [
                    "Jordan Vance",
                    "Senior Platform Engineer - Portland, OR",
                    "Northwind Robotics 2021-03 to present",
                    "Kubernetes Golang Terraform Mentoring",
                    "B.S. Computer Science, Lakeview State University",
                ]
            )
        )
        return path
    raise AssertionError(f"unknown format {fmt}")


# ---- vault extract-text -----------------------------------------------------


class TestExtractTextCLI:
    @pytest.mark.parametrize("fmt", ["txt", "md", "docx", "pdf"])
    def test_extracts_each_format(self, fmt: str, tmp_path: Path) -> None:
        path = _resume_file(fmt, tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "extract-text", str(path)])
        assert result.exit_code == 0, result.output
        assert "Jordan Vance" in result.output
        assert "Kubernetes" in result.output

    def test_json_envelope_fields(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["vault", "extract-text", str(RESUME_TXT), "--json"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert set(payload) == {"file", "format", "chars", "text"}
        assert payload["file"] == str(RESUME_TXT)
        assert payload["format"] == "txt"
        assert payload["chars"] == len(payload["text"])
        assert "Jordan Vance" in payload["text"]

    def test_unsupported_format_is_actionable(self, tmp_path: Path) -> None:
        p = tmp_path / "resume.rtf"
        p.write_text("x", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "extract-text", str(p)])
        assert result.exit_code != 0
        assert "Unsupported" in result.output
        assert ".pdf, .docx, .txt, .md" in result.output

    def test_empty_file_errors(self, tmp_path: Path) -> None:
        p = tmp_path / "resume.txt"
        p.write_text("   \n ", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "extract-text", str(p)])
        assert result.exit_code != 0
        assert "zero text" in result.output

    def test_missing_pypdf_names_import_extra(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = tmp_path / "resume.pdf"
        p.write_bytes(b"%PDF-1.4 stub")
        monkeypatch.setitem(sys.modules, "pypdf", None)  # import -> ImportError
        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "extract-text", str(p)])
        assert result.exit_code != 0
        assert "pip install 'traitprint[import]'" in result.output

    def test_missing_python_docx_names_import_extra(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = tmp_path / "resume.docx"
        p.write_bytes(b"PK stub")
        monkeypatch.setitem(sys.modules, "docx", None)
        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "extract-text", str(p)])
        assert result.exit_code != 0
        assert "pip install 'traitprint[import]'" in result.output


# ---- provider signal detection ---------------------------------------------


class TestHasProviderSignal:
    def test_no_signals_is_false(
        self, no_provider: None  # noqa: ARG002 - fixture strips env
    ) -> None:
        assert has_provider_signal() is False

    @pytest.mark.parametrize("var", PROVIDER_ENV_VARS)
    def test_each_env_var_counts(
        self, var: str, no_provider: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(var, "something")
        assert has_provider_signal() is True

    def test_credentials_file_counts(self, no_provider: None) -> None:
        assert has_provider_signal({"openai_api_key": "sk-x"}) is True
        assert has_provider_signal({"ollama_host": "http://gpu-box:11434"}) is True
        assert has_provider_signal({}) is False


# ---- import-resume D11 resolution --------------------------------------------


class TestAssistModeResolution:
    def test_no_provider_emits_payload_exit_zero(
        self, vault_dir: Path, no_provider: None
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "import-resume", str(RESUME_TXT)],
        )
        assert result.exit_code == 0, result.output
        assert "=== TRAITPRINT AGENT-ASSIST PAYLOAD ===" in result.output
        assert "=== END TRAITPRINT AGENT-ASSIST PAYLOAD ===" in result.output
        # Assist mode never writes to the vault.
        assert VaultStore(vault_dir).load().skills == []

    def test_payload_contains_contract_d9_and_text(
        self, vault_dir: Path, no_provider: None
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "import-resume", str(RESUME_TXT)],
        )
        out = result.output
        # (a) instructions to the wrapping agent
        assert "YOU are the model" in out
        # (b) the extraction contract — same schema text the BYOK prompt uses
        from traitprint.mining import _SYSTEM_PROMPT, EXTRACTION_SCHEMA

        assert EXTRACTION_SCHEMA in out
        assert EXTRACTION_SCHEMA in _SYSTEM_PROMPT  # shared, cannot drift
        # D9 rules restated
        assert "proficiency 2-3" in out
        assert "NEVER invent taxonomy ids" in out
        assert "1-5" in out
        # (c) the extracted resume text
        assert "Jordan Vance" in out
        assert "Northwind Robotics" in out
        # (d) write-back through the validated batch path, ending in audit —
        # every command pinned to the resolved vault via --vault-dir.
        prefix = f"traitprint --vault-dir {vault_dir.resolve()} vault"
        assert f"{prefix} set-profile" in out
        assert f"{prefix} add-skill --from-json -" in out
        assert f"{prefix} add-experience --from-json -" in out
        assert f"{prefix} add-education --from-json -" in out
        assert f"{prefix} audit --json" in out

    def test_payload_json_shape(self, vault_dir: Path, no_provider: None) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "import-resume",
                str(RESUME_TXT),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["mode"] == "agent-assist"
        assert payload["file"] == str(RESUME_TXT)
        assert payload["vault_dir"] == str(vault_dir.resolve())
        contract = payload["contract"]
        assert {"output", "schema", "rules", "proposal_rules"} <= set(contract)
        from traitprint.mining import EXTRACTION_RULES, EXTRACTION_SCHEMA

        assert contract["schema"] == EXTRACTION_SCHEMA
        assert contract["rules"] == EXTRACTION_RULES
        assert "Jordan Vance" in payload["text"]
        steps = payload["write_back"]["steps"]
        assert [s["section"] for s in steps] == [
            "profile",
            "skills",
            "experiences",
            "education",
        ]
        prefix = f"traitprint --vault-dir {vault_dir.resolve()} vault"
        for step in steps:
            assert step["command"].startswith(f"{prefix} ")
        for step in steps[1:]:
            assert "--from-json -" in step["command"]
            assert "stdin" in step
        assert payload["write_back"]["verify"] == f"{prefix} audit --json"

    def test_write_back_pins_custom_vault_dir(
        self, vault_dir: Path, no_provider: None
    ) -> None:
        """P2-1: payload write-back must target the vault the CLI resolved.

        With a custom ``--vault-dir``, every write-back command (and the
        audit verify line) carries that resolved directory explicitly, so a
        wrapping agent in any cwd/env cannot write to the wrong vault.
        """
        runner = CliRunner()
        resolved = str(vault_dir.resolve())
        prefix = f"traitprint --vault-dir {resolved} vault"

        # Text payload: --vault-dir on every write-back command + verify.
        result = runner.invoke(
            cli,
            [
                "--vault-dir",
                str(vault_dir),
                "vault",
                "import-resume",
                str(RESUME_TXT),
            ],
        )
        assert result.exit_code == 0, result.output
        for sub in (
            "set-profile",
            "add-skill --from-json -",
            "add-experience --from-json -",
            "add-education --from-json -",
            "audit --json",
        ):
            assert f"{prefix} {sub}" in result.output

        # JSON payload: vault_dir field + the same commands in every step.
        result = runner.invoke(
            cli,
            [
                "--vault-dir",
                str(vault_dir),
                "vault",
                "import-resume",
                str(RESUME_TXT),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["vault_dir"] == resolved
        steps = payload["write_back"]["steps"]
        assert steps
        for step in steps:
            assert f"--vault-dir {resolved} " in step["command"]
        assert payload["write_back"]["verify"] == f"{prefix} audit --json"

    def test_no_assist_with_no_provider_errors(
        self, vault_dir: Path, no_provider: None
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "import-resume",
                str(RESUME_TXT),
                "--no-assist",
            ],
        )
        assert result.exit_code != 0
        assert "No LLM provider configured" in result.output
        assert "ANTHROPIC_API_KEY" in result.output

    def test_assist_flag_forces_payload_even_with_key(
        self,
        vault_dir: Path,
        no_provider: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "import-resume",
                str(RESUME_TXT),
                "--assist",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "=== TRAITPRINT AGENT-ASSIST PAYLOAD ===" in result.output
        assert "Using provider:" not in result.output

    def test_assist_with_provider_flag_is_usage_error(
        self, vault_dir: Path, no_provider: None
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "import-resume",
                str(RESUME_TXT),
                "--assist",
                "--provider",
                "anthropic",
            ],
        )
        assert result.exit_code == 2
        assert "--assist cannot be combined with --provider" in result.output

    def test_byok_path_used_when_key_configured(
        self,
        vault_dir: Path,
        no_provider: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
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
                str(RESUME_TXT),
                "-y",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Using provider: fake" in result.output
        assert "AGENT-ASSIST" not in result.output


# ---- add-education --from-json -----------------------------------------------


class TestAddEducationBatch:
    def test_batch_adds_entries(self, vault_dir: Path) -> None:
        items = [
            {
                "institution": "Lakeview State University",
                "degree": "B.S.",
                "field_of_study": "Computer Science",
                "start_date": "2012",
                "end_date": "2016",
            },
            {"institution": "Cascadia Community College"},
        ]
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "add-education", "--from-json", "-"],
            input=json.dumps(items),
        )
        assert result.exit_code == 0, result.output
        assert "Summary: added 2, errors 0" in result.output
        education = VaultStore(vault_dir).load().education
        assert len(education) == 2
        assert education[0].field_of_study == "Computer Science"

    def test_batch_reports_all_violations(self, vault_dir: Path) -> None:
        items = [{"degree": 12, "description": False}]
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--path", str(vault_dir), "vault", "add-education", "--from-json", "-"],
            input=json.dumps(items),
        )
        assert result.exit_code == 1
        assert "institution: missing required field" in result.output
        assert "degree: must be a string" in result.output
        assert "description: must be a string" in result.output
        assert "Summary: added 0, errors 1" in result.output

    def test_batch_cannot_mix_with_institution_flag(self, vault_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--path",
                str(vault_dir),
                "vault",
                "add-education",
                "--institution",
                "X",
                "--from-json",
                "-",
            ],
            input="[]",
        )
        assert result.exit_code == 2
        assert "--from-json cannot be combined with --institution" in result.output


# ---- the full simulated-agent loop -------------------------------------------

# A plausible extraction the "agent" produced from the assist payload —
# hardcoded, D9-compliant: skills at modest proficiency (<= 3), names only
# (taxonomy resolution is the CLI's job), nothing invented.
AGENT_DRAFT: dict[str, Any] = {
    "profile": {
        "display_name": "Jordan Vance",
        "headline": "Senior Platform Engineer",
        "summary": "Platform engineer with 8 years of experience building "
        "deploy tooling and container infrastructure.",
        "location": "Portland, OR",
        "contact_email": "jordan.vance@example.com",
    },
    "skills": [
        # "Golang" resolves to taxonomy "Go" via alias.
        {"name": "Golang", "proficiency": 3, "category": "technical"},
        {"name": "Kubernetes", "proficiency": 3, "category": "technical"},
        # Not in the taxonomy -> stays first-class with taxonomy_id null.
        {"name": "Terraform", "proficiency": 2, "category": "tool"},
        {"name": "Mentoring", "proficiency": 3, "category": "soft"},
    ],
    "experiences": [
        {
            "title": "Senior Platform Engineer",
            "company": "Northwind Robotics",
            "start_date": "2021-03",
            "end_date": "",
            "description": "Platform team lead for deploy tooling.",
            "accomplishments": [
                "Led migration of 40 microservices from EC2 to Kubernetes, "
                "cutting infrastructure spend 31%",
                "Built a Golang-based deployment CLI adopted by 60 engineers",
            ],
        },
        {
            "title": "Software Engineer",
            "company": "Cascade Analytics",
            "start_date": "2018-06",
            "end_date": "2021-02",
            "description": "ETL and reporting infrastructure.",
            "accomplishments": [
                "Built Python ETL pipelines processing 2 TB/day into PostgreSQL"
            ],
        },
        {
            "title": "Junior Developer",
            "company": "Harborlight Media",
            "start_date": "2016-07",
            "end_date": "2018-05",
            "description": "Content site maintenance and automation.",
            "accomplishments": [],
        },
    ],
    "education": [
        {
            "institution": "Lakeview State University",
            "degree": "B.S.",
            "field_of_study": "Computer Science",
            "start_date": "2012",
            "end_date": "2016",
        }
    ],
}


class TestAgentAssistLoop:
    @pytest.mark.parametrize("fmt", ["txt", "md", "docx"])
    def test_full_loop(
        self,
        fmt: str,
        tmp_path: Path,
        vault_dir: Path,
        no_provider: None,
    ) -> None:
        resume = _resume_file(fmt, tmp_path)
        runner = CliRunner()
        args = ["--path", str(vault_dir)]

        # 1. Deterministic extraction works for this format.
        result = runner.invoke(cli, ["vault", "extract-text", str(resume)])
        assert result.exit_code == 0, result.output
        assert "Jordan Vance" in result.output

        # 2. import-resume auto-enters assist mode; the payload carries the
        #    contract and the extracted text.
        result = runner.invoke(
            cli, [*args, "vault", "import-resume", str(resume), "--json"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["mode"] == "agent-assist"
        assert "Jordan Vance" in payload["text"]
        assert '"skills"' in payload["contract"]["schema"]

        # 3. Simulate the agent: validate the hardcoded draft against the
        #    contract shape, then write back through the documented commands.
        draft = AGENT_DRAFT
        assert set(draft) <= {"profile", "skills", "experiences", "education"}
        assert all(1 <= s["proficiency"] <= 3 for s in draft["skills"])  # D9

        profile = draft["profile"]
        result = runner.invoke(
            cli,
            [
                *args,
                "vault",
                "set-profile",
                "--name",
                profile["display_name"],
                "--headline",
                profile["headline"],
                "--summary",
                profile["summary"],
                "--location",
                profile["location"],
                "--email",
                profile["contact_email"],
            ],
        )
        assert result.exit_code == 0, result.output

        result = runner.invoke(
            cli,
            [*args, "vault", "add-skill", "--from-json", "-"],
            input=json.dumps(draft["skills"]),
        )
        assert result.exit_code == 0, result.output
        assert "Summary: added 4, errors 0" in result.output

        result = runner.invoke(
            cli,
            [*args, "vault", "add-experience", "--from-json", "-"],
            input=json.dumps(draft["experiences"]),
        )
        assert result.exit_code == 0, result.output
        assert "Summary: added 3, errors 0" in result.output

        result = runner.invoke(
            cli,
            [*args, "vault", "add-education", "--from-json", "-"],
            input=json.dumps(draft["education"]),
        )
        assert result.exit_code == 0, result.output
        assert "Summary: added 1, errors 0" in result.output

        # 4. Vault contents: profile set; skills at modest proficiency with
        #    deterministic taxonomy resolution (alias match vs null).
        vault = VaultStore(vault_dir).load()
        assert vault.profile.display_name == "Jordan Vance"
        assert vault.profile.contact_email == "jordan.vance@example.com"

        skills = {s.name: s for s in vault.skills}
        assert set(skills) == {"Golang", "Kubernetes", "Terraform", "Mentoring"}
        assert all(s.proficiency <= 3 for s in vault.skills)
        go_entry = find_exact("Golang")
        assert go_entry is not None and go_entry.name == "Go"
        assert skills["Golang"].taxonomy_id == go_entry.id  # alias resolved
        kube_entry = find_exact("Kubernetes")
        assert kube_entry is not None
        assert skills["Kubernetes"].taxonomy_id == kube_entry.id
        # Canonical taxonomy (item 4) now includes Terraform, so it resolves
        # rather than landing as an unresolved first-class skill.
        terraform_entry = find_exact("Terraform")
        assert terraform_entry is not None
        assert skills["Terraform"].taxonomy_id == terraform_entry.id

        assert len(vault.experiences) == 3
        assert vault.experiences[0].company == "Northwind Robotics"
        assert len(vault.education) == 1
        assert vault.education[0].institution == "Lakeview State University"

        # 5. The documented finishing step parses.
        result = runner.invoke(cli, [*args, "vault", "audit", "--json"])
        assert result.exit_code == 0, result.output
        report = json.loads(result.output)
        assert "findings" in report and "summary" in report
