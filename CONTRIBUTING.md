# Contributing to Traitprint

Thanks for your interest! Traitprint Local is MIT-licensed and contributions
are welcome — bug reports, docs fixes, and code.

## Dev setup

```bash
git clone https://github.com/DataViking-Tech/traitprint
cd traitprint
python3 -m venv .venv && source .venv/bin/activate   # Python >= 3.10
pip install -e ".[dev]"
```

## Quality gates (CI runs all three on every PR)

```bash
ruff check src/ tests/   # lint
mypy src/                # strict typing
pytest                   # full suite
```

All three must be green. New code ships with tests; match the style of the
neighboring tests (plain pytest classes, no fixtures beyond what the file
already uses).

## Things to know before changing…

- **The vault format** (`src/traitprint/schema.py`, `vault_io.py`): the
  on-disk format is a versioned contract —
  [docs/schema/vault-v1/](docs/schema/vault-v1/). Additive changes need a
  contract *revision* entry (README revision history + `$comment` in the
  JSON schema) and must keep pre-revision vaults byte-identical on
  round-trip (omit new keys while empty). Anything non-additive needs a
  `schema_version` discussion first — open an issue.
- **Agent Skills** (`skills/*/SKILL.md`): folder name must match frontmatter
  `name` and be registered in `SKILL_NAMES` (`src/traitprint/skills.py`).
  `tests/test_skills.py` enforces frontmatter shape, resolvable CLI
  mentions, and the shared-reference link — run it after any skill edit.
- **Proposals** (`src/traitprint/proposals.py`): the validation logic
  mirrors the hosted MCP server's `vault_propose` byte-for-byte semantics.
  If you change payload rules here, flag it in the PR so the cloud side can
  follow — they must not drift.
- **Coherence scoring** (`src/traitprint/coherence.py`): a faithful port of
  cloud's `story-coherence.ts`. Don't change scoring behavior unilaterally;
  file a paired cloud issue instead.
- **Safety invariants**: extracted content is proposed, never silently
  written; taxonomy IDs and UUIDs are never invented. These are
  load-bearing across the CLI, MCP server, and skills — PRs that weaken
  them won't merge.

## PR guidelines

- One concern per PR, with a test demonstrating the change.
- Update `CHANGELOG.md` (Unreleased section, Keep-a-Changelog style) for
  anything user-visible.
- Reference the issue you're addressing (`Closes #NN`).

## Not sure where something goes?

Local (this repo) is the pip-installable, zero-account product. The hosted
web app / sync server / hosted MCP is
[traitprint-cloud](https://github.com/DataViking-Tech/traitprint-cloud) —
they are deliberately separate products. When in doubt, open an issue and
ask.
