---
title: "Plan — #2134: v2 extractor truncation on large multi-session haystacks (measurement-first + cost-bounded escalation net)"
type: plan
domain: engineering
doc_status: draft
created: 2026-09-05
ownedBy: epistemic-team
governingAgreement: "#2134 (parent #1987), #1746, #1787/#1811, #1778, #2280, #2281, #2136, #2069"
---

<!-- research-path: /tmp/2134-scope.md (scope-stage research, 2026-09-05) + /tmp/2281-scope.md §1.6 (epic #2281 write-side context) + docs/plans/2026-08-27-1787-extractor-cap-truncation.md (sibling, same domain) + docs/plans/2026-08-26-1746-parse-error-robustness.md (recovery-ladder sibling) -->

# Plan — #2134: v2 extractor truncation (measurement-first + cost-bounded escalation net)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make v2-mode ingest (`--ingest-mode v2`, the production 5-stage pipeline) stop silently truncating the S2/S4 embed list on large multi-session haystacks — via a **measurement-first** revalidation of the CURRENT 16K cap, then a **cost-bounded one-shot escalation** at the S2/S4 + S1 seams (mirroring #2280) with **permanent telemetry** so the later "bigger budget upfront vs escalate-on-demand" cost optimization is data-driven, and a **fail-loud** residual policy (partial lists never silently pass; the raw-chunk leg stands).

**Team:** epistemic-team
**Tier:** standard (`complexity:standard` — Architecture domain)

**Architecture:** Two legs, sequenced by DATA not assumption. **Leg 1 (measure):** re-run the truncation census at the CURRENT 16K cap on the #2134 recorded-failure set + a fresh mini-set (the deferred #1787 revalidation, made real) to establish the true residual and drive a go/no-go decision. **Leg 2 (escalation net):** on `finish_reason == "length"` at S2/S4 (`_complete_parsed`) and the S1 seam (`run_s1`), re-attempt ONCE at an escalated output budget (`TORTOISE_EXTRACTOR_ESCALATION_TOKENS`, default 32000, clamped [16000..64000]) before any partial-accept or raise — mirroring `_ask_reader_complete` (sdk.py:181-241) + `ask_env_int` (retrieval.py:147). Residual truncation after the one escalation **fails loud** (`truncated_parse_error`, `valid=false`), never a silent partial — the raw-chunk leg (phase-A retention) keeps the session retrievable. Permanent telemetry (escalation count, escalated-recovered, residual-after-escalation, cost delta) makes the upfront-vs-escalation cost question answerable from real runs. **Explicitly NOT built here:** the structural S4 gaps-only delta (#1778, trigger-gated), WS1 read-side ordering, question-type routing.

---

## Context

- **Issue #2134** (OPEN, parent #1987 ask-reader surface). Root cause confirmed in scope (`/tmp/2134-scope.md`): per-session `extract_session_v2` S2/S4 embed JSON exceeds `_S2_S4_MAX_TOKENS = 16000` (extractor_v2.py:3970) on dense sessions → `finish_reason == "length"` → #1746 rung-4 `_longest_valid_prefix` (:4617) partial-accepts the **head** → `partial_parse`/`truncated_parse_error` recorded but **the partial list IS used** (extractor_v2.py:3665-3673 S2, :3725-3733 S4) → tail facts dropped → gold session degrades to raw-chunk-only → reader abstains legitimately given a degraded pool (the eval confound).
- **Owner-corrected logic (2026-09-05, the LAST comment on #2134) — this plan's governing direction:**
  1. **Measure first** — the #1787 cap-raise revalidation ("≤1 `partial_parse` on a fresh 50-Q") was deferred in PR #1811 and NEVER RUN; "cap raise doesn't converge" is unproven. Re-measure at 16K on the recorded-failure set + a fresh mini-set before any budget decision.
  2. **Escalation with a durable record** — cost-bounded retry at a bigger budget (mirror #2280's `_ask_reader_complete` + `ask_env_int`) + permanent telemetry (escalation count, escalated success, residual-after-escalation, cost delta) so the later upfront-vs-escalation optimization is data-driven.
  3. **Coordinate, don't reinvent** — #1778 (OPEN, trigger-gated) owns the STRUCTURAL S4 gaps-only delta; #2134's escalation is the near-term safety net + the measurement feeding #1778's trigger.
  4. **Residual = fail-loud** (partial lists never silently pass); raw session leg stands.
- **Complexity:** `complexity:standard` → Standard tier (condensed plan, descriptive steps + explicit test names/commands).
- **Dependencies:** none blocking (no open PR touches `extractor_v2.py`/eval ingest — checked 2026-09-05). #2136 (reader None-guard crash half) MERGED — not re-scoped. #2280 (reader escalation) MERGED — the pattern to mirror.

## Current state (verified on HEAD 2026-09-05)

| Surface | Today | Problem (#2134) |
|---|---|---|
| `extractor_v2.py:3970` `_S2_S4_MAX_TOKENS = 16000` | S2/S4 stage cap (raised 8K→16K in #1787) | still truncates on 0100672e (42 sessions, 13/139 calls, #2134 2026-09-02) |
| `extractor_v2.py:996` `_PARSE_RETRIES = 1` + `_complete_parsed` (:1015) | `finish_reason=="length"` → **skip retry** ("same prompt + same cap is deterministic failure", :1074-1077) | the skip is the gap — the premise holds only at the SAME cap; a larger-budget retry breaks it |
| `extractor_v2.py:4617` `_longest_valid_prefix` + `_parse_json_robust` (:4648) | rung-4 schema-validated partial-accept → `stats["partial"]=True` | head embedded, tail dropped — the damage; escalation must fire BEFORE this |
| `extractor_v2.py:3665-3673` / `:3725-3733` | caller appends `partial_parse` + "truncated tail dropped"; partial list used | silent pool degradation; gold session loses dense leg |
| `extractor_v2.py:639-654` `run_s1` | S1 chunk summary capped at `_S1_MAX_TOKENS=1500` via `_complete` directly (NO `_complete_parsed`) | S1 truncation recorded (`llm_truncated`) but never errors — quiet tail-drop one stage upstream |
| `extractor_v2.py:4252` `_scaled_deadline` + `_complete` (:4266) | deadline scales 0.05 s/tok (`deadline_s=None` → `_scaled_deadline(600, max_tokens)`) | reusable for the escalated call's bigger budget (32K → 1600s) |
| `retrieval.py:147` `ask_env_int` | ask-lane clamped env-int template | the naming/bounds template the escalation knob mirrors |
| `sdk.py:181-241` `_ask_reader_complete` | reader-lane one-shot escalation on empty+length (fail-loud `AskReaderUnavailable`) | **the #2280 precedent to mirror on the extractor lane** |
| `extractor_v2.py:4161` `_rollup_llm` / `:4173` `_rollup_recovery` | per-stage counters roll into session stats | `_rollup_recovery` copies ALL `recovery.*` keys — new escalation counters roll up for free |
| `tools/longmem_eval/run.py:3653-3656` | outcome projects `llm_calls/llm_retries/llm_truncated/recovery` | the projection point to extend with escalation fields |
| `tools/longmem_eval/report.py:60-96` | `RECOVERABLE_CENSUS_CLASSES` (`truncated`/`truncated_parse_error`/`partial_parse` are recoverable/rate-limited) + `EXTRACTION_KILLER_CENSUS_CLASSES` (`empty_embed_list` killer) | class LISTS unchanged; but the >32K residual band's per-session class flips recoverable→killer by design (P2-5, D7) |
| `model_adapters.py:156-157` | `last_prompt_tokens` / `last_completion_tokens` captured per call | the raw material for the escalation cost delta |

## Pattern Research

> **Findings date:** 2026-09-05. **Gate skipped:** plan touches ZERO new third-party dependencies — `os`/stdlib + in-repo patterns only (the #2280 escalation shape, `ask_env_int`, `_scaled_deadline`, `_rollup_recovery`, `_stage_cap` env-lever pattern). Step B (Perplexity verification gate) does not fire per the zero-deps skip rule. Step A (prior research intake) ran: `/tmp/2134-scope.md` §1-§5 + `/tmp/2281-scope.md` §1.6 + #1787's Axis Research (model ceilings — cited, not re-researched).

- **Canonical (from #1787's Axis Research, re-verified):** OpenAI/Anthropic canonical truncation handling = **retry with a higher `max_tokens`** when `finish_reason="length"` — the #1746 skip-on-length is the documented anti-pattern for a raiseable cap; strict structured output does NOT prevent truncation. DeepSeek V4 max output 384K (the 16K cap is self-imposed, not a model ceiling).
- **Codebase precedent (#2280, the shape to mirror):** `_ask_reader_complete` (sdk.py:181-241) fires ONE cost-bounded retry at `TORTOISE_ASK_ESCALATION_TOKENS` (default 2000, clamp via `ask_env_int` retrieval.py:147) on `length`, then fails loud — never a silent abstention. The extractor analog: one retry at `TORTOISE_EXTRACTOR_ESCALATION_TOKENS` on `length`, then fail loud — never a silent partial.
- **Order-stability (no byte-identity risk):** the label-order shuffle seed derives from the story (`_label_order_rng`, extractor_v2.py:327/:384), so a same-prompt re-emission reproduces the SAME ordered list — escalation adds headroom without changing output shape on the common path (escalation never fires on non-`length` calls, so non-truncated calls are byte-identical).
- **Cost bound (recomputed from the issue's OWN 0100672e evidence, NOT the #1787 reval average):** the 2026-09-02 run observed **13/139 ≈ 9.4% of calls truncating at 16K** (7/42 sessions on the worst question) — ~40× the #1787 reval's 0.22% healthy-run rate, so "rare" is the wrong prior. Per event at deepseek-direct rates (costing.py: in $0.14 / out $0.28 per M): re-billed input (~$0.002-0.03) + escalated output ≤32K (~≤$0.009) ≈ **$0.01-0.04/event**; at the OBSERVED pathological fire-rate a 500-Q run with 0100672e-like composition lands in the **$5-25** range on top of existing extraction spend (the fire-rate is the dominant unknown — Task 1's measurement bounds it; the escalation build is cost-gated by the measurement-first discipline, NOT by a "rare" assumption). The "unlikely ~0.2%" framing is RETIRED — the fire-rate is **measured by Task 1**, never assumed.

## Integration Surface Map

| Surface | Boundary | Bug pattern | Test layer |
|---|---|---|---|
| S2/S4 escalation seam | `_complete_parsed` (:1015) ← `finish_reason=="length"` | silent tail loss; escalation fired twice (budget unbounded); partial-accept still used after escalation | unit |
| S1 escalation seam | `run_s1` (:639) ← `_complete` directly | quiet tail-drop (recorded, never errored) — same class one stage upstream | unit |
| residual policy | `_parse_json_robust` `allow_partial` param ↔ rung-4 | residual partial-accept silently embedded (`valid=false` but tail dropped) | unit |
| escalation env knob | `_extractor_escalation_tokens` (new) | garbage/out-of-range env → crash (must fall back to default); `esc ≤ base` → no-op | unit |
| deadline scaling | `_complete` `deadline_s=None` → `_scaled_deadline(600, esc)` | escalated 32K call killed by the old 600s/800s deadline | unit (assert deadline ≥ 0.05×esc) |
| telemetry roll-up | `_rollup_recovery` (:4173) + `run.py` projection (:4250-4252) | escalation counters dropped at the session/outcome boundary (cost question unanswerable) | integration |
| report readout | `report.py` warning-only line (mirror `llm_truncated`) | escalation-recovered invisible → a "clean" run with escalations indistinguishable from one with none | integration |
| adapter read-timeout + per-call token capture | `model_adapters.py:149` `timeout=(10, 60)` + `:156-157` token attrs | fixed 60s read timeout kills a legit slow 32K stream (P1-3); post-hoc adapter-attr token reads race under `--workers>1` (P1-4) | unit (concurrency) |
| census semantics | `report.py:60-96` | recoverable-vs-killer class LISTS unchanged, but the >32K residual band's per-session class flips recoverable→killer (P2-5) | unit (no-change pin + flip pin) |
| mock-mode CI safety | `CapAwareModel` test fixtures (tests/test_extractor_v2.py:2955-3099) | live-API leakage into deterministic CI (must be mock-only) | unit (CI) |

## Design decisions

### D1 — Escalation trigger + seam: fire ONCE on `finish_reason == "length"`
Trigger = `finish_reason == "length"` on the first parse attempt, regardless of parse outcome — because `length` is the deterministic signal the model exhausted its output budget, and a rung-1 "successful" parse of a truncated JSON is NOT proof of completeness (tail-cut recovery can turn a mid-list cut into a valid shorter list — the silent-loss class). Seams: S2/S4 via `_complete_parsed` (both call it), S1 via `run_s1` (which calls `_complete` directly, bypassing the ladder). Fire **once** (the `escalated` flag), never on the re-prompt attempt.

### D2 — Escalation budget: `TORTOISE_EXTRACTOR_ESCALATION_TOKENS`, default 32000, clamp [16000..64000]
New helper `_extractor_escalation_tokens(base: int | None) -> int` in `extractor_v2.py`, mirroring `ask_env_int` (retrieval.py:147) + `_stage_cap`'s inline env-read pattern (fail-open with visibility, never a crash): reads `TORTOISE_EXTRACTOR_ESCALATION_TOKENS`, default `32000`, clamp lo `16000` / hi `64000`, garbage/out-of-range → default. The caller escalates **only when `esc > (base or 0)`** — so a base cap already at/above the escalation default (e.g. `TORTOISE_EXTRACTOR_MAX_TOKENS=32000`) is a no-op (already at 32K), and a disabled/unavailable escalation falls through to the existing #1746 partial-accept path unchanged.

### D3 — Residual policy: fail-loud, never a silent partial (owner direction)
After the ONE escalation, if `finish_reason` is STILL `"length"` (residual truncation): **do NOT partial-accept — and do NOT let any ladder rung clean-parse a shorter parse.** The reviewers' gap: rungs 1-3 (`_parse_json` progressive tail-cuts at :4334, `_repair_candidates` `}`-closers at :4559, `_validate_output_shape` treating ABSENT sections as valid at :4428) can clean-parse a TRUNCATED response cut at a section boundary → a silent tail-drop with `valid=true`, no census class, and neither `escalated_recovered` nor `escalated_residual` bumped. The fix makes the invariant airtight: **a residual (`escalated and finish == "length"`) is treated as truncated UNCONDITIONALLY** — at most a strict canonical full-JSON parse is attempted (no progressive tail-cut acceptance, no sanitize/repair, no rung-4 partial), and ANY other outcome raises `_ParseError(truncated=True)` → census `truncated_parse_error`, `valid=false`. `_parse_json_robust` gains an `allow_partial: bool = True` param PLUS a strict-truncated mode that `_complete_parsed` engages ONLY in the residual case; all non-escalation paths stay byte-identical (their tests stay green). For S2 the session has no embed list → `empty_embed_list` downstream; for S4 the S2 output stands (existing "S4 failed — kept S2 output" graceful degradation). The raw-chunk leg (phase-A retention, ingest_v2.py:506-518) keeps the session retrievable. `partial_parse` therefore trends toward **zero** on the recorded set.

**Escalation accounting invariant (P1-1, P2-1):** every escalation event lands in EXACTLY one bucket — the return is never unaccounted:
`escalated == escalated_recovered + escalated_residual + escalated_abort + escalated_sloppy`
- `escalated_recovered` ⟺ the escalated call returned AND `finish != "length"` AND the final parse is a FULL list (`not stats["partial"]`).
- `escalated_residual` ⟺ the escalated call returned AND `finish == "length"` (unconditionally truncated → fail-loud).
- `escalated_abort` ⟺ the escalated `_complete` call itself RAISED (transient-after-retries / deadline kill / fatal 4xx / adapter read-timeout) — see D3b.
- `escalated_sloppy` ⟺ the escalated call returned `finish != "length"` but the final parse is STILL partial/failed after `_PARSE_RETRIES` (headroom granted, model still emitted malformed JSON) — recorded `partial_parse`/`parse_error`, NOT recovered, NOT residual. The 4th bucket makes "escalated − recovered − residual" explicit instead of a derived residual (the reviewers' airtightness point).

### D3b — Escalated call failure policy (abort bucket; the escalated call itself can raise)
The escalated `_complete` call can raise: transient 429/5xx exhausted its retries, a deadline kill, a fatal 4xx, or the UNSCALED adapter read timeout (model_adapters.py:149 `timeout=(10, 60)` — only the outer `_scaled_deadline` wall-clock deadline scales; a stalled socket read still dies at a fixed 60s). Without a policy, attempt-1's truncated response is never parsed, the recoverable head is discarded, and the census shifts `partial_parse` → `transient_*`/`empty_embed_list` KILLER for a transient failure. FIX (two parts):

1. **Fallback = re-parse attempt-1's ALREADY-CAPTURED truncated response under the OLD `allow_partial=True` path** (pre-escalation #1746 semantics): recover the head, record `partial_parse` (RECOVERABLE — not a killer), bump `escalated_abort`. If even that head parse fails → fail-loud `truncated_parse_error` + the raw-chunk leg stands. This preserves pre-fix census semantics on a transient escalation failure instead of converting it to a killer. (`_complete_parsed` retains attempt-1's `response` string — the loop's `response` variable survives the escalated call.)
2. **Scale the adapter read timeout for the escalated budget** (the "good" path, not the easy-but-brittle fixed 60s stall): thread a per-call read timeout through `_complete`/`_call_once` → `_cap_kwargs` → the adapter's `complete(timeout=...)` (new kwarg, default `(10, 60)`), set on the escalated call to `(10, max(60, deadline_s))`. A 32K emission can have >60s inter-chunk gaps on a degraded provider — the fixed 60s read timeout would kill a legitimate slow stream. Alternative (accepted bound, cheaper): document 60s as the per-read stall bound and rely on the wall-clock deadline for total duration — but record this choice explicitly, never silently.

**Wall-clock bound (compatible with run-level watchdogs):** the escalated `_complete` inherits the retry loop (default `_COMPLETE_RETRIES=2` → 3 attempts), so a transient-failing escalated call's worst case is **3 × `_scaled_deadline(600, esc)` = 3 × 1600s = 4800s per stage** — the run-watchdog-trip bound. Task 3 must either (a) pass `retries=0` on the escalated call (one attempt, then the D3b fallback — the fallback already salvages the head, an escalated retry adds little), or (b) record the accepted 3×1600s bound against the run-level watchdog budget explicitly. **Default: (a)** — the escalated call gets `retries=0`, bounding the seam to 1600s + one head re-parse.

### D4 — Order-stable re-emission, no byte-identity change
The escalation re-uses the SAME prompt text (zero prompt-TEXT change — #1695's lane is untouched). The label-order shuffle seed derives from the story (`_label_order_rng`), so re-emission reproduces the same ordered list. Non-truncated calls never enter the escalation branch → their output is byte-identical.

### D5 — Deadline scaling for the bigger budget (reuse `_scaled_deadline`)
The escalated `_complete` call passes `max_tokens=esc` and `deadline_s=None` (the default) → `_complete` computes `_scaled_deadline(600, esc)` = **0.05 × 32000 = 1600s**. No new deadline code — the pattern already exists and is unit-tested (#1787 Task 2 Step 6).

### D6 — Telemetry (permanent; makes the cost question answerable)
Per-stage `stats["recovery"]` counters (rolled up for free by `_rollup_recovery` :4173 into `recovery_stats`, then into the outcome `recovery` field already projected at run.py:3656):
- `escalated` — escalation events (incremented at escalation).
- `escalated_recovered` — escalation ended in a full list (⟺ `finish != "length"` AND `not stats["partial"]`).
- `escalated_residual` — escalation STILL truncated → fail-loud (⟺ `finish == "length"`).
- `escalated_abort` — the escalated `_complete` call itself raised (D3b fallback).
- `escalated_sloppy` — escalated, `finish != "length"`, but the parse still partials/fails after `_PARSE_RETRIES` (headroom granted, still malformed — NOT recovered, NOT residual).
- `escalation_prompt_tokens` / `escalation_output_tokens` — accumulated from **in-stats per-call token counts captured IN THE CALLING THREAD** (the F4 pattern, extended), NOT post-hoc reads of the shared `model.last_prompt_tokens`/`last_completion_tokens` adapter attributes — those are the exact #1787-banned anti-pattern (the adapter OVERWRITES them per call; one model is shared across `--workers>1` threads → nondeterministic attribution). Concretely: `_call_once` captures `prompt_tokens`/`completion_tokens` in the same thread as `complete()` (next to the existing `finish_reason` capture at :4181), and `_complete` writes them into `stats["prompt_tokens"]`/`stats["completion_tokens"]` alongside `finish_reason`; the escalation counters accumulate from those in-stats values → **cost delta = prompt_tokens × in_rate + output_tokens × out_rate**.

New outcome fields (run.py, mirroring `llm_truncated`): `llm_escalations` (= `recovery["escalated"]`), `llm_escalations_recovered`, `llm_escalations_residual`, `llm_escalations_abort`, `llm_escalations_sloppy`, `llm_escalation_prompt_tokens`, `llm_escalation_output_tokens` — all 4 buckets projected so the invariant `llm_escalations == recovered + residual + abort + sloppy` is a literal readout, never a derived residual. New `report.py` warning-only readout (mirroring `llm_truncated`): a run with escalations is distinguishable from one with none; an escalation-recovered session stays `valid=true` BUT the truncation is recorded (`stats["truncated"]` stays true — D7 criterion-3 discipline) and the escalation is visible — never silent.

### D7 — Census class LISTS unchanged; the >32K residual band's per-session outcome flips recoverable→killer
`RECOVERABLE_CENSUS_CLASSES` + `EXTRACTION_KILLER_CENSUS_CLASSES` (report.py:60-96) are untouched — the CLASS SETS and the recoverable/killer MEMBERSHIP are identical to today. BUT the per-session OUTCOME for the residual band shifts: pre-fix, a 16K-truncated parseable session was `partial_parse` (RECOVERABLE, head embedded); post-fix, a >32K residual is `truncated_parse_error` + `empty_embed_list` (KILLER, #1946 degraded population). That shift is INTENTIONAL and is exactly the mitigation — the recovery RATE (escalation covers the 16-32K band) is what keeps the recorded-failure set clean; a session that still exceeds 32K is honestly degraded (fail-loud, raw leg stands), never a silent partial. Stated explicitly because "semantics unchanged" is true only of the class LISTS, not of the residual band's per-session class. Pinned in the census test (a >32K residual fixture → `truncated_parse_error` + `empty_embed_list`, never `partial_parse`). The `_OUTPUT_SCHEMA`, S5 execution, NAND-direction policy (ONTOLOGY.md §3.1), `statement`-only extraction kinds (ONTOLOGY.md §5), and the commit payload contract (INGEST_CONTRACT §2/§11) are untouched — escalation only makes the SAME contract's output complete, never a new kind/status/edge.

### D8 — Measurement-first sequencing (owner direction)
The escalation implementation (Tasks 2-5) proceeds **after** Task 1's measurement run records the go/no-go on #2134. The go/no-go rule (Task 1 Step 5) is the data-driven gate: "raise base cap again", "escalation net", or "both (escalation + trigger #1778)".

---

## Implementation steps

### Task 1: Measurement leg — truncation census at the CURRENT 16K cap (go/no-go)

**Intent:** make the deferred #1787 revalidation real — **measure the residual truncation at the CURRENT 16K cap on the recorded-failure set + a fresh mini-set** with DATA before any budget decision (owner direction #1). This is the gate that decides raise-vs-escalation-vs-both. **Comparability caveat (recorded, not papered over):** there is NO clean same-set 8K baseline against which to read a "did the raise help" delta — the #1787 reval was a different set/era, so Task 1 measures the 16K residual in ABSOLUTE terms and records that caveat rather than overclaiming a before/after. *(Optional cheap arm, if the owner wants the delta anyway: re-run the same Task-1 subset at `TORTOISE_EXTRACTOR_MAX_TOKENS=8000` — still within the ≤$3 bound — to produce a same-set 8K-vs-16K comparison. Default: skip, record the caveat.)* The go/no-go drives on the 16K residual + measured overage distribution alone.
**Acceptance:** a recorded measurement on #2134 with the recorded-failure set + fresh mini-set census readout (per-class counts, per-question `llm_truncated`, stage attribution) + the go/no-go decision + a cost figure ≤ $3.
**Files:**
- Modify: none (uses existing `tools/longmem_eval/run.py` subset mechanism + `report.py` census readout). Result recorded as a #2134 comment.

**Step 1: Assemble the sets.** Recorded-failure set = `0100672e` (42 sessions, the quantified reproducer) + any other qids carrying `empty_embed_list`/`partial_parse`/`truncated_parse_error` in the 2026-09-02 report JSON (pull exact qids from that run's report — not in-repo; 0100672e is the issue-recorded one) + 2-3 high-session-count control questions. Fresh mini-set = 3-5 questions not in the recorded set, same high-session-count composition. Run via the existing subset mechanism (`--data <subset>` / per-question `--ingest-mode v2` runs).

**Step 2: Run both arms.** (a) v2 mode on both sets (production-fidelity extractor = the lane's default `deepseek-chat`/deepseek-direct, per the #2069 metering decision — that decision upgraded the READER model, NOT the extractor; the extractor default is deepseek-direct); (b) deterministic mode (`ingest_mode` default, no LLM extraction) on the SAME recorded-failure questions as the un-confounded control (proves gold retrievability; ~$0.1-0.3). **M5/#2069 disposition (P2-8):** the deepseek-vs-qwen M5 reader comparison on the 7 content-error questions runs in the DETERMINISTIC arm NOW — truncation-un-confounded by construction (raw-turn pool, no extraction) — as the #2069 Step-4 owner-gated evidence lane, NOT #2134 acceptance; the v2-mode M5 arm is deferred to Task 6 (post-fix).

**Step 3: Read the census fields.** For each v2-mode outcome, record `error_census` classes (`empty_embed_list`/`partial_parse`/`truncated_parse_error`), `llm_truncated` (per question), `ingest_error_text`, and `recovery` (stage attribution from the "S2 output partial"/"S4 output partial" error strings + the per-session stats). **ALSO record per-call output-token usage of the truncating calls (`last_completion_tokens` per call — from the per-call stats, not the shared adapter attr) so the observed list-size/overage DISTRIBUTION (not just the class count) drives the go/no-go rows** — a class count alone cannot distinguish "1 question at 17K" from "all questions at 60K". Distinguish S2-truncation from S4-truncation and note S1-quiet-truncation (the gap between `llm_truncated` and the census classes is the S1 seam).

**Step 4: Compute cost.** deepseek-direct rates (costing.py:36: in $0.14 / out $0.28 per M). Run V (~5-9 Q incl. 0100672e) ≈ $0.3-1.5 extraction + $0.05-0.2 reader/judge; deterministic arm ≈ $0.1-0.3. Total budget **≤ $3** (mandate bound).

**Step 5: Apply the go/no-go rule (data-driven).**

| Measured residual (16K, v2 mode) | Decision |
|---|---|
| ZERO `partial_parse`/`truncated_parse_error`/`empty_embed_list` on the recorded set | #1787 actually converged → **downgrade** to telemetry-only + keep escalation as a cheap safety net (or defer); record on #2134 |
| Small residual (1-3 questions) AND **max observed list ≤ the ESCALATION KNOB Task 1 records (≥ measured max)** | **escalation net (Option A)** — proceed to Tasks 2-5 with `TORTOISE_EXTRACTOR_ESCALATION_TOKENS` set to the recorded value (NOT assumed 32000) |
| Residual concentrated in a uniform overage just above 16K AND escalation would fire on a LARGE fraction of calls (per-call output-token distribution from Step 3) | **raise base cap again (Option B)** — re-open the #1787 move with the measured overage as the new default (the fire-rate is measured by Task 1, NOT assumed ~0.2%) |
| Residual persists with lists > the recorded escalation knob ceiling (e.g. > 64000 clamp) OR material at scale | **both** — escalation net (near-term) AND record the measurement as #1778's trigger (structural S4 gaps-only delta) |

**Band/default coherence note (P2-3):** Option A's "≤64K covered" is only true at the CLAMP max (64000); the SHIPPED default is 32000, so a list in the (32K, 64K] band **fails loud** at the default knob (→ `truncated_parse_error` + `empty_embed_list`, per D7). The recorded escalation-knob value (≥ the measured max) is what Tasks 2-6 and the Task-6 re-run use; a just-over-32K unit fixture pins the documented class at the default knob.

**Step 6: Record the decision** as a #2134 comment (census readout + go/no-go verdict + cost + stage attribution). This gates Tasks 2-5.

### Task 2: Escalation foundation — env knob + `allow_partial` param

**Intent:** the building blocks the escalation branches on (D2, D3) — a clamped env knob and the ability to suppress rung-4 partial-accept in the residual case, without changing any existing ladder behavior.
**Acceptance:** `_extractor_escalation_tokens` returns 32000 default, clamps [16000..64000], falls back to default on garbage/out-of-range; `_parse_json_robust(..., allow_partial=False)` skips rung-4 and raises; default `allow_partial=True` leaves all existing tests green.
**Files:**
- Modify: `tortoise/extractor_v2.py` (`_extractor_escalation_tokens`, `_parse_json_robust` signature)
- Test: `tests/test_extractor_v2.py`

**Step 1 (test):** `test_extractor_escalation_tokens_default` / `_clamped_low` / `_clamped_high` / `_garbage_falls_back` / `_out_of_range_falls_back` — monkeypatch `os.environ`; assert 32000 default, clamp bounds, default on garbage.
**Step 2 (implement):** `_extractor_escalation_tokens(base)` mirroring `ask_env_int` (retrieval.py:147) + `_stage_cap`'s inline pattern.
**Step 3 (test):** `test_parse_json_robust_allow_partial_false_raises_on_truncation` — a truncated-mid-list fixture → `allow_partial=False` raises `_ParseError` (no `stats["partial"]`); `allow_partial=True` still partial-accepts (existing behavior).
**Step 4 (implement):** add `allow_partial: bool = True` param to `_parse_json_robust`; gate rung-4 `_longest_valid_prefix` on `allow_partial`.
**Step 5 (verify):** `uv run pytest tests/test_extractor_v2.py -v` (existing #1746 ladder tests stay green); **Step 6:** commit `feat(2134): escalation env knob + allow_partial residual gate`.

### Task 3: S2/S4 escalation + residual fail-loud (`_complete_parsed`)

**Intent:** the actual write-side fix (D1, D3, D4, D5) — on `finish_reason=="length"`, re-attempt once at the escalated budget before any partial-accept; residual truncation fails loud.
**Acceptance:** a 16K-truncating fixture escalates ONCE to 32K → full list returned with `stats["truncated"]` true + `recovery["escalated"]==1` + `recovery["escalated_recovered"]==1`, no `partial_parse`; a fixture that still truncates at 32K → `_ParseError(truncated=True)` (no partial), `recovery["escalated_residual"]==1`; a residual cut AT A SECTION BOUNDARY (rung-1 tail-cut would clean-parse it) STILL fails loud with `_ParseError(truncated=True)` (P1-1); non-length calls make exactly one `_complete` call (no escalation); deadline AND adapter read timeout for the escalated call ≥ 0.05×esc; the escalation accounting invariant `escalated == escalated_recovered + escalated_residual + escalated_abort + escalated_sloppy` holds across ALL arms (success / residual / abort / sloppy); the escalated call runs `retries=0` (one attempt) with the D3b head-reparse fallback on abort.
**Files:**
- Modify: `tortoise/extractor_v2.py` (`_complete_parsed`)
- Test: `tests/test_extractor_v2.py`

**Step 1 (test):** `test_length_escalates_once_and_recovers` — CapAwareModel fixture: attempt-1 `finish_reason="length"` with a 16K-truncated head; attempt-2 (escalated) returns a full list → assert exactly 2 model calls, the 2nd `max_tokens=32000`, full list returned, `stats["truncated"]` true, `recovery["escalated"]==1` + `recovery["escalated_recovered"]==1`, and NO `partial_parse` bump at the caller. `test_length_residual_fails_loud` — both attempts `length` → `_ParseError(truncated=True)` raised, `recovery["escalated"]==1` + `recovery["escalated_residual"]==1`, no `stats["partial"]`. `test_length_residual_cut_at_section_boundary_fails_loud` (P1-1) — the residual response is truncated cleanly BETWEEN sections (rung-1 tail-cut parses a valid shorter dict) → STILL `_ParseError(truncated=True)`, never a silent `valid=true` return. `test_length_escalates_then_sloppy` (P2-1) — attempt-2 returns `finish_reason="stop"` but still malformed → the post-escalation `_PARSE_RETRIES` error-informed re-prompt fires AT THE ESCALATED budget (the loop's `max_tokens` is already `esc`), 3 `_complete` calls total per seam, per-call `max_tokens` = [base, esc, esc], and the classed outcome is `escalated_sloppy` (NOT `escalated_recovered`) with `escalated == recovered + residual + abort + sloppy` still holding. `test_escalated_call_abort_falls_back_to_head_reparse` (P1-3) — attempt-2 RAISES (transient-after-retries) → `escalated_abort==1`, attempt-1's head re-parsed under `allow_partial=True` with `partial_parse` recorded (RECOVERABLE, not killer). `test_non_length_no_escalation` — `finish_reason="stop"` → exactly 1 call. `test_escalated_call_deadline_scales` + `test_escalated_call_read_timeout_scales` (P1-3) — assert the escalated `_complete` is called with `max_tokens=esc`, the resolved deadline ≥ `0.05*esc`, and the adapter read timeout ≥ `0.05*esc`.
**Step 2 (implement):** in `_complete_parsed`, after the first `_complete` returns and `finish=="length"` and `not escalated`: compute `esc = _extractor_escalation_tokens(max_tokens)`; if `esc > (max_tokens or 0)`, set `escalated=True`, bump `recovery["escalated"]`, re-`_complete` at `max_tokens=esc` WITH `retries=0` and a read timeout ≥ `0.05*esc`, re-read `finish` (guard the escalated call in a try/except — an exception bumps `recovery["escalated_abort"]` and falls back to re-parsing attempt-1's retained `response` under `allow_partial=True`); set `residual = escalated and finish=="length"`; if `residual`, parse STRICT-truncated (canonical full-JSON only — no tail-cut/repair/partial acceptance) and raise `_ParseError(truncated=True)` on any non-canonical outcome; bump `recovery["escalated_recovered"]` ONLY when `escalated and finish!="length" and not stats["partial"]` (P2-1), else bump `recovery["escalated_sloppy"]` when `escalated and finish!="length"` and the parse partials/fails; on the residual path bump `recovery["escalated_residual"]`. Accumulate `escalation_prompt_tokens`/`escalation_output_tokens` from the in-stats per-call token counts (P1-4). Assert the invariant `escalated == recovered + residual + abort + sloppy` at every return.
**Step 3 (verify):** `uv run pytest tests/test_extractor_v2.py -v`; **Step 4:** commit `feat(2134): one-shot S2/S4 escalation on length + fail-loud residual`.

### Task 4: S1 seam escalation (`run_s1`)

**Intent:** close the quiet tail-drop seam — a 1500-cap-truncated S1 chunk summary re-runs once at the escalated budget so chunk-tail facts stop silently dropping from the compiled story (likely the majority of the 13/139 on 0100672e).
**Acceptance:** an S1 fixture with `finish_reason=="length"` re-runs once at `esc` and returns the full summary with `recovery["escalated"]` bumped; **`stats["truncated"]` stays True after escalation recovery (OR'd across both `_complete` calls, mirroring `_complete_parsed`'s `stage_truncated` — the second `_complete` overwrites `stats["truncated"]` per attempt, extractor_v2.py:4279-4283, and must NOT erase the first call's flag)**; the session `llm.truncated` roll-up counts it; non-length S1 calls make one `_complete` call.
**Files:**
- Modify: `tortoise/extractor_v2.py` (`run_s1`)
- Test: `tests/test_extractor_v2.py`

**Step 1 (test):** `test_s1_length_escalates_once` — CapAwareModel S1-shaped fixture; assert 2 calls, 2nd at `max_tokens=esc`, `recovery["escalated"]==1`, AND `stats["truncated"] is True` (the OR'd flag survives the escalated call). `test_s1_escalated_truncated_flag_survives_rollup` (P1-2) — after escalation recovery, the session `llm.truncated` roll-up (via `_rollup_llm`) counts the truncation (`llm.truncated ≥ 1`). `test_s1_non_length_single_call`.
**Step 2 (implement):** wrap `run_s1`'s `_complete` call with the same one-shot escalation (shared helper if clean, else inline mirror of Task 3's branch — no ladder, just budget escalation on `length`). **OR the `truncated` flag across the base and escalated calls in the `run_s1` wrap** (the `_complete_parsed` `stage_truncated` pattern, extractor_v2.py:1037-1041): after the escalated call returns, set `stats["truncated"] = base_truncated or finish=="length"` so the escalated call's `stats.update(truncated=False)` never erases the base call's flag (P1-2).
**Step 3 (verify):** `uv run pytest tests/test_extractor_v2.py -v`; **Step 4:** commit `feat(2134): one-shot S1 escalation on length`.

### Task 5: Telemetry + report readout (cost question answerable)

**Intent:** surface the escalation counters to the outcome + report so the upfront-vs-escalation cost question is answerable from real runs (owner direction #2).
**Acceptance:** outcome projects `llm_escalations`/`llm_escalations_recovered`/`llm_escalations_residual`/`llm_escalations_abort`/`llm_escalations_sloppy`/`llm_escalation_prompt_tokens`/`llm_escalation_output_tokens`; report adds a warning-only readout (mirror `llm_truncated`); golden-shape pin updated; token counters are accumulated from IN-STATS per-call values (P1-4 — never the shared adapter attrs); a cross-layer roll-up integration test proves the counters thread extract → per-session roll-up → per-question recovery dict → run.py outcome projection (P2-7).
**Files:**
- Modify: `tools/longmem_eval/run.py` (outcome fields + projection key list :4250-4252)
- Modify: `tools/longmem_eval/report.py` (warning-only escalation readout)
- Test: `tests/test_longmem_runner.py`

**Step 1 (test):** update `test_outcomes_to_report_golden_shape` with the 7 new keys; `test_report_escalation_readout_warning_only` — an outcome with `llm_escalations>0` and `valid=true` → listed in the warning readout, NOT in `error_census`, `valid` stays true (escalation-recovered is recorded, never silent). `test_escalation_token_capture_thread_safe` (P1-4) — N escalated calls with KNOWN per-call usage, ≥8 worker threads, ONE shared adapter → the accumulated sums equal the EXACT expected totals (locked-accumulator concurrency — guards the #1787 shared-attr race). `test_ingest_rollup_escalation_counters` (P2-7) — mock-mode integration: the LIVE `ingest_haystack_v2` runs against a STUBBED `extract_session_v2` returning escalated counters + token sums across ≥2 sessions; assert the per-question roll-up (ingest_v2.py:1053-1062) and the run.py outcome projection of the 7 new fields are exact (guards the #1744 dual-copy hazard).
**Step 2 (implement):** run.py maps `recovery` → the 7 outcome fields (accumulated from the in-stats token counts, P1-4); projection key list extended; report.py computes the warning-only readout (e.g. `integrity.escalated_qids`/count + cost-delta summary); `_call_once`/`_complete` capture `prompt_tokens`/`completion_tokens` in-thread and write them into `stats` next to `finish_reason` (the F4 pattern, extended — the #1787 anti-pattern fix).
**Step 3 (verify):** `uv run pytest tests/test_longmem_runner.py -v`; **Step 4:** commit `feat(2134): escalation telemetry + warning-only report readout`.

### Task 6: Verification runs + W2-b gate coordination (note, don't build)

**Intent:** prove the fix against #2134's O/I/T and record the #2281 W2-b CI truncation-gate handoff (extend merged #2183, not re-invent).
**Acceptance:** recorded-failure set re-run (v2) reaches ZERO `partial_parse`/`truncated_parse_error` AND **ZERO `empty_embed_list`** (P2-3 — the only census class still fireable post-fix is `empty_embed_list`; it must be 0, or an explicit documented residual + justification recorded) with gold-in-context held; deterministic arm confirms gold retrievability; the v2-mode M5 arm (deepseek-vs-qwen on the 7 content-error questions) runs POST-fix as the #2069 Step-4 owner-gated evidence — NOT #2134 acceptance (P2-8); a #2134 comment documents the W2-b gate as #2281's WS2 delta (gated on this fix), not built here.
**Files:**
- Modify: none (run + issue comment); optional `docs/runbook/1987-ask-abstention-check.md` note.

**Step 1:** re-run the recorded-failure set (Task 1 Step 2's subset) in v2 mode POST-fix, using the escalation-knob value recorded in Task 1 (P2-3); verify zero census classes (`partial_parse`/`truncated_parse_error`/`empty_embed_list`) + gold-in-context membership (existing `evidence_recall@k` / `chunk_evidence_recall@k` readouts). Run the v2-mode M5 arm (deepseek-vs-qwen, 7 content-error questions) as #2069 Step-4 owner-gated evidence — recorded, NOT #2134 acceptance.
**Step 2:** record the W2-b coordination note on #2134 — the CI truncation gate ("zero `partial_parse`/`truncated_parse_error` with `valid=true` on the multi-session fixture set") is #2281's WS2 delta extending merged #2183; this issue delivers the enabler, not the gate.
**Step 3:** commit any runbook note.

---

## Tests / Verification Plan

### Verification Plan (test-routing, complexity: UX n/a / Architecture standard / Ontology low / Accessibility n/a)

| Layer | Depth | Applies | Notes |
|---|---|---|---|
| Unit | full | ✅ | escalation trigger/residual, env clamp, S1 seam, deadline scaling, `allow_partial` |
| Integration | full | ✅ | telemetry roll-up + projection + report readout; census-semantics no-change pin |
| E2E smoke | mock-only CI | ✅ | CapAwareModel fixtures (tests/test_extractor_v2.py:2955-3099) — NO live keys |
| E2E full | — | ⛔ not in CI | recorded-failure set re-run is @slow/gated (dataset + keys, Task 6) |
| UX / content / config / research domains | — | skipped | no UI, no content pipeline, no external research domain |

### Test list (new/updated)

`tests/test_extractor_v2.py`:
- `test_extractor_escalation_tokens_default` / `_clamped_low` / `_clamped_high` / `_garbage_falls_back` / `_out_of_range_falls_back` (Task 2)
- `test_extractor_escalation_tokens_warns_when_clamped_to_le_base` (P2-6 — explicit value clamped ≤ base warns, mirror `_stage_cap`)
- `test_parse_json_robust_allow_partial_false_raises_on_truncation` (Task 2)
- `test_length_escalates_once_and_recovers` / `test_length_residual_fails_loud` / `test_length_residual_cut_at_section_boundary_fails_loud` (P1-1) / `test_length_escalates_then_sloppy` (P2-1) / `test_escalated_call_abort_falls_back_to_head_reparse` (P1-3) / `test_non_length_no_escalation` / `test_escalated_call_deadline_scales` / `test_escalated_call_read_timeout_scales` (P1-3) / `test_length_no_escalation_when_base_ge_esc` (P2-6) / `test_just_over_32k_residual_classed_at_default_knob` (P2-3) / `test_over_32k_residual_census_killer_not_partial_parse` (P2-5) (Task 3)
- `test_s1_length_escalates_once` / `test_s1_escalated_truncated_flag_survives_rollup` (P1-2) / `test_s1_non_length_single_call` (Task 4)

`tests/test_longmem_runner.py`:
- updated `test_outcomes_to_report_golden_shape` (new key contract — additive projection keys, intentional change) (Task 5)
- `test_report_escalation_readout_warning_only` (Task 5)
- `test_escalation_token_capture_thread_safe` (P1-4 — ≥8 threads, one adapter, exact sums) (Task 5)
- `test_ingest_rollup_escalation_counters` (P2-7 — live `ingest_haystack_v2` + stubbed `extract_session_v2`, ≥2 sessions) (Task 5)

**Negative-case ownership:** truncated output never silently valid (`test_length_residual_fails_loud` + `test_length_residual_cut_at_section_boundary_fails_loud`); escalation never fires twice (`test_length_escalates_once_and_recovers` asserts exactly 2 calls); escalation-recovered never unrecorded (`test_report_escalation_readout_warning_only`); escalation accounting invariant airtight (`escalated == recovered + residual + abort + sloppy` across all arms); census class LISTS unchanged (existing report.py recoverable/killer pins stay green) while the >32K residual band's per-session class flips recoverable→killer by design (P2-5).

## Cross-lane interfaces

| Lane | Interface | Contract |
|---|---|---|
| **#2280** | escalation shape (reader) | MIRROR, not share — the extractor's `_extractor_escalation_tokens` clones `ask_env_int`'s clamp shape (retrieval.py:147); the extractor triggers on `length` (partial), the reader on empty+`length`; both fail-loud after one retry. No cross-import (avoids a retrieval→extractor coupling). |
| **#1778** | structural S4 gaps-only delta (trigger-gated) | #2134's escalation is the near-term safety net + the measurement that feeds #1778's trigger (D8). The S4 delta re-architecture is NOT built here (NON-GOAL). |
| **#2281 WS2** | W2-b CI truncation gate | #2134 delivers the enabler; the CI gate ("zero truncation on the multi-session fixture") is #2281's WS2 delta extending merged #2183 — noted (Task 6), not built here. |
| **#1746** | recovery ladder | Extends it additively: escalation runs BEFORE rung-4 partial-accept; a strict-truncated mode (canonical-full-parse only) is engaged ONLY in the residual case (P1-1); all existing rung behavior for non-escalation paths is byte-identical. |
| **#1787** | cap-raise + `_scaled_deadline` | Reuses the deadline-scaling pattern; the 16K default cap is NOT changed here (the go/no-go in Task 1 may decide otherwise). |
| **#1695** | S2/S4 prompt text | Zero prompt-TEXT changes here (order-stable re-emission reuses the existing prompt). |
| **#2069** | metering decision | The measurement runs on the lane's default extractor (deepseek-direct) — the #2069 upgrade was READER-model only. |

## ⛔ Conditional-gate notes

**Gate 1 — No ontology / architecture / new-field change.** Escalation changes WHICH dict `run_s2`/`run_s4` return on a length-truncated call (full list instead of partial), never the payload schema, kinds, edges, or statuses. `_OUTPUT_SCHEMA`, S5 execution, NAND-direction policy, commit contract untouched. **Census note (P2-5):** the class LISTS are unchanged, but the >32K residual band's per-session outcome shifts `partial_parse` (recoverable) → `truncated_parse_error` + `empty_embed_list` (killer) — the recovery rate is the mitigation, stated explicitly.

**Gate 2 — Residual is fail-loud, never a silent partial (owner direction #4).** `partial_parse` trends to zero on the recorded set; a residual truncation is treated as truncated UNCONDITIONALLY (`finish=="length"` → `_ParseError(truncated=True)`, canonical-full-parse at most) — a between-section cut that rungs 1-3 would clean-parse as a shorter valid list is STILL a fail-loud `truncated_parse_error`/`empty_embed_list` + `valid=false` with the raw-chunk leg standing (P1-1). Exactly the issue's core complaint (partial usage was the damage) — no rung may swallow a truncated escalated response.

**Gate 3 — Escalation is cost-bounded, rare, and never silently disabled.** Fire ONLY on `length`, ONLY once, ONLY when `esc > base`, clamped [16000..64000], with the escalated call at `retries=0` (one attempt → D3b head-reparse fallback). Common path (non-length) makes exactly one `_complete` call — byte-identical to today. **`esc ≤ base` is a LOUD no-op** (P2-6): an explicitly-set escalation value clamped to ≤ base warns (mirror `_stage_cap`'s warning, extractor_v2.py:3973) and falls through to the unchanged rung-4 partial-accept — never a silent disable. **Escalated-call failure is accounted** (P1-3): a raising escalated call bumps `escalated_abort` and falls back to attempt-1 head-reparse, never an unaccounted return.

**Gate 4 — Measurement gates the escalation build (owner direction #1).** Task 1's go/no-go is recorded on #2134 BEFORE Tasks 2-5 proceed. The go/no-go is DATA-driven (Task 1 Step 5 table), never assumed.

**Gate 5 — Telemetry is permanent (owner direction #2).** Escalation count + recovered + residual + token cost delta are projected to the outcome and surfaced as a warning-only report readout — the later upfront-vs-escalation optimization reads real runs, not guesses.

**Gate 6 — CI is mock-only.** No live-API leakage: escalation tests use `CapAwareModel` fixtures (existing pattern, tests/test_extractor_v2.py:2955-3099). The recorded-failure re-run is @slow/gated outside CI.

## Open questions — RESOLVED (plan-review cycle 1)

1. **S1 escalation budget → single knob.** One `TORTOISE_EXTRACTOR_ESCALATION_TOKENS` knob for S1/S2/S4 (default 32000, clamp [16000..64000]); the S1 1500→32000 asymmetry is noted as harmless (S1 summaries never approach the ceiling; deadline scales; cost negligible). A dedicated S1 knob is a later refinement, not built here.
2. **Recovered bucket → per P1-1/P2-1 (4-bucket invariant).** `escalated_recovered ⟺ (finish != "length" AND not stats["partial"])` on the FINAL return — a full list only. The sloppy-fail (escalated, `finish=="stop"`, parse-still-fails → re-prompt at the ESCALATED budget) is NOT recovered; it is the explicit `escalated_sloppy` bucket, so `escalated == escalated_recovered + escalated_residual + escalated_abort + escalated_sloppy` is airtight — no derived residual, no unaccounted return.
3. **Residual `empty_embed_list` + `truncated_parse_error` double-signal → keep BOTH.** Both are honest signals: the session is truncated AND embed-less; the killer-class implication is documented (D7/P2-5) and pinned in the census test. No suppression — the `empty_embed_list` killer is exactly what keeps a >32K residual honest (#1946 degraded population).

## Follow-up issues (coordinate, do NOT absorb)

- **#1778 — S4 gaps-only delta (structural, trigger-gated):** the escalation's ceiling-exceeded cases (Task 1 go/no-go "both" arm) feed #1778's trigger. NOT built here.
- **#2281 WS2 — W2-b CI truncation gate:** extends merged #2183; noted (Task 6), built in #2281's lane.

---

*Status: draft — writing-plans output; plan-review cycle-1 fixes applied.*

## CHANGELOG — plan-review cycle 1

| Issue | Severity | Fix applied | Research? |
|---|---|---|---|
| P1-1 | P1 | Residual (`escalated and finish=="length"`) treated as truncated UNCONDITIONALLY — strict canonical-full-parse at most (no tail-cut/repair/partial acceptance), `_ParseError(truncated=True)`; airtight 4-bucket invariant `escalated == recovered + residual + abort + sloppy`; `test_length_residual_cut_at_section_boundary_fails_loud` added (D3, Task 3, Gate 2) | No — in-repo (extractor_v2.py rungs 1-3) |
| P1-2 | P1 | OR the `truncated` flag across base + escalated `_complete` calls in the `run_s1` wrap (mirror `_complete_parsed`'s `stage_truncated`); `test_s1_escalated_truncated_flag_survives_rollup` asserts `stats["truncated"] is True` + `llm.truncated` roll-up counts it (Task 4) | No — in-repo (extractor_v2.py:4279-4283, :1037-1041) |
| P1-3 | P1 | D3b: escalated-call failure → `escalated_abort` bucket + re-parse attempt-1's retained response under `allow_partial=True` (partial_parse RECOVERABLE); scale adapter read timeout (model_adapters.py:149) for the escalated budget; escalated call runs `retries=0`; 3×1600s wall-clock bound named vs run watchdogs; `test_escalated_call_abort_falls_back_to_head_reparse` + `test_escalated_call_read_timeout_scales` (D3b, Task 3, Gate 3) | No — in-repo (model_adapters.py:149, extractor_v2.py _complete) |
| P1-4 | P1 | In-thread token capture via `_call_once`/`_complete` writing `stats["prompt_tokens"]`/`stats["completion_tokens"]` next to `finish_reason` (F4 pattern extended); counters accumulate from in-stats values, never the shared adapter attrs; `test_escalation_token_capture_thread_safe` (≥8 threads, one adapter, exact sums) (D6, Task 5) | No — in-repo (#1787 anti-pattern) |
| P2-1 | P2 | `escalated_recovered ⟺ (finish!="length" AND not stats["partial"])`; sloppy-fail named as the explicit 4th bucket `escalated_sloppy`; post-escalation `_PARSE_RETRIES` pinned to the ESCALATED budget, 3 calls/3-per-call-max_tokens `[base, esc, esc]`; `test_length_escalates_then_sloppy` (D3, Task 3) | No — in-repo (extractor_v2.py:996, :1015-1088) |
| P2-2 | P2 | Task 1 restated as an ABSOLUTE 16K residual measure + recorded comparability caveat (no clean same-set 8K baseline); optional cheap 8K arm documented, default skip (Task 1 Intent) | No |
| P2-3 | P2 | Task 1 Step 3 also records per-call output-token usage (overage DISTRIBUTION drives go/no-go); go/no-go rows use the recorded escalation-knob value (≥ measured max) instead of assumed 32000/64K; band/default coherence note; `test_just_over_32k_residual_classed_at_default_knob`; Task 6 acceptance adds `empty_embed_list==0` (or explicit residual) | No — in-repo (report.py:60-96) |
| P2-4 | P2 | Cost bound recomputed from 0100672e evidence (13/139 ≈ 9.4%, 7/42 sessions) → 500-Q with like composition = $5-25; "unlikely ~0.2%" retired → "measured by Task 1" (Pattern Research, Task 1 Step 5) | No — in-repo (issue evidence 0100672e) |
| P2-5 | P2 | D7/Gate 1 restated: class LISTS unchanged, but >32K residual band's per-session outcome flips recoverable→killer (recovery rate is the mitigation); `test_over_32k_residual_census_killer_not_partial_parse` (D7, Gate 1, census test) | No — in-repo (report.py:60-96) |
| P2-6 | P2 | `esc ≤ base` = LOUD no-op: warn (mirror `_stage_cap`) + fall through to unchanged rung-4 partial-accept; `test_length_no_escalation_when_base_ge_esc` + `test_extractor_escalation_tokens_warns_when_clamped_to_le_base` (Gate 3, Task 2) | No — in-repo (extractor_v2.py:3973) |
| P2-7 | P2 | `test_ingest_rollup_escalation_counters` — mock-mode integration: live `ingest_haystack_v2` + stubbed `extract_session_v2` returning escalated counters + token sums across ≥2 sessions; asserts per-question roll-up (ingest_v2.py:1053-1062) + run.py 7-field projection (#1744 dual-copy hazard) (Task 5) | No — in-repo |
| P2-8 | P2 | M5/#2069 dispositioned: deterministic M5 arm runs NOW (un-confounded, 7 content-error Qs) in Task 1 Step 2; v2-mode M5 arm runs post-fix in Task 6 as #2069 Step-4 owner-gated evidence — NOT #2134 acceptance | No |
| Q1/Q2/Q3 | — | Resolved: single knob (Q1); recovered bucket per 4-bucket invariant (Q2); keep both `empty_embed_list` + `truncated_parse_error` signals with killer implication documented (Q3) | No |
