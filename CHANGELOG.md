# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.1] - 2026-06-10

### Fixed

- **Git auto-commits never fail silently.** A vault living in a plain
  directory (no `.git`) now gets a repo initialized on its first write;
  an adopted pre-existing repo gets the vault-local identity and
  `commit.gpgsign=false` config (re)applied so a broken global git setup
  cannot block commits; and when a commit still fails, the CLI prints a
  prominent stderr warning ("vault saved but git commit failed: …")
  instead of swallowing it — the data write keeps exit code 0.
  `vault migrate` initializes the repo when missing and warns loudly if
  its promised migration commit cannot be recorded.
- **Invalid UUID flags no longer raise tracebacks.** `add-story
  --skill-id/--experience-id`, `add-philosophy --evidence-id`, and
  `vault remove ITEM_ID` reject bad values with `invalid UUID '<x>'`
  and exit code 2 (usage error).
- **MCP `get_philosophy` honors its filter.** The tool gains a
  `category` argument (exact match against the five philosophy
  categories) that excludes non-matching entries instead of returning
  everything at `match_score` 0.0; topic queries rank by keyword match
  against title/stance, and matches carry meaningful scores.
- **MCP proficiency vocabulary covers all five levels.** Level 3 now
  renders as `proficient` instead of folding into `working`;
  `search_skills min_proficiency` accepts all five labels (`familiar`,
  `working`, `proficient`, `expert`, `authority`) and integers 1-5.
  (Intentionally ahead of the cloud server's current 4-label enum;
  cloud parity catch-up is tracked separately.)
- **MCP `find_story` theme matching includes `theme_tags`.** An exact
  tag match scores highest (1.0), then keyword/substring hits in the
  tags, then STAR body text — a story tagged `incident-response` is now
  found for that theme.
- **Audit severity matches the contract.** Dangling cross-link
  references (`story.dangling_skill`, `story.dangling_experience`,
  `philosophy.dangling_evidence`) are warnings (major), not critical:
  they still surface in every audit but no longer block `traitprint
  push` by default (vault v1 contract rule 2 / architecture D10).
- **Batch `--from-json` errors are complete and clean.** All violations
  per item are reported in one pass (missing fields and range errors
  together) in the `[err] <name>: field: message` style; pydantic error
  dumps and `errors.pydantic.dev` URLs never leak into the output.
- `add-skill --category` is genuinely optional, matching the contract:
  on a taxonomy match the taxonomy's category fills it, otherwise it
  stays empty — in single and batch mode, with honest help text and no
  interactive prompt.
- `add-experience` help no longer claims missing required fields are
  prompted for (only `--title` is required).
- `vault migrate` grammar: "Remapped 1 skill proficiency" (singular).
- `docs/schema/vault-v1` rule 7 notes that `traitprint proposals
  approve` is planned, not shipped (the Traitprint Cloud review queue
  covers approval today).

### Added

- `vault add-story` writes the full story schema: `--lesson TEXT`,
  `--outcome win|failure|learning`, and repeatable `--theme-tag TAG`.
  The same `lesson`/`outcome`/`theme_tags` keys are accepted in
  `--from-json` batch mode, and `vault show --verbose` displays all
  three fields.
- `--json` on the read surface (tp-an-002): `vault show --json` (full
  vault document), `vault list <section> --json` (array of
  `{id, type, name|title}`), `vault history --json` (array of
  `{sha, message}`), `vault diff --json`
  (`{from_sha, to_sha, diff_text}`).
- MCP `get_profile_summary` documents its `depth` levels
  (`brief|standard|detailed`) in the tool description.

### Added — Agent Skills (SKILL.md)

- Five workflow skills under a top-level `skills/` directory
  (agentskills.io format), ported from the MCP prompts and rewritten for
  filesystem/shell agents: `traitprint-fill-vault`,
  `traitprint-mine-story-gaps`, `traitprint-discover-skills`,
  `traitprint-draft-star-story`, `traitprint-audit-coherence`. Shared CLI
  cheatsheet + vault file-tree reference at `skills/shared/cli-reference.md`.
  Install with `npx skills add DataViking-Tech/traitprint`.
- Skills bake in the validation policy: extracted skills are proposed to
  the user before writing, enter at modest proficiency, taxonomy IDs are
  never invented, and every workflow ends with
  `traitprint vault audit --json`.
- Wheels ship the skills as package data (`traitprint/data/skills/`,
  hatchling force-include); the new `traitprint.skills` module resolves the
  package-data copy first, with the repo root as fallback.

### Changed

- The five MCP prompts are now thin wrappers that read the corresponding
  SKILL.md body at serve time (plus prompt arguments and an MCP-context
  note), so the prompts and the published skills cannot drift.
- `AGENTS.md` rewritten as the agent operating manual for the CLI: vault
  v1 file-tree map, full command reference with JSON contracts and exit
  codes, proficiency scale, validation/audit workflow, MCP usage, and
  hand-editing gotchas.

## [0.7.0] - 2026-06-10

### Changed — vault v1 file-tree format (breaking on disk, migrated automatically)

The vault's native storage format is now the **v1 file tree** instead of a
single `vault.json` (contract: `docs/schema/vault-v1/`):

```
<vault>/
├── traitprint.json      # manifest: schema_version=1, vault_id, updated_at
├── profile.json         # JSON Resume-compatible basics keys
├── skills.json          # JSON array
├── education.json       # JSON array
├── experiences/*.md     # YAML frontmatter + body (role description)
├── stories/*.md         # frontmatter + ## Situation/Task/Action/Result (+ Lesson)
└── philosophies/*.md    # frontmatter + body (the stance)
```

- **Readers accept v0 and v1; writers emit v1 only.** Loading a legacy v0
  `vault.json` keeps working; the first write converts it in place.
- **Migration:** run `traitprint vault migrate` to convert explicitly. It
  remaps skill proficiency from 1-10 to 1-5 (`ceil(x/2)`), writes the v1
  tree, removes `vault.json`, and records a single git commit
  ("Migrate vault to schema v1"). `--dry-run` previews the file list and
  proficiency remaps (`--json` for machine-readable output); already-v1
  vaults are a no-op.
- **Proficiency is now 1-5** everywhere (1 familiar, 2 working, 3 proficient,
  4 expert, 5 authority): CLI validation (`add-skill --proficiency`), audit
  thresholds, MCP outputs, exports, and the resume-import LLM prompt. A v0
  vault loaded read-only is remapped in memory so downstream logic always
  sees 1-5.
- **Schema unification with Cloud:** stories gained `lesson`,
  `outcome` (`""|win|failure|learning`) and `theme_tags`; philosophy
  `category` is now optional (empty string allowed; the five enum values are
  the only non-empty options); the vault carries a `vault_id` (UUID) and
  `schema_version: 1`.
- **Git operations cover the whole tree:** auto-commits stage all vault
  content (`.credentials` stays gitignored); `vault history`, `vault diff`,
  and `vault rollback` operate on the full file tree, not just one file.
- `export --format json` still emits the lossless single-document JSON form
  (now schema_version 1 with the new fields) for v0 consumers; cloud sync
  continues to push that same single-document serialization unchanged.

### Added
- **Narrative-coherence engine, ported from Traitprint Cloud** so Local and
  Cloud agree on scoring and vocabulary:
  - `traitprint.coherence` — a faithful port of Cloud's
    `story-coherence.ts`: per-story STAR scoring (`Polished`/`Strong`/`Solid`/
    `Draft` + `demonstrates`/`mentions`/`weak` evidence level) and cross-story
    contradiction detection (conflicting metrics, leader-vs-IC role claims).
  - `traitprint.tensions` — a port of Cloud's `philosophy-contradictions.ts`:
    detects tensions between same-category philosophies, framed as nuance.
- `traitprint vault audit` — a deterministic, read-only pass built on the
  ported engine. Emits per-story coherence scores, findings at
  `critical`/`major`/`minor` severity (unsupported strong skills, unbacked
  philosophies, broken/thin stories, dangling references, orphaned roles,
  cross-story contradictions), and philosophy tensions. Supports `--json`
  (full report), `--severity`, and `--strict`.
- Five MCP prompts on `traitprint mcp-serve`, adapted from Cloud's Experience
  Mining engine (the Socratic coach + its mining modes): `fill_vault`,
  `mine_story_gaps` (STORY OPPORTUNITY), `discover_skills` (SKILL DISCOVERY),
  `draft_star_story` (FOCUSED deep dive), and `audit_coherence`. Local-only;
  the four query tools remain cloud-parity.
- `traitprint push` now runs the coherence audit before uploading and blocks
  on critical findings (broken stories, dangling references, contradicting
  roles). New flags: `--strict` (block on major findings too, matching `vault
  audit --strict`) and `--skip-audit` (bypass). Major/minor are advisory by
  default.

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

[Unreleased]: https://github.com/DataViking-Tech/traitprint/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/DataViking-Tech/traitprint/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/DataViking-Tech/traitprint/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/DataViking-Tech/traitprint/compare/v0.4.2...v0.5.0
[0.4.2]: https://github.com/DataViking-Tech/traitprint/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/DataViking-Tech/traitprint/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/DataViking-Tech/traitprint/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/DataViking-Tech/traitprint/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/DataViking-Tech/traitprint/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/DataViking-Tech/traitprint/releases/tag/v0.1.0
