---
title: "#10976 — the licence's staleness bound: a stale per-population report, measured"
type: evidence
domain: platform
doc_status: live
created: 2026-11-10
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph, FalkorDB cache, cache eviction licence
aboutObjects: "#10976, #11004, #11167, #3551, tortoise/cache_eviction.py, tortoise/cache_evictor.py, tools/turn_parity_census.py, docs/architecture/STORAGE-ARCHITECTURE.md"
---

# #10976 — the licence's staleness bound

The egress census (`#11004`) licenses a sweep; the executor (`#11167`) spends that
licence as its runaway cap. This directory is the measurement for the **bound on the
licence itself** — the residual recorded on `#10976` after `#11167` merged.

## The defect, in one sentence

`assess()`'s only staleness guard compared a parity report against the graph's **whole
node total** (`node_total`, `MATCH (n) RETURN count(n)`), so a stale `turn` report
claiming 20,000 nodes passed on a graph holding 25,000 nodes in total of which only
**500** are turns — and `plan["evictable"][i]["nodes"]` (what
`tortoise/cache_evictor.py` spends as `licence_remaining`) became **40x** the population
it exists to bound.

Nothing unserved could be deleted because of it — gate 3 asks the record per node,
immediately before each delete, and that gate is unchanged. What was weakened is the
property the cap exists for: *"evicting more nodes than the instrument measured is
evicting nodes nothing proved."*

## Run it

```bash
uri="$(uv run python tools/test_lane.py uri)" && eval "$uri"   # start + export a lane
uv run python docs/evidence/10976-licence/repro_licence.py --uri "$TORTOISE_DB_URI" \
    --out /tmp/licence.json
```

The probe mints its **own** graph (`licence_probe_<rand>`), seeds **25,000** `:Point`
nodes of which **500** carry `is_episodic = true` (the `turn` population — the only
population with a parity instrument), writes a CLEAN report claiming **20,000** turns,
and runs the real `assess()` over a declared byte ceiling the instance is over. It drops
the graph and exits on the **licence**, not the verdict: **0** when no licence was issued
(the bound fired), **4** when one was (the defect is present). Not 2 — argparse uses 2 for
a usage error, which must not read as either arm — and not keyed on the verdict, because a
cache under its byte ceiling answers `CLEAN` with an empty licence.

## The measurement (falkordb 4.20.6, private lane container, `noeviction`)

`before.log` / `after.log` in this directory are the captured runs, **both produced by the
committed probe at ONE head** (`b58c26e3495c`), differing only in whether `assess()` applies
the population bound:

```bash
export TORTOISE_DB_URI=<a throwaway lane container>
uv run python docs/evidence/10976-licence/repro_licence.py --uri "$TORTOISE_DB_URI" \
    --without-population-bound --out /tmp/before.json   # exit 4 — licence issued
uv run python docs/evidence/10976-licence/repro_licence.py --uri "$TORTOISE_DB_URI" \
    --out /tmp/after.json                                # exit 0 — no licence
```

⛔ **`before.log` was previously captured from PRE-FIX code, and that made this section's
claim false once #11720 landed.** The pre-fix tree also predated #11720's `measured_at` /
`age_seconds` / `max_age_seconds`, so the old `before.log` printed a four-key licence that
no run of the committed probe could produce at this head, at the merge base, or on `main` —
and "the two differ only in whether `assess()` applies the population bound" was not true —
they differed in two ways. `--without-population-bound` re-captures the pre-fix arm at the
SAME head, so the sentence is true again. What it does: it makes the population read answer
the report's own claim, which is the state the pre-fix code was in **for this comparison**
(no demotion from that arm). It does not assert the graph holds 20,000 turns — the log
states 500 on its own line — and it leaves the freshness arms and the whole-graph check
running, both of which still pass here exactly as pre-fix. The seam prints itself into the
log (`population bound OFF (pre-fix arm)`) and into the artifact's `population_bound` field.

**This is now also the cleanest evidence that the two staleness bounds are NOT redundant.**
The report is stamped fresh, so `age_seconds` reads **0.12** — #11720's age bound does not
fire, and cannot fire. Under the population bound alone the same fresh report still
licenses **20,000** turns against a population of **500**: 40x. The sweep is what shrinks
the population, and it runs inside the age bound's own window, so "fresh" and "oversized"
are independent — which is why adopting this bound *in addition to* #11720's was argued on the
issue rather than assumed.

| | before (no population bound, #11720's age bound ACTIVE) | after |
|---|---|---|
| graph nodes (`MATCH (n) RETURN count(n)`) | 25,000 | 25,000 |
| `turn` population (`is_episodic = true`) | **500** | **500** |
| report claims (CLEAN, `proves=True`) | 20,000 | 20,000 |
| report `age_seconds` at assess time | **0.12** | 0.12 |
| **verdict** | **`PLAN`** | **`REFUSED`** |
| **`plan["evictable"]`** | `[{population: turn, nodes: 20000}]` | `[]` |
| licence ÷ population | **40x** | — |

The refusal names both numbers, so the operator does not have to re-derive why:

```text
turn (population): cannot-answer: tools/turn_parity_census.py claims 20000 turn node(s)
but this graph holds only 500 — the report is stale, or it measured a different
population, so it cannot size a sweep here
```

## The fix

`POPULATION_RESIDENT_COUNT` (population → the statement that counts *its own* resident
nodes) and `assess()`'s per-population check: a report claiming **more** nodes than its
population currently holds is demoted to `cannot-answer`. The comparison is an upper
bound, not equality — a claim **below** the population still licenses a *smaller* cap
(the safe direction), and turns that arrived since the report are growth, each still
gated individually by the executor's per-node record probe.

- **The predicate has one owner of the RECORD, and four other textual carriers.**
  `tools/turn_parity_census.py::_GRAPH_EPISODIC_COUNT_CYPHER` owns the turn population's
  shape (`#3551`). The others are `tools/purge_legacy_turns.py` (the delete),
  `tools/backfill_graph_turns.py`, `tools/backfill_copy_turns.py` and
  `tortoise/projection/__init__.py::_episodic_point_ids`. ⚠️ **Only the instrument is
  pinned, deliberately** — it is the one whose report sizes a licence; pinning the rest
  would red this bound when a statement nothing licenses reflows. The census's statement
  is a mirror of the instrument's, and
  `tests/test_cache_eviction_census.py::test_the_population_count_mirrors_the_instrument`
  compares the two EXECUTED constants (not source text), so a rename, a removal or a
  changed predicate reds.
- **Mirror it exactly.** `is_episodic` rides non-`:Point` labels too —
  `:Session`/`:Event`/`:Source` (`graph-scripts/backfill_is_episodic.py`) and `:Object`
  (`TortoiseSDK.create_entity(is_episodic=…)`) — so dropping the `:Point` half would
  *inflate* the ceiling and re-open this hole from the other side.
- **The whole-graph check stays**, with its reach stated as the mechanical trigger it is,
  in `cache_eviction.py`'s own comment: it compares the REPORT's claim against every node
  the graph holds, so it fires when `resident_population >= claim > node_total` — reachable
  from nodes REMOVED between the population read and the total read, or from a count
  statement inflated enough that the CLAIM it licenses also lands above the total
  (`test_a_claim_above_the_graph_total_is_caught_from_an_inflated_count_statement`). What
  it can never see is a **widened predicate** (a subgraph count cannot exceed the total),
  nor a claim at or below the total — whatever the multiplier
  (`test_an_inflated_ceiling_does_not_arm_the_backstop_at_the_total`). That last band is
  the residual, and it is bounded by the CLAIM, not by how badly a statement multiplies:
  `true_population < claim <= node_total`. A mirror-vs-total check
  (`resident_population > node_total`) **would** fire on a sub-band of it — an inflated
  mirror above the total with the claim under it — but it is **unsound in the other
  direction**: for an honest mirror it fires on removal skew alone, since the total is read
  AFTER every population count (a gain cannot trip it, a removal can), so it would
  false-block a legitimate report. The band is therefore covered by the mirror pin above
  plus the characterisation test that pins it.
- **A population with no count statement is a REFUSAL**, never a defaulted bound.
- **That is ONE of the two staleness bounds, and this file is evidence for this one.**
  The sibling fix that landed first (PR #11720) bounds the SAME licence by WHEN
  the report was measured — `ParityReport.measured_at` against
  `$TORTOISE_PARITY_REPORT_MAX_AGE_S` (default 3600 s). The two are independent and
  neither subsumes the other: a report stamped a minute ago still over-claims if the
  population shrank since (and eviction IS the shrink path, so the two move separately by
  design), while a report of exactly the right size still licenses a sweep when it is
  hours old. ⛔ A fixture that builds a `ParityReport` directly must now satisfy BOTH —
  declare the population's resident size in `_FakeGraph(populations=…)` and stamp
  `measured_at` — or it is demoted for a reason the test was not written to assert.

## Tests

`tests/test_cache_eviction_census.py` (135 tests in the file, 241 passed across the four
eviction suites, 0 skipped — measured, not asserted: `uv run python tools/test_lane.py run
-- tests/test_cache_eviction_census.py tests/test_cache_eviction_evictor.py
tests/test_cache_eviction_evictor_pins.py tests/test_cache_evict_cli.py -q` →
`240 passed in 6.30s`; the census file alone → `134 passed`):

| arm | what it pins |
|---|---|
| `test_a_stale_report_cannot_size_a_sweep_beyond_its_own_population` | **RED without the fix** (`PLAN`, licence 20000, 40x) → `REFUSED`, both numbers in the reason, no mutating statement |
| `test_an_honest_report_of_the_same_population_still_licenses_it` | the same graph with a 500/500 report still `PLAN` with a 500-node licence — a refusal-only suite cannot tell "the bound works" from "the bound is shut" |
| `test_a_report_claiming_fewer_nodes_than_the_population_still_licenses_that_smaller_cap` | the bound is an upper bound, not equality — a conservative report keeps its smaller cap |
| `test_the_population_count_mirrors_the_instrument` | the mirror reds when the instrument's predicate drifts |
| `test_every_registered_population_has_a_resident_count_statement` | no population can be licensed without a bound, and no bound outlives its population |
| `test_no_two_populations_share_a_resident_count_statement` | a copy-pasted statement would bound a new population by the turn count — fail-open, and invisible to the whole-graph backstop |
| `test_the_shared_statement_check_can_actually_fire` | **proves the pin above is not vacuous** — the live assertion runs over ONE population and can never fail, so this drives the same helper with a REFLOWED copy (must report the collision) and a genuinely distinct predicate (must not), which is the only demonstration the guard would fire the day a second population is registered |
| `test_an_empty_population_count_read_is_a_refusal_not_zero` | an empty count read is a refusal, never `0`: "this graph holds only 0" would be a fact nobody measured |
| `test_a_population_with_no_count_statement_is_a_refusal_not_a_guess` | an unbounded population is refused, never bounded by a default (`0` demotes it forever; the whole-graph total restores the defect) |
| `test_a_row_multiplying_count_statement_is_a_documented_residual` | **asserts what the whole-graph backstop does NOT catch** — a 500-node population counted as 2,000 with a 1,500 claim passes both guards; the band is `true_population < claim <= node_total`, bounded by the CLAIM and not by the multiplier, which is why the mirror pin is the only guard there |
| `test_a_claim_above_the_graph_total_is_caught_from_an_inflated_count_statement` | `turn` answering 30,000 on a 25,000-node graph with a 30,000 claim is refused by the **graph-total** check, population bound silent. ⛔ Proves the backstop is reachable on a QUIESCENT graph — a comment claiming it was unreachable there was wrong — and that the refusal comes from the right guard |
| `test_an_inflated_ceiling_does_not_arm_the_backstop_at_the_total` | the **boundary of the residual band**: the SAME 30,000 ceiling on the same 25,000-node graph with a 25,000 claim licenses 25,000, neither guard firing. ⛔ Pins the correction in the direction the previous comment got wrong — the multiplier is irrelevant, only the claim's position against the total matters |
| `test_the_demoted_row_still_names_its_population` | a demotion must not NULL the name the row demonstrably has — dropping the `population_name` carry through the whole-graph demotion makes the exported artefact report `population: null` beside a label that names one. The comment recorded that measured defect but nothing pinned it (deleting the carry left the suite green), so the regression it describes could return silently |

Measured red-first: with the population bound removed, the stale-report arm fails
`assert 'PLAN' == 'REFUSED'` while the two positive arms stay green.

## ⛔ What this does NOT measure

- **It does not make the cache bounded in production.** The licence is issued only where
  a parity instrument both RAN and was CLEAN over the entire population
  (`ParityReport.proves()`) **and** the report survived this bound — and nothing schedules
  that instrument: the census and the sweep are operator-invoked (the periodic sweep is
  `#11167`'s remaining step). ⚠️ **Record readiness is not what withholds it, and this
  README said otherwise for four days:** `#10593` closed **COMPLETED** on 2026-11-06 on a
  non-vacuous measurement (`applied=665 failures={} …`, 30+ min in one process), so the
  record can serve; only `#7879` (the epic) is still open. What withholds the licence is
  the absence of a CLEAN census report for the graph being swept.
  ⚠️ **Which gate refuses matters:** it is the executor's gate 2 (no population
  is proven record-served), *not* the census's verdict word — the census's verdict is its
  BYTE state, so a production cache under its byte ceiling answers `CLEAN` with
  `plan["evictable"] == []` (`tortoise/cache_evictor.py`'s header states why reading this
  as "production can only ever be `REFUSED`" makes a licence widening look harmless). This
  bound tightens a cap on a licence that is not yet issued.
- **It is not indicator (b)** — "an evicted node is served from the record". That needs
  the record, and `#11167`'s probe records its record leg as a stand-in.
- **It does not schedule anything.** The pass is operator-invoked; the periodic sweep is
  named as `#11167`'s remaining step.
- **It bounds the NUMBER, not the membership.** Which node belongs to a population is the
  selector's contract, caught per node by the executor's record probe — unchanged.
