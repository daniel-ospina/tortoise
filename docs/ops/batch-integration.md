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
queue branch, `merge_method: squash`, and its own `batch_size` (a separate lever — #5384 owns raising it,
so this document never restates the number). The queue is the reason a PR does not have to be
manually updated before merging, and it already catches composition failures.

That overlap is real and must not be papered over — but it is not the same gate:

| | `.mergify.yml` queue | this batch |
|---|---|---|
| what runs | the **tiered selection** for the diff (`config/ci-surfaces.yml`) | the **full** deterministic suite, once |
| over what | `main ∪ the queued PRs`, up to `.mergify.yml`'s own `batch_size` | an arbitrary set, assembled in ascending PR order |
| baseline | none — a green run is absolute | a **Phase 1 baseline on `origin/main`**, so pre-existing red is separated from caused red |
| on failure | re-tested, bisected | attributed to a merge commit, and the culprit **dropped** |
| base drift | the queue **is** the writer, so its base cannot drift under it | the batch does not own `main`, so the base **can** drift — see Landing |

So: the queue owns the merge and validates a composition; the batch owns the *evidence* and validates a
composition the queue cannot (larger than the queue's own `batch_size`, chosen deliberately, measured
against a baseline). Neither
substitutes for the other. The ruling above moves the **full suite** to the batch; it does not remove
the queue, and nothing here asks for it to be removed.

## Phases

### 0. Select the lane — first, before anything runs

The suite is **URI-required**. `tests/conftest.py`'s session fixture fails the whole run closed when
`TORTOISE_DB_URI` is unset and `TORTOISE_TEST_CARVE_OUT` is not opted in
(`tests/_embedded.py::_assert_p4_uri_required`). A suite command with no lane does not produce a
baseline; it produces a session-start refusal — and that refusal is **not** visible in the status. The
lane is picked here and exported in **this** shell; every fence that runs the suite therefore *asserts*
it, because an export does not cross a shell boundary (see *The lane is shell-local* below).

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
# ⛔ `export`, not a bare assignment — the NAME is a CARRIER, and this is the one fence that can
# export it. Phases 1 and 3 are usually LATER shells, where a plain assignment made here has not
# survived; there `BATCH="${BATCH:?…}"` then HALTS the fence before anything runs, so the operator is
# told to set a name the doctrine never carried — the value crosses no shell boundary at all. A later
# PLAIN re-assignment PRESERVES the export attribute in bash, so the three fences below re-derive it
# with `:?` and stay exported.
export BATCH="${BATCH:?set BATCH to this batch's own name (e.g. 1 for integration/batch-1) — an empty one means another batch's ledger}"
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

**The lane is shell-local — and its ABSENCE is certified `COMPLETED`.** The `export` above reaches only
the shell that ran it, and Phases 1 and 3 are usually LATER shells (this document already re-derives
`$B` and carries the run's status and `$PINNED` in files, for exactly that reason). A missing lane does
not fail visibly: `tests/conftest.py`'s `_p4_uri_required` is `scope="session", autouse=True`, so
pytest re-raises it **per item** — every collected test `ERROR`s at setup and the process exits **1**,
which is one of the two statuses the `COMPLETED` predicates in Phases 1 and 3 accept as a *finished*
run. Measured at this head with
`env -u TORTOISE_DB_URI -u TORTOISE_TEST_CARVE_OUT pytest tests/test_batch_integration_doctrine.py
--junitxml=…`: **rc=1** and one `<testcase>` per check in the report. Two reachable outcomes, both of
them the failure this whole document exists to prevent:

- **Phase 3 loses the lane and Phase 1 had it** → every batch id is `NEW`, and Phase 4 instructs the
  operator to bisect, attribute and **drop** PRs — convicting an innocent PR over a tree in which
  nothing ran;
- **both phases lose it** → the two all-error id sets are identical, so the Phase 4 diff is empty and
  the doctrine prints **`BATCH-GREEN`** for a tree on which zero tests executed — verbatim the failure
  the per-batch ledger above is written to prevent, one value over.

So **both** suite fences assert the lane, with a halt: present as `TORTOISE_DB_URI` (or an explicit
`TORTOISE_TEST_CARVE_OUT` opt-in), or re-derived in that shell the way `$B` is —
`eval "$(uv run python tools/test_lane.py uri)"`.

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
# ⛔ The fetch HALTS. A bare `git fetch` that fails (network, auth, DNS) leaves `origin/main` at the
# LAST SUCCESSFUL fetch — in the normal flow, the very base this batch was pinned to — and `PINNED`
# below then records a tree that is not current `main`. The baseline is measured on it, so `main`'s
# own new red is absent from the baseline id set, Phase 4 classifies it `NEW`, and an innocent PR is
# dropped: the exact outcome the baseline exists to prevent. (The mirrored failure is worse — a
# silently-stale ref makes a Landing pin check PASS against a moved `main`.)
git fetch origin main || { echo "the fetch FAILED — refusing to pin a stale origin/main; the last successful fetch is not current main, and every later verdict is diffed against this pin"; exit 1; }
PINNED="$(git rev-parse origin/main)"            # PIN THIS SHA — every later comparison is against it
# ⛔ PRINT the pin — do not merely remember it. The batch PR body must carry it as a
# `pinned-base: <sha>` line, and Landing reads that line in a DIFFERENT shell: `$PINNED` does not
# survive this one. Copy the printed line; never re-derive it there. The only re-derivation
# available at body-writing time is `git rev-parse origin/main`, which by then is CURRENT main —
# and feeding that in makes Landing's `[ "$(git rev-parse origin/main)" = "$PINNED" ]` compare
# main to itself: always true, on a moved base, with every other check in that section green.
# Same reason Phase 3 prints `tested-tree:` — a carrier with no producer is a hand-copied value,
# and a hand-copied value is the one thing a mechanical check cannot distinguish from a derived one.
printf 'pinned-base: %s\n' "$PINNED"
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
# ⛔ The LANE is shell-local too, and an absent one is SILENT — `conftest.py`'s `_p4_uri_required` is
# session-scoped and autouse, so pytest re-raises it PER ITEM: every collected test ERRORs at setup and
# the process exits 1, which the COMPLETED predicate below ACCEPTS (measured at this head: rc=1, one
# `<testcase>` per item). Both id sets then become all-error and identical, Phase 4 diffs them to no
# NEW ids, and the verdict is BATCH-GREEN for a tree on which nothing ran. Assert it here, the way
# `$B` is re-derived here.
# ⛔ And assert the GATE'S predicate, not "the variable is set". `tests/_embedded.py`'s
# `_assert_p4_uri_required` tests `_uri_set_supported()` (a SUPPORTED scheme — `is_db_uri`,
# `tortoise/config.py`'s SUPPORTED_URI_SCHEMES) OR `_carve_out_opted_in()` (`is_truthy`,
# `tortoise/env_truthy.py`), and a plain `[ -n … ]` satisfies neither half of that: a
# set-but-unsupported `bolt://` (this repo's own AGENTS.md documents it as a legal value) passes it,
# and so does `TORTOISE_TEST_CARVE_OUT=0`. Both then ERROR every collected item at setup and exit 1 —
# the same fail-open this halt exists to close, one spelling over. So ASK the gate's own two
# predicates instead of re-spelling the scheme list or the truthy vocabulary: one source of truth, and
# no drift when either grows.
uv run python -c 'import sys; from tests._embedded import _carve_out_opted_in as opted_in, _uri_set_supported as supported; sys.exit(0 if supported() or opted_in() else 1)' || { echo "no USABLE lane in this shell — the gate is not \"is the variable set\": tests/_embedded.py's _assert_p4_uri_required accepts only a SUPPORTED TORTOISE_DB_URI (docker/redis/rediss — tortoise/config.py's SUPPORTED_URI_SCHEMES) or a TRUTHY TORTOISE_TEST_CARVE_OUT (1/true/yes/on — tortoise/env_truthy.py), so a set-but-unsupported bolt:// and a =0 both PASS a plain -n test and then ERROR every collected item at setup, exiting 1 — a status COMPLETED accepts. The Phase 0 export does not cross a shell boundary: export it, re-derive it with eval \"\$(uv run python tools/test_lane.py uri)\", or opt into the carve-out explicitly"; exit 1; }
# ⛔ CLEAR the report and the carried count FIRST. pytest truncates a junit file only in
# `pytest_sessionfinish`, so a baseline run the kernel killed leaves the PREVIOUS batch's report at
# this path — and the count below then reads it as THIS baseline, which is the ledger every later
# verdict is diffed against (main's own red is hidden, and an innocent PR is convicted).
rm -f "$B/baseline-junit.xml" "$B/baseline.total" "$B/baseline.skipped" "$B/baseline.skipped-reasons"
# BOTH halves — a baseline that runs one half cannot contain the other half's pre-existing red.
# `--junitxml` is not decoration: Phase 3 compares the gate run's test COUNT to this file,
# because a run the kernel killed reports no summary and would otherwise read as green.
uv run pytest tests/ -q --junitxml="$B/baseline-junit.xml" -m 'not track_b and not live and not integration' --ignore=tests/e2e
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
rm -f "$B/baseline-trackb.xml" "$B/baseline-trackb.skipped" "$B/baseline-trackb.skipped-reasons"
uv run pytest tests/test_ingest_safety.py -q --junitxml="$B/baseline-trackb.xml" -m track_b
BASELINE_TRACK_B_RC=$?
[ "$BASELINE_TRACK_B_RC" -eq 0 ] || [ "$BASELINE_TRACK_B_RC" -eq 1 ] || { echo "the baseline's track_b half did not COMPLETE (rc=$BASELINE_TRACK_B_RC; 137=SIGKILL/OOM) — that half has no ledger, so Phase 4 cannot classify a track_b id at all"; exit 1; }
# ⛔ No `|| exit 1` on this: a pipeline's status is its LAST command's, and `awk … END{print s+0}`
# always succeeds — so that halt could never fire (measured: a missing report prints grep's error, then
# T=[0], and the assignment still exits 0). The `-gt 0` check on the next line IS the halt.
BASELINE_TOTAL="$(grep -o 'tests="[0-9]*"' "$B/baseline-junit.xml" | grep -o '[0-9]*' | awk '{s+=$1} END{print s+0}')"
[ -n "$BASELINE_TOTAL" ] && [ "$BASELINE_TOTAL" -gt 0 ] || { echo "no baseline junit total — the baseline run wrote no report"; exit 1; }
# ⛔ CARRY the total to Phase 3 in a FILE, not a shell variable. Phase 3 runs in a LATER shell (the
# same reason the run's status and `$PINNED` are carried in files): there `[ "$BATCH_TOTAL" -ge
# "$BASELINE_TOTAL" ]` compares against the empty string, bash rejects the expression (rc=2), and
# the count halt reports "SHORTER than the baseline" on a complete, green batch.
printf '%s\n' "$BASELINE_TOTAL" > "$B/baseline.total"
# ⛔ SKIPS are a THIRD axis, and neither of the two detectors above can see them. A skipped test is
# still COLLECTED: it counts in `tests=` and it contributes no failure id, so rc is 0, the count is
# unchanged and the Phase 4 id diff is empty. The reachable bypass is a SOCKET PROBE, not an unset
# variable — `tests/test_projection.py`'s `live_proj` fixture skips when `_docker_falkor_reachable()`
# fails, which it does with `TORTOISE_DB_URI` correctly exported and the server gone (the private
# container reaped, a Docker restart). Every test using it skips, and the verdict is `BATCH-GREEN`
# on a tree where the live-FalkorDB suite never ran. This repo already refuses that class in CI with
# `tools/skip-guard.py` (#1382: *"the historical silent-green masked the #1382 EP regression class
# for days"*, wired at `.github/workflows/python-ci.yml`), so the batch records the baseline's skips
# and Phase 3 halts on any skip the baseline did not have. Baseline-DIFFED, never a whole-run gate:
# the batch lane's `embedded_only` skips are expected (see the gate-legs list), so only a NEW skip
# may convict. The reason multiset is recorded too, because a count alone cannot see a SWAP.
SKIPPED_TOTAL="$(grep -o 'skipped="[0-9]*"' "$B/baseline-junit.xml" | grep -o '[0-9]*' | awk '{s+=$1} END{print s+0}')"
printf '%s\n' "$SKIPPED_TOTAL" > "$B/baseline.skipped"
grep -o '<skipped [^>]*' "$B/baseline-junit.xml" | sed -n 's/.*message="\([^"]*\)".*/\1/p' | sort > "$B/baseline.skipped-reasons"
# ⛔ HALF 2 gets the SAME skip ledger, and its trigger is sharper than half 1's. `-m track_b` is a
# MUST-PASS `needs:` of the required `python-ci-gate`, and its tests skip BY DESIGN behind a feature
# probe: `tests/test_ingest_safety.py`'s `TRACK_B_SKIP` is a `skipif` on `_TRACK_B_FULL and
# _SENTINEL_A9`, so a constituent PR that drops `promote_source` from `create_operator`'s signature
# (or the `DIRECT_EDGE_TRAVERSAL` sentinel) turns every `track_b` test SKIPPED on the batch tree while
# the baseline still runs them. pytest has NO exit code for "everything was skipped" (its documented
# contract: 0 = all collected passed, 1 = some failed, 2/3/4/5 = interrupted/internal/usage/nothing
# collected), so the half exits 0, the COMPLETED predicate above ACCEPTS it, half 1's count is
# untouched, and a skip carries no failure id — so the diff is empty and `BATCH-GREEN` is printed for
# a tree on which a MUST-PASS gate member never ran. CI fails closed on exactly this (the `test-track-b`
# job's `tools/skip-guard.py` leg), and the batch must not be weaker than the gate it stands in for.
# Baseline-diffed for the same reason as half 1: only a NEW skip may convict.
SKIPPED_TRACK_B="$(grep -o 'skipped="[0-9]*"' "$B/baseline-trackb.xml" | grep -o '[0-9]*' | awk '{s+=$1} END{print s+0}')"
printf '%s\n' "$SKIPPED_TRACK_B" > "$B/baseline-trackb.skipped"
grep -o '<skipped [^>]*' "$B/baseline-trackb.xml" | sed -n 's/.*message="\([^"]*\)".*/\1/p' | sort > "$B/baseline-trackb.skipped-reasons"
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
(see [Landing](#landing--main-must-still-be-the-tree-you-tested)). The fence above **prints** that
line (`printf 'pinned-base: %s\n' "$PINNED"`): copy it, and never re-derive it later, because
`git rev-parse origin/main` at body-writing time is current `main` and makes Landing's assertion
compare `main` to itself. It is the same producer/consumer pair as `tested-tree:`.

**Everything this phase must hand to a later phase goes into a FILE, never into a shell variable.**
The doctrine already carries `$PINNED` in the batch PR body and the gate run's exit status in
`$B/batch.rc` for exactly this reason — a plain variable is gone the moment the operator opens the
next shell, and nothing requires phases 1-3 to share one. `$BASELINE_TOTAL` is the third: it is
written to `$B/baseline.total` here and read back in Phase 3, because the count halt is the only
mechanical detector left for a run that finished but reported too few tests. An absent carrier is a
halt, not an empty comparison: `[ "$BATCH_TOTAL" -ge "" ]` is `rc=2`, which a naive fence then reports
as a short run on a batch that was in fact complete. `$BATCH` carries itself differently: Phase 0
**exports** it, and every later fence re-derives `$B` from it with `:?` — so a shell that did not
inherit the name HALTS before it writes anything, rather than writing into another batch's ledger.
That is a halt, not a carrier: nothing here may assume the export reached the shell it is running in.

**The baseline's own report is cleared and completion-checked, like the gate's.** `pytest` truncates a
junit file only in `pytest_sessionfinish`, so a run killed mid-flight (measured on #10399: five
`python` processes in 23 minutes) leaves the **previous** batch's report at the fixed path, and
`pytest`'s exit status is the only thing that distinguishes it from a finished run: only `0` and `1`
are a COMPLETED run. A baseline that is stale or killed is worse than no baseline — it is a
confident ledger of the wrong tree.

**The marker filter is not decoration — and it is not the whole of what makes a run comparable.**
`python-ci.yml`'s deterministic lanes run the suite with `-m 'not track_b and not live and not
integration'` **over a file list, not over `tests/`**: `tools/ci_selection.py` excludes the
`tests/e2e/` prefix **by path** (#1349), and `python-ci.yml` records why — *"Without browsers they
only ever ERROR here"*. So the population has to match too, and `--ignore=tests/e2e` is that second
half; the marker filter alone is not what makes the run comparable to CI at all. Neither selector is
cosmetic:

- `live` marks live-API probes and `integration` marks opt-in tests needing live credentials or
  production access (`pyproject.toml`). A bare `pytest tests/` runs tests that cannot pass on a lane
  without those credentials — and by the Baseline rule every such failure becomes a `BASELINE(<ids>)`
  id, or convicts an innocent PR.
- `tests/e2e/` is a population **no CI lane runs**. On a lane without browsers, every module there
  ERRORs at import, so a healthy batch can never print plain `BATCH-GREEN` — only
  `BATCH-GREEN(BASELINE(<dozens of e2e ids>))`, with the Phase 4 id diff dominated by noise. On a box
  where the browsers *are* installed (this fleet installs them for `app-test`), those tests actually
  run and default to **live targets** — `tests/e2e/test_welcome_page.py` points `WELCOME_URL` at the
  deployed site — so one network-dependent id that is not in the baseline is NEW, convicts an
  innocent PR, and the prescribed remedy is to drop it.

`--ignore=tests/e2e` moves the run **toward** CI's population, which is why it is the one selector
argv this doctrine admits: `--co`, `--lf`, `-k` and `--deselect` move it away.

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
  required check by name, and it is the only leg whose failure makes every **test** leg skip — every
  job that declares `needs: changes`. That is a count to READ FROM THE WORKFLOW, never a literal
  written here: `grep -c '^    needs: changes$' .github/workflows/python-ci.yml` is the measurement,
  and one member of that set (`packs-compile`) is the non-test leg already named above, so the TEST
  legs that skip are one fewer than the grep reports. A hand-written ordinal in this sentence has
  already been wrong once (#10400 review P3 — it named a count the workflow did not have).
  `manifest-integrity`, `workflow-lint` and `surface-guard` declare
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
git fetch --force origin pull/<N>/head:refs/tmp/pr-<N> || { echo "the fetch of PR #<N> was refused — do NOT merge a stale ref"; exit 1; }   # exactly the reviewed head
# ⛔ `--force` (or a leading `+` on the refspec) is load-bearing on a REBUILD, not a
# convenience. The ref written above is LOCAL and it is REUSED by the next run, so when a
# PR head has been rewritten since then — amend, rebase, force-push, the ordinary answer
# to a review — the update is a NON-fast-forward and git REFUSES it (measured: `!
# [rejected] refs/pull/1/head -> refs/tmp/pr-1 (non-fast-forward)`, rc=1, and the ref
# left at the PREVIOUS head). The fetch is the one line in this fence that had no halt,
# so the fence continued and the merge below landed yesterday's head under a comment
# reading "exactly the reviewed head": a clean `--no-ff` merge with a correct-looking
# `batch: merge PR #<N>` subject, Phase 2's own eligibility invariant (`headRefOid` ==
# the reviewed head) broken, and Phase 3 certifying a tree built from heads nobody
# reviewed. The halt is what stops a REFUSED fetch instead of merging a stale ref.
# ⛔ The merge line HALTS, and the line after it asserts a MERGE COMMIT exists. `git merge --no-ff`
# on a head that is already contained prints "Already up to date" and exits **0** with NO merge
# commit — so the `git log` line below is cosmetic, the manifest names a PR whose commits are not in
# the tree, and Phase 4's revert has nothing to revert. The fetch above got its halt for exactly this
# reason (a refusal that exits non-zero and a no-op that exits zero are different failures).
git merge --no-ff --no-edit -m "batch: merge PR #<N> (<sha>)" refs/tmp/pr-<N> || { echo "the merge of PR #<N> did not happen — a refusal, or 'Already up to date' because the reviewed head is already an ancestor of the batch branch"; exit 1; }
git rev-parse -q --verify HEAD^2 >/dev/null || { echo "HEAD is not a merge commit — the batch would record a PR it never merged, and Phase 4's revert would have no mainline"; exit 1; }
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

**The branch name is fixed, deliberately — a second CONCURRENT batch is out of scope here.**
`integration/batch-1` is a repo-global ref, and every fence below (the tree assertion in Phase 3, the
bisect, the revert) names it literally; two batches sharing it would fast-forward each other's merges
and neither's tree would be the one that lands. The *ledger* is already per-batch (`$BATCH`); the
branch is not. A batch that must run alongside another therefore uses its own clone or worktree **and**
names its branch after `$BATCH`, and the fences below must be re-pointed in that copy — this document
does not claim to run two batches concurrently out of one checkout.

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
# ⛔ ...and WHICH tree, not merely a clean one. This fence is usually a LATER shell than Phase 2's, and
# `git switch` in any intervening shell — or a lane branch that was left checked out — makes this run
# measure a tree that is not the batch. Every other phase halts on its tree (Phase 1 and Phase 2
# `git switch -C … || exit 1`, Phase 4 `git switch integration/batch-1 || exit 1`); the gate phase is
# the one whose tree the verdict is ABOUT, so it asserts the branch Phase 2 created.
[ "$(git rev-parse --abbrev-ref HEAD)" = integration/batch-1 ] || { echo "not on integration/batch-1 (HEAD is $(git rev-parse --abbrev-ref HEAD)) — the gate would measure that tree, not the batch: switch to the batch branch and re-run"; exit 1; }

# Half 1 — the deterministic suite. Detached and POLLED, never one blocking call: a wedged
# foreground run takes the whole lane down with it and leaves no log.
# ⛔ The STATUS is written by the run's OWN shell, never recovered with `wait`. `wait` only reaps THIS
# shell's children, so `wait` cannot be used at all. Measured (bash 5.2): `bash: wait: pid N is not a
# child of this shell`, rc=127 — it declared a COMPLETED, green run UNPROVEN and printed the wrong
# diagnosis. The poll below reads the file the run's OWN shell writes, so it is correct in THIS shell
# or in a LATER one — and it WAITS before it reads, which is what makes this fence runnable.
# The subshell writes `$?` to a file, so no step here is shell-local.
# ⛔ Re-derive the ledger directory: this fence is usually a LATER shell than Phase 0's, and `$B` is
# a DERIVATION, not a carrier. A fence that skipped this would `rm -f` and write at `$B`'s path with
# `$B` empty — a bare /tmp name that a peer batch writes too, i.e. exactly the ledger sharing this
# section exists to prevent.
BATCH="${BATCH:?set BATCH to this batch's own name (e.g. 1 for integration/batch-1) — an empty one means another batch's ledger}"
B="/tmp/batch-$BATCH"
[ -d "$B" ] || { echo "no ledger directory $B — Phase 0 creates it, per batch; absent means BATCH is unset or names another batch"; exit 1; }
# ⛔ And the LANE, for the same reason and with the same consequence: this fence is USUALLY a later
# shell than Phase 0's, and a run with no lane ERRORs every item and exits 1 — which the COMPLETED
# predicate below accepts, so both id sets become all-error and identical and Phase 4 diffs them to
# BATCH-GREEN. An absent lane HALTS here; the Phase 0 export is not carried, and a carve-out is an
# explicit opt-in whose verdict is UNPROVEN-FULL-SUITE. The predicate is the GATE's (`tests/_embedded.py`
# `_uri_set_supported` OR `_carve_out_opted_in`), never "the variable is set" — see Phase 1: a
# set-but-unsupported `bolt://` and a `TORTOISE_TEST_CARVE_OUT=0` both satisfy a plain `-n` test and
# both ERROR every item at setup.
uv run python -c 'import sys; from tests._embedded import _carve_out_opted_in as opted_in, _uri_set_supported as supported; sys.exit(0 if supported() or opted_in() else 1)' || { echo "no USABLE lane in this shell — the gate is not \"is the variable set\": tests/_embedded.py's _assert_p4_uri_required accepts only a SUPPORTED TORTOISE_DB_URI (docker/redis/rediss — tortoise/config.py's SUPPORTED_URI_SCHEMES) or a TRUTHY TORTOISE_TEST_CARVE_OUT (1/true/yes/on — tortoise/env_truthy.py), so a set-but-unsupported bolt:// and a =0 both PASS a plain -n test and then ERROR every collected item at setup, exiting 1 — a status COMPLETED accepts. The Phase 0 export does not cross a shell boundary: export it, re-derive it with eval \"\$(uv run python tools/test_lane.py uri)\", or opt into the carve-out explicitly"; exit 1; }
rm -f "$B/batch.rc" "$B/batch-junit.xml"   # a stale status/report from an earlier run must not decide this one
( uv run pytest tests/ -q --junitxml="$B/batch-junit.xml" -m 'not track_b and not live and not integration' --ignore=tests/e2e > "$B/batch.log" 2>&1; echo $? > "$B/batch.rc" ) &
# ⛔ WAIT before the poll, and BOUND the wait. The suite is detached (the `&` above), so a one-shot
# read on the very next line is not a poll — it is a race this fence loses every time: the block would
# print RUNNING and the halt below would then report "never launched in this shell" about a run it
# launched one line earlier, and half 2 — the `track_b` gate member — would never be reached. So the
# fence as written measured nothing on any tree. The run stays detached, so it outlives this shell.
# ⛔ And the bound must OUTLAST the run it bounds, or it is not a wedge detector — it is a false halt.
# This fence carried `240 × 15 s = 60 min`, which is UNDER the run it waits for, by the document's own
# measurements: `docs/ops/ci-wall-clock.md`'s `test (a…i)` p50s serialise to ≈91–134 min, `test-slow`
# is 37.05 min of measured test time (`config/ci-surfaces.yml`), and `test-carve-out` is 8.3–8.6 min ×
# 3 shards ≈ 25 min — ≈153–196 min in the ONE process this command runs (no xdist, no leg partition),
# before collecting 41.7k tests. CI gets ~37 min of wall clock out of that only by sharding across
# runners. 360 × 60 s = 6 h is ≈1.8× that worst documented composition, so an honest full run cannot
# reach it while a wedged one is still released. Phase 1 runs the byte-identical command with no bound
# at all; the same command cannot be expected to finish in Phase 1 and not here.
for _ in $(seq 1 360); do [ -f "$B/batch.rc" ] && break; sleep 60; done
[ -f "$B/batch.rc" ] && echo 'DONE — read $B/batch.rc' || echo RUNNING   # reachable now: the wait above

# ⛔ DONE is not PASSED. A pid that goes away fails exactly as fast for a run the kernel OOM-killed as
# for one that finished — measured on #10399, five `python` processes were killed in 23 minutes — and a
# killed run prints no summary, hence carries no failure ids, so Phase 4 would diff it straight to
# BATCH-GREEN. The status the run itself wrote is the only thing that separates them; require a
# COMPLETED report:
# ⛔ And the wait's EXPIRY does not mean the run stopped: the run stays detached and outlives this
# shell, so re-running this fence would hit the `rm -f` above for a suite that is STILL EXECUTING —
# two runs then share this batch's `$B`, whichever finishes first owns the ledger, and the fence reads
# that. That is the per-batch-ledger collision this document already closes BETWEEN batches, one level
# down INSIDE one batch, where no directory namespacing can separate them. A live pytest writes to its
# log continuously, so a log written in the last 10 minutes IS the run: wait for `$B/batch.rc` instead
# of restarting, and kill the suite first if you are certain it is wedged. Both halts are keyed on
# `$B/batch.rc` being ABSENT, so neither can fire at a run that already finished.
[ -f "$B/batch.rc" ] || [ -n "$(find "$B/batch.log" -newermt '-10 minutes' 2>/dev/null)" ] || { echo "no $B/batch.rc after the 6 h bounded wait, and $B/batch.log has not been written in 10 min — the gate run either never launched in this shell or stopped without writing a status (read $B/batch.log); a run that DID launch writes $B/batch.rc even when it is killed, so this is a shell mistake before it is a wedge"; exit 1; }
[ -f "$B/batch.rc" ] || { echo "no $B/batch.rc after the 6 h bounded wait, but $B/batch.log was written in the last 10 min — a gate run is STILL LIVE and will write its status when it finishes: wait for that instead of re-running (a re-run clears the status of a suite that is still EXECUTING, and the two runs then share this ledger), and kill the suite FIRST if you are certain it is wedged"; exit 1; }
RC="$(cat "$B/batch.rc")"
[ "$RC" -eq 0 ] || [ "$RC" -eq 1 ] || { echo "the gate run did not COMPLETE (rc=$RC; 137=SIGKILL/OOM) — UNPROVEN-FULL-SUITE(<lane>), never green"; exit 1; }
# ⛔ The baseline TOTAL does not survive Phase 1's shell either: Phase 3 may be a LATER shell, where
# the comparison below would run against an empty string (bash: `integer expression expected`, rc=2)
# and the halt would report a COMPLETE, green run as SHORTER than the baseline. Read the file Phase 1
# wrote, exactly as the status and the pin are read, and refuse an absent one.
[ -s "$B/baseline.total" ] || { echo "no $B/baseline.total — Phase 1 never recorded the baseline count; re-run Phase 1 (Landing route 1)"; exit 1; }
BASELINE_TOTAL="$(cat "$B/baseline.total")"
# Same as Phase 1's total: `awk` always exits 0, so a `|| exit 1` here would be an unreachable halt.
# The `-ge` comparison below is the real gate.
BATCH_TOTAL="$(grep -o 'tests="[0-9]*"' "$B/batch-junit.xml" | grep -o '[0-9]*' | awk '{s+=$1} END{print s+0}')"
[ -n "$BATCH_TOTAL" ] && [ "$BATCH_TOTAL" -ge "$BASELINE_TOTAL" ] || { echo "the gate run reported $BATCH_TOTAL tests against a baseline of $BASELINE_TOTAL — SHORTER than the baseline: investigate (a killed run, or a diff that legitimately removes or moves tests) — and never call it green: UNPROVEN-FULL-SUITE(<lane>)"; exit 1; }
# ⛔ SKIPPED is not PASSED, and neither the status nor the count can see it: a skipped test is still
# collected, so it is inside `tests=`, it adds no failure id, and Phase 4's diff is content. A
# `live_proj`-style socket probe that stops reaching FalkorDB skips the whole live suite with rc=0 —
# the tree is then certified green with the suite this document exists to gate unrun (#1382). Halt
# on any skip the BASELINE did not have. Baseline-diffed on purpose: the batch lane's `embedded_only`
# skips are expected, so a whole-run gate (`tools/skip-guard.py` un-diffed) would false-red.
[ -s "$B/baseline.skipped" ] || { echo "no $B/baseline.skipped — Phase 1 never recorded the baseline's skips; re-run Phase 1 (Landing route 1)"; exit 1; }
BASELINE_SKIPPED="$(cat "$B/baseline.skipped")"
BATCH_SKIPPED="$(grep -o 'skipped="[0-9]*"' "$B/batch-junit.xml" | grep -o '[0-9]*' | awk '{s+=$1} END{print s+0}')"
[ -n "$BATCH_SKIPPED" ] && [ "$BATCH_SKIPPED" -le "$BASELINE_SKIPPED" ] || { echo "the gate run SKIPPED $BATCH_SKIPPED tests against a baseline of $BASELINE_SKIPPED — a skip carries no failure id, so the id diff cannot see it (a FalkorDB outage mid-run skips the whole live suite at rc=0): UNPROVEN-FULL-SUITE(<lane>), never green"; exit 1; }
# ...and the REASONS, because an equal COUNT can be a SWAP (one live-FalkorDB skip traded for a
# collection skip). Every reason the batch has and the baseline did not is a test that had to run and
# did not; `tools/skip-guard.py` guards the same three families upstream in CI.
grep -o '<skipped [^>]*' "$B/batch-junit.xml" | sed -n 's/.*message="\([^"]*\)".*/\1/p' | sort > "$B/batch.skipped-reasons"
comm -13 "$B/baseline.skipped-reasons" "$B/batch.skipped-reasons" > "$B/new-skip-reasons"
[ -s "$B/new-skip-reasons" ] && { echo "the gate run has SKIP REASONS the baseline did not (listed in $B/new-skip-reasons) — a test that must run did not, and no failure id records it: UNPROVEN-FULL-SUITE(<lane>), never green"; exit 1; }

# Half 2 — the track_b leg CI runs as its own MUST-PASS, gate-member job (`-m track_b`).
# ⛔ Instrumented exactly like half 1, because the fail-open is the same one: rc 0/1 is a COMPLETED
# run and anything else is a run that did not finish, and the junit report is what puts its ids in
# the Phase 4 diff. Without the report a red `track_b` half has ids in NEITHER set, so the diff
# reads it as green — the MUST-PASS gate member the gate phase exists to observe, unobserved.
rm -f "$B/batch-trackb.xml" "$B/batch-trackb.skipped-reasons" "$B/new-skip-reasons-trackb"
uv run pytest tests/test_ingest_safety.py -q --junitxml="$B/batch-trackb.xml" -m track_b
TRACK_B_RC=$?
[ "$TRACK_B_RC" -eq 0 ] || [ "$TRACK_B_RC" -eq 1 ] || { echo "the track_b half did not COMPLETE (rc=$TRACK_B_RC; 137=SIGKILL/OOM) — UNPROVEN-FULL-SUITE(<lane>), never green"; exit 1; }
# ⛔ ...and half 2's SKIPS, exactly as half 1's — the MUST-PASS gate member is the half where a skip
# is MOST likely and LEAST visible: `TRACK_B_SKIP` is a `skipif` on a feature probe, so a constituent
# PR that removes the probe's subject turns all four tests SKIPPED, the half exits 0 (pytest has no
# all-skipped exit code), its `tests=` count is unchanged and a skip carries no failure id. Without
# these three lines the ONE half the verdict table's "it skipped tests the baseline did not skip"
# clause cannot see is the half whose whole purpose is the gate member.
[ -s "$B/baseline-trackb.skipped" ] || { echo "no $B/baseline-trackb.skipped — Phase 1 never recorded the track_b half's skips; re-run Phase 1 (Landing route 1)"; exit 1; }
BASELINE_TRACK_B_SKIPPED="$(cat "$B/baseline-trackb.skipped")"
BATCH_TRACK_B_SKIPPED="$(grep -o 'skipped="[0-9]*"' "$B/batch-trackb.xml" | grep -o '[0-9]*' | awk '{s+=$1} END{print s+0}')"
[ -n "$BATCH_TRACK_B_SKIPPED" ] && [ "$BATCH_TRACK_B_SKIPPED" -le "$BASELINE_TRACK_B_SKIPPED" ] || { echo "the track_b half SKIPPED $BATCH_TRACK_B_SKIPPED tests against a baseline of $BASELINE_TRACK_B_SKIPPED — the MUST-PASS gate member ran without running, and a skip carries no failure id: UNPROVEN-FULL-SUITE(<lane>), never green"; exit 1; }
grep -o '<skipped [^>]*' "$B/batch-trackb.xml" | sed -n 's/.*message="\([^"]*\)".*/\1/p' | sort > "$B/batch-trackb.skipped-reasons"
comm -13 "$B/baseline-trackb.skipped-reasons" "$B/batch-trackb.skipped-reasons" > "$B/new-skip-reasons-trackb"
[ -s "$B/new-skip-reasons-trackb" ] && { echo "the track_b half has SKIP REASONS the baseline did not (listed in $B/new-skip-reasons-trackb) — a gate-member test that must run did not, and no failure id records it: UNPROVEN-FULL-SUITE(<lane>), never green"; exit 1; }
# ⛔ RECORD the TESTED TREE by VALUE — copy the printed line into the batch PR body beside
# `pinned-base:`. This is what Landing's post-merge detector compares the landed commit's tree
# against, and it cannot be a branch: `integration/batch-1` is a LOCAL ref nothing in this doctrine
# pushes, and a ref records where the branch is LATER (after a revert, or after any commit) rather
# than the tree this run measured. Everything above has now been verified at this tip, so this is it.
TESTED_TREE="$(git rev-parse "integration/batch-1^{tree}")" || exit 1
[ -n "$TESTED_TREE" ] || { echo "the batch branch has no readable tree — nothing to record, and Landing would have nothing to compare the landing against"; exit 1; }
printf 'tested-tree: %s\n' "$TESTED_TREE"
```

A run that skips half 2 has not run the gate, so it cannot produce `BATCH-GREEN` — see the verdict
table and the `UNPROVEN-FULL-SUITE(<lane>)` rule.

### 4. Compare to the baseline and blame

- **The id sets are the junit reports, one per half**: `$B/batch-junit.xml` **and**
  `$B/batch-trackb.xml`, against `$B/baseline-junit.xml` **and** `$B/baseline-trackb.xml`.
  Diffing the deterministic half alone leaves every `track_b` id in neither set — which is how a red
  MUST-PASS gate member reads as green. **After a drop, those two files hold the post-revert
  re-check** — the fence below CLEARS and rewrites both, so the diff can never be against the tree
  that still contained the culprit.
- **Build the ledger before you classify it.** The three bullets in this section classify on a diff
  of failure ids, and until round 39 this document named the four reports and **never built the
  sets** — no command extracted an id or diffed anything, so whether a PR was dropped turned on how
  two operators happened to spell one. Everything else in the verdict path (lane, tree, COMPLETED
  status, test count, skips by count and by reason) is a fence with a halt; this is the same shape,
  including the halt: a report that says it failed while the rule extracts **no** id is
  `UNPROVEN-FULL-SUITE(<lane>)`, never green. **Re-run it after a drop**: the post-revert re-check
  CLEARS and rewrites both batch reports, so a ledger built before the revert describes the tree
  that still contained the culprit.

```bash
# ⛔ ONE id RULE, BOTH SIDES OF THE DIFF. The classification below is a set difference, so the id
# has to be DEFINED — and it is the report schema's definition, not this document's convenience:
# pytest prints the xsd it targets, where `<testcase>` has `name` REQUIRED and `classname` optional
# and its children are `<skipped>?`, `<error>*`, `<failure>*`, `<system-out>*`, `<system-err>*`
# (<https://docs.pytest.org/en/stable/how-to/output.html#creating-junitxml-format-files>).
#
#     failure id = "<classname>::<name>" of every <testcase> that carries a <failure> or <error> CHILD
#
# The child ELEMENT is what makes a case failing — never the line it sits on and never the text next
# to it. `classname` is also overridable per case with `record_xml_attribute` (same page), so the id
# is READ OFF the report and never re-derived from a file path or a module name. It is parsed as XML
# because pytest writes the WHOLE document on one line (measured: `<testcase …/><testcase …><failure
# …></failure></testcase>` all on line 2 of a real 6-test report), so no grep/awk pairing of adjacent
# lines can work at all — the first draft of this fence would have extracted nothing, forever.
BATCH="${BATCH:?set BATCH to this batch's own name (e.g. 1 for integration/batch-1) — an empty one means another batch's ledger}"
B="/tmp/batch-$BATCH"
[ -d "$B" ] || { echo "no ledger directory $B — Phase 0 creates it, per batch; absent means BATCH is unset or names another batch"; exit 1; }
[ -s "$B/batch-junit.xml" ] || { echo "no $B/batch-junit.xml — Phase 3 (or the post-revert re-check) writes it; an absent report means that half never ran here, and 'no ids' is not 'no failures'"; exit 1; }
[ -s "$B/batch-trackb.xml" ] || { echo "no $B/batch-trackb.xml — Phase 3 (or the post-revert re-check) writes it; without it every track_b id sits in NEITHER side of the diff"; exit 1; }
[ -s "$B/baseline-junit.xml" ] || { echo "no $B/baseline-junit.xml — Phase 1 writes it; without it every id in the batch reads as NEW"; exit 1; }
[ -s "$B/baseline-trackb.xml" ] || { echo "no $B/baseline-trackb.xml — Phase 1 writes it; without it every track_b id in the batch reads as NEW"; exit 1; }
for side in batch-junit batch-trackb baseline-junit baseline-trackb; do
  uv run python - "$B/$side.xml" "$B/$side.failures" <<'PY' || exit 1
import sys
import xml.etree.ElementTree as ET
report, ledger = sys.argv[1], sys.argv[2]
failing = sorted(
    f"{(case.get('classname') or '').strip()}::{(case.get('name') or '').strip()}"
    for case in ET.parse(report).getroot().iter('testcase')
    if case.find('failure') is not None or case.find('error') is not None
)
with open(ledger, 'w', encoding='utf-8') as handle:
    handle.writelines(f"{one}\n" for one in failing)
PY
done
cat "$B/batch-junit.failures" "$B/batch-trackb.failures" | sort -u > "$B/batch-failures"
cat "$B/baseline-junit.failures" "$B/baseline-trackb.failures" | sort -u > "$B/baseline-failures"
comm -13 "$B/baseline-failures" "$B/batch-failures" > "$B/new-failures"           # only in the batch — NEW, convicts a PR
comm -12 "$B/baseline-failures" "$B/batch-failures" > "$B/baseline-still-failing"  # in BOTH — BASELINE(<ids>), exempt
comm -23 "$B/baseline-failures" "$B/batch-failures" > "$B/cleared-failures"        # only on main — neither convicts nor exempts
# ⛔ FAIL CLOSED on a ledger the rule could not READ. A report whose own `failures=`/`errors=`
# attributes are non-zero while its extracted id set came out EMPTY means the rule and the producer
# have drifted apart (a `junit_family` change, a rewritten `record_xml_attribute` classname, a
# rewritten report) — and an EMPTY id set diffs empty in both directions, which the verdict table
# would print as `BATCH-GREEN` over a red tree. An undefined id is never green.
for side in batch-junit batch-trackb baseline-junit baseline-trackb; do
  reported="$(grep -o -e 'failures="[0-9]*"' -e 'errors="[0-9]*"' "$B/$side.xml" | grep -o '[0-9]*' | awk '{s+=$1} END{print s+0}')"
  [ "$reported" -eq 0 ] || [ -s "$B/$side.failures" ] || { echo "$B/$side.xml reports $reported failing/erroring cases but the id rule extracted NONE from it — the ledger is UNDEFINED, not empty: UNPROVEN-FULL-SUITE(<lane>), never green"; exit 1; }
done
```

- **A failure id that is in the baseline set is a `BASELINE(<ids>)` failure** — read the ids from
  `$B/baseline-still-failing`. Not a PR's fault. Do not drop a PR for it, and do not fix it inside the
  batch. One line on whether the batch makes it worse.
- **A failure id that is NOT in the baseline set is NEW** and convicts a PR — that is exactly
  `$B/new-failures`. Find which one:

```bash
# ⛔ THE LANE FIRST — Phase 4 is the LATEST shell of all. Phases 1 and 3 state it, Landing is "a
# different actor"; this section is where the operator reads a wall of `ERROR at setup` and calls it a
# culprit. `_p4_uri_required` is session-scoped and autouse, so with no usable lane EVERY item
# ERRORs at setup and pytest exits 1 — a status the grader below accepts as "this commit is bad".
# The predicate is the GATE's (`tests/_embedded.py`'s `_uri_set_supported` OR
# `_carve_out_opted_in`), never a plain `-n`: a set-but-unsupported `bolt://` and a
# `TORTOISE_TEST_CARVE_OUT=0` both pass one and both ERROR every item — see Phase 1.
uv run python -c 'import sys; from tests._embedded import _carve_out_opted_in as opted_in, _uri_set_supported as supported; sys.exit(0 if supported() or opted_in() else 1)' || { echo "no USABLE lane in this shell — the gate is not \"is the variable set\": tests/_embedded.py's _assert_p4_uri_required accepts only a SUPPORTED TORTOISE_DB_URI (docker/redis/rediss — tortoise/config.py's SUPPORTED_URI_SCHEMES) or a TRUTHY TORTOISE_TEST_CARVE_OUT (1/true/yes/on — tortoise/env_truthy.py), so a set-but-unsupported bolt:// and a =0 both PASS a plain -n test and then ERROR every collected item at setup, exiting 1 — a status COMPLETED accepts. The Phase 0 export does not cross a shell boundary: export it, re-derive it with eval \"\$(uv run python tools/test_lane.py uri)\", or opt into the carve-out explicitly"; exit 1; }
git log --first-parent --merges --oneline origin/main..integration/batch-1   # merges ATTEMPTED — skip any with a matching Revert
git bisect start --first-parent integration/batch-1 origin/main   # or, cheaper, walk the merges
#   at each step: run ONLY the failing files (not the whole suite)
#   git bisect good <merge sha> / git bisect bad <merge sha>
uv run pytest <the failing file(s)> -q
```

  `git bisect run` is the honest tool when the failing file set is stable:

```bash
# ⛔ The lane AGAIN — this fence is a different shell from the one above, and a probe with no lane
# ERRORs every item and exits 1, which is the grader's ACCEPTED "bad" status: every probe then reads
# BAD and `bisect run` convicts the OLDEST merge in the batch (measured, and stated two lines below).
uv run python -c 'import sys; from tests._embedded import _carve_out_opted_in as opted_in, _uri_set_supported as supported; sys.exit(0 if supported() or opted_in() else 1)' || { echo "no USABLE lane in this shell — the gate is not \"is the variable set\": tests/_embedded.py's _assert_p4_uri_required accepts only a SUPPORTED TORTOISE_DB_URI (docker/redis/rediss — tortoise/config.py's SUPPORTED_URI_SCHEMES) or a TRUTHY TORTOISE_TEST_CARVE_OUT (1/true/yes/on — tortoise/env_truthy.py), so a set-but-unsupported bolt:// and a =0 both PASS a plain -n test and then ERROR every collected item at setup, exiting 1 — a status COMPLETED accepts. The Phase 0 export does not cross a shell boundary: export it, re-derive it with eval \"\$(uv run python tools/test_lane.py uri)\", or opt into the carve-out explicitly"; exit 1; }
# ⛔ GRADE THE EXIT CODE. `git bisect run`'s protocol is 0 = good, 125 = SKIP THIS PROBE, anything
# else = BAD. A bare `|| exit 1` collapses every other status into "bad": pytest rc 2/3/4/5
# (interrupted / usage / internal / misconfigured) and 128+N (SIGKILL/OOM — the class this document
# measured five times in 23 minutes on #10399) all become "this commit is the culprit". Measured in
# a scratch repo against a five-commit batch whose probe always exits 2: `bisect run` printed
# "first bad commit" and named the OLDEST probe — the first merge in the batch — and the next step
# below would DROP an innocent PR for an infrastructure error, irreversibly within the batch. Accept
# only the two statuses this document already calls COMPLETED, and hand every other one back to git
# as its own skip code, so the probe is removed from the search instead of convicted.
# ⛔ And a probe that could not RUN the suite is not a probe that FAILED it — git's own manual's
# example is `make || exit 125   # this skips broken builds`. pytest assigns ONE status to both
# meanings: an all-setup-ERROR session exits 1, exactly as a real red does, and the lane can go away
# BETWEEN the assertion above and a probe (the private container reaped, a Docker restart). So the
# probe captures the output and declines to convict on the gate's own message: the session-fixture
# failure is `Failed: default pytest requires TORTOISE_DB_URI …` (`tests/_embedded.py`), which no
# genuine test failure carries. `exit 125` removes that probe instead of blaming it.
git bisect run sh -c 'out=$(uv run pytest <failing files> -q 2>&1); rc=$?; case "$out" in *"default pytest requires TORTOISE_DB_URI"*) exit 125;; esac; [ "$rc" -eq 0 ] && exit 0; [ "$rc" -eq 1 ] && exit 1; exit 125'
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
  excludes `-m track_b` is not a green). ⛔ That re-run is not a sentence: it is the run whose result
  is read into the Phase 5 verdict row, so it carries **every** detector Phase 3 carries. A bare
  `pytest tests/` here would have no lane assertion, no tree assertion, no COMPLETED predicate, no
  count and no skip check — the same fail-open this doctrine already closed for the baseline and for
  the `track_b` half, one run further on. And it writes Phase 3's **own** report paths, cleared
  first: `pytest` truncates a junit file only in `pytest_sessionfinish`, so without the clear the
  Phase 4 diff would compare the baseline against the report of the tree that still contained the
  culprit.

  ```bash
  # ⛔ Same tree assertion as Phase 3. The revert just committed ON integration/batch-1, so this
  # fence is usually a LATER shell again — and a run on any other branch measures a tree nobody
  # drops from and nobody lands.
  [ -z "$(git status --porcelain)" ] || { echo "dirty tree — the re-check would measure it, not the reverted batch"; exit 1; }
  [ "$(git rev-parse --abbrev-ref HEAD)" = integration/batch-1 ] || { echo "not on integration/batch-1 (HEAD is $(git rev-parse --abbrev-ref HEAD)) — the re-check would measure that tree, not the reverted batch: switch to the batch branch and re-run"; exit 1; }
  BATCH="${BATCH:?set BATCH to this batch's own name (e.g. 1 for integration/batch-1) — an empty one means another batch's ledger}"
  B="/tmp/batch-$BATCH"
  [ -d "$B" ] || { echo "no ledger directory $B — Phase 0 creates it, per batch; absent means BATCH is unset or names another batch"; exit 1; }
  # ⛔ The GATE's lane predicate, not "the variable is set" (see Phase 1): a set-but-unsupported
  # `bolt://` URI and a `TORTOISE_TEST_CARVE_OUT=0` both pass a plain `-n` test and then ERROR every
  # collected item at setup, exiting 1 — which this fence's own COMPLETED predicate accepts.
  uv run python -c 'import sys; from tests._embedded import _carve_out_opted_in as opted_in, _uri_set_supported as supported; sys.exit(0 if supported() or opted_in() else 1)' || { echo "no USABLE lane in this shell — the gate is not \"is the variable set\": tests/_embedded.py's _assert_p4_uri_required accepts only a SUPPORTED TORTOISE_DB_URI (docker/redis/rediss — tortoise/config.py's SUPPORTED_URI_SCHEMES) or a TRUTHY TORTOISE_TEST_CARVE_OUT (1/true/yes/on — tortoise/env_truthy.py), so a set-but-unsupported bolt:// and a =0 both PASS a plain -n test and then ERROR every collected item at setup, exiting 1 — a status COMPLETED accepts. The Phase 0 export does not cross a shell boundary: export it, re-derive it with eval \"\$(uv run python tools/test_lane.py uri)\", or opt into the carve-out explicitly"; exit 1; }
  # ⛔ CLEAR BOTH halves first: these are Phase 3's paths, so a stale report from the PRE-revert run
  # would be diffed as if it were this one.
  rm -f "$B/batch.rc" "$B/batch-junit.xml" "$B/batch-trackb.xml" "$B/batch.skipped-reasons" "$B/new-skip-reasons"
  rm -f "$B/batch-trackb.skipped-reasons" "$B/new-skip-reasons-trackb"
  ( uv run pytest tests/ -q --junitxml="$B/batch-junit.xml" -m 'not track_b and not live and not integration' --ignore=tests/e2e > "$B/batch.log" 2>&1; echo $? > "$B/batch.rc" ) &
  # ⛔ The SAME bound as Phase 3's, for the same reason (see there): 240 × 15 s = 60 min is UNDER the
  # ≈153–196 min composition this command runs, so it halted every honest re-check instead of a wedge.
  for _ in $(seq 1 360); do [ -f "$B/batch.rc" ] && break; sleep 60; done
  [ -f "$B/batch.rc" ] && echo 'RECHECK DONE — read $B/batch.rc' || echo RUNNING
  # ...and the same refusal to re-run over a LIVE re-check: the wait's expiry is not the run's end.
  [ -f "$B/batch.rc" ] || [ -n "$(find "$B/batch.log" -newermt '-10 minutes' 2>/dev/null)" ] || { echo "no $B/batch.rc after the 6 h bounded wait, and $B/batch.log has not been written in 10 min — the re-check either never launched in this shell or stopped without writing a status (read $B/batch.log)"; exit 1; }
  [ -f "$B/batch.rc" ] || { echo "no $B/batch.rc after the 6 h bounded wait, but $B/batch.log was written in the last 10 min — the re-check is STILL LIVE and will write its status when it finishes: wait for that instead of re-running (a re-run clears the status of a suite that is still EXECUTING, and the two runs then share this ledger), and kill the suite FIRST if you are certain it is wedged"; exit 1; }
  RC="$(cat "$B/batch.rc")"
  [ "$RC" -eq 0 ] || [ "$RC" -eq 1 ] || { echo "the re-check run did not COMPLETE (rc=$RC; 137=SIGKILL/OOM) — UNPROVEN-FULL-SUITE(<lane>), never green"; exit 1; }
  [ -s "$B/baseline.total" ] || { echo "no $B/baseline.total — Phase 1 never recorded the baseline count; re-run Phase 1 (Landing route 1)"; exit 1; }
  BASELINE_TOTAL="$(cat "$B/baseline.total")"
  BATCH_TOTAL="$(grep -o 'tests="[0-9]*"' "$B/batch-junit.xml" | grep -o '[0-9]*' | awk '{s+=$1} END{print s+0}')"
  [ -n "$BATCH_TOTAL" ] && [ "$BATCH_TOTAL" -ge "$BASELINE_TOTAL" ] || { echo "the re-check reported $BATCH_TOTAL tests against a baseline of $BASELINE_TOTAL — SHORTER than the baseline: investigate (a killed run, or a diff that legitimately removes or moves tests) — and never call it green: UNPROVEN-FULL-SUITE(<lane>)"; exit 1; }
  [ -s "$B/baseline.skipped" ] || { echo "no $B/baseline.skipped — Phase 1 never recorded the baseline's skips; re-run Phase 1 (Landing route 1)"; exit 1; }
  BASELINE_SKIPPED="$(cat "$B/baseline.skipped")"
  BATCH_SKIPPED="$(grep -o 'skipped="[0-9]*"' "$B/batch-junit.xml" | grep -o '[0-9]*' | awk '{s+=$1} END{print s+0}')"
  [ -n "$BATCH_SKIPPED" ] && [ "$BATCH_SKIPPED" -le "$BASELINE_SKIPPED" ] || { echo "the re-check SKIPPED $BATCH_SKIPPED tests against a baseline of $BASELINE_SKIPPED — a skip carries no failure id, so the id diff cannot see it: UNPROVEN-FULL-SUITE(<lane>), never green"; exit 1; }
  grep -o '<skipped [^>]*' "$B/batch-junit.xml" | sed -n 's/.*message="\([^"]*\)".*/\1/p' | sort > "$B/batch.skipped-reasons"
  comm -13 "$B/baseline.skipped-reasons" "$B/batch.skipped-reasons" > "$B/new-skip-reasons"
  [ -s "$B/new-skip-reasons" ] && { echo "the re-check has SKIP REASONS the baseline did not (listed in $B/new-skip-reasons) — a test that must run did not, and no failure id records it: UNPROVEN-FULL-SUITE(<lane>), never green"; exit 1; }
  # Half 2, instrumented exactly as in Phase 3 — a green that excludes `-m track_b` is not a green.
  rm -f "$B/batch-trackb.xml" "$B/batch-trackb.skipped-reasons" "$B/new-skip-reasons-trackb"
  uv run pytest tests/test_ingest_safety.py -q --junitxml="$B/batch-trackb.xml" -m track_b
  TRACK_B_RC=$?
  [ "$TRACK_B_RC" -eq 0 ] || [ "$TRACK_B_RC" -eq 1 ] || { echo "the re-check's track_b half did not COMPLETE (rc=$TRACK_B_RC; 137=SIGKILL/OOM) — UNPROVEN-FULL-SUITE(<lane>), never green"; exit 1; }
  # ...and half 2's SKIPS here too: this is the run whose result the verdict row reads, so a skip
  # check that exists only in Phase 3 certifies a post-revert tree on which the gate member skipped.
  [ -s "$B/baseline-trackb.skipped" ] || { echo "no $B/baseline-trackb.skipped — Phase 1 never recorded the track_b half's skips; re-run Phase 1 (Landing route 1)"; exit 1; }
  BASELINE_TRACK_B_SKIPPED="$(cat "$B/baseline-trackb.skipped")"
  BATCH_TRACK_B_SKIPPED="$(grep -o 'skipped="[0-9]*"' "$B/batch-trackb.xml" | grep -o '[0-9]*' | awk '{s+=$1} END{print s+0}')"
  [ -n "$BATCH_TRACK_B_SKIPPED" ] && [ "$BATCH_TRACK_B_SKIPPED" -le "$BASELINE_TRACK_B_SKIPPED" ] || { echo "the re-check's track_b half SKIPPED $BATCH_TRACK_B_SKIPPED tests against a baseline of $BASELINE_TRACK_B_SKIPPED — the MUST-PASS gate member ran without running, and a skip carries no failure id: UNPROVEN-FULL-SUITE(<lane>), never green"; exit 1; }
  grep -o '<skipped [^>]*' "$B/batch-trackb.xml" | sed -n 's/.*message="\([^"]*\)".*/\1/p' | sort > "$B/batch-trackb.skipped-reasons"
  comm -13 "$B/baseline-trackb.skipped-reasons" "$B/batch-trackb.skipped-reasons" > "$B/new-skip-reasons-trackb"
  [ -s "$B/new-skip-reasons-trackb" ] && { echo "the re-check's track_b half has SKIP REASONS the baseline did not (listed in $B/new-skip-reasons-trackb) — a gate-member test that must run did not, and no failure id records it: UNPROVEN-FULL-SUITE(<lane>), never green"; exit 1; }
  # ⛔ RE-RECORD the tested tree: the revert just changed it, so the line Phase 3 printed now names a
  # tree nobody will land. Landing compares the landed tree against THIS one.
  TESTED_TREE="$(git rev-parse "integration/batch-1^{tree}")" || exit 1
  [ -n "$TESTED_TREE" ] || { echo "the reverted batch branch has no readable tree — nothing to record, and Landing would have nothing to compare the landing against"; exit 1; }
  printf 'tested-tree: %s\n' "$TESTED_TREE"
  ```

  A batch that cannot be made green ships without the culprit, never with it.

### 5. Verdict — and it must be honest about what ran

| Verdict | Meaning |
|---|---|
| `BATCH-GREEN` | Full suite ran on the batched tree, no NEW failures vs baseline. It does **not** mean `python-ci-gate` passed: the `embedded_only` and record-postgres test legs, and the non-test legs, are skipped rather than reproduced (see the gate-legs list above). |
| `BATCH-GREEN(BASELINE(<ids>))` | Green *relative to baseline*; the listed ids were already red on main. |
| `BATCH-RED(<ids>)` | New failures remain after dropping the culprit(s). Do not merge. |
| `UNPROVEN-FULL-SUITE(<lane>)` | **The full suite did not run, did not COMPLETE, or the tests that must run SKIPPED.** Only the embedded carve-out or a subset ran; or the gate run was killed / interrupted and reported no final summary, so it has no failure set to diff; or it skipped tests the baseline did not skip — a skip is not a pass and carries no failure id, so the id diff cannot see it. This is never "green". |

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
  `pinned-base: <sha>` line the Landing check reads (Phase 1 prints it), the `tested-tree: <sha>` line
  its post-merge detector reads (Phase 3 prints it; re-record it whenever the batch branch's tree
  changes, i.e. after a revert), baseline vs batch matrix, per-PR attribution, verdict. Written with
  `--body-file`, never an inline body.
- The record of a *review* is not the record of a *batch*. Do not record a review of your own work
  (#825).
- Landing the batch is a separate, human-routed step — and the batch PR is opened as a **draft**,
  un-drafted only once the [landing assertion](#landing--main-must-still-be-the-tree-you-tested)
  passes, because `-draft` is a `.mergify.yml` queue condition and the one lever that keeps an
  unasserted batch out of the merge queue. The draft gates **entry**, not the merge: either declare
  route 2's freeze (the only route that closes the window) or run the post-merge tree detector, since
  `main` can still move inside the queue's own cycle. The batch lane never merges its own PR.

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
# ⛔ The fetch HALTS here too, and this is the fence where a stale ref is a FALSE PASS rather than a
# wrong ledger: with no `set -e`, a failed fetch leaves `origin/main` at the last successful fetch —
# normally the base the batch was pinned to — so `[ "$(git rev-parse origin/main)" = "$PINNED" ]`
# below PASSES against an unmoved local ref while live `main` has moved, the batch is un-drafted, and
# the queue squash-merges an untested composition onto the moved base with every check in this section
# green. That is the one failure this fence exists to prevent, so its only unhalted line cannot be the
# fetch. (The post-merge detector's own fetch halts the same way, and for its own version of the
# same reason: it would otherwise judge the landing against a stale `origin/main`.)
git fetch origin main || { echo "the fetch FAILED — refusing to compare the pin against a stale origin/main: the last successful fetch is not current main, and a green here would certify a base that has moved"; exit 1; }
# $PINNED does NOT survive Phase 1's shell — landing is a different actor.
# Read it from the batch PR body (Phase 1 recorded it there) and never from memory:
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

⛔ **The batch PR is opened as a DRAFT, and un-drafted only after the assertion above passes.** A bash
block in a document is not enforcement: nothing on the merge path invokes it. The batch PR is an
ordinary PR to `main`, so the moment it carries its required contexts it satisfies this repo's live
queue — `.mergify.yml`'s `queue_rules` declare `queue_conditions: base=main`, the `-draft` negation and
the `check-success` contexts, and `merge_protections_settings: auto_merge_conditions: true` — and is
squash-merged onto **whatever `main` is at that moment**, with this fence never run. The draft state is
the one thing the assertion can control, because `-draft` is itself a queue condition: a draft is
never queued, so it can never be merged. Open the batch PR as a draft, run the fence above, and
un-draft it only when the pin still matches (or route 2's freeze is declared and real). That converts
the assertion from a step someone may remember into a **queue-entry** condition — more than a bash
block, and strictly less than the guarantee bors and GitHub's queue get by owning the merge. The
difference is the residual window below, and it is the one part of this section that a reader is most
likely to over-credit.

⛔ **The draft gates ENTRY, not the MERGE — the residual window is real, and it is one queue cycle
wide.** `.mergify.yml`'s own configuration contract states the split in a single line:
`queue_conditions` gates **ENTRY** (evaluated against the PR head) and `merge_conditions` gates the
**MERGE** (evaluated against the queue branch). `-draft` is a `queue_conditions` entry, so
un-drafting makes the PR *eligible*; it does not pin the base. From there:

- `merge_method: squash` applies the batch's diff to `main` **as it is at merge time**, not to
  `$PINNED`;
- the queue does re-validate that moved composition — but its `merge_conditions` are
  `check-success=python-ci-gate` on the queue branch, i.e. the **tiered** selection selected by
  `config/ci-surfaces.yml` (the section above), never the full suite;
- `.github/settings.yml` leaves the branch protection's `strict` **false**, so nothing requires the
  batch branch to be updated onto the moved base before merging.

So the draft closes the gap between "the assertion passed" and "the PR became eligible". It does not
close the gap between eligibility and the merge, and that gap is not microseconds: `.mergify.yml`
measures a full CI cycle at ~37 minutes while `main` merges continuously — the very premise this
document is written on. A batch un-drafted at 09:00 and merged at 09:40 can land `PINNED' +
batch-diff`: a composition no full-suite run has seen, arriving after every check this section
describes has passed.

**Route 2 closes that window; route 1 and a plain pin-checked landing do not — so they carry a
post-merge detector.** The freeze (route 2) is the only route that makes the base unable to move
inside the queue's cycle, and it is the route to take whenever the batch must land unattended. Where
the window is left open, do not paper over it: a squash-merge onto `PINNED` reproduces the tested
tree exactly, so the landed commit's **tree** must equal the tree the suite **measured** — the one
Phase 3 prints as `tested-tree: <sha>` (re-printed by Phase 4 after a revert) — and that is checkable
the moment it lands. It is compared **by value**, never by branch: the batch branch is a local ref
nothing here pushes, and a branch records where it is *later*, not what was tested. This is a
*detector* in the gate/detector split this document opens with: it does not prevent the landing, it
refuses to let the violation pass **silently**.

```bash
# Run AFTER the queue has landed the batch PR. Nothing here is carried from an earlier shell.
# ⛔ The fetch HALTS. With no `set -e`, a refused fetch (network, auth, DNS) leaves `origin/main` at
# the LAST SUCCESSFUL fetch — in the normal flow the pinned base itself — so the detector below would
# judge the landing against a commit that is not current `main`.
git fetch origin main || { echo "the fetch FAILED — refusing to compare against a stale origin/main: the last successful fetch is not current main, and this detector would then judge the wrong landing"; exit 1; }
LANDED="$(git rev-parse origin/main)"
[ -n "$(git rev-parse -q --verify "$LANDED^{commit}")" ] || { echo "origin/main does not resolve to a commit — re-fetch before concluding anything about the landing"; exit 1; }
# ⛔ The TESTED tree, carried BY VALUE in the batch PR body — never a branch and never a remote ref.
# `integration/batch-1` is a LOCAL branch that no step here pushes, so `origin/integration/batch-1`
# is either ABSENT (the landing actor is usually in a fresh clone — and the fetch then dies on a
# refspec nobody wrote, while a stale one makes a PERFECT landing read as a process failure: it
# differs by exactly the PR the revert dropped) or, worse, live but the WRONG OBJECT: a ref records
# where the branch is NOW, so one commit made after the run moves it while the tested tree stays
# behind, and the comparison then certifies a landing no run has seen. Phase 3 prints this line and
# Phase 4 re-prints it after a revert; it is read here, mechanically, and an absent line HALTS.
TESTED_TREE="$(gh pr view <batch PR> --json body --jq '.body' | sed -n 's/^tested-tree: //p')"
[ -n "$TESTED_TREE" ] || { echo "no tested-tree: line in the batch PR body — Phase 3 prints it and Phase 4 re-prints it after a revert; without it the landing cannot be checked at all, and an unchecked landing is how an untested composition reaches main"; exit 1; }
LANDED_TREE="$(git rev-parse "$LANDED"^{tree})" || exit 1
[ "$LANDED_TREE" = "$TESTED_TREE" ] || {
  echo "the landed tree is NOT the tree the suite tested — main moved inside the queue's cycle, or the batch branch changed after the run; report it as a process failure, never validate it after the fact; re-baseline and re-run phases 1-3"; exit 1
}
```

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
protections on the branch the batch lands on. `main` keeps the required contexts declared in
`.github/settings.yml` (`pricing-artifact`, `docs`, `test-isolation`, `license-surface`, `legal-e2e`,
`python-ci-gate`, `ai-review-gate`; `.github/scripts/check-required-set.py --live` is the assertion that
the file and the live protection agree — and the LIST is that file's, never a count written here), and
`.mergify.yml` re-checks `python-ci-gate` on the queue
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
