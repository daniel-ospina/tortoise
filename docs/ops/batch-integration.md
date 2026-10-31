---
title: "Batch integration — the gate is the batch, not the PR"
type: operations
domain: platform
doc_status: live
subjects.team: epistemic-team
aboutSubjects: batch-integration, ci-wall-clock, ci-headroom-guards
aboutObjects: tortoise, "#10399", .mergify.yml, .github/workflows/python-ci.yml, .github/workflows/post-merge-validation.yml, config/ci-surfaces.yml
created: 2026-10-31
updated: 2026-10-31
---

# Batch integration — the gate is the batch, not the PR

**Status:** adopted 2026-10-31 by owner ruling — decision record: issue **#10399**.
**Supersedes:** the post-merge full suite as the *gate*. `python-ci.yml`'s `push: branches: [main]`
leg (and `post-merge-validation.yml`, which is warn-only by its own header) still runs, but it is now a
**detector**, not the gate: a red main is a process failure, not an expected state.

**OVERRIDES:** ci-main.yml's post-merge full suite — the suite now gates the batch BEFORE main, because
a post-merge suite cannot prevent a red main.

> The ruling names `ci-main.yml`. This repository's post-merge full suite is spelled
> `python-ci.yml` (`on: push: branches: [main]`) plus `post-merge-validation.yml`; there is no
> `ci-main.yml` file here. The override is against the **model** — "the full suite runs after the
> merge" — whichever workflow carries it.

## The invariant

> **`main` only ever receives a tree that has already passed the full suite.**

Everything below is in service of that one sentence. A tree that passed the suite is the only thing
that may be landed on `main`; a tree that has not run is not eligible, no matter how green the
constituent PRs looked individually.

Three consequences that are easy to get wrong:

1. **A PR's own CI is not what decides eligibility.** What makes a PR *batch-eligible* is a **clean
   review recorded at its live head** (`~/.pi/agent/reviews/<owner>-<repo>-<N>.json`, key `head_sha`
   == live `headRefOid`). Do not wait on a queued or red GitHub check *before batching* — the batch
   supersedes that check as evidence. This is the whole point: per-PR CI is what "takes forever" and
   what can still be green while the *combination* is red. (It does not waive the landing rail — see
   [Landing](#landing--main-must-still-be-the-tree-you-tested).)
2. **The batch is the gate.** The full suite (not the evals) runs **once** on the batched tree. If it
   is red, the batch names the culprit and **drops** it. A red batch is never merged to be fixed later.
3. **The tested tree must be the landed tree.** A suite run certifies one tree, and only one. If `main`
   moved while the batch ran, the tree that lands is a composition nobody tested — see
   [Landing](#landing--main-must-still-be-the-tree-you-tested), which is not optional.

Why the combination can be red when every PR is green: PR #1 renames a function and every call site on
main; PR #2 adds a new call to the old name. Each is green against main. Merged together, main is red.
Only a tree carrying both can observe it. This is bors-ng's opening example, and it is also **this
repository's own incident record** — `.mergify.yml` carries two recorded composition failures
(#5293, #5335), each a batch of one whose failure its base did not explain.

### This repository already has a merge queue — what it does, and what the batch adds

`.mergify.yml` is a **live** merge queue for `main`, not a plan. Today it merges a PR onto the current
base inside a `mergify/merge-queue/main` branch and runs CI there, so a merge is validated against the
tree that will actually exist; `merge_conditions: check-success=python-ci-gate` is evaluated on that
queue branch, `batch_size: 4`, `merge_method: squash`. The queue is the reason a PR does not have to be
manually updated before merging, and it already catches composition failures.

That overlap is real and must not be papered over — but it is not the same gate:

| | `.mergify.yml` queue | this batch |
|---|---|---|
| what runs | the **tiered selection** for the diff (`config/ci-surfaces.yml`) | the **full** deterministic suite, once |
| over what | `main ∪ the queued PRs`, batch of up to 4 | an arbitrary set, assembled in ascending PR order |
| baseline | none — a green run is absolute | a **Phase 1 baseline on `origin/main`**, so pre-existing red is separated from caused red |
| on failure | re-tested, bisected | attributed to a merge commit, and the culprit **dropped** |
| base drift | the queue **is** the writer, so its base cannot drift under it | the batch does not own `main`, so the base **can** drift — see Landing |

So: the queue owns the merge and validates a composition; the batch owns the *evidence* and validates a
composition the queue cannot (larger than 4, chosen deliberately, measured against a baseline). Neither
substitutes for the other. The ruling above moves the **full suite** to the batch; it does not remove
the queue, and nothing here asks for it to be removed.

## Phases

### 0. Select the lane — first, before anything runs

The suite is **URI-required**. `tests/conftest.py`'s session fixture fails the whole run closed when
`TORTOISE_DB_URI` is unset and `TORTOISE_TEST_CARVE_OUT` is not opted in
(`tests/_embedded.py::_assert_p4_uri_required`). A suite command with no lane does not produce a
baseline; it produces a session-start refusal. So the lane is picked and **exported once**, in the shell
that runs every later phase.

Pick the lane that **isolates this batch's ledger**, and name it in the record:

1. `tools/test_lane.py uri` → a **private** container for this batch. This is the **default**, and
   export **all three** vars it prints (the URI alone is not the lane — see below).
2. Only when you can state that **no other lane is live on this box** → the shared docker FalkorDB
   instance, `TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'`.
3. No docker → `TORTOISE_TEST_CARVE_OUT=1`. Then the verdict is `UNPROVEN-FULL-SUITE` and **no phase
   below can change that**.

```bash
# DEFAULT: a PRIVATE container, so no peer lane's graphs land in this batch's ledger (#5084).
# It prints all THREE seam vars — export every one: tests that resolve their target through
# `tests/_live_utils.py` key on TORTOISE_TEST_DOCKER_PORT, so a URI-only export leaves them
# silently addressing the SHARED instance (`tools/test_lane.py`'s own SCOPE section).
lane="$(uv run python tools/test_lane.py uri)" || exit 1
eval "$lane"

# Shared instance instead — ONLY when no other lane is live (see the #5084 note below):
#   export TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix'

uv sync --extra embeddings --extra parity    # ONE command — see the extras trap below

# ⛔ ONE LEDGER DIRECTORY PER BATCH — every artifact a verdict is built from lives under it (see
# "The ledger is per-batch, not per-box" below). The NAME is required (`:?`): an unset one would put
# the artifacts at a bare /tmp path that every batch on this box writes, so the poll could read a
# PEER batch's status and the count could read its report.
BATCH="${BATCH:?set BATCH to this batch's own name (e.g. 1 for integration/batch-1) — an empty one means another batch's ledger}"
B="/tmp/batch-$BATCH"
mkdir -p "$B" || exit 1
```

**Why the private container is the default here, and not the shared one (#5084).** A batch IS the
multi-lane case: the assembly is agent-driven and `.mergify.yml` keeps merging to `main` throughout,
so peer lanes are running the suite on this box while Phase 1 and Phase 3 run. The shared instance is
not a ledger under that load — measured 2026-10-06 it had **restarted 213 times**, and a single-file
run showed graphs appearing from *other lanes' sessions*. Interference that reds a batch run but not
the baseline run lands in the **batch** id set only, Phase 4 classifies it `NEW`, and an innocent PR
is convicted — "the outcome this whole phase exists to prevent". `tools/test_lane.py` exists exactly
so that cannot happen: its container is one this tool refuses to address from any other lane. Outside
practice agrees — the reason per-run containers are standard for test infrastructure is that shared
resources make results non-deterministic through "data corruption and configuration drift"
(Testcontainers, *What problems does Testcontainers solve?*). Note the container is **not
self-cleaning**: `tools/test_lane.py down` when the batch is over.

The **same lane** must serve Phase 1 and Phase 3. A batch measured on a different lane from its
baseline is not comparable to it.

**The same lane is not enough — the LEDGER must be per-batch too.** Every artifact a verdict is built
from lives under **one per-batch directory**, `B=/tmp/batch-$BATCH`: the two baseline reports, the
baseline count, and the gate's status, log and two reports. `BATCH` is **required** (`${BATCH:?…}`
halts on an empty one) and `$B` is *derived* from it, so every fence — and every later shell —
reproduces the same directory without carrying anything.

A bare `/tmp/batch.rc` is not a ledger under that load: it is what the **box** has. A peer batch
finishing its own run while this one polls is indistinguishable from this one finishing — the poll
reads `DONE`, `$RC` is the peer's status (honestly a *complete* run, just not this one), and
`$BATCH_TOTAL` is the peer's count, a real and coherent number, so the count halt passes too. Phase 4
then diffs the peer's green ids against this batch's baseline and prints `BATCH-GREEN` for a tree
whose own run is still executing; the mirrored case convicts an innocent PR with the peer's ids. The
window is not a race of milliseconds — it is the whole duration of the run, and two batches started
together finish together. This is the container argument one layer down, and this repo has already
measured the class: `python-ci.yml` records a fully GREEN leg that still concluded `failure` because
*"a sibling leg's `> /tmp/pytest.log` truncated the file between this leg's `tail` and its attribution
read"* (run 40808264676), and #10028's fix refuses the shortcut outright — `base="${RUNNER_TEMP:?RUNNER_TEMP is not set — refusing to fall back to a host-global /tmp artifact path}"`.

### 1. Baseline — run the suite on `origin/main` ALONE, first

Non-negotiable, and the cheapest step to skip wrongly. Without a baseline you cannot tell a failure a
PR *caused* from a failure that was **already there**, and you will drop an innocent PR (or "fix" a
pre-existing bug inside a batch, which is scope creep with no review).

```bash
git fetch origin main
PINNED="$(git rev-parse origin/main)"            # PIN THIS SHA — every later comparison is against it
# ⛔ A dirty tree defeats the switch: `git switch -C` CARRIES uncommitted and untracked files.
[ -z "$(git status --porcelain)" ] || { echo "dirty tree — the baseline would measure it, not origin/main"; exit 1; }
git switch -C integration/base-check "$PINNED" || exit 1   # ⛔ SWITCH, not `git branch` — run the suite on THIS tree.
# `-C` (create OR reset), not `-c`: a RE-baseline is the normal case (see Landing route 1), and a
# bare `-c` dies with `fatal: a branch named 'integration/base-check' already exists` — after which,
# in a fence with no `set -e`, the pytest lines below run on whatever tree HEAD happens to be on.
# `|| exit 1` makes that impossible: the fence stops instead of measuring the wrong tree.
# ⛔ Re-derive the ledger directory: this fence may be a LATER shell than Phase 0's. `$B` is a
# DERIVATION, not a carrier — the same lines reproduce it — and an absent directory HALTS instead of
# writing at `$B`'s path with `$B` empty (which is `/baseline-junit.xml`, at the host root).
BATCH="${BATCH:?set BATCH to this batch's own name (e.g. 1 for integration/batch-1) — an empty one means another batch's ledger}"
B="/tmp/batch-$BATCH"
[ -d "$B" ] || { echo "no ledger directory $B — Phase 0 creates it, per batch; absent means BATCH is unset or names another batch"; exit 1; }
# ⛔ CLEAR the report and the carried count FIRST. pytest truncates a junit file only in
# `pytest_sessionfinish`, so a baseline run the kernel killed leaves the PREVIOUS batch's report at
# this path — and the count below then reads it as THIS baseline, which is the ledger every later
# verdict is diffed against (main's own red is hidden, and an innocent PR is convicted).
rm -f "$B/baseline-junit.xml" "$B/baseline.total"
# BOTH halves — a baseline that runs one half cannot contain the other half's pre-existing red.
# `--junitxml` is not decoration: Phase 3 compares the gate run's test COUNT to this file,
# because a run the kernel killed reports no summary and would otherwise read as green.
uv run pytest tests/ -q --junitxml="$B/baseline-junit.xml" -m 'not track_b and not live and not integration'
BASELINE_RC=$?
# ⛔ COMPLETED is not merely finished. pytest exits 0 (passed) or 1 (tests failed) on a COMPLETED
# run, and 2/3/4/5 — or 128+N — when it was interrupted, misconfigured or KILLED. A killed baseline
# writes no summary, and by the Baseline rule a missing id set reads as "nothing was red on main":
# that is the ledger corruption the whole phase exists to prevent, arriving silently.
[ "$BASELINE_RC" -eq 0 ] || [ "$BASELINE_RC" -eq 1 ] || { echo "the baseline run did not COMPLETE (rc=$BASELINE_RC; 137=SIGKILL/OOM) — no ledger, so no honest verdict is possible"; exit 1; }
# ⛔ Half 2 gets the SAME two treatments, for the same two reasons: it is a MUST-PASS `python-ci-gate`
# member, and a half whose run is never status-checked and never writes an id artifact fails open —
# a killed `-m track_b` run is indistinguishable from a finished one, and a red one contributes no
# ids to EITHER side of the Phase 4 diff, so the verdict diff never sees the gate member at all.
rm -f "$B/baseline-trackb.xml"
uv run pytest tests/test_ingest_safety.py -q --junitxml="$B/baseline-trackb.xml" -m track_b
BASELINE_TRACK_B_RC=$?
[ "$BASELINE_TRACK_B_RC" -eq 0 ] || [ "$BASELINE_TRACK_B_RC" -eq 1 ] || { echo "the baseline's track_b half did not COMPLETE (rc=$BASELINE_TRACK_B_RC; 137=SIGKILL/OOM) — that half has no ledger, so Phase 4 cannot classify a track_b id at all"; exit 1; }
BASELINE_TOTAL="$(grep -o 'tests="[0-9]*"' "$B/baseline-junit.xml" | grep -o '[0-9]*' | awk '{s+=$1} END{print s+0}')" || exit 1
[ -n "$BASELINE_TOTAL" ] && [ "$BASELINE_TOTAL" -gt 0 ] || { echo "no baseline junit total — the baseline run wrote no report"; exit 1; }
# ⛔ CARRY the total to Phase 3 in a FILE, not a shell variable. Phase 3 runs in a LATER shell (the
# same reason the run's status and `$PINNED` are carried in files): there `[ "$BATCH_TOTAL" -ge
# "$BASELINE_TOTAL" ]` compares against the empty string, bash rejects the expression (rc=2), and
# the count halt reports "SHORTER than the baseline" on a complete, green batch.
printf '%s\n' "$BASELINE_TOTAL" > "$B/baseline.total"
git switch -                                      # back to the lane you came from
```

**`git switch -C`, never `git branch`.** `git branch integration/base-check "$PINNED"` creates the ref
and leaves your working tree exactly where it was — so the suite runs on whatever branch you happen to
be standing in (in this fleet, usually a lane worktree carrying unreviewed changes), while the heading
above promises `origin/main` alone. The baseline id list is the ledger every later verdict is diffed
against, so a polluted baseline both hides main's own red **and** convicts an innocent PR for a failure
the lane introduced. `git switch -C` (or `git switch --detach "$PINNED"`) fixes the *wrong branch*;
it does **not** fix a *dirty tree* — `git switch -C <branch> <commit>` re-applies local modifications
to the new tree (it refuses only where they differ between the two branches, so uncommitted work on a
file `main` did not touch is carried silently, and untracked files are always carried). Hence the
`git status --porcelain` precondition above: in this fleet the lane's normal state **is** a dirty tree,
and that is exactly the work the baseline must not measure.

`-C` rather than `-c` for a second reason, independent of the tree: **a re-baseline is the normal
case** (Landing route 1 re-runs phases 1 and 3), and `git switch -c` refuses an existing branch name —
`fatal: a branch named 'integration/base-check' already exists` — after which a fence without `set -e`
keeps going and the pytest lines below measure whatever tree HEAD is on. `-C` (create **or reset**) is
idempotent, and `|| exit 1` makes a failure stop the fence instead of silently measuring the wrong
tree. Note `-C` is not `-f`: it does not discard local changes, so the dirty-tree precondition is still
the thing that guards the tree.

**The baseline runs BOTH halves.** Phase 3 runs `-m track_b` as well as the deterministic marker (see
below), so a baseline that runs only the first half is structurally incapable of containing a
pre-existing `track_b` failure — Phase 4 would then classify that failure as `NEW` and convict an
innocent PR, which is the outcome this whole phase exists to prevent.

Record the baseline result **as data**: counts, the **exact list of failing test ids**, and the
duration. Those ids are the ledger that later failures are diffed against. Record `$PINNED` too — in
the batch PR body, as a `pinned-base: <sha>` line — because landing is a different shell
(see [Landing](#landing--main-must-still-be-the-tree-you-tested)).

**Everything this phase must hand to a later phase goes into a FILE, never into a shell variable.**
The doctrine already carries `$PINNED` in the batch PR body and the gate run's exit status in
`$B/batch.rc` for exactly this reason — a plain variable is gone the moment the operator opens the
next shell, and nothing requires phases 1-3 to share one. `$BASELINE_TOTAL` is the third: it is
written to `$B/baseline.total` here and read back in Phase 3, because the count halt is the only
mechanical detector left for a run that finished but reported too few tests. An absent carrier is a
halt, not an empty comparison: `[ "$BATCH_TOTAL" -ge "" ]` is `rc=2`, which a naive fence then reports
as a short run on a batch that was in fact complete. `$BATCH` is the one value that needs no carrier,
because `$B` is a DERIVATION of it — the same two lines in every fence reproduce the same directory.

**The baseline's own report is cleared and completion-checked, like the gate's.** `pytest` truncates a
junit file only in `pytest_sessionfinish`, so a run killed mid-flight (measured on #10399: five
`python` processes in 23 minutes) leaves the **previous** batch's report at the fixed path, and
`pytest`'s exit status is the only thing that distinguishes it from a finished run: only `0` and `1`
are a COMPLETED run. A baseline that is stale or killed is worse than no baseline — it is a
confident ledger of the wrong tree.

**The marker filter is not decoration.** `python-ci.yml`'s deterministic lanes run the suite with
`-m 'not track_b and not live and not integration'`. `live` marks live-API probes and `integration`
marks opt-in tests needing live credentials or production access (`pyproject.toml`). A bare
`pytest tests/` therefore runs tests that cannot pass on a lane without those credentials — and by the
Baseline rule every such failure becomes a `BASELINE(<ids>)` id, or convicts an innocent PR. The filter
is what makes the run comparable to CI at all.

Two corrections to the quantifier, because both decide whether the batch is a gate or a gesture:

- **`-m track_b` is NOT deselected upstream.** `python-ci.yml`'s `test-track-b` job runs
  `tests/test_ingest_safety.py -m track_b` as its own **MUST-PASS** member — it is a `needs:` of the
  required `python-ci-gate`, and its own measured job max is ~1.2 m (`docs/ops/ci-wall-clock.md`,
  captured 2026-10-26, records 1.18 m; the job's own 2026-09-30 cap comment says 1.15 m). CI runs the
  deterministic suite
  in two marker **halves**; a batch that runs only the `not track_b` half leaves a gate member
  unrun, and a `track_b` regression introduced by the composition reaches `main` unseen — the exact
  class the invariant forbids. So Phase 3 runs **both** halves.
- **`post-merge-validation.yml` does NOT use this filter.** Its run is
  `-m 'not track_b' --ignore=tests/e2e $SLOW_IGNORES` — no `not live`, no `not integration`, slow
  files excluded. That is *why* it is the **detector** and not the reproducer: it watches `main` with
  a different (narrower and slower-excluding) set, so a `BATCH-GREEN` batch can still red a
  post-merge run on a test the batch never ran. It detects; the batch gates.

And the gate has legs a pytest run does **not** reproduce, in two kinds — so never read a green batch
as "`python-ci-gate` passed":

- **Non-test legs**, which no pytest run can reproduce at all: `packs-compile` (whole-registry
  `PackRegistry.load_all()`), `manifest-integrity`, `workflow-lint`, `surface-guard`,
  `ci-introduced-failure` — the #9984 predicate, which reads the run's own jobs and step logs through
  the GitHub API to separate a failure **introduced by this change** from `main`'s own pre-existing
  red. It is the rail's automated version of Phase 1 above, so no local run stands in for it — and
  `changes`, the diff selector itself: it is not in `DEFERRABLE_LEGS`, so a failure there reds the
  required check by name, and it is the only leg whose failure makes every **test** leg skip — the nine
  jobs that declare `needs: changes`. `manifest-integrity`, `workflow-lint` and `surface-guard` declare
  no `needs:` at all (#2656 made them peers of the matrix) and `ci-introduced-failure` runs
  `if: always()`, so those four do not skip. It is still exactly
  the selection a batch's pytest run cannot exercise. Do not trust a count of these members written
  here — the authority is the leg table in `.github/workflows/python-ci.yml`
  (`python-ci-gate`'s `LEGS_FILE`), and it grows.
- **Two test legs the batch's lane SKIPS rather than runs.** `test-d14-hosted-api` runs
  `pytest tests/ -m embedded_only` **URI-less** (with `TORTOISE_TEST_CARVE_OUT=1`) and fail-closes on an
  all-skip floor precisely because those tests run in no other job — while Phase 0 above *mandates* a
  URI, and the D14 hook makes every `embedded_only` test **skip visibly** when one is set, so the batch
  collects them and reports skips. `test-record-postgres` installs `.[test,postgres]` and arms
  `TEST_RECORD_DB_URI`; the batch's `uv sync --extra embeddings --extra parity` installs neither
  `psycopg2` nor a record DB, so those skip too.

Every one of them is a `needs:` of the required `python-ci-gate`, so they are gate members, not
formality.
All of the above are owed by the landing rail, which still requires `python-ci-gate` (see
[Landing](#landing--main-must-still-be-the-tree-you-tested)).

### 2. Assemble the batch

Eligibility: open, non-draft, clean review **at the live head**. Verify, never assume — a lane may have
pushed since its review:

```bash
gh pr view <N> --json headRefOid,mergeable     # headRefOid must equal the registry's head_sha
```

Build in **ascending PR number** so the composition is deterministic and reconstructible:

```bash
git switch -C integration/batch-1 "$PINNED" || exit 1
# `-C` = create OR reset. A rebuild from a new pin is the intended case, and a plain `-b` fails on
# an existing branch — after which the `--no-ff` merge below would merge a foreign PR head into
# whatever branch the operator happens to be standing on.
git fetch origin pull/<N>/head:refs/tmp/pr-<N>          # exactly the reviewed head
git merge --no-ff --no-edit -m "batch: merge PR #<N> (<sha>)" refs/tmp/pr-<N>
git log --oneline -1                                    # after EVERY merge
```

Each merge is a real `--no-ff` merge commit naming its PR, so
`git log --first-parent --merges origin/main..integration/batch-1` is the batch's record of **merges
attempted**. Both flags are load-bearing: a batch carries each PR's own commits too, so the bare
`git log origin/main..integration/batch-1` lists commits *inside* the PRs as well as the merges, and a
reader who takes it as the merge list will hand a PR-internal sha to the revert below. It is *not* the landed manifest once Phase 4 reverts a
culprit: a revert adds `Revert "batch: merge PR #<N>"`, so the log then names a PR whose content is
**not** in the tree. Read it as attempted-and-maybe-dropped — the landed content is the **tree**, and
the dropped PR is the one with a matching revert line (a re-run of the Phase 4 bisect that ignores
this walks straight back to the already-dropped merge and re-blames it).

**Conflicts.** Resolve only when the resolution is **unambiguous** — both sides additive, or the same
fix applied two ways with identical effect. Write down which side you took and why. Otherwise **drop
that PR from the batch** and say so in the PR body. Never `-X ours`, never `-X theirs`, never `--force`:
a forced resolution hides a semantic conflict the suite may not catch, which is exactly the failure
mode this process exists to prevent.

### 3. Run the gate on the batch tree

Same lane, same commands, same env as the baseline — **both** halves of the deterministic suite:

```bash
# Same clean-tree precondition as Phase 1, and for the same reason: `git switch -C` CARRIES
# non-conflicting local modifications into the tree under test, so uncommitted work created during
# phases 2-3 would be measured by the run that authorizes BATCH-GREEN.
[ -z "$(git status --porcelain)" ] || { echo "dirty tree — the gate would measure it, not the batch"; exit 1; }

# Half 1 — the deterministic suite. Detached and POLLED, never one blocking call: a wedged
# foreground run takes the whole lane down with it and leaves no log.
# ⛔ The STATUS is written by the run's OWN shell, never recovered with `wait`. `wait` only reaps THIS
# shell's children, and the poll below happens in a LATER shell — the only way to satisfy the line
# above (`never one blocking call`). Measured (bash 5.2): `bash: wait: pid N is not a child of this
# shell`, rc=127 — so `wait` declared a COMPLETED, green run UNPROVEN and printed the wrong diagnosis.
# The subshell writes `$?` to a file, so no step here is shell-local.
# ⛔ Re-derive the ledger directory: this fence is usually a LATER shell than Phase 0's, and `$B` is
# a DERIVATION, not a carrier. A fence that skipped this would `rm -f` and write at `$B`'s path with
# `$B` empty — a bare /tmp name that a peer batch writes too, i.e. exactly the ledger sharing this
# section exists to prevent.
BATCH="${BATCH:?set BATCH to this batch's own name (e.g. 1 for integration/batch-1) — an empty one means another batch's ledger}"
B="/tmp/batch-$BATCH"
[ -d "$B" ] || { echo "no ledger directory $B — Phase 0 creates it, per batch; absent means BATCH is unset or names another batch"; exit 1; }
rm -f "$B/batch.rc" "$B/batch-junit.xml"   # a stale status/report from an earlier run must not decide this one
( uv run pytest tests/ -q --junitxml="$B/batch-junit.xml" -m 'not track_b and not live and not integration' > "$B/batch.log" 2>&1; echo $? > "$B/batch.rc" ) &
[ -f "$B/batch.rc" ] && echo 'DONE — read $B/batch.rc' || echo RUNNING   # repeat until DONE

# ⛔ DONE is not PASSED. A pid that goes away fails exactly as fast for a run the kernel OOM-killed as
# for one that finished — measured on #10399, five `python` processes were killed in 23 minutes — and a
# killed run prints no summary, hence carries no failure ids, so Phase 4 would diff it straight to
# BATCH-GREEN. The status the run itself wrote is the only thing that separates them; require a
# COMPLETED report:
[ -f "$B/batch.rc" ] || { echo "no $B/batch.rc — the gate run was never launched in this shell: launch it above, then poll"; exit 1; }
RC="$(cat "$B/batch.rc")"
[ "$RC" -eq 0 ] || [ "$RC" -eq 1 ] || { echo "the gate run did not COMPLETE (rc=$RC; 137=SIGKILL/OOM) — UNPROVEN-FULL-SUITE(<lane>), never green"; exit 1; }
# ⛔ The baseline TOTAL does not survive Phase 1's shell either: Phase 3 may be a LATER shell, where
# the comparison below would run against an empty string (bash: `integer expression expected`, rc=2)
# and the halt would report a COMPLETE, green run as SHORTER than the baseline. Read the file Phase 1
# wrote, exactly as the status and the pin are read, and refuse an absent one.
[ -s "$B/baseline.total" ] || { echo "no $B/baseline.total — Phase 1 never recorded the baseline count; re-run Phase 1 (Landing route 1)"; exit 1; }
BASELINE_TOTAL="$(cat "$B/baseline.total")"
BATCH_TOTAL="$(grep -o 'tests="[0-9]*"' "$B/batch-junit.xml" | grep -o '[0-9]*' | awk '{s+=$1} END{print s+0}')" || exit 1
[ -n "$BATCH_TOTAL" ] && [ "$BATCH_TOTAL" -ge "$BASELINE_TOTAL" ] || { echo "the gate run reported $BATCH_TOTAL tests against a baseline of $BASELINE_TOTAL — SHORTER than the baseline: investigate (a killed run, or a diff that legitimately removes or moves tests) — and never call it green: UNPROVEN-FULL-SUITE(<lane>)"; exit 1; }

# Half 2 — the track_b leg CI runs as its own MUST-PASS, gate-member job (`-m track_b`).
# ⛔ Instrumented exactly like half 1, because the fail-open is the same one: rc 0/1 is a COMPLETED
# run and anything else is a run that did not finish, and the junit report is what puts its ids in
# the Phase 4 diff. Without the report a red `track_b` half has ids in NEITHER set, so the diff
# reads it as green — the MUST-PASS gate member the gate phase exists to observe, unobserved.
rm -f "$B/batch-trackb.xml"
uv run pytest tests/test_ingest_safety.py -q --junitxml="$B/batch-trackb.xml" -m track_b
TRACK_B_RC=$?
[ "$TRACK_B_RC" -eq 0 ] || [ "$TRACK_B_RC" -eq 1 ] || { echo "the track_b half did not COMPLETE (rc=$TRACK_B_RC; 137=SIGKILL/OOM) — UNPROVEN-FULL-SUITE(<lane>), never green"; exit 1; }
```

A run that skips half 2 has not run the gate, so it cannot produce `BATCH-GREEN` — see the verdict
table and the `UNPROVEN-FULL-SUITE(<lane>)` rule.

### 4. Compare to the baseline and blame

- **The id sets are the junit reports, one per half**: `$B/batch-junit.xml` **and**
  `$B/batch-trackb.xml`, against `$B/baseline-junit.xml` **and** `$B/baseline-trackb.xml`.
  Diffing the deterministic half alone leaves every `track_b` id in neither set — which is how a red
  MUST-PASS gate member reads as green.
- **A failure id that is in the baseline set is a `BASELINE(<ids>)` failure.** Not a PR's fault. Do not
  drop a PR for it, and do not fix it inside the batch. One line on whether the batch makes it worse.
- **A failure id that is NOT in the baseline set is NEW** and convicts a PR. Find which one:

```bash
git log --first-parent --merges --oneline origin/main..integration/batch-1   # merges ATTEMPTED — skip any with a matching Revert
git bisect start --first-parent integration/batch-1 origin/main   # or, cheaper, walk the merges
#   at each step: run ONLY the failing files (not the whole suite)
#   git bisect good <merge sha> / git bisect bad <merge sha>
uv run pytest <the failing file(s)> -q
```

  `git bisect run` is the honest tool when the failing file set is stable:

```bash
git bisect run sh -c 'uv run pytest <failing files> -q >/dev/null 2>&1 || exit 1'
BAD="$(git rev-parse refs/bisect/bad)" # ⛔ NOT `HEAD` — see below: `bisect run` leaves HEAD on the LAST
                                       # PROBE it tested. `refs/bisect/bad` is the ref git's own manual
                                       # names as the result, and it survives until `git bisect reset`,
                                       # which is exactly why this capture sits here.
                                       # (walking the merges by hand instead? set BAD to that merge's sha)
# ⛔ `--first-parent` on the `start` above is what makes this sha a MERGE: by default `git bisect`
# walks EVERY commit in origin/main..integration/batch-1, so the "exact commit" it reports can be a
# commit INSIDE a PR (reproduced in a scratch repo against this doctrine's own --no-ff shape: the
# blame stopped at `PR1 commit 2`, not at `batch: merge PR #1`).
git bisect reset integration/batch-1   # ⛔ REQUIRED: `bisect run` leaves HEAD DETACHED at the last probe
                                       # it tested, which is usually NOT the culprit
git switch integration/batch-1 || exit 1   # the cheaper route leaves it detached too — HALT, or the revert commits detached
git log --oneline -1                   # must name integration/batch-1's tip, never a bisect probe
```

  **`HEAD` is not the answer — `refs/bisect/bad` is.** `git bisect run` leaves `HEAD` detached at the
  **last probe it tested**, which measured (3/5/7/8/13-merge `--no-ff` batches, 40 cases) diverges from
  the first bad commit in **27 of 40** — and every divergence is the merge **immediately before** the
  culprit, a `batch: merge PR #<N>` commit. So the merge-ness assert below passes on the WRONG sha, and
  `git revert -m 1` drops an innocent PR while the culprit stays in the tree. Git's own manual names the
  ref: *"the command will print out a description of the first bad commit. The reference
  `refs/bisect/bad` will be left pointing at that commit"* (<https://git-scm.com/docs/git-bisect>), and
  `git bisect reset` removes it — which is why the capture above precedes the reset and reads the ref,
  never `HEAD`.

  **Without the return, two silent failures follow.** `git bisect` checks out its probes and `git bisect
  run` ends detached at the last probe it tested, so: (1) the revert below commits onto a **detached HEAD**
  and never lands on `integration/batch-1`, which therefore still contains the PR the doctrine says to
  drop; and (2) the "one more full suite run" measures the first-bad commit's tree — missing every PR
  merged after the culprit — and *that* result becomes the verdict row. The verdict would then describe
  a tree nobody lands, which is the tested-tree≠landed-tree failure arriving before the Landing section
  can catch it.

  Attribute to the **merge commit's PR** — with `--first-parent` the blame stops on a batch merge, and
  its log line names the PR. Without it the blame can stop on a commit inside a PR, which has no merge
  to attribute and no merge to revert (the merge-ness assert above is the mechanical check).

- **Drop the culprit**, do not fix it inside the batch. Every merge in the batch is a merge commit **by
  construction**, and Git refuses to revert a merge without a mainline: a bare `git revert <sha>` exits
  128 (`is a merge but no -m option was given`) and drops nothing. The batch branch is mainline `1` —
  the first parent is the batch's own history, the second is the PR — so:

  ```bash
  # ⛔ The sha must be a batch MERGE. `git revert -m 1` on a NON-merge IGNORES the mainline and
  # exits 0 (git-verified), reverting ONE COMMIT of the PR instead of the PR: the rest of it still
  # lands unreviewed, and the re-blame rule below cannot see it — the subject becomes
  # `Revert "PR1 commit 2"`, which matches no `batch: merge PR #<N>` line.
  [ -n "$(git rev-list --no-walk --merges "$BAD")" ] || { echo "$BAD is not a batch merge — re-blame with --first-parent; never revert a non-merge"; exit 1; }
  git revert --no-edit -m 1 "$BAD"                     # the sha whose merge-ness was just asserted
  ```

  Then re-run **only the failed files**, then **one more full suite run — both halves** (a green that
  excludes `-m track_b` is not a green). A batch that cannot be made green ships without the culprit,
  never with it.

### 5. Verdict — and it must be honest about what ran

| Verdict | Meaning |
|---|---|
| `BATCH-GREEN` | Full suite ran on the batched tree, no NEW failures vs baseline. It does **not** mean `python-ci-gate` passed: the `embedded_only` and record-postgres test legs, and the non-test legs, are skipped rather than reproduced (see the gate-legs list above). |
| `BATCH-GREEN(BASELINE(<ids>))` | Green *relative to baseline*; the listed ids were already red on main. |
| `BATCH-RED(<ids>)` | New failures remain after dropping the culprit(s). Do not merge. |
| `UNPROVEN-FULL-SUITE(<lane>)` | **The full suite did not run, or did not COMPLETE.** Only the embedded carve-out or a subset ran; or the gate run was killed / interrupted and reported no final summary, so it has no failure set to diff. This is never "green". |

⛔ **A batch is never green on a partial lane.** If docker/FalkorDB was unavailable and only the
embedded carve-out ran, the verdict is `UNPROVEN-FULL-SUITE(embedded-carve-out)`. Say that in those
words; a subset reported as "green" is how a post-merge red gets manufactured by a process that was
supposed to prevent it.

**Tell a full run from a carve-out run by counting, not by remembering.** The carve-out set is
`config/ci-surfaces.yml` → `carve_out:` (mirrored by `tests/_embedded.py` → `TEST_NO_REDIRECT_STEMS`;
`tests/test_ci_selection.py` fails if the two disagree). Read the list — do not quote a remembered
count of it, because the set grows and any number written here is stale by the next reconciliation.

**The extras trap (#2985):** `uv sync --extra embeddings --extra parity`, always, in ONE command.
Plain `uv sync` silently drops the dense-retrieval extras; retrieval then degrades to keyword-only and
the run reports green for a product that no longer has embeddings. `uv sync --extra embeddings` alone
drops `parity` (pyarrow). The extras are part of the lane definition, not setup trivia.

## Aftermath

- The batch PR body is the **evidence**: constituent PRs, conflicts dropped and why, the
  `pinned-base: <sha>` line the Landing check reads, baseline vs batch matrix, per-PR attribution,
  verdict. Written with `--body-file`, never an inline body.
- The record of a *review* is not the record of a *batch*. Do not record a review of your own work
  (#825).
- Landing the batch is a separate, human-routed step. The batch lane never merges its own PR.

### Landing — `main` must still be the tree you tested

This is the one property the prior art gets for free and a hand-assembled batch does not: bors
fast-forwards `main` to the staging branch it just tested ("bit-for-bit"), and GitHub's queue validates
against *"the latest version of the target branch and any pull requests already in the queue"* — in
both, **the service owns the merge**, so the validated tree is the landed tree by construction. Here
nothing owns it. `.mergify.yml` keeps merging to `main` continuously (that is its job), so a batch run
can outlive its own base.

Nothing else in this document is worth anything if the base moved, so check it at landing time, not at
assembly time:

```bash
git fetch origin main
# $PINNED does NOT survive Phase 1's shell — landing is a different actor. Read it from the batch PR
# body (Phase 1 recorded it there) and never from memory:
PINNED="$(gh pr view <batch PR> --json body --jq '.body' | sed -n 's/^pinned-base: //p')"
[ -n "$PINNED" ] || { echo "no pinned-base: line in the batch PR body — the pin is unknown, re-run Phase 1"; exit 1; }
[ "$(git rev-parse origin/main)" = "$PINNED" ] || {
  echo "main moved: $PINNED -> $(git rev-parse origin/main) — re-baseline and re-run phases 1-3"; exit 1
}
```

An unset `$PINNED` in the landing shell compares against the empty string: it fails **closed** (which is
right) but prints `main moved:  -> <sha>` against an unmoved `main`, so the one mechanical enforcement
of the invariant reads as noise. Materialising it from the PR body makes the check self-contained —
the PR body is already the evidence artifact (see Aftermath).

⛔ **`--jq`, never `--jq -r`.** `gh` has no raw (`-r`) flag — that is `jq`'s — so `-r` is taken as the
*expression*, `.body` becomes a second positional argument, and the payload is never filtered: on
gh 2.102.0 the command fails outright (`accepts at most 1 arg(s), received 2`); on older builds it
prints the pretty-printed JSON object, which the `sed` then cannot match. Either way `$PINNED` is empty
and the landing step exits 1 with *"no pinned-base: line in the batch PR body"* on a body that carries
the line — the check is impossible to pass as written, and the reachable improvisations are to skip the
assertion or hand-copy the sha. `--jq '.body'` (or `--template '{{.body}}'`) emits the bare body.

If that assertion fails, there are exactly two honest routes and no third:

1. **Re-baseline and re-run** — `PINNED` becomes the new main, phases 1 and 3 run again on a batch
   rebuilt from it. Expensive, always correct.
2. **Freeze merges to `main` for the batch's duration** — declare it in the batch PR and hold the
   queue, so the base cannot move. Cheap, and only honest if the hold is real.

What is **not** a route: merging the batch PR anyway. A squash-merge onto the pinned base reproduces
the tested tree exactly (the batch's merge base *is* `PINNED`); a squash-merge onto a `main` that has
moved applies the batch's diff to a different tree, and the result is a composition no run has ever
seen — the same class of failure this doctrine exists to remove, arriving through the door marked
"landing".

**The landing rail still applies.** The ruling moves *what makes a constituent PR eligible*, never the
protections on the branch the batch lands on. `main` keeps the seven required contexts declared in
`.github/settings.yml` (`pricing-artifact`, `docs`, `test-isolation`, `license-surface`, `legal-e2e`,
`python-ci-gate`, `ai-review-gate`; `.github/scripts/check-required-set.py --live` is the assertion that
the file and the live protection agree), and `.mergify.yml` re-checks `python-ci-gate` on the queue
branch. The batch PR therefore still owes those contexts — the batch run is the *evidence* that they
will pass, not a substitute for them, and this doctrine does not ask for an exemption. What it removes
is waiting on each constituent PR's own CI before batching; what it adds is a full-suite run that the
rail's tiered selection does not perform.

## Prior art this matches (and where it diverges)

Convergent practice, three independent implementations:

- **bors-ng** — merge queue, **staging branch**, merge the queued PRs into it, test once, and if it
  passes "the main branch contains the exact contents that were just tested, bit-for-bit". Batches by
  default and **bisects** on failure.
- **GitHub merge queue** — validates each PR "when applied to the latest version of the target branch
  and any pull requests already in the queue" (`merge_group` event), i.e. the same speculative-tree
  idea, per-PR rather than batched.
- **Zuul** — speculative execution / dependent pipelines: test the proposed *future state*, not the
  PR against today's main.

Divergence, deliberate: all three are **automated services that own the merge**, and this repo already
runs one of them (`.mergify.yml`). Here the *batch* is assembled by an agent, the *landing* is
human-routed, and nothing owns `main` on the batch's behalf — which is exactly why the landing
assertion above has to be written down instead of inherited. The batching, the test-what-you-ship
invariant, and bisect-to-blame are the same. Manual `git log` + `git bisect run` is this process's
bisect.
