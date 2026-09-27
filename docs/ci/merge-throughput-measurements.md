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
| capture timestamp (UTC) | `2026-09-27T10:13:10Z` |
| resolved `origin/main` | `5b6cb93673f5e4a060c1b04d96f0c09e5c777956` |
| window (M3/M5/M6) | 8 h — `2026-09-27T02:13Z` → `2026-09-27T10:13Z` |
| window (M4) | 14 d — `2026-09-13T10:13Z` → `2026-09-27T10:13Z` |
| observation corpus | 40 000 workflow runs (`2026-08-28` → `2026-09-27`) via `gh api /actions/runs --paginate`, merged-queue subset 1 268 runs / 126 queue branches |
| instrument | `tools/merge_throughput.py` @ #5705 (`74f9f1eaa`) — **owns every exit code**; this lane supplies **records**, it does not judge |
| observer | `tools/queue_window_observe.py` (added by this lane; read-only) |
| committed records | `docs/ci/merge-throughput-m3-m6-records.json` (8 h), `docs/ci/merge-throughput-m4-capacity-records.json` (14 d) |

**The observer exists because the instrument's `--watch-queue` / `--observe-capacity` take a
SINGLE sample** (`_capacity_sample()` → one `{queued, in_progress}` dict), while M3/M5/M6 are defined
over **timestamped windows** (plan §5, M3: *"Derive from timestamped queue check-run / branch
create-delete events rather than only 60 s polling"*). This is a Task 1 / Task 3 **contract gap**,
recorded here rather than worked around silently: the observer only *observes*; `merge_throughput.py`
still owns polarity, thresholds and exit codes, and the observer does not import or re-implement it.
`--watch-queue` at `74f9f1eaa` returns `{"mode": "watch-queue", "samples": [<one sample>]}`.

**UNKNOWN policy (plan §5).** A non-zero exit, an empty output, or a read that cannot be localised to a
moment is recorded as `UNKNOWN`, **never** as `0` and never as a guess. "Max observed" alone is never
reported as the effective value.

**Reproduce:**

```bash
gh api repos/daniel-ospina/tortoise/actions/runs?per_page=100 --paginate \
    --jq '.workflow_runs[]' > runs.jsonl
python3 tools/merge_throughput.py --json .conflicts > conflicts.json
python3 tools/queue_window_observe.py --from-json runs.jsonl --window-hours 8 \
    --confirm-refs --conflicts-json conflicts.json \
    --out docs/ci/merge-throughput-m3-m6-records.json
# then, with the instrument present (#5705):
python3 tools/merge_throughput.py check batch-size          --input docs/ci/merge-throughput-m3-m6-records.json
python3 tools/merge_throughput.py check capacity            --input docs/ci/merge-throughput-m3-m6-records.json
python3 tools/merge_throughput.py check parallelism-headroom --input docs/ci/merge-throughput-m3-m6-records.json
```

## M2 — cycle-time attribution

Owned by **Task 2**, which has not run in this lane's window. This section is a deliberate placeholder,
not an omission: the document is created here because Task 3 must record measurements and Task 2's doc
did not exist. **M2 is UNKNOWN.** Its 38 m / 28 m split remains unexplained (plan ⟨C2⟩).

## M3 — effective `max_parallel_checks`

| reading | value | basis |
|---|---|---|
| **configured** | not set in `.mergify.yml` | `configured_source = "documented-default"` |
| **effective** | **5** | largest wave size observed in ≥ 2 waves |
| wave sizes (8 h) | `{1: 5, 5: 6}` | 5 branches form together, six times |
| max observed batches (8 h) | 5 | one-off bursts excluded by the recurrence rule |
| max observed batches (14 d) | 15 | a **one-off** burst — never the effective value |
| live queue refs at observation | 2 | momentary; **corroboration only**, not the value |

**Verdict: effective `max_parallel_checks` = 5 — the documented default, now measured.** The
plan's ⟨C1⟩ is confirmed and strengthened: `#5527` setting `max_parallel_checks: 3` is a **reduction
from an effective 5, not a 3× gain.** Any claim that it is a gain must be withdrawn.

**Samples above 5 are not extra parallelism.** A 5-min sweep of simultaneous `Python CI` runs peaked at
7 in the fresh window; those are **bisection re-runs** (a red batch re-tested as singles), which are
*additional* to the speculative batches, not more speculative depth. Reading "max observed" would have
reported 7 or the one-off 15 — the exact error M3 exists to prevent.

**Sampling caveat (stated, per M3):** the wave grouping is bounded to a 360 s formation gap, so two
genuinely separate waves closer than that are merged into one; conversely per-ref polling would alias
short-lived branches *downward*. The value is derived from queue-branch create timestamps and per-run
start/end timestamps, not from a poll.

**Decision unblocked:** `#5527` (T-F) can now be described correctly — a **cap** plus priority rules,
not a multiplier. The plan's §4.2 order-6 note stands.

## M4 — runner capacity and headroom

| reading | 8 h window | 14 d window |
|---|---|---|
| max concurrent **queued** runs | 2 | 32 |
| max concurrent **in-progress** runs | 40 | 125 |
| max concurrent **queued + in-progress** | 41 | 139 |
| max **oldest-queued** age | 177.8 min | 12 689.2 min (~8.8 d, a stale entry) |
| max per-run queue wait | 181.9 min | 12 693.5 min |
| runs **delayed** ≥ 60 min | 2 | 174 |
| **`capacity_at_first_failure`** | **UNKNOWN** | **UNKNOWN** |

**`capacity_at_first_failure` = UNKNOWN, deliberately.** The plan's I9 defines it as *"the concurrency
at which a run **demonstrably failed to acquire a runner**"*. In the observed corpus exactly **one**
run meets that bar: `Python CI` run `35776476728` (`2026-09-22T19:51:30Z`, `startup_failure`, **zero
jobs**, `run_started_at` two days later). Its ~40 h wait places its creation and its failure in
**different windows**, so no single concurrency can be read from it — and it is recorded as UNKNOWN
rather than guessed at. The long waits that remain (63–181 min) are **delays**: those runs *did* acquire
a runner and completed, so counting them would manufacture a capacity number out of ordinary pressure.
**I9 therefore refuses (exit 2), and no parallelism value is authorised by this window.**

**Complementary observation (not an I9 input).** The environment demonstrably absorbed **≥ 125–139
concurrent runs** at peak. The queue's own footprint at saturation is 5 batches × ~7 workflows ≈ **35
run-slots**. On that reading the plan's T-G premise — *runner capacity is the real ceiling* — is **not
supported in this window**: the queue is nowhere near the observed runner ceiling. It is *the CI
runs themselves* (load 49–171 on 10 CPUs) that bind, not runner acquisition.

**Unit warning for I9 (a plan defect, reported not silently fixed).** I9 compares
`configured_max_parallel_checks` (**batches**) against `capacity_at_first_failure`
(**concurrent workflow runs**) — **different units**, so the predicate's verdict can flip on
normalisation alone. The record therefore carries both `capacity_at_first_failure` (runs) and
`capacity_at_first_failure_batches` (queue branches), and F4 (below) names the fix.

## M5 — why batches form at size 1

**They do not. The premise is falsified.** Within the 8 h window the merge queue formed **31 batches:
30 of size 2 and 0 of size 1**; the 5 remaining single-PR branches are **bisection re-runs**, not
formations (a size-1 branch whose PR appeared in an earlier batch within 2 h). Over 14 days: **106
formations, all size 2**, plus 20 bisection singles.

| evidence | value |
|---|---|
| batch formations (8 h) | 30, **all size 2** |
| bisection singles (8 h) | 5 (e.g. `#5356 + #5343` at 06:37Z → `#5356` at 07:11Z → `#5343` at 07:58Z) |
| formations (14 d) | 106, all size 2 |
| **wave formation span** | 1–2 min per 5-branch wave — **inside** `batch_max_wait_time: 5 min` |
| **eligible PRs at formation** | ≥ 10 per 5-wave (5 pairs) |
| conflicted set, same window | 166 open PRs enumerated, **21 conflicting**, 1 unknown |

**The three confounders the plan named are separated:**

1. **`batch_max_wait_time: 5 min`** — **not** capping. Waves form in 1–2 min, well inside the window.
2. **Demand starvation** — **not** the cause. Waves pair 10 distinct PRs simultaneously; there was
   never a "one eligible PR" regime in the paired window.
3. **Conflicts** — real but **not** the modal cap: 21 of 166 PRs conflict (12.7 %), while the paired
   formations dominate. Conflicts cap *individual* PRs, not the batch size.

**What the original reading actually measured.** The 2026-09-26 note *"observed batches are size 1
(`Merge of #5397` 21:44Z, `Merge of #5383` 22:23Z)"* cites **merge** events. A batch of two that reds
is bisected and its constituents land (or not) **separately**, so a *merge* of one is exactly what a
two-PR batch produces after a bisection. The regime also changed: every formation up to
`2026-09-25T22:41Z` was size 1, and **every** formation from `2026-09-26T12:01Z` on is a pair — so a
baseline captured before that switch is stale.

**Decision unblocked:** **E1b (`batch_size: 2 → 4`) is not gated on a pairing failure.** M5's blocking
role (plan §4.3) is discharged: batches already pair, so the question for E1b is the **latency/red-rate**
one (⟨C3⟩), not "why don't batches pair". `batch_size: 2` is the binding and **achieved** value.

## M6 — speculative-invalidation waste

| reading | 8 h | 14 d |
|---|---|---|
| waves | 11 | 42 |
| batch waves (size ≥ 2) | 6 | 18 |
| red batch waves | **5** | 12 |
| **discarded speculative batches** | **20** | 48 |
| waste ratio (per batch wave) | 3.33 | 2.67 |
| `Python CI` on queue heads | **`{failure: 30}` — 0 success** | `{failure: 91, success: 30}` |

**The ⟨C3⟩ term-iii waste is real and at its maximum.** In serial mode a red invalidates the
speculative batches above it, so a red head of an N-branch wave wastes **N − 1** validations. Measured:
each red 5-wave discards **4** speculative batches — the `max_parallel_checks − 1` term the plan said to
measure rather than assume. **20 speculative batch validations were discarded in 8 h.**

**and the cause is not the queue.** Over the fresh 8 h **every** `Python CI` run on a queue head failed
(30/30). Main's required gate reads `exit 2` under S1 (`check main-gate --strict --require-fresh`:
all six required contexts `NO_MAIN_SIGNAL`; the lax polarity reads RED). So the queue is not
draining slowly because of its own configuration — **the heavy leg is red on the tree that would land**,
which is ⟨C4⟩ / #5597, and it makes the speculative machinery pure waste until it is fixed. **This
reorders the plan's own lever list: no P2 throughput work pays while the batch head cannot pass.**

## Records consumed by the instrument (exit codes)

| check | command | result |
|---|---|---|
| `batch-size` | `check batch-size --min 2 --min-depth 1 --input docs/ci/merge-throughput-m3-m6-records.json` | **0** (max 2, 30 events) |
| `parallelism-headroom` | `check parallelism-headroom --input docs/ci/merge-throughput-m4-capacity-records.json` | **2** — `2: capacity_at_first_failure is UNKNOWN (refuse, never pass)` (I9) |
| `capacity` | `check capacity --max-oldest-minutes 120 --min-headroom 1 --input docs/ci/merge-throughput-m4-capacity-records.json` | **2** — `2: capacity field 'capacity_at_first_failure' is missing or not numeric` |
| `parallelism-headroom` | `check parallelism-headroom --input <record>` | **2** — capacity UNKNOWN ⇒ refuse, never pass (I9) |
| `main-gate` (S1) | `check main-gate --strict --require-fresh` | **2** — all six required contexts `NO_MAIN_SIGNAL` |

Both `capacity` and `parallelism-headroom` exit **2**, not `1`: the instrument's field-presence check
precedes the threshold check, so UNKNOWN dominates a threshold miss. I9's refusal must never read as
"merely below threshold".

**S11's oldest-age threshold is independently failing.** In the 8 h window the oldest-queued age
reached **177.8 min**, above S11's `--max-oldest-minutes 120` — so even once headroom is measurable,
capacity is still over the plan's bound. The 14 d record's `oldest_minutes` of 12 689 min is dominated
by a single stale queue entry and must not be read as a steady state.

## Findings that need a decision or a fix (filed, not silently fixed)

| # | finding | owner / route |
|---|---|---|
| **F1** | `--watch-queue` / `--observe-capacity` are single-sample: the plan's M3/M5/M6 cannot be produced by the instrument as delivered | Task 1 lane (#5705); this lane adds the read-only observer |
| **F2** | The plan's reported baseline "observed batch size is 1" conflates **bisection re-runs** and **merge events** with **batch formations**; the effective formation size is 2 | plan §1.1 / §5 M5, corrected here |
| **F3** | `#5527`'s `max_parallel_checks: 3` is a **reduction from an effective 5** (⟨C1⟩ confirmed) | #5527, before it is described as a gain |
| **F4** | I9 compares `capacity_at_first_failure` (**runs**) with `configured_max_parallel_checks` (**batches**) — **different units**, so the predicate's verdict flips on normalisation | plan §3 I9 / Task 4b's guard |
| **F5** | Runner capacity is **not** demonstrated to be the binding ceiling (peak 125–139 concurrent runs absorbed; queue footprint ≈ 35) | plan T-G / D4 |
| **F6** | No `capacity_at_first_failure` sample is localisable in a 14-day window ⇒ I9 refuses; "headroom" stays UNKNOWN until a genuine acquisition failure is observed | D4 (trigger-only, still gated) |
