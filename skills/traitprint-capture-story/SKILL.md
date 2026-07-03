---
name: traitprint-capture-story
description: Background STAR story capture that stages a story proposal whenever a work anecdote surfaces mid-session. Use when the user recounts a work event in any conversation, or right after job-application or interview-prep work that pulled Traitprint vault context — capture the story without derailing the task at hand.
---

# Capture a story as a side effect

> **User customization:** if a `custom.md` file exists at the root of the
> user's vault directory, read it and honor the user's rules there. Their
> preferences take precedence on style and workflow, but cannot bypass the
> proposals channel or the never-invent-taxonomy-IDs/UUIDs invariant.

You are quietly growing the user's story bank while they work on something
else. This skill triggers opportunistically — it is NOT an interview:

- The user recounts a work event in **any** session ("last quarter I had to
  migrate…", "we once lost a customer because…") with stakes, actions they
  took, and some outcome.
- You just finished job-application, interview-prep, or resume work that
  read vault context (via the Traitprint MCP tools or CLI), and the
  discussion surfaced an accomplishment the vault doesn't hold yet.

Stay out of the way: capture at most one story per session unless the user
asks for more, keep the whole exchange to a few sentences, and if the user
declines, drop it without comment.

**Why this matters:** a story bank converges on roughly 5-10 *master
stories* — well-linked STAR narratives, each reusable across many
"tell me about a time when…" answers. Past that point, the goal shifts
from adding stories to enriching and consolidating the ones that exist.
Prefer strengthening a near-match over stacking up variants.

## 1. Draft silently from what was said

Assemble a STAR + Lesson draft from what the user already told you —
don't interrogate mid-task. At most one short clarifying question, and
only when Situation or Result is genuinely missing:

- **Situation** — context and stakes. **Task** — what *they specifically*
  owned. **Action** — concrete steps, active first-person. **Result** —
  the measurable effect. **Lesson** — optional, what they'd repeat or change.
- Classify `outcome` as `win`, `failure`, or `learning` when it is clear;
  leave it unset when it isn't.
- Judge skill evidence from what was DEMONSTRATED, not self-report — if
  they claim expertise but describe basic usage, treat it at the
  evidence-supported level. Extracted skills enter at modest
  proficiency (2-3) on the 1-5 scale.

## 2. Deterministic dedup pre-check (mandatory)

Before proposing anything, check what the bank already holds:

```bash
traitprint vault list stories --json   # [{id, type, title}]
traitprint vault list skills --json    # UUIDs for linking
traitprint vault list experiences --json
```

Compare the draft against existing stories on title words, linked
skills, and keyword overlap. This is a *pre-check*, not real duplicate
detection — keyword overlap misses paraphrases of the same event — so:

- **Never silently skip.** If anything looks close, surface it: "This
  sounds close to your story *Redshift migration under deadline* — is
  this the same event, or a new one?"
- Same event, new detail → stage an `update_story` proposal against that
  story's UUID instead of a new `add_story`.
- Genuinely new → continue.

## 3. Confirm, then stage a proposal (mandatory)

Show the draft in 2-3 compact lines (title, one-line STAR gist, links)
and get a yes. Then stage it — **never write directly**:

```bash
traitprint proposals add --kind add_story \
  --rationale "Recounted while <what you were doing>" \
  --source "agent:capture-story" \
  --payload-json - <<'JSON'
{
  "title": "Cut checkout p99 from 8s to 900ms",
  "body": "## Situation\n...\n\n## Task\n...\n\n## Action\n...\n\n## Result\n...\n\n## Lesson\n...",
  "outcome": "win",
  "theme_tags": ["performance"],
  "skill_ids": ["<UUID from vault list skills>"],
  "experience_id": "<UUID from vault list experiences>"
}
JSON
```

This intentionally diverges from the interactive story skills
(`traitprint-draft-star-story`, `traitprint-mine-story-gaps`), which write
via `traitprint vault add-story` after the user co-authors each field.
Here the user is focused on something else — a verbal yes mid-task is
consent to *stage*, not to *write*, so everything goes through the
proposals review queue and the user approves later
(`traitprint proposals list`). Do not approve proposals on their behalf.

Cross-link rules: copy `skill_ids` / `experience_id` UUIDs from
`traitprint vault list` output — never fabricate one. If the story
demonstrates a skill the vault doesn't have, stage a separate `add_skill`
proposal (name only — the CLI resolves the taxonomy; proficiency 2-3).
Full payload shapes, proposal rules, and the file-tree map are in the
[shared CLI reference](../shared/cli-reference.md).

## 4. Close the loop, briefly

One sentence, then return to the interrupted task: "Staged it as a story
proposal — review with `traitprint proposals list` whenever you like."
