---
title: "Scope — session-hierarchy candidate expansion (#4211)"
type: scoping
domain: capability
doc_status: live
created: 2026-09-19
ownedBy: epistemic-team
---

# Scope: session-hierarchy candidate expansion — introduce the turns the pool never saw

**Issue:** #4211 · **Team:** epistemic-team · **Complexity:** standard (`complexity:standard`)
**Job:** B6 / W6B (`~/.pi/agent/state/b6-briefs/W6B-graph-candidate-introduction-scope.md`)
**Tree:** `origin/main@4a7b7202e` (worktree `scope/w6b-graph-candidate`) · **Receipt:** `docs/scoping/receipts/2026-09-19-w6b-graph-candidate-census.json`
**Scope only — no product code; no behaviour change; no merge.**

> ⛔ **The headline is a census result, and it corrects the brief's premise in one direction while
> confirming it in another.** The graph on the store where the failure was measured has **exactly one
> edge type** — `(Session)-[:CONTAINS]->(turn Point)` — and **zero** IMPL/NAND/about\* edges. There is
> therefore **nothing for PPR to walk**, as the brief feared. But a **one-hop CONTAINS walk from the
> top-40 pool reaches every gold turn in all 5 window-miss questions (8/8)**, including both turns that
> never enter the pool. The walkable path exists; the missing mechanism is a **session-hierarchy
> expansion**, not personalised PageRank.

---

## 1. The problem, in evidence terms

The D3 answer-shape instrument (21 frozen questions) reports `ctx_recall` **21/21** but `shape_rate`
**5/21**. `ctx_recall` counts *≥1 turn head of each gold session* — it is a session-coverage leg, not an
answer-coverage leg. The answer-bearing **turn** is the missing unit. Five questions were classified
**retrieval-window misses** (`#4105`; M1 §5):

| question | answer-bearing turn | fused rank (pool 120) | class |
|---|---|---:|---|
| `0a995998` (count = 3) | `answer_afa9873b_2_t10` | 19 | in cut |
| `0a995998` | `answer_afa9873b_3_t6` | **146** | **never in the pool** |
| `0a995998` | `answer_afa9873b_1_t4` | **152** | **never in the pool** |
| `1d4e3b97` | `answer_e6b6353d_t2` / `_t4` | 55 / 83 | below the 40-cut |
| `1de5cff2` | `answer_ultrachat_440262_t7` | 66 | below the 40-cut |
| `ceb54acb` | `answer_sharegpt_cGdjmYo_0_t3` | 92 | below the 40-cut |
| `e9327a54` | `answer_ultrachat_480665_t7` | 88 | below the 40-cut |

**Why ordering fixes cannot reach `0a995998`:**
1. It is a **multi-session count** question needing three turns from three sessions; only session 2's turn
   is in the pool. With 1 of 3 turns the reader cannot count to 3 (the #2280 run recorded "2 items").
2. The other two turns sit at fused rank 146 / 152 against a **120-item pool** — no re-ranker, fusion
   weight, or reader change can reach a candidate that never entered the fused list.
3. **The window cannot be widened to admit them.** The reader window is bounded by the 8,000-token /
   32 KiB budget and was measured **97.4 % saturated at 40 items (~195 tokens/item)**: rank 146 is
   ~3.6 windows deep. #4105/#4158 makes the caps *honest*, but the honest cap still cannot admit rank
   146. A candidate at rank 146 must be **promoted**, not merely admitted.

*(Ranks here were re-measured on `origin/main@4a7b7202e`; `e9327a54` measured **88** against M1's recorded
**30**. Reported as a discrepancy, not reconciled — the class is unchanged. See receipt
`discrepancy_reported`.)*

---

## 2. The census (read-only, on store copies)

Access discipline: `~/.tortoise/tortoise.db` was **copied** to `/tmp` before any read; the live
FalkorDB on `:16379` was censused with `GRAPH.RO_QUERY` only. No store was written.

### 2.1 The store where the failure lives (the instrument's capture-shaped store)

Seeding the frozen fixture's haystack exactly as the D3 instrument does (`_seed_memory`, capture shape —
Session + CONTAINS turn Points, no embeddings, no extracted claims, date-only Events):

| question | Points | edges | edge types | IMPL | NAND | Object | embeddings |
|---|---:|---:|---|---:|---:|---:|---:|
| `0a995998` | 484 | 484 | `CONTAINS` | 0 | 0 | 0 | 0/484 |
| `1d4e3b97` | 474 | 474 | `CONTAINS` | 0 | 0 | 0 | 0/474 |
| `1de5cff2` | 473 | 473 | `CONTAINS` | 0 | 0 | 0 | 0/473 |
| `ceb54acb` | 493 | 493 | `CONTAINS` | 0 | 0 | 0 | 0/493 |
| `e9327a54` | 533 | 533 | `CONTAINS` | 0 | 0 | 0 | 0/533 |

The 44 `:Event` nodes are date-only markers joined to nothing; the `vector` leg reports
`ran=True, degraded=true, reason=no_embeddings, count=0` and the `structural` leg
`empty_results` — **the retrieval is keyword-only, and the only edge is the session hierarchy.**

### 2.2 A real store copy (`~/.tortoise/tortoise.db`)

| quantity | measured |
|---|---:|
| `Point` / `Session` / `Event` / `Source` | 55 / 1538 / 3732 / 2196 |
| edges total | 2640 |
| edges by type | `references` 2186, `aboutObject` 404, `CONTAINS` 27, `performs` 8, `produces` 8, `participatesIn` 5, `CORRECTS` 1, `uses` 1 |
| `Point`–`Point` IMPL / NAND | **0 / 0** |
| `aboutSubject` / `extractedFrom` | 0 / 0 |
| `Point`s with **zero** edges | **25 of 55** |
| turn Points (`is_episodic`) | 27 — **0 with an embedding** (#4194) |
| 1-hop edge types from a turn | `["CONTAINS"]` (only) |
| claims isolated (1-hop) | 24 of 27 |

**The `aboutObject` 404 are `Event→Object` from an ingest path — reachable from no retrieval Pool turn.**
The capture path writes no Subject/Object at all (**#3664**).

### 2.3 The epistemically rich graph is a *different* graph

`FalkorDB :16379` graph `tortoise` (`GRAPH.RO_QUERY` only): **1259 Points, IMPL 5229, INPUT 260, NAND 26,
150 operators, 1000 embedded Points, 24 isolated** — but **0 Sessions, 0 CONTAINS, 0 turns**.

**This is the decisive structural fact: the two graph shapes never coexist.** The graph that has PPR
material is a decision/ingest graph with no turns; the graph where questions are read is a capture/turn
graph whose only edge is CONTAINS. (The brief's "operator edges 0/4 (#2552)" and "Objects 0 (#3664)"
are correct **for the capture store**; they are not a statement about the `tortoise` decide graph.)

### 2.4 Is there a walkable path from a retrieved point to the missing one? **Yes — one hop, all 5.**

From the top-40 pool, `(hit)<-[:CONTAINS]-(Session)-[:CONTAINS]->(gold)`:

| question | gold turn | rank | gold session's siblings in the pool | best sibling rank | reached from top-40 | reached from top-120 |
|---|---|---:|---:|---:|---|---|
| `0a995998` | `..._2_t10` | 19 | 5 | 5 | ✅ | ✅ |
| `0a995998` | `..._3_t6` | **146** | 4 | 15 | ✅ | ✅ |
| `0a995998` | `..._1_t4` | **152** | 2 | 39 | ✅ | ✅ |
| `1d4e3b97` | `..._t2` / `..._t4` | 55 / 83 | 9 | 0 | ✅ | ✅ |
| `1de5cff2` | `..._t7` | 66 | 10 | 0 | ✅ | ✅ |
| `ceb54acb` | `..._t3` | 92 | 2 | 6 | ✅ | ✅ |
| `e9327a54` | `..._t7` | 88 | 11 | 0 | ✅ | ✅ |

**8/8 gold turns reachable in one hop.** In every case a *sibling* turn of the gold's own session is
already in the pool — the reader saw the session, just not the turn.

### 2.5 Can a bounded per-session selection pick the gold? (within-session ranks + latency)

One batched session-scoped FTS ordering (`db.idx.fulltext.queryNodes` joined through `CONTAINS`) per
seeded session; the gold's rank among its own session's matching turns:

| question | gold turn | global FTS rank | **within-session rank** | session turns | lexicographic id-order position |
|---|---|---:|---:|---:|---:|
| `0a995998` | `..._2_t10` | 19 | **1** of 10 | 12 | 2 |
| `0a995998` | `..._3_t6` | 146 | **4** of 9 | 12 | 8 |
| `0a995998` | `..._1_t4` | 152 | **4** of 8 | 10 | 4 |
| `1d4e3b97` | `..._t2` | 55 | **6** of 12 | 12 | 4 |
| `1d4e3b97` | `..._t4` | 83 | **8** of 12 | 12 | 6 |
| `1de5cff2` | `..._t7` | 66 | **9** of 11 | 12 | 9 |
| `ceb54acb` | `..._t3` | 92 | **1** of 2 | 4 | 3 |
| `e9327a54` | `..._t7` | 88 | **6** of 14 | 14 | 11 |

**Max within-session gold rank = 9 ⇒ a per-session K = 10 covers every measured gold.** Note the last
column: C4's selection order (`coalesce(lme_chunk_index,-1), id`) is **lexicographic by id** on product
turns (both fields null), which would place the gold at positions 4/8/11 — arbitrary, and for
`..._3_t6` at position 8 a K=3 pick misses it. **Selection must be relevance-ordered, not id-ordered.**

**Latency (loaded box, `nice`):** expansion fetch (120-pool head → distinct Sessions → not-in-pool turns)
**2.2–12.1 ms**, returning 122–198 sibling rows; the deep FTS pass **2.2–4.5 ms**; **zero LLM calls**.

---

## 3. Alternatives evaluated, with why-not

| Alternative | ms / LLM | Why not |
|---|---|---|
| **PPR expansion** (HippoRAG: damping 0.5, passage seed 0.05, top-5) | ~1–10 ms / 0 | On the failing store the only edge type is `CONTAINS`, a hub/tree. PPR is a re-parameterisation of session expansion over the same edges — **no extra reach**, plus a damping/seed-weight knob set to tune. It is the right mechanism for the epistemic `tortoise` graph (§2.3) — a different graph with no turns and no measured failure. |
| **TiMem ancestor propagation** (L1:20 / L2:4 / L5:1) | ~0 ms / 0 | No ancestor hierarchy on this substrate: Session→turn is depth 1. Nothing at L2/L5. |
| **Entity-link expansion** | ~2–5 ms / 0 | Needs Objects + `aboutObject`; capture writes neither (**#3664** — sessions are unattached islands). Instrument store has 0 Objects; the 404 `aboutObject` edges in the real copy are `Event→Object`, unreachable from a pool turn. `coverage_loop` C3-1 is exactly this mechanism and is a **no-op on capture stores** for the same reason. |
| **Change the existing `GraphRanker` (#25)** | ~0 ms / 0 | It only **reorders** on EP confidence / IMPL·NAND degree / aboutObject count / recency — it never introduces a candidate. The ask lane runs `order_by="relevance"`, so it is not even in this path. Cannot reach rank 146. |
| **Change the existing `expand_structural_hops`** | ~0–3 ms / 0 | It *does* introduce candidates, but traverses `:IMPL\|NAND` (0 edges here), and the ask lane passes `structural_hops=0` so it never runs. Pointing it at `CONTAINS` is the same change; doing it there (flat hop-score 1.0/0.5, `LIMIT 40`, no per-session budget, no relevance order) injects arbitrary siblings — the precision risk. |
| **Deepen the pool 120→200** | ~0 ms / 0 | Brings the gold into the *fused pool* (it is at 146/152) but not into the 40-item reader cut, and the cut cannot be widened (§1.3). Changes nothing the reader sees. |
| **Leg-gating (weakest-link)** | ~0 ms / 0 | A **suppression** mechanism; the weak leg here is `vector` (inert). Cannot create a candidate. |
| **Do nothing** | — | The honest counterfactual and **falsifier F1**: if the dense leg (#4202) alone lifts these golds inside the cut, this mechanism is unnecessary. |

**What the field converges on:** deterministic graph expansion fused in rank space — four independent
research passes converged (HippoRAG, TiMem, the Gar/TopoRAG precedent, competitor mechanics). The
**class** is adopted; the **instantiation** is decided by the census (session hierarchy, not entity-graph
PPR) because that is the graph we actually have on the failing store. No recorded decision is
contradicted — #1745's fail-safe default (arms OFF, opt-in) and #3863's surface freeze are honoured.

---

## 4. The design

A bounded **`session_expansion` candidate leg**, fused in rank space through the **existing**
`rrf_fusion(strategy_names=…, weights=…)` seam (`tortoise/search_engine.py`). OFF by default. No new
tool or endpoint (#3863). Zero LLM calls.

**Plug point.** The eval composition first (`tools/longmem_eval/retrieve.py`), the product ask lane
second (`tortoise/ask_lane.py`) after the eval A/B gate — the same staging C4 uses. The leg joins the
existing `rrf_fusion` call; the existing `limit` cut, `dedup_pool`, evidence boost, assembly and
rendering are untouched.

- **SEED** — one batched read: `MATCH (s:Session)-[:CONTAINS]->(p:Point) WHERE p.id IN $head RETURN
  DISTINCT s.id` over the **reader-reachable head** (`pool[:40]`). Anchored on the **edge**, never on a
  `session_id` property — a product turn Point has none (this is the C4 seed gap, §5). Measured: 17–25
  distinct sessions in the head across the 5 questions.
- **EXPAND** — one batched read: the seeded sessions' `CONTAINS` turns **not already in the pool**,
  scored session-scoped with the existing FTS index. Keep per-session **top-K by that score**
  (default **K = 10**, per §2.5), under a **relevance floor** (score > 0 **and** ≥ 1 query-token
  overlap — never a structural-only promotion).
- **FUSE** — order survivors by (the session's best base rank asc, within-session score desc, id), so
  the sessions the base retrieval already ranked highly lead, then fuse as one more ranked list at
  weight `w_session` (default **0.5**). Base-leg weights unchanged.

**Precompute vs per-query:** nothing precomputed (phase 1). Per query: two batched reads
(seed + session-scoped candidates) — §2.5's measured 2.2–12.1 ms.

### Precision guard (each item is a stated invariant)

1. **Seed only from the reader-reachable head** (`pool[:40]`) — never a global session sweep. The
   candidate set is bounded by the retrieval that already judged the query relevant.
2. **Relevance floor** on every introduced candidate (session-scoped score > 0 **and** query-token
   overlap) — no candidate enters on structure alone.
3. **Per-session K** and a **global injected-item cap** (seeds × K ≤ 60, under half the pool).
4. **Bounded leg weight** `w_session ≤ 1.0` — the leg can never outvote a base consensus. (Our own
   measured dilution from a weak leg is −1.95 nDCG; the VLDB grid's weak-leg downside is −21.5 pt.)
5. **The reader window remains the final filter** — the leg adds *candidates*, never context. A win that
   evicts other evidence is not a win (§ experiment, token counter-metric).
6. **OFF by default; arm-gated; OFF ⇒ byte-identical** (golden test).

### Degradation path when the graph is absent

No `Session` nodes / no `CONTAINS` edges (the `tortoise` decide graph, an ingest-only graph, or a
single-session haystack whose head already covers every member) ⇒ **seed = ∅ ⇒ the leg list is empty ⇒
`rrf_fusion` is exactly today's**. Leg trace records the **existing** term `reason="empty_results"`,
`degraded=false`; any Cypher failure is fail-open (keep the base fusion), mirroring
`expand_structural_hops` / C3-1 / C4. **The recorded 4-term status vocabulary
(`available | empty | degraded | unconfigured`, `tortoise/status_vocabulary.py`) is consumed, not
extended — no term is minted.**

### Measured latency budget it must fit

Leg budget **p50 ≤ 10 ms / p95 ≤ 25 ms** (measured fetch 2.2–12.1 ms; deep FTS 2.2–4.5 ms). The whole
post-retrieval graph chain is already 17.5 ms p50, 22 % of the retrieval budget (M1 §2).

---

## 5. What already exists — and the exact delta

**A modification of an existing mechanism is a better outcome than a new one.** `tortoise/session_reinjection.py`
(C4, **#2517**, **PR #3577** — open and merge-conflicting) already:
- fetches a seeded hit's own session's turns over `(s:Session)-[:CONTAINS]->(p:Point)` — **product-real**;
- splices them additively, anchored after the session's last base rank;
- is OFF by default and eval-armed.

**Its two gaps are the whole delta of #4211:**
1. **SEED** — `seeded_sessions` keys on the hit's `session_id`, which `annotate_ask_hits` fills from
   `ev.sessionId or n.sessionId` — a capture turn has neither (`""`), so `session_key_of` yields
   `idx:-1` and the seed is dropped. The module's own docstring names this: *"the seed is therefore a
   NON-turn pool hit that carries `session_id` … that is a SEED change, out of scope for the fetch
   retarget."* **Fix:** resolve the Session from the CONTAINS edge.
2. **Selection + placement** — the fetch orders by `coalesce(p.lme_chunk_index,-1), id` (lexicographic
   on product turns) and anchors *after* the session's last base rank, which **cannot lift an off-pool
   turn into the 40-cut**. A census run of C4's rule (first 5 distinct sessions in `pool[:40]`, 3/session,
   total 10) **reaches 0 of the 8 gold turns**: it seeds the wrong sessions. **Fix:** seed from every
   head session, select by session-scoped relevance (K = 10), and **fuse in rank space** rather than
   anchoring in the pool.

**Other existing mechanisms, precisely located:**
- `GraphRanker` (`tortoise/ranking.py`, #25) — reorders only; the ask lane does not use it.
- `expand_structural_hops` (`tortoise/search_engine.py:1066`) — introduces candidates over `:IMPL|NAND`;
  zero such edges on the failing store; the ask lane never enables it.
- `coverage_loop` C3-1 (`tortoise/coverage_loop.py`) — entity-facet completeness via Object/aboutObject;
  no Objects on capture stores (#3664) ⇒ no facets ⇒ no-op.

**Governance:** this issue **does not duplicate #2517** — it is the product-graph seed fix + relevance
selection + product wiring; it must reuse C4's module rather than author a parallel one. If #2517 lands
first, #4211 shrinks to the seed/selection/wiring delta; if #2517 stalls, #4211 owns the whole leg.

---

## 6. The pre-registered experiment (proves or kills it)

**Instrument.** `tools/ask_shape_rate.py`, frozen fixture
`tests/fixtures/ask_spotcheck_composition.json` sha256
**`7f4062643323af4e5d0fec0b98bb3e3ca8499362c3f7e07a83c55f07633d15fa`**; real pinned reader
`deepseek-direct`; **`--mock` forbidden**. The **deterministic rank diagnostic** (no reader, free) is the
primary instrument; the reader run is the cross-check.

**Precondition.** #4202 (dense leg alive) and #4105/#4158 (honest caps) landed. If not, the run is a
**labelled branch baseline** — never presented as the shipped product's (the W6C rule).

**Primary per-question criterion — never an aggregate.** For **each** of the 5 window-miss questions the
gold turn(s) must be **inside the 40-item assembled reader context with the arm ON** and outside with it
OFF. **Pass = 5/5.** Report each question's gold rank before/after **and the assembled token count**
(the window is 97.4 % saturated — a win that evicts other evidence is not a win).

**Precision counter-metric.** `turn_recall@10` / `nDCG@10` on the 21-question fixture, and a
**per-question correct→incorrect count that must be 0**.

**Negative control.** Arm OFF reproduces today's 21 ranks **exactly**; questions already inside the cut do
not regress.

**Pre-registered sweep.** `w_session ∈ {0.25, 0.5, 0.75, 1.0}` × `K ∈ {3, 5, 10}` — stop at the first cell
passing every criterion; record every cell.

**Falsifier.**
- **F1** — the dense leg alone (#4202) already puts all 5 golds inside the cut ⇒ not needed; close.
- **F2** — any window-miss question's gold stays outside the cut with the arm ON ⇒ no recall delivered.
- **F3** — a reproducible per-question correct→incorrect regression ⇒ the precision guard failed.
- **F4** — leg p50 > 15 ms ⇒ the stated budget is blown.

Any firing closes #4211 with the **negative result**, not a shipped no-op.

**Aggregate honesty.** `shape_rate` is reported **with the measured byte-identical-code spread
0.238–0.333 beside it**, and is never the decision.

---

## 7. Files it will touch

| File | Change |
|---|---|
| **NEW** `tortoise/session_expansion.py` | pure rules (seed / floor / per-session top-K / order) + one bounded batched read; fail-open. **New module deliberately avoids the `retrieval.py` / `sdk.py` contention points.** |
| `tortoise/search_engine.py` | register the leg in the fusion call via the existing `rrf_fusion(strategy_names=…, weights=…)` seam; leg-trace entry. No new function surface. |
| `tools/longmem_eval/retrieve.py` | eval arm composition (`--session-expansion` / `TORTOISE_LME_SESSION_EXPANSION`), OFF by default. |
| `tortoise/ask_lane.py` | product-arm composition — **only after** the eval A/B gate passes. |
| `tests/test_session_expansion_rules.py` | hermetic: seed via CONTAINS for a turn with no `session_id`; floor; K; deterministic order. |
| `tests/test_session_expansion.py` | docker lane: arm-OFF byte-identity; no-CONTAINS degradation; fail-open. |
| `config/ci-surfaces.yml` | register the new test files (an unregistered file fails `test_ci_selection.py`). |
| `docs/scoping/2026-09-19-4211-session-expansion.md` + receipt | this document and the census receipt. |

Contention note: `tortoise/retrieval.py` and `tortoise/sdk.py` are the stated contention points
(#4105/#4158 hold `retrieval.py`/`ask_lane.py`; W3B held `sdk.py`). The new module avoids both; the only
`sdk.py` touch, if any, is threading one knob.

---

## 8. Acceptance criteria

1. Census receipt committed (read-only, store copies) showing edges by type per store shape and the
   failing case's one-hop reachability.
2. `tortoise/session_expansion.py` with pure seed/select/order rules and the fail-open contract;
   hermetic tests green.
3. Leg registered through the existing `rrf_fusion` weight seam; **arm OFF byte-identical** (golden test).
4. Eval arm wired and OFF by default; ask-lane wiring gated on the A/B result.
5. Pre-registered experiment run on the frozen fixture; receipt committed; **per-question** result
   reported (5/5 target) with the assembled token count and the per-question regression count.
6. Falsifiers recorded. If F1–F4 fires, #4211 closes with the negative result.
7. Degradation path proven: no Session/CONTAINS edges ⇒ empty leg ⇒ fusion unchanged; existing
   leg-trace term used; recorded status vocabulary consumed, no term minted.

---

## 9. Anything the census could NOT establish

1. **Whether the dense leg alone suffices** (falsifier F1) — unmeasurable until #4202 lands; this is the
   single most likely reason this issue is unnecessary, and it is why F1 is pre-registered.
2. **The fused rank the expansion reaches.** The census proves *reachability* (1 hop) and *within-session
   selectability* (K = 10); it does **not** prove the fused `w_session`/K grid lifts a gold above rank 40.
   That is exactly what the experiment measures — and F2 kills the design if it does not.
3. **The product ask lane's post-#4202 ranks** — the census ranks here are the keyword-only (vector-inert)
   regime; #4194/#4202 will move them, and the census must be re-read after.
4. **The `e9327a54` rank discrepancy** (88 here vs M1's 30) — not reconciled.
