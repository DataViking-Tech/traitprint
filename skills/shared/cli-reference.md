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
├── proposals/*.json    # staged writes awaiting user review
└── .git/               # every CLI write auto-commits
```

You MAY hand-edit these files directly — it is often the best way to polish
narrative text. Rules:

- **Identity lives in the frontmatter `id` (a UUID), never the filename.**
  Filenames are kebab-case slugs; rename freely, but never change or remove
  `id`.
- **Frontmatter allows specific keys only** (`additionalProperties: false`):
  - experiences: `id, title, company, start_date, end_date, accomplishments,
    skill_ids, source, created_at, updated_at`
  - stories: `id, title, skill_ids, experience_id, outcome, theme_tags,
    source, created_at, updated_at`
  - philosophies: `id, title, category, evidence_story_ids, source,
    created_at, updated_at`
- **Story bodies use the STAR heading convention**: `## Situation`,
  `## Task`, `## Action`, `## Result` — each required, in that order —
  plus an optional `## Lesson`. The markdown body is the source of truth
  for narrative text.
- **Cross-links are UUIDs** (`skill_ids` on stories *and* experiences,
  `experience_id`, `evidence_story_ids`). A story's `skill_ids` are the
  skills it evidences; an experience's `skill_ids` are the skills exercised
  in that role. A dangling UUID is not a parse error — it becomes
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
traitprint vault extract-text FILE  # deterministic text extraction from
                                    # PDF|DOCX|TXT|MD (no LLM, no writes);
                                    # --json -> {file, format, chars, text}
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
  --accomplishment "..." --skill-id <UUID>
                                          # --accomplishment and --skill-id are repeatable
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
traitprint vault import-resume FILE       # resolution order: --provider flag ->
                                          # configured BYOK key -> agent-assist
                                          # mode (emits extracted text + the
                                          # extraction contract for YOU, the
                                          # wrapping agent, to complete; exit 0);
                                          # --json emits the assist payload as
                                          # JSON; --assist forces the payload,
                                          # --no-assist errors when no key;
                                          # --yes/--dry-run apply to the BYOK path;
                                          # --propose stages extracted items as
                                          # pending proposals instead of writing
```

### Positioning lenses (`vault lens` — config-like, non-factual)

A **lens** is a named, non-destructive projection over the one vault: it
selects, orders, and re-weights existing content (plus optional
headline/bio overrides) so the same grounded facts read differently for a
target role. A lens never asserts a fact absent from the vault. The vault
holds **at most 5 lenses**; the slug `none` is reserved (it is the
canonical-rendering escape hatch on the read tools). Because lenses are
config-like and non-factual, the CLI edits them directly (each write
auto-commits) — over the hosted MCP server the same edits are staged with
`vault_propose` (`add_lens`/`update_lens`) instead.

```bash
traitprint vault lens add --slug SLUG --name "..."          # slug: lowercase kebab-case
  # optional: --headline-override "..." --bio-override "..."
  #           --target-archetype "..."        (repeatable)
  #           --signature-experience <UUID>   (repeatable, in display order)
  #           --signature-story <UUID>        (repeatable, in display order)
  #           --salience <SKILL_UUID>=LEVEL   (repeatable; LEVEL: core|supporting|suppressed)
  #           --default                        (make it the default lens)
traitprint vault lens update LENS [same optional flags]     # edit in place
traitprint vault lens set-default LENS                      # make LENS the sole default
traitprint vault lens remove LENS -y                        # delete
```

`LENS` resolves by slug, full UUID, or 8-hex id prefix (the prefix is
CLI-only convenience; the MCP tools and store use the strict slug / full-id
grammar). `vault lens add` and `vault lens update` also accept
`--from-json` (a JSON array of lens objects; each `update` item carries a
`ref`) for batch input. Read lenses with `traitprint vault show` (or the
`vault_lens_list` / `vault_lens_get` MCP tools). Unspecified skills default
to `supporting` salience — only name the exceptions.

### Proposals (staged writes — the user approves, never you)

`proposals/*.json` are staged writes awaiting review. Stage changes with
`proposals add`; the USER approves or rejects — never approve on their
behalf without explicit confirmation:

```bash
traitprint proposals list                 # table; --status pending|approved|
                                          # rejected|withdrawn; --json -> full
                                          # proposal documents (+ "file")
traitprint proposals show <ID>            # payload + rationale + current->proposed
                                          # diff for update_* kinds; ID = full UUID
                                          # or 8-char prefix; --json -> {proposal,
                                          # file, diff}
traitprint proposals approve <ID> -y      # validate (hard reject on schema
                                          # violations), apply to the vault, and
                                          # delete the proposal file in ONE commit
traitprint proposals approve --all -y     # apply every pending proposal in one
                                          # batch commit ("Approve N proposals");
                                          # failures stay pending, exit 1
traitprint proposals reject <ID> -y       # status=rejected + resolved_at;
                                          # the file is kept
traitprint proposals add --kind add_skill --rationale "..." \
  --payload-json -                        # stage ONE proposal from a JSON object
                                          # on stdin; kinds: add_*/update_* per
                                          # entity + update_profile; update_* kinds
                                          # need --target-id <UUID>
```

Payload rules: full entity for `add_*`, changed fields only for `update_*`;
narrative text goes in `payload.body` (experiences, stories — STAR
sections — and philosophies); `update_profile` takes
`{"basics": {"name"?, "label"?, "summary"?, "email"?, "location"?}}`.
Unknown payload keys are rejected with the allowed-key list. Pending
proposals show up in `traitprint vault audit` as a `proposals.pending`
finding.

### Cloud sync (git-native, sync-v1 — needs `pip install 'traitprint[cloud]'`)

The `sync` group syncs the vault's git history with the hosted remote
(git bundles over HTTPS). Edits to different files merge cleanly; real
conflicts surface as standard git conflicts. Log in first
(`traitprint login`, or set `TRAITPRINT_API_TOKEN`). The legacy
whole-vault `traitprint push` / `traitprint pull` are deprecated.

```bash
traitprint sync status              # local vs server heads + ingest state;
                                    # --json -> {local_head, server_head,
                                    #   ingest_status, quarantine_summary, relation}
traitprint sync push                # commit pending hand edits, upload new commits;
                                    # --json -> {pushed, head, server_head,
                                    #   ingest_status}; 409 -> pull first; 422 ->
                                    #   per-file [err] lines to fix, then re-push
traitprint sync pull                # fetch + fast-forward or merge server commits;
                                    # --json -> {fetched, result, conflicts, head};
                                    # conflicts exit 1 and print the exact
                                    # git add/commit commands to finish the merge
```

On merge conflicts: resolve the `<<<<<<</=======/>>>>>>>` markers in the
listed files with your file tools, run the printed `git -C <vault> add
-A` and `git -C <vault> commit` commands, then `traitprint sync push`.

### Batch mode (preferred for 3+ items)

`add-skill`, `add-experience`, `add-story`, `add-philosophy`, and
`add-education` accept `--from-json FILE` (or `--from-json -` for stdin).
Input is a JSON array:

```text
add-skill:      [{"name": str, "proficiency": int 1-5, "category"?: str, "notes"?: str}]
add-experience: [{"title": str, "company"?: str, "start_date"?: "YYYY-MM",
                  "end_date"?: "YYYY-MM", "description"?: str,
                  "accomplishments"?: [str], "skill_ids"?: [UUID str]}]
add-story:      [{"title": str, "situation"?: str, "task"?: str, "action"?: str,
                  "result"?: str, "lesson"?: str, "outcome"?: "win|failure|learning",
                  "theme_tags"?: [str], "skill_ids"?: [UUID str],
                  "experience_id"?: UUID str}]
add-philosophy: [{"title": str, "description"?: str, "category"?: str,
                  "evidence_story_ids"?: [UUID str]}]
add-education:  [{"institution": str, "degree"?: str, "field_of_study"?: str,
                  "start_date"?: "YYYY", "end_date"?: "YYYY", "description"?: str}]
```

Output is one `[ok]` / `[dup]` / `[err]` line per item plus
`Summary: added N, errors M`.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | operation failed: any batch item errored or duplicated; `remove` of an id that matches nothing; `audit --strict` found critical/major issues |
| 2 | usage error: bad flags, or mixing `--from-json` with single-item arguments |

## Validation policy (non-negotiable)

1. **Extraction is a proposal.** Skills, stories, or proficiencies you infer
   from conversation or documents are PROPOSED to the user for confirmation
   before any write. Never silently add to someone's professional identity.
2. **Modest entry proficiency.** Extracted skills enter at 2-3 pending the
   user confirming stronger demonstrated evidence.
3. **Never invent taxonomy IDs or UUIDs.** Pass skill *names*; the CLI's
   deterministic resolver maps names to the taxonomy. If `add-skill` prints
   `[note] added as a custom skill (no taxonomy match)` with suggestions,
   the skill is already saved — relay the suggestions to the user and use
   the note's remove/re-add command if they meant one of them.
   Copy cross-link UUIDs from `traitprint vault list` output.
4. **Audit before finishing.** Run `traitprint vault audit --json` after a
   batch of writes and address (or report) what it finds. The audit is the
   vault's CI: dangling references, unsupported strong skills, broken
   stories, and story-less roles all surface there.
5. **A lens is emphasis, never invention.** A positioning lens may only
   select, order, and re-weight facts already in the vault (plus reframe the
   headline/bio) — it must never introduce a title, metric, skill, or story
   the vault can't back. If a desired framing needs a missing fact, add that
   fact first (as a proposal the user approves), then let the lens surface
   it. The 5-lens cap and the reserved `none` slug are enforced at every
   write surface (CLI, proposal apply, cloud ingest); a lens whose signature
   or salience reference points at a deleted entity is not a parse error —
   it surfaces as `disputed`, so repair or drop the stale reference.
