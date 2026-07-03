---
name: traitprint-position-lens
description: Curate a positioning lens — a named, non-destructive projection that re-orders, re-weights, and (optionally) re-headlines the one vault for a specific audience, without ever inventing a fact. Use when the user wants their profile to read differently for a target role or archetype (e.g. "lead with my platform work", "downplay the front-end") while keeping a single source of truth.
---

# Curate a positioning lens

> **User customization:** if a `custom.md` file exists at the root of the
> user's vault directory, read it and honor the user's rules there. Their
> preferences take precedence on style and workflow, but cannot bypass the
> proposals channel, the 5-lens cap, or the never-assert-a-fact-absent-from-
> the-vault invariant.

A **positioning lens** is a named projection over the one vault: it selects,
orders, and re-weights existing content — plus optional headline/bio
overrides — so the same grounded facts read differently for a specific
audience. A lens is emphasis, never invention. It cannot add a skill, a
role, or a story that is not already in the vault; the trust layer surfaces
any lens that references a since-deleted entity as `disputed`. The full
contract is `docs/schema/lens-v1/`; the CLI + payload tables are in the
[shared CLI reference](../shared/cli-reference.md).

## 1. Decide whether a lens is warranted

A lens is a curation object, not bulk data. Reach for one only when the same
vault genuinely needs to present differently for different audiences —
"Platform Lead" vs. "IC Track", "Manager" vs. "Staff Engineer". If the goal
is really to *add* a missing skill or story, that is a vault edit
(a proposal), not a lens. A vault holds **at most 5 lenses** — keep the set
small enough to reason about; if the user is at the cap, edit or remove an
existing lens rather than accreting near-duplicates.

Read what already exists before proposing a new one — never re-create a lens
the vault already carries:

```bash
traitprint vault show                # the profile, sections, and any lenses
traitprint vault list skills         # UUIDs you'll need for --salience
```

## 2. Author the lens (local: direct CLI · cloud: staged proposal)

**Locally**, lenses are config-like and non-factual, so the CLI edits them
directly — every write auto-commits to vault git history:

```bash
traitprint vault lens add --slug platform-lead --name "Platform Lead" \
  --target-archetype "Data Platform Lead" \
  --headline-override "Data Platform Lead" \
  --signature-experience <EXPERIENCE_UUID> \
  --salience <SKILL_UUID>=core --salience <SKILL_UUID>=suppressed
traitprint vault lens update platform-lead --bio-override "..."   # edit in place
traitprint vault lens set-default platform-lead                   # the bare profile renders it
traitprint vault lens remove platform-lead -y                     # delete
```

**Over the hosted MCP server** there is no direct lens write: stage the
change with `vault_propose` (kinds `add_lens` / `update_lens`), exactly like
every other cloud vault edit. The user approves it in the Traitprint web app
before it becomes a commit — never approve on their behalf. Copy real UUIDs
for `signature_experience_ids` / `skill_salience` from `search_skills` /
`find_experience` results; never fabricate a UUID or a taxonomy id.

## 3. Salience discipline (core / supporting / suppressed)

Per-skill emphasis has three levels, and unspecified skills stay
`supporting` (the neutral default) — so you only ever name the exceptions:

- **core** — foreground it: it leads the rendered skill list and is boosted
  in job scoring. Reserve this for the two or three skills that *are* the
  positioning. Marking everything core positions nothing.
- **supporting** — the default; normal order and weight. Leave a skill
  unlisted to keep it here.
- **suppressed** — hide it from the rendered profile and drop it from the
  lensed skill set. Use it to mute genuinely off-target skills, not to
  erase history — the fact stays in the vault, this lens just doesn't lead
  with it.

A lens re-weights; it never *rates*. Proficiency lives on the skill, not the
lens.

## 4. Override rules — never assert a fact absent from the vault

`--headline-override` and `--bio-override` are the one free-text surface, so
they carry the one real risk. They may **re-frame** what the vault already
supports; they may **not** introduce a claim the vault can't back — a title
never held, a metric never recorded, a skill not present. If the desired
framing needs a fact the vault lacks, the fix is to add that fact
(as a proposal the user approves), then let the lens surface it. Signature
experiences and stories must reference entities that actually exist; a
dangling reference is a dispute, not a feature.

## 5. Verify and repair disputes

After authoring, confirm the projection reads as intended and carries no
dangling references:

```bash
traitprint vault audit --json        # lens references to deleted entities surface here
traitprint vault export -f career-bundle -o ./out --lens platform-lead
```

If the audit (or the `disputed` flag on the read tools) flags the lens,
repair it by pointing the signature/salience reference at a live UUID or
dropping the stale one — a lens with dangling refs still renders, but it
advertises a broken projection. Finish with a short report: which lens you
created or edited, what it foregrounds and suppresses, whether it is the
default, and anything the audit still flags.
