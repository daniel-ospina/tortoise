---
title: "CI wall-clock bounds on the merge path"
type: operations
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: ci-wall-clock, ci-headroom-guards
aboutObjects: tortoise, .github/workflows/python-ci.yml, .github/workflows/ci.yml, .github/workflows/ai-review-gate.yml, .github/workflows/drift-guard.yml
created: 2026-10-27
updated: 2026-10-31
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
360-minute default that holds a required check pending for six hours — and that is the hole §3
closes.

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
| `test-record-postgres` | 23 | 1.62 | 14.61 | 15.43 | 20 ⚠ thin |
| `test-d14-hosted-api` | 22 | 4.71 | 7.23 | 8.03 | **40 (was 20)** |
| `test-concurrency-falkor` | 18 | 2.02 | 2.71 | 2.95 | 15 |
| `test-track-b` | 17 | 0.93 | 1.09 | 1.18 | 10 |
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

⚠ `test-record-postgres` is the one pre-existing cap that sits closest to its work (15.43 m
against a 20 m cap). Left unchanged — a measured max under its cap is not a defect — but if it
ever false-kills, the number to move is 20 → 40 from a fresh measurement, not on suspicion, for
the reason below.

⚠ **It IS exposed to the post-step class below — conditionally, on the cache key changing.** What
keeps its save small is not what the job installs; it is whether the `actions/cache` lookup hits
the PRIMARY key exactly. A `restore-keys` partial match does not: `restoreImpl.ts:45` extracts the
matched archive into `~/.cache/pip`, and `saveImpl.ts:50` skips the save POST-step **only** on an
exact primary-key match, so anything else falls through to `cache.saveCache(…)` and re-tars
everything the restore just put there. On a `uv.lock` bump every pip site gets a new primary key
at once, and this job — p50 1.62 m, the fastest of them — is the likely payer: it restores the
shared 3.6 GB archive and re-saves it, its own `.[test,postgres]` downloads
(`python-ci.yml:3635`; `[embeddings]`/torch deliberately absent) being a rounding error beside it.
Its exposure is therefore 12 m in-step + ~1 m setup + ~9 m save ≈ 22 m against 20 → `cancelled` →
red (#5219), on an event rather than on every run. Moved only from a measurement — nothing has
measured it cancelled — but the number to move it TO follows the arm-2 rule, not arm 1: 22 × 1.5 →
**40**, not 2 × 15.43 = 30, because the save this row pays on a key bump is the SHARED archive and
not its own downloads.

⚠ `test-d14-hosted-api` moved 20 → 40 (#10182), and the reason is a blind spot in the table
itself: the sample above counts only jobs whose conclusion is `success` or `failure`, so a job
killed at its own cap — conclusion `cancelled` — is **invisible here by construction**. Run
40587081433 / job 122533023160 passed its marker selection, then spent the remaining budget
uploading a 3.6 GB `~/.cache/pip` archive in the `actions/cache` save POST-step and was cancelled
at 20 m, which `python-ci-gate` reads as red (#5219). `max 8.03` therefore never saw it. The
binding term is the job's own in-step `timeout … 15m` plus a ~9 m cold save, not the 8.03 m norm —
read the cap against the SUM of the job's caps, not against its median.

Every other row was checked against that rule. The discriminating fact is **not** that the
remaining cold saves are small — most of these jobs install the same torch/embeddings wheels and
write the same 3.6 GB archive on a cold key — it is whether the shipped cap already clears its own
SUM. `test` clears it with room to spare (10 m last-failed pre-phase + the emitted 27 m per-shard
watchdog + 6 m embedder + ~1 m setup + ~9 m save = 53 m against 90) — and that watchdog comes from
`uv run python tools/ci_selection.py --emit-push-matrix`, never from `WATCHDOG_FLOOR_MIN`: 19 is the
clamp the carve-out shards land on, not the fast shards' budget, and restating a clamp as a budget
is how a constant goes stale. FOUR rows do NOT clear it, and all four are recorded here rather than
moved: nothing has measured any of them being cancelled, and this document's own rule is to move a
cap from a measurement instead of on suspicion. `test-record-postgres` is the first — 22 m against
20, on a key bump only, and `20 → 40` if it false-kills (above). `test-slow` sits exactly AT its
cap (10 m embedder + 40 m run + ~1 m + ~9 m = 60 m against 60 — no headroom at all) and
`test-carve-out` is 9 m past it (10 m embedder + 19 m shard watchdog + ~1 m + ~9 m = 39 m against
30). The last has the least headroom of all:
**`test-concurrency-falkor` keeps 15 m against an in-step `timeout … 10m` for the run plus a
`timeout-minutes: 10` embedder provision plus the same ~9 m cold `~/.cache/pip` save** — ≈ 30 m —
because it installs `install-cpu-torch` and `.[test,embeddings]` (`python-ci.yml:2268-2273`) and so
writes the same 3.6 GB class of archive as d14 on a cold key. If it does false-kill, the number to
move is 15 → 50: 30 m × the 1.5 margin ≈ 45, rounded up to the next 10.

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

**Rule used.** ONE rule with two arms, and every moved row follows its arm:

- **The job's cold save is NOT the 3.6 GB torch/embeddings archive** — cap = **2 × measured max**,
  rounded to the nearest 5 minutes, floored at 15 m, for a job whose failure reds a required
  context (`docs` 20.5 → 20; `legal-e2e` 18.5 → 20; the rest floor at 15). ⚠ That predicate is
  deliberately narrower than "no cache step": `test-record-postgres` DOES mount `~/.cache/pip` on a
  writable shared key (`python-ci.yml:3624-3631`) and is therefore NOT an arm-1 row — on a key bump
  its save IS the archive (see the note under the table). Arm 1 applies to a job whose save is
  small because nothing restores an archive into its cache path, not because its `pip install`
  line omits torch.
- **The job's cold save IS that archive** — `2 × max` cannot see the POST-step at all, so the
  binding term is the SUM of every in-step cap plus ~1 m setup plus the observed cold save, and
  the cap is **that SUM × 1.5, rounded UP to the next 10 m**. The margin is not decoration: a save
  killed at the cap reds a required check (#5219), while 10 m of extra exposure costs only a held
  slot. The worked example is `test-d14-hosted-api`: 15 m in-step + ~1 m + ~9 m save = 25 m →
  37.5 → **40**, which is the number that ships (#10182).

At the time of the first pass no cap was raised and none was lowered: `test` 90, `test-slow` 60 and
`test-carve-out` 30 carry documented derivations (setup + last-failed pre-phase + the shard's
in-step watchdog, #6135/#6142), and lowering them would re-open #798's death mode — a runner-level
kill that loses the pass/fail summary. The one cap moved since is `test-d14-hosted-api` 20 → 40
(#10182), on the post-step arm.

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
  executing is no longer `OK` (#10237 — it printed `CI: OK(queued=54, pr_running=2, pr_queued=32)`
  with the oldest queued PR run at 527 minutes). The budget's default is derived from this
  document's own §1 measurement (max first-job wait 81.9 min → 90 min), so it fires past the
  measured-normal population rather than on it. Where that wait cannot be MEASURED at all — the
  queued PR runs carry no readable `created_at` — the tool reports `PR-UNWITNESSED` rather than
  `OK`: an unmeasured wait is not a measured-healthy one.
- **It does not reduce jobs per run.** A `python-ci` run is 20 jobs (max 28) by construction; with
  ~17 concurrent PR heads that is the arithmetic behind the p95 77-minute first-job wait, and it
  is a sharding/capacity question (#5215, #9539), not a timeout question. It does now bound how
  many of those jobs ONE run may hold at once — §7 — which moves the queue, not the work.
- **It does not restore post-merge validation.** §4 — 17 of 20 recent push-to-main runs never
  ran, for reasons that predate this change.
- **It does not make a superseded PR cheap.** Cancelling the superseded run frees the slot it was
  holding, not the work it had already done.

---

## 7. Matrix fan-out: how many slots one run may hold (`max-parallel`)

`strategy.max-parallel` was unset on every matrix in this repo until #6744, so a single PR
enqueued its whole shard set — 17 matrix jobs (9 fast + 5 slow + 3 carve-out) — at the same
instant, on top of 11 singleton jobs.

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
   PR's maximum hold is `C_fast + C_slow + C_carve`; reserving 3 of the 12 runners so three other
   PRs can always make progress gives `sum <= 12 - 3 = 9`, i.e. `C <= 3` with equal caps.

Only `C = 3` satisfies both. An interval simulation over the same 9 runs — each job keeps its own
observed start as an earliest bound, so the delta is an **upper** bound: it does not credit back
the queue the PR no longer waits behind — gives per-PR peak / matrix-band delta:

| cap (fast/slow/carve) | per-PR peak (p50 / max) | matrix-band end delta (p50 / max) |
| --- | --- | --- |
| 2 / 2 / 2 | 8 / 9 | +31.8 m / +38.5 m |
| **3 / 3 / 3 (shipped)** | **9 / 12** | **+17.9 m / +23.2 m** |
| 4 / 4 / 3 | 10 / 13 | +10.1 m / +14.4 m |
| 5 / 3 / 3 | 10 / 12 | +8.1 m / +9.6 m |
| unset | 11 / 18 | 0 / 0 |

The accepted regression is the slow matrix's second wave (`ceil(5/3)`): its band goes ~13 m →
~18 m, still narrower than the fast band under the same cap (~31.5 m), so it does not become the
run's widest leg. Capping slow at 5 to keep its single wave would put a PR's hold at 11 of 12
runners and fail invariant 2.

⛔ **The clock this is judged on.** `cancel-in-progress` is keyed per PR (`refs/pull/N/merge`,
§3), so a cap protects **other** PRs from this one. It does **not** make the capped PR finish
sooner, and a later reading that judges it that way is reading the wrong clock: the target is
"no single PR holds more than 9 slots", not "each PR is faster".

⛔ **Why it cannot false-red a shard.** `max-parallel` limits start order only. Every bound in
these jobs is measured from runner pickup — the in-step watchdog is started INSIDE the job — so a
start delayed by the cap cannot consume a shard's budget. The matrix itself is untouched (the
shard set is still the selector's `fast_matrix` / `slow_matrix` / `carve_matrix`), and a `needs:`
on a matrix job waits for **every** leg regardless of ordering (#5219) — so `python-ci-gate`
cannot conclude before a shard it needs has run. Both halves are pinned by
`tests/test_ci_selection.py::test_every_matrix_bounds_its_fan_out_below_the_measured_pool` and
`::test_no_required_check_names_a_fast_leg`.

**Re-derive before moving any of the three numbers**: re-read the pool (`gh api
repos/<owner>/<repo>/actions/runners`), the per-shard wall and the start stagger with §2's
command. If the pool grows, invariant 2 permits a larger cap; if a shard's wall grows past
`4 × cap × wall` catching the carve-out, invariant 1 is the one that moves.

