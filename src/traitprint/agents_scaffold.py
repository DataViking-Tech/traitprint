"""Scaffold agent-runtime entrypoints that delegate to one AGENTS.md.

``traitprint agents init [DIR]`` bootstraps a project directory for agent
CLIs: thin per-runtime wrapper files (a few lines each) that all point at
a single canonical ``AGENTS.md``, copies of the shipped Agent Skills, and
per-runtime MCP registration for ``traitprint mcp-serve``.

Design rules (issue #66):

- **Wrappers are data, not logic.** Every wrapper stays under five
  non-empty lines and only points at ``AGENTS.md``; supporting a new
  runtime is an additive entry in :data:`WRAPPERS` /
  :data:`MCP_REGISTRATIONS`, never a new code path. (This
  thin-wrappers-pointing-at-one-manual layout is the bootstrap pattern
  popularized by career-ops — nominative attribution only; no career-ops
  branding may appear in any scaffolder-authored file or user-facing
  string. Verbatim copies of the shipped Agent Skills are exempt: their
  canonical content may reference career-ops nominatively, per the
  repo-wide policy.)
- **Only the target directory is written, and nothing is overwritten.**
  Registrations that live in the user's home directory (Codex CLI,
  Kimi CLI) are emitted as copy-paste snippets instead of being written.
- **Gemini CLI is intentionally absent.** The repo already publishes a
  Gemini extension (``gemini-extension.json`` + ``GEMINI.md``) that wires
  the hosted MCP server and bundles the skills.

The canonical manual resolves like the skills do (``traitprint.skills``):
package data first (built wheels force-include the repo-root ``AGENTS.md``
as ``traitprint/data/AGENTS.md``), then the repo root for source checkouts
and editable installs.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path, PurePosixPath

from traitprint.skills import skills_root

if sys.version_info >= (3, 11):
    from importlib.resources.abc import Traversable
else:  # pragma: no cover - exercised on Python 3.10 only
    from importlib.abc import Traversable


class ScaffoldError(RuntimeError):
    """Raised when the scaffolder cannot produce its inputs or outputs."""


# ── Canonical manual ─────────────────────────────────────────────────


def agents_manual() -> str:
    """Return the canonical AGENTS.md text (the agent operating manual).

    Tries the package-data copy (installed wheels) first, then the
    repo-root ``AGENTS.md`` (source checkouts / editable installs).
    """
    packaged = files("traitprint.data").joinpath("AGENTS.md")
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[2] / "AGENTS.md"
    if repo_root.is_file():
        return repo_root.read_text(encoding="utf-8")
    raise ScaffoldError(
        "No AGENTS.md found (looked in traitprint/data package data "
        f"and {repo_root})."
    )


# ── Per-runtime wrapper files (thin, additive) ───────────────────────


@dataclass(frozen=True)
class Wrapper:
    """A thin per-runtime entrypoint file that delegates to AGENTS.md."""

    runtime: str  #: stable key, e.g. ``"claude"``
    label: str  #: display name, e.g. ``"Claude Code"``
    path: str  #: project-relative file path
    text: str  #: full file contents


def _wrapper_text(label: str, tail: str) -> str:
    """Render a wrapper body: one heading plus a 3-line delegation note."""
    return (
        f"# Traitprint — {label} entrypoint\n"
        "\n"
        "Read AGENTS.md at the project root and follow it: it is the\n"
        "Traitprint operating manual (CLI reference, vault format, and\n"
        f"safety rules). {tail}\n"
    )


WRAPPERS: tuple[Wrapper, ...] = (
    Wrapper(
        runtime="claude",
        label="Claude Code",
        path="CLAUDE.md",
        text=_wrapper_text(
            "Claude Code",
            "Workflow skills live in .claude/skills/.",
        ),
    ),
    Wrapper(
        runtime="qwen",
        label="Qwen Code",
        path="QWEN.md",
        text=_wrapper_text(
            "Qwen Code",
            "Workflow skills live in .agents/skills/.",
        ),
    ),
    Wrapper(
        runtime="grok",
        label="Grok CLI",
        path=".grok/GROK.md",
        text=_wrapper_text(
            "Grok CLI",
            "Workflow skills live in .agents/skills/.",
        ),
    ),
)

#: Runtimes that read the canonical AGENTS.md directly — no wrapper file.
NATIVE_AGENTS_MD_RUNTIMES: tuple[str, ...] = ("Codex CLI", "OpenCode", "Kimi CLI")


# ── Per-runtime MCP registration for `traitprint mcp-serve` ──────────


@dataclass(frozen=True)
class McpRegistration:
    """How one agent CLI registers the ``traitprint mcp-serve`` server."""

    runtime: str  #: stable key, e.g. ``"codex"``
    label: str  #: display name, e.g. ``"Codex CLI"``
    path: str  #: config file path (project-relative, or ``~/…`` for home)
    snippet: str  #: exact registration text for that file
    in_project: bool  #: True → the scaffolder writes the file when absent


def _mcp_servers_json() -> str:
    payload = {
        "mcpServers": {
            "traitprint": {"command": "traitprint", "args": ["mcp-serve"]}
        }
    }
    return json.dumps(payload, indent=2) + "\n"


def _opencode_json() -> str:
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "traitprint": {
                "type": "local",
                "command": ["traitprint", "mcp-serve"],
                "enabled": True,
            }
        },
    }
    return json.dumps(payload, indent=2) + "\n"


_CODEX_TOML = (
    "[mcp_servers.traitprint]\n"
    'command = "traitprint"\n'
    'args = ["mcp-serve"]\n'
)


def mcp_entry_registered(reg: McpRegistration, target: Path) -> bool:
    """True if ``reg``'s config file already registers a traitprint server.

    Looks at the real config file — ``target/reg.path`` for project-scoped
    registrations, the expanded home path otherwise (read-only; the
    scaffolder still never *writes* outside the target directory) — and
    checks for a ``traitprint`` entry in that runtime's format: a
    ``traitprint`` key under ``mcpServers`` (Claude/Qwen/Grok/Kimi) or
    ``mcp`` (OpenCode), or an ``[mcp_servers.traitprint]`` table (Codex
    TOML). A missing, unreadable, or unparsable file counts as
    unregistered, so the caller keeps emitting the snippet for it.
    """
    config = target / reg.path if reg.in_project else Path(reg.path).expanduser()
    if not config.is_file():
        return False
    try:
        text = config.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if config.suffix == ".toml":
        return any(
            line.strip().startswith("[mcp_servers.traitprint]")
            for line in text.splitlines()
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    return any(
        isinstance(servers := payload.get(key), dict) and "traitprint" in servers
        for key in ("mcpServers", "mcp")
    )


MCP_REGISTRATIONS: tuple[McpRegistration, ...] = (
    McpRegistration(
        runtime="claude",
        label="Claude Code",
        path=".mcp.json",
        snippet=_mcp_servers_json(),
        in_project=True,
    ),
    McpRegistration(
        runtime="codex",
        label="Codex CLI",
        path="~/.codex/config.toml",
        snippet=_CODEX_TOML,
        in_project=False,
    ),
    McpRegistration(
        runtime="opencode",
        label="OpenCode",
        path="opencode.json",
        snippet=_opencode_json(),
        in_project=True,
    ),
    McpRegistration(
        runtime="qwen",
        label="Qwen Code",
        path=".qwen/settings.json",
        snippet=_mcp_servers_json(),
        in_project=True,
    ),
    McpRegistration(
        runtime="grok",
        label="Grok CLI",
        path=".grok/settings.json",
        snippet=_mcp_servers_json(),
        in_project=True,
    ),
    McpRegistration(
        runtime="kimi",
        label="Kimi CLI",
        path="~/.kimi/mcp.json",
        snippet=_mcp_servers_json(),
        in_project=False,
    ),
)


# ── Skill copies ─────────────────────────────────────────────────────

#: Where the shipped skills are copied inside the target directory.
#: ``.agents/skills/`` is the cross-vendor location; ``.claude/skills/``
#: is Claude Code's project-skill discovery path.
SKILL_DESTINATIONS: tuple[str, ...] = (".agents/skills", ".claude/skills")


def _iter_skill_files(
    node: Traversable, prefix: PurePosixPath
) -> list[tuple[str, bytes]]:
    """Flatten the shipped skills tree into (relative posix path, bytes)."""
    out: list[tuple[str, bytes]] = []
    for entry in sorted(node.iterdir(), key=lambda e: e.name):
        if entry.is_dir():
            out.extend(_iter_skill_files(entry, prefix / entry.name))
        elif entry.is_file():
            out.append((str(prefix / entry.name), entry.read_bytes()))
    return out


# ── The scaffold operation ───────────────────────────────────────────


@dataclass(frozen=True)
class ScaffoldedFile:
    """One file the scaffolder planned: written, or kept because it exists."""

    path: str  #: project-relative posix path
    written: bool  #: False → pre-existing file was left untouched
    kind: str  #: ``manual`` | ``wrapper`` | ``mcp`` | ``skill``
    label: str  #: human description for CLI output


@dataclass(frozen=True)
class ScaffoldReport:
    directory: str
    files: tuple[ScaffoldedFile, ...]

    @property
    def written(self) -> list[str]:
        return [f.path for f in self.files if f.written]

    @property
    def skipped(self) -> list[str]:
        return [f.path for f in self.files if not f.written]


def _place(
    target: Path, rel: str, content: bytes, *, kind: str, label: str
) -> ScaffoldedFile:
    """Write ``content`` at ``target/rel`` unless the file already exists."""
    dest = target / rel
    if dest.exists():
        return ScaffoldedFile(path=rel, written=False, kind=kind, label=label)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return ScaffoldedFile(path=rel, written=True, kind=kind, label=label)


def scaffold(target: Path) -> ScaffoldReport:
    """Scaffold agent-runtime entrypoints into ``target``.

    Writes the canonical AGENTS.md, the per-runtime wrappers, the
    project-scoped MCP registrations, and the shipped skills. Existing
    files are never overwritten — they are reported as skipped instead,
    so re-running is always safe (idempotent).
    """
    try:
        manual = agents_manual()
        skill_files = _iter_skill_files(skills_root(), PurePosixPath(""))
    except FileNotFoundError as exc:  # SkillNotFoundError included
        raise ScaffoldError(str(exc)) from exc

    target.mkdir(parents=True, exist_ok=True)
    placed: list[ScaffoldedFile] = [
        _place(
            target,
            "AGENTS.md",
            manual.encode("utf-8"),
            kind="manual",
            label="canonical operating manual (single source)",
        )
    ]
    placed.extend(
        _place(
            target,
            w.path,
            w.text.encode("utf-8"),
            kind="wrapper",
            label=f"{w.label} wrapper",
        )
        for w in WRAPPERS
    )
    placed.extend(
        _place(
            target,
            reg.path,
            reg.snippet.encode("utf-8"),
            kind="mcp",
            label=f"{reg.label} MCP registration",
        )
        for reg in MCP_REGISTRATIONS
        if reg.in_project
    )
    for dest_prefix in SKILL_DESTINATIONS:
        placed.extend(
            _place(
                target,
                f"{dest_prefix}/{rel}",
                data,
                kind="skill",
                label="Agent Skill file",
            )
            for rel, data in skill_files
        )
    return ScaffoldReport(directory=str(target.resolve()), files=tuple(placed))


__all__ = [
    "MCP_REGISTRATIONS",
    "NATIVE_AGENTS_MD_RUNTIMES",
    "SKILL_DESTINATIONS",
    "WRAPPERS",
    "McpRegistration",
    "ScaffoldError",
    "ScaffoldReport",
    "ScaffoldedFile",
    "Wrapper",
    "agents_manual",
    "mcp_entry_registered",
    "scaffold",
]
