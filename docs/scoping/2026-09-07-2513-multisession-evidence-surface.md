---
title: "Epic-scope #2513: multi-session retrieval evidence-surface (layer-3 RECALL)"
type: engineering
domain: capability
doc_status: draft
subjects.team: epistemic-team
aboutSubjects: tortoise
aboutObjects: tortoise-longmemeval, tortoise-retrieval, tortoise-ask
relatedIssues: "#2513"
created: 2026-09-07
extends: docs/research/2026-08-24-1657-retrieval-levers/research.md
---

# Epic-scope #2513 — multi-session retrieval evidence-surface

> Parent epic: #1509 (extractor v3 — four-layer stack, layer 3 = RECALL). Issue: #2513
> (epic-scope). This doc converts the fresh mechanism-named research (2026-09-07) into a
> scope + prioritized child-issue plan. Research basis: #2513 context, the #1509
> 2026-09-07 UPDATE (component attribution), `docs/research/2026-08-24-1657-retrieval-levers/research.md`
> (in-repo stack levers), and the official LongMemEval metric semantics pinned in
> `tools/longmem_eval/w7_publish.py` + `docs/benchmarks/comparison-systems.md` §2.

## 0. Status

Scope proposal — child issues created under #2513; **one product-owner decision open**
(§8: how the coverage-incomplete honesty signal surfaces). Doc is `draft` until that
decision and the Phase-0 baseline run land. Worktree: `opt/2513-retrieval-scope`.

## 1. The measured problem (the numbers this scopes from)

Measurement: 2026-09-07, product pipeline, coherent 150-Q full-mix run (receipt `/tmp/lme-v2-p100.json` for the 100-Q subset; recorded in the #1509 UPDATE). Full-mix QA accuracy **0.66** — exactly the historical official 500-Q anchor (66.1%), confirming the measurement. The 150-Q = 50-Q single-session-user head + 100-Q type-tail slice; the QA table below is over the 100-Q subset and the miss attribution over its **47 misses** (the 50-Q head was all-single-session-user — near-ceiling, zero diagnostic signal).

| Slice | QA accuracy |
|---|---|
| single-session-user | 0.94 |
| single-session-preference | 0.28 |
| multi-session (MSR) | 0.45 |

Component attribution of the 100-Q subset's **47 misses**:

| Cause | Share | Meaning |
|---|---|---|
| **PARTIAL evidence** | **53% (≈25)** | Retrieval surfaced k < N of the N evidence pieces — the answer spans N sessions and one-shot top-k RRF over a ~120-deduped pool clusters on the most-similar session and starves the others. **The dominant loss. This program.** |
| Reader had full evidence, still wrong | 34% (≈16) | Halved by the reader-model swap (#2512, closed) — reader is a measurement lens, not the product. |
| Evidence never reached the reader | 13% (≈6) | Extractor/write or rank-depth (write-path side; pool-depth fixes #1947 already shipped). |

The dominant signature across the tail: **low evidence_recall@5 (0.0–0.33) on
aggregation/spread questions** — the pool cannot surface the FULL evidence set when the
answer spans many sessions. Because the official semantics are all-or-nothing
(`recall_all@5` = every ground-truth session in top-5), "k<N of N" scores **0** on the
official binary — partial is not nearly-good, it is a complete miss.

## 2. Convergent SOTA mechanisms + who does which

Fresh mechanism-named research (2026-09-07, captured in this scope + #2513 context; vendor
claims ⚠️ not re-run by us per `comparison-systems.md` publication discipline). Four
mechanisms converge across independent lines of work:

| Mechanism | LongMemEval paper | PRISM | Zep / Graphiti | Supermemory | Emergence |
|---|---|---|---|---|---|
| **(a) Index below session granularity; entity/fact-augmented keys** | ✅ session decomposition + **fact-augmented key expansion (+9.4% recall@k)**; time-aware query expansion | — | ✅ fact edges per entity pair | ✅ atomic memories (fact-level units) | ✅ turn-level match (finer than session) |
| **(b) Entity grounding as the cross-session join key** | ✅ entity-aware retrieval | — | ✅ two-phase entity resolution; entities = the graph spine | ✅ entity-keyed memories | ✅ per-entity turn index |
| **(c) Completeness loop (retrieve→check→expand) instead of one top-k shot** | — | ✅ **Selector/Adder iterative evidence-complete loop** (independent convergence) | ✅ BFS land-and-expand from seeded hits | — | — |
| **(d) Raw episodes reachable from semantic units (non-lossy backlink)** | ✅ sessions retained under decomposed units | — | ✅ episodes (raw) are a first-class scope | ✅ **inject the source chunk when a memory hits** | ✅ **return the whole session on a turn-level hit** |

**In-repo anchors:** (a) turn/point units + per-point `search_keys` (E3 #1535) are already
written at ingest (`extractor_v2`), indexed as `content ∪ search_keys`, and an additive
query-time expansion hook exists (`build_or_query(expansion_terms=…)`, R2 #1541; the ask
lane's A4 PRF harvests top-5 hits' `search_keys` — `tortoise/sdk.py`); (b) the graph has
entities, `aboutSubject`, and write-time entity resolution (E7 #1539, closed) — the join
substrate exists; (c) **no completeness loop exists anywhere in the product or harness**;
(d) raw `session-transcript` chunks already sit in the same pool (R1 #1540, C5 per-session
cap 3) and the reader context is rank-interleaved (C1 #1745) — the backlink material is
there but nothing re-injects a *whole source session* once a semantic unit hits.

## 3. Open space (we would be inventing)

1. **A retrieval-stage completeness GUARANTEE on the user's own corpus** — "all
   contributing sessions were found" as a declared, checked property. No system does a
   retrieval-stage coverage check; PRISM's loop checks *answer-sufficiency* in an agent
   loop, Zep's BFS expands *until graph budget*, neither asserts corpus coverage.
2. **Cross-session numeric aggregation as a declared first-class query type with a
   coverage check** — detect aggregative intent → per-facet retrieval → coverage check
   *before* answering. Everyone handles aggregation implicitly through the reader; nobody
   detects it and gates the answer on evidence completeness.
3. **Temporal correctness of aggregates** — facts superseded mid-sum (a value changed
   between the sessions being summed/counted) must not be double-counted or stale-counted.
   This aligns with the epistemic differentiator: honest uncertainty when coverage is
   incomplete, and correctness under supersession (`docs/ONTOLOGY.md`, E5/E6 #1537/#1538).

## 4. Our design candidates mapped to the stack

Each child issue below maps to a concrete stack site (product-first: improvements land in
`tortoise/`, the eval `tools/longmem_eval/` stays a thin measuring caller — the
product-cohesion audit rule).

| Candidate | Stack site | Mechanism borrowed |
|---|---|---|
| **C2 entity/fact-augmented keys** | `tortoise/sparse.py::build_or_query(expansion_terms)` + `search_engine.run_fts_query` (additive FTS expansion, R2 #1541); harvest seed aliases from the ontology entity/object structure (`aboutSubject`, entity points, E7 edges) + per-point `search_keys` (E3); dense leg re-embed of the expanded query; evidence-boost/weighted-RRF knobs (A3/A5) keep legs additive | (a)+(b) LongMemEval fact-augmented keys; mem0 entity-boost pattern |
| **C4 source-session re-injection** | pool already carries `session-transcript` raw chunks (R1 #1540, C5 cap 3); seed hits → expand to the full evidence session's chunk set inside the reader-context budget (`assemble_context` caps 40 items / 8k tokens / 32 KiB byte cap); MMR/dedup guard against session flood | (d) Supermemory source-chunk injection; Emergence whole-session return |
| **C3 completeness loop** | sits above one-shot `tortoise_fts_query` (ask lane + eval `hybrid_search`): retrieve → coverage-check per facet/session → re-query with missing-facet terms → merge → repeat until covered or iteration/budget cap. Coverage check reuses stored marks / `has_answer` / confidence machinery (EP weights, evidence classes C2 #1745) | (c) PRISM Selector/Adder; Graphiti BFS land-and-expand |
| **C6 time-aware query expansion** | query-side date injection ("Current Date:" / "as of {date}" already prepended to the reader header) into the *query string* pre-embed; extend temporal intent detection (`detect_time_constraint`, R5 #1544) beyond TR to KU/MSR; prefer-latest at rank time using `superseded_by`/`valid_from→to` (E6 #1538) | LongMemEval time-aware QE; R5 machinery (in-repo, shipped) |
| **C5 aggregative intent + coverage check** | ask lane classifier (rule + detector) for aggregative intent; facet the question per aggregation dimension (entity, date range); per-facet retrieval (C2 keys + C3 loop); counting-discipline reader (A2 #1547 aggregation instructions exist — extend); **coverage check before answering**; temporal correctness of the sum under supersession (E5/E6) | open space §3 (2)+(3); reader A2 in-repo |
| **C1 measurement gate** | `tools/longmem_eval/run.py` outcomes already carry `evidence_recall@k`, `chunk_evidence_recall@k`, `reader_evidence@k`, `reader_surface@k`, per-leg trace; official binary `recall_all@5` projection lives in `w7_publish.py`; type-stratified iteration slices (the 50-Q head-slice artifact lesson) | official semantics (`comparison-systems.md` §2) |

## 5. Phased child-issue plan

Phase order = evidence-to-cost, with the measurement gate first (every lever is gated on
the §6 deltas; nothing ships on QA-acc movement alone).

| # | Phase | Issue | Complexity | Why here |
|---|---|---|---|---|
| C1 | 0 — baseline | seal the per-category retrieval metrics + type-stratified iteration protocol + comparison-table row | standard | Without sealed `evidence_recall@5`/`recall_all@5` deltas the levers are unfalsifiable; the table's 500-Q row (#2105) stays the official anchor |
| C2 | 1 — highest-leverage recall | entity/fact-augmented key expansion (sub-session index keys) | standard | Largest *measured external* delta (+9.4% recall@k, LongMemEval); consumes the unused `search_keys`/entity hooks; creates the entity join key everything else expands from |
| C4 | 2 — cheap surface fix | source-session re-injection on seeded hits | standard | Directly attacks the "evidence never reached the reader" + partial-evidence bucket at near-zero architecture cost (material already in the pool) |
| C3 | 3 — the loop | evidence-completeness retrieval loop (retrieve→check→expand) | complex | The only mechanism that targets the all-or-nothing `recall_all@5` semantics *by construction* (k<N→re-query until N or budget); builds on C2 keys + C4 injection |
| C6 | 4 — bounded tail | time-aware query expansion (KU/MSR prefer-latest) | standard | Bounded, mostly independent; needed for the temporal-correctness half of C5 |
| C5 | 5 — open-space capstone | aggregative-intent detection + coverage check + honest coverage signal | complex | New query type + honesty signal + product-owner decision (§8); the epistemic differentiator plays here, not before C2/C3 prove the loop |

**Deferred (out of scope this cycle):** LLM/HyDE query expansion in the hot path
(research.md L1 verdict — eval-only headroom sizing, not a prod lever); calibration-based
selective abstention (needs calibration data the first sealed runs produce); any ontology
change (entity keys ride existing `aboutSubject`/`search_keys`/E7 edges — no new kinds).

## 6. Metrics to gate on

Official semantics per `docs/benchmarks/comparison-systems.md` §2 and
`tools/longmem_eval/w7_publish.py` — **not** QA accuracy alone (QA-acc measures the answer
model; the reader-model swap #2512 proved it is a lens, not the product).

Primary (per category — MSR, knowledge-update, single-session-preference tail; all-`_abs`-
filtered):
1. **evidence_recall@5** (pool; marked extracted points surfaced / marked total — per-category),
2. **official `recall_all@5`** binary projection — `session_recall@k == 1.0` over the
   question's answer sessions (all pieces in top-5). This is the *all-pieces* semantics the
   program targets; partial-evidence questions score 0 until C3 makes the pool complete.
3. **reader_evidence@5 / reader_surface@k** — the evidence that actually enters the reader
   context (C1 #1745 context-level measures), so a pool win cannot be eaten by the
   assembly caps.

Secondary / guardrails:
4. **QA accuracy on the tail categories** (MSR ≥ target, preference ≥ target) — read
   through the pinned reader lens (per #2512 posture), never as the headline.
5. **Regression guard:** single-session-user ≥ 0.94, full-mix ≥ 0.66, write-path/eval
   integrity gates green.
6. **Budget:** hot-path additions inside the 300ms E2E budget; LLM-in-loop iterations
   (C3/C5) bounded per question (iteration cap + token/cost cap) and reported per outcome.

Baseline for the deltas: the C1 sealed run on the current stack (this is the Phase-0
artifact); each lever reports shared-question deltas vs that baseline with CIs (M8
#1528 discipline).

## 7. Risks / falsification

- **Completeness loop cost creep:** unbounded retrieve→check→expand blows latency/cost —
  hard iteration + token caps per question (measured per outcome), never-starve fallbacks
  inherited from R5's posture.
- **Coverage-check circularity:** the check reuses the same retrieval that missed pieces —
  the check must be *facet-based* (missing entity/date-range facets are re-queried
  directly), not a re-rank of the same pool. Falsified if the loop's recall_all@5 delta
  vs one-shot top-k is not material on the sealed categories.
- **Session-flood regressions:** re-injection + expansion must respect the assembly caps
  (C1 #1745 item cap 40 / token 8k / byte 32 KiB) and MMR/dedup; falsified by context-
  token regressions on the eval's context metrics.
- **Temporal double-count:** C5's aggregates must apply supersession/validity at the
  *counting* step (E5/E6 state already on the points) — falsified by KU golds where the
  user changed the value mid-window.

## 8. Product-owner decision (open)

**How should the coverage-incomplete honesty signal surface on the answer surface?**
Options: (a) **abstention** — no numeric answer, name the missing facets (strictest,
aligns with the epistemic differentiator); (b) **in-band flag** — provisional answer +
"retrieved M of N contributing sessions; may be incomplete"; (c) **posture-dependent** —
flag by default, strict abstention under C5 numeric aggregation when coverage <
threshold. This decides the C3/C5 reader contract and the ask-surface API. Default
recommendation: (c), flag-first, with (a) reserved for numeric-aggregation answers.

## 9. Child issues created

See the summary comment on #2513: C1–C6 (titles verb-first, O/I/T + complexity +
relates-to #2513/#1509 in each body).
