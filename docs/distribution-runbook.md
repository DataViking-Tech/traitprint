# Distribution Runbook — D7 Wave 1

**Owner:** wesley@dataviking.tech
**Status:** Artifacts landed (this repo); submissions are account-gated and
manual. Follow each section verbatim.
**Design:** [`agent-native-architecture.md`](agent-native-architecture.md)
§6 Phase 4 (D7). Cloud-side artifacts referenced below live in the
`traitprint-cloud` repo and are **not** modified from here.

The five wave-1 surfaces:

| # | Surface | What ships | Account needed |
|---|---|---|---|
| 1 | Claude connector directory | hosted remote MCP server | claude.ai (Wesley) |
| 2 | ChatGPT app directory | hosted remote MCP server | OpenAI Platform, verified developer (Wesley) |
| 3 | Official MCP Registry (registry.modelcontextprotocol.io) | hosted server + local PyPI package | DNS access to traitprint.com / GitHub org (Wesley) |
| 4 | Gemini CLI extension gallery | this repo (manifest at root) | GitHub repo admin (Wesley) |
| 5 | skills.sh | `skills/` in this repo | none |

Shared facts (verified 2026-06-10 against the live cloud implementation —
`traitprint-cloud/docs/specs/mcp-oauth.md` and
`supabase/functions/mcp-server/`):

- **Hosted MCP server URL:** `https://api.traitprint.com/functions/v1/mcp-server`
  (Streamable HTTP).
- **Auth:** OAuth 2.1 authorization-code + PKCE (S256 only), RFC 7591
  dynamic client registration, RFC 8707 resource indicators, RFC 9728
  discovery via `WWW-Authenticate: Bearer resource_metadata="…"` on 401
  and the path-appended `…/mcp-server/.well-known/oauth-protected-resource`.
  Root-level well-known URLs are NOT served (Supabase gateway limitation —
  documented deviation; compliant 2025-06-18+ clients don't need them).
  `sk_*` API keys remain a parallel headless auth path.
- **Tools (9):** `get_profile_summary`, `search_skills`, `find_story`,
  `get_philosophy` (read), `vault_propose`, `vault_list_proposals`
  (staged writes, D2/D9), `job_get`, `jobs_match`, `resume_tailor` (jobs).
  All carry `readOnlyHint`/`destructiveHint` annotations.
- **Privacy policy URL (public, no login):** `https://traitprint.com/privacy-public`
- **MCP docs page (public):** `https://traitprint.com/mcp`
- **Support contact:** `hello@traitprint.com` (privacy: `privacy@traitprint.com`)

> **Known gap before directory submissions:** the hosted tool definitions
> have descriptions and annotations but **no `title` field**
> (`ToolDef` in `traitprint-cloud/supabase/functions/mcp-server/shared.ts`).
> Claude's review checks that every tool has a human-readable title;
> missing annotations/titles are a top rejection cause. File a cloud-side
> issue and land titles before submitting sections 1 and 2.

---

## 1. Claude connector directory

Listing in the directory that claude.ai users browse under
Settings → Connectors. Sources: `claude.com/docs/connectors/building/submission`,
support.claude.com articles 11503834 / 11596036 (fetched 2026-06-10).

**Already done (cloud side):**
- Remote MCP server live with OAuth 2.1 + PKCE S256 and DCR — exactly the
  required auth shape (client_credentials-only flows are rejected; ours is
  user-consent based, correct).
- Read/write tool annotations.
- Public privacy policy and docs pages.

**Wesley must:**

1. **Pre-flight (no account needed):** verify the connector works as a
   *custom* connector first — claude.ai → Settings → Connectors → Add
   custom connector → paste
   `https://api.traitprint.com/functions/v1/mcp-server`. Complete the OAuth
   consent, then in a chat confirm `get_profile_summary` and
   `vault_propose` round-trip. Directory reviewers test exactly this path.
2. **Create a reviewer test account** on traitprint.com with a seeded,
   realistic vault (profile + ~10 skills + 2-3 stories + 1 philosophy) and
   at least one pending proposal so reviewers can see the staged-write UX.
   Keep credentials ready for the form.
3. **Prepare assets:**
   - Icon: square PNG (have a high-res master ready; the form states exact
     dimensions at upload time).
   - Name (≤100 chars), tagline (≤55 chars), description (≤2000 chars) —
     ready-to-paste payloads below.
4. **Submit** from the in-app submission portal on claude.ai (the
   docs route is Build connectors → "Submitting to the Connectors
   Directory", `https://claude.com/docs/connectors/building/submission`).
   Fill: server name, tagline, description, 1-5 categories, documentation
   URL, privacy policy URL, support contact, icon, listing URL slug,
   company name + website, primary review contact, test account.
5. **Expect review feedback** on: tool titles (see Known gap above), and
   possibly RFC 7009 `/revoke` / RFC 7662 `/introspect` endpoints — these
   were consciously deferred (`mcp-oauth.md` §5, "Add if a directory
   review asks for them"). If asked, file the cloud issue and resubmit.

**Ready-to-paste payload:**

```
Name:        Traitprint
Tagline:     Your portable career vault, queryable by AI agents
Slug:        traitprint
Categories:  Productivity (primary); Career / Professional if offered
Connector URL:      https://api.traitprint.com/functions/v1/mcp-server
Documentation URL:  https://traitprint.com/mcp
Privacy policy URL: https://traitprint.com/privacy-public
Support contact:    hello@traitprint.com
Company:            DataViking Tech — https://traitprint.com
Auth: OAuth 2.1 + PKCE (S256), dynamic client registration; per-user
      consent with scoped grants (6 scopes), revocable from Settings.
```

```
Description (≤2000 chars):
Traitprint is a structured, user-owned career profile — skills with a
1-5 proficiency scale, work experiences, STAR-format stories,
philosophies, and education, cross-linked so every claim is backed by
evidence. This connector lets Claude query a user's Traitprint vault
and help build it.

Read tools answer questions like "what do they know about Postgres?"
(search_skills), "tell me about a time when…" (find_story), and "what's
their stance on code review?" (get_philosophy), plus a one-shot identity
primer (get_profile_summary). Job tools (job_get, jobs_match,
resume_tailor) rank live job matches from the Traitprint job index and
tailor resumes to a posting.

Writes are never direct: vault_propose stages a change as a proposal the
user reviews and approves — from the web app, the local CLI, or any
agent surface — before anything enters their professional identity.

Traitprint is local-first: the same vault works fully offline with the
MIT-licensed CLI and stdio MCP server (pip install traitprint), and the
hosted server adds sync, a public profile, and job matching. Users grant
scoped, revocable access at consent; data stays theirs.
```

---

## 2. ChatGPT app directory

Sources: `developers.openai.com/apps-sdk/deploy/submission`,
`/apps-sdk/app-submission-guidelines`, help.openai.com article 20001040
(fetched 2026-06-10).

**Already done (cloud side):**
- Streamable HTTP MCP server with OAuth 2.1 (ChatGPT's required shape).
- `readOnlyHint` annotations (ChatGPT requires them — noted in
  `shared.ts`).
- Public privacy policy page.

**Wesley must:**

1. **Complete identity verification** in the OpenAI Platform Dashboard
   (platform.openai.com) for the publishing name — *business verification*
   to publish as "DataViking Tech" (individual verification publishes
   under the personal name). This is a hard prerequisite.
2. **Pre-flight in developer mode:** ChatGPT → Settings → Apps &
   Connectors → Advanced settings → enable Developer mode; then
   Settings → Connectors → Create, paste
   `https://api.traitprint.com/functions/v1/mcp-server`, complete OAuth,
   and exercise every tool. Apps that crash/hang in review are rejected.
3. **Prepare assets:** logo PNG; screenshots of representative tool
   responses; 3-5 test prompts with expected responses (reviewers run
   them); name + description (reuse the Claude payload above, same
   limits apply in spirit); country availability list.
4. **Submit** from the Platform Dashboard app-submission form: app name,
   logo, description, company URL, privacy policy URL
   (`https://traitprint.com/privacy-public`), MCP server URL (must be the
   real live endpoint — no placeholders), tool information, screenshots,
   test prompts/responses, localization/country availability; check every
   confirmation box (App Developer Terms, submission guidelines).
5. **Track review** in the Dashboard; status changes also arrive by
   email. Expect the same tool-title feedback as Claude (Known gap).

**Test prompts to include (verified against the live tool surface):**

```
1. "What does this candidate know about distributed systems?"
   → search_skills returns ranked skills with proficiency + evidence.
2. "Tell me about a time they handled a production incident."
   → find_story returns a STAR narrative.
3. "What's their philosophy on code review?"  → get_philosophy.
4. "Add 'Terraform' to my skills."
   → vault_propose stages a proposal; the reply tells the user to
     approve it (never a silent write).
5. "Which of my job matches fit a staff engineer role?" → jobs_match.
```

---

## 3. Official MCP Registry (registry.modelcontextprotocol.io)

Two distinct listings; do both. Source: `modelcontextprotocol/registry`
publishing guide (fetched 2026-06-10).

### 3a. Local stdio server (PyPI package) — `io.github.dataviking-tech/traitprint`

The submission artifact already exists cloud-side:
`traitprint-cloud/docs/mcp-registry-submission/` (`server.json`,
`smithery.yaml`, and its own click-through runbook for Smithery/Glama).
**Note:** that `server.json` is pinned to `0.2.0`; bump its two `version`
fields to the current release before publishing (cloud-side edit, or edit
the copy at publish time).

```bash
# One-time
npm install -g @modelcontextprotocol/mcp-publisher  # or: brew install mcp-publisher

# GitHub-namespace auth — needs a browser session that can act for the
# DataViking-Tech org (no DNS required for io.github.* namespaces)
mcp-publisher login github

# From a checkout containing the (version-bumped) server.json:
mcp-publisher publish
```

### 3b. Hosted remote server — `com.traitprint/mcp` (Wesley: DNS step)

Domain namespaces require proof of domain ownership. The hosted server URL
(`api.traitprint.com`) is on `traitprint.com`, so DNS verification of
`traitprint.com` covers it.

1. Generate a keypair and the TXT record value:
   ```bash
   openssl genpkey -algorithm Ed25519 -out traitprint-mcp-registry.pem
   echo "v=MCPv1; k=ed25519; p=$(openssl pkey -in traitprint-mcp-registry.pem -pubout -outform DER | tail -c 32 | base64)"
   ```
2. Add the printed value as a TXT record on `traitprint.com` (apex).
   Store the `.pem` in Doppler — it is the long-lived publishing key.
   (Alternative without DNS: serve the same string at
   `https://traitprint.com/.well-known/mcp-registry-auth` and use
   `mcp-publisher login http`.)
3. Author a `server.json` for the hosted server (natural home:
   `traitprint-cloud/docs/mcp-registry-submission/server-hosted.json` —
   cloud-side follow-up). Shape (same `$schema` as the existing
   `server.json`):
   ```json
   {
     "name": "com.traitprint/mcp",
     "title": "Traitprint (hosted)",
     "version": "1.0.0",
     "description": "Hosted career-vault MCP: skills, STAR stories, staged writes, job matching.",
     "websiteUrl": "https://traitprint.com",
     "remotes": [
       { "type": "streamable-http", "url": "https://api.traitprint.com/functions/v1/mcp-server" }
     ]
   }
   ```
   The registry schema (2025-07-09) requires `version` (semver — bump on
   meaningful server changes) and caps `description` at 100 characters.
4. Publish:
   ```bash
   mcp-publisher login dns   # uses the .pem from step 1
   mcp-publisher publish     # from the directory holding the hosted server.json
   ```
5. Verify: `https://registry.modelcontextprotocol.io/v0/servers/com.traitprint/mcp`.
   Smithery and Glama mirror the official registry, so this also seeds
   their listings.

---

## 4. Gemini CLI extension gallery

Source: `google-gemini/gemini-cli` docs — `extensions/reference.md`,
`extensions/writing-extensions.md`, `extensions/releasing.md` (fetched
2026-06-10).

**Already done (this repo):**
- `gemini-extension.json` at the **repo root** — the installer and the
  gallery crawler both require it in "the absolute root of the
  repository", which is why it is not under a `gemini-extension/`
  subdirectory.
- `GEMINI.md` context file at the root (wired via `contextFileName`).
- The six skills under `skills/<name>/SKILL.md` — Gemini CLI
  auto-discovers extension skills from exactly this layout; no manifest
  entry needed. (`skills/shared/` has no SKILL.md, so it is bundled as
  plain supporting files, not a skill.)
- OAuth: the manifest deliberately has **no** `oauth` block —
  Gemini CLI's automatic OAuth discovery handles servers that advertise
  RFC 9728 metadata, which ours does. sk_ keys remain a manual fallback
  (user adds a `headers` override in their own `settings.json`).

**Install (works as soon as this lands on `main` — no account, no review):**

```bash
gemini extensions install https://github.com/DataViking-Tech/traitprint
```

**Wesley must (for the gallery listing at geminicli.com/extensions):**

1. Add the **`gemini-cli-extension` topic** to the GitHub repo (About →
   topics). The gallery crawler discovers extensions by this topic.
2. Ensure the repo has a **git tag** (the crawler indexes tagged repos;
   the next `vX.Y.Z` release tag suffices — `gemini-extension.json`
   version is kept in lockstep with `pyproject.toml` by
   `tests/test_distribution.py` and the release runbook).
3. Wait for the daily crawl; verify the listing on
   geminicli.com/extensions. No form, no email — discovery is automatic.

**Maintenance:** keep the manifest `version` bumped with releases
(release runbook §1 step 2); users on git installs update with
`gemini extensions update traitprint`.

---

## 5. skills.sh

Source: `vercel-labs/skills` README (fetched 2026-06-10).

**Verified requirements — already satisfied, no new artifacts needed:**
- The installer scans `skills/` one level deep for the flat layout
  `skills/<name>/SKILL.md` — exactly this repo's layout.
- Each SKILL.md needs frontmatter `name` + `description` — enforced by
  `tests/test_skills.py`.
- There is **no registry submission flow**: GitHub is the registry.
  A repo appears on skills.sh automatically via install telemetry once
  people run `npx skills add DataViking-Tech/traitprint`.

**Wesley must (after merge to `main`):**

1. Smoke-test the public install path in a scratch directory:
   ```bash
   npx skills add DataViking-Tech/traitprint --list   # shows the 6 skills
   npx skills add DataViking-Tech/traitprint          # installs them
   ```
2. **Check the `../shared/cli-reference.md` link in the installed copy.**
   Every skill references the shared cheatsheet by relative path; the
   skills CLI copies skill folders individually, so the link may dangle
   in the installed location (the skills degrade gracefully — the CLI
   `--help` and `AGENTS.md` cover the same ground). If it dangles, file
   an issue to inline a trimmed reference per skill or ship `shared/` as
   per-skill supporting files. Do not restructure preemptively.
3. Optionally announce the install one-liner (README already carries it);
   the skills.sh listing populates from real installs.

---

## Order of operations

1. Merge this branch → `main` (skills.sh + Gemini git-install become live
   immediately).
2. Repo topic + tag → Gemini gallery (§4).
3. `mcp-publisher` for both registry listings (§3) — no review gate.
4. Cloud-side: add tool `title`s, bump registry `server.json` version,
   then submit Claude (§1) and ChatGPT (§2) — both have human review
   loops; run them in parallel.
