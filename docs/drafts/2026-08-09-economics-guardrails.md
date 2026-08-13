---
title: "Economic/Operational Guardrails — Evaluation Spec"
type: evaluation
domain: economics
doc_status: draft
created: 2026-08-09
ownedBy: epistemic-team
aboutObjects: tortoise, value_extractor, quota, hosted_api, product/pricing.json
supersedes: none
related: docs/drafts/2026-08-09-value-first-extraction-pipeline.md
---

> ⚠️ **PRODUCT-OWNER CORRECTION (2026-08-09): NO capture caps.** Tiers are feature
> baselines; usage is metered separately on top of them. More capture = more graph
> ops = more usage revenue + faster tier progression. Do NOT impose capture caps
> (this doc's F3 caps proposal is REJECTED). The requirement is **cost-model
> integrity**: per-usage cost must stay below per-usage price at scale. The
> value-first volume collapse (≈$1/mo storage, ≈$0.03/session LLM per heavy user)
> makes this hold; the remaining tension to resolve deliberately is the capture
> unit price vs marginal cost (F4 — near break-even at $5/10k write-ops).
> See docs/drafts/2026-08-09-mvp-scope-economics.md for the revised framing.

# Economic/Operational Guardrails — Evaluation Spec

How we know the hosted Tortoise product stays inside cost, latency, and cap
bounds — and how pricing stays honest. Grounds the guardrail/test system in
`product/pricing.json`, `tortoise/quota.py`, `tortoise/hosted_api.py`
(`capture_session`), and the value-first pipeline draft. All numbers below
are computed, not asserted; the session-size percentiles marked † are
interpolations between the measured median (111k tokens) and the measured
heavy band (500–800k) and must be replaced by telemetry in the 2-week pilot
(§8).

## 0. Executive verdict — the five load-bearing findings

| # | Finding | Verdict |
| --- | --------- | --------- |
| **F1** | **Episodic substrate breaks the caps.** The draft keeps ~98 turn Points/session (`hosted_api.py` #490 semantics). Quota counts **all** nodes (`quota.py` `MATCH (n) RETURN count(n)`). At 390 sessions/mo (13/day founder) that's ~44k nodes/mo — a solo user (25k cap) hits 402 at **day ~16–17, mid-month** even with zero extraction. The proposed caps and the draft's "turn Points stay" are mutually inconsistent. | **P0 — must fix before launch.** Either exempt episodic nodes from the node quota (v1: add `is_episodic` label; count non-episodic only) or compact turn Points into a transcript node (v2). Exemption is the minimal change and keeps provenance + re-capture idempotency; the `sessions` quota (`max_sessions`=1000) already bounds episodic growth at ~100k nodes/team ≈ $7.5/mo storage worst case. |
| **F2** | **LLM cost claim validates at median, understates p90+ by ~2x.** Median session ≈ $0.028–0.032 (cached) ✓ inside $0.02–0.05. But heavy sessions (p90/p95) run **$0.06–0.11** because the draft's flat "5 windows" table under-counts: a 500k-token session has ~11 windows of S1, not 5. The cost formula must be window-count-driven, not a fixed table. | Median $0.03, p90 $0.063, p95 $0.092 (cached). Heaviest user $11–16/mo typical, $36/mo worst-case — the "$10–20/mo" claim holds at typical, breaches at all-p95. Guardrail: `cost_per_capture` drift alarm + warrant budget. |
| **F3** | **Pro/team capture caps are 2.5–3x too high for the price.** Pro at its proposed 2,500 captures/mo = $75 LLM COGS vs $25 price (GM −215%). Team at 15,000 = $450 vs $149 (GM −221%). Margin-positive only at ≤ ~1,000 (pro) / ≤ ~5,000 (team) captures/mo at current LLM costs. | **P0 — set pro cap 1,000/mo, team cap 5,000/mo at launch** (still 2.5–13x the power-user median of 390/mo). Revisit upward only when per-session LLM cost ≤ $0.01 (warrant-capped + caching). |
| **F4** | **Overage unit mismatch.** `pricing.json` publishes $5/10k **write-ops**; the capture-cap proposal says $5/1k **captures**. Post-compaction a value-first capture ≈ 55 write-ops, so $5/10k ≈ **$0.0275/capture ≈ marginal LLM cost** — honest. $5/1k captures = $0.005/capture = **1/6 of marginal cost** — a margin hole. | Keep write-ops as the canonical overage unit (already in pricing.json); define the conversion **1 capture ≈ 55 write-ops**; use captures only as the internal cost meter. |
| **F5** | **Instrumentation list is 80% right but missing the money/latency signals.** The proposed 10 omit extraction latency, cap-hit rate, warrant usage (the frontier cost driver), and cache-hit rate — the four numbers that tell you whether the unit economics are drifting. | Refined list: 12 signals, 5 load-bearing (§5). |

Everything else (fail-closed quota counting, async capture, 80% soft warning,
read-only grace) is sound and just needs the test matrix in §6 and the launch
gate in §8.

---

## 1. Cost model validation

### 1.1 The formula (window-count-driven)

```text
Cost(session) =
  S1: n_w × (16,000·P_fi + 200·P_fo)          # value gate
  S2: n_s × (3,000·P_fi + 300·P_fo)           # claim+entity extraction
  S3: n_w × (3,000·P_fi + 300·P_fo)           # windowed relations
  S4: min(3, w) × (4,000·P_xi + 500·P_xo)     # frontier warrants
  S5: 1,000·P_fi + 200·P_fo                   # batched dedup verdict

n_w = ceil(candidate_tokens / 16,000)         # NOT a fixed 5
candidate_tokens = session_tokens × (1 − S0_reduction)
n_s = kept segments (keep-ratio 5–25% × utterances, S2 ceiling)
w   = warrant calls (v1 budget: 1 typical, hard cap 3)
```

Rates: `P_fi=$0.14/M, P_fo=$0.28/M` (DeepSeek V4 Flash); `P_xi≈$3/M,
P_xo≈$15/M` (frontier, per draft S4 numbers). `S0_reduction` 50–70%;
retention 40% at median, 35% on heavy sessions (more tool-output/boilerplate).

**Per-unit costs (computed):** S1 = $0.00230/window · S2 = $0.00050/segment ·
S3 = $0.00050/window · S4 = $0.01950/warrant · S5 = $0.00020.

### 1.2 Per-session cost at real session sizes

| Percentile | Session tokens † | After S0 | Windows | S1 | S2 | S3 | S4 | **Total uncached** | **Cached (−12%)** |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| p50 | 111k (measured) | 44k | 3 | $0.0069 | $0.0040 | $0.0015 | $0.0195 | **$0.032** | **$0.028** |
| p75 | 200k | 80k | 5 | $0.0115 | $0.0071 | $0.0025 | $0.029 | **$0.051** | **$0.044** |
| p90 | 350k † | 123k | 8 | $0.0184 | $0.0101 | $0.0040 | $0.039 | **$0.072** | **$0.063** |
| p95 | 500k † | 175k | 11 | $0.0253 | $0.0151 | $0.0055 | $0.059 | **$0.105** | **$0.092** |
| p99 | 750k † | 263k | 17 | $0.0390 | $0.0227 | $0.0086 | $0.059 | **$0.129** | **$0.114** |

**Verdict on the $0.02–0.05/session claim:** **holds for the median** (p50
$0.028–0.032, i.e. 70% of sessions) **and breaks above p90** (2–3x median).
The draft's flat 5-window table understates p90+ by ~2x because window count
scales with session size. The S4 warrant is 61% of the median session's cost —
the single biggest lever (see §1.4).

### 1.3 Heaviest user monthly (13 sessions/day, 390/mo)

| Mix | Monthly LLM |
| --- | --- |
| 70% p50 / 25% p90 / 5% p95 (realistic) | **$15.7** |
| All p50 (typical day) | $11.0 |
| All p95 (brutal day, sustained) | $35.9 |

**Verdict on "$10–20/mo for the heaviest user":** confirmed at typical mix
($11–16). Breaches only under sustained worst-case sessions. With prompt
caching on shared system prompt + value brief (cache-hit price ≈ 25% of
uncached input; ~20% of input tokens cacheable across windows/sessions) the
blended reduction is **~12%** — real but not transformative. Caching is NOT
the cost fix; warrant discipline is.

### 1.4 Sensitivity — what to instrument first

| Lever | Move | Effect on median session |
| --- | --- | --- |
| Warrants | 3 → 1 per session | $0.032 → **$0.013** (−60%) |
| S0 reduction | 40% → 30% retention | $0.032 → $0.026 (−19%) |
| Caching | 0 → 20% cache-hit of input | $0.032 → $0.028 (−12%) |
| Gate-only sessions | 0% → 40% of sessions extract-nothing | −~28% blended |

**Spec:** v1 warrant budget = **1 typical, hard 3** (already in draft). The
instrumentation must track `warrant_usage_rate` — it is the frontier-cost
driver and the difference between solo break-even and solo −$40/mo.

---

## 2. Latency budgets

### 2.1 SLOs

| SLO | Target | Basis | Alarm |
| --- | --- | --- | --- |
| **LAT-1: capture API p95** | **≤ 1.5 s** (p99 ≤ 3 s) | synchronous work = ~98 turn MERGEs + quota count + Event + audit ≈ 0.5–1 s; value mode REMOVES the regex loop, so capture gets faster than today | p95 > 3 s for 30 min → P1 |
| **LAT-2: background extraction p95** (enqueue → graph write) | **≤ 5 min** (p99 ≤ 15 min) | S1 3–17 windows parallel ≈ 15–30 s; S2 8–45 segments ≈ 10–30 s; S3 ≈ 10 s; S4 1–3 warrants ≈ 20–60 s (frontier is the long pole); S5+S6 ≈ 3 s | p95 > 10 min for 2 consecutive hours → P1 |

Draft correction: the draft says "extraction is a few seconds behind capture"
— that's true for median sessions (~30–60 s) but **minutes for p90+**
(2–3 min). The client contract must say `extraction: pending|done|degraded`,
not imply seconds.

### 2.2 Degradation path (extraction slower than the worker sleep window)

The worker polls the event log every ~30–60 s; a 5-min extraction does not
miss jobs — it accumulates **queue depth**. Degradation is tiered, and the
worker never blocks capture:

1. **Queue depth/team > 10** → drop S4 for that team (skip frontier, keep the
   cheap stages). Flag `hold_queue_rate`. This is the correct first casualty:
   S4 is 60% of the median session cost AND the slowest stage.
2. **Queue lag > 30 min** → pause S1 gate entirely; session fails closed to
   the episodic baseline (Session + Event + turn substrate, no value nodes).
   Alert ops. Never regex.
3. **Worker down** → event log is durable; on restart, replay. Extraction
   completion SLO clock starts at enqueue, pauses at worker outage (documented
   in the SLO definition).
4. **Client reads before completion** → see pre-extraction state (episodic
   only); deltas land minutes later. Accepted per draft (eventual consistency).

---

## 3. Cap arithmetic stress-test

### 3.1 The F1 arithmetic (why episodic counting breaks everything)

Quota counts **all nodes** (`quota.py`: `MATCH (n) RETURN count(n)`); turn
Points are Points (`hosted_api.py` turn MERGE). Value-first keeps ~98
turn/session + ~8–25 value + Session/Event ≈ **113–125 nodes/session**.

| Tier (node cap) | Sessions/mo sustainable | At 13/day | Verdict |
| --- | --- | --- | --- |
| Free 10k | ~85 | day ~7 | node cap binds at 57% of capture cap |
| Solo 25k | ~221 | **day ~17 → mid-month lockout** | **P0 cap churn** |
| Pro 100k | ~885 | fits (29/day) | OK but node cap binds before capture cap |
| Team 600k | ~5,085 | 13 users → day ~29, borderline | fragile |

The proposed capture caps (600 solo, 2,500 pro, 15,000 team) are all
**node-bound within a month for the target segment**. This is the single
most important cap finding: the caps only become the binding constraint if
episodic nodes stop counting against them.

**Fix (recommended, v1):** exempt episodic nodes from the node quota —
`is_episodic: true` label on Session/Event/turn Points; `_count_resource`
counts non-episodic only. Cost of the fix is bounded by `max_sessions`:
1,000 sessions × ~100 episodic ≈ 100k nodes ≈ 100 MB ≈ **$7.5/mo worst-case
storage/team** — acceptable, and it keeps provenance + re-capture idempotency
(#490). (v2: compact turn Points into a transcript node, storage → ~$0.5/mo.)
**Quota bug to fix while there:** `_count_resource("sessions")` currently
counts ALL nodes, not Session nodes — it must `MATCH (s:Session)` so
`max_sessions` actually bounds episodic growth.

### 3.2 Post-fix stress-test (with F1 fix + recommended caps)

Session budget enforced: ≤ 25 value nodes/capture (target 15). Node growth =
captures × 25.

| Tier | Capture cap (proposed) | Cap × 25 | Node cap | Binding constraint | 13/day founder | Headroom |
| --- | --- | --- | --- | --- | --- | --- |
| Free | 150/mo | 3,750 | 10k | **captures** (day ~11 at 13/day — by design) | 402 day ~11 | upgrade prompt |
| Solo | 600/mo | 15,000 | 25k | captures (20/day) | 390 = 65% of cap | 35% ✓ |
| Pro | **1,000/mo (recommended)** | 25,000 | 100k | captures (33/day) | 390 = 39% | 61% ✓ |
| Team | **5,000/mo (recommended)** | 125,000 | 600k | captures (167/day) | 3,900 (10 users) = 78% | 22% ✓ |

Node cap binds only if avg value nodes/session drifts > cap/captures (e.g.,
solo avg > 41.7) — that IS the amplifier-regression alarm
(`nodes_per_session`), so node cap is a correct backstop, not the primary
bound.

### 3.3 Cap-churn / UX verdict

- **80% soft warning + read-only grace: correct.** Free at 13/day warns day
  ~9–10, locks day ~11 — acceptable for a trial tier IF the 402 upgrade path
  is self-serve (checkout #310) and annual/monthly reset semantics are
  crystal clear. No retroactive charges.
- **The solo mid-month lockout (F1) is the only genuine cap-churn bug** —
  fixed by §3.1. With the fix, no tier locks out its target segment mid-month
  (worst case: solo at 20/day locks exactly on day 30).
- **Overage + node-cap UX:** user pays capture overage, then hits node cap →
  confusing. Keep node cap as the final backstop but message it: "node cap is
  the storage bound; captures bill the pipeline". With the 25-node/session
  budget, capture cap × 25 < node cap always, so the node cap only fires on
  amplifier regression — the message then is "storage anomaly, contact us",
  not "upgrade".

---

## 4. Unit economics per tier (COGS table)

Cost basis: storage **$0.075/node/mo** ($73/GB ÷ 1,024 nodes/GB at 1KB;
$0.089 at 1.25KB with index overhead — sensitivity note). LLM **$0.03/capture
blended** (cached, p50-dominant mix; p90 sessions at $0.063 raise it).

COGS scenarios per tier — **typical** = target segment at median utilization,
**heavy** = capture cap at blended cost, **max** = full cap + p90-cost
sessions:

| Tier | Price | Typical usage | LLM | Storage | **COGS** | **GM** | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Free | $0 | 75 cap (50%) | $2.25 | $0.11 | **$2.36** | — | bounded funnel cost ≤ $5.25/mo; fine, cap is the guardrail |
| Solo | $9 | 390 founder | $11.70 | $0.59 | **$12.29** | **−37%** | loss leader, confirmed. **Bound: $19.13 at cap, $40 worst-case** (600 × p90 $0.065 + storage). Alarm at $20. |
| Pro | $25 | 390 power user | $11.70 | $0.59 | **$12.29** | **+51%** | margin-positive ✓. Light user (100/mo): $3.15 → GM 87%, the $4 target holds there. Heavy (1,000): $31.50 → −26%. **Cap at 1,000 or overage recovers it.** |
| Team | $149 | 5 active users (1,950) | $58.50 | $2.93 | **$61.43** | **+59%** | comfortable ✓. 10 users (3,900): $122.85 → GM 18%. **Cap at 5,000 + overage.** |
| — | — | Max (pro 2,500 / team 15,000 as proposed) | $75 / $450 | +$3.75 / +$28 | **$78.75 / $478** | **−215% / −221%** | **the proposed caps are unviable at current LLM costs** (F3) |

**Margin verdicts at launch:**

- **Free:** acceptable funnel cost; bounded by caps. Track aggregate only.
- **Solo:** structural loss leader — the deliberate acquisition cost. The
  bound is the guardrail: max loss ≈ $19/capture-cap, $40/worst-case;
  `cost_per_team` alarm at $20/mo triggers outreach/upgrade incentive.
- **Pro:** margin-positive at GM 51% for the power-user median; the $4 COGS
  target (GM 84%) is only reachable by light users (~100/mo) or at
  per-session cost ≤ $0.01. **Re-scope the target: GM ≥ 35% at median
  utilization at launch, ≥ 60% in 2 quarters** (warrant discipline + caching
  - cheaper flash tier).
- **Team:** comfortable at ≤ 6 active heavy users (GM ≥ 45%); fragile at 10+.
  The 5,000 capture cap + write-op overage (§4.1) holds it.

### 4.1 Overage reconciliation (F4)

`pricing.json`: $5/10k write-ops. A value-first capture ≈ 55 write-ops
(25 value nodes + ~30 edges + session/event), so:

- $5/10k write-ops ≈ **$0.0275/capture ≈ marginal LLM cost ($0.03)** — honest, covers ~90% of marginal cost. ✓ Keep as canonical unit.
- Proposed $5/1k captures = $0.005/capture = **1/6 of marginal cost** — a real margin hole at volume. Reject.

With write-op overage, heavy usage pays its own way: pro at 1,000 captures =
55k ops = $27.50 overage → $52.50 revenue vs $31.50 COGS → GM 40% ✓. Team at
5,000 captures = 275k ops = $137.50 → $286.50 vs ~$155 → GM 46% ✓.

### 4.2 What breaks the model, and the guardrail

| Outlier | Effect | Guardrail |
| --- | --- | --- |
| App-builder on pro, API-driven capture flood | capture cap → overage at marginal cost; node cap is backstop | cap + overage (F4 pricing), `cap_hit_rate` alarm |
| Solo user with sustained p95 sessions | $36/mo LLM vs $9 price (4x COGS) | `cost_per_team` > $20 → outreach; warrant budget; upgrade incentive |
| Team with 13+ active users | GM → ~0 | `sessions_per_user × users × 25` vs 600k fit alarm → enterprise conversation |
| Amplifier regression (regex-style blowup reintroduced) | node cap fires, storage spike | `nodes_per_session` p90 alarm (the load-bearing KPI) |
| LLM provider price change / model swap | per-capture cost shift | `cost_per_capture` drift alarm vs baseline |
| Cache degradation (prompt-cache disabled) | +12% input cost | `cache_hit_rate` alarm |

---

## 5. Instrumentation — refined list (12 signals)

### 5.1 Load-bearing 5 (daily, automated)

| # | Signal | Definition | Target | Alarm (7-day rolling) |
| --- | --- | --- | --- | --- |
| 1 | **nodes_per_session** | value nodes written per capture (post-F1: non-episodic) | median ≤ 25, p90 ≤ 45 | median > 35 or p90 > 60 → **P0 amplifier regression** |
| 2 | **cost_per_team** | LLM+storage $/team/mo (via `monitoring.record_cost`) | ≤ tier price (solo/pro), ≤ 1.5× (team) | > $20 solo-equiv, > $35 pro-equiv, or `cost_per_capture` 7-day MA > 1.5× baseline → P1 |
| 3 | **extract_nothing_rate** | sessions with `keep: []` / total | 20–40% | < 10% (gate not gating → over-extraction) or > 60% (under-extraction → silent loss) → P1 |
| 4 | **budget_overflow_rate** | captures where the 25-node budget dropped nodes, or 402'd | ≤ 2% | > 5% of captures hit session budget, or > 1% of requests 402 → P1 |
| 5 | **extraction_latency_p95** | enqueue → graph write (SLO 5 min) **+ capture p95 (SLO 1.5 s)** | ≤ 5 min / ≤ 1.5 s | p95 > 10 min for 2 consecutive hours → P1 |

### 5.2 Supporting (weekly review, P2)

| Signal | What it catches | Alarm |
| --- | --- | --- |
| `cap_hit_rate` | 402s + 80%-warnings per tier — upgrade funnel health, cap-fit | warning-rate > 10% of a tier's users in a month |
| `warrant_usage_rate` | S4 calls/session — **the frontier cost driver** (§1.4) | sustained > 3/session or > 50% of team cost |
| `cache_hit_rate` | LLM prompt-cache hit — tracks the cost-reduction path | < 10% for 7 days |
| `hold_queue_rate` | extraction queue depth/team (§2.2) | depth > 10 sustained |
| `extractor_version_drift` | keep-ratio/gate-quality per `value@N` and pack version — a pack update that zeroes extraction | keep-ratio distribution shift > 2× vs previous version |
| `fn_rate` | sampled frontier-judge false negatives (1-in-N sessions) | weekly sample, target ≤ 15% of high-value items missed |
| `dedup_rate`, `draft_vs_live_rate`, `point_reuse_rate`, `operator_ratio`, `sessions_per_user` | product-health: over-merging, draft promotion, retrieved-use, ontology balance, cap-fit input | review-only, no page |

### 5.3 Cadence

- **Daily (automated):** load-bearing 5 on 7-day rolling stats + `cap_hit_rate`.
- **Weekly (human):** distributions (never means) of signals 1–5, warrant
  usage, cache hit, fn-rate sample.
- **Monthly (economics review):** COGS vs revenue per tier, overage revenue vs
  marginal cost, cap-fit (share of users within 80% of caps → upgrade
  pressure), pricing sanity (F4 re-check). This is where caps/prices get
  tuned.

---

## 6. Fail-closed correctness — test matrix

Extends `tests/test_quota.py` (which already covers missing-team, at-limit,
counting-error, unknown-resource) + new `tests/test_value_budget.py`.

| # | Test | Expectation |
| --- | --- | --- |
| 1 | Value mode, gate returns 40 kept | exactly 25 value nodes written; overflow dropped; `budget_overflow_rate` incremented — **never silent bypass** |
| 2 | Regex fallback (`TORTOISE_SESSION_EXTRACTION=regex`), 50 regex matches/turn | capture delta ≤ 25 value nodes — the regex amplifier stays capped (this is the #329 flood gate, now budgeted) |
| 3 | Self-hosted (`selfhost_api`, no team) | session budget **still enforced** (config flag, default on) — it's a resource guard, not billing; tier caps skipped |
| 4 | MCP write tools (`create_point`, `create_operator`, `create_event`, `create_subject`, `create_object`, `checkpoint`, `file_decision`, `diary_write`, `mitigate_operator` — the scoping-329 enumeration) | each fails closed at node cap; no tool writes around the capture budget (S6 value nodes count against the same ledger) |
| 5 | validate-then-write | gate output with out-of-vocab kind, conf < 0.6, or bad span → rejected **pre-write**; nodes written == validated candidates |
| 6 | Quota counting exception (registry/graph down) | `QuotaCheckError` → 500, never silent pass (extends `test_counting_error_fails_closed` to the capture path) |
| 7 | Concurrency/TOCTOU | N concurrent captures near cap serialize (per-team mutex on count+write); combined ≤ cap + ε; no double-pass over cap |
| 8 | Overage semantics | pro at 99% of capture cap → 80% warning fired; at cap → metered overage allowed; team at 95% of **node** cap → 402 despite capture headroom (node cap is final backstop) |
| 9 | Extraction-mode fail-closed | `required` without provider → 503 (exists); keep-ratio > 40% for 3 windows → session fails closed to episodic-only + alert, **never regex** |
| 10 | Idempotent re-capture | same `session_id` twice → same nodes (MERGE), no double-count vs quota |

**Perf guardrail:** quota's `MATCH (n)` count is O(n) per write on the hot
path. When count latency p95 > 200 ms or graph > 250k nodes, switch to a
materialized per-team node counter (increment/decrement on write) with the
fail-closed reservation pattern from test 7. Do not ship volume before this.

---

## 7. Launch readiness — economic go/no-go gate

All gates must be green at launch (except solo, which is a **bounded,
explicitly accepted loss**). Any red item requires dated owner sign-off.

### 7.1 The numbers

| # | Gate | Number | Status path |
| --- | --- | --- | --- |
| G1 | **Measured** per-session LLM cost (2-week pilot, production telemetry — not the model) | median ≤ $0.05, p95 ≤ $0.15 | model says $0.028/$0.092 |
| G2 | **Blended COGS/revenue** at launch mix (80 free / 40 solo / 25 pro / 5 team at median utilization) | GM ≥ 25%, path to ≥ 50% in 2 quarters | ≈ $1.2k COGS / $1.7k revenue → GM ~31% |
| G3 | **Per-tier margin floor** | pro GM ≥ 30% at median (51% ✓); team GM ≥ 35% at median (59% ✓); solo loss ≤ $15/mo median ($12.29 ✓, worst-case bound $40 with alarm at $20) | hold |
| G4 | **Cap fit** | ≥ 90% of target-segment users (13/day) fit with ≥ 20% monthly headroom | solo 35%, pro 61%, team 22% (5k cap) ✓ |
| G5 | **Enforcement completeness** | all 10 fail-closed tests green; zero known bypasses | test matrix §6 |
| G6 | **Instrumentation live** | all 12 signals with thresholds, daily/weekly review running | §5 |
| G7 | **Overage honesty** | overage revenue ≥ 70% of marginal LLM cost at heavy usage | write-ops $0.0275/capture vs $0.03 marginal ✓ (F4 fix required) |

### 7.2 Launch-blocking items (the actual work)

1. **F1 fix** — episodic-exempt quota counting (`is_episodic` label;
   `_count_resource` non-episodic + fix `sessions` to count Session nodes).
2. **F3 caps** — pro 2,500 → 1,000; team 15,000 → 5,000 in `pricing.json`.
3. **F4 overage** — keep write-ops unit; publish the 1-capture ≈ 55-write-ops
   conversion; kill the $5/1k-captures framing.
4. **§1.4 warrant budget** — v1: 1 typical / 3 hard, measured via
   `warrant_usage_rate`.
5. **§6 test matrix + §2.2 degradation path** shipped with the value
   extractor.

Everything else is monitor-and-tune: the guardrail system (caps, alarms,
monthly economics review) exists precisely so that the numbers that drift —
per-session cost, amplifier ratio, cap fit — get caught in weeks, not
quarters, and pricing is corrected from measured COGS rather than hope.
