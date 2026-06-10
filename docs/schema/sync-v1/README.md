# Sync v1 — Git-Bundle-over-HTTP Protocol Contract

**Status:** Stable contract (Phase 2 of the
[agent-native architecture](../../agent-native-architecture.md), decisions
D1/D5/D10; Workstream A of the cloud companion spec)
**Consumers:** the `traitprint` CLI (client side, replaces the
`vault-sync` whole-vault LWW transport in `cloud.py`/`sync.py`) and the
traitprint-cloud sync hub (server side: hosted bare repo per user +
ingest into the Postgres projection).

Sync v1 moves vault sync from "POST the whole JSON document, last write
wins" to **git history as the wire format**. The unit of transfer is a
[git bundle](https://git-scm.com/docs/git-bundle) carried over plain
HTTPS — no smart-HTTP server required, so the hub can run behind
Supabase Edge Functions / Workers. The server holds one bare repo per
user; for cloud-only users that repo *is* their canonical vault (D1).

## Endpoints

All endpoints live under the authenticated API base
(`{api_url}/functions/v1`). Bundle bodies are raw bytes
(`Content-Type: application/octet-stream`). SHAs are full 40-hex commit
ids of the vault's default branch (`refs/heads/main`) — the only ref
the protocol syncs.

### `GET /vault-git/info`

Cheap state probe (powers `--dry-run` and status output).

```
200 → {
  "head": "<sha>" | null,            // null: repo exists but has no commits,
                                     // or no repo yet (first push pending)
  "ingest": {
    "status": "clean" | "pending" | "quarantined",
    "last_ingested_sha": "<sha>" | null,
    "quarantined": [                 // present when status == "quarantined"
      {"entity_id": "<uuid>", "file": "stories/foo.md",
       "reason": "dangling reference: skill_ids[1] does not resolve"}
    ]
  }
}
```

`pending` means commits are accepted but not yet projected (ingest is
asynchronous); `quarantined` means the projection is built but some
entities are flagged disputed (see Server behavior).

### `POST /vault-git/push`

Body: a git bundle. Headers:

| Header | Meaning |
|---|---|
| `X-Traitprint-Head` | client HEAD sha — the tip the bundle advances `main` to |

```
200 → {"head": "<sha>", "ingest": {…}}        // ref advanced; ingest report
401 → token missing/expired
409 → {"error": {…}, "server_head": "<sha>"}  // non-fast-forward; see Conflicts
422 → {"error": {…}, "violations": […]}       // schema violation; ref NOT advanced
```

The server verifies the bundle is self-contained against its repo (all
prerequisites present), that the bundle tip equals `X-Traitprint-Head`,
and that the update is a **fast-forward** of `main`. The server never
rewrites or merges history.

### `GET /vault-git/fetch?since=<sha>`

Returns commits the client is missing.

| Request | Response |
|---|---|
| `since` omitted | full bundle of `main` (clone case) |
| `since` == server HEAD | `204 No Content` — already up to date |
| `since` is an ancestor of HEAD | bundle of `<since>..main` |
| `since` unknown to the server | full bundle, `X-Traitprint-Bundle: full` |

`200` bodies are bundle bytes with `X-Traitprint-Head: <server sha>`.
The client applies a bundle with `git fetch <bundle> main` — bundles
are valid fetch remotes; nothing here requires a git server.

## Auth

Unchanged from the current transport: every request carries the public
Supabase `apikey` (anon JWT) header plus
`Authorization: Bearer <token>`, where the token is either a Supabase
access JWT (from `traitprint login`) or a `TRAITPRINT_API_TOKEN` API
key. The authenticated user resolves to exactly one hosted repo; there
is no cross-user access.

## Bundle semantics

- **First push** (server `head` is `null`): full bundle —
  `git bundle create out.bundle main`.
- **Every later push**: thin (incremental) bundle against the
  last-known server HEAD —
  `git bundle create out.bundle <server_head>..main` — so the wire cost
  is proportional to new commits, not vault size. The client learns
  `server_head` from `info`, from the previous push response, or from
  the last fetch.
- A bundle whose prerequisites the server does not have is rejected
  with `422` (`code: "missing_prerequisites"`); the client retries with
  a deeper basis or a full bundle.
- Tags and other refs are ignored; only `refs/heads/main` syncs.
  `.credentials` is gitignored and therefore never enters history or
  the wire (vault v1 contract).

## Server behavior

On an accepted push, in order:

1. **Apply** the bundle to the user's bare repo and fast-forward
   `main` (create repo + branch on first push).
2. **Validate** the tree at the new tip against the vault v1 contract
   ([`docs/schema/vault-v1/`](../vault-v1/)). Only the tip state is
   validated — intermediate commits may be inconsistent.
   - **Structural violations (Layer 0)** — malformed JSON/frontmatter,
     wrong types, bad enums/ranges, invalid UUIDs, missing required
     STAR headings — are **hard-rejected**: `422`, ref rolled back, repo
     left at the previous HEAD. The response lists every violation with
     `file`, `pointer` (JSON Pointer or frontmatter key), `message`,
     and `hint` — agents will read and act on these.
   - **Dangling UUID references (Layer 1)** are **accepted and
     quarantined** (D10): the push succeeds, the affected entities are
     projected with a `disputed` flag, and they appear in
     `ingest.quarantined` until a later commit resolves the link.
3. **Ingest** the new commits into the Postgres projection
   (`tp_vault`), **idempotently keyed by commit SHA**: the server
   records the last ingested SHA per user, replaying a push or
   re-ingesting the same SHA is a no-op, and a crash mid-ingest resumes
   from the recorded SHA. The projection is derived state — it can
   always be rebuilt from the repo.

## Conflict model

The server never merges. A push whose bundle does not fast-forward
`main` gets `409` with the current `server_head`. The client then:

1. `GET /vault-git/fetch?since=<local merge base>` and
   `git fetch` the bundle,
2. merges **locally with git** (standard merge/conflict UX, with CLI
   guidance — file-level granularity is the accepted model; no CRDTs),
3. pushes the merge commit as a new thin bundle.

Whole-vault LWW and its timestamp comparison are gone: concurrent edits
on different files merge cleanly, and real conflicts surface as git
conflicts in the affected files only.

## Commit-through (web edits)

Web-app edits and approved proposals are rendered to file-tree changes
and **committed server-side** to the user's repo (committer
`Traitprint Cloud <sync@traitprint.com>`); the projection is rebuilt
only from commits, never written around them. To sync clients those
commits are indistinguishable from any other remote history: they show
up in the next `fetch`, and an intervening web edit makes a stale push
non-fast-forward (`409`), which is exactly the conflict path above.

## Errors

Non-2xx responses with a body use one envelope:

```json
{"error": {"code": "non_fast_forward", "message": "…", "hint": "Run 'traitprint pull', resolve any conflicts, then push again."}}
```

`code` is stable and machine-matchable (`non_fast_forward`,
`schema_violation`, `missing_prerequisites`, `bundle_invalid`,
`auth_expired`, …); `hint` is the actionable next step.

## Versioning

The endpoint family (`/vault-git/*`) and this directory name
(`sync-v1`) are the version signal. Additive, backward-compatible
changes (new response fields, new error codes, new optional headers) do
not bump the version; anything that changes request/response meaning
ships as `sync-v2` under new paths, with `sync-v1` kept alive through a
deprecation window. The vault *content* is versioned independently by
`traitprint.json#schema_version` (vault v1 contract); the server
validates against the schema version each pushed tip declares.
