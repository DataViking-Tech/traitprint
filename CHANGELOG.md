# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-04-20

### Added
- Split into `traitprint` (local) and `traitprint[cloud]` extras (tp-5q2).
- Per-project vault path resolution (tp-nvt).
- Use case examples and clearer audience framing in README (tp-vrc).
- Explicit privacy commitment for cloud tier (tp-gn8).

## [0.5.0] - 2026-04-20

### Added
- Taxonomy distance graph for cross-concept skill search (tp-e5b).
- Semantic search powered by the taxonomy distance graph.

## [0.4.2] - 2026-04-20

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
