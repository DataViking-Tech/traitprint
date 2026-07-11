---
name: traitprint-improve-profile
description: Vault-wide triage that finds the highest-leverage improvement to a Traitprint profile and routes it to the right workflow. Use when the user asks what to fix next, wants the biggest win for the time they have, or when an audit reports more findings than anyone wants to read.
---

# Improve the profile

> **User customization:** if a `custom.md` file exists at the root of the
> user's vault directory, read it and honor the user's rules there. Their
> preferences take precedence on style and workflow, but cannot bypass the
> proposals channel or the never-invent-taxonomy-IDs/UUIDs invariant.

You are running triage across the whole vault. The audit reports
everything; your job is to rank. Surface the one change with the highest
payoff — or the top three when the choice genuinely depends on the user's
goals — and route each to the workflow that does it well. Do not dump the
full findings list on the user; they can run the audit themselves.

## 1. Gather state

```bash
traitprint doctor --json            # vault phase + freshness findings
traitprint vault audit --json       # findings, story_scores, tensions, summary
traitprint vault show --json        # the full document: basics, links, lenses
```

Hosted agents without a shell: `doctor` is local-only — use
`get_profile_summary` with depth="detailed" instead (top skills, signature
experiences, and the vault-wide `disputes` roll-up), plus `jobs_match`;
skills recurring in its `missing_skills` are market signal, not vault
fact.

## 2. Rank by leverage

Apply this order exactly — it is the heuristic, not a suggestion, so that
any agent running this skill ranks the same vault the same way:

1. **Trust-layer disputes and dangling references.** A profile that
   contradicts itself undermines everything else in it. Repair the
   reference or drop it before polishing anything.
2. **Expert/authority skills (proficiency 4-5) with zero evidencing
   stories.** The biggest credibility gap: the strongest claims are the
   ones with nothing behind them (`skill.unsupported_strength` findings).
3. **Draft or weak scores on signature stories.** The stories a lens
   leads with are the ones that get poked — route to
   `traitprint-deepen-story`.
4. **Experiences with no stories or no skill links.** Roles contributing
   nothing to the narrative (`experience.no_story`,
   `experience.no_skills`) — route to `traitprint-mine-story-gaps`.
5. **Canonical skills recurring in job-match `missing_skills` that the
   user plausibly has.** Usually a labeling gap, not a skill gap — the
   cheapest win on this list.
6. **Stale basics or headline.** A profile whose headline lags its actual
   work undersells everything below it (`traitprint vault set-profile`,
   or an `update_profile` proposal on the hosted server).
7. **Lens coverage for target tracks.** The user is aiming at a track no
   lens is shaped for — route to `traitprint-position-lens`.

## 3. Present the top task (or top 3), concretely

For each task, state the payoff plainly and name the exact next step —
which skill, prompt, or command:

> 1. Your Observability skill is rated authority with zero stories behind
>    it — one story clears the biggest credibility flag in the audit.
>    Next step: `traitprint-mine-story-gaps`.
> 2. "Billing migration" is a signature story scoring Draft (0.38) — it
>    will not survive follow-up questions as written. Next step:
>    `traitprint-deepen-story`.

Let the user pick. One task per session done properly beats three
half-done.

## 4. Execute through the proper channel

Extracted or inferred content is a proposal, never a silent write: stage
with `traitprint proposals add` locally, `vault_propose` on the hosted
server, and let the USER approve. Direct CLI writes — `traitprint vault set-profile`,
`traitprint vault lens add` — are for content the user
dictates. Never invent taxonomy IDs or UUIDs — copy them from
`traitprint vault list` output. Payload shapes and the full command table
are in the [shared CLI reference](../shared/cli-reference.md).

## 5. Re-audit and report the delta

```bash
traitprint vault audit --json
```

Report what cleared (the finding codes that are gone, the score that
moved), what the summary counts were before → after, and the next
highest-leverage task for a future session.
