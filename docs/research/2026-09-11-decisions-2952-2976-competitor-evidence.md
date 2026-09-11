---
title: "Decision evidence — #2952 (degraded retrieval) + #2976 (temporal retrieval) from competitors"
type: research
domain: product
status: live
created: 2026-09-11
doc_status: live
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise-retrieval, tortoise-longmemeval
extends: product/competition/hindsight.md, product/competition/zep.md, product/competition/_analysis.md, docs/research/2026-09-09-competitor-memory-architecture.md, docs/research/2026-09-11-subgraph-retrieval-research.md
related: "#2952 (retrieval non-determinism), #2976 (temporal 0/52), #2978 (content-less slots), #2985 (battery keyword-only), #2165 (deterministic temporal assembler)"
---

# Decision evidence — #2952 + #2976

Supplies the external evidence for two open product decisions. **Not a new measurement**;
the in-repo measurements live in PRs #3018 (#2952) and #3004 (#2976).

---

## Decision 1 (#2952) — what should retrieval do when the embedding/vector leg is unavailable?

**The question.** A failed or slow embedder load returns "no embedder" for a fixed wall-clock
cooldown (`_FAIL_COOLDOWN_S = 60.0`), so retrieval silently serves a **keyword-only** result for
that period, then silently returns to hybrid. Same query, same store, different answer depending
on when you asked. It is both a reproducibility defect and a measurement-integrity defect (a
benchmark can score "the product" while only keyword search ran).

**Options on the table** (from PR #3018): (A) sticky availability per process; (B) explicit
warm-up + record the degrade; (C) refuse/flag a degraded read; (D) delete the cooldown.

**What the field does**

| System | Behaviour when a leg is unavailable |
|---|---|
| **Tortoise's own battery competitor arms** (`battery/arms/a2_mem0.py`, `a2b_zep.py`) | *"raises `ArmUnavailable` on API failure (**never partial memories**)"* — we already refuse rather than serve a partial surface when measuring competitors |
| **Letta** | Local backends are **documented as full-text only** — a *declared* mode, not a silent swap |
| **Hindsight** | Embeddings pluggable ("any OpenAI-compatible API + LiteLLM"); no documented silent keyword fallback |
| **Zep / Graphiti** | Legs fused by RRF; base retrieval zero-LLM; no documented degrade-to-keyword on embedder failure |
| **Mem0 / supermemory** | Managed; degradation semantics not published |

**No surveyed system documents an automatic silent degrade-to-keyword path.** Where a single-leg
mode exists it is a *named configuration*, not a failure fallback.

**Reading:** the field's pattern is **declare, don't silently degrade** → options **(B) + (C)**.
Warm up explicitly, and make a single-leg read *declared* (flag on the result, refusal in real-lane
measurement) so it can never masquerade as hybrid. This is the same principle as **#2985** (the
battery lane refusing rather than wearing the `real_tortoise` label) and **#2978** (empty hits
must not silently consume the context). A pitch number measured on a silently degraded surface
is indefensible — that is the real cost here, not the ranking drift.

---

## Decision 2 (#2976) — how should temporal questions be answered?

**The trace** (PR #3004): on the 55-Q temporal subset the gold evidence sits at ranks **41–120**,
the shipped window is ~12–24 items of **pure semantic RRF**, and the TR machinery **never fires**
(`detect_time_constraint` returns `interval`/`recency` for **0/55** — these questions reference
*events*, not dates). Widening the window is **measured null** (the band starts at rank 40; the
window is already near the 8 000-token ceiling). The one measured lever is a **full-pool
cross-encoder rerank**: gold admitted 0 → 21, answerable-correct 0 → **6/52**, at **6.6× context**.

**What the field does**

| System | Temporal mechanism |
|---|---|
| **Hindsight (Vectorize) — TEMPR** | **Four parallel strategies**: dense vector, BM25, graph traversal, **temporal search**. The temporal arm parses the query's time reference into a **date window**, retrieves memories overlapping it, ranks them by **semantic relevance (not recency)**, and **spreads results across the window** so coverage is not clustered. All four are merged with **RRF + a cross-encoder reranker**, then trimmed to a **token budget (not a fixed K)**. Their pitch: *"simple vector search isn't enough — 'what did Alice do last spring?' requires temporal reasoning."* |
| **Zep / Graphiti** | **Bi-temporal** dated edges (`valid_at`/`invalid_at`), three search scopes (edges / nodes / communities) fused by RRF + BFS "land and expand"; `MAX_SEARCH_DEPTH=3`; base retrieval is **zero-LLM**. Answers "what was true on date X". |
| **Cognee** | Node-level `valid_to` + supersession (edges largely unstamped). |
| **supermemory** | "Dreaming" consolidates documents into **derived** facts/profiles — the dates are **distilled away**; docs claim it "handles temporal changes" but retrieval-side selection is unpublished. |
| **HippoRAG** | **No time at all** — OpenIE drops temporal triples. |

**Two distinct question shapes — this is the crux**

1. **Window / relative-date** ("what did I do last spring", "how many days since…") → the field's
   answer is a **temporal retrieval leg with a date window** (Hindsight). Our detector models this
   intent (`interval`/`recency`) but fires on **0/55** of this subset.
2. **Ordering / comparison** ("which happened first, X or Y?", "how many days between X and Y?")
   → needs **both compared instances co-present with their dates**. **No surveyed system
   documents a mechanism for this.** A window leg does not guarantee it (both events may fall
   outside any single window), and bi-temporal validity answers *"true when"*, not *"which first"*.
   This is the **32/52** class that stays at 0.

**Also relevant:** **token budgeting over fixed-K** (Hindsight) is the same defect **#2978**
measured on our side (item-count cap; **4.2 %** of an 8 000-token budget used). And the measured
lever here — a **cross-encoder rerank** — is exactly what Hindsight does **by default**, evidence
that its cost is the industry norm rather than an exotic add-on.

**Reading**
- Add a **temporal retrieval leg** (window parse → overlap → semantic rank → spread) for the
  window/relative-date class; our TR machinery has the intent but no leg (it is inert 0/55).
- Budget by **tokens, not item counts** — fixes #2978's measurement *and* matches the field.
- The **ordering/comparison** class has **no off-the-shelf answer**: the co-assembly assembler
  (**#2165** lane 2) is the honest differentiator, and it is precisely the "union vs subgraph vs
  verbatim" question the **pre-registered A/B/C experiment** (`docs/experiments/2026-09-11-abc-context-assembly-experiment.md`)
  is designed to settle. Do not pre-empt that experiment with a rerank-default flip.
- **Do not "fix" temporal by switching to distilled facts only:** supermemory's shape loses the
  dated evidence (in-repo brief: *"distilled-fact consolidation trades away the dated evidence
  Tortoise needs for temporal/state questions"*), while Zep's validity windows keep history.

---

## What this does NOT settle

- No competitor publishes a controlled comparison of these temporal strategies **at matched
  context budgets** — the A/B/C experiment would be the first.
- Hindsight's temporal arm's effect size on **ordering/comparison** questions is unpublished.
- Embedder-degradation semantics are undocumented for managed vendors (Mem0, supermemory); the
  evidence for Decision 1 is therefore mostly **our own competitor-lane contract** + the
  declared-mode behaviour of Letta.

## Sources

- In-repo: `product/competition/hindsight.md` (§6 TEMPR), `zep.md` (§6), `letta-cognee.md`,
  `kimiho.md`, `_analysis.md` (§3a matrix, Findings 5–8); `docs/research/2026-09-09-competitor-memory-architecture.md`;
  `docs/research/2026-09-11-subgraph-retrieval-research.md`; `battery/arms/a2_mem0.py`, `a2b_zep.py`.
- External: Hindsight retrieval docs (`hindsight.vectorize.io/developer/retrieval`) + arXiv
  2512.12818v1 (TEMPR); Zep/Graphiti docs; supermemory docs (`supermemory.ai/docs/concepts/how-it-works`);
  LongMemEval (arXiv 2410.10813) temporal-reasoning definition ("counting time intervals and ordering events").
- PRs: #3018 (#2952), #3004 (#2976), #3005 (#2985), #3000 (#2978).
