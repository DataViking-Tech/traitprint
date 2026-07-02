# Traitprint — Agent Operating Manual

Traitprint is a **local-first, user-owned career identity vault**: a
git-versioned directory of JSON + markdown files holding a person's skills,
experiences, STAR stories, philosophies, and education, plus a CLI and a
stdio MCP server for reading and writing it. You (an AI agent) are the
primary interface — this file is your reference for operating the
`traitprint` CLI correctly. Depth lives in `docs/`:
[architecture & decisions](docs/agent-native-architecture.md),
[vault v1 format contract](docs/schema/vault-v1/README.md),
[privacy model](docs/privacy.md).

> Contributing to this codebase (issue tracking, session protocol)?
> See `CLAUDE.md`. This file is about *using* traitprint.

## The vault on disk (schema v1)

Default `~/.traitprint`; override with `--vault-dir DIR` (global flag) or
`$TRAITPRINT_VAULT_DIR`. The CLI also walks up from the cwd looking for a
`.traitprint/` directory.

```
<vault>/
├── traitprint.json     # manifest: schema_version=1, vault id, updated_at
├── profile.json        # identity block; JSON Resume-compatible keys
├── skills.json         # JSON array: {id, name, taxonomy_id, proficiency, category, notes, ...}
├── education.json      # JSON array
├── experiences/*.md    # YAML frontmatter + body = role description
├── stories/*.md        # frontmatter + ## Situation/Task/Action/Result (+ ## Lesson)
├── philosophies/*.md   # frontmatter + body = the stance
├── proposals/*.json    # staged writes awaiting review (see Proposals below)
├── custom.md           # OPTIONAL user instructions for you (see below)
├── .credentials        # gitignored, never synced
└── .git/               # every CLI write auto-commits
```

### User customization (`custom.md`)

An optional, user-owned `custom.md` at the vault root holds free-form
instructions for wrapping agents (suggested sections: "House Rules",
"Output Preferences", "Off-Limits"). If it exists, read it and honor the
user's rules: their preferences take precedence on style and workflow, but
cannot bypass the proposals channel or the never-invent-taxonomy-IDs/UUIDs
invariant. The package never creates or writes this file — it survives
`pip` upgrades untouched, unlike the wheel-shipped skills and prompts. The
MCP prompts append it to every served workflow automatically.

### Hand-editing rules

Editing the files directly is supported and often the best way to polish
narrative text. Constraints:

- **Identity is the frontmatter/JSON `id` (UUIDv4), never the filename.**
  Filenames are slugs — rename freely; never change or delete `id`.
- **Frontmatter accepts allowed keys only** (`additionalProperties: false`
  in the schema):
  - experiences: `id, title, company, start_date, end_date,
    accomplishments, skill_ids, source, created_at, updated_at`
  - stories: `id, title, skill_ids, experience_id, outcome, theme_tags,
    source, created_at, updated_at`
  - philosophies: `id, title, category, evidence_story_ids, source,
    created_at, updated_at`
- **Story bodies use the STAR heading convention** — `## Situation`,
  `## Task`, `## Action`, `## Result`, each required, in that order;
  optional `## Lesson`. Markdown bodies are the source of truth for
  narrative text.
- **Cross-links are UUIDs** (`skill_ids` on stories *and* experiences,
  `experience_id`, `evidence_story_ids`). A story's `skill_ids` are the
  skills the story evidences; an experience's `skill_ids` are the skills
  exercised in that role (contract revision 1.1, optional). A dangling
  UUID does not break parsing — it surfaces as an audit finding. Never
  fabricate UUIDs; copy them from `traitprint vault list` output.
- Hand edits are not auto-committed; the next CLI write commits the whole
  tree, or commit yourself inside the vault directory.

## Proficiency scale (1-5)

`1` familiar · `2` working · `3` proficient · `4` expert · `5` authority.
Rate from demonstrated evidence, not self-report. Skills at 4-5 with no
linked story are flagged as unsupported by the audit. (Legacy v0 vaults
used a ten-point scale; `traitprint vault migrate` remaps `ceil(x/2)`.)

## CLI reference

Global: `traitprint [--vault-dir DIR] <command>` · `--version`.

**Always pass flags.** Most `add-*` commands fall back to interactive
prompts when required flags are missing, which hangs a non-interactive
shell. Use `-y` on `remove`/`rollback` to skip confirmation.

### Read commands (never modify the vault)

| Command | Notes |
|---|---|
| `traitprint vault show [-v] [--json]` | Summary; `-v` dumps everything incl. UUIDs and git metadata; `--json` emits the full vault document |
| `traitprint vault list <section> [--json]` | Table with UUIDs; sections: `skills`, `experiences`, `stories`, `philosophies`, `education`; `--json` emits `[{id, type, name\|title}]` |
| `traitprint vault audit [--json] [--severity critical\|major\|minor] [--strict]` | Coherence report (below) |
| `traitprint vault history [-n N] [--json]` | Vault git log; `--json` emits `[{sha, message}]` |
| `traitprint vault diff [--json]` | Changes since previous commit; `--json` emits `{from_sha, to_sha, diff_text}` |
| `traitprint vault export -f <fmt> [-o FILE]` | Formats: `json` (lossless single doc), `markdown`, `jsonresume`, `synthpanel-persona`. `traitprint export` is a top-level alias |
| `traitprint vault extract-text FILE [--json]` | Deterministic text extraction from PDF/DOCX/TXT/MD — no LLM, no vault writes; `--json` emits `{file, format, chars, text}`. PDF/DOCX need `pip install 'traitprint[import]'` |

### Write commands (each auto-commits)

| Command | Required | Optional |
|---|---|---|
| `traitprint init` | — | creates dir, git repo, empty v1 vault |
| `traitprint vault set-profile` | at least one flag | `--name --headline --summary --location --email`; omitted fields keep values, `""` clears |
| `traitprint vault add-skill NAME -p 1..5` | name, proficiency | `-c CAT` (optional; taxonomy category fills it on a match, else empty), `--notes`, `--force-category` (keep your category over the taxonomy's) |
| `traitprint vault add-experience` | `--title` | `--company --start-date YYYY-MM --end-date YYYY-MM --description --accomplishment ...` (repeatable) `--skill-id UUID` (repeatable) |
| `traitprint vault add-story` | `--title` | `--situation --task --action --result --lesson --outcome win\|failure\|learning --theme-tag TAG` (repeatable) `--skill-id UUID` (repeatable) `--experience-id UUID` |
| `traitprint vault add-philosophy` | `--title` | `--description --category --evidence-id STORY_UUID` (repeatable); categories: `leadership`, `collaboration`, `technical-approach`, `culture`, `decision-making` |
| `traitprint vault add-education` | `--institution` | `--degree --field --start-date YYYY --end-date YYYY --description` |
| `traitprint vault remove UUID -y` | UUID | searches all sections |
| `traitprint vault rollback -y` | — | reset tree to previous commit |
| `traitprint vault migrate [--dry-run] [--json]` | — | v0 → v1 file tree; idempotent |
| `traitprint vault import-resume PATH` | path | LLM extraction; `--provider --model --yes --dry-run --assist/--no-assist --propose --json`; PDF/DOCX need `pip install 'traitprint[import]'`. Resolution (D11): `--provider` flag → configured BYOK key → agent-assist mode (below) → actionable error. `--propose` stages extracted items as pending proposals instead of writing them (D9 staged path) |

#### Agent-assist mode (D11)

When no LLM provider is resolvable (no `--provider`, no API key in env or
`.credentials`, no explicit `OLLAMA_HOST`), `import-resume` does not error:
it prints an **assist payload** and exits 0. The payload contains the
extracted document text, the exact extraction contract the BYOK prompt
uses (JSON shape + rules + D9 proposal rules), and write-back
instructions. If you are the wrapping agent, YOU are the model: produce
the contract JSON, propose it to the user (offer approve-all), write the
approved items via `traitprint vault set-profile` and the `--from-json`
batch commands, then run `traitprint vault audit --json`. `--json` emits
the payload as `{"mode": "agent-assist", "contract": ..., "text": ...,
"write_back": ...}`. `--assist` forces the payload even when a key is
configured; `--no-assist` restores the hard error for headless runs.
BYOK remains required when no agent is wrapping the CLI. With `--propose`
the write-back section switches to `traitprint proposals add` commands
(one JSON object per item) and the verify step becomes
`traitprint proposals list --json` — the user then approves with
`traitprint proposals approve`. The full loop is the
`traitprint-import-resume` skill.

### Proposals (staged writes, D2/D9)

`proposals/*.json` files are staged writes awaiting review — the contract's
`$defs/proposal` shape (`id`, `kind`, `target_id`, `payload`, `rationale`,
`source`, `status`, `created_at`, `resolved_at`). Remote agents (hosted MCP
`vault_propose`), the web app, and local agents (`proposals add`) all stage
the same shape; the user reviews from any surface. Nothing touches the vault
until approval.

| Command | Notes |
|---|---|
| `traitprint proposals list [--status STATUS] [--json]` | Table (8-char id, kind, status, created, summary) or JSON array of full documents (+ `file`). Statuses: `pending`, `approved`, `rejected`, `withdrawn`. Unreadable files print `[warn]` lines on stderr — never a crash |
| `traitprint proposals show ID [--json]` | Payload + rationale + a current→proposed field diff against the live vault for `update_*` kinds. ID = full UUID or unambiguous hex prefix. `--json` emits `{proposal, file, diff}` |
| `traitprint proposals approve ID [-y]` | Validates the payload against the entity schema (Layer 0, hard reject), applies it (`add_*` creates; `update_*` partial-updates by `target_id` — clear error if the target is gone), and deletes the proposal file **in the same git commit** (contract rule 7). Duplicate skill names are rejected with the existing UUID |
| `traitprint proposals approve --all [-y]` | D9 one-step approve-all: applies every pending proposal in ONE batch commit (`Approve N proposals`); failures print `[err]` lines, those proposals stay pending, exit 1 |
| `traitprint proposals reject ID [-y]` | Sets `status: rejected` + `resolved_at`; the file is kept (and committed) |
| `traitprint proposals add --kind K [--target-id UUID] [--rationale R] [--source S] --payload-json -` | Stage a new pending proposal from a JSON object (file or stdin). Same validation as the hosted MCP `vault_propose`: kind enum, per-kind allowed payload keys, `target_id` required for `update_*` kinds (forbidden otherwise). Exit 1 with `[err] proposal: ...` lines on violations |

Payload rules (contract): full entity for `add_*`, only the changed fields
for `update_*`; narrative text travels in `payload.body` for experiences,
stories (`## Situation/Task/Action/Result` sections), and philosophies;
`update_profile` takes `{"basics": {"name"?, "label"?, "summary"?,
"email"?, "location"?}}` (no `target_id` — the profile is a singleton).
Never invent `target_id`s — copy them from `traitprint vault list`.
Pending proposals surface in `traitprint vault audit` as a minor
`proposals.pending` finding.

### Batch input (`--from-json`)

`add-skill`, `add-experience`, `add-story`, `add-philosophy`,
`add-education` accept `--from-json FILE` or `--from-json -` (stdin).
Cannot be combined with single-item arguments (exit 2). Input is a JSON
array:

```text
add-skill:      [{"name": str, "proficiency": int 1-5, "category"?: str, "notes"?: str}]
add-experience: [{"title": str, "company"?: str, "start_date"?: "YYYY-MM",
                  "end_date"?: "YYYY-MM", "description"?: str, "accomplishments"?: [str],
                  "skill_ids"?: [UUID str]}]
add-story:      [{"title": str, "situation"?: str, "task"?: str, "action"?: str,
                  "result"?: str, "lesson"?: str, "outcome"?: "win|failure|learning",
                  "theme_tags"?: [str], "skill_ids"?: [UUID str],
                  "experience_id"?: UUID str}]
add-philosophy: [{"title": str, "description"?: str, "category"?: str,
                  "evidence_story_ids"?: [UUID str]}]
add-education:  [{"institution": str, "degree"?: str, "field_of_study"?: str,
                  "start_date"?: "YYYY", "end_date"?: "YYYY", "description"?: str}]
```

Output: one `[ok] <name> [<uuid>]` / `[dup]` / `[err]` line per item, then
`Summary: added N, errors M`. Items are independent — valid items are
written even when others fail.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | operation failed: any batch item errored or was a duplicate; duplicate single `add-skill`; `audit --strict` with critical/major findings; runtime errors (`Error: ...` on stderr) |
| 2 | usage error: unknown flags, `--from-json` mixed with single-item args, missing required single-item fields |

### `vault audit --json` contract

```json
{
  "findings": [{"severity": "critical|major|minor", "code": "skill.unsupported_strength",
                 "section": "skills", "message": "...", "item_id": "...", "related_id": null}],
  "story_scores": [{"story_id": "...", "title": "...", "overall": 0.88,
                     "evidence_level": "demonstrates|mentions|weak",
                     "label": "Polished|Strong|Solid|Draft"}],
  "tensions": [{"philosophy_a_id": "...", "philosophy_b_id": "...", "category": "...",
                 "insight": "...", "confidence": 0.8}],
  "overall_coherence": 0.56,
  "summary": {"critical": 0, "major": 3, "minor": 1, "total": 4}
}
```

Finding codes worth acting on: `skill.unsupported_strength` (strong skill,
no story), `experience.no_story`, `experience.no_skills` (role links no
skills), `story.*` (thin/broken STAR fields,
missing metrics), dangling-reference findings, contradiction findings
(conflicting metrics or leader-vs-IC claims between stories),
`proposals.pending` (staged writes awaiting review — point the user at
`traitprint proposals list`). Tensions are nuance, not bugs — present
them as context-dependent thinking.

### `vault migrate --json` contract

```json
{"status": "already-v1|planned|migrated", "migrated": false,
 "files": ["profile.json", "..."],
 "proficiency_remaps": [{"id": "...", "name": "...", "from": 8, "to": 4}]}
```

### Cloud sync (git-native, sync-v1)

`traitprint sync` syncs the vault's **git history** with a hosted
per-user remote (git bundles over HTTPS; wire contract in
`docs/schema/sync-v1/`). Concurrent edits to different files merge
cleanly; real conflicts surface as standard git conflicts in the
affected files only. Requires `pip install 'traitprint[cloud]'` and
`traitprint login` (or `TRAITPRINT_API_TOKEN`). The legacy whole-vault
`traitprint push` / `traitprint pull` (last-write-wins) still work but
are deprecated in favor of `sync`.

| Command | Notes |
|---|---|
| `traitprint sync push [--json]` | Commits uncommitted hand edits, then uploads a thin bundle against the last-known server head (full bundle on first push; auto-retries full on `missing_prerequisites`). `--json` → `{pushed, head, server_head, ingest_status}` |
| `traitprint sync pull [--json]` | Fetches the server's bundle, then fast-forwards or merges locally. `--json` → `{fetched, result: "up_to_date"\|"fast_forward"\|"merged"\|"conflicts", conflicts: [files], head}` |
| `traitprint sync status [--json]` | No writes; probes `/vault-git/info`. `--json` → `{local_head, server_head, ingest_status, quarantine_summary: {count, items}, relation}` |

Sync flow rules (the server is fast-forward-only and never merges):

- **A 409 push rejection means the server has commits you don't.**
  Run `traitprint sync push` again only after
  `traitprint sync pull` — the CLI prints exactly this.
- **Merge conflicts exit 1 and leave the merge in progress.** The
  output lists the conflicted files plus the exact `git -C <vault>
  add -A` / `git -C <vault> commit` commands. Resolve the
  `<<<<<<</=======/>>>>>>>` markers with your file tools, run those
  commands, then `traitprint sync push`. Re-running `sync pull` while
  conflicts are unresolved re-prints the report; it never commits
  conflict markers.
- **A 422 push rejection means the server's Layer-0 validation failed**
  (ref not advanced). Every violation prints as
  `[err] <file> @ <pointer>: <message>` + `hint:` — fix the listed
  files, commit, push again.
- **`ingest_status: quarantined`** means the push was accepted but
  some entities have dangling UUID references (D10) — the items list
  the file and reason; fix the links in a follow-up commit.

## Validation policy (how writes are governed)

The vault is a repo; the audit is its CI. Layered (full table in
[the architecture doc](docs/agent-native-architecture.md), §5):

1. **Schema shape** — hard reject at every write.
2. **Referential integrity** — dangling UUIDs are a *warning* (audit
   finding), not a parse error.
3. **Taxonomy/evidence coverage** — flagged, never blocking; unresolved
   skills are first-class (`taxonomy_id: null`).
4. **Narrative coherence** — advisory findings only.

Agent rules (D9, non-negotiable):

- **Extraction is a proposal.** Skills/stories/proficiencies you infer
  from conversation or documents are proposed to the user for confirmation
  before any write — never silently added.
- **Extracted skills enter at modest proficiency (2-3)** pending the user
  confirming stronger demonstrated evidence.
- **Never invent taxonomy IDs.** Pass skill *names*; a deterministic
  resolver maps them. If `add-skill` answers "Did you mean: …?", relay the
  suggestion instead of guessing.
- **Run `traitprint vault audit --json` after a batch of writes** and
  close (or report) the gaps before declaring the work done.

## MCP stdio server

```
traitprint mcp-serve        # blocks; speak MCP JSON-RPC over stdio
```

Client config (Claude Desktop, Cursor, Zed, …):

```json
{"mcpServers": {"traitprint": {"command": "traitprint", "args": ["mcp-serve"],
  "env": {"TRAITPRINT_VAULT_DIR": "/home/you/.traitprint"}}}}
```

Four read-only tools, response schemas mirroring the hosted cloud MCP
server (swap local ↔ cloud by changing a URL): `get_profile_summary`,
`search_skills`, `find_story`, `get_philosophy`. Every tool returns a
`{"result": ..., "meta": {...}}` envelope. Proficiency uses the full
five-label vocabulary (`familiar`/`working`/`proficient`/`expert`/
`authority`); `search_skills min_proficiency` accepts any label or an
integer 1-5. `find_story theme` matches `theme_tags` first, then body
text; `get_philosophy` filters by `topic` and/or `category`. (The cloud
server still exposes a legacy 4-label proficiency enum; parity catch-up
is tracked cloud-side.)

Five prompts — `fill_vault(focus?)`, `mine_story_gaps`, `discover_skills`,
`draft_star_story(experience?)`, `audit_coherence` — served verbatim from
the Agent Skills below, so prompt and skill never drift.

## Agent Skills

Six SKILL.md workflow skills (agentskills.io format) live under
[`skills/`](skills/), with a shared CLI cheatsheet at
[`skills/shared/cli-reference.md`](skills/shared/cli-reference.md). Install
into any skills-aware agent with `npx skills add DataViking-Tech/traitprint`;
they also ship inside the wheel as `traitprint/data/skills/`.

## Gotchas

- **Interactive fallback**: `add-*` without required flags prompts on
  stdin and hangs non-interactive shells. Always pass flags; always `-y`
  on `remove`/`rollback`.
- **Duplicate skills exit 1** with the existing UUID in the message;
  `remove` then re-add to replace.
- **A failed git auto-commit never fails the write.** The CLI warns on
  stderr ("vault saved but git commit failed: …") and keeps exit code 0;
  fix the git problem, then commit inside the vault to restore history.
- **Taxonomy may override your `--category`** on an exact match; pass
  `--force-category` to keep yours.
- **Hand-edited frontmatter**: allowed keys only; unknown keys violate the
  schema. Dangling UUID references become audit findings, not errors.
- **Cloud commands need extras**: `login`/`logout`/`sync`/`push`/`pull`
  require `pip install 'traitprint[cloud]'`; PDF/DOCX in `import-resume`
  and `extract-text` require `'traitprint[import]'`. A base install
  makes zero network calls.
- **Prefer `traitprint sync` over legacy `push`/`pull`.** `sync` moves
  git history (real merges); the legacy commands move the whole vault
  JSON with last-write-wins and remain only for servers without the
  `/vault-git` endpoints.
- **Default-host Ollama is not an auto-detect signal.** `import-resume`
  only counts Ollama as configured when `OLLAMA_HOST` (env or
  `.credentials`) is set; otherwise a keyless run enters agent-assist
  mode. Pass `--provider ollama` or set `OLLAMA_HOST` to use a local
  default-port server.
- **Legacy `push` runs a pre-push audit** and blocks on critical findings
  (`--strict` blocks on major too; `--skip-audit` bypasses). `sync push`
  runs no client-side audit — the server hard-rejects Layer-0 violations
  (422) instead; run `traitprint vault audit` yourself before pushing.
  Token auth: `TRAITPRINT_API_TOKEN` beats `TRAITPRINT_PASSWORD` beats
  the prompt.
- **`vault export -f json`** emits the lossless single-document form for
  v0 consumers; the on-disk tree stays v1.
