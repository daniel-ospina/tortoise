---
title: "Search Architecture — Changelog"
type: engineering
domain: platform
doc_status: live
created: 2026-11-11
ownedBy: epistemic-team
subjects.team: epistemic-team
aboutSubjects: search
aboutObjects: SEARCH-ARCHITECTURE.md, absent belief state, search design revisions
---

# Search Architecture — Changelog

> **What this is.** The record of *how the search design got where it is* — the work `SEARCH-ARCHITECTURE.md`
> drove and the provenance of its rulings. It is the counterpart of `STORAGE-ARCHITECTURE-CHANGELOG.md`, whose
> issue map (moved from `STORAGE-ARCHITECTURE.md` §13) is the sibling shape; `SEARCH-ARCHITECTURE.md` has never
> carried a section→issue index, so there is no map to move yet, and its review record lives on `#4051` (§10
> says where the corrections that changed the decision are stated).
>
> **What `SEARCH-ARCHITECTURE.md` is.** The design. It states where the vector index lives, what the graph
> engine keeps, and the evidence behind each ruling; it does not narrate its own revisions or track the
> state of the issues it drove.
>
> **Why the narration moved here.** The documents the narration guard polices (`DESIGN_DOCS` in
> `tests/test_extractor_v4_citation_rot.py`: `ONTOLOGY.md`, `EXTRACTOR-V4-ARCHITECTURE.md`,
> `STORAGE-ARCHITECTURE.md`, `SEARCH-ARCHITECTURE.md`) each carried the opposite of their own header, and the
> guard that pins the class is
> `tests/test_extractor_v4_citation_rot.py::test_the_design_documents_carry_no_issue_state_or_change_narration`.
> `ONTOLOGY.md`'s move is `#8745`; this document's is `#11907`, with the same convention as the two
> siblings that predate it (`STORAGE-ARCHITECTURE-CHANGELOG.md`,
> `EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md`). Nothing is deleted: the design statements stayed in the design
> document (reworded to state the design rather than the tracker), and the record of the work is here.

---

## `#11702` — absent belief state is `null` with `ep_state: "absent"`, never the neutral 0.5

Landed by `#11803` (`fix(11702): absent belief state is null with ep_state, never 0.5`), which added the
rule to `SEARCH-ARCHITECTURE.md` §6.3 as tracker state (`**✅ FIXED (#11702) — kept here as the reason the
rule exists…**`), and to §1.5 item 5 as `**landed** (#11702, implementing the §6.3 decision of PR #11481)`.

**The rule it states** (kept in the design document, §6.3): a claim with **no persisted belief** used to
answer the neutral Beta(1,1) mean 0.5, and the ranker coalesced the value to it — indistinguishable from a
computed one at the point where ordering happens. The rule that replaced it is **absent state is `null`
with `ep_state: "absent"`, never 0.5**, and it holds on every read/rank surface: `annotate_ep_batch`, the
canonical `why` `ep` block (`tortoise/why.py`), `GraphRanker`, `StateRanker`/`recall_ranking`,
`GapsRanker`/`gaps_ranking`, `SubgraphExpander`, and the event/object aggregate reads; the `analyze`
templates refuse an absent claim rather than admitting it at a coalesced 0.5. The vocabulary is declared
ONCE (`search_engine.EP_STATE_*` + `ep_has_belief_cypher`/`ep_state_cypher`/`ep_state`/`ep_state_resolve`),
and `tests/test_11702_absent_belief_state.py` pins the property across every one of those surfaces, in both
directions (a *measured* 0.5 keeps its 0.5).

**Why it was red on `main` rather than caught at merge time.** `#11803` merged at **15:29:11Z**; its own
`pull_request` run (`43047153399`, head `2fa7d2fe…`) carried a `test (g)` check-run that **started at
17:16:57Z** and red this nodeid at **17:30:08Z** — so the guard *was* in that shard's population, and the
shard had simply not started when the merge landed. Nothing had concluded against it; the merge gate did not
wait. **Why a required shard can start ~1h48m after its merge is unresolved** — `#11907` carries the
follow-up, and its "shard population" reading is that issue's hypothesis, not this record's. `test (g)` is a
required shard, so the violation made every PR on the repository unmergeable.

**What moved out of the design document on 2026-11-11**, and what stayed:

- §1.5 item 5 kept the *requirement* (the rule is machinery that must exist before private-beta) and lost
  the tracker state (`landed`, the issue reference, and "what remains of it is the per-surface sweep").
- §6.3 kept the *design statement* — the rule, why the coalesced 0.5 is unsound, and the full surface list
  above — and lost the `✅ FIXED (#11702)` header and the "used to answer… replaced it" framing.
- Nothing about the rule's content, its surface list, or `tests/test_11702_absent_belief_state.py`'s
  coverage was dropped or loosened; the guard was not widened and `DESIGN_DOCS` was not narrowed.
