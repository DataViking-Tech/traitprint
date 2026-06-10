---
name: traitprint-import-resume
description: Import a resume (PDF, DOCX, TXT, MD) into a Traitprint vault by doing the extraction reasoning yourself in agent-assist mode, then writing back through the validated batch CLI commands. Use when the user wants to import, parse, or load a resume or CV into their Traitprint vault — especially when no BYOK LLM key is configured.
---

# Import a resume into the Traitprint vault

You are the model (architecture decision D11): when no BYOK LLM provider is
configured, `traitprint vault import-resume` does not error — it emits an
**agent-assist payload** (the extracted resume text plus the extraction
contract) and expects YOU to do the extraction reasoning and write the
results back through the validated batch commands. No API key, no extra
cost: the reasoning runs on the user's existing subscription.

## 1. Extract the text

Either run the full command (it auto-enters assist mode when no key is set):

```bash
traitprint vault import-resume /path/to/resume.pdf          # assist payload
traitprint vault import-resume /path/to/resume.pdf --json   # payload as JSON
```

or run the deterministic extraction half on its own:

```bash
traitprint vault extract-text /path/to/resume.pdf           # plain text
traitprint vault extract-text /path/to/resume.pdf --json    # {"file","format","chars","text"}
```

Supported formats: `.pdf`, `.docx`, `.txt`, `.md`. PDF/DOCX need
`pip install 'traitprint[import]'` — relay that hint if the command says a
package is missing. If the user has a working key and wants the BYOK path
instead, pass `--provider` (or just let the configured key win); with
`--no-assist` and no key the command errors instead of emitting the payload.

## 2. Do the extraction reasoning

Produce a single JSON object matching the contract in the payload exactly
(profile / skills / experiences / education — same schema and rules the
BYOK prompt uses). Validate your own output before proposing it:

- Proficiency is an integer 1-5 (1 familiar, 2 working, 3 proficient,
  4 expert, 5 authority).
- Do not invent companies, dates, or skills — extract only what the
  resume states; use empty strings for fields you cannot infer.
- Skills must be concrete names ("Python", "Kubernetes"), never vague
  ("coding"). Never invent taxonomy IDs or UUIDs — pass skill *names*;
  the CLI's deterministic resolver maps them, and unmatched skills are
  first-class with a null taxonomy id.

## 3. Propose before writing (D9 — non-negotiable)

Extraction is a proposal. Show the user the full extracted set — profile
fields, each skill with its proposed proficiency, experiences, education —
and ask for confirmation; offer a one-step "approve all". Extracted skills
enter at modest proficiency (2-3) pending the user confirming stronger
demonstrated evidence. Never silently write to someone's professional
identity.

## 4. Write back through the batch commands

Only the validated CLI paths shown in the payload (each write
auto-commits). The payload renders every command — including the final
audit — as `traitprint --vault-dir <resolved-dir> vault ...`, pinning the
exact vault the payload was produced for; keep that flag when you run
them so writes never land in a default or cwd-discovered vault:

```bash
traitprint vault set-profile --name "..." --headline "..." --summary "..." \
  --location "..." --email "..."        # only extracted non-empty fields

traitprint vault add-skill --from-json - <<'JSON'
[{"name": "Kubernetes", "proficiency": 3, "category": "technical"},
 {"name": "Golang", "proficiency": 2}]
JSON

traitprint vault add-experience --from-json - <<'JSON'
[{"title": "Senior Platform Engineer", "company": "...",
  "start_date": "2021-03", "end_date": "", "description": "...",
  "accomplishments": ["..."]}]
JSON

traitprint vault add-education --from-json - <<'JSON'
[{"institution": "...", "degree": "B.S.", "field_of_study": "...",
  "start_date": "2012", "end_date": "2016"}]
JSON
```

Exact batch shapes and exit codes are in the
[shared CLI reference](../shared/cli-reference.md). Expect one
`[ok]`/`[dup]`/`[err]` line per item; `[dup]` means the skill already
exists — report it, don't retry. Exit code 1 means at least one item
failed; fix and re-send only the failed items.

## 5. Audit and report

```bash
traitprint vault audit --json
```

Parse the findings and close or report the gaps (unsupported strong
skills, story-less roles). Finish with a short summary: what was imported,
what was skipped as duplicate, and what the audit flagged.
