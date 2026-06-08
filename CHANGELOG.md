# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `traitprint vault audit` — a deterministic, read-only narrative-coherence
  pass that flags unsupported skill claims (proficiency ≥ 7 with no story),
  philosophies citing no evidence, incomplete or broken STAR stories, dangling
  references, and roles with no story attached. Supports `--json`,
  `--severity`, and `--strict` (exit non-zero on errors/warnings).
- Three MCP prompts on `traitprint mcp-serve`: `fill_vault` (Socratic
  vault-building over the CLI), `audit_coherence` (run the audit, then apply
  judgment), and `draft_star_story` (turn a raw accomplishment into one
  well-linked STAR story). Local-only; the four query tools remain
  cloud-parity.

### Changed
- README restructured around the vault concept and an agent-driven workflow
  (fill out → audit → publish), with cloud framed as "hosted local + a few
  server-only extras."
- README quickstart now includes `vault set-profile` and a prompt to add
  content before running `mcp-serve`, so first-run MCP queries are not
  empty (tp-1me).
- README's networking-dependency claim now matches the code: `httpx` *is*
  installed transitively via `mcp`; what's true is that no module imports
  it at load time without the `[cloud]` or `[import]` extras (tp-1me).
- Bumped PyPI classifier from `3 - Alpha` to `4 - Beta` (tp-1me).
- Author/contact email consolidated on `@traitprint.com` (tp-1me).

### Added
- README "Contact" section mapping addresses to purposes (tp-1me).
- `traitprint login` now accepts an API token via `--token` or
  `TRAITPRINT_API_TOKEN`, skipping email/password — works in non-TTY shells,
  CI, and agentic flows (tp-y45).
- `TRAITPRINT_PASSWORD` env var fallback for the password prompt (tp-y45).
- `push`/`pull` honor `TRAITPRINT_API_TOKEN` directly, no prior `login`
  required (tp-y45).
- `vault add-education` now supports flag mode (`--institution`, `--degree`,
  `--field`, `--start-date`, `--end-date`, `--description`) for parity with
  the other `add-*` commands; interactive `-i` still works as a fallback
  (tp-gas).
- `--format json-resume` is accepted as a synonym for `--format jsonresume`
  on both `traitprint export` and `traitprint vault export` (tp-gas).

### Changed
- `traitprint export` is now a thin alias of `traitprint vault export`: same
  formats (`json`, `markdown`, `jsonresume`, `synthpanel-persona`), same
  options, same output. The two commands previously diverged for
  `synthpanel-persona`; both now emit the canonical SynthPanel pack
  (`{name, source, personas: [...]}`) (tp-gas).

## [0.6.0] - 2026-04-21

### Added
- Split into `traitprint` (local) and `traitprint[cloud]` extras (tp-5q2).
- Per-project vault path resolution (tp-nvt).
- Use case examples and clearer audience framing in README (tp-vrc).
- Explicit privacy commitment for cloud tier (tp-gn8).

## [0.5.0] - 2026-04-21

### Added
- Taxonomy distance graph for cross-concept skill search (tp-e5b).
- Semantic search powered by the taxonomy distance graph.

## [0.4.2] - 2026-04-21

### Added
- Batch `add-{skill,experience,story,philosophy}` via `--from-json` (tp-apo).
- Free-text `query` parameter for the `find_story` MCP tool (tp-7wo).
- Taxonomy alias expansion for common adjacent terms (tp-5n7).

### Changed
- `vault show` default output now lists names instead of just counts (tp-4pc).
- Version is read from package metadata rather than hardcoded.

### Fixed
- Warn on `add-skill` taxonomy/category mismatch (tp-7xu).

## [0.4.1] - 2026-04-20

### Added
- Token-based semantic search for skills (tp-sd3).
- `vault show --verbose` flag (tp-dmy).

### Changed
- `.beads/` is now gitignored (local infrastructure, not source).

## [0.4.0] - 2026-04-20

### Added
- Non-interactive flags for `add-story`/`add-experience`/`add-philosophy` (tp-6st).
- `vault set-profile` CLI command (tp-a14).

### Fixed
- Wire outcome filter and classify outcome in `find_story` (tp-4tr).
- Force line-buffered stdout in the MCP stdio server (tp-4l3).
- Reject duplicate skill names in `VaultStore.add_skill` (tp-2td).

## [0.3.0] - 2026-04-20

### Added
- `vault export --format json|markdown|jsonresume|synthpanel-persona` (tp-h8m).
- SynthPanel persona export (tp-23r).
- Resume import with BYOK skill mining (tp-a3g).
- Cloud sync with login/push/pull and last-write-wins merging (tp-4gx).
- "Why local vs cloud" documentation page and README section (tp-cti).

### Fixed
- Package `taxonomy.json` inside the wheel and clean up README (tp-q8x).
- Mypy errors — guard None comparison and add `types-PyYAML` stub.

## [0.2.0] - 2026-04-19

### Added
- Resume import.
- Cloud sync.
- Expanded documentation.

## [0.1.0] - 2026-04-19

### Added
- Initial release: Traitprint Local package scaffold (Slice A).
- Vault CRUD, CLI commands, and taxonomy integration (Slice B).
- MCP stdio server with 4 cloud-parity tools (tp-yqh).

### Fixed
- Set server version in MCP stdio `serverInfo`; add PyPI publish workflow.

[Unreleased]: https://github.com/DataViking-Tech/traitprint/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/DataViking-Tech/traitprint/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/DataViking-Tech/traitprint/compare/v0.4.2...v0.5.0
[0.4.2]: https://github.com/DataViking-Tech/traitprint/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/DataViking-Tech/traitprint/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/DataViking-Tech/traitprint/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/DataViking-Tech/traitprint/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/DataViking-Tech/traitprint/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/DataViking-Tech/traitprint/releases/tag/v0.1.0
