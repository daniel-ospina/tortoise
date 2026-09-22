# #1746 — Solution approaches (divergence): S2/S4 unparseable-JSON robustness

> Issue-scoping solution alternatives for the confirmed problem (11/50 pilot questions with
> `_ParseError`, H1/H2/H3 mechanisms, closing criteria 1-4). No winner selected — this is the
> divergence artifact. Scope (a) telemetry+census fidelity is the SHARED foundation of every
> approach; the approaches differ only in scope (b), where the parse-robustness defense lives.
> Verified against source 2026-08-26.

## Shared foundation (scope a — all approaches)

Every approach carries the same telemetry/census substrate; it is the decision gate's eyes:

1. **Re-link per-call `finish_reason`/`truncated` capture** (extractor_v2.py:3139 `_complete`) to the
   failing `_ParseError` — the FIRST-attempt finish_reason is the class-decision signal
   (`length` → truncation hypothesis; `stop` → contamination/sloppiness).
2. **New census classes, deterministic-append everywhere**:
   - `truncated_parse_error` (first-attempt `finish_reason=="length"` AND parse failed → censused as
     truncation, not plain `parse_error`);
   - deterministic classes for the uncensused append paths: no-embed-list, S5-failed,
     entity-resolution-failed, S1-chunk-summary — so `n_ingest_errors == sum(error_census)`
     (motivating datum: pilot 16 error strings vs 14 census entries).
3. **Persist first-error text + `stats.llm` + `truncated` through the report projection**
   (run.py:1330-1359 → report.py) so attribution is auditable post-hoc, not inferred from counts.
4. **No UNRECORDED truncation with valid=true** — every truncated outcome (even one that parses
   cleanly via the tail-cuts — the silent-truncation success class) carries its census class +
   warning. Criterion 3 is structural, not statistical.
5. **Fresh non-resumed 50-Q** for the gate (kills the 20/50 checkpoint-resumed confound).
6. **Criterion 4** (accuracy/retrieval gate: overlapping-30 fresh-vs-fresh ≈0.867; retrieval
   session@20 + chunk_evidence_recall@20 ≈0.5465, CI/margin rule) is re-measured on the fresh run
   in all approaches. Non-parse-class gate failure → companion #1747 trigger (unchanged).

---

## Approach A — Parse-Boundary Recovery Ladder

**Defense location: parse boundary (consumption).**

### Description
Leave S2/S4 prompts, the S4 full-re-emit contract, the provider path, and the 8000 cap untouched.
Replace the binary parse-or-retry in `_complete_parsed`/`_parse_json` with a bounded, census-visible
recovery ladder. Each rung is a recorded event; failures end as either recovered output (warned) or
a censused, warned fallback — never silent.

Ladder per attempt (rungs 1-4 in-process on the same completion; rung 5 = the retry; rung 6 = floor):
1. `_parse_json` canonical (unchanged: fences, brace-balance, tail-cuts).
2. **Sanitize** — strip/escape raw control chars (incl. newlines) inside string literals only
   (structural whitespace preserved). Neutralizes H2's output-side vector without waiting for
   attribution. Cheap, idempotent.
3. **Repair** — json-repair-style fixes on sanitized text: missing commas, unbalanced trailing
   braces, trailing junk (research: parse → repair beats vague retry).
4. **Schema-validated partial-accept** — on repair failure, take the longest valid-JSON prefix from
   the brace-balance scan; validate against an OUTPUT_CONTRACT-derived schema; accept only
   schema-valid sections. Outcome tagged `partial=true` + warning + census `partial_parse`. Converts
   the silent-truncation success class into a RECORDED one (criterion 3); recovers H3 mid-string
   truncation without data-loss silence.
5. **Error-informed re-prompt** — the single retry (keep `_PARSE_RETRIES=1` budget) re-prompts with
   the parse/repair error excerpt appended to the user message (research: error-informed re-prompt
   beats same-prompt retry, which is deterministic for truncation/contamination).
6. **Censused fallback** — still unparseable → S2 output stands / S4 gap-review skipped, census
   `truncated_parse_error` or `parse_error` + warning. Never `valid=true` with data loss.

### Files touched
- `tortoise/extractor_v2.py` — `_parse_json` rungs 2-4, `_complete_parsed` rung 5, new
  OUTPUT_CONTRACT schema validator, census classes.
- `tools/longmem_eval/run.py`, `tools/longmem_eval/report.py` — shared foundation projection.
- `tests/test_extractor_v2.py` — `test_rejects_unparseable` retry-prompt contract changes; new
  sanitize/repair/partial-accept/schema tests.
- `tests/test_extractor_reliability.py` — new census-class tests.

### Architecture
Single choke point between provider output and graph writes. Ladder layers are independently
testable with fixtures (raw control chars, missing comma, mid-string truncation, fence-wrapped).

### Risks
- Repair/partial-accept can emit semantically-degraded-but-valid output; schema gates structure,
  not meaning — every recovery must stay censused + warned so the report can audit what repair did.
- Ladder complexity is real surface (5+ rungs); rung 4 overlaps the existing tail-cuts (rung 1) and
  must be justified by recorded recovery events, not assumed.
- Root cause stays: every truncated outcome still costs a retry + warning. If H3 dominates, the
  ladder makes failures visible, not absent.

### Tradeoffs
Fastest to land; zero prompt churn → zero #1695 collision; hypothesis-agnostic (works whichever of
H1/H2/H3 dominates); schema validator is reusable by Approach C. Against: symptom-grade — failure
surface remains, and the gate must decide whether recovered-and-warned (`partial_parse`) counts as
"clean" for criterion 1 (aggregated class definition).

### Best-fit-if
Attribution (H1 vs H2 vs H3) is still uncertain and we want a robust floor plus an informative fresh
run before committing to a bigger lever; #1695 prompt sequencing is near-term; or we want the
cheapest defense that satisfies criterion 3 immediately.

---

## Approach B — Source Reduction: S4 Delta Contract + JSON-Mode Parity + Input Sanitation

**Defense location: generation source (contract + input + provider parity).**

### Description
Three coordinated changes attacking root causes where output is produced:

1. **S4 contract inversion → gaps-only deltas.** S4 emits only delta instructions — `adds` (new
   items), `changes` (item-id → field-level updates: lifecycle, supersedes, slots, quote, note),
   `removes`/`retractions` — instead of the complete list. S4 output drops from ≥ S2 output (the
   7-8 vs 6 failure skew is the re-emit tax) to a small fraction of the 8000 cap: the H3
   mid-string truncation class becomes structurally impossible (no long string to truncate).
   Ingest replaces `merge_embed_lists`' identity-key union (extractor_v2.py:1228-1270) with a
   **field-level deep-merge** applying delta instructions onto the S2 base; `_s4_merge_stats`
   extends to count applied/changed/removed/rejected deltas.
2. **JSON-mode parity on the direct path.** Add `response_format={"type":"json_object"}` to
   `DeepSeekDirectModel.complete` (model_adapters.py:168-198) behind the same `TORTOISE_JSON_MODE`
   toggle (default 1). H1 is the UNTESTED lever — this is the cheap test. DeepSeek docs warn JSON
   mode breaks at max_tokens, which is exactly why (1) must land with it: the delta contract removes
   the truncation trigger JSON mode cannot survive. "json" + example already present in prompt.
3. **Input-boundary sanitation for E3 quotes.** Escape/strip raw control chars at template-fill time
   on the ≤200-char verbatim `quote` contract (and source-transcript block) so raw control chars
   can't ride into the output JSON the model copies verbatim. Neutralizes H2 at the input boundary —
   cheap and hypothesis-independent (the newline-density datum weakly disconfirms a content-level
   feature; the mechanism is raw chars in strings, which this removes regardless).

Retry: keep the same-prompt retry for sloppiness, but make it finish_reason-aware — first-attempt
`finish_reason=="length"` skips the same-prompt retry (deterministic truncation won't self-correct;
with the delta contract this branch should be ~never) → censused fallback. Otherwise error-informed
re-prompt with the error excerpt on attempt 2.

### Files touched
- `tortoise/extractor_v2.py` — S4_TMPL delta-grammar rewrite, `merge_embed_lists` → deep-merge +
  delta validation, `_s4_merge_stats`, E3 quote sanitation at template-fill, retry branch.
- `tortoise/model_adapters.py` — `DeepSeekDirectModel.complete` response_format.
- `tools/longmem_eval/run.py`, `report.py` — shared foundation + delta-application stats.
- `tests/test_extractor_v2.py` — S4 fixtures rewrite; deep-merge unit tests (dangling delta ref,
  field-level update semantics, removes); model_adapters body assertion.

### Architecture
The contract bounds output size (kills H3 structurally), JSON mode constrains structure (kills H1),
input sanitation removes the contamination vector (kills H2). The parse boundary stays canonical;
retries become the exception path, not the primary recovery.

### Risks
- **Deep-merge is the largest new failure surface in the design space**: dangling delta refs (S4
  references a changed/omitted S2 key), exhaustively-undefined field-update semantics (slots
  replace-vs-merge? quote/source_turn_id carried?), and a merge bug corrupts the embed list
  SILENTLY — precisely what criterion 3 forbids. Delta validation (reject/record dangling refs as
  census + warning) is mandatory.
- S4 delta format IS a prompt-text change → #1695 collision; if #1695 lands first the closing run
  measures a joint effect (run record must note — already required by the confirmed problem).
- JSON mode on `deepseek-chat` direct path is provider-untested (H1's reason to exist); json_object
  is schema-free — constrains structure, not OUTPUT_CONTRACT semantics (ingest schema validation
  still needed).
- Delta-merge output differs subtly from union-merge output → criterion 4 baseline must be
  re-verified (highest regression risk of the three approaches).

### Tradeoffs
The only approach that eliminates the truncation class rather than recovering it: structural
guarantee, smaller S4 payloads (cheaper/faster per question), and a byproduct H1 test. Against:
deepest change surface, merge-semantics ownership, prompt collision with #1695, and prompt-text
change lands in the same window as #1695 (attribution coupling).

### Best-fit-if
We want the strongest structural guarantee on criterion 1 and will own deep-merge semantics +
delta-validation; the gate's tolerance for recovered-but-warned outcomes is low; we want the H1
test as a side effect; and #1695 sequencing can be coordinated (joint-effect note).

---

## Approach C — Provider-Constrained Decoding + Schema Gate, Two-Stage Escalation

**Defense location: provider/decode layer, with an explicit two-stage decision gate.**

### Description
**Stage 1 — constrained decoding + schema gate:**
1. **JSON-mode parity on both adapters** — `response_format={"type":"json_object"}` on
   `DeepSeekDirectModel` (mirroring OpenRouterModel:111-113), plus attempt
   `{"type":"json_schema","json_schema":{...}}` where honored (json_object is schema-free and weaker;
   json_schema constrains the grammar at decode time so structurally-broken objects are not
   emittable). **Pre-flight probe before the run** (10-20 calls on the direct path, assert the mode
   is honored via finish_reason + malformed-rate vs baseline): H1 is an UNTESTED lever; the probe
   tests it for pennies instead of discovering inertness mid-run.
2. **Schema-validate every parse** — `_parse_json` → OUTPUT_CONTRACT-derived schema → on schema
   failure, error-informed re-prompt (attempt 2 carries the schema error) → censused fallback.
   Approach A's repair/partial-accept are deliberately NOT used at stage 1: keep attribution clean —
   if constrained decoding is honored, repair is unnecessary; if it fails, we want to SEE the class.
3. **Truncation policy** — pair stage 1 with ONE of: (a) Approach B's S4 delta contract, or
   (b) a two-emit chunked S4 (brace-balance-aware split into two capped calls, concatenated at
   ingest — keeps the full-re-emit contract, halves per-call length). Default (b) at stage 1 (no
   contract change); escalate to (a) at stage 2 only if needed. Choosing one, not both, keeps the
   run's attribution clean.

**Stage 2 — escalation decision.** The fresh-run gate consumes finish_reason-attributed telemetry:
`truncated_parse_error` still trips criterion 1 under constrained decoding → the provider lever is
insufficient for truncation → escalate to Approach B's delta contract as a SECOND fresh run
(documented escalation, not a mid-run patch). Contamination-class failures → add Approach A's
sanitize rung. Non-parse-class failures → #1747 trigger (unchanged).

### Files touched
- `tortoise/model_adapters.py` — `DeepSeekDirectModel` response_format + schema mode.
- `tortoise/extractor_v2.py` — schema validator, error-informed retry, S4 chunked emit (option b),
  census classes.
- New pre-flight probe script (e.g. `tools/experiments/extractor-v2/`).
- `tools/longmem_eval/run.py`, `report.py` — shared foundation + stage marker in report.
- `tests/test_extractor_v2.py`, `tests/test_extractor_reliability.py` — schema-gate, chunked-emit
  merge, response_format body-assertion tests.

### Architecture
The model cannot emit malformed structure by construction (if honored); a schema gate + error-
informed retry catches what constrained decoding still allows; the gate is a two-stage decision
machine — run 1 tests the cheapest structural lever, census classes decide whether run 2 needs the
contract inversion.

### Risks
- Provider dependence is the whole bet: if the direct API silently ignores `response_format` (the
  H1 observation makes this plausible), stage 1 degrades to today's behavior and the run is
  uninformative — mitigated by the pre-flight probe, but the probe must actually distinguish
  "honored" from "ignored".
- json_schema support varies by provider/model; if schema mode doesn't constrain, fall back to
  schema-gate-only (weaker than promised).
- Schema maintenance coupling: contract edit → schema edit (a coupling the repo doesn't have today).
- Chunked S4 emit (option b) needs a boundary that can't split an item mid-way; and inflates total
  output tokens vs Approach B.
- Two-stage escalation doubles the run budget if stage 1 fails (two fresh 50-Q runs + two accuracy
  baselines).

### Tradeoffs
Prompts untouched at stage 1 (no #1695 collision); strongest structural guarantee at the cheapest
per-question cost (no output inflation, no parse-side complexity); fully reversible (toggle/env).
Against: provider-honor risk, schema maintenance coupling, and criterion 1 may close only on run 2 —
the approach explicitly budgets for escalation.

### Best-fit-if
We'll run the pre-flight probe and trust provider-level guarantees; prompt text should stay frozen
for #1695; the team prefers a reversible lever and an explicit two-stage escalation over a single
run; a second fresh run for escalation is acceptable cost.

---

## Cross-approach comparison

| Axis | A: Parse Ladder | B: Source Reduction | C: Constrained Decoding |
|---|---|---|---|
| Defense location | parse boundary | generation source/contract | provider/decode |
| S4 contract | unchanged (full re-emit) | gaps-only deltas | unchanged (or chunked emit) |
| Truncation (H3) | recovered + censused | structurally eliminated | recovered/chunked → escalate to B |
| JSON mode (H1) | untouched | added on direct path | added + pre-flight probe |
| Contamination (H2) | output-side sanitize | input-side sanitation | schema gate only |
| Retry | error-informed re-prompt | finish_reason-aware skip + error-informed | error-informed re-prompt |
| #1695 collision | none | yes (S4 prompt) | none (stage 1) |
| Criterion 3 posture | all outcomes censused+warned | structural + merge-audit | censused + schema gate |
| Biggest risk | repair accepts degraded content | deep-merge silent corruption | provider ignores the mode |
| Regression risk (criterion 4) | low | HIGHEST (delta-merge ≠ union) | medium (chunked emit) |
| Best-fit | fast floor, uncertain attribution | structural guarantee, own merge | provider trust + reversible + escalation budget |

All three satisfy closing criteria 1-3 only WITH the shared scope-(a) foundation in place.
Criterion 4 is re-measured on the fresh run in all three; Approach B carries the highest regression
risk, Approach C requires the pre-flight probe to make its run interpretable.
