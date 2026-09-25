---
title: "#2520: time-aware query expansion (C6, epic #2513) — Implementation Plan"
type: engineering
domain: capability
doc_status: live
created: 2026-09-25
subjects.team: epistemic-team
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: Point
---

# Plan — #2520: time-aware query expansion (C6, epic #2513)

**Issue:** #2520 · **Epic:** #2513 (parent #1509 layer-3 RECALL) · **Team:** epistemic-team
**Complexity:** standard (Level: task) · **Branch:** `fix/2520-time-aware-query-expansion`
**Research path:** scoping research = `docs/scoping/2026-09-07-2513-multisession-evidence-surface.md`
§4 C6 + `docs/research/2026-08-24-1657-retrieval-levers/research.md` Lever 2. Plan-level
re-derivation: zero third-party deps (all in-repo machinery — R5 #1544, E5 #1537, E6 #1538), so the
research-intake Step B skip applies; the scoping artifact is consumed as PRIOR_RESEARCH.

## Problem statement (confirmed by repro — see #2520 comment)

Temporal correctness is **label-gated and ranking-blind**:

1. The dense leg embeds the bare question (`tortoise/sdk.py::tortoise_fts_query`,
   `model.encode([query])`); `question_date` reaches only the *reader* header (`render_context`).
   Nothing in the query string can express "which version was current on X".
2. `tools/longmem_eval/retrieve.py::retrieve_for_question` gates the temporal stack on
   `is_tr = question_type == "temporal-reasoning"` — the recency weight, the point+event union, the
   ascending render. KU/MSR recency intent is never detected from the query text.
3. The fused order ignores the promoted supersession state (`superseded_by` / `validTo` /
   `expiredAt`, E5 #1537 + E6 #1538). Reproduced: a superseded `createdAt 2024` point outranks its
   live `2026` successor (rrf 0.04098 vs 0.02419) for `where do I live now?` on the eval/ask-lane
   posture (`include_terminal=True`).
4. Negative class: an explicit-date question ("where did I live in 2024?") is the case where the
   **old** fact is the answer; on the product default path terminal exclusion makes it unreachable,
   so a past question returns the current fact.

## Design decisions

**D1 — Product owns the mechanism; the eval is a thin measuring caller** (the product-cohesion
rule, same as #2518/C2 and `tortoise/temporal_leg.py`). New pure module `tortoise/time_aware.py`:
no DB access, no I/O, no LLM. The eval imports it; the reverse never happens. The eval owns only
*where in its pipeline* the reorder lands.

**D2 — Three pure functions, one dataclass, and NO vocabulary collision with the existing
detectors.** The repo already has two *window-constraint* detectors — `detect_time_constraint`
(eval, R5 #1544; kinds `interval`/`recency`/`ordering`) and `tortoise/coverage_loop.py::
facet_date_constraint` (a declared mirror of it). Those answer "which dated slice of the pool does
this question need?", a **filter** question. C6 asks a different question — "does this question
want the *current* version, or a *pinned* past one?" — a **rank-preference** question. Forking them
would collide on the word `recency` (a window there, a freshness preference here). So:
- `detect_temporal_intent(query) -> TemporalIntent` with `kind ∈ {"prefer-latest", "date-pinned",
  None}` — deliberately disjoint from `{"interval","recency","ordering"}`. `prefer-latest` = current
  intent ("now", "currently", "these days", "still", "latest", "most recent", "has X changed",
  "did I switch"). `date-pinned` = a past window is named (4-digit year, ISO date, month name,
  "back in", "used to", "N days/weeks/months ago", "last <period>", "between A and B").
  **`date-pinned` takes precedence** — a pinned date must not get the fresh bias.
  `None`/`""` query → `kind=None` (full-scan safe).
- `inject_query_date(query, question_date) -> str` — bounded string op; returns
  `query + " (as of <YYYY-MM-DD>)"` via the module constant `QUERY_DATE_SUFFIX`; unchanged for a
  missing/unparseable date; idempotent. The *format* is declared here and documented against the
  reader-header constant (`Current Date:` in `render_context`) and the eval's `_date_header`: the
  query-side suffix is deliberately not the header prefix (the query is an embedding input, the
  header is a reader line) — the divergence is *stated*, not accidental.
- `prefer_latest_order(entries, *, intent, question_date=None) -> (list, stats)` — a **stable
  reorder**, member-preserving. Applies ONLY when `kind == "prefer-latest"`. Stale = `superseded_by`
  present, OR a validity window that has **closed at or before `question_date`** (`valid_to` /
  `expired_at` ≤ question_date; when `question_date` is missing/unparseable, fall back to presence),
  OR `live.is_terminal_status(status)` — the **single shared** status predicate, imported from
  `tortoise/live.py`, never re-declared inline. The legacy `outdated` flag is **not** an input:
  the eval path cannot read it (no `SearchResult` field, no prop fetch), and it is redundant —
  `invalidate_point` writes `validTo`/`expiredAt` alongside the flag, so the window clause already
  catches flag-invalidated points. `status` IS readable from the search payload
  (`SearchResult.to_dict`); it is the only clause that catches a status-only stale row
  (`retract_point` writes no window and no CORRECTS edge). Live entries keep their relative order,
  then stale entries keep theirs. If no entry is stale, or every entry is stale, the input order is
  returned untouched with `applied=False`.
  **Date normalization (mixed-format safe):** `valid_to`/`expired_at` may be a full ISO timestamp
  (the common case), a date-only string, OR a **numeric epoch** — `supersede_point` copies the
  successor's stored `validFrom` into the predecessor's `validTo`, and a stored numeric `validFrom`
  is legal (`tests/test_validity_windows.py`). A naive `str(v)[:10]` misreads a numeric value. Both
  sides are normalized to a **calendar date** by `_as_date(v)` (numeric epoch → UTC date; ISO →
  `[:10]`; unparseable → `None` = not stale), at the same day granularity the reader's `[valid …]`
  markers use. A window that closed **on** `question_date` is therefore stale; a window still open
  after it is not.

**D3 — Where each half of the mechanism lives (corrected after review cycle 1).**
- **Query-side date anchor → product (`tortoise_fts_query`).** The method gains two optional
  kwargs: `query_date: str | None = None`, `time_aware: bool = False`. When `time_aware` is on,
  `query_date` is present and the detected intent is `prefer-latest`, the **dense-leg embedding**
  uses `inject_query_date(query, query_date)`. The FTS/structural legs keep the original query
  (no date tokens in the OR-union). Default off → the branch is not entered → byte-identical.
- **Rank-time prefer-latest → the eval's FINAL `pool`, NOT inside `tortoise_fts_query`.**
  ⛔ Review cycle-1 P0: `hybrid_search` re-sorts every hit by `(-scores.rrf, id)`, so a stable
  reorder performed inside the SDK is **discarded** before the eval's metrics see it.
  ⛔ Review cycle-2: applying it to `annotated` is also discarded — the later pool-movers
  (`coverage_loop` merge, C4 re-injection merge, C2 evidence boost, R6 rerank) each rewrite `pool`,
  and `_recall_metrics`/`ranked_ids`/`context_points` all read `pool`, not `annotated`.
  **Insertion point (pinned):** on `pool` immediately **before the final
  `_recall_metrics(pool, …)` call** — i.e. after every pool-mover, so prefer-latest is the LAST
  order-owner of the measured surface (by construction it cannot be discarded). With `rerank_on`
  the reorder happens within the reranker's selected set (the reranker has already chosen the
  membership) — the sealed #2520 arm is therefore run with the pool-mover arms OFF, stated in the
  methodology. Product correctness for direct SDK callers is not needed here: the default product
  read path excludes terminal points at base retrieval (`include_terminal=False`), so there is
  nothing to demote. The SDK/product rank-time path is **intentionally not** made
  prefer-latest-aware (recorded under "Not covered").

**D4 — `time_aware` is applied for NON-TR questions only (TR no-regression).** The eval resolves
the arm once and passes the query-side anchor only when `not is_tr`; TR keeps its R5 stack
completely untouched. Stated explicitly because the SDK levers are intent-gated, not category-gated
— an armed `prefer-latest` TR question ("…most recently…") would otherwise change TR behaviour.
The integration test asserts the non-TR path and that TR is not passed the arm.

**D5 — Stats cross the product→eval boundary at the eval layer.** Because D3 moves the reorder into
the eval, `time_aware_stats` is computed there from the same `prefer_latest_order` call — no new
out-parameter channel is needed on `tortoise_fts_query`. `time_aware_stats` carries `applied`,
`reason`, `stale`, `tr_excluded` (a per-question marker mirroring `coverage_loop_stats`), so a TR
question under an armed run is distinguishable from an applied one.

**D6 — Non-TR recency weight is bounded and intent-gated.** With the arm ON and intent
`prefer-latest`, a non-TR question gets `recency_fields={"point":"createdAt","event":"startedAt"}`
and `recency_boost=DEFAULT_TIME_AWARE_RECENCY_WEIGHT` (a module constant `= 0.5`, the TR weight) —
the AutoMem `RECALL_RECENCY_BIAS=auto` posture. The weight is **fixed for the sealed run**: it is
not a harness knob (the sealed A/B varies the ARM, ON vs OFF — a weight sweep would need its own
knob, fingerprint key, CLI/env sites and a new run). `retrieve_for_question` exposes
`time_aware_recency_weight: float | None = None` (default = the constant) so a test can isolate the
reorder from the weight; no `run.py` site is added for it. Covered by a Task-3 step + AC.

**D7 — Layering decision (resolves the issue's Open decision on double-handling).** For a non-TR
`prefer-latest` question there are three mechanisms: (i) the D5 engine recency boost — a *soft* RRF
multiplier that lifts newer points generally; (ii) `prefer_latest_order` — a *hard* stable reorder
on the final pool, driven by explicit supersession state; (iii) the reader's `[SUPERSEDED BY]` /
`[valid …]` markers — a *last-resort* discount for anything the rank stage could not resolve.
Decision: (i) and (ii) are complementary, not redundant — (i) is date-only and state-blind,
(ii) is state-driven and date-bounded; (iii) is **retained** deliberately, because the reader must
still be able to answer an explicit-date history question where the demoted row IS the answer. The
rank stage never removes a row (membership-preserving), so double-handling cannot lose evidence;
it can only change order. The sealed A/B (a single coupled ON/OFF arm per D6/D8) prices the
**combined** (i)+(ii) effect against OFF; pricing (i) alone or (ii) alone would need a weight knob,
which D6 deliberately does not add. Recorded here so the split is intentional, not accidental.

**D8 — The arm is fully plumbed through the harness (`tools/longmem_eval/run.py`).** A lever the
sealed run cannot arm is a dead lever. Mirroring `entity_key_expansion` EXACTLY, the complete site
checklist is: `_build_fingerprint` param + dict, `run_evaluation` signature, env resolve, the
fingerprint call, the `retrieve_for_question` call, the outcome projection, the `r1_knobs`
methodology record (`report.py` merges `r1_knobs` into the published methodology — without it a
sealed report cannot be attributed to ON/OFF), the report allow-list, the CLI flag pair, the main
resolve, and the main `run_evaluation(...)` call. Tri-state: `time_aware_qe=None` → explicit arg >
env `TORTOISE_LME_TIME_AWARE_QE` > OFF, only `1/true/yes/on` enables.

**D9 — Fixtures must contain reachable stale rows (Class B).** The integration fixture seeds real
supersession via `sdk.supersede_point(old, new)` (status terminal, `validTo`, `expiredAt`,
`superseded_by`) — a REACHABILITY test asserts the arm's census sees `stale >= 1` from that real
write-path state. For the **placement** assertion the fixture pins the intermediate order by
stubbing `hybrid_search` to a deterministic stale-first pool, because the production fusion is not
reliably invertible in fixture data: the CORRECTS edge expands the structural leg, ranking the
**successor** up, and the arm's own recency weight ranks the **newer-created** row up (not
necessarily the live one) — so a raw-pool inversion cannot be constructed from fixture data alone.
The stub makes the OFF arm provably stale-first (`[STALE, LIVE]`), so the ON differential is
attributable to the reorder alone; a mutation that disables the reorder reds the test (verified).
The reorder test does **not** require the embedder (FTS/structural legs alone retrieve the pair);
only the date-anchor test is gated on `EmbeddingModel.get() is not None`.

## Rejected alternatives

| Alternative | Why rejected |
|---|---|
| Reader-only hardening (tune the KU prompt / post-read guard) | Failure is upstream; the successor may never enter the window. Fixes neither the anchor nor the label gate. |
| Filter superseded everywhere | Breaks the historical class and deletes the marker-based discount surface. |
| A hard validity-window filter for KU (reuse `_apply_time_window`) | A window would drop the successor when the gold session is older than the window; the never-starve fallback would fire constantly and the lever would measure as noise. |
| Inject the date into the FTS query too | Date tokens dilute the sparse OR-union for zero benefit. |
| Apply the reorder inside `tortoise_fts_query` | Review cycle-1 P0: `hybrid_search`'s RRF re-sort discards it; the sealed A/B would measure a no-op. |
| On-by-default in the product | Changes read behaviour with no measured delta; #1745 fail-safe decision + C2 #2518 precedent require OFF. |
| Reuse `detect_time_constraint` as the intent detector | Its `recency` is a *window* (older-than-N); C6 needs a *freshness preference*. Reusing it would either change R5's TR filter semantics or collide vocabularies. Named distinctly and documented; a consistency test pins the vocabularies disjoint. |

## Integration surface map

| Surface | Type | Covered by | Status |
|---|---|---|---|
| `tortoise/time_aware.py` (new pure module) | product logic | hermetic unit tests (no DB) | ✅ |
| `tortoise/sdk.py::tortoise_fts_query` — `query_date`/`time_aware` kwargs + dense anchor | SDK read path | docker integration test (embedder-gated) | ✅ |
| `tools/longmem_eval/retrieve.py::hybrid_search` | eval caller | unit (kwarg threading) | ✅ |
| `tools/longmem_eval/retrieve.py::retrieve_for_question` — intent, recency weight, prefer-latest on the **final `pool` immediately before `_recall_metrics`** (after every pool-mover), outcome fields | eval arm | docker integration (FTS-only, embedder-independent) | ✅ |
| `tools/longmem_eval/run.py` — env resolve, CLI flag, `_build_fingerprint`, retrieve threading, outcome + report projection | eval harness | hermetic unit (fingerprint/resolve) | ✅ |
| `config/ci-surfaces.yml` | CI selection | registration per file (`core`; `core`+`sdk` for the docker SDK file; +`eval` for the files touching `tools/longmem_eval/`) + `durations:` entries (the 90% `duration_coverage_issues` floor); docker files fast-pool, no `slow_files` edit | ✅ |
| MCP tool surface (`TOOL_REGISTRY`) | NOT TOUCHED | — | ✅ |
| Public SDK method count | NOT TOUCHED (kwargs only) | `tools/surface-guard.py` | ✅ |
| `docs/product/mcp-sdk-surface.md` | NOT TOUCHED (no new tool/field) | `surface_manifest check` | ✅ |

Runtime prerequisites: `TORTOISE_DB_URI` (docker FalkorDB) for the integration test; the
`embeddings` extra only for the date-anchor leg (that test skips with the reason when
`EmbeddingModel.get()` is None — the reorder leg does not).

## Acceptance criteria

1. `detect_temporal_intent("where do I live now?")` → `prefer-latest`;
   `…("where did I live in 2024?")` → `date-pinned`; `…("what is your name?")` → `None`;
   `detect_temporal_intent(None)` and `("")` → `None`; an MSR-shaped recency query
   (`"has the user changed their mind about the gym?"`) → `prefer-latest`. The kind vocabulary is
   disjoint from `detect_time_constraint`'s `{interval, recency, ordering}` (consistency assertion
   in Task 1's test list).
2. `inject_query_date(q, "2026-09-25") != q`; the same `q` with another date differs only in the
   date; missing/invalid date → `q`; applying twice is idempotent.
3. `prefer_latest_order` under `prefer-latest` puts a live successor above a stale predecessor;
   under `date-pinned`/`None` the order is unchanged; all-stale and no-stale both return the input
   order with `applied=False`; a **future** `valid_to` (after `question_date`) is NOT stale; a
   window closed exactly ON `question_date` IS stale; a **numeric-epoch** `valid_to`/`expired_at`
   (past and future) is classified correctly; an unparseable window value is NOT stale; a
   **status-only** stale row (`status="retracted"`, no `superseded_by`, no window) IS demoted (the
   clause that catches `retract_point`).
4. `tortoise_fts_query(..., time_aware=False)` is byte-identical to today; `detect_temporal_intent`
   may not be called at all on the off path.
5. `tortoise_fts_query(q, time_aware=True, query_date=…, include_terminal=True)` embeds the
   date-anchored string on the dense leg and leaves the FTS leg's query unchanged.
6. `retrieve_for_question` returns the final ranked order with the live successor above the stale
   predecessor for a `prefer-latest` KU question (asserted on `ranked_ids`, i.e. after
   `hybrid_search`'s sort AND after every pool-mover), and leaves a `date-pinned` question's order
   untouched; `time_aware_stats["applied"]`/`["tr_excluded"]` are asserted (a TR question under
   the armed run records `tr_excluded=True`, `applied=False`); the D5 recency weight is threaded
   for a non-TR `prefer-latest` question and stays `0.0`/`None` for a `date-pinned` one. The
   placement test stubs `hybrid_search` (see D9) so the reorder is the only mover; the recency
   seam is asserted directly on the stub's captured kwargs (default `0.5` under a `prefer-latest`
   intent, `0.0`/no fields under `date-pinned` or OFF).
7. `run.py` resolves the tri-state, stamps `_build_fingerprint`, and projects the fields.
8. `uv run python tools/surface-guard.py` and `uv run python tools/surface_manifest.py check` green.

## Tasks

### Task 1: `tortoise/time_aware.py` (pure module)
**Intent:** Own the C6 mechanism in the product layer.
**Acceptance:** AC 1–3. `ruff` clean; no DB/LLM import at module scope.
**Files:** Create `tortoise/time_aware.py`; Test `tests/test_time_aware_2520.py` (hermetic).
Steps (TDD): write failing tests for intent (positive/negative/precedence/None-safety/MSR),
inject (present/absent/invalid/idempotent/date-only-diff), prefer-latest (prefer-latest,
date-pinned guard, non-intent, all-stale, no-stale, future-window, window-closed-on-question-date,
status-only-retracted), and the vocabulary-disjointness assertion vs `detect_time_constraint`;
implement until green.

### Task 2: `tortoise_fts_query` dense-leg anchor
**Intent:** Make the date anchor reach the embedding.
**Acceptance:** AC 4–5.
**Files:** Modify `tortoise/sdk.py`; Test `tests/test_time_aware_sdk_2520.py` (docker half — the
recording embedder proves the anchored string reaches `model.encode`), plus a HERMETIC half that
always runs: the anchor decision is factored into `tortoise.time_aware.dense_query_for` (pure), so
`test_time_aware_2520.py` and `test_time_aware_sdk_2520.py` pin it without a graph, and the graph
halves carry per-test `skipif`s — AC5 is never permanently skipped in CI (a FalkorDB-less lane
still proves the decision; the graph half proves the wiring).
Steps: add kwargs + docstring; factor the decision into `dense_query_for`; use the returned string
for the vector encode only; off path byte-identical (no import on the off path).

### Task 3: eval arm (`retrieve.py`)
**Intent:** The sealed A/B can switch the lever on and reconstruct the arm; the reorder survives
`hybrid_search`'s sort.
**Acceptance:** AC 6.
**Files:** Modify `tools/longmem_eval/retrieve.py`, `tests/test_session_reinjection_rules.py`,
`tests/test_coverage_loop.py`; Test `tests/test_time_aware_eval_2520.py`
(docker, FTS-only, embedder-independent) — the reorder + `_hybrid_kwargs` threading + outcome.
Steps:
1. Hoist `question_date = question.get("question_date", "") or None` **above** the `hybrid_search`
   call (it is currently bound only inside the `if is_tr:` block and later for the reader header —
   cycle-2 P0: referencing it earlier raises `UnboundLocalError` for every question). Reuse the one
   binding everywhere.
2. `hybrid_search` threads `query_date`/`time_aware` into `tortoise_fts_query` ONLY on the ON path,
   via the existing `_hybrid_kwargs` dict (off path passes no new kwarg → byte-identical and strict
   hermetic stubs with fixed signatures keep working).
3. `_annotate_hits`: add `status` sourced from the search payload (`h.get("status") or ""`) —
   NOT from `point_props_for_hits`, which does not fetch it. This takes the annotated key set from
   17 to 18, so update the two goldens that pin it (`tests/test_session_reinjection_rules.py`
   ~line 688 `len(base) == 17`, `tests/test_coverage_loop.py` ~line 254 `len(inj_hit) == 17`) and
   the `annotate_pool_additions` "all 17 annotated keys" docstring — add both to this task's Files
   and to Task 5's verification. Do NOT add `outdated` (see D2).
4. `retrieve_for_question`: tri-state resolve; detect intent; guard `not is_tr`; for a non-TR
   `prefer-latest` question pass `recency_fields`/`recency_boost` (D6); apply
   `prefer_latest_order(pool, intent=…, question_date=…)` immediately **before the final
   `_recall_metrics(pool, …)`** call; record `time_aware_qe` + `time_aware_stats` (incl.
   `applied`/`tr_excluded`).

### Task 4: harness plumbing (`run.py`)
**Intent:** An unarmed lever is a dead lever.
**Acceptance:** AC 7.
**Files:** Modify `tools/longmem_eval/run.py` (D8 site checklist), `tests/test_longmem_runner.py` (the
`test_outcomes_to_report_golden_shape` pin on the projected per-outcome key set); Test
`tests/test_time_aware_run_2520.py` (hermetic: resolve + fingerprint + **both projections** — the
outcome field and the published-report allow-list, each a silent-failure site). Add
`"time_aware_qe": None` (and `"time_aware_stats": None` if the stats block rides the projection,
as `coverage_loop_stats` does) to the golden next to `"entity_key_expansion": None`.
Steps (the full `entity_key_expansion` site checklist — every one required): `_build_fingerprint`
param + dict; `run_evaluation` signature; env resolve; the fingerprint call; the
`retrieve_for_question` call; outcome projection; the `r1_knobs` methodology record; the
published-report allow-list; the CLI flag pair; the main resolve; the main `run_evaluation(...)`
call.

### Task 5: CI registration + verification
**Intent:** An unregistered test file never runs.
**Acceptance:** AC 8 + the files are selected by the tooling.
**Files:** Modify `config/ci-surfaces.yml`.
Steps: register against the **verified** precedent — `tortoise/time_aware.py` matches no
`SOURCE_PATTERNS` entry, so a `time_aware.py`-only change selects only the `core` fallback. The
hermetic `test_time_aware_2520.py` (subject: the product module) goes under `core`.
`test_time_aware_run_2520.py` pins `tools/longmem_eval/run.py`, whose whole tree maps to `eval`
(`ci_selection.py` SOURCE_PATTERNS) — so it goes under **`core` + `eval`** (the
`test_eval_reinjection_cap_resume.py` precedent), otherwise a future `run.py`-only change would
silently never run it. The docker `test_time_aware_sdk_2520.py` The docker `test_time_aware_sdk_2520.py` (pins
`tortoise/time_aware.py` + `tortoise/sdk.py`) goes under `core` + `sdk`, exactly as
`test_entity_key_expansion.py` (core 990 + sdk 1364) and `test_session_reinjection.py` (core 989 +
sdk 1363) do. `test_time_aware_eval_2520.py` touches `tools/longmem_eval/retrieve.py`, so it goes
under `eval` as well (as the C4 file does at 1459). Add a `durations:` entry for **every** new file
— `tools/ci_selection.py::duration_coverage_issues` enforces a 90% floor and CI runs it. The docker
files are fast-pool (`durations: 0.0` per the #2517 precedent) — **no `slow_files` /
`.github/workflows/python-ci.yml` edit**. Minimal edit, no reorder.
Then: `uv run python tools/surface-guard.py`; `uv run python tools/surface_manifest.py check`;
`uv run pytest tests/test_time_aware_2520.py tests/test_time_aware_run_2520.py
 tests/test_time_aware_sdk_2520.py tests/test_time_aware_eval_2520.py
 tests/test_session_reinjection_rules.py tests/test_coverage_loop.py tests/test_longmem_runner.py -v`.

## Parallelization

Task 1 (pure module) and Task 4 (run.py mirror) have no dependency on Tasks 2/3 once the kwarg and
field names are frozen — they run concurrently with the Task 2→3 integration chain. The CI
registration half of Task 5 is independent of all of them. Tasks 2→3 are sequential (Task 3
consumes the kwargs Task 2 adds). Final verification is last.

## Not covered (stated plainly)

- **The rank-time reorder lands on the eval's measured pool, not on a shipped product surface.**
  `tortoise_fts_query(time_aware=True)` carries the query-side date anchor only. The readers that
  pass `include_terminal=True` and would need the reorder — `tools/longmem_eval/retrieve.py`
  (covered here) and `tortoise/ask_lane.py::run_ask_lane` — are both **eval-only** surfaces
  (`ask_lane.py` says so explicitly: #3849, no product caller; its `include_terminal=True` call is
  the eval's reader path). The product read paths (`tortoise_search` default,
  `tortoise_recall` mode=state) exclude terminal points at base retrieval, so there is nothing to
  demote. A product-facing rank-time path would need a score/order contract the eval honours and
  its own test; that is **not** this task's scope (recorded, not silently claimed).
- The product's terminal-exclusion recall gap for **explicit-date history** ("where did I live in
  2024?" unreachable on the default search path) — this plan guarantees the fix does not *worsen*
  it and does not apply the fresh bias to it. Closing it needs a time-travel read surface; the
  as-of / `restore_point_at` gap is owned by the existing epic **#2349** (record-axis history layer).
- No sealed LongMemEval A/B is run here. The lever is fully arm-able and the re-check is the sealed
  run under **#2516** (C1 measurement gate); the arm requirement (arm name, ingest mode that makes
  the prefer-latest half live vs inert, KU **and MSR** target metrics) is posted as a
  comment on #2516 (issuecomment-5835261141) — not left in this plan doc.
- The recency-weight lever (`recency_fields`/`recency_boost`) for non-TR questions changes retrieval
  ordering; its harm/benefit is deliberately left to the sealed A/B (that is the point of the gate).

<!-- plan-review: cycles=5, status=clean, version=2.3.0 -->
