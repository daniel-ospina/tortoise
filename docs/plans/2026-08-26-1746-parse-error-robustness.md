---
title: "Plan — #1746 pilot parse-error robustness: censused recovery ladder + JSON-mode parity + probe, two-stage escalation"
type: plan
domain: capability
doc_status: draft
created: 2026-08-26
ownedBy: epistemic-team
governingAgreement: "#1746 (epic #1509, M3/M4/M7 lineage; issue-scoping solution-converge)"
---

<!-- research-path: docs/plans/2026-08-26-1746-solution-approaches.md (divergence artifact, verified 2026-08-26) + docs/epics/2026-08-20-1509-extractor-v3/03-scope.md (M3/M4/M7) + 05-detailed-e2e.md (E2E-2) -->

# Plan — #1746: S2/S4 unparseable-JSON robustness (censused ladder + JSON-mode parity + probe)

## Context

- **Issue:** #1746 — pilot parse-error robustness (epic #1509 Extractor V3). Contract: `03-scope.md` M3 (retry/census lineage), M4 (integrity, per-question `valid`, error census), M7 (report contract); `05-detailed-e2e.md` E2E-2 (a run cannot silently degrade). Issue-scoping confirmed problem + closing criteria 1-4; solution-diverge produced A/B/C (see `2026-08-26-1746-solution-approaches.md`).
- **Confirmed problem (passed problem-verify):** 11/50 pilot questions carry `_ParseError` (14 census `parse_error`; S2 6 qids, S4 8 qids, 3 lost both). No distinguishing content feature (newline density INVERSE 45.4% vs 47.2%). Mechanisms ranked: **H1** JSON mode inert on the pilot's `DeepSeekDirectModel` path — UNTESTED lever (verified in code 2026-08-26: `DeepSeekDirectModel.complete` omits `response_format`, `OpenRouterModel.complete` sets it); **H2** E3 verbatim-quote control-char contamination (raw control chars defeat tail-cuts; 46.9% of 246,750 turns have newlines); **H3** 8000-cap mid-string truncation (S4 re-emits the COMPLETE list at the same cap; silent-truncation success class verified — valid=true with silent data loss).
- **Closing criteria:** (1) fresh non-resumed 50-Q ≤ 1 question in the parse-failure family — the three-class per-question set `{parse_error, truncated_parse_error, partial_parse}` (D10 operationalizes the confirmed two-class aggregate by deliberately extending it with `partial_parse` — anti-gaming: a partial-accept question is invalid-but-embedded and must not masquerade as clean); (2) census equality all outcomes (`n_ingest_errors == sum(error_classes)`); (3) no UNRECORDED truncation with valid=true; (4) no regression vs the measured baselines (pinned to the 1745 plan §1.2 table, verified from pilot artifacts): the closing fresh-50 run compares against the BLENDED-50 same-question-set baselines — accuracy ≥ 0.74, `session@20` ≥ 0.90, `chunk_evidence_recall@20` ≥ 0.5465 — with the clean single-population reference stated alongside (fresh-30: accuracy 0.867, `session@20` 1.00, `chunk-evidence@20` 0.567); CI/margin rule (e.g. 90%-CI lower bound or ±0.05 point margin). Note: `evidence@20` (0.268 blended / 0.256 fresh) and `turn@20` are NOT this issue's metrics — they are #1745's targets; #1746 only guards the regression metrics above. Non-parse gate failure → #1747 trigger.
- **Complexity:** `complexity:standard` → Standard tier (condensed plan).
- **Dependencies:** #1695 (S2 prompt slimming) — **parallel lane; prompt TEXT changes are #1695's lane.** This plan makes **zero prompt-text changes** (see D11). #1747 (gate-criterion semantics) — companion, NOT absorbed. M3/M4 (#1524) retry+census machinery is the base this plan extends; P2 (#1530) taxonomy delegation already in place (`_bump_census` → `is_fatal`).
- **Scope guard:** telemetry+census fidelity (scope a) + generation/parse robustness (scope b, capped at the levers below). **Out:** gate-criterion semantics (#1747), S2/S4 prompt text (#1695), content quality, retrieval/reader/judge, non-parse classes beyond census, 500-Q orchestration, the S4 gaps-only delta contract (follow-up, escalation-gated), session-dedup/caching (follow-up).
- ⛔ **Plan-review note:** this doc is the issue-scoping Phase 5 converge output (solution-verify gate to follow); it will be re-verified by the writing-plans plan-review gate before execution.

## Current state (verified on HEAD 2026-08-26)

| Surface | Today | Problem (#1746) |
|---|---|---|
| `tortoise/extractor_v2.py::_parse_json` (3158) | canonical: fences, brace-balance, tail-cuts (`None,-1,-2,-3,-5,-10,-20`); raises `ValueError` otherwise | raw control chars INSIDE string values break `json.loads` (H2); mid-string truncation leaves no recoverable boundary in the ≤20-char tail-cut set (H3); binary parse-or-fail — no recovery ladder, no schema gate |
| `extractor_v2.py::_complete_parsed` (804) | same-prompt retry ×1 (`_PARSE_RETRIES=1`); retry is deterministic for truncation (same prompt + same cap); first-attempt `finish_reason` discarded; census always `parse_error` | truncation is retried into the same failure; H3-vs-H2 attribution impossible at the class level |
| `extractor_v2.py::extract_session_v2` error paths (S2 ~2808, S4 ~2833, entity-resolution ~2838, no-embed-list ~2850, S5 ~2852, S1 summary ~2794) | S1-per-chunk/S2/S4 append AND bump census; **S1 summary line, entity-resolution, no-embed-list, S5 append WITHOUT census** | `n_ingest_errors != sum(error_census)` (pilot datum: 16 error strings vs 14 census entries) — criterion 2 unreachable |
| `tools/longmem_eval/ingest_v2.py` (534-548) | collects `errors` + `error_census` per session; **drops `out["stats"]["llm"]` (calls/retries/truncated) and any recovery counters** | per-question `truncated` never reaches the outcome → criterion 3's readout impossible at report level |
| `tools/longmem_eval/run.py::outcomes_to_report` (1576-1591) | projection drops `ingest_error_text` (computed at 1338) and llm fields | first-error text + truncation not auditable post-hoc from the report |
| `tortoise/model_adapters.py::DeepSeekDirectModel.complete` (168-198) | **no `response_format`** in the request body | H1 — the pilot ran the direct path WITHOUT JSON mode; the 922→14 reduction (baseline verified: `longmemeval_s_20260824T012939Z.report.json` → `parse_error: 922`, invalid 50/50, pre-dating the #1639/#1693 fixes) bundles robust-parse + retry + JSON-mode + provider switch and is not attributable to any single lever |
| `tools/longmem_eval/report.py::build_report` integrity (338-387) | rolls `error_census` from per-question `error_classes`; `valid` = `n_ingest_errors == 0` | no warning-only truncation readout (`truncated_valid`); the silent-truncation success class is invisible |

## Pattern Research

> **Findings date:** 2026-08-26. **Gate skipped:** plan touches ZERO new third-party dependencies — `json`/`re` (stdlib), in-repo patterns only (M3 retry/census, `_bump_census` taxonomy, bounded stage caps). Step B (Perplexity verification gate) does not fire per the zero-deps skip rule. Step A (prior research intake) ran: the solution-diverge artifact's cited research + epic 02-research-brief (prompt-efficiency: JSON mode, parse-retry) + 03-scope (M3/M4/M7) + 05-detailed-e2e (E2E-2).

**Canonical + codebase-verification (from the solution-approaches artifact, re-verified against source 2026-08-26):**
- **H1 is real in code:** `DeepSeekDirectModel.complete` never sends `response_format`; `OpenRouterModel.complete` does (model_adapters.py:111-113, toggle `TORTOISE_JSON_MODE` default "1"). The pilot's direct path therefore ran JSON-mode-free. JSON mode is an UNTESTED lever on that path; DeepSeek docs warn JSON mode breaks at `max_tokens` (truncation still possible → JSON mode must be paired with truncation-aware handling, never assumed to fix H3).
- **Parse → bounded repair beats vague re-prompt** for deterministic defects (missing commas, trailing junk); **error-informed re-prompt beats same-prompt retry** for sloppiness (the retry carries the failure signal). Same-prompt retry is deterministic for truncation (same input + same cap → same length) — it must be skipped, not retried (confirmed-problem H3 note).
- **Control-char contamination defeats tail-cuts** (H2): raw newlines/tabs inside string values make `json.loads` fail regardless of tail state; string-aware escaping is lossless via `json.loads` round-trip.
- **Longest-valid-prefix partial-accept** converts the silent-truncation success class into a recorded recovery (H3 verified: valid=true with silent data loss today); the accepted prefix must be schema-validated so junk prefixes are never accepted.
- **Provider-honor probe before trusting the lever:** `response_format` support varies by provider/model; a 10-20 call probe distinguishes honored/ignored/rejected for pennies instead of discovering inertness mid-run (H1's reason to exist).

## Integration Surface Map (test-design — #1746-owned subset)

| Surface | Boundary | Bug pattern | Test layer |
|---|---|---|---|
| S2/S4 parse seam | `_complete_parsed`/`_parse_json_robust` ← provider output | silent data loss; wasted deterministic retries; contaminated/truncated text accepted wrongly | unit + integration (ladder rungs, retry policy) |
| finish_reason linkage | `_complete` (3139) → `_ParseError` → `_bump_census` | class misattribution (truncation labeled `parse_error`) | unit |
| census append paths | `extract_session_v2` error sites | uncensused append → `n_ingest_errors != sum(census)` (16-vs-14) | unit + integration |
| llm rollup threading | `ingest_v2` session loop | `truncated`/calls/retries dropped at the boundary | integration |
| report projection | `outcomes_to_report` key list | `ingest_error_text`/llm fields dropped (M1-regression class — pinned) | unit (`test_outcomes_to_report_golden_shape` update) |
| provider decode | `DeepSeekDirectModel.complete` body | `response_format` absent (H1) | unit (body assertion, toggle honored) |
| probe | `probe_json_mode.py` → live API | provider ignores/rejects the mode → uninterpretable run | unit (dry-run verdict logic) + @slow (real, gated) |
| report integrity | `build_report` integrity block | silent-truncation success invisible; census rollup drift | integration + unit |

## Design decisions

### D1 — Census vocabulary: deterministic 1:1 error classes + warning-only classes (exact keys)
**Error classes** (every one appends a human-readable string to `errors` AND bumps `error_census[class]` — 1:1, enforced by test):
- `parse_error` — S2/S4 final parse failure, first parse-failing attempt `finish_reason != "length"` (existing key, semantics narrowed per D2).
- `truncated_parse_error` — S2/S4 final parse failure, first parse-failing attempt `finish_reason == "length"` (NEW).
- `partial_parse` — schema-validated partial-accept applied; the truncated tail was dropped (NEW; an ERROR — the embed list is incomplete; `valid=false`; D4 rung 4).
- `empty_embed_list` — "no embed list produced (S2/S4 empty)" (NEW deterministic append for the previously-uncensused path).
- `s5_failed` — S5 exception (NEW deterministic append).
- `entity_resolution_failed` — entity-resolution exception (NEW deterministic append).
- `s1_chunk_summary` — the "N/M S1 chunks failed" summary line (NEW; one bump per summary event; per-chunk failures keep their exception-class bumps).
- Existing `transient_*` / `fatal_*` classes unchanged (P2 delegation preserved in `_bump_census`).
**Warning-only** (recorded; NEVER an `errors`-string append; NEVER in `error_census`; `valid` unaffected — excluded from criterion 2's class universe):
- `truncated_valid` — ≥1 extractor call with `finish_reason=="length"` AND the question has no error classes (the silent-truncation success class, now RECORDED). Surfaced as `integrity.truncated_valid_qids` (D7).
- `recovered_sanitize` / `recovered_repair` — ladder rungs 2/3 succeeded with a schema-valid FULL output (D4). Counters in `stats["recovery"]`, projected, never error strings.
**Invariant:** for every `extract_session_v2` result, `len(errors) == sum(error_census.values())` — enforced by a unit test on a synthetic mixed-error session and the integration census-equality test (criterion 2).

### D2 — finish_reason re-link: `_ParseError` carries the class-decision signal
`_ParseError` gains attributes: `truncated: bool` (first parse-failing attempt's `getattr(model, "last_finish_reason", None) == "length"`), `attempt: int`, `excerpt: str` (bounded error region, D3). `_bump_census` maps `_ParseError` → `"truncated_parse_error" if e.truncated else "parse_error"`; all other exceptions keep `_classify_error`. Capture rule: read `model.last_finish_reason` immediately after each `_complete` return, hold the FIRST parse-failing attempt's value (the retry only runs for the stop/None class, so first-attempt == the only attempt in the truncation case; a stop-fail followed by a length-fail is classed `parse_error` — per the confirmed problem's FIRST-attempt rule). The `stats["llm"]` rollup (`calls`/`retries`/`truncated`) stays structurally as-is; precision note: `_rollup_llm` accumulates a per-STAGE truncated flag (`int(bool(stage_stats["truncated"]))`) and `_complete_parsed`'s parse-retries land in the nested per-call `stats["llm"]["retries"]` — distinct from the session `llm_stats["retries"]` (transient backoff only). The readout needs only `> 0`, but the two counters must not be conflated in the report labels (documented, not load-bearing).

### D3 — Retry policy: error-informed re-prompt, truncation-aware skip
`_PARSE_RETRIES` stays 1. Attempt 1 runs the ladder (D4). Then:
- First parse-failing attempt `finish_reason == "length"` → **skip the same-prompt retry** (deterministic failure — same prompt + same cap) → censused fallback (`truncated_parse_error`). Truncation-aware handling = the ladder's rung-4 partial-accept already ran in-process on attempt 1; nothing more to recover by re-prompting.
- `finish_reason == "stop"` (or `None`, e.g. MockModel / adapters that don't set it) → attempt 2 is the **error-informed re-prompt**: the user message is the original plus a bounded block: `\n\nYour previous response did not parse as the required JSON.\nParse error: {msg}\nOffending region: {excerpt}\nRespond with ONLY the JSON object, no explanation.` where `msg` = the JSONDecodeError message ≤ 300 chars and `excerpt` = the region around `err.pos` (±150 chars) if available else the last 400 chars of the response (total ≤ 500 chars). Attempt 2 runs the ladder too; final failure → censused fallback.
- No mid-run re-prompt for truncation; no same-prompt retry ever again.

### D4 — Parse-boundary recovery ladder (`_parse_json_robust`)
`_parse_json_robust(response, *, stats) -> dict` raises `_ParseError` on final failure. Rungs per attempt:
1. **Canonical** — existing `_parse_json` (fences, brace-balance, tail-cuts). Success → return.
2. **Sanitize (H2, output-side)** — string-aware scan (same in_str/esc tracker as `_parse_json`); escape raw C0 control chars (0x00-0x1F, incl. raw newlines/tabs) INSIDE string literals as their JSON escapes (`\n`, `\t`, `\uXXXX`); structural whitespace untouched. Re-parse. Success → `stats["recovery"]["sanitize"] += 1` + return. On rung-2 REPARSE FAILURE, the sanitized text (when it differs from the original) becomes the `working` input to rungs 3 and 4; a mis-tracked scan is backstopped by the D5 schema gate before any rung-3/4 output is accepted (worst case it fails schema and falls through — never corrupting). The event "rung 2 altered but did not parse" increments `stats["recovery"]["sanitize_insufficient"]` — the gate can measure the contamination-repair gap.
3. **Bounded repair** — runs on the **`working` text from rung 2** (sanitized when it differs from the original, else original) — the H2∧H3/structural intersection (raw control char inside a string AND a missing comma / unterminated object) is otherwise unrecoverable and falls to data-loss partial-accept; bounded, schema-gated (D5), first-valid-wins: (a) unterminated object → append `}` up to 8 closers; (b) unambiguous missing commas at boundary joins (`}"{`, `]"{"`, `}"[`, `]"[`, `"["`… bounded rule list); (c) trailing junk (already in tail-cuts). Re-parse + schema-validate (D5). Success → `stats["recovery"]["repair"] += 1` + return. No free-form json-repair library, no unbounded heuristics.
4. **Schema-validated partial-accept (H3)** — operates on the **`working` text from rung 2** (longer valid prefixes under contamination — cutting the original recovers less); progressive prefix cuts at item boundaries (`}`/`]` positions from the tail, bounded ≤ 200 candidates); the longest prefix that parses AND passes schema validation (D5) with ≥ 1 non-empty **embed** section (`entities`/`points`/`events`/`operators` — matching the S4 caller's merge condition, so a partial the caller would discard can never be falsely classed `partial_parse`) is accepted → `stats["partial"] = True` (caller appends the `partial_parse` error string + census; the partial embed list IS used — merge_embed_lists preserves the S2 base, E4 intact). A truncated-to-empty prefix never counts; a prefix with only `chain_notes`/`link_before_create`/`retractions` non-empty falls through to raise.
5. **Raise** `_ParseError(str(last), truncated=…, attempt=…, excerpt=…)`.

Caller wiring (`extract_session_v2`): after a successful `run_s2`/`run_s4`, `stage_stats.get("partial")` → append the error string + `_bump_census_class(error_census, "partial_parse")`; `stats["recovery"]` rolls into the session result (never an error string).

### D5 — Output-shape schema validator (structural, permissive on extras)
`_validate_output_shape(parsed) -> (bool, issues)` from a machine-readable `_OUTPUT_SCHEMA` derived from `OUTPUT_CONTRACT` (kept adjacent with a coupling comment — contract edit → schema edit is a NEW coupling, tracked in Open questions): top-level is a dict; each present section (`entities`/`events`/`points`/`operators`/`chain_notes`/`link_before_create`/`retractions`) is a LIST of dicts; required keys with primitive types — `entities: name/kind`; `events: content/eventKind`; `points: content`; `operators: src/dst/op_type`; `chain_notes: chain/finding/action`; `link_before_create: searched_for/found`; `retractions: content|id`. Unknown keys and empty arrays are VALID (fields ride through by reference; S5's execution validation owns semantic repair). Structural-only strictness: catches valid-JSON-wrong-shape (e.g. `points` as a dict) → error-informed re-prompt. Reused by rungs 3-4.

### D6 — JSON-mode parity on the direct path + pre-flight probe (H1)
`DeepSeekDirectModel.complete` mirrors `OpenRouterModel`: when `os.environ.get("TORTOISE_JSON_MODE", "1") == "1"` (read at call time) the body gains `"response_format": {"type": "json_object"}`. DeepSeek's "json"+example requirement is already satisfied (S2/S4 prompts contain "JSON object" + the OUTPUT_CONTRACT example). The `TORTOISE_JSON_MODE=0` escape stays documented. **Pairing note:** JSON mode does NOT fix truncation (breaks at max_tokens) — the ladder (D4) is the truncation pairing; no cap raise in this issue. New `tools/longmem_eval/probe_json_mode.py`: `--n` (default 10) S2-shaped completions per mode (on/off, same prompts), verdict ∈ {honored, ignored, rejected, inconclusive}: rejected = any HTTP 400/404; honored = (malformed-rate(mode-on) < malformed-rate(mode-off) AND ≥ 1 parse success) OR (mode-on malformed == 0 AND mode-off malformed > 0) — the both-zero case is **inconclusive** (n too small to distinguish an inert mode from a clean model; a false-honored would mislabel the H1 test); ignored = statistically indistinguishable (recorded as heuristic in the run record — no significance claim at n=10; a strictly WORSE mode-on rate is still verdict `ignored` but records `mode_delta: "worse"` in the verdict JSON so the harmful-direction signal is not lost — the ladder + C4 backstop it); inconclusive = n too small / transient errors. **Probe-verdict → run-mode mapping (operational):** `rejected` → the closing run aborts pre-flight OR re-runs with `TORTOISE_JSON_MODE=0` (documented escape) — never a wholesale-400 mid-run; `inconclusive` → re-probe at `--n 20` (or add an S4-shaped sample — S4 is the heavier, more truncation-prone call) and make an explicit mode decision that lands in the run record; `honored`/`ignored` → proceed with the verdict noted. **Probe adapter selection (H1 validity):** the probe must exercise the pilot's path — `DeepSeekDirectModel` when `DEEPSEEK_API_KEY` is set AND `TORTOISE_EXTRACTOR_PROVIDER != "openrouter"`, else the resolved default — or its verdict does not test H1. Verdict JSON written to `--out`; the closing run record (Task 5) consumes it.

### D7 — Warning-only truncation readout (criterion 3, structural)
`ingest_v2` threads `stats["llm"]` (calls/retries/truncated) + `stats["recovery"]` from each session's extractor result (summed; the session-level exception path contributes 1 call / 0 truncated). `run.py` outcome gains `llm_calls`/`llm_retries`/`llm_truncated`/`recovery`. `report.py` computes `integrity.truncated_valid_qids = [qid for o in outcomes if o["llm_truncated"] > 0 and o["valid"]]` + count — warning-only, never in `error_census` (criterion 2's class universe unchanged). Criterion 3 becomes structural: every truncated question is either an error class (invalid) or a listed `truncated_valid` qid — no truncation is unrecorded.

### D8 — Report projection persistence (additive, M1-regression-class pin)
`outcomes_to_report` key list gains `ingest_error_text` (already computed at run.py:1338), `llm_calls`, `llm_retries`, `llm_truncated`, `recovery`. `test_outcomes_to_report_golden_shape`'s exact key-set is updated to the new contract — the planned, intentional contract change (M7 Gate-4 precedent; #1414 parity battery is hash-based on methodology → additive keys verified safe).

### D9 — Census-equality enforcement (criterion 2)
Deterministic-append rule: every `errors.append` site must bump exactly one census class (1:1). Enforced by (a) the D1 invariant unit test, (b) an integration test asserting per-question `n_ingest_errors == sum(error_classes.values())` and report-level `integrity.error_census == Σ per-question error_classes` on a synthetic mixed-error outcome set. `report.py`'s existing rollup (Counter over `error_classes`) already satisfies the report half once per-question equality holds.

### D10 — Two-stage escalation, consumed by the closing-run gate (closing criteria 1–4, NOT `integrity.valid`)
Closing run (fresh non-resumed 50-Q) reads: the **parse-failure family** = `{parse_error, truncated_parse_error, partial_parse}`, counted **per question** (a question with S2 `parse_error` + S4 `truncated_parse_error` counts ONCE — the family is a qid set, not a class-count total; pinned in the run-record template). #1746 **closes when ALL of**: **C1** family ≤ 1 question; **C2** census equality holds (D9) on all outcomes; **C3** no UNRECORDED truncation — every outcome with `llm_truncated > 0` is either in an error class (invalid) or listed in `truncated_valid_qids` (recorded-ness reading; a non-empty `truncated_valid_qids` is a #1747-flagged observation, not a close-blocker — benign truncation recovered by the ladder is legitimate); **C4** no accuracy/retrieval regression vs the pinned baselines (accuracy ≥ 0.74, `session@20` ≥ 0.90, `chunk_evidence_recall@20` ≥ 0.5465 vs the blended-50 same-question-set; fresh-30 clean-population reference 0.867 / 1.00 / 0.567 stated alongside; CI/margin rule). The `integrity.valid == true` flag is DELIBERATELY NOT a closing condition: at threshold 0.0 it requires ZERO error-class questions, which would make C1's "≤ 1" vacuous and reproduce #1747's unreachability inside this issue's own gate — the flag's semantics are #1747's lane.
Escalation taxonomy (mutually exclusive; both fire when both apply):
- **C1 violated** (family > 1 question) → **escalate to the B-delta follow-up** (#XXXX, filed in Task 5): the S4 gaps-only contract as a SECOND fresh run (documented escalation, never a mid-run patch). The family is the honest readout — a run that trades `truncated_parse_error` for `partial_parse` en masse has NOT closed — no class-gaming loophole. #1746 closes upon the closing-run verdict + escalation decision; the second run's execution and contract land in #XXXX's scope.
- **Non-parse-class invalidity** (e.g. `transient_429`/`s5_failed`/`entity_resolution_failed` qids) → **#1747 trigger** (criterion + justification policy; the run records a justified gate via the existing `threshold_violation_justification` surface) — not this issue's fix lane.
- **C2 violated** (census inequality) → harness bug in THIS issue's deliverables (D9/D1 machinery) → fix + re-run, not an external escalation.
- **C3 violated** (unrecorded truncation under the recorded-ness reading) → harness bug in D7's readout → fix + re-run, not an external escalation.
- **C4 violated** (accuracy/retrieval regression vs the baseline) → do NOT close; investigate vs the cited baseline (re-run or re-baseline; the #XXXX follow-up owns criterion-4 re-baseline) — a content regression is the one outcome no census can excuse.

### D11 — #1695 coordination: zero prompt-TEXT changes
This plan changes no S2/S4 template text: the ladder operates on provider OUTPUT; JSON mode is a request-body field; the re-prompt error block is assembled at call time from the original message (not a template edit); census/projection are harness-side. Input-side quote sanitation (B's second lever) is REJECTED for this issue (see Rejected alternatives) — it would alter what the model sees in the source-transcript block and risk the M6 quote→turn anchor match. If #1695 lands before the closing run, the run measures a joint effect — the run record MUST note it (Task 5 template).

---

## Implementation steps

### Task 1: Census + finish_reason telemetry foundation (scope a, extractor side)

**Intent:** make the parse-error class truthful (truncation vs sloppiness) and make `n_ingest_errors == sum(error_census)` structurally achievable — the eyes of the decision gate.
**Acceptance:** `_ParseError` carries `truncated`; `_bump_census` emits `truncated_parse_error` vs `parse_error`; the four previously-uncensused append paths (S1 summary, entity resolution, no-embed-list, S5) each append a deterministic class; `len(errors) == sum(error_census.values())` on a synthetic mixed-error session; `ingest_v2` threads `stats["llm"]` + `stats["recovery"]` to its stats.
**Files:**
- Modify: `tortoise/extractor_v2.py` (`_ParseError`, `_bump_census`, `_bump_census_class`, `extract_session_v2` append sites, `_complete_parsed` capture)
- Modify: `tools/longmem_eval/ingest_v2.py` (session-loop llm/recovery rollup — the LIVE copy used by run.py, ~line 655, which shadows the earlier definition at ~line 358; patch the live copy and note the dead duplicate, do not patch both)
- Test: `tests/test_extractor_reliability.py`

**Step 1 (test):** `test_extract_census_truncated_parse_error` — adapter with `last_finish_reason="length"` returning garbage → census `truncated_parse_error` (not `parse_error`), `llm["truncated"] == 1`. Extend the existing `test_extract_census_parse_error` (finish_reason None → still `parse_error`).
**Step 2 (implement):** `_ParseError(ValueError)` gains `truncated/attempt/excerpt`; `_bump_census` class-decides on `e.truncated`; add `_bump_census_class(error_census, cls)` for class-explicit bumps.
**Step 3 (test):** `test_census_equality_mixed_errors` — synthetic session with S1 chunk failure + S2 parse failure + S4 truncation + no-embed-list + entity-resolution exception + S5 exception → `len(out["errors"]) == sum(out["error_census"].values())` and each of the four new classes present.
**Step 4 (implement):** deterministic appends at the four sites (summary line → `s1_chunk_summary` — the bump fires under the SAME condition as the append (`failed_chunks > 0`), so a clean run has no stray bump; resolution → `entity_resolution_failed`; no-embed-list → `empty_embed_list`; S5 → `s5_failed`).
**Step 5 (test):** `test_ingest_v2_llm_and_recovery_rollup` — mini pipeline through `ingest_v2` with a recovering-then-failing session mix → `ingest_stats["llm"]` sums calls/retries/truncated across sessions; `ingest_stats["recovery"]` rolls counters. **Step 5b (test):** `test_llm_truncated_warning_only_not_error` — a session with `truncated` > 0 but a valid recovered parse → outcome valid, no `truncated_parse_error` in `error_classes`, `llm_truncated` surfaced (the D7 warning-only contract).
**Step 6 (implement):** the LIVE `ingest_haystack_v2` copy (line ~655; the ~358 copy is a tracked pre-existing duplicate, #1744 — leave untouched) collects `out["stats"]["llm"]`/`out["stats"]["recovery"]` into its stats; exception path contributes `1` call; the v1 `ingest_haystack` lane is OUT OF SCOPE (its extractor output has no `stats["llm"]`).
**Step 7 (verify):** `uv run pytest tests/test_extractor_reliability.py -v`; **Step 8:** commit `feat(1746): parse census truth — truncated_parse_error + deterministic append classes`.

### Task 2: Report projection + warning-only truncation readout (scope a, harness side)

**Intent:** the report can answer "which question failed, why, with what first error, and was any call truncated" — criterion 2 + 3 auditable post-hoc.
**Acceptance:** outcomes project `ingest_error_text`/`llm_calls`/`llm_retries`/`llm_truncated`/`recovery`; `integrity.truncated_valid_qids` present and excluded from `error_census`; golden-shape pin updated.
**Files:**
- Modify: `tools/longmem_eval/run.py` (outcome fields from `ingest_stats`, projection key list)
- Modify: `tools/longmem_eval/report.py` (integrity truncation readout)
- Test: `tests/test_longmem_runner.py`

**Step 1 (test):** update `test_outcomes_to_report_golden_shape` — new keys in the pinned set (intentional contract change, Gate 4 note).
**Step 2 (implement):** outcome gains `llm_calls/llm_retries/llm_truncated` (from `ingest_stats["llm"]`) + `recovery`; projection key list extended with the 5 keys + `ingest_error_text`.
**Step 3 (test):** `test_report_truncation_readout_warning_only` — outcomes incl. a truncated-valid question → `integrity.truncated_valid_qids` lists it; its llm fields present; `error_census` does NOT contain a truncation key for it; `valid` stays true.
**Step 4 (implement):** `build_report` computes `truncated_valid_qids` + count into `integrity`.
**Step 5 (test):** `test_census_equality_integration_mixed_outcomes` — synthetic mixed-error outcome set: per-question `n_ingest_errors == sum(error_classes.values())`; `integrity.error_census == Σ per-question error_classes`; `valid` false exactly on questions with error classes.
**Step 6 (verify):** `uv run pytest tests/test_longmem_runner.py -v`; **Step 7:** commit `feat(1746): report projection persists error text + llm telemetry; truncated_valid readout`.

### Task 3: JSON-mode parity on DeepSeekDirectModel + pre-flight probe (H1)

**Intent:** test H1 for pennies and make the direct path's JSON-mode behavior identical to the OpenRouter path — reversible, prompt-agnostic.
**Acceptance:** `DeepSeekDirectModel.complete` sends `response_format={"type":"json_object"}` when `TORTOISE_JSON_MODE=1` (default) AND the prompt requests JSON ("json" present, case-insensitive — #1782) and omits it when `=0` or the prompt lacks the text "json" (DeepSeek 400s on the mode-without-token combination; non-JSON calls like the preflight probe/ping omit the mode); `probe_json_mode.py` CLI produces a verdict JSON applying the D6 rules (both-zero → inconclusive pinned in `test_probe_verdict_logic`); the live probe selects the adapter per the D6 rule and the @slow live test skips when the direct-path key/provider env is absent; a dry-run mode is unit-testable.
**Files:**
- Modify: `tortoise/model_adapters.py` (`DeepSeekDirectModel.complete`)
- Create: `tools/longmem_eval/probe_json_mode.py`
- Test: `tests/test_models.py` (or `tests/test_model_adapters_routing.py`), new `tests/test_probe_json_mode.py`

**Step 1 (test):** `test_deepseek_direct_json_mode_default_on` / `test_deepseek_direct_json_mode_disabled` — monkeypatch the adapter's `_session.post` to capture the body; assert presence/absence of `response_format` under `TORTOISE_JSON_MODE` 1/0 (mirror `test_openai_build_request`'s body-assertion style).
**Step 2 (implement):** add the env toggle + `response_format` to `DeepSeekDirectModel.complete` (read at call time, same pattern as `OpenRouterModel`).
**Step 3 (test):** `tests/test_probe_json_mode.py::test_probe_verdict_logic` — dry-run with a fake adapter per scenario: honored (mode-on parses, mode-off doesn't), ignored (identical), rejected (HTTP 400), inconclusive (n too small) — asserts verdict strings + the JSON report shape.
**Step 4 (implement):** `probe_json_mode.py` — `probe_json_mode(adapter, *, n, dry_run=False)` runs n S2-shaped completions per mode, computes malformed-rate + finish_reason distribution, returns the verdict dict; CLI `--n/--model/--out/--dry-run`.
**Step 5 (verify):** `uv run pytest tests/test_models.py tests/test_probe_json_mode.py -v`; **Step 6:** commit `feat(1746): JSON-mode parity on the direct path + honor probe`.

### Task 4: Parse-boundary recovery ladder (scope b — sanitize, repair, partial-accept, re-prompt)

**Intent:** recover parseable, schema-valid output from H2 contamination and H3 truncation at the boundary, with every recovery recorded and every failure classed by mechanism — the consumption-side floor.
**Acceptance:** `_parse_json_robust` implements rungs 1-5 with per-rung recovery counters; `_complete_parsed` skips the retry on first-attempt truncation and error-informs on stop-class; `partial_parse` is a recorded error class with the partial list used; recovered outputs are schema-valid and warned, not errored; no prompt-TEXT change.
**Files:**
- Modify: `tortoise/extractor_v2.py` (`_parse_json_robust`, `_validate_output_shape` + `_OUTPUT_SCHEMA`, `_complete_parsed` retry policy, `extract_session_v2` partial wiring, `_error_excerpt`)
- Test: `tests/test_extractor_v2.py`, `tests/test_extractor_reliability.py`

**Step 1 (test):** `test_sanitize_rung_recovers_control_chars` — output with a raw newline inside a string value → parses; the value round-trips (newline preserved); `stats["recovery"]["sanitize"] == 1`. `test_sanitize_preserves_structural_whitespace` — control chars between tokens untouched; a bad sanitize never feeds repair (construct a mis-track case → still falls through cleanly).
**Step 2 (implement):** rung 2 string-aware escape (in_str/esc tracker; C0 inside strings → JSON escapes; discard-on-failure).
**Step 3 (test):** `test_repair_rung_missing_comma` / `test_repair_rung_trailing_brace` — bounded repairs recover full output; `recovery["repair"] == 1`; repair result schema-validated.
**Step 4 (implement):** rung 3 bounded repair rule list + schema check.
**Step 5 (test):** `test_schema_validator_accepts_contract_shape` / `test_schema_validator_rejects_shape_mismatch` — a valid embed-list passes; `{"points": {...}}` (dict not list) fails; unknown keys + empty arrays pass (ride-through).
**Step 6 (implement):** `_OUTPUT_SCHEMA` + `_validate_output_shape`.
**Step 7 (test):** `test_partial_accept_recovers_truncated_list` — S4 output cut mid-`points` item → longest valid prefix accepted, `partial_parse` in census + error string, `valid=false`, merged list ≥ S2 base. `test_partial_accept_rejects_empty_prefix` — truncation before any item → failure, not partial.
**Step 8 (implement):** rung 4 prefix-cut partial-accept + `extract_session_v2` partial wiring (`stage_stats["partial"]` → error string + `_bump_census_class("partial_parse")`).
**Step 9 (test):** `test_truncated_skips_same_prompt_retry` — `last_finish_reason="length"` attempt 1 → exactly 1 call, census `truncated_parse_error`. Update `test_rejects_unparseable`/`test_parse_retry_recovers` — attempt-2 user message carries the parse-error excerpt (assert `"did not parse"` + the offending-region text in the second call).
**Step 10 (implement):** `_complete_parsed` retry policy (skip-on-length; error-informed re-prompt with bounded `_error_excerpt`); ladder per attempt.
**Step 11 (verify):** `uv run pytest tests/test_extractor_v2.py tests/test_extractor_reliability.py -v`; **Step 12:** commit `feat(1746): censused parse-recovery ladder (sanitize → repair → partial-accept → error-informed re-prompt)`.

### Task 5: Docs, closing-run protocol, companion/follow-up issues

**Intent:** the closing run is interpretable, gated, and the escalation path + neighbors are explicit — no absorbed scope, no lost coordination.
**Acceptance:** README/plan sections document the new census keys, `TORTOISE_JSON_MODE` direct-path behavior, probe usage, and the closing-run record template; follow-up issues filed (#1747 companion noted; B-delta follow-up filed with the trigger); #1695 coordination note in the plan and issue.
**Files:**
- Modify: `tools/longmem_eval/README.md` (probe + census + readout)
- Modify: `docs/epics/2026-08-20-1509-extractor-v3/04-plan.md` (run protocol: step-3/5 notes reference the probe + parse-family census) — advisory only
- Test: n/a (docs + orchestration)

**Step 1:** document the census vocabulary (D1), the readout (D7), the probe CLI, and `TORTOISE_JSON_MODE` direct-path semantics in the README.
**Step 2:** write the closing-run record template (this plan's Conditional-gate section) into the run-protocol notes with these mandatory fields: (1) probe verdict + **effective `TORTOISE_JSON_MODE`** (recorded in report methodology so closing C1 is interpreted against the actual configuration); (2) `#1695` landing status (joint-effect note if landed); (3) **full error-class qid enumeration** — EVERY outcome with an error class, grouped by class, with the parse-family readout (per-question qid set per D10) shown separately — so a #1747 justification is auditable (it must enumerate and account for every non-family error-class qid, e.g. `empty_embed_list` total-loss questions); (4) criterion-1 family count + the confirmed two-class aggregate; (5) census-equality check (D9); (6) `truncated_valid_qids`; (7) criterion-4 comparison with the population pinned: the FULL all-fresh 50-Q including `partial_parse`-recovered questions (never exclude recovered-degraded from accuracy — that is class-gaming; consistent with the 0.867 baseline which counted invalid-but-answered questions), the previously-resumed 20 reported separately, per-metric targets `session@20` ≥ 0.90 and `chunk_evidence_recall@20` ≥ 0.5465 (blended-50; fresh-30 reference 1.00 / 0.567) and accuracy ≥ 0.74 (fresh-30 reference 0.867), a CI/margin rule (e.g. 90%-CI lower bound or ±0.05 point margin), and the baseline source cited (this plan's Context + the 1745 plan §1.2 — canonical references; the source doc lives in the gitignored worktree); (8) escalation decision per the D10 taxonomy (every closing-run outcome has a branch).
**Step 3:** file the follow-up issues (per file-extra-issues): (a) B-delta S4 contract with the D10 trigger text; (b) session-dedup/caching across questions (pre-existing candidate); note #1747 as the companion (NOT absorbed). Notify the user with the 📋 summary.
**Step 4:** commit `docs(1746): closing-run protocol, census vocabulary, escalation trigger`.

---

## Tests

### Verification Plan (test-routing, complexity: UX n/a / Architecture high / Ontology low / Accessibility n/a)

| Layer | Depth | Applies | Notes |
|---|---|---|---|
| Unit | full | ✅ | ladder rungs, census classes, retry policy, schema validator, probe verdict logic, adapter body |
| Integration | full | ✅ | census equality (mixed-error set), llm threading, projection/golden shape, report readout |
| E2E smoke | — | ⛔ not in CI | fresh non-resumed 50-Q is @slow, gated on dataset+keys (run protocol steps 3/5) |
| UX / content / config / research domains | — | skipped | no UI, no content pipeline, no config surface, no external research domain |

### Test list (new/updated)

`tests/test_extractor_v2.py`:
- updated `test_rejects_unparseable` / `test_parse_retry_recovers` (error-informed attempt-2 prompt asserted) (Task 4)
- `test_sanitize_rung_recovers_control_chars`, `test_sanitize_preserves_structural_whitespace` (Task 4)
- `test_repair_rung_missing_comma`, `test_repair_rung_trailing_brace` (Task 4)
- `test_schema_validator_accepts_contract_shape`, `test_schema_validator_rejects_shape_mismatch` (Task 4)
- `test_partial_accept_recovers_truncated_list`, `test_partial_accept_rejects_empty_prefix` (Task 4)
- `test_truncated_skips_same_prompt_retry` (Task 4)

`tests/test_extractor_reliability.py`:
- updated `test_extract_census_parse_error` (finish_reason=None → `parse_error` preserved) (Task 1)
- `test_extract_census_truncated_parse_error` (Task 1)
- `test_census_equality_mixed_errors` (Task 1 — the D1 invariant)
- `test_ingest_v2_llm_and_recovery_rollup` (Task 1)
- `test_llm_truncated_warning_only_not_error` (Task 1 — truncated-valid is not an error class at the extractor level)

`tests/test_longmem_runner.py`:
- updated `test_outcomes_to_report_golden_shape` (new key contract — **intentional contract change**, M1-regression guard) (Task 2)
- `test_report_truncation_readout_warning_only` (Task 2)
- `test_census_equality_integration_mixed_outcomes` (Task 2 — criterion 2)

`tests/test_models.py` (or `tests/test_model_adapters_routing.py`): `test_deepseek_direct_json_mode_default_on` / `_disabled` (Task 3).

`tests/test_probe_json_mode.py` (new): `test_probe_verdict_logic` (honored/ignored/rejected/inconclusive via fake adapter) (Task 3); `@slow test_probe_live_direct_path` (real keys, gated — skip if absent).

**Negative-case ownership (05-detailed-e2e):** truncated output never silently valid (`test_report_truncation_readout_warning_only` + `test_truncated_skips_same_prompt_retry`); recovered-but-degraded never unrecorded (`test_partial_accept_recovers_truncated_list`); census never drifts (`test_census_equality_*`).

## Cross-lane interfaces

| Lane | Interface | Contract |
|---|---|---|
| **#1695** | S2/S4 prompt text | Zero prompt-TEXT changes here (D11). If #1695 lands first, closing run = joint effect — run record MUST note it. Input-side sanitation rejected for anchor-fidelity reasons (Rejected alternatives) — a future input-side change lives in #1695's lane. |
| **#1747** | integrity gate-criterion semantics | Companion, NOT absorbed. #1746 defers to #1747 ONLY the threshold/`valid` semantics + justification policy — the parse-failure family definition is DECIDED here (D10), not deferred. |
| **M3/M4 (#1524)** | retry/census taxonomy | This plan EXTENDS the D3/D6 vocabulary additively (new classes, same `_bump_census`/P2 delegation); retry budget unchanged (`_PARSE_RETRIES=1`); M4 `valid`/`error_classes` semantics unchanged. |
| **M7 (#1527)** | report contract | Projection additions are additive keys; golden-shape pin updated in the same change (Gate-4 precedent); #1414 parity battery hash-based → safe. |
| **M2** | pre-flight | The probe (Task 3) is pre-flight-adjacent; runs before the closing 50-Q like M2's billing/judge checks; probe verdict lands in the run record. |
| **M6** | evidence marking / quote→turn resolver | Untouched; the rejected input-side sanitation was rejected partly to protect the resolver's anchor match. |
| **E2E-2** | integrity.valid + census | This plan's deliverables are the assertion targets: `truncated_valid_qids` readout, parse-family census, census equality. ⚠ Divergence note: the closing gate deliberately EXCLUDES `integrity.valid` (D10 — vacuous at threshold 0.0; threshold semantics deferred to #1747), so the closing run does not assert E2E-2's `valid=true` letter. |
| **Run protocol** | step 3 pilot / step 5 500 | Closing 50-Q is fresh + non-resumed (kills the 20/50 checkpoint-resumed confound); probe + record are prerequisites for interpretation. |

## ⛔ Conditional-gate notes

**Gate 1 — No ontology / architecture / new-field change.** All changes are eval-instrumentation + parse-boundary behavior: new census keys, `stats` keys, projection keys, report keys — no new kinds, edge types, expansion packs, or Point properties. The ladder changes which dict `run_s2`/`run_s4` return on degraded output (partial list instead of exception), never the payload schema.

**Gate 2 — Census equality is enforced by construction.** Deterministic-append rule (D1/D9) + the D1 invariant unit test + the integration equality test. A review checklist item: any future `errors.append` in `extract_session_v2` must pair a census bump.

**Gate 3 — #1695 sequencing.** No prompt-TEXT change ships here (D11). If #1695 lands in the same window, the closing run is a JOINT-EFFECT measurement — the run record states it; attribution is the parse-family census, not the aggregate alone.

**Gate 4 — Golden-shape contract change is intentional.** `test_outcomes_to_report_golden_shape`'s pinned key set changes in Task 2 (additive projection keys). Same-change update required; #1414 parity battery stays green (hash-based).

**Gate 5 — Closing run is @slow, gated.** Fresh non-resumed 50-Q + probe + accuracy/retrieval baseline comparison run OUTSIDE CI (dataset + keys gated, run protocol steps 3/5). The run record template (Task 5) is the deliverable that makes the gate auditable.

**Gate 6 — Escalation trigger (D10).** If the parse-family census (incl. `partial_parse`) exceeds the criterion-1 target (family > 1 question) on the closing run, the B-delta follow-up (#XXXX) fires as a SECOND fresh run — documented escalation, never a mid-run patch; the possibility of the second run is acknowledged in this issue's budget, while its execution and contract land in #XXXX's scope — this issue's scope ends at the closing-run verdict + escalation decision.

## Open questions

1. **`partial_parse` in the criterion-1 aggregate — RESOLVED (D10):** the family `{parse_error, truncated_parse_error, partial_parse}` counted per question IS the honest criterion-1 readout; Context criterion 1 is aligned to it; only the threshold/`valid` semantics (NOT the family definition) stay #1747's lane.
2. **Schema-validator coupling:** `_OUTPUT_SCHEMA` is hand-derived from `OUTPUT_CONTRACT` — a new contract-edit→schema-edit coupling the repo doesn't have today. Should the schema be generated from the contract text (fragile) or kept hand-written with a pinning test (this plan's choice)? Review gate decides.
3. **Repair rung scope:** bounded comma/brace repair only. If the closing run shows `recovered_repair` dominating, should a fuller repair (or `json-repair` dep) be adopted? Currently no new third-party deps (zero-deps gate); the decision is evidence-based post-run.
4. **`truncated_valid` at 500-Q scale:** the readout lists truncated-valid qids; if that count is material (H3 silent loss), does the 500-Q gate (E2E-2 `valid`) need them surfaced as a separate integrity sub-check, or is the readout enough? #1747-adjacent; flag to the companion.
5. **Probe placement:** `tools/longmem_eval/probe_json_mode.py` vs M2's pre-flight module — merge into the pre-flight flow later, or keep standalone? Default: standalone, referenced by the run protocol (Task 5).
6. **Input-side sanitation (rejected) revisit:** if `recovered_sanitize` dominates the closing census (contamination is the main class), reconsider input-side escaping INSIDE #1695's lane with the M6 resolver's normalization updated in the same change — currently rejected for anchor-fidelity risk.

## Follow-up issues (filed per file-extra-issues — not absorbed)

- **#XXXX — S4 gaps-only delta contract (Approach B):** the two-stage escalation lever (D10). Trigger: closing run's parse-family census exceeds criterion 1 OR `partial_parse`/`truncated_valid` material at 500-Q scale. Owns deep-merge semantics + delta validation + criterion-4 re-baseline. Prompt-text → coordinates with #1695.
- **#YYYY — session-dedup/caching across eval questions:** pre-existing candidate from the confirmed problem; unrelated to parse robustness; file independently.
- **#1747 (companion, exists):** gate-criterion semantics — explicitly NOT absorbed; the run record's threshold/`valid` interpretation questions route there.

---

*Status: draft — issue-scoping Phase 5 converge output; awaiting solution-verify gate + plan-review gate before execution.*
