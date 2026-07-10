# CLAUDE.md — developing traitprint

Instructions for coding agents working **on** this codebase. If you are an
agent **using** traitprint (the product — CLI, vault, MCP server), read
`AGENTS.md` instead; that is the operating manual. This file is for
developing it.

## Dev setup

System python is 3.9 — too old; the package requires >= 3.10. Use
homebrew 3.12:

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Quality gates

All three must pass before any PR. CI (`.github/workflows/ci.yml`) runs
them on Python 3.10–3.13:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src/ tests/
.venv/bin/mypy src/
```

## Issue tracking

GitHub issues, via `gh`. That is the only tracker — `bd` is not installed
and not used.

## Constraints

- **The vault format is a versioned contract** (`docs/schema/vault-v1/`).
  Additive revisions only: pre-revision vaults must round-trip
  byte-identical (omit new keys while empty). Adding allowed keys means
  bumping the contract revision (README revision history + `$comment` in
  the JSON schema). Anything non-additive needs an issue first.
- **Skills** (`skills/*/SKILL.md`): every new or changed skill must be
  registered in `SKILL_NAMES` (`src/traitprint/skills.py`) and pass the
  conformance tests (`tests/test_skills.py`). Skills ship inside the
  wheel via the `force-include` in `pyproject.toml`.
- **Don't drift from cloud**: `src/traitprint/proposals.py` mirrors the
  hosted MCP server's `vault_propose` validation, and
  `src/traitprint/coherence.py` is a port of cloud's
  `story-coherence.ts`. Changing behavior in either requires
  coordinating with traitprint-cloud — flag it in the PR.
- **CHANGELOG.md**: add an entry under `[Unreleased]` (Keep a Changelog
  style) for every user-visible change.
