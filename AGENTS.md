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
├── .credentials        # gitignored, never synced
└── .git/               # every CLI write auto-commits
```

### Hand-editing rules

Editing the files directly is supported and often the best way to polish
narrative text. Constraints:

- **Identity is the frontmatter/JSON `id` (UUIDv4), never the filename.**
  Filenames are slugs — rename freely; never change or delete `id`.
- **Frontmatter accepts allowed keys only** (`additionalProperties: false`
  in the schema):
  - experiences: `id, title, company, start_date, end_date,
    accomplishments, source, created_at, updated_at`
  - stories: `id, title, skill_ids, experience_id, outcome, theme_tags,
    source, created_at, updated_at`
  - philosophies: `id, title, category, evidence_story_ids, source,
    created_at, updated_at`
- **Story bodies use the STAR heading convention** — `## Situation`,
  `## Task`, `## Action`, `## Result`, each required, in that order;
  optional `## Lesson`. Markdown bodies are the source of truth for
  narrative text.
- **Cross-links are UUIDs** (`skill_ids`, `experience_id`,
  `evidence_story_ids`). A dangling UUID does not break parsing — it
  surfaces as an audit finding. Never fabricate UUIDs; copy them from
  `traitprint vault list` output.
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
| `traitprint vault show [-v]` | Summary; `-v` dumps everything incl. UUIDs and git metadata |
| `traitprint vault list <section>` | Table with UUIDs; sections: `skills`, `experiences`, `stories`, `philosophies`, `education` |
| `traitprint vault audit [--json] [--severity critical\|major\|minor] [--strict]` | Coherence report (below) |
| `traitprint vault history [-n N]` | Vault git log |
| `traitprint vault diff` | Changes since previous commit |
| `traitprint vault export -f <fmt> [-o FILE]` | Formats: `json` (lossless single doc), `markdown`, `jsonresume`, `synthpanel-persona`. `traitprint export` is a top-level alias |

### Write commands (each auto-commits)

| Command | Required | Optional |
|---|---|---|
| `traitprint init` | — | creates dir, git repo, empty v1 vault |
| `traitprint vault set-profile` | at least one flag | `--name --headline --summary --location --email`; omitted fields keep values, `""` clears |
| `traitprint vault add-skill NAME -p 1..5 -c CAT` | name, proficiency, category | `--notes`, `--force-category` (keep your category over the taxonomy's) |
| `traitprint vault add-experience` | `--title` | `--company --start-date YYYY-MM --end-date YYYY-MM --description --accomplishment ...` (repeatable) |
| `traitprint vault add-story` | `--title` | `--situation --task --action --result --skill-id UUID` (repeatable) `--experience-id UUID` |
| `traitprint vault add-philosophy` | `--title` | `--description --category --evidence-id STORY_UUID` (repeatable); categories: `leadership`, `collaboration`, `technical-approach`, `culture`, `decision-making` |
| `traitprint vault add-education` | `--institution` | `--degree --field --start-date YYYY --end-date YYYY --description` (no batch mode) |
| `traitprint vault remove UUID -y` | UUID | searches all sections |
| `traitprint vault rollback -y` | — | reset tree to previous commit |
| `traitprint vault migrate [--dry-run] [--json]` | — | v0 → v1 file tree; idempotent |
| `traitprint vault import-resume PATH` | path | BYOK LLM extraction; `--provider --model --yes --dry-run`; needs `pip install 'traitprint[import]'` |

### Batch input (`--from-json`)

`add-skill`, `add-experience`, `add-story`, `add-philosophy` accept
`--from-json FILE` or `--from-json -` (stdin). Cannot be combined with
single-item arguments (exit 2). Input is a JSON array:

```text
add-skill:      [{"name": str, "proficiency": int 1-5, "category": str, "notes"?: str}]
add-experience: [{"title": str, "company"?: str, "start_date"?: "YYYY-MM",
                  "end_date"?: "YYYY-MM", "description"?: str, "accomplishments"?: [str]}]
add-story:      [{"title": str, "situation"?: str, "task"?: str, "action"?: str,
                  "result"?: str, "skill_ids"?: [UUID str], "experience_id"?: UUID str}]
add-philosophy: [{"title": str, "description"?: str, "category"?: str,
                  "evidence_story_ids"?: [UUID str]}]
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
no story), `experience.no_story`, `story.*` (thin/broken STAR fields,
missing metrics), dangling-reference findings, contradiction findings
(conflicting metrics or leader-vs-IC claims between stories). Tensions are
nuance, not bugs — present them as context-dependent thinking.

### `vault migrate --json` contract

```json
{"status": "already-v1|planned|migrated", "migrated": false,
 "files": ["profile.json", "..."],
 "proficiency_remaps": [{"id": "...", "name": "...", "from": 8, "to": 4}]}
```

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

Four read-only tools, response schemas identical to the hosted cloud MCP
server (swap local ↔ cloud by changing a URL): `get_profile_summary`,
`search_skills`, `find_story`, `get_philosophy`. Every tool returns a
`{"result": ..., "meta": {...}}` envelope.

Five prompts — `fill_vault(focus?)`, `mine_story_gaps`, `discover_skills`,
`draft_star_story(experience?)`, `audit_coherence` — served verbatim from
the Agent Skills below, so prompt and skill never drift.

## Agent Skills

Five SKILL.md workflow skills (agentskills.io format) live under
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
- **Taxonomy may override your `--category`** on an exact match; pass
  `--force-category` to keep yours.
- **Hand-edited frontmatter**: allowed keys only; unknown keys violate the
  schema. Dangling UUID references become audit findings, not errors.
- **`education.json` has no batch mode** — loop `add-education`.
- **Cloud commands need extras**: `login`/`logout`/`push`/`pull` require
  `pip install 'traitprint[cloud]'`; `import-resume` requires
  `'traitprint[import]'`. A base install makes zero network calls.
- **`push` runs a pre-push audit** and blocks on critical findings
  (`--strict` blocks on major too; `--skip-audit` bypasses). Token auth:
  `TRAITPRINT_API_TOKEN` beats `TRAITPRINT_PASSWORD` beats the prompt.
- **`vault export -f json`** emits the lossless single-document form for
  v0 consumers; the on-disk tree stays v1.
