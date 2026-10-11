---
title: "Relative-date-lookup resolution (#2655) — Implementation Plan"
type: engineering
domain: capability
doc_status: draft
created: 2026-10-10
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: "#2655, #2165, tests/_assembly_census.json"
---

<!-- research-path: docs/plans/2026-09-08-2165-connected-assembly.md + tests/_assembly_census.json + docs/runbook/2578-measured-outcomes-133.jsonl -->

# Relative-date-lookup resolution (#2655) — implementation plan

> **For Pi:** scoped + planned inline (the lane runs without a `task` tool; the
> parent owns the review gate). Pipeline level: **project** (complexity:standard,
> `domain:data`, team:epistemic-team). Align is INHERITED from #2165's epic
> decision — see "Align inheritance" below.

**Goal:** give the connected-assembly lane a DETERMINISTIC calendar-anchor
resolver (fixed-date holidays + weekday-of-`question_date` offsets) feeding an
**as-of equality-day lookup over the walker's typed slices**, so a
"What … on Valentine's day / last Saturday / the past weekend?" question fires on
the day's own evidence instead of firing nothing.

---

## 1. Measured problem (re-measured 2026-10-10, not trusted from the body)

The body's claim ("6/133 relative-date-lookup rows currently fire NOTHING")
**HELD on re-measurement** — plain `classify_question` over the 6 committed
census rows returns `None` for all six:

```bash
python -c "from tortoise.assembly import classify_question; ..."   # 6/6 -> None
```

The legacy A-arm's own committed measurement
(`docs/runbook/2578-measured-outcomes-133.jsonl`, arm `A-default-133q`) shows
`gold_admitted: false` + `reader_refusal: true` for **all six** — so the
"gold admission ≥ legacy A-arm" half of the target is satisfied by any
admission count ≥ 0.

Re-measuring the CLASS (not just the count) changes the shape of the work:
the census label is only ~half calendar-anchored.

| qid | question | anchor family | in this lane? |
|---|---|---|---|
| `71017277` | I received a piece of jewelry **last Saturday** from whom? | weekday-of-`question_date` offset | ✅ |
| `gpt4_f420262d` | What was the airline that I flied with **on Valentine's day**? | fixed-date holiday | ✅ |
| `gpt4_e414231f` | Which bike did I fixed or serviced **the past weekend**? | weekend of `question_date` | ✅ |
| `gpt4_4929293b` | … I participated in **a week ago**? | day-offset (`ago`) — **#2652's class** | ❌ (MECE) |
| `gpt4_4edbafa2` | What was the **date** on which I attended the first BBQ event in June? | no anchor; ordering-within-month | ❌ |
| `gpt4_9a159967` | Which airline did I fly with the **most** in March and April? | frequency/count — **#2886's class** | ❌ |

This mirrors #2886's measured finding on `frequency/count` ("the class is not
what the label says"). **Honest coverage target: 3/6 rows fire**; the other 3 are
recorded as out-of-family with their owning issue, never absorbed.

### Align inheritance

The #2165 plan filed #2652–#2655 as *recorded follow-ups* with an explicit trigger
(census). Its "Out of scope" note says ago-relative "needs LLM anchor+offset —
constraint 6" — **#2655 is deliberately the deterministic subset of that**:
fixed-date holidays and weekday offsets need no model, only `question_date`.
No recorded decision is contradicted: the #2165 decision was "the connected
branch fires only on shapes with MEASURED deterministic support"; #2655 supplies
that measurement + the deterministic mechanism for one more shape.

**OVERRIDES:** the #2165 R13 "reject-on-relative-offset" rule that pinned
`last Saturday` / `Valentine's day` as never-firing — overridden *only* for the
calendar-anchor surface, because the date is deterministically computable from
`question_date` (R13's reason was "cannot resolve without a model", which is
false for fixed dates and weekday offsets); every other relative offset
(`ago`, `yesterday`, `last week/month/year`, `in N days`) stays rejected.

### Boundaries (out of scope, recorded)

- `ago`-relative day offsets (`a week ago`) → **#2652** (live concurrently).
- Month-window frequency/count (`most in March and April`) → **#2886** (closed).
- Movable feasts (Thanksgiving, Easter) — they need nth-weekday-of-month
  arithmetic, not a fixed `(month, day)`. Left out of the v1 table (a named
  residual, not a silent omission).
- No reader fragments, no qtype change, no MCP/SDK surface change.

## 2. Alternatives considered

| # | Approach | Outcome | Verdict |
|---|---|---|---|
| A | **Anchor table + equality-day typed-slice lookup** inside the assembly lane (new pure anchor module + `collect_day_slices` over the walker port) | Deterministic, zero-LLM, reuses `_norm_date`/`_tier_and_date` (ONE date source), fits the body's "typed slices" target; the day lookup is gated by row presence so a false fire falls through to legacy | **CHOSEN** |
| B | Reuse the eval-lane `detect_time_constraint` date WINDOW (hard filter on `session_date`) | Lives in `tools/longmem_eval/retrieve.py` — the *retrieval* lane, not the assembler; the body explicitly asks for the typed-slice path; would create a second date-resolution implementation (two sources of truth) | rejected |
| C | LLM anchor extraction (constraint 6's escape hatch) | Non-deterministic, fails the "deterministic calendar anchors" target, adds model cost/latency to a shape whose date is arithmetic | rejected |
| D | Widen `_RE_AGO`-adjacent regexes into the existing `INTERVAL` shape | Wrong shape semantics (an interval needs two anchors); would misfire on single-anchor lookups | rejected |

**Why A is the better outcome, not merely the easier one:** it introduces no
second date library (the anchor module is pure and the walker keeps owning
`_norm_date`), it is *as-of safe* by construction (a resolved day is never after
`question_date`), and it never silently degrades a question that another shape
owns (the classifier returns `None` for every anchor-bearing question that also
carries another shape's morphology — byte-identical to pre-change behaviour,
because `_RE_AGO` already rejected all of them).

## 3. Implementation plan

1. **`tortoise/calendar_anchors.py`** (new, pure — no graph, no model, no clock):
   `find_anchor_spans(question)`, `resolve_calendar_anchor(question, as_of)`.
   - fixed-date holiday table (Valentine's, New Year's day/eve, Christmas
     day/eve, Halloween, Independence day / the Fourth of July, St Patrick's
     day, April Fools', Veterans day) + optional explicit year;
   - `last <weekday>` / `this past <weekday>` / `past <weekday>` → the most
     recent such weekday **strictly before** `as_of`;
   - `last/this past/the past/this weekend` → the most recent Saturday on or
     before `as_of`, plus its Sunday when that Sunday is not after `as_of`;
   - **as-of discipline:** a fixed date in `as_of`'s year that is after `as_of`
     rolls back one year; an explicit FUTURE year abstains. A resolved day is
     never after `as_of`.
   - **ambiguity → abstain:** >1 distinct anchor span returns `None`.
2. **`tortoise/assembly.py`**
   - `AssemblyShape.RELATIVE_DATE = "relative-date-lookup"` (value = the census
     class name, so measurement attribution reads it literally);
   - `classify_question`: an anchor branch evaluated BEFORE the guards, on a
     residual that has the anchor span **removed** — so an anchor-bearing
     question that also carries another shape's morphology (or a non-anchor
     relative offset) still returns `None`, exactly as today;
   - `extract_subject_terms` returns `[]` for this shape (the day is the scope,
     not a subject) — the fired path branches before extraction;
   - `collect_day_slices(port, days, *, per_day_cap)` — the as-of EQUALITY-day
     lookup over the typed spine: R2 ladder (`_tier_and_date`), **undated rows
     excluded** (an undated row can never satisfy a day equality), `id` dedupe,
     deterministic `(date, kind, id)` order, bounded by `bounded_fanout`, with
     the same admission census shape as `collect_slices`;
   - `WalkerPort.day_rows(days, cap)` + the docker adapter (one batched
     prefix-range statement per day per kind — never row-level N+1);
   - `synthesize_hits(..., date_anchor=…)`: a `DATE (<label>): <iso[, iso]>`
     header + the day's rows, sorted; the subject-section loop is bypassed
     (there are no resolved subjects);
   - `_assemble_connected`: branch `RELATIVE_DATE` → resolve the anchor from
     `question_date` → `collect_day_slices` → **no rows on that day ⇒
     `fired=False` and legacy falls through** (the R1/P1-1 spirit: fire only
     when the fired block is a real replacement). The shared
     decorate/enrich/`assemble_context` tail is reused unchanged.
3. **Tests** — `tests/test_calendar_anchors.py` (new, pure: table, weekday
   arithmetic, as-of rollback, weekend window, future-year abstention,
   ambiguity, malformed `as_of`); `tests/test_assembly_pure.py` additions
   (RELATIVE_DATE classification incl. every preserved negative, day-slice
   collector on the dict-stub port, and the **re-pinned census fire count**);
   `tests/test_assembly_sdk.py` addition (docker end-to-end fired path + the
   no-rows-on-day fallback).
4. **`config/ci-surfaces.yml`** — register `test_calendar_anchors.py` in the
   `core` surface (its subject `tortoise/calendar_anchors.py` matches no
   `SOURCE_PATTERNS` rule, so `core` is the exact selection home). Verify with
   `python tools/ci_selection.py --integrity`.
5. **Docs** — update the `tortoise/assembly.py` module docstring's
   never-fires list (relative-date-lookup moves out of the negative set with the
   measured 3/6 note) and record the plan/measurement outcome on #2655.

## 4. Integration surface map / wiring check

| Surface | Test layer | Bug-pattern flag |
|---|---|---|
| `tortoise/calendar_anchors.py` (new) | pure unit (no DB/model) | as-of rollback off-by-one; Feb-29/garbage `as_of`; future-year abstention |
| `tortoise/assembly.py` classifier | pure unit + the committed 133-row census | **precision floor**: no NEW false fire on the measured negatives (asserted) |
| `tortoise/assembly.py` day-slice collector | dict-stub port unit + docker integration | undated-row leak; cap accounting; input-order dependence |
| `tortoise/assembly.py` `_assemble_connected` | docker (existing `test_assembly_sdk.py` harness) | flag-OFF byte-identity (untouched); empty-day must fall through, never render an empty block |
| `tests/_assembly_census.json` | committed artifact (read-only) | the census is the trigger record — never regenerated here |
| `config/ci-surfaces.yml` | manifest / `--integrity` | an unregistered test file is never selected and fails `manifest-integrity` |
| MCP tool registry / `TortoiseSDK` public surface | **not touched** | new shape is internal; no approval needed |

## 5. Acceptance criteria

1. `classify_question` returns `AssemblyShape.RELATIVE_DATE` for the 3
   calendar-anchored census rows and `None` for the other 3 (pinned).
2. **3/6** census rows fire (measured, pinned) — from 0/6 at HEAD.
3. Zero new false fires over the census negative set (measured, pinned): the
   negative-set precision floor still holds unchanged.
4. Every pinned never-fire example at HEAD still returns `None` — except the two
   calendar-anchored rows this issue exists to reverse (deliberate, documented).
5. `resolve_calendar_anchor` never returns a day after `as_of`; abstains on
   ambiguity, multiple anchors, missing/garbage `as_of`, and explicit future years.
6. Fired path with no rows on the anchored day ⇒ `fired=False` (legacy falls
   through byte-identically).
7. `flag OFF` ⇒ all existing ask-lane goldens byte-identical (unchanged code path).
8. `python tools/ci_selection.py --integrity` passes with the new test file
   registered.

## Status — implementation (2026-10-10)

Implemented as planned. Measured results pinned in tests:

- census `relative-date-lookup`: **3/6 fire** (was 0/6); the 3 non-anchored rows
  stay non-firing with their owning issues recorded (#2652 / #2886 / no-anchor).
- negative-set precision after the class correction: **1/29 = 0.9655** (≥ 0.95);
  3 further census rows adjudicated INTO the class by morphology (the residual
  set is current `main`'s 35 rows minus this lane's 3 anchored class rows and
  those 3 re-adjudications).
- pure lane **206 passed** · census neighbours **107 passed** · docker walker
  round-trips **14 passed**; the day-path SDK test passed against a real
  FalkorDB (later runs blocked by a native FalkorDB panic in the test container,
  reproducible on a flag-OFF test — environmental, see the runbook §6).
- `ci_selection --integrity` clean; `ruff` clean on every touched file.

Full disposition + exact commands:
`docs/runbook/2655-relative-date-lookup-disposition.md`.

## 6. Residuals (recorded, not hidden)

- Movable feasts (Thanksgiving/Easter) are not in the v1 table.
- The day scan is a per-day prefix-range read over `Point`/`Event`; it is
  bounded (LIMIT + `bounded_fanout`) but is a scan, not an indexed lookup — a
  date index is a named follow-up if this shape reaches production traffic.
- 3/6 census rows stay non-firing by design (rows owned by #2652 and #2886, and
  one row with no anchor at all).
- The 133-Q sentence-level re-measure cannot be re-run in this lane (the
  LongMemEval corpus is not committed and no cache is present); the fired-coverage
  claim is therefore measured on the committed census + committed legacy A-arm
  outcomes, and the end-to-end answer path is pinned on the committed synthetic
  graph fixture.
