# Security Policy

## Supported versions

Security fixes land on `main` and ship in the next release to PyPI. Only the
latest released version of `traitprint` is supported — the package has no
LTS branches.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

- Preferred: [GitHub private vulnerability reporting](https://github.com/DataViking-Tech/traitprint/security/advisories/new)
  on this repository.
- Or email **hello@traitprint.com** with `[SECURITY]` in the subject.

You'll get an acknowledgment within a few business days. Please include a
reproduction against a scratch vault where possible.

## Scope notes — what "vulnerability" means here

Traitprint Local's core promise is that the base install makes **no network
calls**: a fresh `pip install traitprint` never imports a network client at
module load, and only the optional `[cloud]` / `[import]` extras can reach
the network (see [docs/privacy.md](docs/privacy.md) for the threat model).
Reports we especially want:

- Any way the **base install** (no extras) triggers a network request.
- Vault content leaking outside the vault directory (temp files, logs,
  crash reports) or crossing into MCP responses it shouldn't.
- The proposals channel being bypassed — a path where an agent-driven write
  lands in the vault without staging/approval where staging is contracted.
- Credential handling issues (`.credentials`, `TRAITPRINT_API_TOKEN`,
  BYOK keys touching disk or process lists unexpectedly).
- Malicious vault files (hand-edited JSON/markdown/frontmatter) achieving
  code execution or path traversal when read by the CLI or MCP server.

The hosted product lives at
[DataViking-Tech/traitprint-cloud](https://github.com/DataViking-Tech/traitprint-cloud)
— report server-side issues there (privately, same rules).
