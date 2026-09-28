---
title: "Merge Throughput Measurements"
type: engineering
domain: capability
doc_status: live
created: 2026-09-27
subjects.team: epistemic-team
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# Merge Throughput Measurements (#5215)

> Record home for the **measurement plan (§5)** of `docs/plans/2026-09-26-5215-merge-throughput.md`.
> **One writer per file per wave.** Task 2 wrote **M2** (below). Task 3 appends **M3–M6** (M8 is
> Task 4b's; Task 6 records the full M1–M6/M8 set). Task 6 writes the `#ceiling` anchor. A row's
> numbers are owned by that row's task — do not restate them here.
>
> Every record carries a **capture timestamp** and the **resolved `origin/main` SHA**; the validity
> window is **per row** (§5). The instrument's `check baseline-fresh` is the implementation of
> `--require-fresh`; a row states its own window.
>
> **Reconciliation note.** Task 2 and Task 3 both had to write this file; Task 2 landed first
> (#5874, M2) while this branch was open, so the two versions were **merged into one file**: Task 2's
> M2 section is preserved verbatim above, and Task 3's M3–M6 (provenance, records, findings) follows.

---

## M2 — Cycle-time attribution (first-class; ⟨C2⟩)

**Intent.** `test (a)` runs materially longer than `test (b)` on a full push-to-main matrix, and the
cause is unrecorded (⟨C2⟩). The claim attached to it — that reweighting the shards buys ~4 minutes of
cycle — is only valid if the imbalance is **weight-driven** rather than **overhead-driven**. M2
partitions the heavy job's wall time into **collection / execution / teardown / watchdog-recovery**,
summing to wall time (±30 s), and names the driver.

### Record

| Field | Value |
| --- | --- |
| `capture` (`verified_at`) | `2026-09-27T18:49:49Z` |
| validity window | **7 days** (§5: M2) |
| resolved `origin/main` at capture | `db70babd507abd33c7a1b6749b98b7ee8a5c4bf8` |
| measured run id | `36333730533` (event `push`, branch `main`) |
| measured run `head_sha` | `fad98a5fd67e3a5dd15fdf4103df19b4ea3f0798` |
| run `created_at` | `2026-09-27T16:34:17Z` |
| heavy jobs | `test (a)` = `108668615242`, `test (b)` = `108668615136` |

### Sources read — and why the primary read is UNKNOWN, not zero

M2's primary source is the existing collector (`tools/ci_timing.py` / `docs/ci-timing.json`). It was
read first. **It is empty**, so that path is recorded as **UNKNOWN** — never as a zero value:

| Source | Bytes | sha256 | Usable? |
| --- | --- | --- | --- |
| `docs/ci-timing.json` (primary) | **43** | `486bf262b8573fb6aa2b42cb152d2e9d28d8fd3320042a59f4f9ef07c3531ce5` | **No** — `{"schema_version":1,"history":[]}`, no `sampled_run`/`steps`; the collector has never sampled (⟨C2⟩) |
| collector JSON for run `36333730533` (fallback) | **25 621** | — | **Yes** — Jobs-API step durations, produced by the collector itself |
| raw job log `test (a)` (fallback) | **218 822** | — | **Yes** — non-empty (`0 bytes ≠ empty`) |
| raw job log `test (b)` (fallback) | **205 980** | — | **Yes** — non-empty |

The fallback is the one M2 sanctions: *"fall back to the job log only if unusable — then
`gh api --allow-escape-sequences` (0 bytes ≠ empty)"*. It was taken because the primary read was
empty. The collector was not forked: it was **run** (`--run-id … --out-dir …`) and its own
`steps_by_job` step table is the step-level source. The raw logs supplied the **pytest session-banner
wall time** (and the pass/fail summary counts) — the one phase boundary the collector does not record.
That is a **documented gap in the collector**, owned by #5393 / plan Task 4b, **not** a second parser:
`tools/ci_timing.py::parse_log` parses files/counts/outcomes/**killed** (the collector *does* flag a
watchdog kill) and `steps_by_job` records each step's `conclusion`, so the watchdog signal and step
conclusions are already the collector's; only the session wall time is not.

### Phase attribution — the four phases, summed to wall time

`collection` = the pre-execution job steps **plus pytest's in-run collection**; `execution` = the
pytest session minus that in-run collection; `teardown` = the post-session tail (atexit + the step's
summary print) plus the post-execution job steps and the inter-step gaps; `watchdog-recovery` = the
SIGINT/grace time and re-runs a watchdog kill costs.

| Phase | `test (a)` (s) | `test (a)` (min) | `test (b)` (s) | `test (b)` (min) | Composition |
| --- | ---: | ---: | ---: | ---: | --- |
| collection | 314.00 | 5.23 | 308.00 | 5.13 | pre-run steps (232 / 240, incl. the standalone collect-only pass 82 / 68) + in-run collection proxy (82 / 68) |
| execution | 1860.67 | 31.01 | 1280.22 | 21.34 | pytest session (1942.67 / 1348.22) − in-run collection proxy (82 / 68) |
| teardown | 15.33 | 0.26 | 18.78 | 0.31 | post-session tail (9.33 / 9.78, atexit + summary) + post-run steps (3 / 5) + inter-step gap (3 / 4) |
| watchdog-recovery | 0.00 | 0.00 | 0.00 | 0.00 | no kill: `rc=0` / `rc=1`, no `WATCHDOG: pytest killed` banner |
| **sum** | **2190.00** | **36.50** | **1607.00** | **26.78** | **= job wall exactly (Δ = 0.00 s; bound ±30 s)** |

**Backing measurements** (Jobs API step `started_at`/`completed_at` via the collector; pytest session
banner read from the raw log):

| Quantity | `test (a)` | `test (b)` |
| --- | ---: | ---: |
| job wall | 2190 s (36.50 min) | 1607 s (26.78 min) |
| Σ step durations | 2187 s | 1603 s |
| pre-run steps | 232 s | 240 s |
| `Run fast test suite` step | 1952 s | 1358 s |
| pytest session (banner) | 1942.67 s | 1348.22 s |
| post-session tail (step − banner) | 9.33 s | 9.78 s |
| post-run steps | 3 s | 5 s |
| tests | 8238 passed, 0 failed | 8722 passed, 2 failed |

The phase sum is **exact and independent of the collection proxy**: if in-run collection is `X` instead
of 82 / 68, `collection` gains `X` and `execution` loses `X`, and the total is unchanged at 2190.00 /
1607.00 s. The proxy is the standalone collect-only step (`.github/workflows/python-ci.yml`, "Generate
coverage manifest"), which collects the identical file set under the identical marker filter.

### Verdict — the 38/28 split is **weight-driven**, not overhead-driven

| Phase delta (`test (a)` − `test (b)`) | Seconds | Minutes | Share of the gap |
| --- | ---: | ---: | ---: |
| collection | +6.00 | +0.100 | 1.0 % |
| execution | **+580.45** | **+9.674** | **99.6 %** |
| teardown | −3.45 | −0.058 | −0.6 % |
| watchdog-recovery | +0.00 | +0.000 | 0.0 % |
| **total** | **+583.00** | **+9.717** | 100 % |

`m2_verdict: weight_driven`

The entire ~9.7-minute gap sits in **execution**. The overhead phases do not explain it — collection
differs by **6 s** and teardown is **lower** for `test (a)`. Watchdog recovery is 0 for both halves.

The imbalance is a **stale-weight** defect, not a pack that cannot be improved. The durations map
(`config/ci-surfaces.yml:durations`, 688 entries) claims the halves are **exactly balanced**:

| Half | Files | Map weight | Observed pytest session |
| --- | ---: | ---: | ---: |
| `half_a` | 329 | 29.22 min | 32.38 min |
| `half_b` | 326 | 29.22 min | 22.47 min |
| ratio | — | **1.00×** (tolerance 1.25×) | **1.44×** |

The LPT pack balanced the **map's** weights perfectly (29.22 = 29.22 min), yet the observed execution
diverges by 44 %. `test (a)` executes **fewer** tests (8238 vs 8724) in **more** time — its per-test
cost is 1.53× `test (b)`'s. So the map's per-file durations are wrong, not the packing: a fresh
measurement feeding the map (the collector → map **bridge**, plan Task 4b) is the lever, and it is the
one T-B's "~4-minute cycle win" depends on. Balancing execution to the observed mean would move the
slowest half from 32.4 min toward ~27.4 min — the same order as the plan's claimed ~4 min.

### Sample caveat — no both-green run exists, and the 38/28 signal is confounded with main's red

All **14 of 14** most recent completed push-to-main `python-ci.yml` runs have **at least one heavy half
red** (checked `2026-09-27T18:5xZ` over the 20-run window). There is therefore **no both-green clean
sample** to confirm the split.

The measured run is still usable for the *work* comparison: `test (b)` ended `rc=1` with **2 failures
out of 8724 and did NOT hit `--maxfail=20`** (no "stopping after 20 failures"), so its 1348.22 s
session measured its **full** file set — it is not a truncated red. `test (a)` was green (`rc=0`). The
comparison is therefore valid for execution cost, but the *presence* of main's red on one half in every
available run is itself a finding: **the 38/28 imbalance cannot be cleanly separated from the live
main-red defect (⟨C4⟩) until a both-green run exists.** That is a precondition for T-B, not a detail.

### Not measurable (recorded, not silently zeroed)

- **pytest's in-run collection time** is not printed by the suite; the standalone collect-only step
  (82 s / 68 s) is the proxy. The phase **sum** does not depend on it.
- **watchdog-recovery** is 0 **because neither half was killed in this sample**. The partition is
  stated so that a killed run yields a non-zero value; a 0 here is a measured 0, not an assumption.
- **The collector path itself** — `docs/ci-timing.json` is empty, so M2's own reading of the collector
  is **UNKNOWN**. The collector was refreshed into a temp dir only; the committed artifact was **not**
  modified (Task 4b owns `tools/ci_timing.py` + `.github/workflows/ci-timing.yml`; one writer per
  file per wave).

### Reproduction

```bash
# 1. Primary read — the collector's committed artifact (empty ⇒ UNKNOWN, never 0)
wc -c docs/ci-timing.json            # 43
shasum -a 256 docs/ci-timing.json

# 2. Fallback — the collector's own eligible-run selection, then the step table
python3 tools/ci_timing.py --repo daniel-ospina/tortoise --pick-run          # 36333730533
python3 tools/ci_timing.py --repo daniel-ospina/tortoise \
    --run-id 36333730533 --out-dir /tmp/m2-collector --logs-dir /tmp/m2-nologs

# 3. Raw job logs (the sanctioned fallback read; 0 bytes ≠ empty)
gh api --allow-escape-sequences repos/daniel-ospina/tortoise/actions/jobs/108668615242/logs > a.raw
gh api --allow-escape-sequences repos/daniel-ospina/tortoise/actions/jobs/108668615136/logs > b.raw
grep -aoE "[0-9]+ passed[^=]* in [0-9.]+s" a.raw   # pytest session banner
grep -aoE "pytest exit code: [0-9]+"        b.raw
grep -ac  "stopping after"                  b.raw   # 0 ⇒ --maxfail not hit

# 4. Duration-map weights per half (consumes ci_selection.py, never a second packer)
python3 -c "import sys; sys.path.insert(0,'tools'); import ci_selection as c; \
m=c.load_manifest(); l=c.push_legs(m); d=c._durations_map(m); \
print({h: round(sum(c._duration_weight(d.get(f if f.endswith('.py') else f+'.py')) for f in l[h])/60, 2) for h in ('half_a','half_b')})"
```

---

## M3–M6 provenance (Task 3), and how to reproduce

| field | value |
|---|---|
| capture timestamp (UTC) | both records `end` = `2026-09-27T19:35:39Z` — the newest run in the corpus, pinned with `--as-of` so the window end sits **inside** the corpus's coverage (rather than in the gap between the dump and the run) |
| resolved `origin/main` at capture | `56e2558399e73f9619b817016c92790a97c7b4bc` — passed as `--main-sha` and recorded per record as `window.main_sha`, with `main_sha_source = "caller-supplied --main-sha (asserted, not resolved by this tool)"`: the replay did not itself resolve `origin/main`, and the source field says so rather than claiming the tool resolved it |
| window (M3/M5/M6) | 8 h |
| window (M4) | 14 d |
| corpus | 39 972 unique workflow runs (`2026-08-30T23:00:23Z` → `2026-09-27T19:35:39Z`, 27.9 d) via `gh api /actions/runs`; 8 h window: 501 queue runs / 43 queue branches; 14 d window: 1 917 queue runs / 165 queue branches. The Actions endpoint caps pagination at **400 pages / 40 000 runs** (pages past it return HTTP 422), so the dump is **deduped by run `id`** and non-run error payloads dropped before replay. Both records carry the corpus read itself (`window.corpus_runs`, `window.corpus_first_run_at`, and `window.truncated` — **`null` here, because a `--from-json` replay did not produce the dump and its completeness is UNKNOWN; the field is `true`/`false` only on the observer's own `--live` read**), so this row is verifiable from the committed artifacts. |
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
# newest-first dump; the Actions endpoint caps pagination at 400 pages, so pages
# past it return HTTP 422 — dedupe by run id and drop the error payloads.
gh api repos/daniel-ospina/tortoise/actions/runs?per_page=100 --paginate \
    --jq '.workflow_runs[]' > runs.raw.jsonl
python3 - <<'PY'
import json
seen = set()
with open("runs.raw.jsonl") as src, open("runs.jsonl", "w") as out:
    for line in src:
        try:
            r = json.loads(line)
        except ValueError:
            continue                      # HTTP-422 pagination error payload
        if isinstance(r, dict) and "id" in r and r["id"] not in seen:
            seen.add(r["id"])
            out.write(json.dumps(r) + "\n")
PY
python3 tools/merge_throughput.py --json .conflicts > conflicts.json
MAIN=56e2558399e73f9619b817016c92790a97c7b4bc   # capture-time origin/main — pinned, NOT `git ls-remote` (which moves past --as-of)
python3 tools/queue_window_observe.py --from-json runs.jsonl --window-hours 8 \
    --as-of 2026-09-27T19:35:39Z --main-sha "$MAIN" \
    --confirm-refs --conflicts-json conflicts.json \
    --out docs/ci/merge-throughput-m3-m6-records.json
python3 tools/queue_window_observe.py --from-json runs.jsonl --window-hours 336 \
    --as-of 2026-09-27T19:35:39Z --main-sha "$MAIN" \
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

## M3 — effective `max_parallel_checks`

| reading | value | basis |
|---|---|---|
| **configured** | **5** | `configured_source = "documented-default (not set in .mergify.yml)"` |
| **effective** | **5** | largest wave size observed in **≥ 2 waves** |
| wave sizes, 8 h | `{1: 2, 4: 2, 5: 5, 8: 1}` | 5 recurs 5×, the 4s recur 2×; the 8 is a **one-off** |
| wave sizes, 14 d | `{1: 12, 2: 1, 4: 2, 5: 22, 8: 1, 10: 1, 15: 1}` | 5 recurs 22×; the 8, 10 and 15 are **one-offs** |
| max observed batches | 8 (8 h) / 15 (14 d) | one-off bursts — never the effective value |
| live queue refs at observation | *recorded per cut* (`live_queue_refs_at_observation`) | **momentary** corroboration only — a queue branch is short-lived, so it is NOT the effective value here (`parallelism.basis = "largest wave size observed in >=2 waves"`). It stands in only as a **disclosed** fallback when the window has no repeatable wave (`basis = "direct ref listing (no repeatable wave in the window)"`), and it is never pinned as a doc figure |

**Verdict: effective `max_parallel_checks` = 5 — the documented default, now measured.** The plan's
⟨C1⟩ is confirmed and strengthened: `#5527` setting `max_parallel_checks: 3` is a **reduction from an
effective 5, not a 3× gain** (F3). Any claim that it is a gain must be withdrawn.

**A max-observed reading would have been wrong.** The 14-day window contains one-off **15**-, **10**- and **8**-branch waves; the 8-hour window's sweep also sees **bisection re-runs** (`batches.bisection_singles` = 6) layered on top of the speculative wave. The recurrence rule
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
| max concurrent **queued** runs | 7 | 43 |
| max concurrent **in-progress** runs | 56 | 125 |
| max concurrent **queued + in-progress** | 56 | 139 |
| max **oldest-queued** age (`oldest_minutes`) | **52.8 min** | 12 693.5 min (a stale entry; not a steady state) |
| max per-run queue wait (`max_queue_wait_minutes`) | 52.8 min | 12 693.5 min |
| runs **delayed** ≥ 60 min | 0 | 189 |
| **`capacity_at_first_failure`** | **UNKNOWN** | **UNKNOWN** |
| **`headroom`** | **UNKNOWN** | **UNKNOWN** |

**`capacity_at_first_failure` = UNKNOWN, deliberately — 8 h because no sample exists, 14 d because the
one sample is not localisable.** The plan's I9 defines it as *"the concurrency at which a run
**demonstrably failed to acquire a runner**"*. The observed API signal for that is exactly one
`startup_failure` (a run whose workflow created **zero jobs**), `Python CI` run `35776476728` at
`2026-09-22T19:51:30Z`, whose recorded queue wait is **2 410.3 min (~1.7 d)** — far above the 360-min
stale bound. The candidate is named in the record's `capacity_at_first_failure_candidates`, so this
claim is verifiable from the committed artifact, not only from prose. Its creation and its failure are
therefore in **different windows**, so no single concurrency can be read from it; the record's
`capacity_at_first_failure_reason` states this verbatim (the 8 h record: *"no run in the window failed
to acquire a runner"*; the 14 d record: *"all 1 failed-to-start candidate(s) … unlocalizable wait …
above the 360-min stale bound"*). Long waits are **delays**, not starvation: those runs *did* acquire a
runner and completed (8 h: none ≥ 60 min, max 52.8; 14 d: 189 runs ≥ 60 min, max a stale 12 693.5),
and are recorded separately in `max_queue_wait_minutes` / `runs_delayed_over_60min`. Counting them
would manufacture a capacity number out of ordinary pressure.

**I9 therefore refuses (exit 2), and no parallelism value is authorised by this window** (F6). This is
the plan's required behaviour — an unmeasured headroom must never read as a pass.

**Complementary observation (not an I9 input).** The environment demonstrably absorbed **≥ 125–139
concurrent runs** at peak. The queue's own footprint at saturation is 5 batches × ~7 workflows ≈ **35
run-slots**. On that reading the plan's T-G premise — *runner capacity is the real ceiling* — is **not
supported in this window** (F5): the queue is nowhere near the observed runner ceiling, and what binds
is the CI load itself.

**S11's oldest-age threshold.** On the fresh 8 h record `oldest_minutes` is **52.8 min**, *below*
S11's `--max-oldest-minutes 120`; on the 14 d record it is a stale **12 693.5 min**, far above it.
S11 still exits 2 — because `capacity_at_first_failure` is UNKNOWN, not because of the age threshold —
so the binding failure is the **missing headroom**, not an over-age queue.

**Unit warning for I9 (F4).** I9 compares `configured_max_parallel_checks` (**batches**) with
`capacity_at_first_failure` (**concurrent workflow runs**) — **different units**, so the predicate's
verdict flips on normalisation. The record carries both readings (`capacity_at_first_failure`,
`capacity_at_first_failure_batches`, `headroom`, `headroom_batches`). Its `headroom_reason` here
records the **UNKNOWN refusal** (neither window has a localisable sample); the unit-mismatch wording is
emitted only on the branch where a capacity sample exists.

## M5 — why batches are size 1

**In the fresh window they are not. The premise holds only for a 6.1 % historical residue.**

| evidence | 8 h | 14 d |
|---|---|---|
| batch formations (bisections excluded) | **43** | **165** |
| — of size 2 | **43** | **155** |
| — genuinely size 1 | **0** | **10** |
| bisection re-runs (excluded from formations) | 6 | 24 |
| `formation_size_distribution` | `{2: 43}` | `{1: 10, 2: 155}` |
| **queue depth at formation** (`max_queue_depth_at_formation`) | **10** | **15** |
| conflicted set (point-in-time snapshot) | 177 enumerated, **23 conflicting**, 1 unknown | same snapshot |

**The fresh window has ZERO single-PR formations.** Every one of the 43 formations in the last 8 h is a
pair (155 of 165 over 14 d). The size-1 branches are **bisection re-runs** after a red (6 in 8 h, 24 in
14 d). The plan's own cite (`Merge of #5397` 21:44Z) is a **merge** event, which is exactly what a
two-PR batch produces *after* a bisection. The regime also changed: every formation up to
`2026-09-25T22:41Z` was size 1, and every one from `2026-09-26T12:01Z` on is a multiple, so a baseline
captured before that switch is stale.

**The 14-day residue is 10 of 165 (6.1 %) genuine singles** — a real but small minority, not "batch size
is 1". Those are PRs that were conflict-capped or demand-starved individually; they are the honest
remainder, and they are why the E1b decision is stated as *"pairing works"*, not *"pairing is
universal"*.

**The three confounders the plan named are separated:**

1. **`batch_max_wait_time: 5 min`** — **not** capping. 5-branch waves form in 1–2 min, well inside the
   window.
2. **Demand starvation** — **not** the modal cause. A 5-wave pairs 10 distinct PRs simultaneously.
3. **Conflicts** — real but not the modal cap: **23 of 177** open PRs conflict (13.0 %) in the snapshot,
   while paired formations dominate.

**Queue depth at formation** is the number of distinct `mergify/merge-queue/*` branches whose run was
**in flight at the formation timestamp** (`batches.max_queue_depth_at_formation`, with a per-formation
`queue_depth_at_formation`) — it separates "the queue was empty" from "the pair did not form". **The
conflicted set is a point-in-time snapshot of the currently open PRs**, stamped
`window_scope: "point-in-time snapshot at capture (NOT window-scoped)"`: the PR API keeps no historical
conflict state, so it **cannot** be scoped to the 14-day window (F10).

**Decision unblocked:** **E1b (`batch_size: 2 → 4`) is not gated on a pairing failure.** M5's blocking
role (plan §4.3) is discharged: the remaining question for E1b is the **latency / red-rate** one
(⟨C3⟩), not "why don't batches pair".

## M6 — speculative-invalidation waste

| reading | 8 h | 14 d |
|---|---|---|
| waves | 10 | 40 |
| batch waves (size ≥ 2) | 8 | 28 |
| red batch waves | **4** | 18 |
| unobserved wave heads (`red: null`) | 1 | 1 |
| **discarded speculative batches** | **17** | **73** |
| waste ratio (per batch wave) | 2.12 | 2.61 |
| `Python CI` on queue heads | `{failure: 33, success: 7, cancelled: 4}` | `{failure: 140, success: 40, cancelled: 4}` |

**The ⟨C3⟩ term-iii waste is real and large.** In serial mode a red invalidates the speculative
batches above it, so a red head of an N-branch wave wastes **N − 1** validations. Measured: each red
5-wave discards **4** speculative batches (the `max_parallel_checks − 1` term the plan said to measure
rather than assume), and the one-off **8**-branch wave discarded **7**. **17 speculative batch
validations were discarded in 8 h** (73 over 14 d).

A wave head whose heavy leg was never observed is recorded as `red: null` and **excluded** from the red
count, so its waste is a **floor**, not a verified zero — an unobserved head must not read as "clean".

**The queue is dominated by red, but the heavy leg is not universally failing.** Over the fresh 8 h the
`Python CI` heavy leg on queue heads is `{failure: 33, success: 7, cancelled: 4}` — **7 successes**, so
an earlier reading of *every* queue head failing (30/30) is **superseded** by this window. The failure
rate is 33 of 44 concluded runs (75 %), which still makes most speculative work waste and still points
at **the heavy leg being red on the tree that would land** (⟨C4⟩ / #5597) as the dominant term. The
honest statement is "mostly red", not "always red": measured, not assumed.

## Records consumed by the instrument (exit codes)

Run with the instrument at `74f9f1eaa`; each command was executed and its code recorded.

| criterion | command | result |
|---|---|---|
| **S6** | `check batch-size --min 2 --min-depth 1 --require-fresh --input docs/ci/merge-throughput-m3-m6-records.json` | **0** (43 events, all size 2) |
| **S14** | `check parallelism-headroom --require-fresh --input docs/ci/merge-throughput-m4-capacity-records.json` | **2** — `2: capacity_at_first_failure is UNKNOWN (refuse, never pass)` (I9) |
| **S11** | `check capacity --max-oldest-minutes 120 --min-headroom 1 --require-complete --require-fresh --input docs/ci/merge-throughput-m4-capacity-records.json` | **2** — `2: capacity field 'capacity_at_first_failure' is missing or not numeric` |
| **S1** | `check main-gate --strict --require-fresh` | **2** — all six required contexts `NO_MAIN_SIGNAL` |

Both `capacity` and `parallelism-headroom` exit **2**, not `1`: the instrument's field-presence check
precedes the threshold check, so UNKNOWN dominates a threshold miss. I9's refusal must never read as
"merely below threshold".

**S1's six-context reading, recorded precisely.** At main `56e255839…`, `check-runs?filter=all` returns
**zero** check-runs for **all six** required names, so the strict read is `NO_MAIN_SIGNAL` for each and
the criterion is `2`. Five of those are D13's permanent gap (the `pull_request`-only contexts);
`python-ci-gate` **is** push-triggered (`python-ci.yml: push:[main]`), so its absence at a
just-advanced main is a **push run not yet completed**, not a never-emitted signal — a distinction the
instrument cannot make and this record therefore states (F8).

## Findings that need a decision or a fix (reported, not silently fixed)

| # | finding | owner / route |
|---|---|---|
| **F1** | `--watch-queue` / `--observe-capacity` are single-sample: the plan's M3/M5/M6 cannot be produced by the instrument as delivered | Task 1 lane (#5705) |
| **F2** | The plan's "observed batch size is 1" baseline conflates **bisection re-runs** and **merge events** with **batch formations**: the fresh window has 43/43 pairs and **zero** single formations; the 14 d residue is 10/165 (6.1 %) | plan §1.1 / §5 M5 |
| **F3** | `#5527`'s `max_parallel_checks: 3` is a **reduction from an effective 5** (⟨C1⟩ confirmed) | #5527, before it is described as a gain |
| **F4** | I9 compares `capacity_at_first_failure` (**runs**) with `configured_max_parallel_checks` (**batches**) — **different units**, so the predicate's verdict flips on normalisation | plan §3 I9 / Task 4b's guard |
| **F5** | Runner capacity is **not** demonstrated to be the binding ceiling (peak 125–139 concurrent runs absorbed; queue footprint ≈ 35 run-slots) | plan T-G / D4 |
| **F6** | No localisable `capacity_at_first_failure` sample exists in a 14-day window ⇒ I9 refuses; "headroom" stays UNKNOWN until a genuine acquisition failure is observed | D4 (trigger-only, still gated) |
| **F7** | S11/S14 carry `--require-complete`, but a capacity record is a set of **scalar** measurements with no `items`/`total_count`, so `parallelism-headroom --require-complete` exits 2 for a **shape** reason rather than I9's refusal — the criterion cannot distinguish them | plan §11 S11/S14 |
| **F8** | S1 cannot distinguish a required context that **never** emits on main (D13's five) from one whose push run **has not completed** (`python-ci-gate`); both read `NO_MAIN_SIGNAL` | plan §11 S1 / D13 |
| **F10** | M5 asks for the conflicted set *in the same window*. The PR API keeps **no historical conflict state**, so the read is a **point-in-time snapshot** (stamped `window_scope` on the record) reused for both windows — the item is **not retrospectively observable** as windowed. Batch-arrival timestamps and **queue depth at formation** ARE windowed and recorded per formation | plan §5 M5 — limitation stated, never silently claimed |
| **F9** | **Resolved by reconciliation.** Plan Task 3's Files line says *"modify the measurement doc (created by Task 2 — it must land first)"*. Task 2 had **not** landed when this lane created the doc; it landed (#5874, M2) while this PR was open, so the two versions are **merged into one file** — M2 (weight-driven 38/28 split) preserved verbatim, M3–M6 appended. The earlier UNKNOWN M2 placeholder is superseded | plan §10 Task 3 / §4.1 wave map — **RESOLVED** |

---

## M8 — duration-map vs observed wall time, and the collector → map bridge (Task 4b)

**Intent.** Compare `config/ci-surfaces.yml:durations` against the latest completed run's
Jobs-API per-job times, and the map's capture age. `shard_imbalance_minutes` is **observed wall
time**, not the map — a perfectly balanced map with a 10-minute observed split is the whole point.
Task 4b also builds the missing **bridge** from the collector into that map (⟨C2⟩).

### Record

| Field | Value |
| --- | --- |
| `capture` (`verified_at`) | `2026-09-28T01:46:07Z` |
| validity window | **14 days** (§5: M8) |
| resolved `origin/main` at capture | `f520a6575b949e65497a856e548f7573c7f8eba8` |
| instrument | `tools/merge_throughput.py` @ #5705 (`cd536622f`) + this lane's Task 4b collectors |
| measured run id | `36361388386` (event `push`, branch `main`, `created_at` `2026-09-28T00:13:15Z`) |
| heavy legs | `test (a)` = **2465 s (41.08 min)**, conclusion `failure` · `test (b)` = **1539 s (25.65 min)**, conclusion `success` |

### Observed vs the map — the divergence M8 exists to catch

| reading | value | source |
| --- | --- | --- |
| `shard_imbalance_minutes` (observed) | **15.43 min** | Jobs API wall time of the two heavy legs, run `36361388386` |
| map half weights (`half_a` / `half_b`) | 29.25 / 29.26 min | `ci_selection.push_legs` over the committed map |
| map ratio | **1.000×** (tolerance 1.25×) | the map claims perfect balance |
| observed ratio | **1.60×** | 41.08 / 25.65 |

**The map's per-file weights are wrong, not the packing.** The LPT pack balanced the *map's*
weights to 1.000×, while the observed wall time diverges 1.60×. This is the M2 verdict
(`m2_verdict: weight_driven`) reproduced on a second run.

**`check shard-balance --max 3` returns `2` (UNKNOWN), never a false 0.** S8 requires **both** heavy
legs present and `success`; the leg conclusions in this run are `(failure, success)`, so the check
exits 2 with `2: heavy leg 'a' absent or not success`. A red leg is a coverage failure, not a
3-minute pass.

### No both-green run exists in the scanned window

A bounded scan of the **19** most recent completed push-to-main `python-ci.yml` runs (each
Jobs-API read spaced 3 s) found **zero** runs with both heavy legs `success` — every run has exactly
one red half. The observed imbalance across those runs ranged **0.90 – 16.40 min**, and **16 of the
19 exceed the 3-minute criterion** (the exceptions were 2.43, 0.90 and 0.92 min). The imbalance is
not a constant: it swings by an order of magnitude run to run, which is itself evidence that the
map's fixed 29.25/29.26 split does not predict the observed wall time. This confirms M2's sample
caveat: the 38/28 signal **cannot be cleanly separated from the live main-red defect (⟨C4⟩)** until
a both-green run exists, so `shard-balance` will keep exiting **2** — never 0 — in this window.

| run | created_at | `test (a)` | `test (b)` | observed imbalance |
| --- | --- | ---: | ---: | ---: |
| `36361388386` | 2026-09-28T00:13:15Z | failure 41.08 | success 25.65 | 15.43 min |
| `36361356924` | 2026-09-28T00:12:46Z | failure 37.90 | success 33.68 | 4.22 min |
| `36353414171` | 2026-09-27T21:54:35Z | failure 39.42 | success 31.18 | 8.23 min |
| `36347054264` | 2026-09-27T20:10:37Z | success 37.08 | failure 32.63 | 4.45 min |
| `36343532859` | 2026-09-27T19:13:00Z | success 36.78 | failure 32.00 | 4.78 min |
| `36340267748` | 2026-09-27T18:20:17Z | success 30.42 | failure 21.88 | 8.53 min |
| `36339264075` | 2026-09-27T18:04:07Z | success 37.18 | failure 34.17 | 3.02 min |
| `36333730533` | 2026-09-27T16:34:17Z | success 36.50 | failure 26.78 | 9.72 min |
| `36333688276` | 2026-09-27T16:33:37Z | success 25.28 | failure 31.52 | 6.23 min |
| `36324597528` | 2026-09-27T14:04:20Z | success 37.00 | failure 31.80 | 5.20 min |
| `36322252428` | 2026-09-27T13:23:58Z | success 25.88 | failure 33.02 | 7.13 min |
| `36319743555` | 2026-09-27T12:40:00Z | success 36.10 | failure 21.87 | 14.23 min |
| `36319740432` | 2026-09-27T12:39:56Z | success 34.25 | failure 31.10 | 3.15 min |
| `36313443838` | 2026-09-27T10:43:19Z | success 29.20 | failure 31.63 | 2.43 min |
| `36309985069` | 2026-09-27T09:37:56Z | success 37.12 | failure 30.23 | 6.88 min |
| `36308247727` | 2026-09-27T09:05:14Z | failure 38.18 | success 30.88 | 7.30 min |
| `36285863638` | 2026-09-27T01:32:52Z | failure 30.27 | success 31.17 | 0.90 min |
| `36273920361` | 2026-09-26T21:43:42Z | failure 38.65 | success 22.25 | 16.40 min |
| `36272313147` | 2026-09-26T21:15:36Z | failure 30.98 | success 31.90 | 0.92 min |

### The bridge: `tools/ci_timing.py --refresh-durations`

`--refresh-durations` is now the **sole writer** of `config/ci-surfaces.yml:durations`. It derives
from the collector's own `parse_log` (no second parser), is **text-preserving** (line edits, never a
whole-file `yaml.safe_dump`, which would strip the sweep-basis comment header), and **fail-closed**:
a zero-key projection, a collector key not already classified in the manifest, or a refresh that
would fail the manifest-side 0.90 floor writes **nothing** and exits non-zero.

**Demonstration on run `36361388386`** (the same run as the shard reading), into a scratch copy of
the manifest — the committed map is **not** hand-edited (that would re-create the retired one-off
sweep; the scheduled `ci-timing.yml` refresh is the only producer):

```
python3 tools/ci_timing.py --refresh-durations \
  --repo daniel-ospina/tortoise --run-id 36361388386 \
  --logs-dir /tmp/task4b-logs --manifest /tmp/ci-surfaces-mt4b.yml
# → refreshed …: 30 sampled, 658 carried forward (captured_at 2026-09-28T12:00:00Z)
```

**30 weights changed**, 658 carried forward (the collector's `--durations=15` projection is
**partial** — it cannot enumerate the whole 688-key map, which is exactly why the 0.90 floor lives on
the resulting MANIFEST, not on the projection). The 30 re-derivations include several massive stale
over-estimates that the 2026-09-22 sweep carried:

| key | committed | refreshed |
| --- | ---: | ---: |
| `test_onboarding_state_split.py` | 310.7 | **18.4** |
| `test_reaper.py` | 195.9 | **34.3** |
| `test_longmem_runner.py` | 173.3 | **17.0** |
| `test_embedded_lifecycle.py` | 87.7 | **46.6** |
| `test_github_indexer.py` | 50.2 | **24.4** |
| `eval/write_path/test_write_path_benchmark.py` | 101.8 | **229.4** |
| `test_selfhost_rest.py` | 1.4 | **60.4** |
| `test_session_verify.py` | 97.4 | **72.2** |

**Resulting pack (scratch copy):** the map's aggregate pack weight falls from **58.51 min** to
**54.34 min** (the phantom over-estimates removed), and the LPT re-pack stays inside the 1.25×
tolerance (`half_a` 27.17 = `half_b` 27.17 min). That ~4.2-minute map reduction is the mechanism
behind T-B's claimed ~4-minute cycle win — **conditional on the collector actually running**, which
is why this bridge must not land before the collector fix (#5393) and why the committed map is only
refreshed by the scheduled workflow, never by hand.

### Not measurable (recorded, not silently zeroed)

- **A both-green run does not exist** in the 19-run scan, so `shard-balance` is **UNKNOWN (2)** on
  every sample; the imbalance numbers above are observed wall times from red runs, not a clean
  both-green criterion result.
- **The committed map has no `durations_captured_at`**, so `check durations-map --max-age-days 14`
  exits **2** (age UNKNOWN) until the first scheduled `--refresh-durations` writes it. The bridge
  writes the key; its absence is UNKNOWN, never "fresh".
- **The projection is partial by construction** (top-15 per job): 30 of 688 keys were measured, 658
  carried forward. `diverged` is therefore only computed over the observed keys; an un-observed key
  is not evidence of a wrong weight.

### Reproduction

```bash
RUN=$(python3 tools/ci_timing.py --pick-run --repo daniel-ospina/tortoise)
gh run download "$RUN" -R daniel-ospina/tortoise -p 'pytest-log-*' -D /tmp/task4b-logs
# observed shard metric (the instrument's own exit code)
python3 tools/merge_throughput.py check shard-balance --max 3     # → 2 (one heavy leg red)
# the bridge, against a scratch manifest (run twice, or omit --dry-run to write the scratch)
cp config/ci-surfaces.yml /tmp/ci-surfaces-mt4b.yml
python3 tools/ci_timing.py --refresh-durations --repo daniel-ospina/tortoise \
  --run-id "$RUN" --logs-dir /tmp/task4b-logs --manifest /tmp/ci-surfaces-mt4b.yml
```
