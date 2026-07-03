"""Locate and read the Agent Skills (SKILL.md) that ship with traitprint.

The canonical skill files live at the repository root under ``skills/`` —
one folder per skill plus ``skills/shared/`` reference material. That
top-level directory is what ``npx skills add DataViking-Tech/traitprint``
consumes, and it is the single source of truth.

Built wheels repackage the same directory as package data at
``traitprint/data/skills/`` via ``[tool.hatch.build.targets.wheel.force-include]``
in ``pyproject.toml``, so ``pip install traitprint`` ships the skills too.
This module resolves whichever copy is present: package data first, then
the repo root (source checkouts and editable installs, where the
force-include is not materialized).

The MCP server reads skill bodies through :func:`skill_body` at serve time,
so the five workflow prompts can never drift from the published skills.
"""

from __future__ import annotations

import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

from traitprint.vault_io import VaultFormatError, parse_markdown

if sys.version_info >= (3, 11):
    from importlib.resources.abc import Traversable
else:  # pragma: no cover - exercised on Python 3.10 only
    from importlib.abc import Traversable

#: The published workflow skills; folder name == frontmatter ``name``.
SKILL_NAMES = (
    "traitprint-fill-vault",
    "traitprint-mine-story-gaps",
    "traitprint-discover-skills",
    "traitprint-draft-star-story",
    "traitprint-audit-coherence",
    "traitprint-import-resume",
    "traitprint-capture-story",
)


class SkillNotFoundError(FileNotFoundError):
    """Raised when the skills directory or a SKILL.md cannot be located."""


def skills_root() -> Traversable:
    """Return the directory holding the skill folders.

    Tries the package-data copy (installed wheels) first, then the
    repo-root ``skills/`` directory (source checkouts / editable installs).
    """
    packaged = files("traitprint.data").joinpath("skills")
    if packaged.is_dir():
        return packaged
    repo_root = Path(__file__).resolve().parents[2] / "skills"
    if repo_root.is_dir():
        return repo_root
    raise SkillNotFoundError(
        "No skills directory found (looked in traitprint/data/skills "
        f"package data and {repo_root})."
    )


def load_skill(name: str) -> tuple[dict[str, Any], str]:
    """Return ``(frontmatter, body)`` for the named skill's SKILL.md."""
    skill_file = skills_root().joinpath(name).joinpath("SKILL.md")
    if not skill_file.is_file():
        raise SkillNotFoundError(f"No SKILL.md for skill {name!r}")
    try:
        return parse_markdown(skill_file.read_text(encoding="utf-8"))
    except VaultFormatError as exc:
        raise SkillNotFoundError(f"Malformed SKILL.md for {name!r}: {exc}") from exc


def skill_body(name: str) -> str:
    """Return the markdown body (frontmatter stripped) of the named skill."""
    return load_skill(name)[1]


__all__ = [
    "SKILL_NAMES",
    "SkillNotFoundError",
    "load_skill",
    "skill_body",
    "skills_root",
]
