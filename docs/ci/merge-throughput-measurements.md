---
title: "Merge-throughput measurements — M3/M4/M5/M6 (#5215, plan Task 3)"
type: measurement
domain: operations
doc_status: live
created: 2026-09-27
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# Merge-throughput measurements — M3/M4/M5/M6

**Plan:** `docs/plans/2026-09-26-5215-merge-throughput.md` §5 (Measurement plan) and §10
**Task 3** (this lane). **Epic:** #5215.

## 0. Provenance, and how to reproduce

| field | value |
|---|---|
| capture timestamp (UTC) | M3/M5/M6 record `end` = `2026-09-27T10:48:28Z`; M4 record `end` = `2026-09-27T10:49:51Z` |
| resolved `origin/main` | `74a88cf8382dd4fef08c25bbeba49baa7f05d3f6` — from `git ls-remote origin refs/heads/main` at capture, recorded per record as `window.main_sha` with `main_sha_source` |
| window (M3/M5/M6) | 8 h |
| window (M4) | 14 d |
| corpus | 40 000 workflow runs (`2026-08-28` → `2026-09-27`) via `gh api /actions/runs --paginate`; 8 h window: 358 queue runs / 30 queue branches; 14 d window: 1 268 queue runs / 111 queue branches |
| instrument | `tools/merge_throughput.py` @ #5705 (`74f9f1eaa`) — **owns every exit code**; this lane supplies **records**, it does not judge |
| observer | `tools/queue_window_observe.py` (added by this lane; read-only, stdlib-only) |
| committed records | `docs/ci/merge-throughput-m3-m6-records.json` (8 h), `docs/ci/merge-throughput-m4-capacity-records.json` (14 d) |

**Why there is an observer.** The instrument's `--watch-queue` / `--observe-capacity` take a **SINGLE**
sample (`_capacity_sample()` → one `{queued, in_progress}` dict), while M3/M5/M6 are defined over
**timestamped windows** (plan §5, M3: *"Derive from timestamped queue check-run / branch
create-delete events rather than only 60 s polling"*). This is a Task 1 / Task 3 **contract gap** (F1),
recorded rather than worked around silently: the observer only *observes*. `merge_throughput.py` still
owns polarity, thresholds and exit codes; the observer does not import or re-implement it.

**UNKNOWN policy (plan §5).** A non-zero exit, an empty output, a partial read, or a value that cannot
be localised to a moment is recorded as `UNKNOWN` — **never `0`**, never a guess. "Max observed" alone
is never the effective value. A read with an unparseable line, or a window containing no runs, produces
`{"status": "UNKNOWN"}` and exit 2 — there is **no silent widening** of the window and no silent
truncation of the corpus.

**Reproduce:**

```bash
gh api repos/daniel-ospina/tortoise/actions/runs?per_page=100 --paginate \
    --jq '.workflow_runs[]' > runs.jsonl
python3 tools/merge_throughput.py --json .conflicts > conflicts.json
python3 tools/queue_window_observe.py --from-json runs.jsonl --window-hours 8 \
    --confirm-refs --conflicts-json conflicts.json \
    --out docs/ci/merge-throughput-m3-m6-records.json
python3 tools/queue_window_observe.py --from-json runs.jsonl --window-hours 336 \
    --conflicts-json conflicts.json \
    --out docs/ci/merge-throughput-m4-capacity-records.json
# then, with the instrument present (#5705), the exact S-criteria flags:
python3 tools/merge_throughput.py check batch-size --min 2 --min-depth 1 --require-fresh \
    --input docs/ci/merge-throughput-m3-m6-records.json              # -> 0
python3 tools/merge_throughput.py check parallelism-headroom --require-fresh \
    --input docs/ci/merge-throughput-m4-capacity-records.json        # -> 2
python3 tools/merge_throughput.py check capacity --max-oldest-minutes 120 --min-headroom 1 \
    --require-complete --require-fresh \
    --input docs/ci/merge-throughput-m4-capacity-records.json        # -> 2
```

## M2 — cycle-time attribution

Owned by **Task 2**, which has not run. This section is a deliberate placeholder, not an omission: the
document is created here because Task 3 must record measurements and Task 2's doc did not exist (F9).
**M2 is UNKNOWN.** Its 38 m / 28 m split remains unexplained (plan ⟨C2⟩).

## M3 — effective `max_parallel_checks`

| reading | value | basis |
|---|---|---|
| **configured** | **5** | `configured_source = "documented-default (not set in .mergify.yml)"` |
| **effective** | **5** | largest wave size observed in **≥ 2 waves** |
| wave sizes, 8 h | `{5: 6}` | 5 branches form together, six times |
| wave sizes, 14 d | `{1: 9, 2: 1, 5: 15, 10: 1, 15: 1}` | 5 recurs 15×; the 10 and 15 are **one-offs** |
| max observed batches | 5 (8 h) / 15 (14 d) | one-off bursts — never the effective value |
| live queue refs at observation | **5** | momentary, recorded as corroboration only |

**Verdict: effective `max_parallel_checks` = 5 — the documented default, now measured.** The plan's
⟨C1⟩ is confirmed and strengthened: `#5527` setting `max_parallel_checks: 3` is a **reduction from an
effective 5, not a 3× gain** (F3). Any claim that it is a gain must be withdrawn.

**A max-observed reading would have been wrong.** The 14-day window contains a one-off **15**-branch
wave and a one-off **10**; the 8-hour window's 5-minute concurrency sweep also sees **bisection
re-runs** (`batches.bisection_singles` = 5) layered on top of the speculative wave. The recurrence rule
("seen in ≥ 2 waves") excludes all of them. This is the error M3 exists to prevent, and it is pinned by
a **discriminating test** (`test_record_effective_parallelism_ignores_a_one_off_larger_wave`: a 7-wave
plus two 5-waves must yield `effective == 5` with `max_observed_batches == 7`).

**Sampling caveat (stated, per M3).** Wave grouping is bounded to a 360 s formation gap, so two
genuinely separate waves closer than that merge into one; per-ref polling would instead alias
short-lived branches **downward**. The value is derived from queue-branch create timestamps and per-run
start/end timestamps, not from a poll. Bisections are removed **before** waves are clustered, so a
size-1 re-run cannot inflate a wave.

## M4 — runner capacity and headroom

| reading | 8 h window | 14 d window |
|---|---|---|
| max concurrent **queued** runs | 2 | 32 |
| max concurrent **in-progress** runs | 38 | 125 |
| max concurrent **queued + in-progress** | 39 | 139 |
| max **oldest-queued** age (`oldest_minutes`) | **181.9 min** | 12 693.5 min (a stale entry; not a steady state) |
| max per-run queue wait (`max_queue_wait_minutes`) | 181.9 min | 12 693.5 min |
| runs **delayed** ≥ 60 min | 1 | 174 |
| **`capacity_at_first_failure`** | **UNKNOWN** | **UNKNOWN** |
| **`headroom`** | **UNKNOWN** | **UNKNOWN** |

**`capacity_at_first_failure` = UNKNOWN, deliberately — 8 h because no sample exists, 14 d because the
one sample is not localisable.** The plan's I9 defines it as *"the concurrency at which a run
**demonstrably failed to acquire a runner**"*. The observed API signal for that is exactly one
`startup_failure` (a run whose workflow created **zero jobs**), `Python CI` run `35776476728` at
`2026-09-22T19:51:30Z`, whose `run_started_at` is two days later. Its creation and its failure are
therefore in **different windows**, so no single concurrency can be read from it; the record's
`capacity_at_first_failure_reason` states this verbatim (the 8 h record: *"no run in the window failed
to acquire a runner"*; the 14 d record: *"all 1 failed-to-start candidate(s) … unlocalizable wait …
above the 360-min stale bound"*). Long waits (63–182 min) are **delays**, not starvation: those runs
*did* acquire a runner and completed, and are recorded separately in `max_queue_wait_minutes` /
`runs_delayed_over_60min`. Counting them would manufacture a capacity number out of ordinary pressure.

**I9 therefore refuses (exit 2), and no parallelism value is authorised by this window** (F6). This is
the plan's required behaviour — an unmeasured headroom must never read as a pass.

**Complementary observation (not an I9 input).** The environment demonstrably absorbed **≥ 125–139
concurrent runs** at peak. The queue's own footprint at saturation is 5 batches × ~7 workflows ≈ **35
run-slots**. On that reading the plan's T-G premise — *runner capacity is the real ceiling* — is **not
supported in this window** (F5): the queue is nowhere near the observed runner ceiling, and what binds
is the CI load itself.

**S11's oldest-age threshold is independently failing.** `oldest_minutes` reached **181.9 min**, above
S11's `--max-oldest-minutes 120` — so even once headroom becomes measurable, capacity is still over the
plan's bound.

**Unit warning for I9 (F4).** I9 compares `configured_max_parallel_checks` (**batches**) with
`capacity_at_first_failure` (**concurrent workflow runs**) — **different units**, so the predicate's
verdict flips on normalisation. The record carries `capacity_at_first_failure`,
`capacity_at_first_failure_batches`, `headroom` and `headroom_batches`, and its
`headroom_reason` names the mismatch.

## M5 — why batches are size 1

**In the fresh window they are not. The premise holds only for a 9 % historical residue.**

| evidence | 8 h | 14 d |
|---|---|---|
| batch formations (bisections excluded) | **30** | **111** |
| — of size 2 | **30** | **101** |
| — genuinely size 1 | **0** | **10** |
| bisection re-runs (excluded from formations) | 5 | 15 |
| `formation_size_distribution` | `{2: 30}` | `{1: 10, 2: 101}` |
| conflicted set, same window | 166 enumerated, **21 conflicting**, 1 unknown | same read |

**The fresh window has ZERO single-PR formations.** Every one of the 30 formations in the last 8 h is a
pair. The size-1 branches are **bisection re-runs** after a red — `#5356 + #5343` formed at 06:37Z, then
`#5356` alone at 07:11Z and `#5343` alone at 07:58Z. The plan's own cite (`Merge of #5397` 21:44Z) is a
**merge** event, which is exactly what a two-PR batch produces *after* a bisection. The regime also
changed: every formation up to `2026-09-25T22:41Z` was size 1, and every one from `2026-09-26T12:01Z`
on is a pair, so a baseline captured before that switch is stale.

**The 14-day residue is 10 of 111 (9 %) genuine singles** — a real but small minority, not "batch size
is 1". Those are PRs that were conflict-capped or demand-starved individually; they are the honest
remainder, and they are why the E1b decision is stated as *"pairing works"*, not *"pairing is
universal"*.

**The three confounders the plan named are separated:**

1. **`batch_max_wait_time: 5 min`** — **not** capping. 5-branch waves form in 1–2 min, well inside the
   window.
2. **Demand starvation** — **not** the modal cause. A 5-wave pairs 10 distinct PRs simultaneously.
3. **Conflicts** — real but not the modal cap: **21 of 166** PRs conflict (12.7 %), while paired
   formations dominate.

**Decision unblocked:** **E1b (`batch_size: 2 → 4`) is not gated on a pairing failure.** M5's blocking
role (plan §4.3) is discharged: the remaining question for E1b is the **latency / red-rate** one
(⟨C3⟩), not "why don't batches pair".

## M6 — speculative-invalidation waste

| reading | 8 h | 14 d |
|---|---|---|
| waves | 6 | 27 |
| batch waves (size ≥ 2) | 6 | 18 |
| red batch waves | **5** | 12 |
| unobserved wave heads (`red: null`) | 1 | 1 |
| **discarded speculative batches** | **20** | **48** |
| waste ratio (per batch wave) | 3.33 | 2.67 |
| `Python CI` on queue heads | **`{failure: 30}` — 0 success** | `{failure: 91, success: 30}` |

**The ⟨C3⟩ term-iii waste is real and at its maximum.** In serial mode a red invalidates the speculative
batches above it, so a red head of an N-branch wave wastes **N − 1** validations. Measured: each red
5-wave discards **4** speculative batches — the `max_parallel_checks − 1` term the plan said to measure
rather than assume. **20 speculative batch validations were discarded in 8 h.**

A wave head whose heavy leg was never observed is recorded as `red: null` and **excluded** from the red
count, so its waste is a **floor**, not a verified zero — an unobserved head must not read as "clean".

**The cause is not the queue.** Over the fresh 8 h **every** `Python CI` run on a queue head failed
(30/30). Main's required gate reads `exit 2` under S1 (see below). So the queue is not draining slowly
because of its own configuration — **the heavy leg is red on the tree that would land**, which is ⟨C4⟩
/ #5597, and it makes the speculative machinery pure waste until it is fixed. **This reorders the plan's
own lever list: no P2 throughput work pays while the batch head cannot pass.**

## Records consumed by the instrument (exit codes)

Run with the instrument at `74f9f1eaa`; each command was executed and its code recorded.

| criterion | command | result |
|---|---|---|
| **S6** | `check batch-size --min 2 --min-depth 1 --require-fresh --input docs/ci/merge-throughput-m3-m6-records.json` | **0** (30 events, all size 2) |
| **S14** | `check parallelism-headroom --require-fresh --input docs/ci/merge-throughput-m4-capacity-records.json` | **2** — `2: capacity_at_first_failure is UNKNOWN (refuse, never pass)` (I9) |
| **S11** | `check capacity --max-oldest-minutes 120 --min-headroom 1 --require-complete --require-fresh --input docs/ci/merge-throughput-m4-capacity-records.json` | **2** — `2: capacity field 'capacity_at_first_failure' is missing or not numeric` |
| **S1** | `check main-gate --strict --require-fresh` | **2** — all six required contexts `NO_MAIN_SIGNAL` |

Both `capacity` and `parallelism-headroom` exit **2**, not `1`: the instrument's field-presence check
precedes the threshold check, so UNKNOWN dominates a threshold miss. I9's refusal must never read as
"merely below threshold".

**S1's six-context reading, recorded precisely.** At main `74a88cf83…`, `check-runs?filter=all` returns
**zero** check-runs for **all six** required names, so the strict read is `NO_MAIN_SIGNAL` for each and
the criterion is `2`. Five of those are D13's permanent gap (the `pull_request`-only contexts);
`python-ci-gate` **is** push-triggered (`python-ci.yml: push:[main]`), so its absence at a
just-advanced main is a **push run not yet completed**, not a never-emitted signal — a distinction the
instrument cannot make and this record therefore states (F8).

## Findings that need a decision or a fix (reported, not silently fixed)

| # | finding | owner / route |
|---|---|---|
| **F1** | `--watch-queue` / `--observe-capacity` are single-sample: the plan's M3/M5/M6 cannot be produced by the instrument as delivered | Task 1 lane (#5705) |
| **F2** | The plan's "observed batch size is 1" baseline conflates **bisection re-runs** and **merge events** with **batch formations**: the fresh window has 30/30 pairs and **zero** single formations; the 14 d residue is 10/111 | plan §1.1 / §5 M5 |
| **F3** | `#5527`'s `max_parallel_checks: 3` is a **reduction from an effective 5** (⟨C1⟩ confirmed) | #5527, before it is described as a gain |
| **F4** | I9 compares `capacity_at_first_failure` (**runs**) with `configured_max_parallel_checks` (**batches**) — **different units**, so the predicate's verdict flips on normalisation | plan §3 I9 / Task 4b's guard |
| **F5** | Runner capacity is **not** demonstrated to be the binding ceiling (peak 125–139 concurrent runs absorbed; queue footprint ≈ 35 run-slots) | plan T-G / D4 |
| **F6** | No localisable `capacity_at_first_failure` sample exists in a 14-day window ⇒ I9 refuses; "headroom" stays UNKNOWN until a genuine acquisition failure is observed | D4 (trigger-only, still gated) |
| **F7** | S11/S14 carry `--require-complete`, but a capacity record is a set of **scalar** measurements with no `items`/`total_count`, so `parallelism-headroom --require-complete` exits 2 for a **shape** reason rather than I9's refusal — the criterion cannot distinguish them | plan §11 S11/S14 |
| **F8** | S1 cannot distinguish a required context that **never** emits on main (D13's five) from one whose push run **has not completed** (`python-ci-gate`); both read `NO_MAIN_SIGNAL` | plan §11 S1 / D13 |
| **F9** | Plan Task 3's Files line says *"modify the measurement doc (created by Task 2 — it must land first)"*; **Task 2 has not run**, so this lane **created** the doc and left M2 as an explicit UNKNOWN placeholder | plan §10 Task 3 / §4.1 wave map |
