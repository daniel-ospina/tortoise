# ADR-009: Server-Side Embedding Model Selection for Hosted Tortoise

**Status:** Accepted (2026-08-21 — gate verdict + user decision; see Evidence Summary)
  - Statistical verdict: **bge-small +15.7% turn_recall@10 (p=0.0005, BH-FDR clean)**,
    arctic-xs +10.6% (p=0.0135, BH-FDR clean), arctic-s -8.9% (ns) vs MiniLM control
    (0.679 ± 0.39, n=138 paired, Docker FalkorDB surface, 0 failures)
  - HNSW production-surface spot-check: **PASSED** for bge-small (p=3.7e-05, n=150, 0 dropped)
  - E2E-8 latency: bge-small 345-358ms p95 vs MiniLM control 418-464ms on the
    contended benchmark laptop — bge-small is 15-25% FASTER than the status quo;
    both exceed the 300ms absolute band only on this environment. **User decision:
    proceed with the swap; latency re-validated at T15 on the production-class
    benchmark box before final acceptance (pre-registered T15 step).**
  - Verdict disposition: swap APPROVED for bge-small (option 1 per user, 2026-08-21)
**Date:** 2026-08-17
**Issue:** #1349
**Owner:** epistemic-team

## Decision

Hosted Tortoise selects its server-side 384-dim embedding model via a
**pre-registered, evidence-gated decision rule** (below), benchmarked across the
candidate pool (MiniLM control, snowflake-arctic-embed-xs, snowflake-arctic-embed-s,
bge-small-en-v1.5). The swap **lands only if** `gate_1349.py` returns PASS and the
PR2 preconditions (a)-(e) hold; otherwise the decision is a documented no-swap /
keep-MiniLM verdict with the negative evidence attached and the non-embedder levers
filed unconditionally. This ADR is the **pre-registration record** — it ships in
PR1 regardless of gate outcome, and the gate script (`tests/eval/retrieval/gate_1349.py`)
encodes the rule mechanically.

A second, product-level decision is recorded here: **local embedding is not offered
to hosted tenants** (rationale and launch checklist below; full UX analysis in
`docs/research/2026-08-17-1349-embedder-selection/ux-research.md`).

## Pre-registered Decision Rule

> Copy of the authoritative rule from
> `docs/scoping/2026-08-17-1349-embedder-selection-scoping.md` (§Pre-registered
> Decision Rule). Numbers are normative; the gate script derives the bar from the
> actual per-question data with the same formula.

**Primary metrics — CO-PRIMARY turn_recall@10 + nDCG@10** on the LongMemEval-S
category set: questions with `question_type` starting `single-session-` OR in
{temporal-reasoning, knowledge-update, multi-session}; exclude `_abs` abstention
questions. A candidate wins iff it clears the procedure on **EITHER** metric.

- **nDCG@10 definition (net-new):** binary gains (1 for a retrieved turn that is a
  has_answer evidence turn, else 0), position discount log₂(i+2), IDCG = ideal
  ranking with all evidence turns first capped at 10; questions with zero evidence
  turns → nDCG@10 = 0.0, included in the mean; mean aggregate.
- **Win criterion (POWER-PRE-REGISTERED — the normative bar):** candidate wins iff
  (a) aggregate mean beats real-MiniLM control by **≥ +5% relative** (nominal floor)
  AND (b) BH-FDR at q=0.10 rejects the pairwise one-sided bootstrap test, on EITHER
  co-primary metric.
- **BH family = m=6** (3 candidates × 2 co-primary metrics; win on EITHER) → top-rank
  p threshold = q/m = 0.10/6 = **0.0167 → z ≈ 2.128**.
- **n-adaptive bar:** bar ≈ z(1−q/m)·sd/√n ÷ control_mean (q=0.10, m=6). Effective
  bars: **+9.5% at n≈500, +12.3% at n≈300, +15.1% at n≈200** (unit-tested expected
  values). The bar is procedure-pre-registered but number-empirical — derived from
  the same data it tests (double-dip acknowledged below, §Empirical-Bar Double-Dip).
- **Significance test:** per-candidate one-sided bootstrap p = P(mean resampled
  paired delta ≤ 0) over per-query paired deltas. Paired 90% CI on the delta
  excluding 0 is **reported as evidence but is NOT an additional gate** — the win
  gate is (a) AND (b) only.
- **Family reduction:** the 6 configs (MiniLM, bge-small, arctic-xs ×2, arctic-s ×2)
  reduce to 3 FAMILY deltas per metric (arctic = max of its 2 configs, pre-registered
  selection rule); m=6 = 3 families × 2 metrics. P@10 (secondary); session_recall@10 and P@5 reported as tertiary (per scoping)
  reported, not gated.

**Escalation rule (fires when NEITHER metric clears — OR-of-three):** escalate if
ANY of (i) ≥1 family delta positive at p<0.10 pre-FDR on EITHER co-primary metric
(turn_recall@10 OR nDCG@10); OR (ii) control turn_recall@10 ≥ **0.50**
(ceiling-compressed); OR (iii) ≥1 family with a per-category gain **≥ +5pp** on any
of the 4 paper categories on EITHER co-primary metric. Escalation = end-to-end judged
check on the **top-2 candidates PLUS control** (3 judged families; control always
judged so the vs-control paired criterion is always computable) executed **on the
production HNSW surface** (T2 `--db docker://...`, fresh per-family graphs, reusing
only the disk-persisted model-keyed encode cache — NOT retrieval checkpoints), on the
**FULL filtered-split question set** (a post-hoc n that shrinks until p<0.10 is a
cherry-picking window, explicitly forbidden); judge model **pinned** (single
OpenRouterModel, recorded in the escalation artifact).

- **Escalation PASS criterion:** the judged top-2 run's winner clears end-to-end
  accuracy vs control by **≥ +5% relative** with one-sided paired **p<0.10 pre-FDR**
  on the same category set, else NO-WINNER. **Judge unavailable/non-answers →
  NO-WINNER** (pre-registered).
- **Escalation top-2 selection:** argmax of combined rank over the co-primary deltas
  (reusing the multi-winner ordering); control always added as the third judged
  family. For escalation winners, GATE (c) is satisfied by the judged run itself
  (executed on HNSW); the m=2 retrieval leg is a non-blocking directional read.

**HNSW spot-check:** winner-vs-control on the production HNSW surface (Docker, same
category set); pass = same BH q=0.10 procedure at the spot-check's **own m=2**
(z≈1.645, NOT the burn's m=6 — stated; m=6 would block the PR2 fail-safe), at the
spot-check's own n = the full filtered-split question set. WAIVED when the escalation
run fires (the judged HNSW run supersedes it).

**Outcomes:**
- **PASS(model):** PR2 is created (subject to preconditions (a)-(e) below).
- **NO-WINNER:** no candidate beats control ≥Δ with FDR-clean CIs on either
  co-primary metric → **no swap** with the negative evidence attached; non-embedder
  levers (key-expansion, time-aware query expansion, fusion-fix) AND the TF-IDF
  hard-tier lexical+semantic hybrid research issue are filed **UNCONDITIONALLY** as a
  **new retrieval-optimization research issue — NOT #317** (the reranking slice only).
- **INSUFFICIENT-POWER:** judgment tiebreak in pinned order — **mini-BEIR OOD
  datasets → labeled-pair calibration → nDCG@10** — recorded as judgment-based, NOT
  an indefinite deferral.
- **Degenerate baseline:** control mean turn_recall@10 < 0.05 → relative-delta
  criterion ill-defined → absolute-delta fallback fires only if a candidate clears
  absolute turn_recall@10 ≥ **0.30**.
- **Multi-winner tiebreak:** if >1 candidate clears (a)+(b), land argmax aggregate
  (turn_recall@10 + nDCG@10 combined rank); ties broken by lower E2E-8 latency, then
  smaller image size, then family-preserving (arctic-xs vs MiniLM encoder-space
  proximity — hypothesis-level, final tiebreak only).
- **Split-config tie rule:** if different arctic configs win different metrics, the
  swap config = argmax on the metric that cleared the gate, else combined rank.
- **Dropped-question accounting:** breaker_open / dropped questions excluded from
  means with counts surfaced in the report; fail threshold > 5% of paired questions.

**Stage-0 pilot (pre-burn):** n≈150 (MiniLM control + arctic-s) measuring control
turn_recall@10 + nDCG@10, empirical paired-delta sd, rough delta. **Go/no-go
(directional only):** control recall ≥ ~0.50 (ceiling-compressed) OR best candidate
< +2pp absolute recall vs control → escalate to the nDCG@10/end-to-end path or close
with the pilot evidence (**pilot close requires HUMAN confirmation**; the +2pp leg
is a 0.6σ read at n≈150).

**PR2 preconditions (a)-(e) — enforced by gate_1349.py:**
- (a) `gate_1349.py` verdict = **PASS**.
- (b) **product-call.json** = `server-side` (enum ∈ {server-side, selfhost-only,
  reject-swap}; asked BEFORE the burn at the T7→T8 gate; no response in 24h →
  proceed with server-side default, recorded).
- (c) **winner-vs-control HNSW spot-check** (Docker, production surface) clears
  (escalation winners: satisfied by the judged run itself).
- (d) **pre-swap E2E-8 ≤300ms p95** (winner, on the deployment VM class) — latency
  is not first-measured post-sunk.
- (e) **#265 merge-status check:** no non-384 dimension landed before PR2 (if #265
  lands non-384, the 768/1024 pool reopens and PR2 is not created as planned).

**Retrieval geometry (stated):** gate runs are brute-force exact cosine on embedded
graphs; production lands on HNSW approximate — the spot-check (c) is the
production-surface confirmation. Pre-registered rollback: if the winner does not hold
on the HNSW/production surface, revert to MiniLM.

## Candidate Pool

| Candidate | Params | Dim | MTEB-R | Role |
|---|---|---|---|---|
| all-MiniLM-L6-v2 | 22.7M | 384 | 41.95 | **control** |
| snowflake-arctic-embed-xs | 22M | 384 | 50.15 | candidate (fine-tuned FROM MiniLM; +8.2 at identical size) |
| snowflake-arctic-embed-s | 33M | 384 | 51.98 | candidate |
| bge-small-en-v1.5 | 33.4M | 384 | 51.68 | candidate (~127MB, MIT; ~1.7-2× slower per encode than MiniLM on CPU) |

All sentence-transformers-loadable within the existing `>=3,<6` pin, no
`trust_remote_code`. The 768/1024-dim class (nomic-embed-text-v1.5, Qwen3-Embedding-0.6B)
and NVIDIA Llama-3.2 fine-tunes are the documented **upgrade path — NOT benchmark
candidates** (collide with the #265 384-dim pin + 2GB VM budget; GPU/API tier).

## Encode Policy

- **bge-small-en-v1.5 / MiniLM: no prefix** (v1.5's designed instruction-free mode;
  MiniLM has no prefix concept).
- **snowflake arctic-xs / arctic-s: run in BOTH no-prefix AND vendor config
  (`prompt_name="query"`, query-side prefix, documents plain) UNCONDITIONALLY in T8**
  (one extra encode pass per arctic model — removes the systematic false-negative
  risk of measuring arctic below its vendor config).
- The gate compares each candidate in its **best-validated config**; the swap lands
  in the config that measured best. Cross-lens matching stays plain (same single
  model, prefix applied query-side in the search path only, mirroring mem0's
  query/doc prefix tagging).
- Encode cache is **model-keyed** (`sha256(model_id + prompt_name + text)`, scoped
  per config run) — a content-hash-only cache would let one model's run serve
  another's vectors.

## Outcome Branches

- **Swap lands in the config that measured best** (per-candidate best-validated
  config; arctic split-config tie rule above).
- **No-winner → keep MiniLM** + file the non-embedder levers (key-expansion,
  time-aware query expansion, fusion-fix) AND the TF-IDF hard-tier lexical+semantic
  hybrid research issue **unconditionally**, as a new retrieval-optimization issue
  (NOT #317).
- **Insufficient power → judgment tiebreak** (mini-BEIR OOD → labeled-pair
  calibration → nDCG@10), recorded as judgment-based.
- **Customer-local verdict (product call = reject-swap or selfhost-only) → swap
  becomes moot** until the hosted-vs-local architecture resolves; the candidate class
  changes entirely (configurable/local embedding support) — filed as a project-level
  issue coordinated with #265.

## Local Embedding for Hosted Tenants: NO (rationale)

**Local embedding is NOT offered to hosted tenants** (default tier). Rationale:
(1) **industry precedent** — mem0/Zep/LangMem/Letta all run embedding server-side in
the hosted tier; local is the self-hosted story (Zep even removed its bundled local
embedding service in CE); (2) **server-side default** — the baked model matches the
industry answer and is strictly more local than mem0's default (no external embedding
API call); customer control = model choice via config; (3) **self-hosted = the local
story** — operators needing on-machine embedding run self-hosted Tortoise (lazy
first-use download, on-machine encode). The **#265 encrypted tier** carves out
**client-side embedding as a special case for encrypted teams**
(`encryptionVersion>=1`, client-computed 384-dim vectors into the shared index).

## Tenant-Visible Changes at Launch (checklist)

Launch-comms source: `docs/research/2026-08-17-1349-embedder-selection/ux-research.md`.

- [ ] API/SDK surface unchanged — no new parameters, no schema change.
- [ ] Stored vectors re-embedded server-side during a maintenance window (backfill
      `--force-re-embed`, 6-label surface); dedup/review-connections cosine matching
      degrades during the batched window (operator guidance: pause ingestion or
      explicitly accept degraded dedup suggestions).
- [ ] Retrieval quality direction per gate evidence — launch message reflects the
      measured verdict (co-primary turn_recall@10 + nDCG@10, BH-FDR q=0.10 over m=6).
- [ ] Latency envelope ≤300ms p95 (E2E-8 band holds post-swap; pre-swap
      E2E-8-with-candidate ≤300ms is a HARD PR2 precondition).
- [ ] No tenant action — no re-auth, no client change, no re-download, no settings
      change.
- [ ] Self-host one-time download — self-hosted operators pull the new model once on
      first use after upgrading (lazy first-use download).

## Per-Label Blast Radius

> **T16 placeholder.** Filled with the measured swap impact per label at PR2/T16.
> The re-embed surface is the backfill tool's LABEL_CONFIG set —
> **Point / Subject / Object / Document / Event / Source** (6 labels) — with
> AgentSession rows counted as a sub-breakdown of Event and the legacy meeting
> junk-vector purge included in the maintenance window.

## Deploy Checklist

> **T16 placeholder.** Finalized at PR2/T16 with the evidence summary, provenance
> links, and the post-bake envelope/E2E-8 re-runs. Scaffold: bake winner in
> Dockerfile.hosted (org-qualified cache path + entrypoint FATAL), CI cache key v2,
> backfill `--force-re-embed` across the 6-label surface, same-dim NO-DROP index
> (auto-update on SET; 768-dim boundary would require DROP+recreate), threshold
> recalibration from the labeled-pair fixture, E2E-8 ≤300ms + HNSW spot-check on the
> post-swap image.

**Rollback:** re-bake the **previous `EMBEDDING_MODEL`** + **re-run the
force-re-embed backfill** (same no-DROP mechanics; vectors return to the previous
model's space). Pre-registered trigger: the winner does not hold on the
HNSW/production surface.

## Empirical-Bar Double-Dip (acknowledged)

The n-adaptive bar derives `sd` and `control_mean` **from the same burn data it
tests** — a mild double-dip, acknowledged here as pre-registered. **Mitigation:** the
pre-burn stage-0 pilot (n≈150) measures control level and empirical paired-delta sd
**independently**, bounding the bar's inputs before the full burn and feeding the
go/no-go.

## Context

Hosted Tortoise moves toward a real-model retrieval baseline: the only committed
baseline uses synthetic topic-centroid stand-in vectors (model-independent,
near-ceiling) and cannot measure an embedder swap. The candidate pool is bounded to
384-dim CPU-feasible encoders by (a) the pending #265 encrypted-tier design (client
384-dim MiniLM vectors into the shared index — a **chosen scope cut**, not a forced
shipped constraint; escape clause: if #265 lands non-384 before PR2, the 768/1024
pool reopens), (b) the 2GB VM feasibility budget, (c) single Point.embedding index
dimension-fixedness (any dim change = full re-embed + index rebuild). The hosted-vs-
local question resolves as a constraint, not a blocker (industry precedent:
server-side baked; local = self-hosted). Decision record ships in PR1 regardless of
gate outcome — the swap is conditional, the record is not.

## Evidence Summary (2026-08-21 — T8 burn, Docker FalkorDB surface, n≈138 paired)

| Config | turn_recall@10 | vs MiniLM control (0.679) | one-sided p | BH-FDR | n-adaptive bar | Winner |
|---|---|---|---|---|---|---|
| MiniLM (control) | 0.679 | — | — | — | — | — |
| **bge-small** | **0.786** | **+15.7%** | 0.0005 | ✅ | 8.4% | ✅ |
| arctic-xs vendor | 0.751 | +10.6% | 0.0135 | ✅ | 9.9% | ✅ |
| arctic-xs no-prefix | 0.605 | -11.0% | — | — | — | prompt-penalized |
| arctic-s vendor | 0.619 | -8.9% | 0.97 | ❌ | 10.1% | ❌ |
| arctic-s no-prefix | 0.366 | -46.1% | — | — | — | prompt-penalized |

- Burn: LongMemEval-S, 150-question subset, vector-only arm, Docker FalkorDB (stable — 0 question
  failures vs 21-61 in embedded mode under machine contention); per-question outcomes + gate
  manifest + verdict JSON committed under `docs/research/2026-08-17-1349-embedder-selection/`.
- HNSW spot-check (production surface): bge-small turn_recall@10 +0.098 (p=3.7e-05), nDCG@10
  +0.111 (p=0.0), 0 dropped — `hnsw-spotcheck-bge-small.json` committed.
- Threshold recalibration (bge bands, measured 2026-08-21): DEFAULT 0.40→0.72,
  NEAR_DUPLICATE 0.75→0.89, DEDUP_REVIEW 0.60→0.84, DEDUP_AUTO_MERGE 0.92→0.94;
  checkpoint 0.95 re-validated (near-dup anchor 0.9547 ≥ 0.95); applied across
  embeddings.py, cross_lens.py (single-source import), sdk.py, mining.py, mcp_server.py.
- Latency: see Status block — bge-small faster than control on the contended box; 300ms
  band re-validated at T15 on the benchmark box (deployment-class measurement).
- Post-swap E2E-8 re-run (default=bge, 2026-08-21): p95 460ms 'inconclusive' under
  full-suite contention (345-460ms across runs); MiniLM control 418-464ms under identical
  conditions — bge-small remains relatively faster. The 300ms absolute band requires the
  production-class benchmark box (pre-registered T15 deployment-class measurement);
  recorded here as the re-validation trigger, not a passing/failing gate on this hardware.
- Non-embedder levers (key-expansion, time-aware query, fusion-fix; TF-IDF hard-tier hybrid):
  filed UNCONDITIONALLY as a new retrieval-optimization issue per the pre-registration
  (independent of the PASS verdict).

## Consequences

- **Positive:** measurable, pre-registered model selection (no judgment call at merge
  time); real-model baseline replaces the synthetic one; the ADR + UX research
  survive a negative gate as the documented decision record; swap lands in the config
  that measured best; unconditional lever filings attach the negative evidence on a
  NO-WINNER.
- **Negative:** gate is expensive (12-45h burn, up to 4 days contention, 5-day
  escalation to project-workflow); bar double-dip (acknowledged, pilot-mitigated); a
  no-winner verdict leaves MiniLM in place (documented, with the levers filed); the
  hosted-vs-local answer is locked to server-side for the default tier (self-hosted +
  #265 encrypted tier carry the local stories).
- **Risks:** benchmark-box vs 2GB-VM envelope difference (measured separately, T8 +
  T15); MTEB/BEIR contamination (BGE/arctic trained on MS MARCO — mini-BEIR OOD
  datasets weighted for the tiebreak read); BH-FDR PRDS assumption for 6 one-sided
  tests vs a common control (plausibly holds; dependent-deltas unit case in the
  gate's tests).
