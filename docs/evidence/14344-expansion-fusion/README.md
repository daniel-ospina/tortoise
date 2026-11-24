---
title: "#14344 — the expansion merge rule: what fusing it changes, and what it trades"
type: evidence
domain: platform
doc_status: live
created: 2026-11-23
subjects.team: epistemic-team
aboutSubjects: entity/fact-augmented key expansion, additive second pass, RRF fusion, evidence-complete retrieval
aboutObjects: "#14344, #2513, #2518, #12186, SEARCH-ARCHITECTURE.md"
---

# #14344 — the expansion merge rule, measured

**Instrument:** [`tools/measure_expansion_merge_14344.py`](../../../tools/measure_expansion_merge_14344.py)
**Artifact:** [`merge-rule-measurement.json`](./merge-rule-measurement.json) ·
**Rule under test:** `tortoise/expansion_merge.py::fuse_expansion_pass` (replacing the rank
concatenation that sat in the entity/fact expansion's two passes — A4's
`_search_keys_prf_expansion` still concatenates, deliberately: it is a different lever with its own
measured arm, and this change did not touch it).

⛔ **This is not a recall@k figure on a query set, and it is not comparable to
`docs/evidence/12186-capstone/measurement.json`.** It measures the MERGE RULE's effect on
*which* evidence survives a page cut, over seeded randomised pools. §3 says why the capstone set
cannot supply the graded number, and what would.

---

## 1. What the two rules are

An additive second pass re-asks the store with an expanded query and must merge its rows into the
leg it expands. Both rules below return **the same candidate set** (the union) — they differ only in
ORDER, and order is what a page cut consumes.

| rule | how it orders |
|---|---|
| **old — rank concatenation** | `expanded + pass-1-only`. Every id the expanded run returned sits above **every** pass-1 id the expanded run did not repeat, whatever the two runs' relative ranks were. |
| **new — `fuse_expansion_pass`** | `rrf_fusion([pass1, expanded])` — the module's own fusion (`search_engine.rrf_fusion`), the same function the strategy-level legs go through. A document earns a reciprocal-rank term per list, so an id **both** passes carry earns two terms — an advantage, **not** dominance, and the condition is a **pair of ranks, not a bound on one**: `1/(61+h1) + 1/(61+h2)` beats a single-list candidate at 0-based rank `r` only while `r < (h1 + h2 − 61) / 2`. Two consequences. (a) The **symmetric** case `h1 = h2 = h` — the only one a same-rank fixture can build — wins while the shared id sits at 1-based rank ≤ 61 in **both** lists, TIES a lone rank-1 at 62 (`2/122 = 1/61`, `rrf_fusion`'s `(-score, id)` tiebreak then deciding by id spelling rather than evidence) and loses past that. (b) A shared id holding the **top slot in either list** beats every single-list term outright (`1/61 + 1/(61+h2) > 1/(61+r)` for all `h2`, `r`), so membership is **not** confined to the ≤ 61 case. A pass-1 id can only be passed by a candidate that out-ranks it in the expanded run, by one that ties it and wins that tiebreak (#2952), which at equal rank is the id's order, not its evidence — **or by a shared id under that pair condition**, because two terms can clear a better single-list rank there (which is why the measured `pass1_rank0_position` lands at 2.06 and not 0: only a two-term candidate can clear pass-1 rank 0's single `1/61` term). |

The old rule was not merely inelegant: the sparse pass's own docstring sold the lever as additive
("the expansion can never turn a working lane into a broken one"), which is true of the SET and was
false of the ORDER. At a fixed `limit`, the tail of the merged leg is exactly what leaves the answer.

## 2. Measured — the trade, not a free win

500 seeded trials per shape, `k ∈ {5, 10}`, mean rows per leg's first-k positions:

| k | pass-1 evidence in the page | of that, evidence the expansion did NOT repeat | expansion-only evidence admitted | position of pass-1's own rank-0 row |
|---|---|---|---|---|
| 5 | 1.14 → **3.66** | **0.00 → 1.26** | 3.86 → **1.34** | **12.21 → 2.06** |
| 10 | 2.30 → **6.19** | **0.00 → 3.59** | 7.70 → **3.81** | **12.21 → 2.06** |

⛔ **Read the third column first, because it is the only one that is not a
re-description of the second.** Under the concatenation the count of *pass-1-only* ids in the page
was **exactly zero** in every one of the 2,500 trials — the expansion's rows occupied the head by
construction, so evidence the first pass had already reached survived only where the expansion
happened to repeat it. Under the fusion it is **1.26 at k=5 and 3.59 at k=10**. That is the defect
this change removes, stated as a count rather than as a ranking theory.

The fifth column is the displacement made visible, and it is measured over the whole merged leg so it
cannot saturate: under the concatenation pass 1's **own top row** sat at position ~12 — every row the
expansion returned was above it — and under the fusion it sits at ~2. (An earlier revision of this
doc reported a "did pass-1's top row fall out of the head" boolean instead; that reads `0.0` for
BOTH arms by construction, because each rule's merged head is dominated by whatever that rule puts
first, so it measured nothing and was replaced.)

**Read the second and fourth columns together before claiming a win.** Fusing the merge roughly
triples how much of the first pass's evidence survives the page (second column), and roughly halves
how many *new* candidates reach the same page (fourth). The concatenation was not "better recall": it filled the head with the expansion's rows **by
construction** (with `pass1=10, expanded=10, k=10` the head was simply the expansion), which is the
same displacement read from the other end — and it is why the pass-1-only count above is a flat zero
for that rule rather than merely smaller.

⇒ **The two rules sit on a Pareto frontier of (retention, admission), and no in-leg merge rule can
leave it:** the union of two passes is larger than the leg's page, so a rule that keeps more of one
side must show less of the other. Which point is right is a decision the adoption A/B should make,
not something this lane can settle by choosing a formula.

**The design that would leave the frontier — named, not built here (see §4).** Fuse the expansion as
its OWN leg (`raw_results["entity_key_expansion"]`) at the strategy-level `rrf_fusion` instead of
merging it into the `fts` leg. Then the pass-1 leg is byte-identical to the unarmed read (retention =
100 %, by construction rather than by measurement) and the expansion contributes its own independent
terms (admission = full). Its blocker is a real one and is why it is not in this change: with two legs
present, `_tortoise_fts_query` stops taking the single-strategy path, and that path is where the
caller's `threshold` filter on **raw** leg scores lives — so an armed FTS-only read (dense leg down,
a common shape on a loaded box) would silently stop applying the threshold, contaminating exactly the
A/B the lever exists to win. Any lane taking this over must handle the threshold explicitly.

## 3. Why the capstone fixed set cannot answer the graded question

`docs/evidence/12186-capstone/queries.json` is what the programme's capstone measured +15.47 pts on,
and this lane read it in full before choosing a mechanism. It is a **single-hop NAME set**, and both
of the workstream's expansions are point/session levers:

| what `recall_all@5` needs | what the pinned set carries |
|---|---|
| ≥2 gold **sessions** per question, so "ALL present" is not degenerate | **1** gold row per query (`population_rows`: `{cls, id, name, records}`), i.e. `recall_all@5 ≡ recall@5` |
| a session/turn identity to be "present" | none — the population is harvested object **names**, built by `_upsert_object` into an `:Object`-only graph |
| a temporal window for temporal-range expansion | none — 0 of 300 queries carry a date; no timestamps exist in the corpus |
| points (the C2 lever's scope) | none — `sdk.py` guards the expansion on `entity_type == "point"` (P1 #2557), and the harness queries `entity_type="object"` |

The committed artifact's own residual agrees: at the user door **294/300** queries reach the
canonical by k=15, and **all 6** misses are `rows_zero` with `degraded: true`
(`fallback:degraded:no_fallback_applicable`) — leg timeouts, not ranking misses. **There is no
addressable ranking residual on this set for ANY expansion to close.**

**What is missing to produce the graded number, named exactly:** the multi-session cohort
`longmemeval_2517_ms_tail.json` (n=71, sha256 `091b6e64…`) — built by
`tools/longmem_eval/build_cohorts.py`, whose docstring states **"a 49 MB slice is not committed"** —
plus a working eval embedder preflight (`docs/scoping/receipts/2026-09-22-2513-embedder-preflight-blocker.json`).
Neither is available in this lane, so `recall_all@5` is **not reported**, not substituted.

## 4. What this lane did NOT do

- **No default was flipped.** Both levers remain OFF (`TORTOISE_LME_ENTITY_KEY_EXPANSION`,
  `TORTOISE_ENTITY_KEY_DENSE` — the #1745 fail-safe decision); adoption is the sealed A/B's call.
- **No surface change.** No MCP tool and no SDK method was added, removed or renamed; the new
  helper is module-internal and the two passes keep their signatures.
- **No graded recall figure.** See §3 — and `#2513`'s 2026-11-07 accounting already records that
  every arming point in this programme is eval-lane-only.
