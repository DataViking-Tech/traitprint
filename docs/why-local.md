# Why Local vs Cloud

Traitprint is **local-first**. Your vault lives on your machine as a versioned
file — not in our database. This page explains what you get for free, what
requires an account, and why the split is drawn where it is.

## What you get, free forever, no account

Everything below runs entirely on your laptop. No signup, no network calls, no
telemetry, no credit card.

| Capability | Command |
|---|---|
| Create a vault | `traitprint init` |
| Add skills, stories, philosophy, experience, education | `traitprint vault add-*` |
| Browse your vault | `traitprint vault show`, `vault list` |
| Version history + diffs + rollback | `traitprint vault history`, `vault diff`, `vault rollback` |
| MCP server for Claude Desktop / any MCP client | `traitprint mcp-serve` |
| Resume import via BYOK LLM (Anthropic, OpenAI, Ollama, OpenRouter) | `traitprint import resume` |
| SQL-queryable Dolt-backed storage | via vault directory |
| MIT source, fork it, run it forever | — |

**Free forever** is a commitment, not a pricing tier. These capabilities will
never move behind an account, a paywall, or a network dependency. If we ever
break that promise, your existing vault keeps working because it's just files
on your disk under an MIT license.

## What requires a (free) account on traitprint.com

The cloud side is **opt-in** and exists for things that fundamentally can't run
locally — they need a public URL, a shared index, or a server someone else's
agent can reach.

| Capability | Why it needs cloud |
|---|---|
| Public profile at `traitprint.com/profile/you` | Needs a URL recruiters/agents can GET |
| Hosted MCP endpoint for recruiter agents | Needs a server reachable over the internet |
| Job matching against a shared job index | Needs a shared index across users |
| Digital-twin chat ("talk to my Traitprint") | Needs hosted inference + rate limiting |
| Cross-device sync | Needs a server to sync through |

You reach cloud features by running `traitprint login` and then
`traitprint push`. Until you do, no byte of your vault leaves your machine.

## The privacy commitment

1. **Local by default.** A fresh install does not talk to traitprint.com. Ever.
   No pings, no analytics, no update checks on the vault path.
2. **You push, we don't pull.** Cloud sync is always initiated from your CLI.
   We never reach into your vault.
3. **You choose what ships.** `traitprint push` takes flags for what to publish.
   Private philosophy, notes, and rough-draft stories stay local unless you
   mark them public.
4. **BYOK for LLMs.** Resume import uses your API key against your chosen
   provider. We don't proxy, log, or see your prompts.
5. **MIT license, forever.** The local binary you install today will keep
   working even if traitprint.com goes away tomorrow. Your vault is a git
   repo — portable, inspectable, yours.
6. **Delete means delete.** `traitprint logout --purge` removes your cloud
   profile and rotates your API token. Your local vault is untouched.

## Migration guide

### Local-only → Cloud-enabled

You've been using `traitprint` locally and now want a public profile or a
hosted MCP endpoint.

```bash
# 1. Create a free account (opens browser)
traitprint login

# 2. Review what will be published (dry run)
traitprint push --dry-run

# 3. Push. By default only items marked `public: true` are sent.
traitprint push

# 4. Your profile is live:
#    https://traitprint.com/profile/<your-handle>
```

Your local vault is unchanged. Cloud is a mirror of the subset you publish, not
a replacement.

### Cloud-enabled → Local-only

You want to go back to a fully local setup.

```bash
# 1. Pull anything from cloud you don't already have locally
traitprint pull

# 2. Remove the cloud mirror and rotate your token
traitprint logout --purge

# 3. (Optional) Confirm nothing in your vault references the cloud
traitprint vault show
```

Your local vault keeps working. Every MCP client, CLI command, and vault file
behaves identically to before — because none of them ever depended on cloud.

### Exporting off Traitprint entirely

Your vault directory is a plain git repository with JSON inside. To leave:

```bash
cp -r ~/.traitprint /path/to/your/backup
```

That's it. No export API, no lock-in window. The file format is documented in
`src/traitprint/schema.py` and will remain readable by any MIT-licensed fork.

## The design rule

> A feature belongs in local unless it **can't** work locally.

If something only needs your data and your machine, it runs offline and free.
Cloud is reserved for the small set of features that genuinely require a
server — public URLs, shared indexes, hosted inference. That split is the
product, not a pricing funnel.
