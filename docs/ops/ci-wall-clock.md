---
title: "CI wall-clock bounds on the merge path"
type: operations
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: ci-wall-clock, ci-headroom-guards
aboutObjects: tortoise, .github/workflows/python-ci.yml, .github/workflows/ci.yml, .github/workflows/ai-review-gate.yml, .github/workflows/drift-guard.yml
created: 2026-10-27
updated: 2026-11-06
---

# CI wall-clock bounds on the merge path

What bounds a merge here, measured — which clock to read when CI "takes hours", what each
merge-gating job is allowed to take, and what to do when a bound kills a job.

**Measured 2026-10-27, ~04:00Z.** Every number came from the Actions API on that date; re-run
the command in §2 before quoting any of it.

---

## 1. The long reading is a RUN clock, not a job clock

A "Python CI … 4 h 35 m and counting" reading is elapsed **run** time. The run behind the
report was `40417989806` (PR #10083, head `9cd600c7`):

| clock | value |
| --- | --- |
| created → first job started | 23:11:11Z → 23:36:39Z (25 min waiting for a runner) |
| attempt-1 job starts, first → last | 23:36:39Z → 01:29:39Z (**1 h 53 m** to hand out 28 slots) |
| attempt 1 required gate | `python-ci-gate` started 01:29:38Z, red at 01:29:41Z |
| attempt 2 (re-run of the failed legs) | 03:33:18Z → gate green 04:04:04Z |
| run end | **cancelled at 04:04:04Z** by the PR's next commit (run `40446823439`, created 03:55) — the PR arm's `cancel-in-progress` doing its job |
| **longest single job in the whole run** | **22.4 min** (`test (a)`, attempt 1) |
| **run wall, creation → end** | **4 h 53 m** |

So no job ran 4½ hours and nothing was wedged: the run waited for runners, its job slots were
handed out over nearly two hours, one leg went red, and a re-run landed four hours later.
⛔ Do **not** cite the run's `run_duration_ms` as "job execution": on this run that field is
absent, and where GitHub does populate it, it measures the last attempt's start → update — not a
sum over jobs.

The same shape holds across the sample — 54 `python-ci` runs whose jobs are visible among the
60 newest runs (52 `pull_request`, 2 `push`), created 2026-10-26T23:21Z → 10-27T04:10Z:

| queue-level reading | p50 | p95 | max |
| --- | --- | --- | --- |
| jobs per run | 20 | — | 28 |
| wait for a run's FIRST job to start | 29.9 min | 77.0 min | 81.9 min |
| run elapsed (creation → last step end) | 45.9 min | 168.1 min | 183.4 min |
| spread of one run's job starts (first → last) | 19.7 min | 111.5 min | 127.5 min |

⛔ **`timeout-minutes` cannot bound any of that.** The clock starts when a runner picks the job
up, so a job that never gets a runner has no timeout at all. A timeout bounds a WEDGED job — the
360-minute default that holds a required check pending for six hours — and that is the hole §2
closes (its tail: `python-ci.yml` is now capped on **every** job, so no leg inherits the default).

**What the queue is made of** (the lever nobody should confuse with a timeout):

- 18 queued `Python CI` runs at 04:10Z: **17 `pull_request`, 1 `push`**. Repo-wide, 63 queued
  runs: 38 `pull_request`, 12 `issue_comment`, 5 `pull_request_target`, 4 `schedule`, 2 `push`,
  2 `issues`.
- 9 self-hosted runners registered, 8 online, 4 busy at read time. `ubuntu-latest` also matches
  the self-hosted ARM64 pool (#7867), so "hosted" legs contend for the same slots.
- **Superseded `push` runs never reach a runner.** Over the 20 `Python CI` push-to-main runs
  created 2026-10-26T22:40Z → 10-27T04:50Z, **17 of the 20 never started a single job**
  (18 ended `cancelled`, 1 `failure`, 1 still queued) — GitHub keeps one *pending* run per
  concurrency group, so a newer push replaces a waiting one even with
  `cancel-in-progress: false`. Only 3 runs ever started a job, and **one of those 3 was cancelled
  mid-flight** after all 28 slots had been handed out — which is the shape the `push` arm would
  produce for every run if it cancelled in progress. ⚠️ The cancelled subtotal DRIFTS as pending
  runs age into `cancelled`: the same fixed window read 15-cancelled / 14-zero-start at the 04:00Z
  cut and 18-cancelled / 17-zero-start at 06:05Z. **"Never started a job" is the load-bearing
  figure, not the cancelled count** — re-read rather than quote a subtotal. `Post-merge
  validation` has the same shape (**17 of 20 never ran at the 04:00Z cut; 16 of the last 20
  cancelled, 2 `failure`, 2 in flight when re-read at 06:05Z**), which
  `.github/workflows/post-merge-validation.yml` already documents and
  `tests/test_reporting_workflow_concurrency.py` already pins.
- Push-to-main cadence is **bursty**, not periodic: over that 6.2 h the gaps were p50 3 min /
  p95 55 min / max 152 min, and one burst was **9 pushes in 8 minutes** (the merge queue
  draining). That is why §4 refuses in-progress cancellation on `push`.

---

## 2. Measured per-job wall-clock (the sample and the command)

Executed wall = **first step start → last step end**, **completed jobs only**. Two exclusions
matter, and skipping either inflates everything:

- a job that was queued and then cancelled has `started_at` but **no steps** — counting it
  reported a `changes` job at 18.8 min for 30 seconds of work on the first pass of this
  measurement;
- a **cancelled** job's elapsed is its lifetime until cancellation completed, not its work —
  the first pass also reported `test-carve-out (b)` at 31.0 min, which is a cancelled job, not a
  31-minute shard.

The per-run jobs endpoint returns only the latest attempt by default; `filter=all` returns every
attempt, which is what §1 quotes.

```bash
# per workflow: p50/p95/max executed wall for every job (completed jobs only)
wf=python-ci.yml; n=60
for id in $(gh run list -R daniel-ospina/tortoise --workflow $wf --limit $n \
              --json databaseId --jq '.[].databaseId'); do
  gh api "repos/daniel-ospina/tortoise/actions/runs/$id/jobs?per_page=100&filter=all" \
    --jq '.jobs[] | select(.conclusion == "success" or .conclusion == "failure")
          | {job: .name, att: .run_attempt,
             s: ([.steps[].started_at   | select(.!=null)] | min),
             e: ([.steps[].completed_at | select(.!=null)] | max)}'
done | jq -s '
  map(select(.s != null and .e != null))
  | map(. + {d: (((.e | fromdateiso8601) - (.s | fromdateiso8601)) / 60)})
  | group_by(.job)
  | map({job: .[0].job, n: length, max: (map(.d) | max),
         p50: (map(.d) | sort | .[length / 2 | floor]),
         p95: (map(.d) | sort | .[((length - 1) * 0.95) | floor])})'
```

Samples: `python-ci.yml` 60 newest runs (54 with visible jobs, `pull_request`-dominated —
2026-10-26T23:21Z..10-27T04:10Z), `ci.yml` 60 (10-27T00:03Z..04:10Z), `ai-review-gate.yml` 40,
`drift-guard.yml` 40. `n` = runs whose job actually executed.

**`python-ci.yml` — the merge gate's whole `needs:` set**

| job | n | p50 | p95 | max | cap |
| --- | --- | --- | --- | --- | --- |
| `test (a…i)` (9 shards) | 15–16 | 10.1–14.9 | 15.3–23.7 | **34.97** | 90 |
| `test-slow (a…e)` (5 legs) | 18–19 | 8.0–9.9 | 9.9–12.7 | 12.92 | 60 |
| `test-carve-out` (+`(b)`,`(c)`) | 20–23 | 8.3–8.6 | 8.7–9.2 | 19.85 | 30 |
| `test-record-postgres` | 23 | 1.62 | 14.61 | 15.43 | 20 |
| `test-d14-hosted-api` | 22 | 4.71 | 7.23 | 8.03 | **50 (was 40)** |
| `test-concurrency-falkor` | 18 | 2.02 | 2.71 | 2.95 | 15 |
| `test-track-b` | 17 | 0.93 | 1.09 | 1.18 | **30 (was 10)** |
| `surface-guard` | 24 | 1.07 | 1.26 | 1.35 | 10 |
| `changes` | 27 | 0.38 | 0.58 | 1.25 | 5 |
| `manifest-integrity` | 28 | 0.28 | 0.33 | 0.40 | 5 |
| `workflow-lint` | 32 | 0.19 | 0.26 | 0.30 | 5 |
| `ci-introduced-failure` (#9984) | — | not yet measured | — | — | 15 |
| **`packs-compile`** | 19 | 0.60 | 0.76 | 0.80 | **15 (was none)** |
| **`python-ci-gate`** (REQUIRED) | 34 | 0.00 | 0.02 | 0.02 | **15 (was none)** |

`ci-introduced-failure` is on the merge path (it is the one job `python-ci-gate`
delegates a failed test arm to), so its cap is what bounds a stalled required check
when the predicate job is what wedged. It has no p50/p95 here yet because it landed
after this sample was taken — take a fresh measurement (`filter=all`, completed jobs
only, §2's command) before moving the cap, rather than inferring one from a sibling.

⚠ `test-record-postgres` **false-killed on 2026-11-05 at its 20 m cap — and the remedy is
RESTORE-ONLY, not a raised cap.** The false kill is real and worth keeping on the record.
Run [41975408278](https://github.com/daniel-ospina/tortoise/actions/runs/41975408278), job
`127110027242`, head `9e32e81a3`:

| | |
|---|---|
| job starts, cap 20 m | 07:40:25 |
| pytest runs → ends **130 s, 0 failed** | 07:42:36 → 07:44:46 |
| last WORK step (postgres stopped) green | 07:44:53 |
| `Post Cache pip dependency caches` begins a ~3.6 GB save | 07:44:53 |
| killed at **79.8%** (2,885,681,152 of 3,616,385,407 sent) when the cap elapsed | 08:00:46 |
| conclusion | **`cancelled`** → `python-ci-gate` red (#5219) |

The work was green and never at risk; the whole red was the POST-step, on a diff that never touched
this job. The first response was to raise the cap to 50 over a SUM that included that save — **and
that arm is now VOID, because #11063 removed the save from this job.** It runs
`actions/cache/restore@v4` with no post step, so nothing is charged against its cap but its own
steps: **12 m in-step watchdog + ~1 m setup/provision ≈ 13 m; × 1.5 = 19.5 → 20**. Its own
`.[test,postgres]` downloads (`python-ci.yml:4704` — the `Install package + the postgres extra` step
in `test-record-postgres`; `[embeddings]`/torch deliberately absent) are a rounding error beside the
shared archive it now only restores. The cap is
**20** — what `main` carried before #10962 raised it, re-derived here rather than inherited — keeping the raised
cap would pin a per-PR concurrency slot at 50 m (#3239) against a SUM that can no longer occur.

That also retires the arm-2 ranking this row used to carry. Arm 2 exists for a job that pays the
shared archive's save on a key change; this job no longer writes a cache at all, so the ~20 m term
the 50 was derived from is not in its SUM, and the "it warms the shared key for the long legs"
argument no longer holds for it. Which rows still pay a save is the caveat below's own list —
stated there once, not restated here, which is how this pair drifted apart in the first place.

⚠ **The `~9 m` save term the saving rows use is not re-measured here.** That figure comes from the
`test-d14-hosted-api` incident (#10182). The 15.9 m above was measured on this job *before* #11063
stood its save down, on a different runner and day, and shows a 3.6 GB save can cost TWICE that.
The rows derived from `~9 m` (`test-slow`'s 60 m, the `test-d14-hosted-api` headroom statements
below, and `test-track-b`'s save arm — `python-ci.yml:3230`, `cache: pip`) are optimistic by ~7 m
against the censored 15.9 m and by ~11 m against the completed ~20 m. ⛔ **`test` is NOT one of
them:** #11063 re-derived its SUM on a MEASURED ~7.3 m startup plus an 8 m cache-write term and
split its payload, so it no longer writes the 3.6 GB archive at all (its row below is the
authority). Nor is `test-carve-out`, whose pip step has been `actions/cache/restore@v4` since #10182.
Re-deriving them is owed and is deliberately NOT done here: the two cancellations that DID
move a cap (`test-d14-hosted-api`, `test-track-b` — both worked below) were sized WITH this `~9 m`
figure rather than from a fresh save measurement, and this document's rule is to move a cap from a measurement, not from a comparison of two samples.

⚠ `test-d14-hosted-api` moved 20 → 40 (#10182), and the reason is a blind spot in the table
itself: the sample above counts only jobs whose conclusion is `success` or `failure`, so a job
killed at its own cap — conclusion `cancelled` — is **invisible here by construction**. Run
40587081433 / job 122533023160 passed its marker selection, then spent the remaining budget
uploading a 3.6 GB `~/.cache/pip` archive in the `actions/cache` save POST-step and was cancelled
at 20 m, which `python-ci-gate` reads as red (#5219). `max 8.03` therefore never saw it. The
binding term is the job's own in-step `timeout … 15m` plus a ~9 m cold save, not the 8.03 m norm —
read the cap against the SUM of the job's caps, not against its median. ⚠ Only the FIRST of those
terms moved on 2026-11-06: the in-step `15m` in the sentence above is SUPERSEDED (it is `20m` now,
sized from the COLLECT measurement), while the `~9 m` cold save did NOT move and is the value the
block below still uses. Derive this row from that block, which works the move.

⚠ **The two job caps below moved on 2026-11-06, on a measurement that closes the question the
paragraph above leaves open.** Run 42154769896 (PR #11032, head `8f32c5255`) reddened **two** required
checks with no failing test in either artifact, and each caps exactly one of the terms this section
names:

- `test-d14-hosted-api` (job 127701854811) — the in-step `15m` was eaten by **COLLECTION**: 11 m 10 s
  (04:23:22 → 04:34:32) over `collected 47070 items`, against 4 m 59 s for the 46,906-item
  collection on run 42135045069. The run then finished CLEAN — verbatim
  `256 passed, 39 skipped, 46812 deselected, 14 warnings in 855.20s (0:14:15)` — and the watchdog SIGINT-killed the process at 900 s while it unwound —
  which `pytest-attribution` reads as `INCOMPLETE`, so a green leg certified nothing (#5219).
  #10212 predicted exactly this and is what sizes the in-step cap: the worst observed COLLECT was
  11 m 10 s (against 4 m 59 s on the earlier run), on a leg whose start-to-summary wall was
  14 m 30 s → **15 → 20 m** (1.38×), and the save arm then sizes the job cap:
  20 + ~1 + ~9 = 30 m × 1.5 = 45 → **50**.
- `test-track-b` (job 127701854910) — `4 passed, 9 deselected in 44.96s`, then
  `setup-python cache: pip` tarred the SAME shared archive in its POST step and GitHub cancelled the
  job at 10 m 15 s against a 10 m cap. **This row was missed by the audit above for a structural
  reason worth keeping: its save surface is created by a third-party action**, not by an
  `actions/cache` step of its own (`actions/setup-python` with `cache: pip`,
  `python-ci.yml:3230`) — so a scan for `actions/cache` steps cannot see it, and a 1.18 m max looked
  like a comfortable 10 m. It is the only `cache: pip` site left in the workflow. Save arm:
  5 m in-step + ~1 + ~9 = 15 m × 1.5 = 22.5 → **30**.

Every other row was checked against that rule. The discriminating fact is **not** that the
remaining cold saves are small — most of these jobs install the same torch/embeddings wheels and
write the same 3.6 GB archive on a cold key — it is whether the shipped cap already clears its own
SUM. `test` cleared it with room to spare until the 2026-11-09 refresh — the refresh's
map took it over, and #11602 bought the room back by raising `fast_shards` 9 → 10; #11576 then
raised it 10 → 11 for the same reason one merge later (the history is at the end of this
parenthetical). At **`fast_shards: 11`** — the shipped shape — the row reads: 10 m
last-failed pre-phase + the emitted **26 m** per-shard
watchdog + 6 m embedder + **~7.3 m of MEASURED startup, not the ~1 m an earlier revision restated**
(`Set up job` 182 s = 3 m 02 s, 10:21:30 → 10:24:32, plus 4 m 14 s of uncapped setup steps to the
suite's first line — the 10:24:32 → 10:28:58 window of 266 s MINUS the embedder's own measured 12 s,
whose 6 m cap is already a term above; run 42000057137, job `test (d)`, `actions/jobs/127165076497`)
plus **an 8 m cache-write term (1 m prune + 7 m save)** and ~0.4 m last-failed save = **57.6 m against
90, x 1.5 = 86.5** — 3.5 m of headroom, where the refreshed map at 9 shards stood at
**63.6 m, x 1.5 = 95.5** and reded
`test_the_test_job_carries_no_unbounded_cache_post_step`. The term that moved is
the watchdog: it read **26 m** until #10999 registered its own fast suite
(`tests/test_10999_batch_story_relations.py`), **27 m** until the 2026-11-09 sweep re-weighted the fast
pool, and **32 m** at 9 shards on that refreshed map — re-packing into 10 shards took it to
**28 m** (59.6 m, x 1.5 = 89.5), because one more file in `fast_pool()` re-packs the worst
shard across a whole minute, and this paragraph restates that emission rather than owning it:
`tools/ci_selection.py --emit-push-matrix` is the authority, and `tests/test_ci_selection.py`
reds the moment the two disagree again. ⛔ **#11576 then re-broke it the same way, one merge
later**: main's `test_embed_decision_step_11086.py` and that branch's
`test_demo_session_read_exclusion_8290.py` land in `fast_pool()` together, the union
packs the worst shard at **840.2 s** (either parent alone packed 840.0 s), `ceil(840.2/60 x 2)`
rounds up to **29 m**, and the arm-2 SUM became **60.64 m, x 1.5 = 90.96 m against the 90 m cap**
— RED where both parents pass. `fast_shards: 11` packs the same map to 763.8 s → 26 m, i.e. the
57.6 m above: 3.5 m of real headroom instead of the 0.5 m S=10 had been clearing by. The cap was
NOT moved — `#3239`'s stale-head exposure is what raising it re-opens, and the SUM rose from the
POOL, not from the pre-suite terms.) The write term fits because #11063 split this job's cache by payload: `~/.cache/pip`
(3,616,385,308 B in a PR scope, the torch wheel set, 93% of the 3.88 GB payload the defect uploaded)
is now read from the shared `pip-<os>-<hash uv.lock>` key that TWO other jobs write, while
`~/.cache/uv` keeps exactly one writer here, bounded in ACCUMULATION (`uv cache prune` — it drops entries no environment reaches, not a single environment's set) and in time
(`timeout-minutes: 7`, `continue-on-error`, fail-open to a MISS). A SINGLE 3.88 GB write still does
not fit — but on the measured rates, not on an assumption: this run's degraded uplink was 0.9 MB/s
sustained (3,019,898,880 B in 3,412 s, cancelled at 77.8% of the payload), while the same runner
class completed a cold 3,616,385,308 B save in 12 m 21 s four hours later (PR 11036, job 127280762096,
`Sent 3616385308 of 3616385308 (100.0%), 7.1 MBs/sec` ≈ 4.9 MB/s end to end) — i.e. ~13 m healthy and
~57 m+ degraded against the ~8.4 m of room the SUM leaves. Splitting the payload is what brings it
inside. `tests/test_ci_selection.py::test_the_test_job_carries_no_unbounded_cache_post_step` pins
that shape (one bounded writer ON `~/.cache/uv`, no implicit post step — and no OTHER unbounded cache write anywhere in the job except the admitted `Save pytest last-failed state (#6142)`), and the watchdog comes from
`uv run python tools/ci_selection.py --emit-push-matrix`, never from `WATCHDOG_FLOOR_MIN`: 19 is the
clamp the carve-out shards land on, not the fast shards' budget, and restating a clamp as a budget
is how a constant goes stale. At the first pass FOUR rows did NOT clear it, and all four were
recorded here rather than moved: nothing had measured any of them being cancelled, and this
document's own rule is to move a cap from a measurement instead of on suspicion. THREE of the four
are still in that state — `test-slow`, `test-carve-out` and `test-concurrency-falkor`.
`test-record-postgres` was the FIRST of them (22 m against 20 on the arm-2 estimate), and it
false-killed on 2026-11-05 — which is why the job is restore-only, NOT why its cap rose: #11063 took that
save out of its SUM (13 m × 1.5 → 20), so it clears 20 and leaves the three. `test-slow` sits exactly AT its cap
(10 m embedder + 40 m run + ~1 m + ~9 m = 60 m against 60 — no headroom at all; its own ungated `~/.cache/huggingface` POST is unmeasured) and
`test-carve-out` sits at ~31.6 m against 30: 10 m embedder + 19 m shard watchdog + ~1 m, plus the 1 m 38 s
`~/.cache/huggingface` POST (`python-ci.yml:3547-3549`) — its `~/.cache/pip` save is NOT in that SUM (restore-only since #10182). The last has the least headroom of all:
**`test-concurrency-falkor` keeps 15 m against an in-step `timeout … 10m` for the run plus a
`timeout-minutes: 10` embedder provision** — ≈ 21 m of IN-STEP caps (10 + 10 + ~1 m), and it installs
`install-cpu-torch` + `.[test,embeddings]` (`python-ci.yml:2949-2954`). The ~30 m this row used to
carry also counted a `~/.cache/pip` save #11063 stood down (`python-ci.yml:2942-2946` is
`actions/cache/restore@`), and it still carries an ungated `actions/cache@v4` POST on `~/.cache/huggingface`
(`python-ci.yml:2985-2987`) that uploads on a MISS, so its SUM needs a measurement: `15 → 50` is VOID.

**`ci.yml` — five of the seven required contexts had no cap at all**

| job (required context) | n | p50 | p95 | max | cap |
| --- | --- | --- | --- | --- | --- |
| `legal-e2e` | 26 | 1.79 | 8.91 | 9.25 | **20 (was none)** |
| `docs` | 26 | 0.96 | 1.10 | **10.25** | **20 (was none)** |
| `pricing-artifact` | 25 | 0.60 | 0.73 | 0.73 | **15 (was none)** |
| `test-isolation` | 27 | 0.13 | 0.15 | 0.33 | **15 (was none)** |
| `license-surface` | 24 | 0.13 | 0.15 | 0.15 | **15 (was none)** |

The `docs` max is why the cheap required jobs are floored at 15 m and not 5 m: a one-minute job
was measured at 10.25 m — a runner-side stall of ~9 m on work that does not take 9 m. A cap under
the observed stall would false-red a REQUIRED check and cost a whole re-run cycle: the cost this
document exists to remove.

| workflow / job | n | p50 | p95 | max | cap |
| --- | --- | --- | --- | --- | --- |
| `ai-review-gate` (REQUIRED, entry gate) | 14 | 0.03 | 0.10 | 0.13 | **15 (was none)** |
| `drift-guard` (merge path, not a required context) | 22 | 0.33 | 0.37 | 0.43 | **15 (was none)** |

⚠️ **The `drift-guard` row above is HISTORICAL as of 2026-10-29:** GitHub reports that workflow
`disabled_manually` (`gh api repos/<owner>/<repo>/actions/workflows/drift-guard.yml` →
`state=disabled_manually`, `updated_at=2026-10-29T02:33:12Z`; the last run for it is `cancelled`
from `2026-10-29T02:28Z`, while dozens of `pull_request` events have fired since). `finding-provenance`
was disabled in the same window. **The numbers and the 15 m cap were measured while it still ran, so
they describe the job's cost when it runs again — they are not a live signal**; the disablement is a
deliberate state whose reversal is its own reviewed decision (it restores a merge-path gate).
Recorded here because this is where the next reader looks for why a `drift-guard` row exists and
produces no runs — and because a warn-only step added to that workflow never executes at all
(`#10597`: the `Closes-coherence` instrument was first mounted there and moved to
`.github/workflows/closes-coherence.yml`, #10634 review P1).

**Rule used.** ONE rule with two arms, and every moved row follows its arm:

- **The job's cold save is NOT the 3.6 GB torch/embeddings archive** — cap = **2 × measured max**,
  rounded to the nearest 5 minutes, floored at 15 m, for a job whose failure reds a required
  context (`docs` 20.5 → 20; `legal-e2e` 18.5 → 20; the rest floor at 15). ⚠ That predicate is
  deliberately narrower than "no cache step": a job that mounts `~/.cache/pip` on a WRITABLE
  shared key is not an arm-1 row, because on a key change its save IS the archive.
  `test-record-postgres` mounts that path (`python-ci.yml:4696-4698` — its `Restore pip dependency
  caches (#7612, RESTORE-ONLY — #11063)` step's `with:` block) but is **restore-only since #11063**,
  so it pays no save: 20 is the ARM-2 SUM minus that term (13 m × 1.5 → 20), not an arm-1 reading,
  and its retained `max` 15.43 m is save-era (§1 predates #11063), so arm 1 would say 30 — re-measure.
  Arm 1 otherwise applies to a job whose save is small because nothing restores an archive into
  its cache path, not because its `pip install` line omits torch.
- **The job's cold save IS that archive** — `2 × max` cannot see the POST-step at all, so the
  binding term is the SUM of every in-step cap plus ~1 m setup plus the observed cold save, and
  the cap is **that SUM × 1.5, rounded UP to the next 10 m**. The margin is not decoration: a save
  killed at the cap reds a required check (#5219), while 10 m of extra exposure costs only a held
  slot. The worked example is `test-d14-hosted-api`: 20 m in-step + ~1 m + ~9 m save = 30 m →
  45 → **50**, which is the number that ships (#10182, re-sized in #10212).

At the time of the first pass no cap was raised and none was lowered: `test` 90, `test-slow` 60 and
`test-carve-out` 30 carry documented derivations (setup + last-failed pre-phase + the shard's
in-step watchdog, #6135/#6142), and lowering them would re-open #798's death mode — a runner-level
kill that loses the pass/fail summary. TWO caps have moved since, both on the post-step arm:
`test-d14-hosted-api` 20 → 40 → 50 (#10182, re-sized in #10212) and `test-track-b` 10 → 30 (the
same class: an `actions/setup-python cache: pip` save). ⚠ `test-record-postgres` was raised 20 → 50
on 2026-11-05 from the first measured false-kill; #11063 then made the job restore-only and removed
the save that raise was derived from, and the cap is **put back to 20 by this change** — see the note under the table.

`python-ci.yml` is now capped on **every** job, and `tests/test_ci_selection.py` asserts both
that and the derived-required-context invariant, so the next added leg cannot silently inherit
the 360-minute default.

---

## 3. Concurrency: which run cancels which

| workflow | group key | cancel-in-progress | net effect |
| --- | --- | --- | --- |
| `python-ci` | `python-ci-<event>-<ref>` | **PR only** | On `pull_request` `github.ref` is `refs/pull/N/merge`, so a new commit on PR N cancels PR N's stale run — and only PR N's. The push arm is deliberately NOT cancelling; see §4. |
| `ci` | `ci-<event>-<ref>` | PR only (`workflow_call` serialises) | No push arm exists — this workflow triggers on `pull_request` + `workflow_call` only. |
| `ai-review-gate` | `ai-review-gate-<PR number>` | true | Already compliant; `pull_request_target` only, so every run is PR-keyed. |
| `drift-guard` | `drift-guard-<ref>` | true | Already compliant; `pull_request` + `workflow_dispatch`. |
| `post-merge-validation` | `postmerge-validation` (constant) | false | Serialises; superseded *pending* runs are still dropped by GitHub (§1), which is why `main-lint` keyed its own group on `github.sha`. |
| `deploy-hosted` | `deploy-hosted` (constant) | false | A RUNNING deploy is never cancelled (#10155; #10109 / #10152 were the stale-production class — production fell hours behind because later merges killed the deploy in flight). A PENDING one is still superseded by the newest push (§4), which is the residual `supabase-deploy`'s flip watcher reports as `UNKNOWN` rather than as a failed flip. The group must stay constant: `supabase-deploy` dispatches this workflow and then watches that run (#3627). |
| `ci-queue-health` | `ci-queue-health` | false | Deliberate: two governors would cancel each other's kept runs. |

`github.ref` is what makes this branch-scoped: `refs/pull/N/merge` on PRs, `refs/heads/main` on
push. Two different branches are two different refs, hence two different groups, and can never
cancel each other.

**Confirming it live.** `workflow_run` objects carry **no** `concurrency` field (verified:
`has("concurrency")` → `false`), so the group a run resolved to cannot be read back from the API.
Confirm the *observable* instead — a superseded run ends `cancelled`:

```bash
# PR arm: the older run of the same PR is cancelled when the head moves
gh run list -R daniel-ospina/tortoise --workflow python-ci.yml --limit 20 \
  --json databaseId,event,headBranch,status,conclusion \
  --jq '.[] | select(.event=="pull_request") | "\(.databaseId) \(.headBranch) \(.conclusion)"'
# push arm: superseded runs are cancelled having started ZERO jobs (the pending-drop rule)
gh api "repos/daniel-ospina/tortoise/actions/runs?event=push&branch=main&per_page=20" \
  --jq '.workflow_runs[] | select(.name=="Python CI") | "\(.id) \(.created_at) \(.conclusion)"'
```

---

## 4. Why `push` is deliberately not `cancel-in-progress: true`

The tempting reading is "a superseded `main` run occupies a runner". Measured, it does not, and
cancelling in progress would cost more than it saves:

1. **The pending case is already handled.** GitHub keeps one pending run per group even with
   `cancel-in-progress: false`: 17 of the 20 push runs never started a single job and only 3 ever
   started one (§1). A superseded *waiting* head already costs nothing.
2. **The alternative kills the only full matrix `main` gets.** Pushes arrive in bursts — 9 in 8
   minutes in the measured window — and with `cancel-in-progress: true` nearly every main run
   would be cut within minutes of starting. The run that survived the pending drop is exactly
   the one being killed.
3. **The fallback is not equivalent coverage.** `post-merge-validation` falls open on a
   cancelled run (#1474), but its `validate` job **excludes `tests/e2e` and
   `config/ci-surfaces.yml: slow_files`** — so a superseded tree's `test-slow` legs would run
   nowhere. That is a coverage loss on `main`, not a capacity win.
4. **It is not where the queue is.** Push runs are 1 of 18 queued `Python CI` runs; the wall in
   §1 is the PR side, and its levers are capacity and queue governance
   (`tools/ci_queue_health.py`, #9115), not a workflow's cancel policy.

A separate, **pre-existing** finding this measurement surfaced: because `post-merge-validation`
uses a constant group, **17 of 20 recent push-to-main runs never ran at all**, so post-merge
validation is missing for most merged trees — the same starvation `main-lint` was moved out of
this workflow to escape (.github/workflows/post-merge-validation.yml, and
`tests/test_reporting_workflow_concurrency.py`). That is its own defect, not one a timeout fixes.

---

## 5. When a job is killed by its bound

A timeout kill is **one fact and no diagnosis**: the job hit a number, and the step log stops
where it stopped. Read the last lines of the log before deciding.

| what the log shows | read it as | do |
| --- | --- | --- |
| `WATCHDOG: killed after Nm` banner + counts | the in-step watchdog fired — pytest hung or the suite outgrew its shard budget | **investigate**: a hanging test is a product bug; the shard budget is re-derived from `config/ci-surfaces.yml: durations` |
| log stops mid-step, no banner, no counts | the runner-level cap fired — the step never printed | **re-run once**, then investigate: one instance is a runner hiccup, two at the same step is a wedge |
| `The action has timed out` / no steps at all | the job never started executing | **re-run**; if it repeats, the bound is too small for the runner class — take a fresh measurement before moving it |
| killed on a REQUIRED context (`python-ci-gate`, `docs`, …) | merge entry is blocked, by design | re-run is the default; do NOT raise the cap to clear it without a measurement |

A green re-run is not proof a flake is gone — it is proof the tree passed once. Two kills at the
same step with the same tail is the investigation.

---

## 6. What this does NOT do

- **It does not bound queue wait.** §1. No `timeout-minutes` can. The lever there is capacity and
  queue governance (`tools/ci_queue_health.py`, #9115). That tool's `PR-BACKLOGGED` state is what
  reports the wait itself: a queued PR run older than its wait budget while PR CI is *still*
  counted as executing is no longer `OK` (#10237 — it printed `CI: OK(queued=54, pr_running=2, pr_queued=32)`
  with the oldest queued PR run at 527 minutes). The budget's default is derived from this
  document's own §1 measurement (max first-job wait 81.9 min → 90 min), so it fires past the
  measured-normal population rather than on it. `PR-UNWITNESSED` is reported rather than `OK`
  whenever the PR work is neither witnessed nor disproved, and it has THREE causes on different
  dimensions — and FIVE triggers, because a cut `pending`/`requested` leg is unwitnessed by the
  same argument as a cut queued feed and takes the same lever (it just is not a *fourth* cause),
  and because the unreadable-stamp cause applies to the WHOLE waiting population
  (`queued + pending + requested`), not to the queued leg alone:
  - the queued feed was cut at its page cap **and no PR-event run is executing** — raise
    `--max-pages` to read further back;
  - a WAITING PR-event run (`queued`/`pending`/`requested`) has no measurable age for its
    CURRENT ATTEMPT, so the wait cannot be measured at all. ⛔ ONE OWNER for that rule:
    `QueuedRun.wait_age_seconds`, which abstains on THREE routes — an unreadable `created_at`
    (attempt 1), an unreadable `run_started_at`, or an `attempt >= 2` run with NO start stamp
    (where falling back to `created_at` would report a fresh attempt as an old wait). So a
    perfectly READABLE `created_at` does NOT rule this cause out. There is no lever here; a
    stamp GitHub never wrote cannot be recovered, so retry the reading;
  - the RUNNING CENSUS is unwitnessed — every run counted in `pr_running` was unmeasured. This
    shows on the state line as `census=unmeasured` **whenever the counted census is entirely
    unmeasured, on ANY verdict that can carry that shape** (`PR-BACKLOGGED`, `PR-UNWITNESSED`
    via an unreadable stamp or a cut `pending`/`requested` leg, and an `OK` from a non-probing
    caller — `PR-STARVED`/`FLOOD` cannot: they require `pr_running == 0`, and an unmeasured run
    is always counted). ⛔ Only its DIAGNOSIS is scoped: the two-sub-cause remedy sentence and
    its annotation arm run only on the `PR-UNWITNESSED` degradation. The two counts themselves
    are in `--json` on EVERY verdict (`pr_running_unmeasured_unprobed` / `..._unread`), so open
    the payload on any verdict — the annotation names the one that applies only where it runs:
    - **the probe budget never REACHED the run** — raise `--max-job-probes`. The budget is spent
      oldest-first, so the surplus is the *newest* runs;
    - **the jobs read did not PROVE them idle** — RETRY the reading. This bucket now has
      several causes and they share the one lever: the read ITSELF FAILED; the read returned
      nothing USABLE (an empty, partial, malformed, or other-attempt job list — none of which is
      a failed read, so the old "read FAILED" wording sent operators hunting a transport fault
      on a surface that answered correctly); the read SUCCEEDED and returned `False` on a run
      that had not been STILL for `JOB_SETTLE_SECONDS` (300 s), so an all-`completed` job list
      was not yet a proof (a DYNAMIC matrix creates its shards only after its parent job
      finishes, so retrying immediately reproduces the same downgrade — retry once the runs
      have settled); or a proof whose completeness could not be established. A larger budget
      does not help: it only probes more runs the same way (the failure is not sticky — every
      reading re-probes from scratch — it is just invisible, since an unproven read abstains
      into `has_live_job = None` by design).

    There is deliberately **no** "repair the frozen run" remedy here, and that is not an
    omission: a frozen run — `in_progress` with every job `completed` **and its own
    `updated_at` motionless for `JOB_SETTLE_SECONDS` (300 s)** — is the *proof* that EXCLUDES
    it from `pr_running` (see the `excluded` sentence), so it can never make the census
    unmeasured. ⛔ The stillness half is load-bearing: this repo's test matrix is DYNAMIC
    (`python-ci.yml` instantiates `test`/`test-slow`/`test-carve-out` from
    `fromJSON(needs.changes.outputs.*_matrix)`), so between the parent job finishing and the
    shards being created the jobs endpoint answers with ONE `completed` row while the run is
    about to execute — an all-`completed` list alone would subtract a run that is working.
    A run whose stillness cannot be proven abstains instead (counted, and reported in
    `pr_running_unmeasured`). Such a run is identified *by being probed*, i.e. by the budget
    lever above, and neither `/cancel` nor `/force-cancel` accepts a repair. It is **NOT
    `--max-pages`**.

  In every case an unmeasured dimension is not a measured-healthy one.
- **It does not reduce jobs per run.** A `python-ci` run is 22 jobs (max 30) by construction; with
  ~17 concurrent PR heads that is the arithmetic behind the p95 77-minute first-job wait, and it
  is a sharding/capacity question (#5215, #9539), not a timeout question. It does now bound how
  many of those jobs ONE run may hold at once — §7 — which moves the queue, not the work. It bounds
  a `batch-fold` run too: 3 per matrix, so a fold with dedicated capacity for all 9 wall-carrying
  legs still runs them 3 at a time (§7, "What the cap does NOT exempt").
- **It does not restore post-merge validation.** §4 — 17 of 20 recent push-to-main runs never
  ran, for reasons that predate this change.
- **It does not make a superseded PR cheap.** Cancelling the superseded run frees the slot it was
  holding, not the work it had already done.

---

## 7. Matrix fan-out: how many slots one run may hold (`max-parallel`)

No matrix in this repo declared `strategy.max-parallel` before #6744, so a single PR's whole shard
set — up to 17 matrix jobs (9 fast + 5 slow + 3 carve-out) — was free to **start** at the same
instant, on top of the **8** non-matrix jobs that are co-eligible with them. The fan-out was in
simultaneous RUNNING slots, not in enqueueing: GitHub creates every matrix cell either way (see
"The re-derivation cannot use per-job queue wait", below), so the cap changes only how many of
those already-created cells may run at once — and that is precisely what let one run hold the
pool. (It is 8 and not 11 because four of the 12 non-matrix jobs cannot run alongside the
matrices: `changes` is their own prerequisite, `canary-streak` needs `test` and is `push`-only,
and `ci-introduced-failure` / `python-ci-gate` both `need:` the matrices.)

⛔ **Scope of that claim: `python-ci`'s three shard matrices.** It is not a repo-wide audit, and
reading it as one would be wrong — `.github/workflows/mirror-images.yml` still declares a
`matrix` with no cap. That one is out of scope rather than overlooked: it is not PR-triggered
(`workflow_dispatch` plus a nightly `schedule`), so it has no per-PR fan-out to bound, and its
`matrix` is a static 3-target `include:` list (mirroring registry images), not a shard set that
grows with the suite. It is pinned to a GitHub-hosted `ubuntu-24.04` runner **by choice** (its own
egress IP keeps these registry pulls off the fleet's shared quota) — but that is not a guarantee it
stays off the measured pool: `mirror-images.yml` records that since the 2026-10-29 mitigation the
x64 pool can answer `ubuntu-24.04` under `CI_RUNNER_OS_LABEL=on|auto`, so while that mitigation is
engaged this job is POOL-ELIGIBLE and can take up to 3 of the 12 measured slots. It is
recorded here only so a later reader does not mistake this section for "every matrix in the repo
is bounded".

**Measured 2026-10-31, ~03:30Z**, 9 `python-ci` runs (`pull_request`, 2026-10-30T21:48Z ..
10-31T02:54Z), jobs API `filter=all`, completed jobs only:

| reading | value |
| --- | --- |
| runners registered / online | 12 / 12 (11 busy, 1 idle at read) |
| per-PR PEAK simultaneous jobs | **p50 11, max 18** (92%–150% of the pool) |
| per-PR peak of the FAST matrix alone | p50 8, **max 9** (75% of the pool) |
| instants with ≥6 jobs running where ONE run held ≥50% of them | **352 of 367** (median share 0.73, max 1.00) |
| repo queue at the read | 35 queued runs, 3 in-progress runs |
| per-job queue wait (`created_at` → `started_at`) | p50 5.4 m, p95 21.3 m, max 27.3 m |
| fast-shard wall | p50 10.5 m (5.8–15.7) |
| fast-shard START stagger within a run | 4.0–23.9 m (p50 ~10) — the pool already delivers 2–4 waves |
| slow-leg wall / carve-out wall | p50 8.9 m (3.7–12.6) / p50 8.8 m (7.5–9.4) |

The monopolisation row is the one that names the queue: it is not that jobs are slow, it is that
one run owns the pool, so every other PR waits for that run's shard set to drain.

**The cap is `3` on all three matrices, and it is pinned from both sides rather than chosen:**

1. **Do not invent waves the pool already creates.** `ceil(fast_shards / C) <= 4` — the measured
   stagger (4.0–23.9 m against a p50 10.5 m wall) says the pool already runs these shards in 2–4
   waves, so a cap needing more than 4 is strictly added wall. `C >= 3`.
2. **No single PR may hold the pool.** The three matrices start after `changes` and overlap, so a
   PR's maximum **matrix** hold is `C_fast + C_slow + C_carve`; keeping 3 of the 12 measured
   runners outside any one PR's matrix bound gives `sum <= 12 - 3 = 9`, i.e. `C <= 3` with equal
   caps. This reserves MATRIX headroom only — the same PR's eligible non-matrix jobs can still take
   those runners, so it bounds one PR's matrix fan-out and is not a guarantee that three other PRs
   always get a runner (see "What the cap does NOT bound: the total").

With ONE cap for all three matrices — the choice this section makes — only `C = 3` satisfies both:
invariant 1 forces `C >= 3` and invariant 2 forces `C <= 3`. ⛔ The two inequalities alone do NOT
pin 3: an unequal split can satisfy them (3/2/3 holds 8 matrix slots and needs at most 3 waves, and
`tests/test_ci_selection.py` accepts it), so what rules those splits out is the choice of a single
cap, not the invariants. An interval simulation over the same 9 runs — each job keeps its own
observed start as an earliest bound, so the delta is an **upper** bound: it does not credit back
the queue the PR no longer waits behind — gives per-PR peak / matrix-band delta:

| cap (fast/slow/carve) | per-PR peak (p50 / max) | matrix-band end delta (p50 / max) |
| --- | --- | --- |
| 2 / 2 / 2 — ⛔ rejected by invariant 1 (`ceil(10/2) = 5` waves) | 8 / 9 | +31.8 m / +38.5 m |
| **3 / 3 / 3 (shipped)** | **9 / 12** | **+17.9 m / +23.2 m** |
| 4 / 4 / 3 — ⛔ rejected by invariant 2 (`sum = 11 > 12 - 3`) | 10 / 13 | +10.1 m / +14.4 m |
| 5 / 3 / 3 — ⛔ rejected by invariant 2 (`sum = 11 > 12 - 3`) | 10 / 12 | +8.1 m / +9.6 m |
| unset | 11 / 18 | 0 / 0 |

⛔ **The `2 / 2 / 2` row is the one candidate this section's own pin rejects.** `ceil(10 / 2)` = 5
waves exceeds the `max_waves = 4` ceiling invariant 1 pins
(`tests/test_ci_selection.py::test_every_matrix_bounds_its_fan_out_below_the_measured_pool`), so a
re-derivation from a fresh measurement that picks the row with the most pool headroom (`8 / 9`) is
redded by the pin shipped in this same section. The two rows that fail the other side are the
mirror case: `4 / 4 / 3` and `5 / 3 / 3` each hold 11 matrix slots, past `12 - 3 = 9`, so they fail
invariant 2.

**The accepted regression has two components, and only the run-level number judges it.** The slow
matrix gains its second wave (`ceil(5/3)` = 2): one wave of the measured slow-leg wall is ~8.9 m
(row above), so its band goes ~9 m → ~18 m (1 wave → 2), still narrower than
the fast band under the same cap (~31.5 m), so it does not become the run's widest leg. **The wall
guard measures that band, not the per-leg share**: `slow_wall_projection()` takes the job's
`max-parallel` and reports `waves x (max leg + setup)` — `8.63 m` per leg → **`17.26 m`** from the
committed `durations` map — and
`test_slow_shards_are_duration_packed_and_meet_the_wall_target` holds it to
`SLOW_LEG_CAPPED_WALL_TARGET_MIN` (20 m). That constant is a documented departure from the 10 m
per-leg guideline: a guard that stopped at the per-leg share stayed GREEN while the job's wall was
double it, which is the false PASS this pin exists to prevent. The fast
matrix's band is pinned at `ceil(11/3)` = **4** waves after #11576 raised `fast_shards` to 11
(it was `ceil(10/3)` = 4 at the 10-shard shape and `ceil(9/3)` = 3 at the 9-shard measurement) — exactly the 2–4 wave band invariant 1
protects, so the pin stays GREEN while the accounted regression grows by a wave. That matters for
the arithmetic below: the `+17.9 m p50 / +23.2 m max` band delta was simulated on the 3-wave shape,
so it is a LOWER bound on the shipped 4-wave one, not a measurement of it — re-derive before
quoting it as the current figure. No saving is claimed on the runs where it took the full 4. The pair is what the simulation's **matrix-band end delta
of +17.9 m p50 / +23.2 m max** (table above) records, and that — not the slow matrix's ~9 m in
isolation — is the figure a keep-vs-rollback reading must use (see "The re-derivation cannot use
per-job queue wait", below). Capping slow at
5 to keep its single wave would put a PR's maximum **matrix** hold at 11 of 12 runners and fail
invariant 2.

⛔ **The clock this is judged on.** `cancel-in-progress` is keyed per PR (`refs/pull/N/merge`,
§3), so a cap protects **other** PRs from this one. It does **not** make the capped PR finish
sooner, and a later reading that judges it that way is reading the wrong clock: the target is
"no single PR holds more than 9 **matrix** slots", not "each PR is faster".

⛔ **What the cap does NOT bound: the total.** `max-parallel` limits matrix legs only. This
workflow also has 12 non-matrix jobs, and **8 of them are eligible to run concurrently with the
matrices**: `manifest-integrity`, `surface-guard` and `workflow-lint` carry no `needs:` at all,
and `test-concurrency-falkor`, `packs-compile`, `test-track-b`, `test-d14-hosted-api` and
`test-record-postgres` need only `changes` — the same trigger the three matrices wait on.
(`changes` is itself a fourth `needs:`-less job and is excluded from the 8 deliberately: the
matrices wait on it, so it cannot overlap them.) On a full selection a PR therefore **requests 30
jobs** (19 matrix legs + 11 non-matrix — the 12th, `canary-streak`, is `push`-gated), of which at
most **17 can be in flight at once** (9 capped matrix slots + the 8 eligible non-matrix). **The
pool does not bound that** — it bounds only the subset that lands on it. `test` and
`test-carve-out` ask for `ubuntu-latest`, and `test-slow` pins `ubuntu-24.04` — but on this repo
**both** labels can be answered by the self-hosted pool while their mitigations are engaged
(`docs/ops/ci-runner-x64.md`, "the label contract": `ubuntu-24.04` is "GitHub-hosted, plus — as a
temporary mitigation since 2026-10-29 — the self-hosted pool"), so all three matrices are
pool-eligible. What stops the pool being a hard ceiling is the other half of label matching: a
hosted runner answers those same labels too, so a leg that cannot get a pool slot is served
outside it. That is how a run was measured at **18 simultaneous jobs against a 12-runner pool**
(table above): the overflow is answered outside the pool. The cap is a fairness bound on the FAN-OUT (which is
what was unbounded, and what made one PR own the pool); it is not a total concurrency budget, and
reading 9 as one would understate the hold by nearly half (it is 9 of 17).

⛔ **What the cap does NOT exempt: a `batch-fold` run.** The cap is a bare literal on all three
matrices, so it applies to a fold's validation run exactly as to any other PR. That matters because
a fold is the one run this repo deliberately gives extra capacity to:
`docs/ops/batch-integration.md` mechanism 3 ("CI priority for the fold") gives the fold's matrix
rows their own runner class, and `tools/ci_selection.py::fast_matrix_include` states that
exemption's scope in its own words — "A fold gets priority on the nine legs that carry the wall",
the fast matrix's 10 shards minus the `canary_producer` row, which stays on the hosted label for the
streak. Those nine are legs this cap bounds, so while the class seam is engaged (it is OFF by
default: with `CI_FOLD_RUNNER_LABELS` unset every row resolves to its own runner) a fold with
dedicated capacity for all 9 still runs them **3 at a time**, and its own validation run carries the
matrix-band delta above (+17.9 m p50 / +23.2 m max). This is recorded as a KNOWN COST of #6744, not
as a decision that a fold run should be capped — the cap's justification ("no single PR may hold the
pool") is a fairness bound between concurrent PRs, and a fold is not competing with them that way.
Exempting one would need an expression-valued `max-parallel` — the key is JOB-level, not per-row
data like `runs-on`, so a per-row exemption is impossible — and the one expression form this repo has
evidence about is rejected: a `needs` output is a STRING, and `max-parallel` refuses a string result
at evaluation time (`Error when evaluating 'strategy' for job … Unexpected value`, reported against
`max-parallel: ${{ needs.<job>.outputs.count }}`, github/orgs/community#75886). ⛔ That is narrower
than "no expression can ever work": a numeric-valued expression is reported to be accepted elsewhere
and has NOT been measured here, so the coarse `jobs.<job_id>.strategy` context row is not evidence
either way. The exemption is therefore **not taken as a decision** — this run's fold cost is booked as
a known cost of #6744, and the cap is a fairness bound between concurrent PRs — not because the
platform is proven to forbid it. This paragraph exists so a silent exemption cannot be introduced as
a "tidy-up" without saying so.

⛔ **Why it cannot false-red a shard.** `max-parallel` limits start order only. Every bound in
these jobs is measured from runner pickup — the in-step watchdog is started INSIDE the job — so a
start delayed by the cap cannot consume a shard's budget. The matrix itself is untouched (the
shard set is still the selector's `fast_matrix` / `slow_matrix` / `carve_matrix`), and a `needs:`
on a matrix job waits for **every** leg regardless of ordering (#5219) — so `python-ci-gate`
cannot conclude before a shard it needs has run. Both halves are pinned, and by three separate
pins rather than two — the caps are a different property from the two false-red halves:

- the per-shard watchdog sitting INSIDE the job, and the matrix coming from the selector rather
  than a workflow literal →
  `tests/test_ci_selection.py::test_the_workflow_matrix_is_derived_and_the_watchdog_is_per_shard`;
- the aggregate keying on the job id, never on a leg name → `::test_no_required_check_names_a_fast_leg`;
- the three caps themselves (presence, `ceil(shards/cap) <= 4`, and `sum <= 9`) →
  `::test_every_matrix_bounds_its_fan_out_below_the_measured_pool`.

**Re-derive before moving any of the three numbers**: re-read the pool (`gh api
repos/<owner>/<repo>/actions/runners`), and re-measure the per-shard wall with §2's command — but
NOTE that §2's command keys on `.steps[].started_at|completed_at`, so it yields EXECUTED WALL and
not the within-run start stagger; the stagger (4.0–23.9 m, the 2–4 wave claim invariant 1 rests
on) needs the JOB-level `started_at` of one run's legs (`…/runs/<id>/jobs`), which §2 never reads.

If the pool grows, invariant 2 permits a larger cap — and that move is not free: the
`measured_pool` literal in the test is the one input that must be edited with the caps (below). If
a shard's wall grows, the accepted regression grows with it, because each added wave costs one
shard wall: recompute `waves × wall` for each capped matrix (at cap 3: fast `3 × 10.5 m ≈ 31.5 m`,
slow `2 × 8.9 m ≈ 17.8 m`, carve-out `1 × 8.8 m ≈ 8.8 m`) and compare each against that same
matrix's uncapped band before trusting the +17.9 m figure above.

⛔ **Re-measuring the pool is not only a reason to move a cap — it is an edit to the pin.**
`tests/test_ci_selection.py` holds `measured_pool = 12` as a **literal** and asserts
`sum(caps) <= measured_pool - reserved_for_others`. The test pins the INVARIANTS; the pool they
are measured against is the one number in it that is measured rather than derived, so it has to
move **in the same change as the caps**. Landing a permitted re-tune without it (4/4/4 against a
16-runner pool, say) satisfies both invariants and still reds the required check on the stale
constant.

⛔ **The re-derivation cannot use per-job queue wait to see the cost this creates.** This section's
own measurement table records a per-job queue wait (`created_at → started_at`, p50 5.4 m / p95
21.3 m) — note that §2 does NOT: §2 measures EXECUTED WALL (`first step start → last step end`),
a different clock, and it never reads `created_at`. **GitHub creates every cell of a direct matrix
in one batch, all sharing one `created_at`, when the job becomes eligible** (after its `needs:`
resolves — see the measurement below), and `max-parallel` gates which of those already-created
cells may **start** — so on a capped matrix this interval now counts cap-induced
queueing **and** runner-availability wait together, and it can attribute neither. A capped PR's
per-job wait will therefore **rise**, not fall: a leg held for a slot is queued for the whole
wave ahead of it. The clock for this change is the **matrix band** — the first leg's `started_at`
to the last leg's `completed_at` within one matrix job — compared against the same band on an
uncapped run. That is the quantity the simulation's `matrix-band end delta` column is expressing
(+17.9 m p50 / +23.2 m max at cap 3), and the only number a later reading should judge the
regression against.

The read that confirms the shape on this repo's first capped run — one shared `created_at` across
all 9 fast legs, with `started_at` spread over the waves, is this text; distinct `created_at`
values that track `started_at` would mean these cells are created lazily and this paragraph is
wrong for this workflow:

```bash
gh api "repos/daniel-ospina/tortoise/actions/runs/<capped_run_id>/jobs?per_page=100&filter=all" \
  --jq '.jobs[] | select(.name|startswith("test ")) | {name, created_at, started_at, completed_at}'
```

⛔ **That falsifier is the whole of the claim, and it is read here for ONE shape: a DIRECT matrix**
(all three of these jobs declare `runs-on` and `steps`). Nothing in this repo measures cell-creation
timing for any other shape, so none is asserted: a matrix job that calls a reusable workflow through
a job-level `uses:` would have to be re-read with the command above before this paragraph could be
applied to it.

**Taken 2026-11-05 on the first capped run** (`pull_request`, head `80d059348f68`, run
`42034142918`, 29 job records — the 26 accounted for below plus the 3 dependents (`canary-streak`,
`ci-introduced-failure`, `python-ci-gate`), jobs API `filter=all`) — the shape holds, measured rather
than assumed; the batch is real, but it is created when the job becomes eligible, NOT at the run's
start:

| reading | value |
| --- | --- |
| the 4 `needs:`-less jobs (`changes`, `manifest-integrity`, `surface-guard`, `workflow-lint`) | one `created_at`: `14:57:11Z` |
| every cell of all 3 matrices + the 5 jobs that need only `changes` (22 jobs) | one `created_at`: `15:38:51Z` |
| the run's own creation | `13:15:19Z` — so its first job was created 1 h 42 m later |
| fast legs' `started_at` (4 of 9) | `16:40:46`, `16:45:34`, `16:46:13`, `17:36:36` |
| their wait, `created_at` → `started_at` | 62, 67, 67, **118** minutes |

So the cells are created in ONE batch that shares a timestamp, and `max-parallel` gates which may
start — both halves of the paragraph above are confirmed. What the batch is NOT is "the run's
start": the cells are created when the job becomes eligible, i.e. **after its `needs:` resolves** —
2 h 23 m into this run, not at `13:15:19Z`. The spread over ~3 waves is still not observable from
this read (only 4 of 9 fast legs had started when it was taken, and they were spread over 1 h), so
a later reader wanting the BAND must take it from a run whose matrix drained.

⛔ **And the per-job wait on a QUEUED leg reads exactly 0, which is a trap the table above cannot
show.** GitHub does not leave `started_at` null for a cell it has created but not started — it
fills it with `created_at`. Measured on the same run: `test (e)`–`test (i)` were `status: queued`
and every one reported `created_at == started_at == 15:38:51Z`, i.e. a wait of **0**, while the
true wait on them was already ~2 h and counting (`test (d)` read 0 in the same snapshot and 118 m
once it actually started). This biases a matrix's per-job wait the WRONG way — the legs
contributing the most wait contribute zero — so keep `conclusion` `success`/`failure` (§2's own
population, and the one `test (e)`–`test (i)` failed) or discard a `started_at` equal to
`created_at` before averaging, and do not read a low average as relief. ⛔ `status` alone does NOT
filter them: once the run ends those legs read `status: completed`, `conclusion: cancelled`, 0
steps and `started_at == created_at`, so a `status == "completed"` filter keeps exactly the
population this paragraph is warning about.
