<!-- research-path: docs/epics/2026-08-20-1509-extractor-v3/02-research-brief.md -->

# #1526 — M6 Evidence-Marking Recalibration (3 marks, N/A-not-0.0) + 52-Healthy Fixture — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Replace the miscalibrated ≥0.4 content-overlap evidence predicate (fired **1/12,085** — 51/52 healthy questions with zero evidence marks) with **three independent marks** (source-session attribution, verbatim anchor, raw-chunk containment), make `evidence_recall`/`turn_recall` **N/A-not-0.0** on empty denominators, and commit the **52-healthy-question calibration fixture** so the M6 micro-test (run protocol step 2) can calibrate the marks offline — no run, no LLM keys.

**Team:** epistemic-team

**Architecture:** One shared predicate module (`tools/longmem_eval/evidence.py`) consumed by both ingest legs; marks are OR-combined and written at ingest time as the existing eval-instrumentation `has_answer` property (no new kinds, no new edges, no ontology change). `retrieve.py` returns `None` (N/A) for recall denominators that are empty instead of a forced 0.0. `report.py` aggregates over evidence-bearing questions only and records vacuity + coverage. A committed fixture (`tests/fixtures/lme_v2_healthy52.json`, ~0.9 MB) + builder script make the calibration deterministic: the answer-session raw transcript is guaranteed marked by marks (a)+(c) — verified **52/52** coverage offline, ≥ the E2E-3 >95% gate.

### Pattern Research

> **Findings date:** 2026-08-20
> **Gate:** skipped — the plan touches **zero third-party dependencies** (pure in-repo Python: `tools/longmem_eval/*`, `tortoise` SDK, pytest). Prior research intake (Step A ran): epic `02-research-brief.md` Raw Notes (2026-08-20 — "52 questions wrote 12,085 points but 51/52 had zero evidence marks (second mechanism beyond the 402: the ≥0.4 threshold is miscalibrated for summarizing extractors, fired 1/12,085)"), `03-scope.md` M6 + E2E-3, `04-plan.md` §4 Data Model (Point `quote` existing — E3/M6; `has_answer` eval-instrumentation; raw chunks substring containment), `05-detailed-e2e.md` E2E-3/E2E-5 + Precondition 1, test-design #1515 (surface 19 — evidence marking; BVA 0.39/0.40/0.41; forced-0.0-on-empty → M6 N/A fix), `/tmp/v3-review/06-retrieval.md` (probe C — paraphrased v2 points fail FTS AND-match; the evidence-recall attribution bug).

### Integration Surface Map

| Surface (#1515) | Data Flow | Contract | Test Layer |
|---|---|---|---|
| 19 — evidence marking (state) | `ingest.py`/`ingest_v2.py` → `evidence.py` predicates → `has_answer` on Points (extracted + raw transcripts) | 3 marks OR'd: (a) point's `session_id` ∈ evidence sessions; (b) point `quote` contains/overlaps an answer turn; (c) raw chunk text contains an answer turn (normalized substring). Old ≥0.4 predicate removed. Marks are eval-instrumentation only (never a production ontology concept) | Unit (predicate BVA) + integration (ingest → graph, both legs) |
| 19 — N/A semantics | `retrieve_for_question` → `evidence_recall@k` / `turn_recall@k` | `None` when the denominator is empty (no evidence points / no evidence turns) — never forced 0.0 | Unit (empty-graph case, evidence-less abstention) |
| 19 — vacuity accounting | `report.py` → `retrieval.evidence_recall@k` + methodology | Mean over evidence-bearing questions only; `evidence_vacuity_rate@k` + `evidence_coverage` recorded; expectation band anchored to run protocol step 6 (initial band from the fixture: 0/52 vacuous) | Unit (mixed None/real outcomes) + calibration |
| 3/4 — reader/judge | `run.py` → `render_context` | **Passive** — marked hits render byte-identically; N/A semantics do not reach the reader | Covered by existing render_context tests |
| 22 — Layer-1 payload | `extractor_v2.execute_embed` → points `quote` | **Read-only** — payload `quote` (currently `""`) is consumed when non-empty; M6 adds a deterministic turn-anchoring fallback (D3) so quotes exist without an extractor prompt change | Unit (anchor + cap ≤200) |
| 27 — dataset fixture (M7) | `dataset.py` + v2 checkpoint → `tests/fixtures/lme_v2_healthy52.json` | Fixture = 52 qids with `ingest.points > 0`; per-question compact subset (metadata + answer-session turns + v2-checkpoint counts/recall/errors) | Calibration test |
| 14 — checkpoint | run checkpoint JSON | **Passive** — the fixture builder reads `/tmp/lme-v2-full.json` (checkpoint shape `outcomes[]` with `ingest` stats) but the runner's checkpoint write path is untouched | — |

**Bug pattern flags:** (1) BVA on the removed ≥0.4 predicate — a point at 0.39/0.40/0.41 content overlap must be treated identically (mark only via the new marks); (2) `quote` ≤200-char cap vs 41/54 evidence turns >200 chars — (b) needs an n-gram fallback, prefix truncation alone fails 7/54; (3) empty-denominator forced 0.0 indistinguishable from "evidence exists but never surfaces" (the #1369 measurement bug); (4) vacuity drag — a `None` coerced to 0.0 in the report mean silently reintroduces the vacuity the epic excludes; (5) mark (c) on the whole-session raw transcript is coarse — R1 turn-granular chunks (another issue) make it precise; the predicate is chunk-agnostic so it composes; (6) idempotent re-ingest — the `_point_exists` OR-in path must OR the new marks, not overwrite `False` over `True`.

---

## Design Decisions

### D1 — Three independent marks replace the single ≥0.4 predicate
The v2 failure mechanism: v2 points are **paraphrased** (S1 story-summarizes), so a ≥0.4 token-overlap predicate against the verbatim answer turn almost never fires (1/12,085). The recalibration ORs three marks, each independent and each recoverable when the others fail:

- **(a) source-session attribution** — a point written from an evidence-bearing session is marked. Evidence session = a haystack session containing ≥1 `has_answer` turn (equivalently the question's `answer_session_ids`; the M7 dataset-semantics audit owns the equivalence proof — for the 52 healthy, `answer_session_ids` ≡ the single has-answer session, verified 52/52). Implementation: compare the point's existing `session_id` prop (already written by both ingest legs) against the evidence-session id set. The point carries its session already — no new point property needed.
- **(b) verbatim anchor** — a point whose source `quote` contains (normalized substring) an answer turn, or whose `quote` n-gram-overlaps an answer turn ≥ **0.5**, is marked. `quote` is an existing commit_schema field (≤200 chars) that the extractor emits as `""` — see D3 for the deterministic population.
- **(c) raw-chunk containment** — a raw chunk (today: the per-session raw-transcript Point; later: R1 turn-granular chunks) whose text contains an answer turn (normalized-verbatim substring) is marked. Verified offline: **54/54** evidence turns are verbatim substrings of their own session's raw transcript → this mark alone guarantees ≥1 evidence point per healthy question (52/52).

OR-combined at write time: `has_answer = (a) or (b) or (c)`. Each mark is independently attributable in the stats (D7), so the report can say *why* evidence exists (extractor wrote from the right session / anchored the right turn / the raw chunk contains it).

### D2 — Shared predicate module `tools/longmem_eval/evidence.py`
Single source of truth for both ingest legs (and the fixture calibration test). Move `_STOPWORDS`/`_tokens`/`_overlap` from `ingest_v2.py` here (public names); add:

```python
EVIDENCE_QUOTE_OVERLAP = 0.5   # (b) n-gram fallback threshold (the calibration knob)
EVIDENCE_ANCHOR_FLOOR = 0.25   # min point↔turn overlap to anchor a quote (D3)
EVIDENCE_QUOTE_CAP = 200       # commit_schema quote cap (v3.6 #11)

def evidence_sessions(question) -> set[str]            # haystack ids of sessions with has_answer turns
def source_session_mark(point_session_id, evidence_sessions) -> bool   # (a)
def quote_mark(quote, answer_turn_contents) -> bool                    # (b)
def chunk_mark(chunk_text, answer_turn_contents) -> bool               # (c)
def mark_for(point, *, session_id, evidence_sessions, answer_turn_contents) -> dict
    # -> {"has_answer": bool, "marks": {"source_session": bool, "verbatim": bool, "raw_chunk": bool}}
```

`ingest_v2.py` re-exports `_overlap` (back-compat) and delegates to `evidence.py`. `mark_for` returns the per-mark breakdown so `_write_payload` can count `stats["evidence_marks"]` by type (D7).

### D3 — Deterministic quote population (verbatim anchor without an extractor prompt change)
The extractor's S2 payload points carry `"quote": ""` (verified `tortoise/extractor_v2.py:1164`). Making mark (b) real requires quotes to exist — done **deterministically at ingest** so the calibration is LLM-free and CI-runnable:

1. For each v2 point, find the raw turn with maximum token overlap against the point's content (the anchor).
2. If anchor overlap ≥ `EVIDENCE_ANCHOR_FLOOR` (0.25), set the point's `quote` to the anchor turn's **best-window ≤200-char span** (the window maximizing overlap with the point's content — prefix truncation alone fails 7/54 evidence turns; best-window fails only 3/54, all very-long turns where (b) legitimately doesn't fire and (a)/(c) cover the question).
3. Write `quote` via `create_point(..., quote=...)` (prop passes `_sanitize_props` — only `sourcePath`/`source_path`/`id` are rejected).
4. Mark (b) fires on `quote_mark` — never on raw point content. This is the semantic fix: the old predicate demanded the *paraphrase* carry the answer's tokens; the new one demands only the *anchored verbatim source* relate to the answer.

E3 (speaker attribution) later reuses the same `quote` for read-time source-turn role derivation — no conflict (single ≤200-char field, E3 adds offsets separately).

### D4 — Mark (b) boundary: verbatim containment OR n-gram overlap ≥ 0.5
41/54 evidence turns exceed the 200-char quote cap, so strict substring containment alone would under-fire on truncation. (b) = `normalize(quote) contains normalize(answer_turn)` **or** `_overlap(quote, answer_turn) >= 0.5`. BVA at 0.49/0.50/0.51 (test-design #1515 flag; the threshold is the run-protocol step-2 knob — pilot and 500 run the chosen value).

### D5 — N/A-not-0.0 in `retrieve_for_question` (never forced 0.0 on an empty denominator)
Today `_evidence_recall[k] = 0.0` when `evidence_point_count == 0` and `turn_recall[k] = 0.0` when no evidence turns — the #1369 measurement bug: "evidence exists but never surfaces" and "no evidence exists" are indistinguishable. New semantics:

- `evidence_recall@k[k] = None` when the graph has **zero** `has_answer` points (`evidence_point_count == 0`). Real number otherwise.
- `turn_recall@k[k] = None` when **both** `evidence_point_count == 0` and `evidence_turn_ids` is empty. When one leg has a denominator, that leg reports its real number (honest attribution: the deterministic leg still measures turn-level recall when the v2 leg wrote nothing).
- `session_recall@k` unchanged (its denominator is `answer_session_ids`; present for all 30 abstention questions in the S split — not part of the M6 N/A contract).

Concretely, replace the `else: _evidence_recall[str(k)] = 0.0` branch with `None`, and the inner `else: turn_recall[str(k)] = 0.0` with `None`.

### D6 — Vacuity accounting in `report.py` (evidence-bearing questions only)
- Mean `evidence_recall@k` over outcomes whose value is **not None** (drop N/A from the denominator — today `_mean([v or 0.0 ...])` coerces `None`→0.0, silently re-dragging vacuity).
- Record alongside the mean: `evidence_recall_n@k` (denominator count), `evidence_vacuity_rate@k` (fraction of evidence-bearing questions with 0.0 — the "0.0 while evidence exists" rate), and `evidence_coverage` (fraction of evidence-bearing questions with `ingest.evidence_points > 0`, computed from the per-outcome ingest stats — the E2E-3 >95% gate metric).
- Methodology records the vacuity **expectation band**: initial band from the fixture calibration (0/52 vacuous on healthy questions), to be re-anchored from the 500-Q run after run protocol step 6 (mechanical fixes) — the epic's "recorded in the report methodology after M6/M7 calibration (run protocol step 6) as the expectation band" contract.

### D7 — Fixture: 52 healthy qids + compact question subset + v2-checkpoint subset
`tests/fixtures/lme_v2_healthy52.json` (~0.9 MB, verified committable):

- **Healthy criterion:** v2 checkpoint outcome with `ingest.points > 0` (the 52/496; all `single-session-user`, first in run order — extraction health decays with run position via 402 exhaustion, `msr-category-report.md`).
- **Per question (compact, from the dataset cache `xiaowu0162/longmemeval-cleaned` split=s):** `question_id`, `question_type`, `question`, `answer`, `question_date`, `answer_session_ids`, `n_haystack_sessions`, `haystack_session_ids`, `haystack_dates`, and **`answer_sessions`** (the turns of the has-answer session — 1 per question, verified 52/52; all 54 evidence turns are inside it). This is the minimal content needed to recompute marks (a)+(c) offline; the full 52-question haystack is 26.8 MB (not committable).
- **Per question (v2-checkpoint subset from `/tmp/lme-v2-full.json`):** `points`, `evidence_points` (the 1/12,085 pin), `sessions`, `turns`, `raw_transcripts`, `entities`, `events`, `operators`, `supersessions`, `n_ingest_errors`, `first_error`, `evidence_recall@k`/`turn_recall@k`/`session_recall@k`.
- **`_meta`:** source checkpoint path + `updated_at_utc`, dataset id/split, healthy criterion, calibration goal (>95% gate), and the miscalibration note (old predicate fired 1/12,085; 51/52 with 0 marks).

Builder script `tools/longmem_eval/build_healthy52_fixture.py` (CLI `--checkpoint /tmp/lme-v2-full.json --dataset ~/.cache/tortoise-longmemeval/longmemeval_s_cleaned.json --out tests/fixtures/lme_v2_healthy52.json`): reproducible regeneration + honest provenance; the committed JSON is the artifact CI consumes.

### D8 — Calibration micro-test (run protocol step 2, "knob selected")
`tests/test_lme_m6_evidence.py::test_healthy52_calibration_coverage` runs the new marks over the fixture — no graph, no LLM:

1. **(a)+(c) coverage:** for each of the 52, rebuild `_session_transcript(answer_session)` from the fixture and assert `chunk_mark(...)` (and the session-id mark) fires → `evidence_points ≥ 1` → **52/52**, asserting the E2E-3 **>95% gate** is achievable with the chosen marks/thresholds (the knob).
2. **Regression pin:** the fixture's checkpoint subset asserts the old state (51/52 with `evidence_points == 0`, total 1/12,085) — the miscalibration the recalibration fixes.
3. **(b) boundary:** synthetic quotes over the fixture's 54 evidence turns assert BVA (0.49 no / 0.50 yes / 0.51 yes) and the >200-char truncation fallback.
4. **Vacuity baseline:** 0/52 evidence-bearing questions vacuous → the initial expectation band for D6.

The pilot and 500 run the chosen knob values (mark set + thresholds) per run protocol step 2's gate.

### D9 — Idempotency + consistency across both ingest legs
- `ingest_v2._write_payload`'s existing `_point_exists` OR-in path must **OR** the new marks (`SET p.has_answer = true` when any mark fires — never overwrite `True` with `False` on collision).
- `ingest.py` (deterministic leg) keeps evidence-turn marks and gains mark (c) on the raw-transcript Point + mark (a) on the Session's points — both legs produce the same `stats["evidence_points"]`-style accounting (D7) so `evidence_coverage` is comparable across ingest modes.
- The ≥0.4 predicate, its BVA tests, and the `EXTRACTION_APPROACH_V2` docstring text ("marked has_answer by content overlap (>=0.4)", `run.py:72`) are removed/updated together — no dead references.

---

## Implementation Steps

### Task 1: `tools/longmem_eval/evidence.py` — shared predicates (red → green)

**Intent:** One module owns the three marks + token utilities so both ingest legs and the calibration test share identical logic.
**Acceptance:** `mark_for()` returns the OR of (a)/(b)/(c) with a per-mark breakdown; `chunk_mark` fires on normalized-verbatim containment; `quote_mark` fires on containment or ≥0.5 overlap; existing `_overlap` behavior preserved (re-exported by `ingest_v2`).

**Files:**
- Create: `tools/longmem_eval/evidence.py`
- Test: `tests/test_lme_m6_evidence.py`

**Step 1 — failing tests:** predicate BVA + mark-matrix cases (each mark alone, OR combinations, ≥0.4-removal equivalence — a 0.39/0.40/0.41 point with no new mark stays unmarked).
**Step 2 — run:** `uv run pytest tests/test_lme_m6_evidence.py -v` → FAIL (module missing).
**Step 3 — implement** the module (D1/D2/D4 logic; move `_STOPWORDS`/`_tokens`/`_overlap`).
**Step 4 — green + commit** (skill: `commit-workflow`).

### Task 2: `ingest_v2.py` recalibration — 3 marks + quote anchoring

**Intent:** The v2 write path marks extracted points by the 3 marks instead of ≥0.4 overlap, writes anchored quotes (D3), marks the answer-session raw transcript (c), and reports per-mark stats.
**Acceptance:** For a fixture-like question with a paraphrased evidence point + an answer-session raw transcript, the graph contains `has_answer=true` on (i) the raw transcript via (c), (ii) any point from the evidence session via (a), (iii) a quoted point overlapping an answer turn via (b); `stats["evidence_points"]` counts all marked points incl. the raw transcript; idempotent re-ingest ORs marks.

**Files:**
- Modify: `tools/longmem_eval/ingest_v2.py` (`_write_payload` mark path, raw-transcript `has_answer`, quote write, `evidence_marks` stats)
- Modify: `tools/longmem_eval/run.py:72` (`EXTRACTION_APPROACH_V2` text → the 3-mark description)
- Test: `tests/test_lme_m6_evidence.py` (integration: FalkorDBLite ingest → query marks) + `tests/test_longmem_runner.py` (extend `test_v2_ingest_writes_payload_with_evidence_marks` — keep the existing assertions green)

**Steps:** failing test → run → implement (D1/D3/D9) → green → commit. BVA: anchor floor 0.24/0.25/0.26; quote cap boundary at 199/200/201 chars.

### Task 3: `ingest.py` — deterministic-leg raw-chunk + session marks

**Intent:** The deterministic baseline's raw transcript (its recall mitigation leg) gets mark (c)/(a) so both legs are non-vacuous and `evidence_coverage` is comparable.
**Acceptance:** The answer-session raw-transcript Point carries `has_answer=true`; evidence-turn points unchanged; `stats` gains `evidence_points`.

**Files:**
- Modify: `tools/longmem_eval/ingest.py` (raw-transcript write, stats)
- Test: `tests/test_lme_m6_evidence.py` (integration over `longmemeval_mini.json`)

**Steps:** failing test → run → implement → green → commit.

### Task 4: `retrieve.py` — N/A-not-0.0

**Intent:** Empty denominators report `None`, not forced 0.0 (the #1369 measurement bug).
**Acceptance:** `evidence_recall@k` is `None` when the graph has zero `has_answer` points; `turn_recall@k` is `None` when both legs are empty; the deterministic leg still reports its real number when only the v2 leg is empty; docstrings updated.

**Files:**
- Modify: `tools/longmem_eval/retrieve.py` (the two forced-0.0 branches; module docstring recall definitions)
- Test: `tests/test_lme_m6_evidence.py` (empty-graph question → both `None`; evidence-less abstention mini fixture → `None`, not 0.0; existing `test_retrieval_recalls_evidence_session` stays green)

**Steps:** failing test → run → implement → green → commit.

### Task 5: `report.py` — vacuity accounting + methodology band

**Intent:** The report drops N/A questions from the evidence_recall mean and records vacuity/coverage + the step-6-anchored expectation band.
**Acceptance:** `retrieval.evidence_recall@k` is the mean over evidence-bearing outcomes only; `evidence_recall_n@k`, `evidence_vacuity_rate@k`, `evidence_coverage` present; methodology has `vacuity_band` + `vacuity_band_anchor: "fixture calibration 2026-08-20 (0/52 vacuous); re-anchor at run protocol step 6"`.

**Files:**
- Modify: `tools/longmem_eval/report.py` (aggregation + methodology)
- Test: `tests/test_lme_m6_evidence.py` (mixed None/real outcomes — the vacuity-drag regression)

**Steps:** failing test → run → implement → green → commit.

### Task 6: Fixture builder + committed fixture

**Intent:** Reproducible extraction of the 52 healthy qids + checkpoint subset into the committable fixture.
**Acceptance:** `tools/longmem_eval/build_healthy52_fixture.py --checkpoint /tmp/lme-v2-full.json --dataset ~/.cache/tortoise-longmemeval/longmemeval_s_cleaned.json` regenerates a byte-identical `tests/fixtures/lme_v2_healthy52.json` (52 questions; every question has exactly 1 `answer_session`; `_meta` complete).

**Files:**
- Create: `tools/longmem_eval/build_healthy52_fixture.py`
- Create: `tests/fixtures/lme_v2_healthy52.json` (~0.9 MB; build now with the verified script, commit the artifact)

**Steps:** write script → run → validate shape (52/52 answer sessions, checkpoint subset present, size ≤1 MB) → commit.

### Task 7: Calibration micro-test (run protocol step 2)

**Intent:** The offline calibration that selects the knob and proves the >95% gate achievable (D8).
**Acceptance:** `test_healthy52_calibration_coverage` asserts (a)+(c) coverage **52/52** with the chosen thresholds; the 1/12,085 pin asserts the old-state regression; (b) BVA over the fixture's 54 evidence turns; vacuity baseline 0/52.

**Files:**
- Modify: `tests/test_lme_m6_evidence.py` (calibration section, fixture-loaded)
- Modify: `tests/test_longmem_runner.py` only if an existing assertion contradicts the new semantics (grep `evidence_recall` — line 755's `is not None` stays valid; no forced-0.0 assertions exist)

**Steps:** write tests → run → green → commit. Full sweep: `uv run pytest tests/test_lme_m6_evidence.py tests/test_longmem_runner.py tests/test_longmem_reader_prompting.py -v`.

---

## Tests

| Test | Layer | Asserts |
|---|---|---|
| `test_evidence_marks_matrix` | unit | Each mark alone + OR combos; ≥0.4 removal equivalence (0.39/0.40/0.41 identical) |
| `test_quote_mark_bva` | unit | 0.49 no / 0.50 yes / 0.51 yes; >200-char truncation fallback (best-window) |
| `test_chunk_mark_normalized_verbatim` | unit | Whitespace/case-insensitive containment; non-answer text not marked |
| `test_v2_ingest_marks_three_ways` | integration | FalkorDBLite: raw transcript (c), session point (a), quoted point (b) all `has_answer=true`; idempotent re-ingest ORs |
| `test_deterministic_ingest_marks_raw_transcript` | integration | `ingest.py` raw-transcript mark on the mini fixture's answer session |
| `test_retrieve_na_not_zero` | unit | Empty-graph → `evidence_recall@k`/`turn_recall@k` all `None`; one-leg-empty → other leg real; abstention mini → `None` |
| `test_report_vacuity_excludes_na` | unit | Mixed None/real outcomes: mean over evidence-bearing only, `evidence_vacuity_rate@k`, `evidence_coverage`, methodology band |
| `test_healthy52_calibration_coverage` | calibration | Fixture: (a)+(c) 52/52 ≥ 0.95 gate; 1/12,085 pin; vacuity baseline 0/52 |
| `test_healthy52_fixture_shape` | calibration | 52 qids, all `single-session-user`, exactly 1 answer session each, checkpoint subset complete |

Run: `uv run pytest tests/test_lme_m6_evidence.py tests/test_longmem_runner.py tests/test_longmem_reader_prompting.py -v` (FalkorDBLite, offline, no keys — CI-green).

## Cross-lane Interfaces

- **E2E-3 / harness (M7-adjacent issue):** consumes the report's vacuity/coverage fields for the run-time >95% gate and the step-6 vacuity band; its Precondition 1 (this fixture) is satisfied by Task 6.
- **E2E-5 (E2/E3/E4 issue):** its "verbatim value retrievable and evidence-marked" assertion depends on M6 marks — the anchored `quote` (D3) is the mechanism that makes a paraphrased point evidence-marked without raw-transcript reliance.
- **R1 (turn-granular raw chunks):** `chunk_mark` takes chunk text — R1's finer chunks are marked automatically once they exist; the per-session raw transcript mark is the M6 granularity (coarse but non-vacuous).
- **E3 (speaker attribution):** shares `quote`; M6 writes it deterministically, E3 derives source-turn role from `quote`/offsets at read time — no field conflict (single ≤200-char source).
- **M7 (report/hygiene):** owns the integrity block + dataset-semantics audit; the `answer_session_ids` ≡ has-answer-session equivalence M6 relies on is M7's audit target (verified 52/52 on the fixture's questions as a pre-check).
- **M2/M3/M4 (reliability):** the raw-transcript mark (c) is deterministic — evidence marking survives extractor failures by design (the 402-run would have had non-vacuous marks via (c) alone); no dependency on retry fixes.
- **P3 rebase:** no real-backend dependency — all tasks run on FalkorDBLite; no drift gate required.

## ⛔ CONDITIONAL GATES

1. **`quote` ≤200-char cap vs long answer turns (41/54 > 200 chars).** Mitigated in-plan by the best-window anchor + 0.5 n-gram fallback (fails only 3/54 very-long turns, where (a)/(c) cover the question). **Gate:** if the calibration run shows (b)-only coverage below the expected bound on short-value questions, raise the cap via `commit_schema.py:226` (`quote: str = Field(default="", max_length=200)`) — a schema-field tweak, NOT an ontology change — or add an eval-local `evidence_quote` prop. Default: no schema change.
2. **E3 landing before M6 executes.** **Gate:** if the extractor begins emitting non-empty `quote` in the payload (E3's `quote`/offsets wiring), drop the D3 ingest-side anchoring and consume the payload quote directly (keep the anchor as fallback for empty quotes). Check at execution: `tortoise/extractor_v2.py:1164` still `"quote": ""`?
3. **R1 turn-granular chunks landing first.** **Gate:** keep `chunk_mark` chunk-agnostic (D2) and count ONE mark per point (a chunk containing 2 answer turns is one marked point — no double-count in `evidence_point_count`).
4. **`source_session_id` additive point property (04-plan §4 Data Model row).** The epic lists it as a new point property; M6 (a) uses the **existing `session_id` prop** on points (same value — the haystack session id). **Gate:** confirm with the epic owner that the data-model row is satisfied by `session_id` (no new property); if a distinct property is required, add `source_session_id` alongside `session_id` at write time (additive, no ontology impact).
5. **No ontology/architecture/new-field changes are otherwise required** — marks reuse the existing eval-instrumentation `has_answer`; `quote` already exists in commit_schema; no new kinds/edges/packs.

## Open Questions

1. **`evidence_points` stats semantic** — include the marked raw-transcript in `stats["evidence_points"]` (chosen: yes, D7, so `evidence_coverage` is leg-comparable) vs keeping it as a separate `evidence_raw_chunks` counter. Low risk either way; the report field name (`evidence_coverage`) is defined by this plan and consumed by E2E-3 — confirm at Task 2 review.
2. **The (b) threshold (0.5) is the run-protocol step-2 knob.** The fixture proves (a)+(c) alone hit 52/52; (b) adds the paraphrased-point signal E2E-5 depends on. If the pilot shows (b) misfires (quote anchored to a non-answer turn that coincidentally overlaps ≥0.5), tighten via the anchor floor (0.25) before step 3 — no code change beyond constants.
3. **Vacuity band initial value** — fixture-derived 0/52 is the stated baseline; the step-6 re-anchor happens in the run (M7 territory). Confirm the report field name (`vacuity_band`) matches M7's planned integrity block.
4. **Fixture regeneration policy** — the committed fixture pins the 2026-08-20 dataset + checkpoint. A future dataset re-download could change qids (the cleaned split is versioned on HF); regeneration is a deliberate, reviewed act (builder script exists), not automatic.
