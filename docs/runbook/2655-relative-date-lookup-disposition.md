---
title: "#2655 — census `relative-date-lookup` disposition"
type: operations
domain: operations
doc_status: live
created: 2026-10-10
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
issue: 2655
---

# #2655 — census `relative-date-lookup` disposition

> The census class is (6/133 rows, `tests/_assembly_census.json`). **Measured
> 2026-10-10**: three of its six rows name a resolvable calendar anchor and now
> fire (`RELATIVE_DATE`); the other three name no calendar anchor at all and
> stay non-firing **by design**. Three FURTHER census rows filed under other
> classes are the same shape and fire too — the label under-counts its own
> class.

## 1. Fired coverage of the class (re-measured, from the committed census)

Command (deterministic, no DB, no model):

```bash
.venv/bin/python -c "
import json
from tortoise.assembly import classify_question
d = json.load(open('tests/_assembly_census.json'))
for r in d['rows']:
    if r['cls'] == 'relative-date-lookup':
        print(r['qid'], classify_question(r['question']), '|', r['question'])"
```

| qid | question | anchor | HEAD | now |
|---|---|---|---|---|
| `71017277` | I received a piece of jewelry **last Saturday** from whom? | weekday − `question_date` | `None` | `relative-date-lookup` |
| `gpt4_f420262d` | What was the airline that I flied with **on Valentine's day**? | fixed date (2/14) | `None` | `relative-date-lookup` |
| `gpt4_e414231f` | Which bike did I fixed or serviced **the past weekend**? | weekend of `question_date` | `None` | `relative-date-lookup` |
| `gpt4_4929293b` | … I participated in **a week ago**? | none (day-offset — #2652's class) | `None` | `None` |
| `gpt4_4edbafa2` | What was the **date** on which I attended the first BBQ event in June? | none (ordering-within-a-month) | `None` | `None` |
| `gpt4_9a159967` | Which airline did I fly with the **most** in March and April? | none (frequency/count — #2886's class) | `None` | `None` |

**3/6 fire** (from 0/6). The three non-fires are out of family, not failures:
one belongs to the `ago`-relative lane (#2652, live concurrently), one is an
ordering-within-a-month question with no day at all, and one is the
`frequency/count` shape #2886 already owns. Absorbing them would duplicate
other lanes' work and violate MECE; they are recorded, not silently dropped.

## 2. The label under-counts the class (measured)

Three rows the census filed **outside** `relative-date-lookup` are plain
calendar-anchor lookups (each names a `last <weekday>` and nothing else). They
now fire as `relative-date-lookup`, which is why the negative-set denominator in
`tests/test_assembly_pure.py` is corrected by 3 rather than the change being
counted as precision loss:

| qid | census class | question |
|---|---|---|
| `gpt4_d6585ce9` | `recency/current-state` | Who did I go with to the music event **last Saturday**? |
| `gpt4_468eb064` | `recency/current-state` | Who did I meet with during the lunch **last Tuesday**? |
| `gpt4_fa19884d` | `other` | What is the artist that I started to listen to **last Friday**? |

Negative-set precision after the correction: **1/29 = 0.9655** (floor ≥ 0.95).
The denominator is the residual set on current `main` — the 35 rows left by
the #2652/#2653/#2654 re-measurements — minus this lane's 3 calendar-anchored
`relative-date-lookup` rows (35 → 32, ``_RELATIVE_DATE_ANCHORED``), minus the 3
misfiled rows above that are re-adjudicated INTO the class (32 → 29,
``_RELATIVE_DATE_CLASS_EXPANSION``). The single remaining fire is the
pre-existing, already-adjudicated in-shape twin `370a8ff4` (the second twin the
pre-merge draft counted, `gpt4_70e84552_abs`, left the denominator with #2653's
nary-ordering re-measurement).

## 3. Legacy A-arm comparison (committed evidence)

`docs/runbook/2578-measured-outcomes-133.jsonl` (arm `A-default-133q`) — for all
six rows:

```text
71017277       gold_admitted=False  reader_refusal=True
gpt4_f420262d  gold_admitted=False  reader_refusal=True
gpt4_e414231f  gold_admitted=False  reader_refusal=True
gpt4_4929293b  gold_admitted=False  reader_refusal=True
gpt4_4edbafa2  gold_admitted=False  reader_refusal=True
gpt4_9a159967  gold_admitted=False  reader_refusal=True
```

The legacy A-arm admits **0/6** gold and the reader refuses on all six, so the
issue's "gold admission ≥ legacy A-arm" half is satisfied at ≥ 0. The A-arm
numbers are a *floor*, not a capability measure — the target that matters is the
fired coverage in §1, which the committed synthetic-graph lane verifies
end-to-end on the real pipeline
(`tests/test_assembly_sdk.py::test_relative_date_lookup_fires_on_the_named_day`).

## 4. What was built

* `tortoise/calendar_anchors.py` — pure, zero-LLM, zero-clock anchor resolver:
  a reviewed fixed-date holiday table (`(month, day)`, optional explicit year) +
  weekday/weekend offsets from the caller's `as_of`, **never a future day**
  (fixed dates roll back a year; an explicit future year abstains) and
  **abstain on ambiguity** (two distinct anchors → `None`).
* `tortoise/assembly.py` — `AssemblyShape.RELATIVE_DATE` (value = the census
  class name, so attribution reads the census string verbatim), a high-precision
  classifier branch that never hijacks another shape, `collect_day_slices` (an
  as-of **equality-day** lookup over the typed spine, undated rows excluded),
  the `day_rows` walker seam + docker adapter, a date-scoped renderer, and the
  `_assemble_connected` branch — which **refuses to fire on an empty day** so
  the legacy lane runs byte-identically.

## 5. Residuals (recorded, not hidden)

* Movable feasts (Thanksgiving, Easter, Labour day) need nth-weekday-of-month
  arithmetic — not in the v1 table.
* The day scan is a per-day prefix-range read over `Point`/`Event`: bounded
  (LIMIT + `bounded_fanout`), but a scan rather than an indexed lookup — a date
  index is the named follow-up if the shape reaches product traffic.
* The 133-Q sentence-level re-measure could not be re-run in this lane (the
  LongMemEval corpus is not committed and no cache is present); fired coverage
  is measured on the committed census + committed legacy A-arm outcomes, and the
  end-to-end answer path is pinned on the committed synthetic graph fixture.
* The private test lane's FalkorDB container crashed (native panic in
  `src/reply.rs:655` / segfault in `reply_compact` during `execute_query`)
  while running `tests/test_assembly_sdk.py` on this host — including on the
  flag-OFF golden test, which never enters any #2655 code, so the crash is
  environmental. What DID run against the real engine before the crash:
  `tests/test_assembly_sdk.py::test_relative_date_lookup_fires_on_the_named_day`
  (PASSED — the whole day lookup end-to-end on the real graph) and
  `tests/test_assembly_pure.py -k walker` (14 passed, including the docker
  walker round-trips). The remaining cases are pinned hermetically
  (`tests/test_assembly_pure.py` — the day collector, the renderer, and the
  fired decision driven through `_assemble_connected` with stubbed graph seams,
  plus the SUBJECT path through the same orchestration so the shared
  `_finish_fired` tail extraction is regression-pinned without a DB).

## 6. Test evidence

```bash
# pure lane (no DB, no model) — classifier, anchor resolver, day collector,
# renderer, and the fired decision:
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
  tests/test_assembly_pure.py tests/test_calendar_anchors.py -q \
  -p no:cacheprovider -k "not docker"
# -> 206 passed, 3 skipped, 12 deselected on this lane's own commit;
#    221 passed, 3 skipped, 12 deselected after the origin/main merge that
#    brought #2654's duration-state shape in (the +15 are that shape's tests).

# committed-census neighbours (temporal leg + measurement subset + eval-only lane):
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
  tests/test_temporal_leg_2976.py tests/longmem_eval/test_measure_temporal.py \
  tests/test_ask_eval_only.py -q -p no:cacheprovider
# -> 107 passed

# private lane instance (#5084), real FalkorDB:
TORTOISE_DB_URI='docker://:@127.0.0.1:16395/tortoise_test_matrix' \
  .venv/bin/python -m pytest tests/test_assembly_pure.py -q -k "walker"
# -> 14 passed

python tools/ci_selection.py --integrity   # -> integrity: all test files classified
```
