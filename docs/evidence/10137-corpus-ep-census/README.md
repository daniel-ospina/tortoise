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
| `questions_in_run` / `questions_failed` | 24 / **0** (the denominator the ratio covers is `questions`; a crashed run makes these three disagree, and the gap IS the disclosure) |
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
| `point_coverage` | `2,696 / 27,608 = 0.0977` — a **floor** on claim coverage (see the two limits below), never quoted as claim coverage |
| `points_substrate` / `belief_eligible_total` | **UNKNOWN** (`null`, `_unread: 24`) — this run predates #10493, so no question measured the eligibility population. Not zero: the run never read it |
| `belief_eligible_with_ep` / `belief_eligible_unmeasured` | **UNKNOWN** (`null`, `_unread: 24`) — same reason |
| `belief_eligible_ratio` | **UNKNOWN** (`null`) — withheld, because a ratio over a population nobody read is the fabricated figure the `_unread` companions exist to prevent |

> **`questions_with_ep_ratio` now has a producer in the RUNNER (2026-11-09) — indicator (a).** In this
> artifact the headline quotient was derived by `probes/census.py` alone, so the number the issue exists
> to publish had no producer on the run-report path: a report carried the numerator and the denominator
> and left every consumer to divide, which is two spellings of one cell waiting to disagree.
> `measurement_posture.summarize_belief_activation` now publishes `questions_with_ep_ratio` beside the
> two cells it divides, gated exactly like its sibling ratios — `None` (UNKNOWN) over an empty
> population, never `0.0`, because zero questions has not measured a zero rate. Every run report's
> `methodology.measurement_posture.activation` carries it; the probe READS that cell instead of dividing
> again (falling back to the same arithmetic for an input whose summary predates it), and the pin below
> divides the two cells independently. The committed value is unchanged: `24 / 24 = 1.00`.

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
   which are not claims and can never carry EP state. The probe therefore publishes the ratio as
   **`point_coverage`**, never `claim_coverage`: a key named for a denominator nobody measured is how
   9.77% gets quoted as "claim coverage" one lane later (#10137 review, P2).

   ⚠️ **ERRATUM (2026-11-03).** This item used to end *"a claim-only denominator does not exist as a
   cell today"*. That is **no longer true**. It was true when written (2026-10-31, `7adf5cea9` —
   neither `points_substrate` nor `belief_eligible_*` existed in the tree) and stopped being true
   ~6 h later, when #10493 (`d2c3b08b1`, merged 2026-11-01) added `points_substrate` /
   `belief_eligible_total` / `_with_ep` / `_unmeasured` to every per-question manifest
   (`tools/longmem_eval/ep_activation.py`) and `belief_eligible_ratio` to the posture summary
   (`tools/longmem_eval/measurement_posture.py`).
   The belief-eligible denominator exists. What did **not** carry it was this bundle's probe — its
   `cells` block is an allow-list, and the new cells were never added to it (#10493's own *residual
   1*, closed 2026-11-03). `census.json` now carries the nine keys as **UNKNOWN**: the five value
   keys (`points_substrate`, `belief_eligible_total`, `belief_eligible_with_ep`,
   `belief_eligible_unmeasured`, `belief_eligible_ratio`) are `null`, and the four `_unread`
   companions are `24` — because the 24-question run predates #10493 and its
   manifests never measured them — **not zero**,
   and not obtainable by re-probing this run: the cells are read off the manifests, and these
   manifests never carried them. The next **post-#10493** run through `probes/census.py` emits the
   real values per question. The floor beside it is unchanged and still published: the two ratios
   answer different questions (whole-graph coverage vs coverage of the population that can actually
   carry a belief).
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
  `questions_with_ep / questions` can read 1.00 at 9.77% point coverage. The cells that expose it
  already exist and are published beside the ratio (`points_with_ep`, `points_unmeasured`,
  `points_off_neutral`, `points_moved_off_neutral`). Indicator (a) is satisfied by publishing the
  ratio **with** those cells — which is what this artifact does.

**Errata discipline.** `census.json` is a run's artifact, so its measured cells are never edited. The
nine `points_substrate` / `belief_eligible_*` keys were APPENDED on 2026-11-03 with values **derived
by the repo's own aggregator** (`measurement_posture.summarize_belief_activation`) over this
artifact's committed `per_question` rows — which is why the value keys read `null` while their
`_unread` companions read `24`: that run never measured the eligibility population, so neither side
carries a number. No pre-existing cell was touched,
and `test_committed_census_rederives_its_own_aggregate` re-derives all 28 from the committed rows.

**Disclosure of what this artifact can and cannot re-derive:** `census.json` carries the run totals
and the per-question cells but not the per-question graphs, and the corpus cache lives outside the
repo. The **aggregate `cells` ARE re-derived from the committed `per_question` rows** —
`tests/longmem_eval/test_ep_census_10137.py::test_committed_census_rederives_its_own_aggregate` pins
them, along with the `questions_with_ep_ratio` quotient, `errors` and the per-type bucket cells, plus
the committed sample identity (`census.json.completed`: 24 `question_id` + `question_type` pairs).
Residually un-derived, and declared as such in the pin's own docstring: the `questions_in_run` /
`questions_failed` pair, and the per-row shape identity. `source` is a third case of a different kind —
asserted as an artifact-identity anchor rather than derived, so a hand-edit reds, but it is an
assertion about provenance, not a re-derivation. What the artifact **cannot** do is
re-derive the per-question **values** — those need the graphs, so they are reproducible only by
re-running the two probes. The pin is therefore internal consistency, and the measurement's TRUTH
still needs that re-run. ⚠️ It is registered in the NON-GATING `on_demand` eval lane
(`config/ci-surfaces.yml`, #10194/#10206): it reds a hand-edit when it runs, it does not block a
merge.

- The issue's **open owner decision** ("should `activated` additionally require contribution?") gains
  data: all 24 questions are themselves counted in `cells.questions_off_neutral` (24, i.e. 4 in each
  of the six question types) and every one of the 24 rows carries `points_moved_off_neutral > 0`, so
  on this sample the stricter bar would not change any question's state. The two are different units:
  `questions_off_neutral` counts QUESTIONS while `points_moved_off_neutral` counts POINTS, so neither
  can be read as a per-question value of the other. It would still be a re-cut
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

> **⚠️ These cells PREDATE the operator-promotion fix (#10137).** `activate_beliefs`
> used to promote only operators incident to a Point promoted **in the same pass**
> ("`c.id IN <draft ids>`"), so a draft operator whose endpoints were *already*
> live was promoted by nothing and stayed out of the factor graph for ever. That
> fix landed after this measurement, and these numbers were NOT re-measured.
>
> They should nevertheless be unchanged, and the reason is worth stating because
> it is also the reason this sample could not have found the bug: this run was a
> **fresh single-pass ingest per question**, so every claim was `draft` before
> phase 1a promoted it, and every operator the ingest minted was incident to one
> of those claims. On that shape the two candidate sets coincide. What *would*
> move the numbers is an operator minted over an **already-live** claim — i.e. a
> **resumed / partial ingest**, which this pre-registered fresh sample contains
> none of (`ingest_v2` skips a point it already wrote but still writes the
> operator over it). Note what the old incident query could NOT reach, because it
> is narrower than "no `:Point` target": that query matched
> `(o)-[:IMPL|NAND]->(c:Point)`, and the source is written as an outgoing edge too
> (`idx:0`), so a promoted draft SOURCE reached it exactly as a target did — but
> a part/whole (`composedOf`/`decomposesInto`/…) operator has `:Point` endpoints
> reachable through NO such edge, its edges being `hasPart` (`sdk.py:22396`),
> so the old query could not reach it in any pass, and the new sweep promotes it
> (the engine's GATE would admit it too, but the engine has no graph-wide sweep,
> so here the sweep is the only route). The same goes for an operator whose
> endpoint is a `:Subject`/`:Object`/`:Event` rather than a `:Point`: the deleted pattern
> required `(c:Point)` and the new read is label-wide (#7853), so that shape is
> newly reachable too — though there the sweep can be the ONLY promotion route,
> since `promote_point` cannot promote a non-`:Point` at all. `ingest_v2` mints
> none of these — its operator loop keeps `op_type` to `IMPL`/`NAND` and every
> endpoint is a point it wrote (`ingest_v2.py:401-404`) — so this sample contains
> neither shape, nor the resumed-ingest shape. Treat these cells as pre-fix
> values until a re-cut says otherwise.

The cells' line citations carry a scope note of their own.

> **Line citations below are into the MEASUREMENT commit** —
> `main@eac49c0dd9aababd01016325a34a327481dd006c`, the tree this run was taken
> on (stated at the end of the Reproduce block). `tools/longmem_eval/run.py` has
> drifted since, so a `run.py:<line>` here may point elsewhere on today's `main`
> while being exactly right for the run it documents. Re-resolve by SYMBOL
> (`run.py::outcomes_to_report`, `…::_activate_beliefs_for_question`) before
> treating a mismatch as a defect — and do not "fix" these numbers against a
> tree the measurement was never taken on.

### Provenance note (why the cells come from a checkpoint, not a report)

All 24 questions completed (`failed = 0`), then the runner's watchdog aborted the *finalisation*
(`RUN ABORTED — degraded_aborted: leg_dead`) on a box at load 10–55, so no `*.report.json` was
written. The census is therefore derived from the run's own checkpoint via the runner's own
aggregation function — the same code path the report uses (`run.py`'s call to
`measurement_posture.summarize_belief_activation`) — not a second implementation.
`probes/census.py` is that call, and it reads either shape.

### The per-question block, on the REPORT path (the shape a finalised run writes)

The checkpoint write has always carried each question's belief manifest (`run.py`, where the
checkpoint row spreads `belief_activation` in when it is not `None`), which is why the cells above
could be audited row by row. The **report's** outcome projection is an explicit whitelist and did
**not** carry it, so reading a report gave a
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
  `per_question_unread` says how many questions inside them have an **unread** census — the same
  rule, and the same predicate (`measurement_posture._UNKNOWN_PER_MANIFEST_KEYS`, coerced through
  the module's own `_optional_int`, which refuses a `bool` or any non-numeric cell), the four
  pre-#10493 summed cells use for `<key>_unread`. It does **not** cover the four `points_substrate` /
  `belief_eligible_*` sums: #10493 keeps those in their own `_UNKNOWN_PER_MANIFEST_KEYS_10137` tuple
  on purpose, so that adding a cell can never retroactively mark a pre-#10493 checkpoint's measured
  census as unread. The two disclosures therefore CAN disagree, and on this artifact they do —
  `per_question_unread: 0` beside `points_substrate_unread: 24` — because each is true of its own
  key set: every question's four pre-#10493 cells were read, and no question read the eligibility
  population. Reading one as the other's verdict is the misreading the separate tuple prevents
  (#10137 review, P2/P3: a manifest PRESENT is not a census READ — the producer's fail-open shape
  `{"ep_ran": false, "error": …}` carries a manifest and no cells, and used to count as read; and
  `operators: true` is not a count).
  Each row also carries that manifest's `error`, so the reason a census is missing is readable per
  question instead of inferred from nulls.

  ⛔ **The failures behind the ratio are read, never left implicit** (#10137 review, P2).
  `summarize_belief_activation` only ever sees `outcomes`, so a checkpoint from a *crashed* run —
  the normal case, and the case this very bundle came from — would publish
  `questions_with_ep / questions = N/N = 1.00` over its survivors with the ablation invisible. The
  probe therefore reads the input's own failure list (`run.py` writes the `failures` key into the
  checkpoint; a report carries both the list and `n_failed`, `report.py:2792-2798`) and publishes
  `questions_failed` beside `questions_in_run` — **the ratio's own denominator plus the questions it
  excludes**, which is what makes `questions_in_run − questions == questions_failed` hold by
  construction rather than by luck. The failure ids are counted as **distinct ids over both lists**,
  never `len(outcomes) + len(failures)`: a qid completed by one worker and failed by another lands in
  both (`report.py:1407-1410`), and summing the lengths published a 2-question population as a
  3-question run. And the total is never a *second* count of the population: the runner's own
  denominator counts rows while the ids are distinct, so a duplicate-`question_id` input published a
  total smaller than the denominator it described. On a clean run all three agree; on a crash the
  gap is the disclosure. A question that
  failed and then produced an outcome counts once, as an outcome — it was recovered, and it is in
  the ratio's denominator. ⛔ The report arm does **not** take its total from `n_questions`: that is
  `len(outcomes)` (`report.py:1106`), so overriding with it silently dropped the failure population
  in the arm that publishes the ordinary artifact. This artifact: `questions_in_run = 24`,
  `questions_failed = 0`. These branches are pinned by
  `tests/longmem_eval/test_ep_census_10137.py::test_census_probe_*`.

`census.json` below was written by the *checkpoint* path, so it carries `per_question_source:
"outcome.belief_activation"` and `per_question_unread: 0` — every one of the 24 questions produced an
outcome, which is verifiable from the rows themselves. The rows carry **thirteen** value cells, and
"measured" is a per-cell question, not a per-row one:

- the **seven COUNT cells** (`operators`, `factor_edges`, `points_with_ep`, `points_unmeasured`,
  `points_off_neutral`, `points_moved_off_neutral`, `ep_ran`) are non-`null` in all 24 rows;
- `no_op_reason` is `null` in all 24 because no question no-op'd — that null is the measured value,
  not a missing one, and `error` is `null` for the same reason;
- the **four `points_substrate` / `belief_eligible_*` cells are `null` in all 24 rows, and those
  nulls are NOT measured** — this run predates #10493, so no question ever read the eligibility
  population. Each carries its `_unread: 24` companion, and that companion is the only thing
  separating "we looked and the answer was nothing" from "nobody looked"; `belief_eligible_ratio`
  is therefore withheld rather than published over an unread denominator. See the cell table
  above, `cells.belief_eligible_total` and `census.json`'s own rows.
