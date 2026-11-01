---
title: "#10137 — the corpus census: questions_with_ep / questions on frozen LongMemEval-S"
type: evidence
domain: platform
doc_status: live
created: 2026-10-31
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph, epistemic belief layer, LongMemEval-S corpus
aboutObjects: "#10137, #10188, #8486, #2598, ingest_v2, measurement_posture"
---

# #10137 — the corpus census: `questions_with_ep / questions` on frozen LongMemEval-S

**The number this issue was opened to produce, measured on a pre-registered 24-question sample.**
Until now the instrument
(`#10188`, squash `4b014cdca`) emitted the cells and nobody had ever run them on the corpus; the
issue's verification comment recorded that as `PARTIAL` and the remaining work as "needs the #8486
step-5 run". This is a **pre-registered stratified sample**, not the 500-question population — the
denominator is stated everywhere below.

```text
questions_with_ep / questions = 24 / 24 = 1.00      (all six question types: 4/4 each)
corpus   = xiaowu0162/longmemeval-cleaned, split s (frozen)
           sha256:d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442
sample   = 24 questions of 500, type-stratified 4×6, chosen BEFORE the run by the committed
           selector (tools/longmem_eval/run_protocol.py::stratified_slice_indices) — never a
           head slice (#2516)
ingest   = v2 (production 5-stage extractor), --activate-beliefs (promote drafts→live, then EP)
params   = --workers 4 --session-workers 2, embedded per-question graphs,
           --mock (MockReader/MockJudge: the cell is the EP census, not QA accuracy — no answer
           spend), --skip-preflight → dense leg WAIVED (embedder absent), retrieval was FTS-only
score    = PARTIAL(24 of 500) — a complete sample of a sample, never presented as the population
```

`belief_layer = activated`, `failed = 0`, `ep_ran = 24`, `errors = []`.

## The cells (`census.json`)

| cell | value |
|---|---|
| `questions` / `questions_with_ep` | 24 / **24** |
| `questions_inert` (`inert_by_reason`) | **0** (`{}`) |
| `questions_neutral` | **0** |
| `questions_off_neutral` | 24 |
| `operators` (graph census, sum) | **1,710** (71/question, min 259 per type-sum) |
| `factor_edges` (direct claim↔claim IMPL/NAND) | **0** |
| `points_with_ep` | 2,696 |
| `points_unmeasured` | 24,912 |
| `points_with_ep_unread` / `points_unmeasured_unread` | **0 / 0** (every question's census was read — the sum is complete, not partial) |
| `points_off_neutral` = `points_moved_off_neutral` | 185 |
| `points_off_neutral_unread` / `points_moved_off_neutral_unread` | **0 / 0** (same disclosure for the two contribution sums — a partial 185 is not read as a complete one) |

### Per question type

| question type | questions | with_ep | operators | inert | neutral | off_neutral | points moved |
|---|---|---|---|---|---|---|---|
| single-session-user | 4 | 4 | 311 | 0 | 0 | 4 | 36 |
| single-session-assistant | 4 | 4 | 259 | 0 | 0 | 4 | 33 |
| single-session-preference | 4 | 4 | 320 | 0 | 0 | 4 | 27 |
| multi-session | 4 | 4 | 292 | 0 | 0 | 4 | 34 |
| temporal-reasoning | 4 | 4 | 260 | 0 | 0 | 4 | 26 |
| knowledge-update | 4 | 4 | 268 | 0 | 0 | 4 | 29 |

## What this falsifies, and what it does not

**Falsified: the premise that the zero-operator population is the common case.** The issue body and
`docs/benchmarks/longmemeval-500q/run-report.md:54` both assert that "single-session questions are
frequently this shape" (no IMPL/NAND ⇒ `no_ep_factors`). On 24 real questions across all six types
the extraction minted **1,710 operators**, every question's pass wrote posteriors, and **not one
question** landed in `inert_questions`. The three single-session rows — the population the claim
names — are 12/12 with EP.

**Not falsified, and now the actual residue:** the belief layer participates on every question but on
**~10% of the points**. `points_with_ep / (points_with_ep + points_unmeasured)` = 2,696 / 27,608 =
**9.77%**, and only **185 of 27,608 points (0.67%)** were moved off the neutral mean by the pass.
So a question-level ratio of 1.00 licenses far less than "belief-activated" reads: the answer on
every one of these questions was produced mostly by retrieval + reader, with the epistemic layer
touching a tenth of the graph at most.

Two honest limits on that 10%:

1. It is a **floor**, not a claim-coverage figure. `_COUNT_POINTS_TOTAL` counts every non-operator
   `Point`, and the v2 ingest also writes *episodic turn points* and *session-transcript chunks*,
   which are not claims and can never carry EP state. A claim-only denominator does not exist as a
   cell today.
2. `factor_edges = 0` while `operators = 1,710`: operators are minted, but none is a **direct
   claim↔claim IMPL/NAND**. Which point pairs the operators do connect is not measured here.

## Decision this implies (the reason it is not a code change)

- **Not `ARMING`.** The graph is not EP-inert: with `--activate-beliefs` the pass promotes and writes
  posteriors on 24/24. `ingest_v2`'s `status="draft"` (`:284`, `:618`, `:644`) and
  `promote_source=False` (`:425`) are **deliberate recorded postures** — drafts are not evidence
  (`tortoise/ep.py:1202` `include_draft: bool = False`, `:1275` `_live_only`; #2598). Flipping them
  would reverse a recorded decision **and is unnecessary**: `activate_beliefs` is the sanctioned
  activation path and it works. It would also change nothing about the 90% of points that are not
  reachable from any factor.
- **Not `SAMPLING` of the question population.** On this sample there is no zero-operator population
  to sample.
- **The residue is within-question coverage**, and it is a *reporting* risk, not an arming bug:
  `questions_with_ep / questions` can read 1.00 at 9.77% claim coverage. The cells that expose it
  already exist and are published beside the ratio (`points_with_ep`, `points_unmeasured`,
  `points_off_neutral`, `points_moved_off_neutral`). Indicator (a) is satisfied by publishing the
  ratio **with** those cells — which is what this artifact does.

**Disclosure of what this artifact cannot re-derive:** `census.json` carries the run totals and the
per-question cells but not the per-question graphs, and the corpus cache lives outside the repo — so
the cells are auditable against the committed sample identity (`census.json.completed`: 24
`question_id` + `question_type` pairs) and reproducible by re-running the two probes, not
recomputable from the artifact alone.

- The issue's **open owner decision** ("should `activated` additionally require contribution?") gains
  data: every question here has `questions_off_neutral == 1` and `points_moved_off_neutral > 0`, so
  on this sample the stricter bar would not change any question's state. It would still be a re-cut
  of #8486's recorded disclosure bar, so it stays an owner decision — not taken here.

## Reproduce

```bash
export TORTOISE_LME_CACHE_DIR=~/.cache/tortoise-longmemeval
# 1. pre-registered stratified sample (N fixed before the run; committed selector)
PYTHONPATH=. python docs/evidence/10137-corpus-ep-census/probes/build_slice.py 24 --out /tmp/10137/run
# 2. the run (v2 ingest + belief activation; reader/judge mocked, dense leg waived)
PYTHONPATH=. python -m tools.longmem_eval.run \
  --data /tmp/10137/run/pilot_stratified_24.json \
  --ingest-mode v2 --activate-beliefs --mock --skip-preflight \
  --workers 4 --session-workers 2 \
  --checkpoint /tmp/10137/run/ckpt.json --output /tmp/10137/run/report.json
# 3. the cells — the runner's OWN derivation (measurement_posture.summarize_belief_activation)
PYTHONPATH=. python docs/evidence/10137-corpus-ep-census/probes/census.py /tmp/10137/run/ckpt.json
```

Measured 2026-10-31 on `main@eac49c0dd9aababd01016325a34a327481dd006c`.

### Provenance note (why the cells come from a checkpoint, not a report)

All 24 questions completed (`failed = 0`), then the runner's watchdog aborted the *finalisation*
(`RUN ABORTED — degraded_aborted: leg_dead`) on a box at load 10–55, so no `*.report.json` was
written. The census is therefore derived from the run's own checkpoint via the runner's own
aggregation function — the same code path the report uses (`run.py:5693`) — not a second
implementation. `probes/census.py` is that call, and it reads either shape.

### The per-question block, on the REPORT path (the shape a finalised run writes)

The checkpoint write has always carried each question's belief manifest
(`run.py:5115-5116`), which is why the cells above could be audited row by row. The **report's**
outcome projection is an explicit whitelist and did **not** carry it, so reading a report gave a
correct aggregate (`methodology.measurement_posture.activation`) above **192 of 192 null**
per-question cells — the ordinary path a future run takes was the one that lost the column, and a
null `ep_ran` / `points_with_ep` in such a block is indistinguishable from a measured one
(#10137 review, P2). Two changes close it:

- `outcomes_to_report` now projects `belief_activation` **conditionally** — under the arm only,
  exactly like the checkpoint write and the `#2578` / `#2976` arms. An UNARMED outcome gains no
  key, so its published shape is unchanged (the golden projection in
  `tests/test_longmem_runner.py` still matches key for key — that pin proves the *absence* case
  only). The **presence** case is pinned by
  `tests/test_longmem_report_usage.py::test_projection_emits_belief_activation_conditionally`,
  which is the pin this arm was missing until #10137 review: without it the whole change could be
  reverted with every test still green.
- `probes/census.py` **fails closed** on an input that carries no manifest at all: it emits
  `per_question: null` with `per_question_source` naming why, instead of one null row per
  question — including the empty-population boundary, where an input with *no* outcomes must not
  claim a source it never read. A *partial* population still publishes its rows, and
  `per_question_unread` says how many questions inside them were never measured — the same
  disclosure the summed cells already make with `<key>_unread`, and the `*_unread` companions of
  `points_with_ep` / `points_unmeasured` / `points_off_neutral` / `points_moved_off_neutral` now
  ride `cells` too. These branches are pinned by
  `tests/longmem_eval/test_ep_census_10137.py::test_census_probe_*`.

`census.json` below was written by the *checkpoint* path, so it carries `per_question_source:
"outcome.belief_activation"` and `per_question_unread: 0` — measured on every one of the 24
questions, which is verifiable from the rows themselves (no `null` in any of the seven COUNT cells
across the 24 questions; the eighth cell, `no_op_reason`, is `null` in all 24 because no question
no-op'd — that null is the measured value, not a missing one).
