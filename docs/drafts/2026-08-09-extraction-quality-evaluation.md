---
title: "Extraction-Quality Evaluation Framework — Value-First Extractor"
type: design
domain: engineering
doc_status: draft
created: 2026-08-09
ownedBy: epistemic-team
aboutObjects: tortoise, extractor, value-extractor, ontology, expansion-packs
supersedes: tests/bench_gold.py (seed harness, subsumed)
---

# Extraction-Quality Evaluation Framework

How we *know* the value-first extractor (`docs/drafts/2026-08-09-value-first-extraction-pipeline.md`)
extracts what is valuable — and does not under-extract (silent loss) or
re-pollute (gate collapse). This spec defines the gold set, the metric suite
with targets, the confidence-calibration loop, the CI regression harness, the
v1 acceptance criteria, and the production drift telemetry. It subsumes the
seed harnesses (`tests/bench_gold.py`, `tests/eval_harness.py`) into one
versioned, CI-gated system: `tests/extraction_eval/`.

Thesis statement: **extraction quality is a two-sided risk.** Over-extraction
is measured by precision/keep-ratio (regex's 88% noise). Under-extraction is
measured by false-negative rate and empty-session rate — and "extract
nothing" has *no established benchmark anywhere* (verified research), so this
framework is also the product's leading artifact on the question. Every number
below is a decision, not a hope.

---

## 0. Scope and units

- **Unit under test:** the `value` extraction mode as specified — S0
  preprocessing → S1 value gate → S2 claim/entity extraction → S3 relations →
  S4 warrants → S5 grounding → S6 write. Same `Extractor` protocol
  (`tortoise/extractor.py:50`), new module `tortoise/value_extractor.py`.
- **Labeling unit:** a *window* — a contiguous run of 20–60 turns (~10–30k
  tokens) cut from a real session. Windows, not whole sessions, are labeled:
  real sessions are 100–800k tokens and unlabelable wholesale; and the window
  is exactly the pipeline's processing unit (S1 gates per 16k-token window),
  which keeps the eval *causal* — gold says what a correct extractor would
  return for the same input the pipeline sees.
- **Session-derived quantities** (amplification, node budget, empty-session
  rate) aggregate window metrics to the session level using the session's
  real turn count.
- **Baseline to beat:** regex mode (`hosted_api.py:1451-1660`): 88% noise,
  ~160 nodes/turn, ~16k nodes/session, 0 relations, 0 entities. The harness
  runs regex on the same gold windows and scores it with the same suite — the
  delta table is the headline proof.

---

## 1. The gold set (`tests/extraction_eval/gold/`)

### 1.1 Source pool

33 real sessions on disk, ~42 MB of pi JSONL:

- `~/.pi/agent/sessions/--Users-danielospina-Documents-GitHub-tortoise--/` — 21 sessions, 42 MB (the dominant workload)
- `~/.pi/agent/sessions/--Users-danielospina-Documents-GitHub-agent-infra--/` — 5 sessions
- `~/.pi/agent/sessions/--Users-danielospina-Documents-GitHub-premise-labs--/` — 3 sessions
- `~/.pi/agent/sessions/--Users-danielospina-Documents-GitHub-agent-infra-pi-bootstrap--/` — 2 sessions
- `~/.pi/agent/sessions/--Users-danielospina-Documents-GitHub-autocast-project--/` + `--Users-danielospina--` — 2 sessions
- `~/.tortoise/session-events/*.jsonl` — 787 `pi-session-quit` capture events (conversation + metadata: team, projectRoot, charCount, PRs) — the exact capture substrate the pipeline ingests.

Sessions range 248 KB–4.7 MB (≈100k–800k tokens); the tortoise dir alone
contains the 5-hour, 4.7 MB planning monster and dozens of focused dev
sessions — a realistic difficulty gradient.

### 1.2 Sampling (v1 target: 30 labeled windows)

Stratified sample across four axes, biased to the real distribution:

| Stratum | Windows | Source |
|---|---|---|
| Tortoise dev (heavy, realistic) | 10 | tortoise sessions, mixed lengths |
| Research/planning prose | 6 | tortoise + premise-labs long sessions |
| Agent-infra ops (short, tool-heavy) | 6 | agent-infra + bootstrap |
| Variety (autocast, ~/ root, capture-events-only) | 4 | remaining dirs + `session-events` |
| **Routine/academic low-value** (under-extraction test) | 4 | any — *selected for expected "nothing"* |

**Selection rules (hard):**
1. **No cherry-picking by expected quality.** Windows are selected by
   metadata (length, project, turn-type mix) *before* reading content.
   Content readability is checked only for labeling feasibility (encoding,
   code dumps), not value.
2. ≥10 distinct real sessions represented; no more than 3 windows from one
   session.
3. Length mix: ≥8 windows under 30 turns, ≥8 over 60.
4. Each window records `{session_id, turn_start, turn_end, char_start, char_end}`
   so the extractor is fed *exactly* the same bytes as the annotator saw.
5. The 4 low-value windows are deliberately drawn from sessions that look
   routine (tool output, acks, short fixes) — they are the "extract nothing"
   honesty check. **It must be possible for the annotator to say nothing.**
6. **Pre-fill policy:** dev-set windows may be pre-filled with the current
   best extractor's candidate keeps for annotator editing (cuts labeling time
   ~60%). Test-set and agreement windows are labeled **from scratch** — no
   pre-fill, no extractor output visible. This keeps the held-out truth
   independent of the thing being measured.

### 1.3 Splits

| Split | Windows | Use | Touched by |
|---|---|---|---|
| `dev` | 18 | Prompt iteration, threshold tuning, calibration fitting | Every iteration |
| `val` | 6 | Bump decisions (extractor/prompt/model gates) | Every bump |
| `test` | 6 | v1 acceptance + every release; calibration evaluation | Acceptance/release only |

Test windows are byte-identical copies held in git; touching them outside an
acceptance run is a process violation (review-gated).

### 1.4 The labeling task

Per window, the annotator produces exactly what a correct extractor would:

1. **Segment labels** — every segment (S0-level sentence utterance) is marked
   `keep` or `drop`, and `drop` is annotated with a reason bucket
   (`transient`, `tool-output`, `boilerplate`, `restatement`, `no-commitment`).
   This is the gate-level ground truth.
2. **Points** — for each `keep` segment: normalized claim content, `pointKind`
   from the pack vocab (`known_kinds()`, `tortoise/domain_loader.py` +
   pack manifests), `aboutEntities` (names), `confidence` 0–1 (rubric-anchored,
   §1.6), `reason` ∈ {NEW, REVISES, CONNECTS, RESOLVES}, and the exact span.
   Empty per segment is legal.
3. **Entities** — names + `subjectKind`/`objectKind` from vocab + whether the
   name passes the ≥2-segment frequency gate (the gold records the gate
   outcome so entity evaluation is not coupled to S0's segmentation).
4. **Operators** — IMPL/NAND/REPHrASE between kept points (endpoint pair +
   gate), quote-backed.
5. **Window verdict** — `nothing: true|false`. **`nothing: true` is a
   first-class answer**, not an escape hatch: it means "nothing durably
   valuable in this window per the value brief."
6. **Founder flag** — `highValue: true|false` per point (confidence ≥0.7
   AND durable). Feeds the weighted FN-rate and the calibration strata.

Schema: Appendix A. Format: one JSON per window plus `manifest.json`
(source session, turns, annotator, status, agreement pairs).

### 1.5 Who labels, and agreement

- **Primary/adjudicator: the founder.** The product's value standard is the
  founder's. Solo-first reality: v1 gold is founder-labeled; there is no
  second human available, and pretending otherwise would be fiction.
- **Second annotator: frontier model as judge** (Claude Opus-class or
  DeepSeek R1) with the *same rubric and schema*, run on the 6-window
  agreement sub-sample (4 dev + 2 test). This is the
  LLM-as-judge-vs-human agreement measurement, reported explicitly.
- **Agreement metrics** (computed on the 6 agreement windows):
  - κ (Cohen's) on segment keep/drop — **target ≥0.60 per window**.
  - Point-content match rate via the §3.1 matching protocol —
    **target ≥0.65**.
  - `nothing` verdict agreement — must be ≥80% (both say nothing or both
    don't). Disagreement on `nothing` is the loudest rubric-failure signal.
  - **Decision rule:** κ <0.50 on any window → the rubric or value brief is
    under-specified → revise both, re-label that window, before growing the
    gold set. We do not ship an eval whose ground truth is not reproducible.
- **Inter-annotator note:** with a single human adjudicator there is no
  human-human κ; the model-human agreement sub-sample is the honest proxy,
  and the report must state this limitation (we benchmark against the
  founder's standard, not a committee's).

### 1.6 The confidence rubric (shared with the pipeline)

The pipeline's rubric, made explicit so annotator and model self-assessment
are the same scale:

| c | Meaning |
|---|---|
| 0.90+ | Explicit unambiguous assertion; authoritative speaker; directly grounded |
| 0.70–0.90 | Clear assertion with hedging or second-hand status |
| 0.50–0.70 | Implied, inferred, or contested |
| <0.50 | Speculative; keep only as tagged low-confidence hypothesis |

Annotators assign confidence with this table visible; the judge gets the same
table in-system. Confidence is *part of the gold* so calibration has targets
(§4), not just ECE on predictions.

### 1.7 Cost and timeline

- **Founder labeling:** 30 windows × 45–75 min (pre-filled dev windows ~20–30
  min) ≈ **25–35 hours** spread over 2–3 weeks, batched in 2-window sessions
  to keep the standard stable.
- **Judge labeling:** 30 windows × ~$0.15–0.50 (30k-token window, frontier
  model) ≈ **$5–15 one-time**.
- **v1 gold shipped at:** ≥24 windows (18 dev + 6 val or test) for the first
  bump gate; full 30 required for v1 acceptance (§6). Gold growth target:
  +10 windows/quarter from real sessions, always through the §1.2 rules.

---

## 2. Metric suite (`tests/extraction_eval/metrics.py`)

### 2.1 Matching protocol (the only place semantics live)

Extracted vs gold items match at three levels, scored greedily (best match
first, one gold item per extracted item):

1. **Gate level** (segments): binary keep/drop agreement. Trivially
   computable; the extractor's effective `drop` = S0 filtered + S1 rejected.
2. **Point level:** match iff
   `embed(content_e, content_g) ≥ 0.90` (existing `embeddings.py` stack,
   sentence-transformers; the same cosine that S5 grounding uses) **and**
   `pointKind(e) == pointKind(g)` → score 1.0; kind mismatch with embed ≥0.90
   → score **0.5** (half credit: right claim, wrong kind — a real failure
   mode worth measuring, not a miss); else 0. Span overlap ≥50% required for
   the match to anchor (provenance sanity — a matched claim must come from
   the right part of the window). No gold item may be consumed twice.
3. **Operator level:** match iff `(src, dst, op_type)` all agree (endpoint
   pair with gate) — the `bench_gold.py` with-gate measure. Endpoint-only is
   reported separately.

### 2.2 Core extraction metrics

Per window, then **macro-averaged** over windows (each window equal weight —
a 200-turn session must not drown the 20-turn ones; micro-averaged values are
reported alongside):

- `P`, `R`, `F1` — point-level (2.1.2), **raw** (all S2 output) and **live**
  (post draft-gate, §4.3). Live is what the graph actually receives; raw is
  what the extractor is capable of. Both must hit targets — raw guards against
  "the draft queue is doing all the work".
- Per-kind P/R/F1 for the top-4 kinds in the gold (by frequency), aggregate
  "other" as one bucket. Decisions vs observations calibrate differently —
  hiding them in the aggregate hides the failure.
- `Entity P/R`: matched (normalized name + kind) entities vs gold entities,
  restricted to entities passing the frequency gate (gold-recorded). Kinds
  validated against `known_kinds()` — out-of-vocab entity kind = automatic FP.
- `Operator P/R`: IMPL/NAND/REPHrASE, with-gate and endpoint-only. NAND gets
  its own precision row (over-minting NAND is an argument-graph pollution
  risk; NAND P target is the strictest).

### 2.3 Extract-nothing metrics (the new ground)

- **Empty-window rate** `e = |{w : E_w = ∅}| / |W|` — target band **20–40%**.
  <15% for 4 weeks ⇒ the gate is not gating (drift alert). >50% ⇒
  under-extraction hypothesis (checked via FN-rate, never assumed).
- **High-value-session empty rate** — empty rate *restricted to windows the
  founder flagged as value-heavy* (`highValue` points present in gold):
  target **<15%**. This kills the lazy-gate failure mode: a gate that empties
  everything satisfies the 20–40% band but fails this sub-target. The two
  bands together pin the gate to *real* discrimination.
- **False-negative rate (gold):** `FN = 1 − R` on the point level, and
  **high-value FN-rate** `FN_hv = missed highValue gold points / gold highValue
  points` — target **≤10%**. Missing a durable decision is the most expensive
  failure the product has; the raw recall can hide it if the gold is full of
  marginal claims.
- **False-positive session rate (gate integrity):** fraction of gold-`nothing`
  windows on which the extractor emitted anything — target **0%**, fail-closed
  at >20% (this is the *measurable* face of "extract nothing is a real
  output": if the extractor cannot see nothing, nothing is not real).
- **Live FN-rate (1-in-N judge):** §4.4 — target **≤15%** with the §1.5
  agreement caveat recorded in the report.

### 2.4 Degradation and budget metrics

- **Keep-ratio** (per window): kept segments / S0-candidate segments. Target
  **5–25%**; fail-closed >40% for ≥3 consecutive windows (pipeline spec) —
  the eval adds the *measurement*: distribution + P90, alert on 4-week drift
  of the median >±5pp.
- **Amplification KPI** `A = non-episodic new nodes / session turns` —
  non-episodic = Points (kept) + entities + operators + warrants, post-dedup
  (S5 outcomes recorded in the audit log). Target **≤0.15** vs regex **1.6**
  (a >10× cut; the headline number in every eval report).
- **Node budget:** non-episodic nodes/session — median **≤15**, P95 **≤25**,
  hard ceiling 50 (`MAX_VALUE_POINTS_PER_SESSION`, `tortoise/quota.py`
  family). Episodic baseline (~100 turn nodes) excluded and reported
  unchanged.
- **Cost budget:** LLM $/session **≤$0.06** (spec: ~$0.04 typical), frontier
  calls **≤3/session** (`extracted_by` stratification separates model tiers).
- **Graph utility (lagging):** 30-day retrieval hit rate of kept Points via
  `search_engine.py` / `memory_orchestrator.py`. Zero-hit rate >60% over a
  month ⇒ gate over- or mis-gating (alert, §7).

### 2.5 The delta table (shipped with every eval report)

| | Regex (baseline) | Value-first target |
|---|---|---|
| Non-episodic nodes / turn | 1.6 | ≤0.15 |
| Non-episodic nodes / session | ~16,000 | ≤25 (median 15) |
| Noise (FP share) | 88% | FN-rate ≤10%, FP-session 0% |
| Relations | 0 | IMPL/NAND P ≥0.60 |
| Entities | 0 | P ≥0.80, R ≥0.65 |
| LLM $/session | ~0 | ≤$0.06 |
| Extract-nothing | impossible | 20–40% empty, first-class |

---

## 3. Confidence calibration loop

### 3.1 Problem and target

LLM self-assessed confidence is uncalibrated (verified research): on
specialist/conversational text, extraction precision is ~59–73% while models
emit mean confidence ~0.8. Uncalibrated confidence poisons (a) the draft/live
gate (0.6 floor is meaningless on the wrong scale), (b) `confidence_to_prior`
(`tortoise/ep.py:403` — extractor confidence maps directly to the Beta
prior), and (c) every downstream EP confidence that belief propagation
consumes.

**Target: ECE ≤0.10 on the test split**, Brier score reported.

### 3.2 The loop

1. **Collect (c_llm, verdict) pairs.** Every gold window contributes gold
   points with founder confidence; the extractor's predictions on the same
   window contribute c_llm. The 1-in-N live judge loop (§4.4) adds
   production pairs continuously. Founder-approval events (the
   `humanApproval` pattern, `docs/research/2026-08-07-human-approval-...`)
   become the high-trust strata.
2. **Fit a per-model remap** `c_cal = f(c_llm)` — isotonic regression
   (primary; Platt if n < 100) fit on **dev-pool pairs only**, stratified
   **per-kind** where n ≥ 20 (decision claims calibrate differently from
   observations). Store the remap as a versioned table in the model config
   (`tortoise/models.py` config block): `calibration: {model, kind, table,
   fitted_on: gold_v1_dev+judge_n, version}`.
3. **Evaluate on held-out** — ECE and Brier computed on **test-split**
   predictions using c_cal. Fitting-set ECE is never reported (it is ~0 by
   construction).
4. **Draft/live gate uses c_cal, not c_llm.** Pipeline floors — claim floor
   mean ≥0.6, variance ≤0.04 — are applied to the *calibrated* confidence:
   `c_cal ≥ 0.6` → live write; `0.5 ≤ c_cal < 0.6` → draft queue (never
   dropped); `c_cal < 0.5` → tagged low-confidence hypothesis. The gate
   boundaries are pinned by §6 acceptance (≥95% of live points must sit
   ≥0.6 c_cal), so a drift in calibration is visible as a compliance drop
   before it is visible as wrong beliefs.
5. **Per-model remap on every model bump.** A new model is not just a config
   change — it is a new calibration curve. Bump protocol: full gold run →
   re-fit remap → ECE check → then and only then does the §5 gate pass.
6. **Guarding the judge:** the 1-in-N judge output feeds calibration only
   through the §1.5 agreement lens (κ reported); judge-only pairs are
   downweighted by agreement, never trusted at founder weight.

---

## 4. Regression harness (`tests/extraction_eval/`)

### 4.1 Layout

```
tests/extraction_eval/
  gold/v1/manifest.json + *.json      # labeled windows (§1, Appendix A)
  runner.py                           # CLI: extract → score → report vs thresholds
  metrics.py                          # §2 suite (pure functions, unit-testable)
  thresholds.yaml                     # the gate table (Appendix B) — versioned
  report/<run_id>/                    # per-run JSON + delta table + ECE curves
  README.md
```

`runner.py` protocol: read window → build the exact `SessionRequest`-shaped
input (S0-equivalent span annotation) → run the configured extractor
(`value@<version>` or `regex@<version>` baseline) → score via `metrics.py` →
compare `thresholds.yaml` → exit 0/1 with a machine-readable report. One
command, deterministic, ~$1.20/run (30 windows × ~$0.04) + embeddings
(local, free).

### 4.2 Triggers (CI: `.github/workflows/eval-extraction.yml`, PR-only per repo budget convention)

The eval job runs on any PR touching:

- `tortoise/value_extractor.py`, `tortoise/extractor.py`, `tortoise/models.py`,
  `tortoise/domain_loader.py`, `tortoise/embeddings.py`
- `packs/*/manifest.yaml` (value brief changes are prompt changes)
- `tortoise/config*`, model/provider config
- `tests/extraction_eval/**` (harness self-changes)

Model bumps that are config-only still trigger via config path. Prompt
changes live in code/manifests and trigger by construction.

### 4.3 Gates (thresholds.yaml, Appendix B)

- **Hard-fail (blocks merge):** dev point F1 **no lower than the previous
  merged run's −0.05** (regression guard — the standing bar is §6 targets),
  keep-ratio within 5–25%, empty-window rate within 20–40%, node budget P95
  ≤25, ECE ≤0.15 on val, operator endpoint P ≥0.50, FP-session rate ≤20% on
  val.
- **Val-gated bumps:** extractor architecture changes and model bumps must
  additionally hold on `val` (the dev-set bar alone overfits prompts to dev).
- **Test-gated:** v1 acceptance and every tagged release run the test split;
  §6 targets are the gate.
- **Prompt-only bumps:** dev-gated only (cheap iteration loop);
  thresholds.yaml version bumps with the run.

### 4.4 Provenance of the extractor (the auditability contract)

`extracted_by = "value@<semver>+<prompt_hash>+<model_cfg_hash>"` flows into
every Point's provenance (`tortoise/api.py:17` `provenance(...)`,
`extracted_by=` — today hardcoded `mock@0`, now versioned). Consequences:

1. **Every node in production is traceable to the exact evaluation-relevant
   config** that produced it — telemetry can stratify quality by
   `extracted_by` (§7) and detect a bad bump in days, not quarters.
2. **The CI gate and production are the same object:** a merge is the act of
   promoting `value@X` past the §4.3 gates; the provenance string is the
   proof.
3. **Rollback is a version string**, not archaeology: if telemetry shows
   degradation in `value@X`, re-point config to `value@X−1` and the
   calibration remap table follows automatically.

### 4.5 The 1-in-N live judge (production, not CI)

- Every Nth session (N=5 ⇒ ~20%; configurable) is re-scored by the frontier
  judge (same rubric/schema) on the windows the pipeline actually processed.
- Outputs: **live FN-rate** (judge-worthy items missed), live ECE pairs for
  calibration (§3.2), live keep-ratio/empty-rate at production load.
- Fail-closed guard: judge unavailable or agreement κ dropping below 0.5 for
  a week → FN-rate signal flagged `UNRELIABLE`, never silently trusted.

---

## 5. v1 acceptance criteria (the concrete table)

Gate = v1 ships when **all** rows hold on the **test split** (6 windows,
never tuned on) plus 30-day live telemetry on production.

| # | Metric | Target | Block (fail) at |
|---|---|---|---|
| A1 | Point precision (raw) | ≥0.65 | <0.55 |
| A2 | Point recall (raw) | ≥0.70 | <0.60 |
| A3 | Point F1 (raw) | ≥0.68 | <0.58 |
| A4 | Point precision (live, post draft-gate) | ≥0.80 | <0.70 |
| A5 | High-value FN-rate (gold) | ≤10% | >15% |
| A6 | FN-rate (1-in-N live judge) | ≤15% | >20% (alert; block at release) |
| A7 | Empty-window rate | 20–40% | <15% or >50% (4-week) |
| A8 | High-value-session empty rate | <15% | >25% |
| A9 | FP-session rate (gold-`nothing` windows) | 0% | >20% |
| A10 | Per-kind F1, top-4 kinds | ≥0.60 each | any <0.45 (report) |
| A11 | NAND precision | ≥0.70 | <0.50 |
| A12 | Entity precision / recall | ≥0.80 / ≥0.65 | <0.65 / <0.50 |
| A13 | ECE (test, calibrated) | ≤0.10 | >0.15 |
| A14 | Calibrated live-floor compliance (live points ≥0.6 c_cal) | ≥95% | <90% |
| A15 | Amplification (non-episodic nodes/turn) | ≤0.15 | >0.30 (alert) |
| A16 | Node budget (non-episodic/session) | median ≤15, P95 ≤25 | >50 hard |
| A17 | Keep-ratio | 5–25% | >40% ×3 windows fail-closed |
| A18 | LLM cost / session | ≤$0.06 | >$0.15 (alert) |
| A19 | Frontier calls / session | ≤3 | >5 (alert) |
| A20 | Provenance coverage (span+quote+`extracted_by`) | 100% of live Points | <98% |
| A21 | Judge agreement κ (agreement sub-sample) | ≥0.60 | <0.50 (rubric revision gate, §1.5) |
| A22 | Amplification vs regex | ≤0.15 vs 1.6 baseline (≥10× cut) | ratio >5× |

Rows A1–A3 are *honest*: research puts conversational extraction at 59–73%
precision; the gate task (S1) is easier than open extraction, and 0.65/0.70
is where a value-gated pipeline lands without the draft queue faking
quality. A4 is the user-facing number — the draft gate must lift live
precision ≥15pp over raw. A5–A9 are the under-extraction side; A9 (0%) is
the "extract nothing is real" guarantee.

---

## 6. Production instrumentation (drift detection)

Wired through `tortoise/analytics.py` / `monitoring.py` (existing `/metrics`
handler) + `alert_store.py` (GitHub/Telegram alerts, the #596 DR pattern).
Eight core signals + one integrity counter. All are **distributions, not
means**, stratified by `extracted_by` version.

| # | Signal | Definition | Alert |
|---|---|---|---|
| 1 | `extraction.keep_ratio` | kept/S0-candidates per window | 4-week median drift >5pp; >40% ×3 windows → fail-closed (event logged) |
| 2 | `extraction.empty_rate` | empty windows / windows, 7d rolling | <15% or >50% |
| 3 | `extraction.fn_rate_live` | 1-in-N judge (§4.5) | >20%; `UNRELIABLE` flag if κ <0.5 |
| 4 | `extraction.nodes_per_session` | non-episodic, median/P95 | P95 >25; hard block at 50 |
| 5 | `extraction.amplification` | non-episodic nodes / turn | >0.30 |
| 6 | `extraction.ece_live` | rolling ECE on judged pairs (c_cal) | >0.15 |
| 7 | `extraction.cost` + `extraction.frontier_calls` | $/session, frontier calls/session | >$0.15 or >5 calls |
| 8 | `graph.utility_retrieval_hitrate` | 30-day zero-hit rate of kept Points in search/memory queries | >60% (over-/mis-gating) |
| 9 | `extraction.failcloses` + `extraction.provenance_coverage` | fail-closed session count; % Points with span+quote+`extracted_by` | any fail-close increase (provider-incident signal); <98% coverage |

Signals 1–3 detect the two failure directions early (2,3 = silent loss; 1 =
re-pollution) with signal 9 as the integrity backstop. Every signal is
versioned by `extracted_by` so a bad bump is caught on its own curve before
it contaminates the aggregate.

---

## 7. Risks and decisions already made

- **Founder-only gold is a bias risk** → mitigated by the model-judge
  agreement sub-sample (§1.5) and the no-prefill test split (§1.2.6).
- **Gold staleness** → the pipeline's ontology changes (value brief); every
  manifest change re-runs the harness (CI trigger) and flags gold windows
  whose kinds are no longer in vocab for re-review.
- **Judge feedback loops** → the live judge's pseudo-gold never enters the
  gold set; it feeds calibration (downweighted by κ) and FN-rate only.
- **Harness cost is negligible** (~$1.20/run) → PR-gated per the repo's
  shared-Actions-budget convention, with caching keyed on
  `(extracted_by version, gold version)`.
- **We are leading on "extract nothing" evaluation** → the gold schema,
  rubric, and judge protocol are written to be reusable/publishable; the
  `nothing: true` verdict is a schema citizen, not a special case.

---

## Appendix A — Gold window schema (v1)

```json
{
  "gold_version": "v1",
  "window_id": "w_014",
  "source": {
    "session_id": "019fd9d1-6e22-70e4-a00f-3f8c4e58f903",
    "turn_start": 12, "turn_end": 41,
    "char_start": 12345, "char_end": 89456,
    "project": "tortoise"
  },
  "split": "test",
  "annotator": "founder",
  "segments": [
    {"seg_idx": 0, "turn": 12, "span": [12345, 12610], "text": "...",
     "keep": true, "drop_reason": null}
  ],
  "points": [
    {"seg_idx": 0, "content": "Tortoise solo cap survives ~200 value-first sessions",
     "pointKind": "statement", "aboutEntities": ["tortoise"],
     "confidence": 0.85, "reason": "NEW", "highValue": true, "span": [12345, 12610]}
  ],
  "entities": [
    {"name": "tortoise", "kind": "software", "passes_frequency_gate": true}
  ],
  "operators": [
    {"src": 0, "dst": 2, "op_type": "IMPL", "quote": "..."}
  ],
  "nothing": false,
  "annotator_notes": ""
}
```

`nothing: true` windows omit `points`/`operators` (entities may still exist —
entities alone are not "value" under the gate). Judge-produced windows carry
`"annotator": "judge:<model>"` and are stored in a separate dir
(`gold/v1/judge/`) so they can never be confused with founder truth.

## Appendix B — thresholds.yaml (v1)

```yaml
gold_version: v1
extractor_version: value@0.1.0
standards:
  point_precision_raw: {target: 0.65, block: 0.55}
  point_recall_raw: {target: 0.70, block: 0.60}
  point_f1_raw: {target: 0.68, block: 0.58}
  point_precision_live: {target: 0.80, block: 0.70}
  fn_rate_hv: {target: 0.10, block: 0.15}
  fn_rate_live_judge: {target: 0.15, alert: 0.20}
  empty_rate: {lo: 0.20, hi: 0.40, alert_lo: 0.15, alert_hi: 0.50}
  empty_rate_hv: {target: 0.15, block: 0.25}
  fp_session_rate: {target: 0.0, block: 0.20}
  nand_precision: {target: 0.70, block: 0.50}
  entity_precision: {target: 0.80, block: 0.65}
  entity_recall: {target: 0.65, block: 0.50}
  ece: {target: 0.10, block: 0.15}
  live_floor_compliance: {target: 0.95, block: 0.90}
  amplification: {target: 0.15, alert: 0.30}
  nodes_per_session: {median: 15, p95: 25, hard_ceiling: 50}
  keep_ratio: {lo: 0.05, hi: 0.25, failclose_ratio: 0.40, failclose_windows: 3}
  cost_per_session: {target: 0.06, alert: 0.15}
  frontier_calls_per_session: {target: 3, alert: 5}
  provenance_coverage: {target: 1.0, block: 0.98}
  judge_agreement_kappa: {target: 0.60, rubric_gate: 0.50}
  regression_delta_f1: -0.05
```
