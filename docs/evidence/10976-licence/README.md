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
the graph and exits 0 when the licence is refused, **4** when one is issued (not 2 —
argparse uses 2 for a usage error, which must not read as the defect arm).

## The measurement (falkordb 4.20.6, private lane container, `noeviction`)

`before.log` / `after.log` in this directory are the captured runs; the two differ only
in whether `assess()` applies the population bound.

| | before (no population bound) | after |
|---|---|---|
| graph nodes (`MATCH (n) RETURN count(n)`) | 25,000 | 25,000 |
| `turn` population (`is_episodic = true`) | **500** | **500** |
| report claims (CLEAN, `proves=True`) | 20,000 | 20,000 |
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

- **The predicate has one owner of the RECORD, and four textual carriers.**
  `tools/turn_parity_census.py::_GRAPH_EPISODIC_COUNT_CYPHER` owns the turn population's
  shape (`#3551`); `tools/purge_legacy_turns.py` (the delete), `tools/backfill_graph_turns.py`
  and `tortoise/projection/__init__.py::_episodic_point_ids` carry the same predicate for
  their own purposes. The census's statement is a mirror of the instrument's — the one
  whose report sizes the licence — and
  `tests/test_cache_eviction_census.py::test_the_population_count_mirrors_the_instrument`
  compares the two EXECUTED constants (not source text), so a rename, a removal or a
  changed predicate reds.
- **Mirror it exactly.** `is_episodic` is stamped on `:Source`/`:Object` nodes too
  (`graph-scripts/backfill_is_episodic.py`), so dropping the `:Point` half would *inflate*
  the ceiling and re-open this hole from the other side.
- **The whole-graph check stays**, as the second, looser guard: a bad mirror can
  over-count, so a claim above the graph's entire node count is still refused.
- **A population with no count statement is a REFUSAL**, never a defaulted bound.

## Tests

`tests/test_cache_eviction_census.py` (112 tests in the file, 218 across the four
eviction suites — the counts are the final run recorded in `WORKTREE-REPORT.md`):

| arm | what it pins |
|---|---|
| `test_a_stale_report_cannot_size_a_sweep_beyond_its_own_population` | **RED without the fix** (`PLAN`, licence 20000, 40x) → `REFUSED`, both numbers in the reason, no mutating statement |
| `test_an_honest_report_of_the_same_population_still_licenses_it` | the same graph with a 500/500 report still `PLAN` with a 500-node licence — a refusal-only suite cannot tell "the bound works" from "the bound is shut" |
| `test_a_report_claiming_fewer_nodes_than_the_population_still_licenses_that_smaller_cap` | the bound is an upper bound, not equality — a conservative report keeps its smaller cap |
| `test_the_population_count_mirrors_the_instrument` | the mirror reds when the instrument's predicate drifts |
| `test_every_registered_population_has_a_resident_count_statement` | no population can be licensed without a bound, and no bound outlives its population |
| `test_no_two_populations_share_a_resident_count_statement` | a copy-pasted statement would bound a new population by the turn count — fail-open, and invisible to the whole-graph backstop |
| `test_an_empty_population_count_read_is_a_refusal_not_zero` | an empty count read is a refusal, never `0`: "this graph holds only 0" would be a fact nobody measured |
| `test_a_population_with_no_count_statement_is_a_refusal_not_a_guess` | an unbounded population is refused, never bounded by a default (`0` demotes it forever; the whole-graph total restores the defect) |

Measured red-first: with the population bound removed, the stale-report arm fails
`assert 'PLAN' == 'REFUSED'` while the two positive arms stay green.

## ⛔ What this does NOT measure

- **It does not make the cache bounded in production.** While the record cannot serve
  (`#10593`, `#7879` open) the licence is empty and the executor issues no mutating
  statement. ⚠️ **Which gate refuses matters:** it is the executor's gate 2 (no population
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
