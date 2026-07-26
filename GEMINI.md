# Traitprint — career vault context

Traitprint is a **local-first, user-owned career identity vault**: skills,
experiences, STAR stories, philosophies, and education, stored as a
git-versioned directory of JSON + markdown files the user owns. This
extension gives you two surfaces:

1. **Hosted MCP server** (wired by this extension) at
   `https://api.traitprint.com/functions/v1/mcp-server` — query the user's
   synced vault and stage writes as proposals. First use triggers an OAuth
   sign-in to traitprint.com; approve it with `/mcp auth traitprint`.
   Headless alternative: an `sk_` API key from the web app
   (Settings → API Keys) sent as a `Bearer` header.
2. **Local CLI** (`pip install traitprint`) — direct, audit-gated access to
   the vault on this machine. The bundled skills
   (fill-vault, mine-story-gaps, discover-skills, draft-star-story,
   audit-coherence, import-resume, capture-story, agent-vault-sync,
   position-lens, deepen-story, improve-profile) drive the CLI; the
   full operating manual is `AGENTS.md` in this extension directory.

Prefer the local CLI when the `traitprint` command is available; fall back
to the hosted MCP tools otherwise.

## Hosted MCP tools

Read: `get_profile_summary`, `search_skills`, `find_story`,
`find_experience`, `get_philosophy`, `vault_lens_list`, `vault_lens_get`,
`vault_sync_status` (server head + ingest/quarantine state).
All but `find_experience` and `vault_sync_status` are shared with the
local stdio server and use the same response envelope — but the servers
are not identical: local additionally has a `doctor` tool, the
`vault_sync` cloud-sync trigger (status/push/pull; the hosted server
cannot reach a local vault, so it serves only the read-only
`vault_sync_status`), and the `find_bullets` resume-bullet
inventory (its hosted mirror is rolling out — do not call it hosted yet),
a free-text `query` filter on `find_story`, real `lesson` text, an
inferred `outcome`, and a `category` filter on `get_philosophy`; hosted
`find_story` instead has a `skill` filter and an uncapped `total`
inventory.
Write (staged, never direct): `vault_propose` creates a proposal the user
must approve; `vault_list_proposals` shows pending ones; `vault_retract`
withdraws one. Jobs (cloud-only): `jobs_search`, `jobs_match`, `job_get`,
`resume_tailor`, `job_submit`.
Tools are scope-filtered per session — if a tool is missing, the user
granted a narrower scope. OAuth grants also freeze the scope set at
consent time: a tool gated on a scope added *after* the user connected
stays invisible until they reconnect (re-run the OAuth consent), while
tools riding an already-granted scope appear without re-consent.
Suggest a reconnect when a documented tool is absent.

## The vault on disk (schema v1)

Default `~/.traitprint`; override with `--vault-dir DIR` or
`$TRAITPRINT_VAULT_DIR`.

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
├── lenses.json         # positioning lenses (only written once a lens exists)
└── .git/               # every CLI write auto-commits
```

Hand-editing is supported: identity lives in the frontmatter `id` (UUID),
never the filename; frontmatter accepts allowed keys only; story bodies use
the `## Situation` / `## Task` / `## Action` / `## Result` heading
convention; cross-links (`skill_ids`, `experience_id`,
`evidence_story_ids`) are UUIDs — never fabricate one, copy it from
`traitprint vault list` output.

## Proficiency scale (1-5)

`1` familiar · `2` working · `3` proficient · `4` expert · `5` authority.
Rate from demonstrated evidence, not self-report. Skills at 4-5 with no
complete STAR story linked are flagged as unsupported by the audit.

## CLI quick reference

Read: `traitprint vault show`, `traitprint vault list <section> --json`,
`traitprint vault audit --json`, `traitprint vault export -f json`.
Write (each auto-commits): `traitprint init`,
`traitprint vault set-profile`, `traitprint vault add-skill NAME -p 1..5`,
`traitprint vault add-experience`, `traitprint vault add-story`,
`traitprint vault add-philosophy`, `traitprint vault add-education`,
`traitprint vault remove UUID -y`, `traitprint vault import-resume PATH`.
Proposals: `traitprint proposals list`, `traitprint proposals show ID`,
`traitprint proposals approve ID`, `traitprint proposals reject ID`.
Sync (cloud extras): `traitprint sync push`, `traitprint sync pull`,
`traitprint sync status`.

Always pass flags — most `add-*` commands fall back to interactive
prompts and hang non-interactive shells (`add-skill` instead fails fast
with exit 2). Batch mode: every `add-*` takes
`--from-json -` (JSON array on stdin). The cheatsheet is
`skills/shared/cli-reference.md` in this extension directory.

## Validation rules (non-negotiable)

- **Extraction is a proposal.** Skills, stories, or proficiencies you infer
  from conversation or documents are proposed to the user for confirmation
  before any write — never silently added. On the hosted server this is
  enforced (`vault_propose` is the only write); on the CLI, use
  `traitprint proposals add` or present your plan first.
- **Extracted skills enter at modest proficiency (2-3)** pending the user
  confirming stronger demonstrated evidence.
- **Never invent taxonomy IDs or UUIDs.** Pass skill *names*; a
  deterministic resolver maps them.
- **Schema violations are hard-rejected; dangling UUID links are audit
  findings**, not errors. Run `traitprint vault audit --json` after a batch
  of writes and close the gaps before declaring the work done.
