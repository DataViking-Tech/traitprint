# Traitprint

Local-first career identity vault for the agent era.

> Your resume is a lossy snapshot. Your Traitprint is a live API.

🚧 **Coming soon.** See [COMING_SOON.md](COMING_SOON.md) for details.

## Local vs Cloud

Traitprint is local-first. Everything below runs on your laptop with no
account, no network calls, and no paywall.

| Capability | Free forever, no account | Requires traitprint.com account |
|---|---|---|
| Create + edit your vault (`init`, `vault add-*`, `remove`) | ✅ | — |
| Version history, diff, rollback | ✅ | — |
| MCP server for Claude Desktop / any MCP client (stdio) | ✅ | — |
| Resume import via BYOK LLM (Anthropic, OpenAI, Ollama, OpenRouter) | ✅ | — |
| SQL-queryable Dolt-backed storage | ✅ | — |
| MIT-licensed source, fork and self-host | ✅ | — |
| Public profile at `traitprint.com/profile/you` | — | ✅ |
| Hosted MCP endpoint reachable by recruiter agents | — | ✅ |
| Job matching against a shared job index | — | ✅ |
| Digital-twin chat | — | ✅ |
| Cross-device sync | — | ✅ |

A fresh install never talks to traitprint.com. Cloud features are opt-in via
`traitprint login` and `traitprint push`.

**Full details, privacy commitment, and migration guide:**
[docs/why-local.md](docs/why-local.md)

## License

[MIT](LICENSE)
