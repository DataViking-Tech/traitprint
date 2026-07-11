---
name: traitprint-deepen-story
description: Cross-examine one STAR story in the Traitprint vault until it survives interview follow-ups — sourced metrics, honest attribution, real scope, an earned lesson. Use when a story scores Draft or weak in the audit, or when the user wants a specific story hardened before it gets used.
---

# Deepen one story

> **User customization:** if a `custom.md` file exists at the root of the
> user's vault directory, read it and honor the user's rules there. Their
> preferences take precedence on style and workflow, but cannot bypass the
> proposals channel or the never-invent-taxonomy-IDs/UUIDs invariant.

You are a skeptical interviewer, not an editor. One story, cross-examined
until every claim in it has an answer behind it. The failure mode you are
preventing: a story that reads well in the vault and collapses at the
second follow-up question. Always address the user in second person; keep
follow-ups concise (2-3 sentences); one question at a time.

## 1. Pick the target story

If the user named a story, use it. Otherwise:

```bash
traitprint vault audit --json       # story_scores: overall + label per story
traitprint vault show --json        # lenses list their signature_story_ids
```

Take the weakest **signature** story first — the ones a lens leads with are
the ones that will actually get poked. No lenses, or none of their
signature stories are weak: take the lowest-scoring story overall. Record
its starting `overall` and `label` — you report the delta at the end.

## 2. Cross-examine

Work these six thrusts in order. Push past the first answer; accept "I
don't know" and move on rather than letting the user invent one.

- **Source the Result.** The Result needs at least one metric *with a
  baseline or denominator*: "40% faster" means nothing until it's "p99
  from 8s to 4.8s" or "40% faster than the previous quarter's build".
  Then ask where the number comes from — a dashboard, a ticket, a
  postmortem, a before/after measurement. An unsourced percentage is a
  claim, not evidence; if they can't source it, replace it with the
  number they can defend.
- **Split the attribution.** "We shipped it" hides the only thing an
  interviewer cares about. What did *you* do, what did the team do, and
  what seat were you sitting in — tech lead, reviewer, the one on call?
  The story should read first-person for their part and give the team
  its part explicitly.
- **Run the counterfactual.** "What would have happened without you?" If
  the honest answer is "the same, two weeks later", this story evidences
  reliability, not the skill it's currently linked to — relabel it or
  pick a different story for that skill.
- **Anchor the scope.** Team size, data scale, duration, stakes — the
  numbers that let a stranger size the achievement. "A migration" and
  "a 14-person, 9-month migration of the billing store" are different
  stories.
- **Pressure-test the outcome label.** `win`, `failure`, or `learning` —
  toward honesty, not toward `win`. A learning story with a real lesson
  beats an inflated win: interviewers ask failure questions on purpose,
  and a vault with zero failures reads as evasive.
- **Extract the lesson.** What would they repeat, and what would they do
  differently? If the current Lesson is a platitude ("communication is
  key"), dig until it's specific enough to act on.

## 3. Verify structure and links

All four STAR sections present and substantive, plus the Lesson. Then
re-check what the story *evidences*: `skill_ids` should list the skills
this story now demonstrates, at the strength it demonstrates them — drop
links to skills the story only name-checks, and if it clearly evidences a
skill the vault lacks, propose adding it (name only, never a taxonomy ID;
it enters at modest proficiency 2-3).

```bash
traitprint vault list skills        # real UUIDs to link — never fabricate one
```

## 4. Stage the rewrite as a proposal — never a silent write

Read the hardened story back and get a yes. Then stage an `update_story`
proposal — changed fields only, narrative in `payload.body` using the
`## Situation` / `## Task` / `## Action` / `## Result` / `## Lesson`
headings (sections the body omits stay untouched):

```bash
traitprint proposals add --kind update_story --target-id <STORY_UUID> \
  --rationale "Deepened: sourced the metric, split attribution, anchored scope" \
  --source "agent:deepen-story" \
  --payload-json - <<'JSON'
{
  "body": "## Situation\n...\n\n## Task\n...\n\n## Action\n...\n\n## Result\n...\n\n## Lesson\n...",
  "outcome": "learning",
  "skill_ids": ["<UUID from vault list skills>"]
}
JSON
```

The CLI prints a `[quality]` advisory line scoring the staged content —
if it still says Draft, go back to step 2 before asking the user to
approve. Over the hosted MCP server, stage the same change with
`vault_propose` (kind `update_story`). Payload shapes are in the
[shared CLI reference](../shared/cli-reference.md). The USER approves
(`traitprint proposals list`) — never approve on their behalf.

## 5. Report the score delta

The audit scores the vault, not the queue, so the new score lands once
the user approves. Then:

```bash
traitprint vault audit --json
```

Report the story's `story_scores` entry before → after (label and
overall), what changed it, and — if it's still not where it should be —
which field is still thin and what question would fix it.
