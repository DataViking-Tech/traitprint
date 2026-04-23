# Privacy

This document describes, in concrete terms, what Traitprint does and does not
do with your data. It is written for someone who will read the source before
trusting it. Every claim below corresponds to specific code in this
repository; file and function references are provided so you can verify.

Traitprint is MIT-licensed. If anything here drifts from the code, the code
wins — please open an issue.

## The shape of the system

There are two pieces:

- **Local vault** — a directory on your machine (default `~/.traitprint`)
  containing a `vault.json` file plus a git-backed history. Created by
  `traitprint init`. Nothing here depends on a network.
- **Cloud sync** (opt-in) — a single HTTP edge function at
  `https://traitprint.com/vault-sync` that stores one vault snapshot per
  account. Reached only when you run `traitprint login`, `push`, or `pull`.

A fresh install has no credentials and never contacts the cloud. See
`src/traitprint/cli.py` — no command except `login`, `push`, `pull`, and
`logout` constructs a `CloudClient`.

## 1. What leaves your machine when you push

`traitprint push` sends exactly one HTTP request:

```
POST https://traitprint.com/vault-sync
Authorization: Bearer <token from traitprint login>
Content-Type: application/json

{"vault": <your entire VaultSchema as JSON>}
```

The payload is the full `VaultSchema` defined in `src/traitprint/schema.py`:

- `profile` (name, headline, location, links)
- `skills`, `experiences`, `stories`, `philosophies`, `education`
- `updated_at` timestamp and `schema_version`

That is the complete list. There is no companion request, no telemetry ping,
no device fingerprint, no analytics beacon. You can verify by reading
`CloudClient.push` in `src/traitprint/cloud.py` and
`do_push` in `src/traitprint/sync.py`, or by running any HTTP proxy against
`traitprint push`.

**Today, `push` uploads the whole vault.** There is currently no
per-item public/private flag in the schema; if you do not want something on
the server, do not add it to the vault, or remove it with
`traitprint vault remove` before pushing. A selective-publish model is a
planned feature, not a shipped one — we would rather tell you that than
imply otherwise.

Your bearer token lives at `<vault>/.credentials` (mode `0600`) — see
`src/traitprint/credentials.py`. It never leaves your machine except as the
`Authorization` header on requests to the API URL you logged into.

## 2. What traitprint.com stores

When the server accepts a push, it persists:

- Your **account record**: email, a password hash, and an API token.
- Your **vault snapshot**: the most recent JSON body you sent, and the
  `updated_at` timestamp used for last-write-wins conflict detection.
- **HTTP request metadata** that any web service necessarily sees: source
  IP, timestamp, and user-agent, retained in standard access logs.

One vault snapshot per account. `push` overwrites the previous snapshot;
there is no server-side version history of your vault. (Your *local* vault
has full history via git — the server does not.)

## 3. What traitprint.com does not do

These are commitments, not aspirations. If we ever change them we will ship
a version bump and note it here.

- **No analytics or modeling on vault contents.** Your `stories`,
  `philosophies`, `skills`, and other vault fields are not parsed, mined,
  indexed, classified, embedded, or fed into any analytics pipeline on our
  side for purposes other than serving them back to you and to clients you
  explicitly authorize (see §4).
- **No selling, renting, or sharing with third parties.** Not to ad
  networks, not to data brokers, not to "partners." The vault exists to be
  returned to you and to agents you authorize.
- **No recruiter access without explicit per-query consent.** A recruiter
  or their agent cannot read your vault by knowing your email or username.
  Access to non-public fields requires an authenticated request you approve.
  The public profile at `traitprint.com/profile/<handle>` exposes only the
  subset you mark as public; everything else requires an access grant
  scoped to the querying party and, where configured, to the query.
- **No training on your data.** Your vault is not used as training data
  for any model — ours, a vendor's, or otherwise.

## 4. How the digital-twin chat works

The digital-twin chat (`traitprint.com/profile/<handle>/chat`) is a
retrieval system, not a fine-tune.

- Each incoming question is used to **retrieve** passages from *your* vault
  snapshot only. No other user's data is in the retrieval set.
- Retrieved passages plus the question are sent to a hosted LLM. The
  response is returned to the asker and not stored beyond the rate-limit
  and abuse-prevention window described below.
- Your vault is **not used as training data** for the underlying model or
  any fine-tune. Inference providers are bound by contract to the same.
- We log, per request: timestamp, a truncated hash of the question, the
  IP class of the asker, and whether the call succeeded — for rate
  limiting and abuse prevention. We do not log question text, response
  text, or which passages were retrieved, beyond what's needed to debug a
  specific incident you or we report.
- Rate-limit and abuse logs are retained for **30 days** and then deleted.

If the digital-twin chat is disabled on your profile, none of the above
runs — there is no retrieval, no inference call, and nothing to log.

## 5. How to delete everything

**Account deletion = vault deletion.** There is no retention period, no
"anonymized copy kept for analytics," no archive.

```bash
# Remove the server-side vault, profile, and rotate the token:
traitprint logout --purge

# Your local vault is untouched. To remove it too:
rm -rf ~/.traitprint
```

What happens on `logout --purge`:

1. The server deletes your vault snapshot.
2. The server deletes your account record and revokes the bearer token.
3. Any public profile at `traitprint.com/profile/<handle>` returns 404
   within the cache TTL (≤ 60 seconds).
4. Your local `<vault>/.credentials` file is removed.

Standard HTTP access logs (IP + timestamp + path) are retained for 30
days for abuse prevention and then deleted; they do not contain vault
contents.

If you lose access to the account and can't run `logout --purge`, email
`privacy@traitprint.com` from the registered address and we will purge
the server side for you.

## The threat model we are and aren't defending against

We think it's useful to be explicit about this.

**In scope:**

- A fresh install making no network calls. (Tested: grep
  `src/traitprint/` for `httpx` — only `cloud.py` and `providers/` import
  it, and `providers` is only reached by BYOK resume import when you
  invoke it.)
- Minimizing what the cloud sees (one endpoint, one payload, no
  telemetry) so you can reason about exposure.
- Giving you a path off the service at any time (MIT-licensed local
  binary, plain-JSON vault, one-command account purge).

**Out of scope:**

- Protecting against a malicious or compromised machine you're running
  the CLI on. Your vault and bearer token are files on your disk; normal
  filesystem permissions apply.
- Protecting against a malicious MCP client you point at
  `traitprint mcp-serve`. That client gets whatever the MCP tools expose.
- Protecting against compelled disclosure. If a lawful order demands your
  server-side vault, we will produce it; we keep the server-side surface
  small (one snapshot, no history) partly so that set is bounded.

## BYOK LLMs (resume import)

`traitprint vault import-resume` sends your resume text to the LLM provider
*you* configure (Anthropic, OpenAI, Ollama, OpenRouter) using *your* API
key. The request goes directly from your machine to that provider. We do
not proxy it, see it, or log it. See `src/traitprint/providers/`.

## Verifying the claims

The authoritative list of network calls made by the CLI is:

- `traitprint login` → `POST /auth/login` (your email + password)
- `traitprint push` → `GET /vault-sync` then `POST /vault-sync`
  (the LWW check, then the upload)
- `traitprint pull` → `GET /vault-sync`
- `traitprint vault import-resume` → whichever URL your BYOK provider uses,
  with your key

That is the whole set. You can confirm by:

```bash
# Proxy the CLI and watch the requests:
HTTPS_PROXY=http://localhost:8080 traitprint push

# Or just read the code — it's ~220 lines:
less src/traitprint/cloud.py
```

---

Questions, disagreements, or anything in this doc that doesn't match the
code: please open an issue at
<https://github.com/DataViking-Tech/traitprint/issues>. This page is part
of the product.
