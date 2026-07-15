"""Doc-truth conformance: agent-facing docs must match the code.

The agent surfaces (AGENTS.md, README.md, GEMINI.md,
docs/distribution-runbook.md, skills/shared/cli-reference.md) state
counts and enumerate registries. These tests parse those files and pin
every such claim to the live code, so shipping a new skill, MCP tool,
prompt, or export format fails here until the docs are updated:

- any explicit "<N> ... skills" numeral  == ``len(SKILL_NAMES)``
- any explicit "<N> ... tools" numeral   == the local server's tool count
- any explicit "<N> ... prompts" numeral == the local server's prompt count
- README's "plus <N> skills with no prompt counterpart" == skills - prompts
- every local MCP tool name is documented in AGENTS.md and README.md,
  named in the MCP serving note appended to every served prompt, and
  every hosted-shared one (all but ``doctor``) in GEMINI.md
- every MCP prompt name is documented in AGENTS.md and README.md
- every bundled skill is named in GEMINI.md's bundled-skills list
- every ``vault export -f`` choice appears inside the export reference
  of AGENTS.md, README.md, and the shared cli-reference
- any inlined ``update_profile`` ``{"basics": {...}}`` key list matches
  ``traitprint.proposals``
- AGENTS.md's stated "current vault contract revision" matches the
  revision declared by docs/schema/vault-v1/README.md

Counting convention for doc authors: prefer count-free phrasing ("the
bundled skills", "the tools below"). Where a numeral is deliberate it
must be adjacent to the noun ("seven read-only tools") so it is pinned
here. Hosted-server tool counts cannot be verified from this repo — keep
them count-free.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from traitprint.cli import _EXPORT_FORMAT_CHOICES
from traitprint.mcp_server import _MCP_SERVING_NOTE, create_server
from traitprint.proposals import (
    _PROFILE_BASICS_KEYS,
    PROPOSAL_KINDS,
    PROPOSAL_STATUSES,
)
from traitprint.skills import SKILL_NAMES
from traitprint.vault import VaultStore

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The agent surfaces under conformance. Keys are repo-relative paths.
DOC_PATHS = (
    "AGENTS.md",
    "README.md",
    "GEMINI.md",
    "docs/distribution-runbook.md",
    "skills/shared/cli-reference.md",
)

_WORD_TO_INT = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

#: Adjective tokens allowed between a numeral and skills/tools/prompts for
#: the phrase to count as a registry-size claim ("seven read-only tools",
#: "Nine SKILL.md workflow skills"). Anything else ("10 skills + 2-3
#: stories" style vault-content examples) is not treated as a claim.
_MODIFIER_WORDS = frozenset(
    {
        "skill.md",
        "workflow",
        "agent",
        "query",
        "read-only",
        "read",
        "bundled",
        "mcp",
        "stdio",
        "local",
        "local-only",
        "hosted",
        "cloud",
    }
)

_COUNT_RE = re.compile(
    r"(?<![~\w.$-])"
    r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d+)"
    r"((?:\s+\[?[A-Za-z][\w.()\]/-]*){0,2}?)"
    r"\s+(skills|tools|prompts)\b",
    re.IGNORECASE,
)

_BASICS_RE = re.compile(r"\{\"basics\":\s*\{(.*?)\}\}", re.DOTALL)

#: Locators for each file's export-format reference. Formats must appear
#: inside this snippet (token-bounded), not merely anywhere in the file —
#: bare substring matching would let prose "json"/"markdown" pass.
_EXPORT_SNIPPET_RES = {
    "AGENTS.md": re.compile(r"^\| `traitprint vault export .*$", re.MULTILINE),
    "README.md": re.compile(r"^\| Export \(.*$", re.MULTILINE),
    "skills/shared/cli-reference.md": re.compile(
        r"^traitprint vault export .*?(?=^traitprint )", re.MULTILINE | re.DOTALL
    ),
}

#: The on-disk format contract; its declared revision is what AGENTS.md's
#: "current vault contract revision is X.Y" claim must match.
_SCHEMA_README = "docs/schema/vault-v1/README.md"
_DECLARED_REVISION_RE = re.compile(
    r"^\*\*Contract revision:\*\*\s*(\d+(?:\.\d+)+)", re.MULTILINE
)
_CLAIMED_REVISION_RE = re.compile(
    r"current\s+vault\s+contract\s+revision\s+is\s+(\d+(?:\.\d+)+)"
)


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def docs() -> dict[str, str]:
    return {rel: _read(rel) for rel in DOC_PATHS}


@pytest.fixture(scope="module")
def live_server_names(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[frozenset[str], frozenset[str]]:
    """(tool names, prompt names) from the real FastMCP registry.

    ``create_server`` never touches the vault at registration time, so an
    uninitialized store on an empty directory is enough to enumerate.
    """
    server = create_server(VaultStore(tmp_path_factory.mktemp("doc-truth")))
    tools = frozenset(t.name for t in asyncio.run(server.list_tools()))
    prompts = frozenset(p.name for p in asyncio.run(server.list_prompts()))
    return tools, prompts


def _count_claims(text: str) -> list[tuple[int, str, str, str]]:
    """Yield (value, noun, matched phrase, trailing context) claims."""
    claims: list[tuple[int, str, str, str]] = []
    for m in _COUNT_RE.finditer(text):
        raw, middle, noun = m.group(1), m.group(2), m.group(3)
        tokens = [t.strip("[]()`*_/").lower() for t in middle.split()]
        if any(t and t not in _MODIFIER_WORDS for t in tokens):
            continue
        value = _WORD_TO_INT.get(raw.lower())
        if value is None:
            value = int(raw)
        # Whitespace-normalized so line wraps don't hide the context.
        trailing = re.sub(r"\s+", " ", text[m.end() : m.end() + 40])
        claims.append((value, noun.lower(), m.group(0), trailing))
    return claims


class TestCounts:
    def test_every_stated_count_matches_the_registries(
        self,
        docs: dict[str, str],
        live_server_names: tuple[frozenset[str], frozenset[str]],
    ) -> None:
        tools, prompts = live_server_names
        expected = {
            "skills": len(SKILL_NAMES),
            "tools": len(tools),
            "prompts": len(prompts),
        }
        problems: list[str] = []
        total_claims = 0
        for rel, text in docs.items():
            for value, noun, phrase, trailing in _count_claims(text):
                total_claims += 1
                want = expected[noun]
                # README's derived claim: skills with no prompt counterpart.
                if noun == "skills" and "no prompt counterpart" in trailing:
                    want = len(SKILL_NAMES) - len(prompts)
                if value != want:
                    problems.append(
                        f"{rel}: says {phrase!r} but the live count is "
                        f"{want} — update the doc (or use count-free "
                        f"phrasing) to match the code"
                    )
        assert not problems, "\n".join(problems)
        # Positive-match floor: the corpus is known to carry at least this
        # many deliberate count claims (was 9 until AGENTS.md's skill/prompt
        # counts went count-free when the quality skills landed; the six
        # that remain are the stable tool counts, README's prompt counts,
        # and the derived no-prompt-counterpart claim). If a rephrase drops
        # below it, the claim parser stopped seeing a phrase it used to
        # check (e.g. a modifier word missing from _MODIFIER_WORDS) —
        # extend the allowlist or lower the floor consciously, don't let
        # the test go vacuous.
        assert total_claims >= 6, (
            f"only {total_claims} count claims parsed across "
            f"{', '.join(DOC_PATHS)} (expected >= 6) — a phrasing change "
            f"likely made a claim invisible to _count_claims()"
        )

    def test_the_registries_are_nonempty_sanity(
        self, live_server_names: tuple[frozenset[str], frozenset[str]]
    ) -> None:
        tools, prompts = live_server_names
        assert len(SKILL_NAMES) >= 8
        assert len(tools) >= 7
        assert len(prompts) >= 6


class TestToolAndPromptNames:
    def test_local_tools_documented_in_agents_and_readme(
        self,
        docs: dict[str, str],
        live_server_names: tuple[frozenset[str], frozenset[str]],
    ) -> None:
        tools, _ = live_server_names
        problems = [
            f"{rel}: local MCP tool `{name}` is not documented — "
            f"add it to the MCP tool list"
            for rel in ("AGENTS.md", "README.md")
            for name in sorted(tools)
            if f"`{name}`" not in docs[rel]
        ]
        assert not problems, "\n".join(problems)

    def test_hosted_shared_tools_documented_in_gemini(
        self,
        docs: dict[str, str],
        live_server_names: tuple[frozenset[str], frozenset[str]],
    ) -> None:
        tools, _ = live_server_names
        shared = tools - {"doctor"}  # doctor is local-only by design
        missing = [
            name for name in sorted(shared) if f"`{name}`" not in docs["GEMINI.md"]
        ]
        assert not missing, (
            f"GEMINI.md: hosted-shared MCP tools missing from the tool "
            f"list: {missing}"
        )

    def test_prompts_documented_in_agents_and_readme(
        self,
        docs: dict[str, str],
        live_server_names: tuple[frozenset[str], frozenset[str]],
    ) -> None:
        _, prompts = live_server_names
        # Prefix match (no closing backtick): docs legitimately write
        # prompts with their arguments, e.g. `fill_vault(focus?)`.
        problems = [
            f"{rel}: MCP prompt `{name}` is not documented"
            for rel in ("AGENTS.md", "README.md")
            for name in sorted(prompts)
            if f"`{name}" not in docs[rel]
        ]
        assert not problems, "\n".join(problems)

    def test_serving_note_names_every_live_tool(
        self, live_server_names: tuple[frozenset[str], frozenset[str]]
    ) -> None:
        """The MCP serving note is the operating instruction appended to
        every served prompt — a tool it doesn't name is invisible to
        shell-less MCP consumers. This is exactly where the four-tool
        list once went stale."""
        tools, _ = live_server_names
        missing = [
            name for name in sorted(tools) if f"`{name}`" not in _MCP_SERVING_NOTE
        ]
        assert not missing, (
            f"mcp_server._MCP_SERVING_NOTE does not name the live tools "
            f"{missing} — extend the note so served prompts advertise the "
            f"full read surface"
        )


class TestSkillNames:
    def test_every_bundled_skill_is_named_in_gemini(
        self, docs: dict[str, str]
    ) -> None:
        text = docs["GEMINI.md"]
        missing = [
            name
            for name in SKILL_NAMES
            if name.removeprefix("traitprint-") not in text
        ]
        assert not missing, (
            f"GEMINI.md: bundled skills missing from the bundled-skills "
            f"list: {missing} — a new skill must be named there"
        )


class TestExportFormats:
    def test_every_export_format_is_documented(self, docs: dict[str, str]) -> None:
        problems: list[str] = []
        for rel, snippet_re in _EXPORT_SNIPPET_RES.items():
            m = snippet_re.search(docs[rel])
            assert m, (
                f"{rel}: export-format reference not found — the locator in "
                f"_EXPORT_SNIPPET_RES no longer matches; update it alongside "
                f"the doc"
            )
            snippet = m.group(0)
            for fmt in _EXPORT_FORMAT_CHOICES:
                # Token-bounded within the export reference itself, so
                # prose "json"/"markdown" elsewhere can't satisfy this,
                # and "json" can't match inside "jsonresume"/"json-resume".
                if not re.search(rf"(?<![\w-]){re.escape(fmt)}(?![\w-])", snippet):
                    problems.append(
                        f"{rel}: export format {fmt!r} (from "
                        f"cli._EXPORT_FORMAT_CHOICES) is missing from the "
                        f"export command reference"
                    )
        assert not problems, "\n".join(problems)


class TestProposalBasics:
    def test_inlined_basics_key_lists_match_the_contract(
        self, docs: dict[str, str]
    ) -> None:
        canonical = set(_PROFILE_BASICS_KEYS)
        problems: list[str] = []
        seen_any = False
        for rel, text in docs.items():
            for m in _BASICS_RE.finditer(text):
                seen_any = True
                keys = set(re.findall(r"\"([a-z_]+)\"\s*\??", m.group(1)))
                if keys != canonical:
                    problems.append(
                        f"{rel}: inlined update_profile basics keys {sorted(keys)} "
                        f"!= contract {sorted(canonical)} (traitprint.proposals)"
                    )
        assert seen_any, (
            "no inlined {\"basics\": {...}} example found in any doc — if the "
            "docs dropped the inline list on purpose, delete this check"
        )
        assert not problems, "\n".join(problems)


class TestContractRevision:
    def test_agents_md_states_the_declared_revision(
        self, docs: dict[str, str]
    ) -> None:
        declared_m = _DECLARED_REVISION_RE.search(_read(_SCHEMA_README))
        assert declared_m, (
            f"{_SCHEMA_README}: no '**Contract revision:** X.Y' line found — "
            f"update _DECLARED_REVISION_RE alongside the schema README"
        )
        declared = declared_m.group(1)
        claims = _CLAIMED_REVISION_RE.findall(docs["AGENTS.md"])
        assert claims, (
            "AGENTS.md: no 'current vault contract revision is X.Y' claim "
            "found — if the phrasing changed, update _CLAIMED_REVISION_RE; "
            "if the claim was dropped on purpose, delete this check"
        )
        problems = [
            f"AGENTS.md: claims contract revision {claim} but "
            f"{_SCHEMA_README} declares {declared}"
            for claim in claims
            if claim != declared
        ]
        assert not problems, "\n".join(problems)


class TestSchemaProposalEnums:
    """The contract JSON Schema's proposal enums must match the code.

    External validators consume ``vault-v1.schema.json`` directly, so
    its ``$defs/proposal`` kind/status enums must never drift from
    ``PROPOSAL_KINDS``/``PROPOSAL_STATUSES`` (the reference validation
    shared with the hosted ``vault_propose``). The 1.4 lens kinds were
    missed in the schema once; this pins the two surfaces together.
    """

    @staticmethod
    def _proposal_def() -> dict[str, object]:
        schema = json.loads(_read("docs/schema/vault-v1/vault-v1.schema.json"))
        proposal = schema["$defs"]["proposal"]
        assert isinstance(proposal, dict)
        return proposal

    def test_kind_enum_matches_proposal_kinds(self) -> None:
        properties = self._proposal_def()["properties"]
        assert isinstance(properties, dict)
        enum = properties["kind"]["enum"]
        assert set(enum) == set(PROPOSAL_KINDS), (
            "vault-v1.schema.json $defs/proposal kind enum drifted from "
            "traitprint.proposals.PROPOSAL_KINDS — update both together "
            "(README revision history + $comment per CLAUDE.md)"
        )
        assert len(enum) == len(set(enum)), "duplicate kinds in the schema enum"

    def test_status_enum_matches_proposal_statuses(self) -> None:
        properties = self._proposal_def()["properties"]
        assert isinstance(properties, dict)
        enum = properties["status"]["enum"]
        assert set(enum) == set(PROPOSAL_STATUSES), (
            "vault-v1.schema.json $defs/proposal status enum drifted from "
            "traitprint.proposals.PROPOSAL_STATUSES"
        )
