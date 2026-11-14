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

---

## `#12298` — M3 measured: the text index does not move, and the recall gap was never the scorer

`SEARCH-ARCHITECTURE.md` §6.2 defers the text index behind measure **M3** — *"a BM25-capable Postgres
option with score distributions, not only ranks, plus the identifier fixture"* — and §9's M3 row
states the same. `#12298` ran that measurement on the `#11901` corpus with its instrument
(`tools/m3_fts_leg_recall.py`, artifacts in `docs/research/2026-11-13-12298-m3-fts-leg/`), and its
result answers M3's question: **the text index does not move**.

**The numbers** (300 names, paired — one pinned population, one run, both backends, repeated with an
identical second run):

| arm | recall@3 | Δ vs shipped | paired p |
|---|---|---|---|
| shipped `db.idx.fulltext` (RediSearch TF-IDF) | 0.7133 | — | — |
| ParadeDB `pg_search` BM25, **our production terms** | 0.7267 | +1.33 pts | 0.34 |
| ⤷ the same, tokens-controlled subset (174 of 300 queries) | 0.5287 vs 0.5172 | +1.15 pts | 0.73 |
| ParadeDB `pg_search` BM25, its own analyser | 0.9633 | +25.00 pts | <0.001 |
| **the shipped index**, query with the `_`-preserving runs added | 0.9633 | +25.00 pts | <0.001 |

**What that says, in the decision's own terms.** The scorer is not the constraint: with the query terms
held constant, swapping TF-IDF for BM25 moves recall@3 by 1.33 points (1.15 on the subset where the
control holds), is **−0.67 at k=5** (the shipped leg ahead 2–0) and **zero** at k=15, with zero
discordant pairs at k=15. Both directions are stated because reporting only k=3 and k=15 reads as a
floor where the evidence has a sign change. The 25-point difference is real, is
**not BM25**, and is reproducible **inside the current index** — so an index migration earns no recall
return, which is what §6.2's deferral exists to establish.

⛔ **This entry does NOT close `C3` (SPLADE), and an earlier revision of it wrongly said so.** `#12298`'s
step 3 is a recorded owner instruction to proceed into SPLADE without re-litigation, and the literal
falsifier it states is *not* met — a deployable BM25-in-Postgres does beat the current scorer. The
measurement is evidence that C3's **premise** (a ranking limit in the text index) is falsified, and the
brief records that as a recommendation; the ruling on C3 stays in the OPEN homes — epic `#12186` and the pre-registration issue `#12192` — where the owner can take it. ⛔ NOT on `#12298`: that issue is **CLOSED** (`COMPLETED`, 2026-11-13T05:21:53Z) on the reading *"M3 FAILS"*, which this measurement contradicts: the closing condition in its own falsifier sentence is NOT triggered — a deployable BM25-in-Postgres beats the current scorer.
A lane may no more decline pre-authorised scope on its own reading than it may adopt something over a
decision.

**The `#8933` identifier fixture is part of the DoD, and the shipped leg fails it.** Five fixture records
queried at the production window: shipped **3 of 5 reachable**, the in-place query **5 of 5**, BM25 fed
our terms **3 of 5**, BM25 with its own analyser **5 of 5**. The `abc_def` row is **absent from its own name's window at the production
limit and at k=15** on the shipped leg. The identifier capability the product believes it has is defeated on exactly
the shapes `#8933` was built for.

**The defect it found, stated as a design fact rather than as tracker state.** RediSearch treats `_` as a
word character; `sparse.tokenize_sparse_query` (`tortoise/sparse.py:662`, splitting on `[^a-z0-9]` at
`:67`) does not. So for a stored name `web_search` the leg issues `search|web` — two terms the index does
not contain — and `snake_case` names are unretrievable by their own name: of the 73 in the sample,
**1 was found at k=3 by the shipped leg and 68 by the in-place query**. The repair is additive (the
`#8933` alias pattern — an OR alternative, never a removed term) and it has **zero** regressions across
the sample. It is **measured in this harness and not shipped** — that is the state at THIS measurement;
the reason it was withheld (a query-bytes change touches every label and reverts part of `#2070`'s
byte-identity control) is exactly what `#12368` then answered with the number in front of it, and it is
shipped there (see the entry below).

---

## `#12368` — the `_`-preserving composite shipped, on both surfaces

`#12298`'s M3 finding ended with the one repair it could not land on its own authority: the index holds
`web_search` as **one** term while `build_or_query` emitted `search|web`, so the record was
unretrievable by its own name. `#12360` asked for that repair and `#12368` lands it in the one seam
every text read shares (`tortoise/sparse.py::build_or_query`), as an **additional OR alternative** —
`underscore_run_terms` rides after the originals, the `#8933` alias/phrase tails and the `#2070`
expansion tail, and no term is ever removed.

**What it costs and what it buys, at the seam.** Additive-only by construction, and byte-identical for
every query carrying no `_` at all: measured over this bundle's own 300-name population, the emitted
string changes for **87** of them — exactly the names whose §3.4 mechanism row says the leg emitted terms
the index never holds. Measured on the real read path (not a harness), candidate recall@3 over that
population moves 0.7133 → 0.9633 with **zero** regressions, and the same on both fused surfaces.

**The tokenizer contract is NOT what was repaired.** `tokenize_sparse_query` still splits on `_`, so
`#12350`'s question is untouched, and this entry is not a ruling on it. What the repair rides is the
query side, which is why every query without a `_` is byte-for-byte the old one — the property `#2070`'s
control depends on and the reason the review that withheld this in `#12298` was right to withhold it
until the number existed.

⚠️ M3's published tables were measured before this tail existed and were **not** re-run here — a
re-measurement needs a live FalkorDB lane and a `pg_search` Postgres. Its brief §3.4 records that
disclosure in place; the numbers there are a record of the pre-`#12368` emission, not of what ships.
