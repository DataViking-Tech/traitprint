# Staging Vault Writes from External Tools

**Audience:** authors of third-party exporters, importers, and plugins —
in any language — that want to move data *into* a traitprint-managed
vault.
**Contract:** vault v1 `$defs/proposal`
([`docs/schema/vault-v1`](schema/vault-v1/README.md), rule 7).

The proposals channel is the sanctioned integration surface for
external writers. Remote agents (the hosted MCP server's
`vault_propose`), the web app, the local CLI, and your tool all stage
the **same document shape**; the user reviews from any surface. Nothing
touches the vault until the user approves. That property is what makes
a third-party writer safe to ship: your tool emits *suggestions*, never
silent writes.

## The proposal document

One JSON object per file under `<vault>/proposals/`:

```json
{
  "id": "7d444840-9dc0-11d1-b245-5ffdce74fad2",
  "kind": "add_story",
  "target_id": null,
  "payload": {
    "title": "Migrated the billing pipeline",
    "theme_tags": ["migration"],
    "body": "## Situation\n...\n## Task\n...\n## Action\n...\n## Result\n...\n## Lesson\n..."
  },
  "rationale": "Found in the working directory's story bank.",
  "source": "my-exporter",
  "status": "pending",
  "created_at": "2026-07-02T12:00:00Z",
  "resolved_at": null
}
```

Field rules:

- `id` — a UUIDv4 you generate for the *proposal document itself*.
  Filenames are `<kind>-<id8>.json` slugs (kebab-case kind + first 8
  hex chars of the id), but identity is always the document `id`.
- `kind` — one of the `add_*` / `update_*` kinds. `add_*` payloads
  carry the full entity; `update_*` payloads carry only the changed
  fields.
- `target_id` — required for `update_*` kinds (the UUID of the entity
  being changed), forbidden otherwise. `update_profile` is the
  exception: the profile is a singleton and never takes a `target_id`.
- `payload` — allowed keys are per-kind. Narrative text travels in
  `payload.body` for experiences, stories (`## Situation` / `## Task` /
  `## Action` / `## Result` headings, optional `## Lesson`), and
  philosophies. `update_profile` takes `{"basics": {...}}` with
  JSON Resume-compatible keys.
- `source` — a stable identity for your tool (e.g. `"my-exporter"`).
  It is shown at review time and recorded as entity provenance on
  approval.
- `status` — emit `"pending"`; the review flow owns every other value.

## The authoritative key tables

Do not hand-copy the per-kind payload keys from this page — ask an
installed CLI, which states exactly what the validator enforces:

```console
$ traitprint proposals contract          # human-readable
$ traitprint proposals contract --json   # machine-readable document
```

The `--json` document (`kinds`, `statuses`, `payload_keys`,
`required_payload_keys`, `profile_basics_keys`,
`target_id_required_for`) is stable output: vendor it in your test
fixtures and diff it against a live install in CI to catch contract
drift instead of re-reading Python source. Additive contract revisions
may *add* keys; keys are never removed or renamed within vault v1.

## Validating your output

`traitprint proposals validate` runs the exact checks the review queue
runs — document shape plus kind/target/payload-key rules — read-only,
against plain files, with **no vault required**:

```console
$ my-exporter --out ./staging
$ traitprint proposals validate ./staging
[ok] staging/add-story-7d444840.json
[ok] staging/update-profile-91c2aa01.json
Summary: 2 valid, 0 invalid
```

Exit code 0 means every document is valid; 1 lists `[err]` lines per
problem. `--json` emits `{valid, checked, results}` for smoke tests.
Point it at files or at a directory (validates every `*.json` inside).
A passing run means the files will load cleanly into the review queue
and survive `proposals add`'s validation byte-for-byte.

## Getting proposals into the vault

Two supported paths:

1. **Shell out to the CLI** (recommended when the CLI is installed).
   One call per item; it validates, writes the slug file, and
   git-commits:

   ```console
   $ traitprint proposals add --kind add_skill --source my-exporter \
       --rationale "found in project history" --payload-json - <<'EOF'
   {"name": "Rust", "proficiency": 2}
   EOF
   ```

2. **Write the files yourself** into `<vault>/proposals/` using the
   document shape above (pre-flight them with the
   `traitprint proposals validate` command). Hand-written files are
   picked up by the next review
   command; unreadable or invalid files surface as `[warn]` findings,
   never a crash. They are committed with the next CLI write, or
   commit them yourself inside the vault.

Either way, the user reviews with `traitprint proposals list`,
inspects with `traitprint proposals show`, and applies with
`traitprint proposals approve` (or `traitprint proposals approve
--all`). Pending proposals also surface as a `proposals.pending`
finding in `traitprint vault audit`.

## Safety invariants (non-negotiable)

These mirror the agent rules in [`AGENTS.md`](../AGENTS.md) (D9) and
apply equally to external tools:

- **Extraction is a proposal.** Anything your tool infers from
  documents or history is staged for review — never written directly
  to `profile.json`, `skills.json`, or the markdown trees.
- **Never invent taxonomy IDs.** Emit skill *names* and leave
  `taxonomy_id` out; the deterministic resolver maps names on
  approval.
- **Never invent UUIDs for existing entities.** `target_id`,
  `skill_ids`, `experience_id`, and `evidence_story_ids` must be
  copied from the vault (`traitprint vault list skills --json` and
  friends), not fabricated. Omit a link you cannot resolve — a missing
  link is an audit finding, a fabricated one is corruption.
  (Generating a fresh UUID for the proposal document itself, or as a
  pre-linked `payload.id` on `add_*` bundles you emit together, is
  fine.)
- **Extracted skills enter at modest proficiency (2–3)** pending the
  user confirming stronger demonstrated evidence.
- **No network egress requirement.** Reading local files and writing
  local proposal files needs no network; a pure local transform is the
  easiest integration to trust and review.
