---
title: "W7-a: LongMemEval 500-Q sealed run — official recall_all@5 + labeled variants + validated receipt"
type: benchmarks
issue: "#2105"
epic: "#2080"
date: 2026-09-05
doc_status: live
ownedBy: epistemic-team
aboutObjects: tortoise-longmemeval
extends: docs/planning/2026-09-01-2080-gbrain-plan.md (DM-11, §6.6, §7 E2E-8), tools/longmem_eval/README.md
---

# W7-a: LongMemEval 500-Q sealed run (issue #2105)

> **Epic:** #2080 (W7 run half). **Issue:** #2105. **Consumes:** the W2-b/W3
> baseline-gate + receipt patterns. **Feeds:** W7-b (#2106) comparison-table
> fill (rows drafted in `w7a-comparison-rows-for-2106.md`).
>
> **Anti-gaming note:** every number below is derived from the committed
> runner report (`w7a-500q.report.json`) via the committed computation
> (`tools/longmem_eval/w7_publish.py`, tested in
> `tests/test_w7_publish.py`). The receipt is machine-built and validated —
> no hand-authored numbers, no n=1 smoke substitute, no variant mixed into
> the official number (R12). The judge pin is **derived from the judge the
> report records** (`judge_pin_for`), never minted as a constant: the official
> pin belongs to `gpt-4o-2024-08-06` over any transport, a stub records the
> mock pin, any other model records `unpinned:<model>`. The `verdict` is
> derived from that **same** identity, so the receipt is self-consistent
> rather than merely refused downstream: only an official-pinned completed
> run records `verdict: pass`, and a mock- or substituted-judge run records
> `inconclusive`. `validate_receipt` additionally refuses a pin that does not
> belong to the recorded judge, and a `pass` from any non-official judge or
> from any non-completed run — on **every** `run_status`, so a hand-written
> receipt cannot claim either by declaring itself `failed`/`skipped` (`#8486`).
>
> **RUN STATUS (this commit): PENDING — full-run NOT executed.** The 9-step
> run_protocol gates 1–4 (corpus seal, answer-key manifest, dataset_audit
> divergence record, judge pin) PASSED with evidence (see the protocol
> state file). Step 5+ (the full 500-Q execution + judge calls) was NOT
> run before this machinery commit: it is a ~470-question retrieval +
> judged-QA compute run with a real cost footprint, executed under the
> epic's BPRE/HARD_STOP discipline as a separately scheduled operation.
> No metric values, no receipt are claimed in this commit — the results
> table below states PENDING explicitly. Filling it is an annotated update
> (errata discipline), never a silent edit.

## 1. Run summary

| Field | Value |
|---|---|
| Benchmark | LongMemEval-S cleaned (`xiaowu0162/longmemeval-cleaned`, official 500-Q set) |
| Run | `run_protocol` step 5 (full 500-Q), resumable state machine |
| Ingest mode | **`v2`** — the production extractor (statement points + IMPL/NAND operators), i.e. `run_protocol` step 5's documented mode. **SUPERSEDES the `deterministic` record this row carried before #8486**: the belief layer is armable ONLY on a `v2` graph (the runner **refuses** `--activate-beliefs` with `--ingest-mode deterministic` — `parser.error` at the CLI, `ValueError` for programmatic callers), and the #8486 protocol injects `--ingest-mode v2 --activate-beliefs` on every run step, so a `deterministic` step-5 command is now unrunnable. The deterministic raw-content mode (turn points + raw chunks, no operators) remains the historical variant documented in §3; it cannot carry a belief layer. |
| Belief layer | **`inert` — not activated.** The belief-activation arm (`tools/longmem_eval/run.py --activate-beliefs` → `ep_activation.activate_beliefs`, `#3011`) is now wired into the runner and armed by the `#8486` run protocol; the step-5 run MUST arm it. Reasons it is not optional: `ingest_v2` writes every extraction point `status="draft"` and mints its operators with `promote_source=False`, and `tortoise/ep.py` filters the factor graph with `_live_only(..., include_draft=False)` — so an un-activated graph is **EP-inert** and every claim reads the neutral `0.50` of a Beta(1,1) prior that was never measured (`#2598`). **The four resolved states — and the bar in terms of them** (`tools/longmem_eval/measurement_posture.py::belief_layer_state`, the ONE place they are derived): `activated` = the arm ran **and every question carries a measured posterior** (its post-pass census found at least one non-operator point with `points_with_ep > 0`); `partial` = the arm ran but not every question does — a resumed outcome with no manifest, an activation that failed, or, in the common case on this benchmark, a question whose extraction minted no IMPL/NAND operators, so `sdk.dream` returned with zero belief writes (`no_op_reason = "no_ep_factors"`, and the #1163 stale-run stand-down — single-session questions are frequently this shape); `inert` = the arm was not requested; `unknown` = no posture was ever recorded. **`partial` is the state a correctly-armed step-5 run is expected to report**, so the disclosure bar is PER QUESTION, never all-or-nothing: a `partial` number may be described as measuring Tortoise's reasoning **for the N of M questions whose claims carry a measured posterior** (`posture.activation.questions_with_ep` of `posture.activation.questions`, with `beliefs_activated: false`), and must NOT be described as belief-activated as a whole. Only `activated` licenses the unqualified description (`#8486`). Both halves are carried mechanically: the report's `methodology.measurement_posture` records them, and `w7_publish.validate_receipt` **refuses** a `completed` receipt whose posture is absent or `unknown` (it accepts `partial` — which is why the state has to be defined here, on the surface that states the number). |
| Reader | `deepseek:deepseek-v4-flash` (M5 pinned default, `reader_pinned: true`) — see §1.1 |
| Judge | `deepseek:deepseek-v4-flash` — **INTERNAL BASELINE SERIES, not comparable to any published LongMemEval figure**; receipt records `unpinned:deepseek-v4-flash` + `verdict: inconclusive` **by design** — see §1.1 |
| Backend | real FalkorDB (docker `:falkordb@localhost:6379/tortoise_lme_w7`) — FTS + vector + hybrid RRF |
| Embedder | `BAAI/bge-small-en-v1.5` (sentence-transformers, local — no API cost) |
| Commit (exact) | `PENDING — machinery commit; the run's exact commit recorded at execution` |
| Corpus hash | `sha256:d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442` (verified vs the official split pin) |
| Protocol state | `.longmemeval_cache/run_protocol_state.json` (gates 1–4 passed with evidence notes; step-5 record) |
| Cost | PENDING — no run executed; the run window's provider key-usage delta recorded at execution (see §6) |
| HARD_STOP_USD | 10.00 (pre-registered) |

### 1.1 Judge and reader — annotated (#8486, 2026-11-07)

> **Annotated addition, errata discipline (§9; publication discipline, #2106 §7):
> no number above is changed.**
> The Reader and Judge rows were repointed at the configuration the #8486 ruling
> names. Both rows previously named a transport (`openrouter`) that
> holds no key on the box this audit measured, and a judge (`gpt-4o-2024-08-06`)
> the owner ruling of 2026-10-27 replaced.

**Judge.** The owner ruled on this issue (2026-10-27): *"Judge: our own model
(`deepseek-v4-flash`), pinned to an exact snapshot id — not a floating alias …
What the benchmark is for, for now: progress against ourselves."* The owner
re-stated it on 2026-11-03 — *"i have already mentioned multiple times that we
use deepseek and that i want to complete the p0 and p1 issues before doing an
eval"* — and the decision comment that records that answer draws the
consequence in its own words (this is the comment's reading, not a quotation):
the model is DeepSeek, not OpenAI, not OpenRouter, and no credential request
stands. The code now defaults to it (`tools/longmem_eval/judge.py`,
`DEFAULT_JUDGE_MODEL`), and `OFFICIAL_JUDGE_MODEL` keeps the official identity
named and reachable.

**What a run of this configuration may and may not claim.**

- The receipt will read `judge_pin: unpinned:deepseek-v4-flash` and
  `verdict: inconclusive`. **That is not a failed run.** It is the gate
  correctly saying *"this is not the official baseline"* — the two fields are
  derived from the judge that graded (`w7_publish.judge_pin_for`, an
  allow-list, never a default), so they cannot say anything else.
- **Describe it as an internal baseline series.** Never "the LongMemEval
  number", and never beside a vendor figure (HydraDB 90.79 %, mem0's own
  numbers) as if it were on the same axis. No comparison to a vendor number is
  legitimate unless that vendor discloses its reader *and* judge.
- **Reader and judge are the same model**, so the model grades its own
  answers. Recorded as a limitation, not a blocker: the bias is roughly
  constant between runs, so a *trend* survives it, but it can flatten exactly
  the gains the benchmark is meant to show.
- The rubric is unchanged (`judge_rubric_id_hash 82d07b0de05daa48`, the
  verbatim `evaluate_qa.py` anscheck prompts). The **grader** is not the
  official one, and the substitution is **not neutral**. Two measurements in the
  #8486 record say how it errs. On its **16 scored triples** the #8486 fitness
  probe (2026-11-02, `deepseek-v4-flash`) agreed with the expected verdict
  16/16 with **0 over-credits**; a **separate #1949 near-miss probe in the same
  comment** — gold `"University of Melbourne in Australia"` vs the hypothesis
  `"University of Melbourne."`, which gpt-4o grades *no* and which
  `NEAR_MISS_GRADING = "strict"` also grades wrong — then came back **3/3
  over-credited for `deepseek-v4-flash`** (and 3/3 for `deepseek-v4-pro`; 6/6
  across the two models). At the near-miss rate `judge.py`'s #1949 decision
  record carries (**1/50 in reval3** — the probe's own basis for the estimate,
  not a rate the probe measured) that is ≈ **+2 points, wrong→right: an UPWARD
  bias** — the substituted grader can make this run's number look *better* than
  the official configuration it stands in for. Its direction on the remaining
  classes is unmeasured (the probe covered those with clearly-correct and
  clearly-wrong hypotheses only).
- **The judge's request body also gains one field**, for the same reason the
  reader's does: the judge shares the reader's transport declaration
  (`judge.py` → `self.disables_thinking = api_key_env in
  THINKING_BY_DEFAULT_KEY_ENVS`), so a `deepseek:` spec adds a top-level
  `thinking: {"type": "disabled"}` to each grading call. The official call
  shape is otherwise reproduced verbatim (one user message, no system message,
  `n=1`, `temperature=0`, `max_tokens=10`, no `response_format`); the `thinking`
  field is the single deviation, and it exists so the grader does not spend its
  10-token answer budget on hidden reasoning. Stated here because a run graded
  under the official model does **not** send it — so the two configurations
  differ by one request field as well as by model.
- **The official judge is still servable**, and it takes BOTH the spec and
  that provider's key: name it
  (`TORTOISE_LME_JUDGE_MODEL=openrouter:openai/gpt-4o-2024-08-06` with
  `OPENROUTER_API_KEY`, or `openai:gpt-4o-2024-08-06` with `OPENAI_API_KEY`).
  Holding a key alone does NOT select it — the default is our own model, so a
  box with only `OPENAI_API_KEY` still grades nothing until the spec names it
  and `build_judge` fails closed and says so. That configuration is the only
  one whose number may be compared to a published figure, and running it is a
  legal configuration rather than a lane's decision.

**Reader.** The M5 pin (#1525) exists so the code default equals what runs use.
It named `openrouter:deepseek/deepseek-v4-flash`; `OPENROUTER_API_KEY` is absent
on the box the #8486 credential audit measured (2026-11-02 — one box and
worktree, not a fleet census), so a default-config run raised at the
named-provider check *before loading a question* (also observed 2026-10-23:
`reader.py: ValueError: model spec names provider 'openrouter' but its key is
not set`). The constant now names the DeepSeek transport. The **model
is unchanged**; the transport moved, and a box holding only another
provider's key uses the documented override (which records
`reader_pinned: false` rather than silently reporting a different endpoint as
the pin).

Two things moved with it, and only one of them is the endpoint. The reader
class is a function of the **key env**, not of the model id
(`reader.py` → `transport = _ThinkingDisabledTransport if key_env in
THINKING_BY_DEFAULT_KEY_ENVS else OpenAICompatModel`, with
`THINKING_BY_DEFAULT_KEY_ENVS = {"DEEPSEEK_API_KEY"}`), so under the new pin
the request body also gains a top-level `thinking: {"type": "disabled"}`.
That is required, not incidental: a reasoning-by-default provider would
otherwise spend the bounded answer budget on hidden reasoning instead of the
answer. Recorded here because a pinned instrument is only reproducible if the
request shape is disclosed with it.

## 2. Official metric + labeled variants (R12: never mixed)

The OFFICIAL number is `recall_all@5` — mean over non-`_abs` questions of
the binary `all(doc in recalled_docs for doc in correct_docs)` (the official
`print_retrieval_metrics.py` + `eval_utils.evaluate_retrieval` semantics;
asserted against the official evaluator in `tests/test_w7_publish.py`).
The harness's per-question `session_recall@k` is an exact fraction over the
question's answer sessions, so the official binaries are exact projections
(`recall_all == (fraction == 1.0)`, `recall_any == (fraction > 0)`) — never
approximated, never any-hit presented as official.

| Metric | Value | n | Label / semantics |
|---|---|---|---|
| **`recall_all@5` (OFFICIAL)** | PENDING | —/470 | `all(doc in recalled_docs …)`, non-_abs — no run executed |
| any-hit R@5 | PENDING | — | LABELED VARIANT (NOT official) — gbrain 97.6% erratum semantics |
| per-question fraction @5 (non-_abs) | PENDING | — | LABELED VARIANT D1+D3 (paper-aligned exclusion) |
| per-question fraction @5 (`_abs`-inclusive) | PENDING | — | LABELED VARIANT D1 (legacy) |
| turn-level recall @5 (paper keys) | PENDING | — | LABELED VARIANT D2 (assistant-role turns included) |
| QA accuracy (**official rubric**; judge model per §1.1) | PENDING | —/500 | end-to-end reader accuracy — NOT a retrieval metric (§3) |

The metric-formula machinery (official-vs-any-hit projection, `_abs`
exclusion, variant separation) is COMMITTED and tested in
`tests/test_w7_publish.py` — the formulas are validated against synthetic
rows today; the real numbers land with the executed run (annotated
update).

### 2.1 Per-category gate rows + type-stratified slices (#2516, C1 of #2513) — annotated

> **Added 2026-10-15 (#2516).** This is an annotated addition, not a change
> to any number above (§9 discipline; publication discipline, #2106 §7 — the table above is untouched).

The sealed run's gate surface is the **type tail**, not just the full-mix
mean: the measured loss on the multi-session classes is invisible to an
official all-or-nothing binary averaged over all questions. Two machinery
requirements therefore now apply to step 5 (and to any later run whose
receipt is published here):

1. **Per-category rows are required on a completed receipt.** The receipt's
   `metrics` must carry `official_recall_all@5_by_category`
   (`w7_publish.category_variant_rows`) with a `full_mix` row and all three
   gate categories (`multi-session`, `knowledge-update`,
   `single-session-preference`). A gate category with no questions is
   legitimate and stays visible as `n == 0` — an ABSENT block is not
   (`validate_receipt` refuses a completed receipt without it, #2516).
2. **The pilot/confirmation slices are TYPE-STRATIFIED.** The official
   split's first 50 rows are ~100% `single-session-user`, so the historical
   `--limit 50` pilot measured a near-ceiling slice with zero diagnostic
   signal on the tail (the 2026-09-07 head-slice artifact). Step 3 now runs
   a deterministic stratified subset (`pilot_stratified_50.json`) and the
   protocol records the slice composition in the run state; a slice spanning
   fewer than 2 question types is refused.

The fact-grain bridge `answer_evidence@k` (is the answer-bearing unit inside
the ASSEMBLED reader context?) is emitted by the runner and aggregated per
category; it is reported BESIDE the official number, never instead of it
(`docs/benchmarks/comparison-systems.md` §2). The metrics table above is
unchanged: this run's numbers — including the new per-category rows — stay
`PENDING` until the run executes and the receipt is committed.

Full per-`k` table + per-question digests: `w7a-500q.variants.json`
(generated by `w7_publish.py`).

## 3. What this run does and does not measure

- **Measures:** Tortoise's graph-indexed hybrid retrieval over each
  question's history (isolated per-question corpus — the report's
  `retrieval_scope`), scored with the official LongMemEval retrieval
  semantics; and end-to-end QA accuracy via the official anscheck RUBRIC —
  graded by the run's judge, which by default is our own pinned model and not
  the official one (§1.1), so only an explicitly official-graded run is
  comparable to a published figure.
- **Does NOT measure:** why-context assembly (the `#3011` B/C/D arms), and
  it does not measure the belief layer's effect on **retrieval** — the
  ranking is hybrid RRF over FTS/vector plus the structural leg, and no
  posterior/confidence term enters the score (`tools/longmem_eval/
  retrieve.py`), so arming the belief layer changes what a claim's
  confidence READS (each claim's posterior, `ep_activation.
  read_confidence`, `#2598`), never which sessions are recalled. The reader
  model is the pinned default (deepseek-v4-flash); QA accuracy is
  reader-bound and is reported separately from retrieval.
  **Erratum (#8486):** this bullet used to assert that the eval graphs
  contain no IMPL/NAND operators, citing a `dataset_audit.py` divergence
  record. That was true only of the `deterministic` raw-content variant and is
  FALSE for the `v2` step-5 configuration in §1: `ingest_v2` writes entities,
  points, events and **operators** (IMPL/NAND via `sdk.create_operator`,
  `#1369`), and `ep_activation` has a target to promote and EP precisely
  because they exist. `dataset_audit.py` never mentioned operators, so the
  attribution is dropped — the `v2` operators ARE measured, and the
  disclosure question is the one §1's **Belief layer** row now answers per
  question.
- **Ingest-mode note (mechanism honesty):** the pre-#8486 configuration of
  this run used the runner's `deterministic` ingest (raw-content graph: turn
  points + raw chunks) — **superseded for step 5 by #8486**, which requires
  the belief layer and therefore `v2` (the runner REFUSES
  `--activate-beliefs` on `deterministic`, so §1's ingest mode and §4's command
  are `v2`). Step 5 itself has NOT been executed: the v2 (LLM-extraction) 500-Q
  run is PENDING, because it requires the 3-provider rotation for a ~40 h run at
  tens of dollars (measured: 2-Q v2 slice ≈ 40 min; #1509 pilot 50-Q ≈ 4.7 h at
  5 workers) and was NOT within the cost/time bound this machinery was approved
  for. There is no third configuration: `deterministic` is the historical
  variant, `v2` is what step 5 runs, and the v2-vs-deterministic delta is the
  extraction leg itself — tracked as the #1509 protocol follow-up. (This bullet
  previously closed by calling the v2 baseline "pending" as though `v2` were a
  future configuration rather than this run's mode; the closing clause now says
  what is pending — the RUN — and not a third mode.)

## 4. Run config (exact command + resolved knobs)

```bash
TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_lme_w7' \
python -m tools.longmem_eval.run_protocol run 5 -- \
  --ingest-mode v2 --workers 3
```

(#8486: `--ingest-mode v2` — the belief layer is armable ONLY on a v2 graph,
and the protocol additionally injects `--activate-beliefs` on every run step.
A `--ingest-mode deterministic` extra is now refused by the runner's CLI at
arm resolution, so it can no longer appear here.)

(#8486, 2026-11-07: this command carried
`TORTOISE_LME_JUDGE_MODEL='openrouter:openai/gpt-4o-2024-08-06'` — the
official judge via a transport holding no key here. The 2026-10-27 ruling made
our own pinned model the run's judge, so the line is gone: the default *is*
the ruled configuration (§1.1). Naming the official judge again is a
`TORTOISE_LME_JUDGE_MODEL` override, and it buys the only number that may be
compared to a published figure.)

(Full argv in the protocol state file's step-5 `command`.) Resolved knobs are
recorded in the report methodology: chunk_turns 2, context cap 8000,
context items 40, max chunks/session 3, k = 5/10/20, TR knobs at default,
rerank OFF (no rerank keys in the report), evidence boost OFF.

## 5. Receipt (validated)

`w7a-500q.receipt.json` — machine-built by `w7_publish.py`, validated by
`validate_receipt` (S13): run_status / verdict / failure_origin / exact
commit / corpus hash / judge pin / cost_usd. Sealed answer keys:
`w7a-500q.seal.json` (per-question sha256 over `{question_id, answer,
answer_session_ids}` + aggregate digest — a gold-only edit changes the
digest).

| Receipt field | Value (PENDING — no run executed; filled at execution) |
|---|---|
| run_status | PENDING |
| verdict | PENDING |
| failure_origin | PENDING |
| commit | PENDING |
| corpus_hash | sha256:d6f21ea9…3a442 (seal verified, gates 1-4) |
| judge_pin | PENDING — derived from the judge that graded, so no value can be entered before the run. A run of the pinned default (§1.1) will record `unpinned:deepseek-v4-flash`; the `longmemeval-official-anscheck-gpt4o-2024-08-06-v1` pin is minted only for a run the OFFICIAL judge graded |
| cost_usd | PENDING |

`w7a-500q.receipt.json` / `w7a-500q.report.json` / `w7a-500q.seal.json`
are NOT committed in this machinery PR — they land WITH the executed run
(annotated update, never a silent in-place edit).

## 6. Cost discipline (R13)

- BPRE discipline: a measured 10-Q slice (real backend, real reader/judge)
  preceded the full run (4 m 16 s, 10 Q @ 3 workers; cost + report shape
  verified) — the full run launched only after the extrapolation cleared
  the HARD_STOP bound.
- Embeddings: local sentence-transformers (no API embedding cost); the
  dataset was cached and digest-verified (no re-download).
- HARD_STOP_USD = 10.00 pre-registered; actual spend = REPLACE_COST
  (key-usage delta over the run window on the keys the run ACTUALLY uses. The
  run bills THREE lanes, not two, so a delta on ONE key is complete only if every
  lane lands there: `DEEPSEEK_API_KEY` covers `stage="reader"` and `stage="judge"`
  (both default to `deepseek:deepseek-v4-flash` since #8486), and the
  `stage="ingest"` extractor lane only while its pool resolves to
  `deepseek-direct`. ⛔ That is NOT a promise it does: with three providers
  configured the pool is a weighted rotation (venice 0.50 / openrouter 0.35 /
  deepseek-direct 0.15, `model_adapters.py::_build_routing_model`), and a
  transient failure puts that provider in a `cooldown_s` window (300 s by
  default) before it re-enters the rotation. So the ingest lane's spend must be
  SUMMED across every key it actually touched, never read off the DeepSeek key
  alone. A run that NAMES the official judge through OpenRouter adds that key's
  lane. A delta measured on a key the run did not use reports ~$0.00 for a paid
  run, and any of these deltas includes concurrent traffic on the same key.)
- The v2-extractor lane (the #1509-protocol 500-Q) is the cost/scale item
  NOT run here (§3).

## 7. Report gate evidence

- `build_report` raises `ValueError` without the `dataset_semantics_audit`
  record — verified directly (report.py publication gate, E2E-3 Precondition
  2); this report's methodology carries the audit record with verdict
  `trusted-as-documented-variant`.
- Protocol gates 1-4 evidence (corpus seal digest, answer-key manifest
  coverage, dataset_audit divergence record, judge-pin record): committed
  alongside this report in `gate-evidence.json` (a J7 auditor can verify
  without the uncommitted `.longmemeval_cache/`).
- Integrity block of the executed run's report: PENDING (filled at
  execution with the committed `w7a-500q.report.json` integrity block).

## 8. Runner fix carried by this issue

Deterministic-mode runs ≥ 10 questions aborted with `leg_dead` (watchdog):
the structural leg kind-filters `statement` points that deterministic graphs
never write, so its empty scan tripped the leg-dead arm (floors calibrated
to v2/extraction graphs). Fixed in `tools/longmem_eval/run.py`
(`_legs_degraded(structural_empty_by_design=…)` — exempts ONLY the by-design
empty shape; a structural timeout still degrades) + regression test
(`tests/test_graph_integrity_gate.py`). Without the fix the README-default
deterministic full run is unexecutable — evidence: 3 aborted 10-Q runs
(workers 3/4/8), all healthy on fts/vector, structural-empty on every
outcome.

## 9. Errata / annotations log

Every post-commit change to THIS report is an annotated entry here —
never a silent in-place edit (publication discipline, #2106 §7):

| Date | Entry |
|---|---|
| (machinery commit) | Report committed in PENDING state: protocol gates 1-4 passed (evidence below); steps 5+ (full 500-Q execution) not run. |
| 2026-10-21 | #8486 follow-up (doc reconciliation, raised by the PR's fresh-context review): §1's **Ingest mode** row now states `v2` (it still said `deterministic`, which is mutually exclusive with the adjacent **Belief layer** row — the runner refuses `--activate-beliefs` on a deterministic graph, and the #8486 protocol injects `--ingest-mode v2 --activate-beliefs`). §3's ingest-mode note and §4's exact command follow. No metric, gate or receipt value changed — the run is still PENDING. |
| 2026-10-21 | #8486 follow-up, second pass (same review): §3's **Does NOT measure** bullet dropped its claim that the eval graphs contain no IMPL/NAND operators, and its `dataset_audit.py` attribution — both are false for the `v2` configuration §1 records (the extractor writes IMPL/NAND operators, which is what `--activate-beliefs` promotes and EPs), so the bullet now names what is genuinely unmeasured (why-context assembly, and the belief layer's effect on the RRF retrieval score). §1's **Belief layer** row now defines all four resolved states and reconciles the disclosure bar with them: `partial` is the expected state of a correctly-armed run, and the bar is per question (`questions_with_ep` of `questions`), not all-or-nothing. Pinned by `tests/longmem_eval/test_measurement_posture.py` (the report must define every state the module can return and must not restate the operators claim). No metric, gate or receipt value changed — the run is still PENDING. |
| 2026-11-07 | #8486 (configuration half, PR #11493) — **seven in-place edits, recorded here per the discipline in the paragraph above**: §1's **Reader** row (`openrouter:deepseek/deepseek-v4-flash` → `deepseek:deepseek-v4-flash`), §1's **Judge** row (`openai/gpt-4o-2024-08-06` → `deepseek:deepseek-v4-flash`), §1's **Cost** row ("OpenRouter key-usage delta" → "provider key-usage delta"), §2's **QA accuracy** metric row ("official anscheck judge" → "**official rubric**; judge model per §1.1" — the rubric is unchanged, the grader is not), §3's **Measures** bullet (the same correction in prose: the anscheck *rubric* is official, the model that applies it is the run's judge), §5's receipt `judge_pin` row (the pre-filled official pin → `PENDING`, with the value a run of the pinned default will record — `unpinned:deepseek-v4-flash` — named in the cell and derived from the judge that graded), and §4's step-5 command (the `TORTOISE_LME_JUDGE_MODEL='openrouter:openai/gpt-4o-2024-08-06'` line removed — the default is now the ruled configuration). The disclosure of what a run of that configuration may and may not claim is **§1.1**, added in the same change. No **observed** metric, gate or receipt *value* changed — `run_status` is still PENDING, every number is still `PENDING`, and the only receipt cell that moved went from a pre-filled value to `PENDING` (an entered value would be a claim no run has earned). The historical 2026-09-04 gate-4 receipt pin in `gate-evidence.json` is retained untouched with an `errata` entry naming the ruling that supersedes it. |
| 2026-11-07 | #8486, second pass (raised by a fresh-context review of this PR) — **one more in-place edit, recorded here per the same rule**. §6's spend bullet still planned the run as an *OpenRouter* key-usage delta ("reader + judge flow through OpenRouter"); under the configuration §1 records neither lane touches OpenRouter, so a paid run would have recorded ~$0.00 — it now names the keys the run actually uses, with the concurrent-traffic caveat unchanged. No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, third pass (fresh-context review of the delta) — **one in-place edit**. §6's spend bullet enumerated two of the run's THREE billed lanes: `run.py` declares `stage="reader"`, `stage="judge"` and the `stage="ingest"` extractor lane to the hard stop, and the extractor's pool (`_chain("deepseek-direct", "openrouter", "venice")`) can fall off DeepSeek on a box whose only key is OpenRouter's — which the runbook's own strong-reader config requires. The bullet now names all three and the condition, and its dangling "see the receipt note" pointer is gone (no such note exists in §5). No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, fourth pass (fresh-context review of the third) — **one in-place edit**. §6's three-lane bullet gave key absence as the only way the `stage="ingest"` lane leaves `DEEPSEEK_API_KEY`; two paths are not key-driven — a box holding three providers runs a WEIGHTED rotation (venice 0.50 / openrouter 0.35 / deepseek-direct 0.15, `model_adapters.py::_build_routing_model`) while `resolve_extractor_provider` still reports `deepseek-direct` as primary, and a transient failure only cools that provider for `cooldown_s` (300 s default) before it re-enters the rotation — forward-stickiness belongs to `RoutingModel`, the <3-provider path. The bullet now says the ingest delta must be summed across every key the lane touched. No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, fifth pass (fresh-context review of the fourth) — **no in-place edit of the report's numbers or claims; this row records a correction to the record itself**. The fourth pass amended its own errata row rather than adding one, so the row above now reads as though the fourth pass wrote two things it did not. The fourth pass actually wrote `model_adapters.py::_build_pool` and "fails the lane over and STAYS there"; BOTH were wrong — `_build_pool` is not a symbol in `tortoise/model_adapters.py` (the 3-provider pool builder is `_build_routing_model`), and the rotating pool does not stay failed-over (a transient failure cools that provider for `cooldown_s`, 300 s by default, then re-admits it; forward stickiness is `RoutingModel`'s, the <3-provider branch). The amended facts above are correct and are kept; this row is the trace that they were CORRECTED. No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, sixth pass (fresh-context review of the fifth) — **one in-place edit to §3** in this report, plus one clause in `docs/benchmarks/w7a-comparison-rows-for-2106.md`, both logged here per the rule above. §3's ingest-mode note already scoped `deterministic` correctly ("the pre-#8486 configuration of this run", "superseded for step 5 by #8486"), but it closed by saying *"The v2 (LLM-extraction) 500-Q baseline — `run_protocol` step 5's documented mode — remains pending"*, which left `v2` reading as future work in a report whose §1 ingest mode and §4 command are `v2`. It now states that what is pending is the RUN, that `v2` is what step 5 runs, and that there is no third configuration. The handoff row carried the same clause ("the v2 … 500-Q baseline remains pending — outside this run's bound") while declaring `v2` as this run's mechanism; it now says the run itself is PENDING. No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, seventh pass (fresh-context review of the PR at `393be2d5d58b`) — **four in-place edits, all in §1.1 or its annotation, none touching a number** (the count in this row is corrected by the tenth pass: this commit made four, and the two omissions are named here as (c) and (d)): (a) the "the direction of that bias is unknown" bullet now carries the measurement the issue already holds — the #8486 fitness probe's 6/6 over-credit of the #1949 near-miss shape, ≈ +2 points *upward* at the recorded 1/50 rate (**as pass 7 wrote it** — the eighth row corrects the count to 3/3 per model and the rate's provenance) — because a substituted grader that is known to inflate must not be disclosed as a bias of unknown direction beside the number it inflates; (b) the Judge paragraph's 2026-11-03 citation is split so only the owner's own words are quoted, with the recording comment's consequence stated as that comment's wording; (c) the §1.1 annotation's `holds no key on any box we run` became `holds no key on the box this audit measured`; (d) the **Reader** paragraph's `absent on every box we hold` became `absent on the box the #8486 credential audit measured (… one box and worktree, not a fleet census)` — the same single-box scoping the commit applied to `reader.py` and `tools/longmem_eval/README.md`. No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, eighth pass (fresh-context review of the PR at `3efdaf59316a`) — **four in-place edits to §1.1 and to the §1.1 annotation, none touching a number** (the count in this row is corrected by the tenth pass, which names the missing fourth as (d)). (a) §1.1 conflated two separate probes: the 16 scored triples graded 16/16 **with 0 over-credits**, and it is a *separate* #1949 near-miss probe in the same comment that came back **3/3 over-credited for `deepseek-v4-flash`** (3/3 for `deepseek-v4-pro`; 6/6 across the two models) — the seventh pass had put the 6/6 on this model alone, which double-counted the per-model evidence. (b) the 1/50 rate is now cited as the #1949 decision record's own **`reval3`** expectation that `judge.py` carries, not as a rate this probe measured (the probe's population is 16 triples plus the 3-repeat near-miss block). (c) the annotation's "errata discipline (§7)" pointer named §7 (Report gate evidence) and now names §9, where the rule lives; (d) the same annotation's `the configuration the fleet actually runs` became `the configuration the #8486 ruling names`, so the default is attributed to the decision rather than to an unmeasured census. Same pass, **inside §9**: this pass also rewrote the seventh-pass row in place (head `393be2d5d58b` → `3efdaf59316a`, and its count restated) — the silent-in-place-edit of the LOG itself, which `da33057e89` restored from `3efdaf59316a` and the ninth row records as its repair (b). Same pass, outside this report, in **five** files (this sentence named three of them until the twelfth pass completed the list, and characterised the last-listed one (`run.py`) wrongly; the fourteenth pass fixes that — see the twelfth and fourteenth rows): the fleet-wide credential quantifier was scoped to the measured box in `tools/longmem_eval/reader.py`, `tools/longmem_eval/README.md`, `docs/runbook/1987-ask-abstention-check.md` and `tests/test_longmem_reader_pinning.py`, and `tools/longmem_eval/run.py`'s `--reader-model` help dropped the census by attributing the transport to the ruling instead (`the transport the fleet runs` → `the transport the #8486 ruling names`) — the 2026-11-02 audit measured ONE box and worktree, and `reader.py` already said so two lines away. No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, ninth pass (fresh-context review of the PR at `762f6c4794`) — **this row is the log recording its own repair**, because commit `da33057e89` changed §2.1's cross-reference and RESTORED the seventh-pass row without logging either — the silent-in-place-edit class the seventh pass was itself repairing. Recorded here: (a) §2.1's "(§7 discipline" pointer now names §9, where the errata rule lives, with the #2106 §7 publication-discipline citation — matching the §1.1 fix; (b) the seventh-pass row is restored BYTE-IDENTICAL to its state at `3efdaf59316a`, so the record that a wrong 6/6 attribution once stood in §1.1 is visible again, with the eighth-pass row carrying the corrections that fixed it. The same commit's two non-report fixes ride with it, for the trace: `tools/longmem_eval/judge.py`'s docstring now says the RUBRIC is bit-identical and that comparability is the OFFICIAL model's (it had concluded that copying the official templates made published numbers "directly comparable", contradicting its own ruling eight lines below), and `docs/benchmarks/longmemeval-500q/gate-evidence.json`'s judge-pin errata now quotes only the owner's 2026-11-03 verbatim, attributing "The model is DeepSeek. Not OpenAI, not OpenRouter" to point 2 of the decision comment that records it. `tools/longmem_eval/README.md`'s env table was not a change to this report and carries no row: its blockquote rendered the rows after it as quote text, and commit `80e72e8656` restored it to one table. No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, tenth pass (fresh-context review of the merged head at `ca8a4dd4dd`) — **no edit to any number; this row records a correction to the ERRATA RECORD itself** (the cited head is corrected by the twelfth pass; `1de6a23db4`'s own message says the review was *at the merged head*). Two rows understated the work their own commit did, and since §9 exists so a reader can trace which pass moved what, a wrong count is the same silent-in-place-edit class the fifth and ninth pass rows were created for: (a) the seventh row said **two** in-place edits and `3efdaf593` made **four** — that commit also scoped the fleet-wide credential quantifier in the §1.1 annotation and in the Reader paragraph, now named (c)/(d) there; (b) the eighth row said **three** and `762f6c479` made **four** — the same annotation hunk also repointed `the configuration the fleet actually runs` at the ruling, now named (d) there. Both rows now carry the count **and** state that the tenth pass corrected it, so the correction is visible rather than the row silently reading as if it had always said four. The seventh row's superseded "6/6 … 1/50" is bracketed as *what pass 7 wrote*, with the eighth row carrying the correction. Nothing here restates the ninth row's repair (the §2.1 pointer and the seventh-row restoration); that repair and the commits it records (`da33057e89`, `80e72e8656`) are not this lane's, so their account stands as written — with the one exception this pass creates: the ninth row's (b) calls the seventh-pass row *byte-identical* to its state at `3efdaf59316a`, and the tenth pass edits that row again to carry the corrected count, so that identity now holds at `da33057e89` and not at this head (the thirteenth row records the correction). No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, eleventh pass (fresh-context review of the head at `e7f72f0d4e99`) — **no edit to any number; this row is the log recording the two most recent changes to ITSELF that carried no row of their own**. The contract in the paragraph above binds *every* post-commit change to this report, and the two commits that closed the tenth pass's findings each changed §9 and added no entry — the class the fifth and ninth rows exist for: (a) `e7f72f0d4e9` — whose own subject is "the eighth pass also rewrote the log, and the row for it did not say so" — rewrote **two rows**: the eighth-pass row gained its "Same pass, **inside §9**" sentence (the omission that commit was closing), and the tenth-pass row's `passrows` typo and its "recorded there by the box that made it" claim were replaced with the peer commits `da33057e89` / `0ab326830`; (b) `043d743733e` restored the blank line the tenth row's insertion had eaten between this table and `## 10.` (the `MD058` / `MD022` red the required `docs` job would otherwise have taken). Neither touches a claim about the run; both touch this file. Logged here rather than left to a later pass, because the row is the only trace a reader has of which commit moved which text. No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, twelfth pass (fresh-context review of the head at `f54f23806ca6`) — **no edit to any number; two corrections to the ERRATA RECORD itself, both the undercount class this PR exists for**. (a) The tenth row cited `80e72e86569` as the head it reviewed, and the ninth row it reasons about does not exist there: `0ab326830` — the commit that ADDS the ninth row — has `80e72e8656` as its parent, and the report at `80e72e8656` contains no ninth-pass row at all, while the tenth pass's own commit `1de6a23db4` says the review was *at the merged head* `ca8a4dd4dd`, which does contain it. The row now cites the merged head and says why. (b) The eighth row's "same pass, outside this report" clause named **three** files where `762f6c479` changed the quantifier in **five** — the omitted two are `tools/longmem_eval/run.py`'s `--reader-model` help, which drops the census by attributing the transport to the ruling instead (`the transport the fleet runs` → `the transport the #8486 ruling names`), and a comment in `tests/test_longmem_reader_pinning.py`, which carries the measured-box scoping verbatim ("the box the 2026-11-02 credential audit measured (not a fleet census)"). Both facts re-derived from the commits, not taken from the review's prose (the fourteenth pass corrects this row's first version, which said both omitted files carried that one scoping). Also recorded and not repaired, because a pushed commit message is not rewritten: `1de6a23db4`'s message cites `tools/longmem_eval/README.md:100` where the provider resolution order is at `:102` (the PR body carries the correct line; the correction is posted on the PR instead). No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, thirteenth pass (second fresh-context review of the head at `f54f23806ca`) — **no edit to any number; two further corrections to the ERRATA RECORD itself**, both re-derived from the commits before being applied. (a) The tenth row closed by asserting the ninth row's account "stands as written", but the tenth pass itself edits the seventh-pass row that the ninth row's (b) calls *byte-identical* to its state at `3efdaf59316a`. The seventh-pass row digests to `f825df5c…` at both `3efdaf593` and `da33057e89`, `56636b04…` at `762f6c479` and `c0d0f0b5…` at this head (all four are `md5` of the row's line including its trailing newline — named here because the report's other digests are `sha256:`, which mismatches every one of these and would make a true assertion read as false), so the byte-identity belongs to `da33057e89` and the tenth row now says so instead of declaring the ninth row untouched. (b) That same sentence named the commits the ninth row records as (`da33057e89`, `0ab326830`); the ninth row records `3efdaf59316a`, `762f6c4794`, `80e72e8656` and `da33057e89`, and `0ab326830` is the commit that WROTE it — the citation now names `da33057e89` and `80e72e8656`. Both were introduced by this lane's own `1de6a23db` and `e7f72f0d4e9`. The eighth row's three-of-five file list, the third finding of the same review, was already completed by the twelfth pass and is not restated here. No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, fourteenth pass (fresh-context review of the head at `e5427135ea`; the peer thirteenth-pass corrections are verified and not restated) — **no edit to any number; two findings across three edit sites — one in the ERRATA RECORD (two rows) and one in a `#:` comment**. (a) The twelfth row said both files it added to the eighth row's list carried *the same* measured-box scoping; `762f6c479`'s `tools/longmem_eval/run.py` hunk is the **ruling-attribution** mechanism instead (`the transport the fleet runs` → `the transport the #8486 ruling names`), so it drops the census by naming the ruling rather than by scoping the box. Both rows now name each file's mechanism separately. This is why the thirteenth row's note that the twelfth "completed" that list is true of the LIST and not of the twelfth pass's characterisation of it. (b) `tools/longmem_eval/judge.py`'s alignment citation said *the* 2026-11-02 analysis blocks state the rule; the wording is in **two** of that day's eleven blocks (a third carries `re-pin judge and reader constants (not env overrides)`), so the comment now counts them and quotes the variant. All three re-derived from the commits and the issue's comments, not taken from the review's prose. No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, fifteenth pass — the entry for its commit `94e18e7a24` (a fresh-context review of the head at `12940b34c2`) — **no edit to any number; two corrections to the ERRATA RECORD itself**. (a) The eighth row's pointer named the **thirteenth** pass, which is the peer lane's row that explicitly declines that finding ("not restated here"), so a reader following it never reached the row carrying the fix; it now names the fourteenth pass, but it still counts — it writes "the fourth-listed one (`run.py`)", an ordinal that is also wrong (`run.py` is enumerated last of the five) — and the sixteenth pass corrects that ordinal to `last-listed` (see the sixteenth row). (b) The fourteenth row's heading said "two corrections" while its closing sentence said "All three re-derived"; the row records two findings across three edit sites, and now says so. No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, sixteenth pass — the entry for its commit `c40aa8a7fc` (a fresh-context review of the head at `94e18e7a24`) — **no edit to any number; two corrections to the ERRATA RECORD itself**. (a) `94e18e7a24` resolved the eighth row's ordinal to a name but kept the wrong ordinal: it wrote "the fourth-listed one (`run.py`)", and `run.py` is enumerated **last** of the five files in that same sentence, while the fourth-listed file — `tests/test_longmem_reader_pinning.py` — is the one the twelfth row says was characterised correctly. It now reads "last-listed". (b) `94e18e7a24` had also changed §9 without an entry, so `c40aa8a7fc` added the fifteenth row above. No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, seventeenth pass — the entry for `3493d1c29b`'s two §9 changes and for this row's own addition (a fresh-context review of the head at `c40aa8a7fc`) — **no edit to any number; two corrections to the ERRATA RECORD, made by TWO commits, not one**, so §9's contract is satisfied for this change instead of for every change except the last one: (a) below is `3493d1c29b`'s, and (b), together with this row, belongs to the commit that carries it — because a row cannot cite the commit that adds it. The nineteenth row names that limit instead of leaving it implied. (a) The fifteenth row as first written, by `c40aa8a7fc`, read "fifteenth pass (fresh-context review of the head at `94e18e7a24`)" — filing the SIXTEENTH pass's reviewed head under the fifteenth pass's label, since a pass cannot review its own output commit. `94e18e7a24`'s own message names `12940b34c2`, and `3493d1c29b` re-labelled the row to "fifteenth pass — the entry for its commit `94e18e7a24` (a fresh-context review of the head at `12940b34c2`)". (b) The sixteenth row as first written, by `3493d1c29b`, called itself "the entry for the commit that makes them", attributing to one commit two corrections that two commits made — the ordinal belongs to `c40aa8a7fc` and the fifteenth row's re-label to `3493d1c29b`. It is now the entry for `c40aa8a7fc` alone, and this row records `3493d1c29b`'s two §9 changes (that re-label, and the addition of the sixteenth row) because that commit made no entry of its own. No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, nineteenth and twentieth passes — **this row is the entry for the §9 changes committed with it**, and it states NO count of the commits that carry it: it was written as the eighteenth row for `ea2805cdf`'s single change, folded in when the twentieth pass corrected it, and then corrected in place by later passes, recorded here for **what they corrected** rather than counted — `8bfd21dd7b` removed the quotation marks around `It now says which commit did which`, which `e285fbba2` had set in quotation marks as the text `ea2805cdf` wrote into the seventeenth row's header when it was this row's own paraphrase (`ea2805cdf`'s sentence is *"The header now says which commit did which"*), and `decbf146bc` dropped the count of commits this sentence had stated, replacing it with the reason no count can stand here. The removal is named here because it is not a wording preference: a fabricated quotation inside a log whose purpose is exact attribution is the defect class this PR exists to close, and a reader who goes to §9 to check that attribution would otherwise find the row reading as though it had always been unquoted. Further in-place corrections to this same row are claimed by this entry rather than given rows of their own, because a row cannot cite the commit that adds it. What it records is **`ea2805cdf`'s rewrite of the seventeenth row's header**, which now says which commit did which, **and the twentieth pass's three corrections, (i)-(iii) below — one to the seventeenth row and two to this row**. The seventeenth row said "this row is the entry for the commit that makes them and adds it". That credits one commit with two corrections two commits made — (a) is `3493d1c29b`'s, while (b) and the seventeenth row's addition belong to `9b0dc1b6e7e`, the commit carrying both — and it is the same error the seventeenth row was written to repair one row up. The fifteenth through seventeenth rows each closed that gap for the previous commit; the seventeenth claimed to close it for itself — a row cannot cite the commit that adds it, so a row claiming to be the entry for its own commit is one commit late. **Made in place, not silently — this paragraph is the annotation.** (i) This row labelled itself the *eighteenth* pass, counting the pass that MADE the edit rather than the pass whose edit it records: the eighteenth pass's change is already inside the seventeenth row, so the label would have credited it twice — hence the gap between the seventeenth and nineteenth labels, explained by this sentence and by no missing row. (ii) It said "the twelfth through seventeenth", untrue of rows 12-14, each of which added its own entry in its own commit — the one-commit-late pattern begins at the fifteenth row; `git log -S` on each row's label resolves 12/13/14 to `e5427135e`/`2de97503c`/`12940b34c` and 15/16/17 to the commit after the pass they record. (iii) The seventeenth row's pointer to this row said eighteenth and now says nineteenth. The corrections live here rather than in a further row because §9 asks for an annotated entry, and this is that annotation; a row recording this row would need a row of its own. No metric, gate or receipt *value* changed — the run is still PENDING. |
| 2026-11-08 | #8486, correction row for the fresh-context review of the head at `99382d3f95` (**deliberately unnumbered**: the pass ordinals above are themselves under repair, so this row states no count it would have to re-derive) — **two in-place corrections to the ERRATA RECORD itself, both in rows this PR's own later passes added**. (a) The fifteenth row said `94e18e7a24` named the file (`run.py`) "rather than counting it"; that commit in fact wrote "the **fourth-listed** one (`run.py`)" — it is still an ordinal, and the wrong one, since `run.py` is enumerated last of the five — which the SIXTEENTH row already said in the same table, so two rows added by this PR contradicted each other about one commit. The fifteenth row now states what the commit wrote and defers the repair to the sixteenth row. (b) The thirteenth row asserted the seventh-pass row's byte-identity across four digest prefixes without naming the instrument: they are `md5` of the row's line including its trailing newline, while this report's other digests are `sha256:`, so a reader re-deriving with the report's own convention mismatched all four and would conclude a true assertion false. The hash function and the hashed unit are now named. Both corrections are made in place and recorded here rather than in rows of their own, for the reason the nineteenth-and-twentieth row gives: a row cannot cite the commit that adds it. No metric, gate or receipt *value* changed — the run is still PENDING. |

## 10. Handoff to W7-b (#2106)

The comparison-table rows for this run are drafted in
`docs/benchmarks/w7a-comparison-rows-for-2106.md` (mechanism-named,
receipt-cited, §6.7 row contract) — the #2106 agent merges them into
`docs/benchmarks/comparison-systems.md` when publishing. The epic's
publication sequencing (W7-b capstone) is respected: this commit lands the
run report + the MACHINERY that builds/validates the receipt (no receipt
is claimed — the run has not executed); the comparison doc fill is W7-b's
atomic event after execution.
