# Traitprint CLI & vault reference

Shared reference for the Traitprint agent skills. The full operating manual
is `AGENTS.md` in the repo root; the on-disk format contract is
`docs/schema/vault-v1/`.

## The vault on disk (schema v1)

A vault is a git-versioned directory (default `~/.traitprint`; override with
`--vault-dir` or `$TRAITPRINT_VAULT_DIR`):

```
<vault>/
├── traitprint.json     # manifest: schema_version=1, vault id, updated_at
├── profile.json        # identity block (JSON Resume-compatible keys)
├── skills.json         # JSON array of skill objects
├── education.json      # JSON array
├── experiences/*.md    # YAML frontmatter + body = role description
├── stories/*.md        # frontmatter + ## Situation/Task/Action/Result (+ ## Lesson)
├── philosophies/*.md   # frontmatter + body = the stance
└── .git/               # every CLI write auto-commits
```

You MAY hand-edit these files directly — it is often the best way to polish
narrative text. Rules:

- **Identity lives in the frontmatter `id` (a UUID), never the filename.**
  Filenames are kebab-case slugs; rename freely, but never change or remove
  `id`.
- **Frontmatter allows specific keys only** (`additionalProperties: false`):
  - experiences: `id, title, company, start_date, end_date, accomplishments,
    source, created_at, updated_at`
  - stories: `id, title, skill_ids, experience_id, outcome, theme_tags,
    source, created_at, updated_at`
  - philosophies: `id, title, category, evidence_story_ids, source,
    created_at, updated_at`
- **Story bodies use the STAR heading convention**: `## Situation`,
  `## Task`, `## Action`, `## Result` — each required, in that order —
  plus an optional `## Lesson`. The markdown body is the source of truth
  for narrative text.
- **Cross-links are UUIDs** (`skill_ids`, `experience_id`,
  `evidence_story_ids`). A dangling UUID is not a parse error — it becomes
  an audit finding. Never fabricate a UUID; copy real ones from
  `traitprint vault list` output.
- After hand edits, run `traitprint vault audit` to validate; the change is
  committed on the next CLI write, or commit it yourself inside the vault
  directory.

## Proficiency scale (1-5)

| Level | Label | Meaning |
|---|---|---|
| 1 | familiar | has touched it |
| 2 | working | uses it with support |
| 3 | proficient | independent day-to-day use |
| 4 | expert | goes deep; others ask them |
| 5 | authority | recognized beyond their own team |

Rate from demonstrated evidence, not self-report. Skills extracted from
conversation enter at 2-3 until the user confirms stronger evidence.

## CLI cheatsheet

Reads (never modify the vault):

```bash
traitprint vault show               # summary; -v for full dump with UUIDs;
                                    # --json for the full vault document
traitprint vault list skills        # also: experiences|stories|philosophies|education
                                    # --json -> [{id, type, name|title}]
traitprint vault audit              # coherence report; --json to parse;
                                    # --severity critical|major|minor; --strict exits 1
traitprint vault history -n 10      # git log of vault changes; --json -> [{sha, message}]
traitprint vault diff               # changes since previous commit;
                                    # --json -> {from_sha, to_sha, diff_text}
traitprint vault export -f json     # also: markdown|jsonresume|synthpanel-persona; -o FILE
```

Writes (flags only — omitting required flags triggers interactive prompts,
which hang non-interactive shells; each write auto-commits):

```bash
traitprint vault set-profile --name "..." --headline "..." --summary "..." \
  --location "..." --email "..."          # pass only the fields to change
traitprint vault add-skill "Name" --proficiency 3 --category technical \
  --notes "..."         # --category is optional (free-form: technical|soft|domain|tool);
                        # a taxonomy match fills it when omitted
traitprint vault add-experience --title "..." --company "..." \
  --start-date YYYY-MM --end-date YYYY-MM --description "..." \
  --accomplishment "..."                  # --accomplishment is repeatable
traitprint vault add-story --title "..." --situation "..." --task "..." \
  --action "..." --result "..." --lesson "..." --outcome win|failure|learning \
  --theme-tag TAG --skill-id <UUID> --experience-id <UUID>
                                          # --theme-tag and --skill-id are repeatable
traitprint vault add-philosophy --title "..." --description "..." \
  --category leadership --evidence-id <STORY_UUID>
  # categories: leadership|collaboration|technical-approach|culture|decision-making
traitprint vault add-education --institution "..." --degree "..." \
  --field "..." --start-date YYYY --end-date YYYY
traitprint vault remove <UUID> -y         # any section, by UUID
traitprint vault rollback -y              # undo the last vault commit
traitprint vault migrate                  # legacy v0 vault -> v1 file tree
                                          # (remaps old proficiency scale to 1-5)
```

### Batch mode (preferred for 3+ items)

`add-skill`, `add-experience`, `add-story`, and `add-philosophy` accept
`--from-json FILE` (or `--from-json -` for stdin). Input is a JSON array:

```text
add-skill:      [{"name": str, "proficiency": int 1-5, "category"?: str, "notes"?: str}]
add-experience: [{"title": str, "company"?: str, "start_date"?: "YYYY-MM",
                  "end_date"?: "YYYY-MM", "description"?: str,
                  "accomplishments"?: [str]}]
add-story:      [{"title": str, "situation"?: str, "task"?: str, "action"?: str,
                  "result"?: str, "lesson"?: str, "outcome"?: "win|failure|learning",
                  "theme_tags"?: [str], "skill_ids"?: [UUID str],
                  "experience_id"?: UUID str}]
add-philosophy: [{"title": str, "description"?: str, "category"?: str,
                  "evidence_story_ids"?: [UUID str]}]
```

Output is one `[ok]` / `[dup]` / `[err]` line per item plus
`Summary: added N, errors M`.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | operation failed: any batch item errored or duplicated; `audit --strict` found critical/major issues |
| 2 | usage error: bad flags, or mixing `--from-json` with single-item arguments |

## Validation policy (non-negotiable)

1. **Extraction is a proposal.** Skills, stories, or proficiencies you infer
   from conversation or documents are PROPOSED to the user for confirmation
   before any write. Never silently add to someone's professional identity.
2. **Modest entry proficiency.** Extracted skills enter at 2-3 pending the
   user confirming stronger demonstrated evidence.
3. **Never invent taxonomy IDs or UUIDs.** Pass skill *names*; the CLI's
   deterministic resolver maps names to the taxonomy. If `add-skill` replies
   "Did you mean: …?", relay the suggestion to the user instead of guessing.
   Copy cross-link UUIDs from `traitprint vault list` output.
4. **Audit before finishing.** Run `traitprint vault audit --json` after a
   batch of writes and address (or report) what it finds. The audit is the
   vault's CI: dangling references, unsupported strong skills, broken
   stories, and story-less roles all surface there.
