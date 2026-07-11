# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Positioning lenses — a named, non-destructive projection over the one
  vault.** A lens selects, orders, and re-weights existing vault content
  (per-skill salience `core`/`supporting`/`suppressed`, signature
  experiences/stories, optional headline/bio overrides) so the same
  grounded facts read differently for a target role or archetype — it
  never asserts a fact absent from the vault. Persisted as `lenses.json`
  (omitted entirely when no lens exists, so an un-lensed vault is
  byte-identical to pre-lens). A vault holds at most five lenses, and the
  reserved slug `none` is the canonical-rendering escape hatch. Authored
  through first-class validated surfaces: the `vault lens
  add|update|set-default|remove` CLI group (with `--from-json` batch
  parity), staged `add_lens`/`update_lens` proposal kinds, and — over the
  hosted MCP server — `vault_propose`. Rendered by the lens-aware
  `get_profile_summary` (new optional `lens` param) and the `vault_lens_list`
  / `vault_lens_get` read tools on both the local and cloud servers, and
  by `vault export -f career-bundle --lens`. Lenses that reference a
  since-deleted entity surface as `disputed` via the live trust layer. New
  `traitprint-position-lens` Agent Skill and matching MCP prompt teach the
  curation workflow; the contract lives in `docs/schema/lens-v1/`.
- **Proposal pre-flight surface for external tools**
  (`traitprint proposals validate`, `traitprint proposals contract`).
  Two read-only subcommands that need no vault: `validate` checks
  proposal `.json` files (or a directory of them) with the exact
  contract checks `proposals add` and the review queue run — document
  shape plus kind/target/payload-key rules — without staging anything
  (`[ok]`/`[err]` lines, `--json` report, exit 0/1); `contract` prints
  the machine-readable proposal contract (kinds, per-kind
  allowed/required payload keys, statuses, profile `basics` keys,
  `target_id` rules) so exporters in any language can vendor or diff
  it to catch drift instead of re-reading Python source. New
  `docs/external-exporters.md` guide documents the staged-write
  integration path (emit `proposals/*.json`, validate, hand off to
  user review) and its safety invariants. No vault contract change —
  validation semantics are unchanged. (Refs #68: the neutral,
  non-contingent substrate for third-party exporter plugins.)
- **Agent-runtime entrypoint scaffolder (`traitprint agents init`).**
  New `agents` command group; `traitprint agents init [DIR] [--json]`
  bootstraps a project directory for agent CLIs without touching the
  existing `traitprint init` (which keeps its "create the vault"
  meaning). It writes a canonical `AGENTS.md` copy (the operating
  manual, now also shipped in the wheel as
  `traitprint/data/AGENTS.md`), thin wrappers that delegate to it
  (`CLAUDE.md`, `QWEN.md`, `.grok/GROK.md`; Codex CLI, OpenCode, and
  Kimi CLI read `AGENTS.md` natively), the shipped Agent Skills under
  `.agents/skills/` and `.claude/skills/`, and project-scoped MCP
  registration for `traitprint mcp-serve` (`.mcp.json`,
  `opencode.json`, `.qwen/settings.json`, `.grok/settings.json`).
  Home-directory registrations (Codex `~/.codex/config.toml`, Kimi
  `~/.kimi/mcp.json`) are emitted as snippets; nothing outside DIR is
  ever written, existing files are never overwritten (idempotent
  re-runs), and Gemini CLI is skipped — the published extension
  (`gemini-extension.json`) already covers it. Ends with a plain
  next-steps checklist (`traitprint init` if no vault, MCP snippets,
  launch the `traitprint-fill-vault` interview). `--json` emits
  `{directory, written, skipped, mcp, next_steps}`.
- **New Agent Skill `traitprint-agent-vault-sync`** — teaches a wrapping
  agent the supported round-trip between a Traitprint vault and the
  working directory of an external agent-driven career tool (career-ops
  or any CLI-agent career tool, referenced nominatively): deterministic
  `vault export` out, judgment gaps (compensation targets, exit story,
  archetype fits) filled only by asking the user, everything the tool
  produced staged back via `traitprint proposals add`, and
  `traitprint vault audit --json` to close the loop. No BYOK key —
  agent-is-the-model (D11). Registered as the seventh skill; ships in
  the wheel like the rest.
- **New docs page `docs/external-tool-sync.md`** — the external-tool
  sync workflow in prose, a copy-paste "sync traitprint" Custom
  Workflows snippet for the user layer of a career-ops-style
  `modes/_custom.md` (user-owned config, survives the tool's own
  updater), and a note on preferring `traitprint mcp-serve`
  (`get_profile_summary`, `find_story`) for grounded, UUID-linked facts
  over re-reading a freeform `cv.md` snapshot. Includes the brand and
  MIT-attribution note for nominative career-ops references.
- **`vault import-story-bank`: deterministic working-dir importer.**
  New CLI command that detects a job-search working directory
  (`config/profile.yml` + `interview-prep/*.md`, matched by shape,
  never by brand) and stages everything through the proposals
  channel — one `add_story` proposal per `### [theme] Title` STAR
  block (tolerant regex parse: bold/plain labels, list bullets,
  Reflection→`## Lesson`, multi-line continuations) plus one
  `update_profile` proposal from profile.yml (candidate/narrative →
  JSON Resume basics; target-role archetypes are stashed in the
  proposal rationale — no lens proposal kind exists yet). Tags
  resolve to existing vault skill UUIDs by exact name/taxonomy match
  (the rest become theme_tags); a `Source:` line matches a vault
  experience; UUIDs are never invented. `--dry-run` and `--json`
  supported; a `cv.md` is routed to the existing
  `vault import-resume --propose` pipeline instead of re-parsed.
  Ships with a fixture suite covering the canonical format and a
  drift variant.

- **`vault export -f career-bundle`: multi-file working-dir bundle.**
  New bundle exporter emitting three user-layer files an agent-CLI
  job-search tool can consume directly — `cv.md` (ATS-style resume:
  the canonical markdown export with "Professional Summary"/"Work
  Experience" header renames and Stories/Philosophy dropped),
  `interview-prep/story-bank.md` (one `### [theme] Title` STAR block
  per story with Reflection, Source, and "Best for questions about"
  tags built from theme_tags + resolved skill names), and
  `config/profile.yml` (candidate/narrative/target_roles with
  commented placeholder keys; string-templated because yaml.safe_dump
  cannot emit comments, and round-trip-parse tested). New
  `export_vault_bundle()` dict-returning API beside `export_vault`;
  the CLI requires `-o DIR` (or `--zip` for a single archive) and
  gains `--lens SLUG` to project cv.md through a positioning lens
  (headline/bio overrides, signature experiences first, core skills
  lead, suppressed skills hidden). The layout is compatible with
  career-ops-style working directories (nominative reference only;
  the format token is brand-neutral).
- **`traitprint doctor`: vault phase detection + freshness audit.**
  New read-only CLI command (and local-only MCP tool of the same name)
  classifying the vault from deterministic date math — `first-run` |
  `growing` | `established` | `stale` — plus freshness findings folded
  into `vault audit`, all at minor severity:
  - `vault.stale_stories` — the story bank as a whole untouched past
    the threshold (aggregate, never per-story spam).
  - `experience.current_stale` — a current role (no end date) not
    edited past the threshold.
  - `skill.stale_evidence` — a strong (4-5/5) skill whose only
    evidence stories are stale (no-evidence skills stay
    `skill.unsupported_strength`).
  - `lens.draft_signature` — a lens showcasing a story that scores
    only Draft.
  Findings carry a structured `fix_skill` field naming the shipped
  Agent Skill that addresses them (fill-vault / mine-story-gaps /
  capture-story / draft-star-story), so wrapping agents can
  self-orient at session start. `vault show` now leads with the phase
  and top staleness flags. Threshold configurable via
  `doctor --stale-days` (default 90).

- **Style-lint warnings in `vault audit`.** Three new warning-only
  finding codes, all at minor severity so the pre-push gate and
  `--severity` filtering are unchanged:
  - `story.buzzword` — cliché/filler phrasing in story text ("synergy",
    "leveraged", "rockstar", "move the needle", …), with the found
    terms named in the message.
  - `experience.weak_bullet` — accomplishment bullets that use vague
    phrasing (including "responsible for"), don't lead with an active
    verb, or lack both a metric and a concrete tool noun; one finding
    per experience with an example and count.
  - `story.polished_no_lesson` — stories scoring Polished with no
    `lesson`, the one field that makes them interview-ready.
  The lint lives entirely in audit.py, composing coherence.py's
  helpers — coherence scoring is untouched, preserving the documented
  lockstep with cloud's story-coherence.ts.

- **Repo launch playbook.** GitHub issue forms (bug report, feature
  request, and a "Share your story" testimonial template with explicit
  quote-permission field), `SECURITY.md` (private reporting channels +
  the local-first threat-model scope), `CONTRIBUTING.md` (dev setup, CI
  gates, contract-revision and skill-registry rules), and
  `CITATION.cff`. The README lead now surfaces the ethics invariant —
  "the vault never asserts a fact you didn't put in it" — as the
  quotable framing; it was already enforced by the schema, proposals
  channel, and audit. GOVERNANCE.md, translations scaffolding, and an
  outcome-narrative rewrite are deliberately deferred per #65.

- **`traitprint-capture-story` skill.** New Agent Skill
  (`skills/traitprint-capture-story/`) for opportunistic, background
  STAR story capture: whenever the user recounts a work event in any
  session (or right after job-application work that used vault
  context), the wrapping agent drafts a STAR + Lesson story, runs a
  deterministic dedup *pre-check* against the existing bank
  (`vault list stories`; near-matches are surfaced to the user, never
  silently skipped), confirms, and stages the result via
  `traitprint proposals add --kind add_story` — never a direct write.
  This intentionally diverges from the interactive story skills'
  direct-write pattern: for side-effect capture the user approves
  later through the proposals review queue. Ships in `SKILL_NAMES`
  (wheel package data + `npx skills add`); no MCP prompt counterpart,
  same as `traitprint-import-resume`.

- **Profile phone + links (vault contract revision 1.3, additive).**
  The profile gains optional `phone`, `url` (personal website/portfolio)
  and `profiles[]` (social links, `{ network, username?, url? }`),
  following the JSON Resume `basics` vocabulary. Older vaults remain
  valid (`schema_version` stays 1); the new keys are omitted from
  `profile.json` while empty, so pre-1.3 vaults round-trip
  byte-identically. Shipped across the local product:
  - `vault set-profile --phone`, `--url`, and repeatable
    `--link NETWORK=URL` (passing any `--link` replaces the list; a
    single `--link ''` clears it).
  - The jsonresume exporter emits `basics.phone`, `basics.url` and real
    `basics.profiles[]` entries instead of a hardcoded empty array.
  - Proposals: `phone`, `url` and `profiles` allowlisted in
    `update_profile` payloads, with shape validation for links.
  - Job-search preference fields (target titles, compensation, work
    authorization) are deliberately **not** part of this revision — they
    are not JSON Resume basics and need their own design.

- **User customization layer (`custom.md`).** An optional, user-owned
  `custom.md` at the vault root holds durable instructions for wrapping
  agents (suggested sections: House Rules, Output Preferences,
  Off-Limits). The package never creates or writes it, so it survives
  pip upgrades — unlike the wheel-shipped skills. The MCP workflow
  prompts append its contents (capped at 32 KiB; missing/empty/
  unreadable is a silent no-op) under a delimited "User customization"
  header, and every shipped SKILL.md now tells the wrapping agent to
  honor it. User rules win on style and workflow but cannot bypass the
  proposals channel or the never-invent-taxonomy-IDs/UUIDs invariant.
  Schema-ignored: no vault contract revision.

### Changed

- **Contributor docs for coding agents replaced.** `CLAUDE.md` was an
  unfilled issue-tracker integration template mandating a `bd` tool that
  is not part of this project; it now documents the real dev setup, the
  three quality gates, the tracker (GitHub issues via `gh`), and the
  repo's don't-break constraints, and points agents *using* traitprint
  to `AGENTS.md`. The `.claude/settings.json` session hooks and the
  `done`/`handoff` commands that depended on absent tooling were
  removed (`review` stays). No behavior change to the package itself.

## [0.11.0] - 2026-06-11

### Added

- **Experience skill links (vault contract revision 1.1, additive).**
  Experiences carry an optional `skill_ids[]` — the skills exercised in
  that role, same UUID-array reference style as story `skill_ids`.
  Older vaults remain valid (`schema_version` stays 1; missing key
  reads as empty). Shipped across the local product:
  - `vault add-experience --skill-id` (repeatable), interactive prompt,
    and `skill_ids` in `--from-json` batch items.
  - `vault show --verbose` / `--json` and `vault list experiences`
    (new Skills count column) surface the links.
  - `vault audit`: `experience.dangling_skill` (major warning, Layer 1,
    mirrors `story.dangling_skill`) and `experience.no_skills` (minor
    gap, mirrors `story.no_skills`).
  - Proposals: `skill_ids` allowlisted in `add_experience` /
    `update_experience` payloads.
  - MCP `get_profile_summary` (detailed): `signature_experiences` carry
    `related_skills` (names; dangling refs skipped). Cloud parity ships
    separately.

## [0.10.0] - 2026-06-10

### Added

- **Git-native cloud sync (sync-v1, architecture D5, tp-an-020).** New
  `traitprint sync` command group implementing the client half of the
  [sync-v1 wire contract](docs/schema/sync-v1/README.md) — git bundles
  over HTTPS to `/vault-git/push|fetch|info`, replacing whole-vault
  last-write-wins with real git history transfer:
  - `sync push [--json]` — commits uncommitted hand edits, then
    uploads a thin bundle against the last-known server head (full
    bundle on first push). The basis sha is persisted in
    `.git/traitprint/server-head` and refreshed from every push,
    fetch, and 409 response. A 422 `missing_prerequisites` rejection
    auto-retries once with a full bundle. A 422 `schema_violation`
    prints every server violation verbatim
    (`[err] <file> @ <pointer>: <message>` + `hint:`) — they are
    agent-actionable; the ref is not advanced. `--json` emits
    `{pushed, head, server_head, ingest_status}`.
  - `sync pull [--json]` — fetches the server's bundle
    (`?since=<last-known head>` for an incremental bundle, 204 when up
    to date), verifies and applies it with `git bundle verify` +
    `git fetch`, then fast-forwards or merges. Merge conflicts exit 1,
    leave the merge in progress, and print the conflicted files plus
    the exact `git add`/`git commit` commands to finish — resolve with
    file tools, commit, then `sync push`. Re-running `sync pull`
    mid-conflict re-prints the report and never commits conflict
    markers. `--json` emits `{fetched, result, conflicts, head}`.
  - `sync status [--json]` — `GET /vault-git/info` probe: local vs
    server heads, relation (in-sync/ahead/behind/diverged/...), ingest
    state, and D10 quarantined entities. `--json` emits `{local_head,
    server_head, ingest_status, quarantine_summary, relation}`.
  - Only `refs/heads/main` syncs; vaults on another local branch get a
    local `main` ref mirroring HEAD so bundles always carry the ref
    the server expects. Auth reuses the existing credentials plumbing
    (`traitprint login` / `TRAITPRINT_API_TOKEN`); a 401 answers with
    the re-login hint.

### Changed

- Legacy `traitprint push` / `traitprint pull` (whole-vault
  last-write-wins) still work unchanged but are marked deprecated in
  their help text, pointing at `traitprint sync push|pull`.

## [0.9.0] - 2026-06-10

### Added

- **Proposals review CLI (architecture D2/D9, tp-an-021).** New
  `traitprint proposals` command group operating on `proposals/*.json`
  staged writes (vault v1 contract `$defs/proposal`):
  - `proposals list [--status pending|approved|rejected|withdrawn]
    [--json]` — table or full-document JSON array; unreadable proposal
    files surface as `[warn]` lines (stderr), never crashes.
  - `proposals show ID [--json]` — payload, rationale, source, and a
    current→proposed field diff against the live vault for `update_*`
    kinds. IDs accept the full UUID or an unambiguous hex prefix (the
    8-char prefix in the filename).
  - `proposals approve ID [-y]` — validates the payload against the
    entity schema (Layer 0, hard reject), applies it to the vault
    (`add_*` creates the entity; `update_*` partial-updates by
    `target_id` with an actionable error when the target is gone), and
    deletes the proposal file **in the same git commit** (contract
    rule 7).
  - `proposals approve --all [-y]` — the D9 one-step approve-all:
    applies every pending proposal and records **one batch commit**
    (`Approve N proposals`); failed items are reported per-line
    (`[ok]`/`[err]` + `Summary:`) and stay pending (exit 1).
  - `proposals reject ID [-y]` — sets `status: rejected` +
    `resolved_at` and keeps the file.
  - `proposals add --kind K [--target-id UUID] [--rationale R]
    [--source S] --payload-json -` — the local staged-write path,
    validating kind/target rules and per-kind payload keys exactly as
    the hosted MCP server's `vault_propose` tool does, so local agents
    get the same propose-and-review flow remote agents have.
- **`vault import-resume --propose` (D9 staged path).** Extracted items
  become pending proposals instead of direct writes: the BYOK path
  stages one proposal per item (single commit, `source:
  import-resume`), and the agent-assist payload's write-back section
  switches to `traitprint proposals add` commands (one JSON object per
  invocation) with `proposals list --json` as the verify step. Default
  behavior without the flag is unchanged.
- **Pending proposals surface in `vault audit`** as a minor finding
  (`proposals.pending`, "N proposals awaiting review"); unreadable
  `proposals/*.json` files surface as `proposals.invalid_file`
  findings.

### Changed

- `docs/schema/vault-v1/README.md` rule 7 updated: proposal review CLI
  support is shipped (was "planned, not yet shipped"), and proposal
  filenames are documented as `<kind>-<id8>.json` slugs.

## [0.8.0] - 2026-06-10

### Added

- **Agent-assist mode for `vault import-resume` (architecture D11).**
  Provider resolution is now: explicit `--provider` flag → configured
  BYOK key (env or `.credentials`, including an explicit `OLLAMA_HOST`)
  → ambient agent → actionable error. With no resolvable provider the
  command no longer fails: it prints a clearly delimited ASSIST PAYLOAD
  (exit 0) containing instructions to the wrapping agent, the exact
  extraction contract the BYOK system prompt uses (JSON shape + rules,
  factored so the two paths cannot drift), the D9 proposal rules
  (extracted skills at modest proficiency 2-3, never invent taxonomy
  ids, 1-5 scale), the extracted resume text, and write-back
  instructions through the validated batch commands ending with
  `vault audit --json`. New flags: `--assist` forces the payload even
  when a key is configured, `--no-assist` restores the hard error
  (headless runs keep requiring BYOK), `--json` emits the payload as
  `{"mode": "agent-assist", "contract": ..., "text": ...,
  "write_back": ...}`. The BYOK path is byte-for-byte unchanged when a
  key is configured.
- **`traitprint vault extract-text FILE [--json]`** — the deterministic
  half of resume import as its own command: PDF (pypdf), DOCX
  (python-docx), TXT, or MD to plain text on stdout, no LLM, no vault
  writes. `--json` wraps it as `{"file", "format", "chars", "text"}`.
  Missing optional dependencies name the extra to install
  (`pip install 'traitprint[import]'`).
- **`vault add-education --from-json`** — education entries gain the
  same batch path as the other sections
  (`[{"institution", "degree"?, "field_of_study"?, "start_date"?,
  "end_date"?, "description"?}]`), closing the "no batch mode" gap and
  completing the assist-mode write-back surface.
- **New Agent Skill `traitprint-import-resume`** — teaches a wrapping
  agent the full assist loop: extract text, do the extraction reasoning
  against the contract, propose to the user (D9, with approve-all),
  write back via the batch commands, audit, and report.

### Changed

- Default-host Ollama is no longer an implicit auto-detect signal for
  `import-resume`: without `OLLAMA_HOST` (env or `.credentials`) a
  keyless run enters agent-assist mode instead of attempting a network
  call to `localhost:11434`. Pass `--provider ollama` or set
  `OLLAMA_HOST` to keep using a local default-port server.

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
