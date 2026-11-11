---
title: "Search Architecture — Changelog"
type: engineering
domain: platform
doc_status: live
created: 2026-11-11
subjects.team: epistemic-team
aboutSubjects: Tortoise search architecture
aboutObjects: SEARCH-ARCHITECTURE.md, the absent-belief rule, search design revisions
---

# Search Architecture — Changelog

> **What this is.** The record of *how the search design got where it is* — the
> delivery state of each piece and the corrections applied to the design
> document, indexed by issue.
>
> **What `SEARCH-ARCHITECTURE.md` is.** The design and the measurements behind
> it. It states the rule each material question settled on; it does not report an
> issue's open/closed state, and it does not narrate its own revisions (#8542).
>
> **Split on 2026-11-11 (#11909).** The design document carried two delivery
> claims in §6.3 — `✅ FIXED (#11702)` on the explanatory paragraph, and
> `**landed**` on pre-beta item 5. Both moved here verbatim in substance; the
> design document keeps the rule and the surfaces it holds on.

## #11702 — absent belief state is `null` with `ep_state: "absent"`, never 0.5

The rule was decided in the §6.3 decision of PR #11481 and implemented in
#11702: a claim with no persisted belief answers `null` with
`ep_state: "absent"` instead of the neutral Beta(1,1) mean 0.5, on
`annotate_ep_batch`, the canonical `why` `ep` block (`tortoise/why.py`),
`GraphRanker`, `StateRanker`/`recall_ranking`, `GapsRanker`/`gaps_ranking`,
`SubgraphExpander`, and the event/object aggregate reads. What remained after
that change was the per-surface sweep named in §6.3 of the design document, not
the rule.

The design document keeps the rule, why a coalesced 0.5 is unusable at the
point where ordering happens, and how each ranker honours an absent value.
