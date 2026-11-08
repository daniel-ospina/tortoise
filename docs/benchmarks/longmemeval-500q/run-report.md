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

> **Annotated addition, errata discipline (§7): no number above is changed.**
> The Reader and Judge rows were repointed at the configuration the fleet
> actually runs. Both rows previously named a transport (`openrouter`) that
> holds no key on any box we run, and a judge (`gpt-4o-2024-08-06`) the owner
> ruling of 2026-10-27 replaced.

**Judge.** The owner ruled on this issue (2026-10-27): *"Judge: our own model
(`deepseek-v4-flash`), pinned to an exact snapshot id — not a floating alias …
What the benchmark is for, for now: progress against ourselves."* Re-stated
2026-11-03: *"we use deepseek … Not OpenAI, not OpenRouter. No credential
request stands."* The code now defaults to it (`tools/longmem_eval/judge.py`,
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
  official one, and the direction of that bias is unknown.
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
on every box we hold, so a default-config run raised at the named-provider
check *before loading a question* (measured on this issue 2026-10-23 and
2026-11-02: `reader.py: ValueError: model spec names provider 'openrouter' but
its key is not set`). The constant now names the DeepSeek transport. The **model
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
> to any number above (§7 discipline — the table above is untouched).

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
  `--activate-beliefs` on `deterministic`, so §1's ingest mode is `v2`). The
  v2 (LLM-extraction) 500-Q baseline — `run_protocol` step 5's documented
  mode — remains pending: it requires the 3-provider rotation for a ~40 h
  run at tens of dollars (measured: 2-Q v2 slice ≈ 40 min; #1509 pilot
  50-Q ≈ 4.7 h at 5 workers) and was NOT within this run's cost/time bound.
  The deterministic run measures the same retrieval machinery over the same
  content index; the v2 delta is the extraction leg, tracked as the #1509
  protocol follow-up.

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
  (OpenRouter key-usage delta over the run window — reader + judge flow
  through OpenRouter; caveat: delta includes any concurrent traffic on the
  key, see the receipt note).
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

## 10. Handoff to W7-b (#2106)

The comparison-table rows for this run are drafted in
`docs/benchmarks/w7a-comparison-rows-for-2106.md` (mechanism-named,
receipt-cited, §6.7 row contract) — the #2106 agent merges them into
`docs/benchmarks/comparison-systems.md` when publishing. The epic's
publication sequencing (W7-b capstone) is respected: this commit lands the
run report + the MACHINERY that builds/validates the receipt (no receipt
is claimed — the run has not executed); the comparison doc fill is W7-b's
atomic event after execution.
