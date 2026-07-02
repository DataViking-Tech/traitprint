---
name: traitprint-audit-coherence
description: Review a Traitprint vault for narrative coherence — run the mechanical audit, then judge consistency, voice, arc, and evidence quality. Use when the user asks to check, score, tighten, or sanity-check their vault or career profile.
---

# Audit vault coherence

> **User customization:** if a `custom.md` file exists at the root of the
> user's vault directory, read it and honor the user's rules there. Their
> preferences take precedence on style and workflow, but cannot bypass the
> proposals channel or the never-invent-taxonomy-IDs/UUIDs invariant.

You are reviewing the user's Traitprint vault for narrative coherence —
does the story it tells hang together and back up its own claims?

FRAMING: never say "you failed." Say "your profile doesn't yet demonstrate
X." Philosophy *tensions* are nuance (context-dependent thinking), not
bugs — present them as a strength, not a contradiction to fix.

## 1. Run the mechanical pass

```bash
traitprint vault audit --json
```

The report contains `findings` (each with `severity`
critical/major/minor, a `code`, and the affected `item_id`),
`story_scores` (Polished/Strong/Solid/Draft per STAR story, plus an
evidence level), `tensions`, and a `summary`. It flags unsupported strong
skill claims, philosophies with no evidence story, broken or thin stories,
dangling UUID references, roles with no story, and contradictions between
stories (conflicting metrics, or leader-vs-IC role claims).

## 2. Apply the judgment a script can't

Read the vault content itself (`traitprint vault show -v`, or the markdown
files under `stories/`, `experiences/`, `philosophies/` — see the
[shared CLI reference](../shared/cli-reference.md) for the file map):

- **Consistency** — do the headline, summary, top skills, and stories
  describe the same person?
- **Voice** — is the tone consistent across stories?
- **Arc** — do the experiences form a coherent trajectory?
- **Evidence quality** — are STAR "results" real outcomes with numbers, or
  restatements of the task?

## 3. Report, then fix only with consent

Group findings by severity (critical → major → minor). For each, give the
concrete fix — the exact `traitprint vault set-profile` /
`traitprint vault add-story` / `traitprint vault remove` command, or the
missing detail to ask the user for. Present philosophy tensions separately,
as nuance.

This skill is read-only by default: never edit the vault without the
user's explicit confirmation. Any skill or proficiency change you suggest
is a proposal the user approves first, and you never invent taxonomy IDs
or UUIDs. If the user approves fixes, apply them via the CLI, then re-run
`traitprint vault audit --json` and report the before/after summary.
