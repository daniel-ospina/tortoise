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

`doc_status: draft` — of the two exit conditions stated here since 2026-09-07, **one is met and
one is not**:

- **§8 product-owner decision — DECIDED (c), 2026-09-22** (owner ruling, issue comment 5787468112),
  with its `OVERRIDES:` line carried in §8. This section said "one product-owner decision open" for
  six weeks *after* the ruling landed; corrected 2026-11-07.
- **Phase-0 sealed baseline receipt (#2516 indicator 1) — NOT RUN, and now gated by name.** The gate is
  the owner's **2026-11-07 finite-gate ruling**, not a parked lane: *"if we have other p1,p0,p2 that we
  know affect the eval, then let's first fix those. And then we run the eval. what i dont want is to
  waste an eval while we know what's broken"* (#8486 comment 6710955512). That ruling names **eight**
  affected items and makes them dispatchable; **#2516 is one of them**, and its own deliverable is the
  reporting surface (below), not the run — *"none of them needs an eval to close: their deliverable is
  the fix, each with its own mechanism-level test"*. The run happens when the named set is empty, under
  the unchanged `HARD_STOP_USD = 10.00`.
  **Two blockers this bullet used to name are RETIRED** (re-measured 2026-11-08 at `53d2ab7854`):
  **#10540** is CLOSED (merge `5f033b6d221`, `git merge-base --is-ancestor` → true) and **#11070** is
  CLOSED (merge `98df3bd4c`, ancestor → true). They were current at this doc's own pin (`de72cfd455a`,
  2026-11-06 18:05), which is why the rows below still listed them — stale-by-pin, not wrong when
  written. Do not read a retired blocker as an unblock: **budget and ingest cost are properties of the
  run, not of the issues** that quoted them, and #10538 (the ingest-cost blocker in its own right) is
  still OPEN.
  The third constraint is unchanged and is **not** an issue: the eval lane's embedder preflight fails
  **under load** — the blocker receipt
  (`docs/scoping/receipts/2026-09-22-2513-embedder-preflight-blocker.json`, PR #4679) records three
  consecutive `--mock` preflights timing out at 30.0–30.1 s at load ~250 on 10 CPUs, and #2518 recorded
  the same failure at `--workers 6` — after which `--mock` continues keyword-only, i.e. it measures a
  degraded instrument. The constraint is **load-dependent, not absolute**: the 2026-09-17 capsweep and
  #2518's 2026-09-24 run both loaded the real embedder (`BAAI/bge-small-en-v1.5`, hybrid FTS+vector+RRF),
  so a host that can load it under its own load can take the run. So the doc stays `draft`.
- **No unlanded work exists on any #2513 branch** — by two different proofs, stated per branch because
  they are not the same proof (§10.1). The worktree this section named until 2026-11-07 —
  `opt/2513-retrieval-scope` — is a branch whose content is on `main`; naming it as this doc's worktree
  is what sent rescue lanes after work that had already landed.
- **Landing state per scope unit, with evidence: §10.**

Scope proposal — child issues created under #2513 (§9).

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

**In-repo anchors (as of 2026-09-07, when this plan was written — §10.2 carries the current landing
state of each):** (a) turn/point units + per-point `search_keys` (E3 #1535) are already
written at ingest (`extractor_v2`), indexed as `content ∪ search_keys`, and an additive
query-time expansion hook exists (`build_or_query(expansion_terms=…)`, R2 #1541; the ask
lane's A4 PRF harvests top-5 hits' `search_keys` — `tortoise/sdk.py`); (b) the graph has
entities, `aboutSubject`, and write-time entity resolution (E7 #1539, closed) — the join
substrate exists; (c) **no completeness loop existed anywhere in the product or harness**;
(d) raw `session-transcript` chunks already sit in the same pool (R1 #1540, C5 per-session
cap 3) and the reader context is rank-interleaved (C1 #1745) — the backlink material was
there but nothing re-injected a *whole source session* once a semantic unit hit.

⚠️ **(c) and (d) have both landed since** — `tortoise/coverage_loop.py` (#2608, C3) and
`tortoise/session_reinjection.py` (#3577, C4) — and both are armed from the **eval lane only**,
not the product. §10.2 is the authoritative row-by-row state; do not read (c)/(d) as current.

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
the §6 deltas; nothing ships on QA-acc movement alone). **This table is the plan, not a status
board** — per-row landing state and its evidence are in §10.

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
2b. **`recall_all@N_gold` — the ADDITIVE cutoff variant (sealed 2026-11-08, #2516).** `recall_all@5`
   is **identically `0`** for any question whose evidence set exceeds five pieces, at **any** ranking
   quality — the fifth slot is a wall — so the spread-evidence class §1 attributes 53 % of the misses to
   **cannot register an improvement under the official cutoff even when the fix works perfectly** (the
   arithmetic is recorded on #10538, decision relay 2026-11-07T17:54Z, which names #2516 as the issue
   that seals it). The variant measures the **same** all-or-nothing binary at a cutoff equal to each
   question's **own** gold-session count:

   ```text
   recall_all@N_gold = all(gold session in ranked[:N_gold]),  N_gold = |gold sessions|
   ```

   It is a **declared labelled variant**, never a substitution (R12): `recall_all@5` stays the official
   row, untouched and comparable, and the variant rides beside it with its own `n`. Live in
   `w7_publish.GOLD_CUTOFF_ROW` (top-level + per gate category in `category_variant_rows`), fed by
   `retrieve.py::_recall_metrics` → `session_recall@n_gold` on the **same** ranking the `@5` fraction is
   measured on, and surfaced per category as a fraction in `report.py`'s `retrieval_by_category`. A
   report that predates it counts `n_missing`, never `0.0`.

   **Granularity caveat (recorded 2026-11-09, PR #11598 review note 1).** `N_gold` counts **sessions**
   while the ranking being sliced is **points**, and the deduped pool admits up to
   `DEFAULT_MAX_CHUNKS_PER_SESSION = 3` points per session. `recall_all@N_gold == 1.0` therefore
   additionally requires the first `N_gold` slots to hold `N_gold` **distinct** gold sessions; a
   session-complete ranking that repeats one session inside the window scores `< 1.0`. This is not the
   TREC passage-level convention (which defines R over the retrieval unit) — changing the cutoff's unit
   would change the metric the #10538 relay prescribed, so it stays a documented caveat, not a change.
   The same block also records the **fetch-depth bound**: for a question whose gold count exceeds the
   arm's fetch depth (`max(ks)`, 20 by default) the fraction is pinned below 1.0, which is not
   reachable on the corpora measured so far.
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

## 8. Product-owner decision — **DECIDED (c)**, 2026-09-22

**How should the coverage-incomplete honesty signal surface on the answer surface?**

**Ruling: (c) posture-dependent.** Flag by default — *"retrieved M of N contributing
sessions; may be incomplete"* — and **strict abstention (no numeric answer, named missing
facets) when the question asks for a total or count across sessions** and coverage is
below threshold.

The alternatives considered: (a) **abstention** everywhere — no numeric answer, name the
missing facets (strictest, aligns with the epistemic differentiator); (b) **in-band flag**
everywhere — provisional answer + the M-of-N note. This decides the C3/C5 reader contract
and the ask-surface API.

Why (c): blanket (a) withholds a good answer over a possibly-irrelevant gap — the sealed
census puts evidence loss at **9 of 71 questions**, so it would fire on roughly 13% of real
questions, most of them narrative ones where the gap does not change the answer. Blanket
(b) trains the reader to ignore the flag, destroying the signal. (c) spends strictness only
where a gap produces a **wrong** answer rather than a **weaker** one: a missing session in a
narrative question degrades quality, but in a numeric question it yields a confident wrong
total. It reuses the question-type detection C5 already requires, so it costs nothing extra.

**OVERRIDES:** the common RAG default of answering without any coverage signal — we surface
retrieved/excluded counts and abstain on numeric aggregation, because a silently missing
session turns a count or total into a confident wrong answer, and a wrong number is a worse
failure than an admitted gap.

Authoritative record: the owner ruling on #2513 (comment 5787468112), 2026-09-22.

## 9. Child issues created

See the summary comment on #2513: C1–C6 (titles verb-first, O/I/T + complexity +
relates-to #2513/#1509 in each body).

## 10. Landing state and evidence — reconciled 2026-11-07

Every row below was re-measured at `de72cfd455a`. The **sha** is what these numbers are pinned to, not
the ref name: `origin/main` has advanced since, and a reader who re-resolves the ref will find a
different commit. A unit is **landed** only when a
merged PR's squash/merge commit is an ancestor of that commit (`git merge-base --is-ancestor`), and
"live" means the mechanism's own tests pass there (§10.3). Nothing here is inferred from an
open/closed issue state — that is how this epic was read as "unopened work" three times.

### 10.1 The branches carry no unlanded work — per-branch proof

`git merge-base --is-ancestor` reachability is useless for these branches: they are stale by months
and `git merge-tree --write-tree` returns conflict-marker trees for all of them (main rewrote the
same regions since). Two tests are **authoritative** — `headRefOid` identity (tip == a MERGED PR's
head), or, where that fails, blob identity of the files the branch adds against the commit that
landed them:

| branch | tip | merged PR | merged at | why the tip carries no unlanded work |
|---|---|---|---|---|
| `fix/2513-retrieval-evidence` | `b862d2320cb` | #3577 | 2026-09-22T13:41:54Z | tip **is** the PR's `headRefOid` (identity) |
| `docs/2513-guard-reach` | `78db0426e52` | #4679 | 2026-09-22T19:29:28Z | tip **is** the PR's `headRefOid` (identity) |
| `opt/2513-retrieval-scope` | `9b822b9accd` | #4720 | 2026-09-24T15:24:56Z | identity does **not** hold: the tip is neither #4720's head (`fbc17b8bb34`) nor its parent (`a96663969e0`), and is not an ancestor of `main`. Its **content** is landed: `git rev-parse 9b822b9accd:<doc>` == `git rev-parse a96663969e0:<doc>` == `00ab6062465` (same 182-line doc, landed on a different base; #4720 then added the §8 ruling on top) |

⇒ **zero unlanded work**, but the proof differs per branch — two are `headRefOid` identities and the
third is a byte-identical blob plus a content commit lineage. Do not quote "all three are merged PR
heads" as the identity claim: it is false for row 3.

The gate agrees: `uv run python tools/collision_preflight.py 2513 --repo .` exits **0** —
`VERDICT: CLEAN (exit 0) — 8/9 surfaces queried` (`ALL_SURFACES` has 9 members on this tree), re-run at
2026-11-07 01:12 UTC **from this branch's own worktree** *with this doc's own branch and PR already on
the remote*, so the count includes this lane's signals. The `--repo .` pin is required: unpinned, `#2513`
resolves in both `tortoise` and `agent-infra`, and the tool refuses it as AMBIGUOUS (exit 2). Read the
run this way: the tool excludes the caller's own branch/PR, so the verdict is **worktree-relative** — the
same command run from the hub checkout on `main` returns `VERDICT: COLLISION (exit 1)` on the very PR it
is clearing here. Quote the worktree it ran in, not just the exit code.

⚠️ Do **not** quote a `who-is-on` verdict as this doc's durable evidence, and never merge its numbers
across checkouts. It enumerates *live* signals, so it read `nobody` at 00:18 UTC (before this lane
existed) and `4 holder(s) across 4 surface(s)` on the same 01:12 UTC worktree re-run — while the same
command from the hub checkout (`main`) answers `5 holder(s) across 5 surface(s)`, **more**, because the
hub does not exclude this lane's own branch/worktree/PR. Every one of those is correct
for its moment and its checkout, none is durable. The durable claim is the table above, not a
point-in-time answer.

### 10.2 Per-unit landing state

The mapping to the issue's own **candidate scope** (the #2513 body, items 1–4) — the units a lane
must be able to name:

| # | Candidate-scope unit | Mechanism (child) | Landing evidence on `de72cfd455a` | Where it is armed | Graded movement |
|---|---|---|---|---|---|
| 1 | Query expansion / time-aware retrieval | **C6 #2520**; C2 #2518; also **#4211** (recall-side candidate expansion, an open child of this epic) | #5297 merge `0b3e4a90240`; #10549 sq `2f2582a2720` (DATE-RANGE widens candidates by session); C2's key expansion #2557 merge `0b00b40c274` | **product SDK, default OFF**: C2's `entity_key_expansion` and C6's `time_aware` are keyword parameters of `TortoiseSDK.tortoise_fts_query` (`tortoise/sdk.py:37042`, `:37057`), both `False` by default (the #1745 fail-safe). No in-tree product lane turns them on — the REST search path calls the method without either (`tortoise/hosted_api.py:12952`); the eval lane is the only caller that sets them (`tools/longmem_eval/retrieve.py:1913`, `:1919`) | **UNMEASURED** — #10538 open; C2 measured and **not adopted**, fail-safe OFF stands (#2518); #4211 has its own O/I/T and no evidenced target (§10.4) |
| 2 | Source-session re-injection | **C4 #2517** | #3577 sq `26207c42905` (`tortoise/session_reinjection.py`; **472 L at that commit**, 714 L at this column's `de72cfd455a`); evolved by #10629 sq `58011ae535b` (the guard's missing LOWER bound) | `source_session_chunk_pass` / `reinjection_merge_order` have **exactly one non-test caller**: `tools/longmem_eval/retrieve.py` — a **library + eval arm**, not the product ask path | **MEASURED — NEGATIVE**: #8713's pre-registered surface census (sealed by #3789) FALSIFIED the arm — `reader_evidence@5` / `reader_surface@5` / `evidence_recall@5` all DROP; the unit's Target is unmeasured and the surviving fix is #10540 (§10.4) |
| 3 | Official-metric framing (`recall_all@5`) | **C1 #2516** | #8707 sq `4a487147a0b`: per-category `recall_all@5` rows (`tools/longmem_eval/report.py:2701`) **and** the fact-grain leg `answer_evidence@k` (`run.py:5362`, `run.py:6215`) — this is item (a) of the #2513 scoping comment's §4.2 amendment | eval lane | the **metric exists**; the **sealed Phase-0 receipt does not** (#2516 indicator 1) |
| 4 | Aggregation-aware reading | **C5 #2521** | #2607 merge `7361250f0e1` (content `6550db3ee80`): `tortoise/aggregate.py::detect_aggregative_intent`, called at `tortoise/reader.py:400` (via `_is_aggregative_question`, `:382`) and appended **unconditionally** — no env gate — by the prompt composer `_system_prompt_for_question` (`:510`); #10550 sq `7e866e462f9` (reader keeps counting discipline when its type fragment loses it). ⛔ **Not a product surface**: the reader is reached only from **eval-only** code — the ask lane (`tortoise/ask_lane.py:702`) and the harness re-export (`tools/longmem_eval/reader.py:60`) — #3849 removed the `/v1/ask` route, the SDK `ask()` method and any MCP ask tool, and nothing product-side imports `tortoise.reader` (`tortoise/reader.py:1-10`) | **reader prompt composer, reached only from EVAL-ONLY code** — the ask lane (`tortoise/ask_lane.py`) and the harness re-export (`tools/longmem_eval/reader.py:60`) — no MCP tool, no SDK `ask()`, no `/v1/ask` route (#3849) | #10553 open (exact-count accuracy before/after) |
| — | Completeness loop (the loop both 1 and 2 compose through) | **C3 #2519** | #2608 merge `77e590f88f7`: `tortoise/coverage_loop.py` shell + `session_diverse_order`. The loop's merge **discipline** ships in `tortoise/retrieval.py::guard_and_recap_pool` (`:835`, `session_diverse_order` then `dedup_pool` — its own docstring calls it "the shared merge discipline"); the full composition (census→gap→expand→merge) exists **only** in `tools/longmem_eval/retrieve.py::retrieve_for_question` (`:1444`), so the shipped point is the discipline, not the loop | `guard_and_recap_pool` has **two** non-test call sites — `tools/longmem_eval/retrieve.py:2294` and `tortoise/session_reinjection.py:561` (the re-injection merge) — and both are reached only from the eval lane (`retrieve.py:2597`) | **UNMEASURED**; #2569 / #2570 open |

**The pattern this table records, plainly:** every mechanism is landed, and **none of them has a
product lane that turns it on** — but they are not all eval-lane code, and the difference is what
"landed ≠ shipped" means for each:

- **unit 1 (C2/C6) is already product code, held OFF by a default.** `entity_key_expansion` and
  `time_aware` are keyword parameters of the product SDK method `TortoiseSDK.tortoise_fts_query`
  (`tortoise/sdk.py:37042`, `:37057`), both `False`; the REST search path calls the method without either
  (`tortoise/hosted_api.py:12952`) and the eval lane is the only caller that sets them
  (`tools/longmem_eval/retrieve.py:1913`, `:1919`). Arming this unit is a **decision** (the sealed A/B),
  not build work — which is what the parameter's own docstring says (`tortoise/sdk.py`: "OFF by default
  in the product … the sealed #2513 A/B gate decides adoption").
- **unit 4 (C5) is product code reached only from EVAL-ONLY code** — the ask lane and the
  `tools/longmem_eval/reader.py:60` re-export (#3849: no MCP tool, no SDK `ask()`, no `/v1/ask` route) —
  where its fragment is appended unconditionally (ON, no env gate), so what ships is *code no product
  surface reaches*, not a product behaviour.
- **unit 2 (C4) and the completeness loop (C3) are eval-lane-armed**: their composition call sites are
  reached only from `tools/longmem_eval/retrieve.py` (`:2294`, `:2597`).

"Landed" therefore never means "shipped to users" for this program — read the `Where it is armed` column,
not the PR list.

### 10.3 Proof the landed seams are live (the run that backs this section)

Run in an isolated worktree at `de72cfd455a`, on a private lane container
(`tools/test_lane.py` → `fdb-lane-eedd939a8d`, `127.0.0.1:16421`), so no peer lane's graphs are
implicated:

```bash
# from an isolated worktree at de72cfd455a — private lane container, not the shared one
uri="$(uv run python tools/test_lane.py uri | grep '^export')"   # 3 export lines
eval "$uri"
uv run pytest -q -p no:randomly \
  tests/test_entity_key_expansion.py tests/test_coverage_loop_rules.py \
  tests/test_session_reinjection_rules.py tests/test_guard_measured_revision.py \
  tests/test_build_cohorts_2517.py tests/test_eval_reinjection_cap_resume.py \
  tests/test_aggregative_intent.py tests/test_aggregative_facet_coverage.py \
  tests/test_reader_aggregation_aware.py tests/test_temporal_leg_date_window_10538.py \
  tests/test_w7_publish.py
# → 309 passed in 24.26s

uv run pytest -q -p no:randomly tests/test_session_reinjection.py tests/test_coverage_loop.py
# → 44 passed in 21.14s   (docker-lane E2E legs)
```

**353 passed / 0 failed / 0 skipped.** ⚠️ Read this count for exactly what it is: the seams are exercised
in **both** their default and armed states (several tests arm a mechanism ON explicitly —
`TORTOISE_LME_AGGREGATIVE_FLAG`, `TORTOISE_LME_TEMPORAL_AGGREGATE_FLAG`,
`TORTOISE_LME_ENTITY_KEY_EXPANSION` — to assert the mechanism engages), and the default-preserving
cases are pinned too. What the suite does **not** show is a graded metric **moving**: that needs §6's
cohort run, which is blocked (§10.4).

### 10.4 What remains (this is the scope exit, not a wish list)

| Remaining | Why it is open, stated as a testable condition |
|---|---|
| **#2516 (C1)** | Its indicator 1 — a sealed Phase-0 baseline receipt — has not run. The gate is the owner's 2026-11-07 finite-gate ruling (§0): #2516 is one of the eight **named** items, and its deliverable is the reporting surface plus a mechanism-level test — *not* the run (*"none of them needs an eval to close"*). **Retired as blockers (re-measured 2026-11-08 at `53d2ab7854`):** #10540 (CLOSED, merge `5f033b6d221`) and #11070 (CLOSED, merge `98df3bd4c`) — both were current at this doc's pin `de72cfd455a`. Remaining: the **run**, gated on the other named items (#10538, #10360, #2683, #2684, #2518, #8775, all OPEN) **and** the load-dependent embedder preflight (§0). |
| **#10538 (C6 tail)** | The DATE-RANGE half landed (#10549); the unit's Target is not evidenced — same gated run. **Sealed here, 2026-11-08:** the metric re-cut this issue's decision relay assigns to #2516 — `recall_all@N_gold`, the official all-or-nothing binary at a cutoff equal to the question's own gold count — now rides the publication surface as a **declared labelled variant** (`w7_publish.GOLD_CUTOFF_ROW`, per category in `category_variant_rows`, fraction per category in `report.py`). `recall_all@5` is untouched. |
| **#2518 (C2)** | Measured and **not adopted**; its plan's fail-safe OFF stands. Open as a decision, not as work. |
| **#10540 (C4 survivor)** | **CLOSED 2026-11-07T17:28:40Z** — merge `5f033b6d221`, an ancestor of `main` at `53d2ab7854` (`git merge-base --is-ancestor` → true). The row read *"its instrument landed (#10632) but not the fix"* because this doc's pin (`de72cfd455a`, 2026-11-06 18:05) **preceded the merge by 66 minutes** — stale-by-pin, not a live blocker. Its constraint (reader-window crowding) still rides any run's budget; closing the *issue* did not make the run cheaper. |
| **#2567 (C3 shell, P0)** | The shell and its arm markers landed (#2608; `tortoise/coverage_loop.py`, `TORTOISE_LME_COVERAGE_LOOP` off-by-default, markers/iteration-cap tests in §10.3). Its Target — official `recall_all@5` ≥ +0.06 on the sealed MSR slice with a Wilson lower bound ≥ +0.02 — is the same blocked sealed run as #2516, so it closes on that receipt, not before. |
| **#4211 (candidate-scope item 1, recall half)** | Open child of this epic (`Epic: #2513`), not one of the C1–C6 children: it introduces the turns the pool never saw via the `(Session)-[:CONTAINS]->(turn)` hierarchy (1 of the 5 measured window-miss questions has its answer-bearing turns outside the ~120-item pool, so no re-ranker or reader change can reach them). Its own O/I/T is the exit; nothing in §10.2's table is its landing evidence. |
| **#10553 (C5 measurement)** | Exact-count accuracy before/after for the reader aggregation discipline. |
| **#2569 / #2570 (C3 tails)** | Coverage-signal routing; stratified window reconciliation. |
| **N1 / N2 (proposed, unfiled)** | N1 is BLOCKED-BY W2A's root-cause attribution; N2 is trigger-conditional on the fact-grain leg. Both are defined in the #2513 scoping comment's §4.3 (this doc has no such subsections). Neither is dispatchable, and neither should be filed to look busy. |
| **The honest full-mix ceiling (~0.71)** | Not a work unit — a *target question* the #2513 body says the epic also owns: an honest full-mix ceiling of ~0.71 with a strong reader **until this program lands** (no published system exceeds ~0.71 honestly on this benchmark; the 85%+ figures are vendor self-grades). It has no O/I/T of its own and no owning child, and the one run that would move it is the same blocked sealed run as #2516/#2567, so it is named here for completeness rather than dispatched. |
| **The sealed A/B** | The one measurement that would arm any of the OFF mechanisms. Cannot run here (same two blockers). #2513 therefore **stays OPEN**: its outcome is the evidence, and the evidence is what is missing. |
