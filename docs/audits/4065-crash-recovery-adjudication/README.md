# PR #4065 — crash-recovery failure adjudication

**Question the merge rail could not answer:** in PR #4065's CI run, 11 tests in
`tests/test_event_log.py` / `tests/test_crash_recovery_e2e.py` went red. Main's recorded runs carry
no failure in those files, so the rail read "absence of a main-side measurement" as "cannot tell
introduced from pre-existing" and refused — correctly, rather than guess.

**Answer: PRE-EXISTING.** They are red on the PR's own **pre-PR base commit** `d7c102559` (before any
PR commit), with **identical failing node ids and identical assertion messages**, in the **same lane
and the same collection shape**. The PR's diff (`tortoise/assembly.py`, `tests/test_assembly_pure.py`)
does not touch them; no shared fixture, journal, or `_embedded.py` helper connects the two. Current
`origin/main` already carries the repair (#4229, `b49f5d6a0`) — the branch is simply **231 commits
behind**, and the recorded CI run measured a tree on which those tests had been stale since before
this PR existed.

**State:** `fix/3317-object-resolver-status` was rebased onto `origin/main f2b3e91b2` and pushed with a
pinned lease — new head **`c0c1ed4dd`** (was `1fb955511`). The 11 flagged tests are green there
(§7). Nothing was marked ready; the PR was **not** merged. No test was weakened, skipped, or
`xfail`ed anywhere in this work.

---

## 1. The lane — measured, not assumed

The failing CI job is **NOT** the docker lane. It is the **embedded / tier-2 URI-less** shape.

Evidence (`raw/ci-job-105877771740-lane-evidence.txt`, from
`gh api repos/daniel-ospina/tortoise/actions/jobs/105877771740/logs`, job `test (b)` on head
`1fb955511`, workflow run `35407858365`):

```
URI:                       (empty)
EXPECT_URI:                (empty)
CARVE_OUT:                 1
TORTOISE_DB_URI:           (empty)
TORTOISE_TEST_CARVE_OUT:   1
SKIPPED tests/test_assembly_pure.py:608: docker lane unavailable (pure tests unaffected)
```

`changes.outputs.full != true` for this PR, so the workflow's `Compute docker URI` step took the
`else` branch: `CARVE_OUT=1`, no URI. A docker-lane comparison would have been shape-blind and would
have silently waived exactly this failure class.

**Lane for every measurement below: EMBEDDED — `TORTOISE_TEST_CARVE_OUT=1`, `TORTOISE_DB_URI` unset.**

### Local measurement environment

The shared `.venv` in the primary checkout carries an editable finder whose `MAPPING` points
`tortoise` at an **unrelated worktree** (`.worktrees/fix/3498-control-plane-offloop`). Every run below
therefore used a `sitecustomize.py` shim that strips that finder, with the interpreter's `cwd` inside
a per-revision worktree, so each leg imports **its own** `tortoise/` tree. Verified per leg
(`import tortoise; print(tortoise.__file__)` → the worktree).

`TORTOISE_REAPER_MIN_UPTIME=86400` was set for the order-paired runs: this box is running ~237
embedded redis servers from other lanes, and the session-scoped `_redislite_hygiene` sweep exceeded the
CI's `--timeout=300` at session setup (82/26 setup ERRORs, observed twice). The knob makes the
conftest hygiene sweep a fast no-op; it touches no code under test (the event-log tests are pure, no
DB, no redislite). It is an environment control, not a lane change — the lane stays embedded.

## 2. The failures are only observable in the CI *collection shape*

`tests/test_event_log.py` on the pre-#4229 tree imports the **bare top-level** `shared_state`
package. That package does not exist at the repo root; it resolves to `tortoise/shared_state/` only
because an **earlier-collected module** has already inserted `tortoise/` on `sys.path`:

```
# tortoise/tortoise_client.py
_TORTOISE_ROOT = Path(__file__).resolve().parent
if str(_TORTOISE_ROOT) not in sys.path:
    sys.path.insert(0, str(_TORTOISE_ROOT))
```

`tests/test_tortoise_client.py` is the **first file in the CI `test (b)` FILES list** and does
`from tortoise import tortoise_client` at module import — i.e. during **collection**. Every later
module in the half can then reach `tortoise/shared_state/`.

Consequence, and the trap the rail must not fall into:

| shape | stale tree (PR head / base) |
|---|---|
| file run **standalone** | `1 skipped` — `collected 0 items / 1 skipped` (module-level skip; **zero failures, zero evidence**) |
| file run **after `test_tortoise_client.py`** (the CI shape) | **11 FAILED** |

So "run the file on the branch and see" is not sufficient: standalone it *skips*, which reads as
green. The order-paired invocation below reproduces the CI condition on both revisions.

## 3. Measurements (all embedded lane, all raw output committed)

Order-paired command (identical on every leg):

```
TORTOISE_TEST_CARVE_OUT=1 TORTOISE_REAPER_MIN_UPTIME=86400 \
  python -m pytest tests/test_tortoise_client.py tests/test_event_log.py \
                   tests/test_crash_recovery_e2e.py \
    --deselect tests/test_tortoise_client.py -v -p no:cacheprovider \
    -m 'not track_b and not live' --timeout=300 -r fEs
```

`--deselect` drops `test_tortoise_client.py`'s own tests but still **imports the module during
collection** — that import is the whole trigger; running its tests adds nothing but runtime.

| leg | revision | result | raw |
|---|---|---|---|
| **CI run** | PR head `1fb955511` | **11 failed**, 15 passed, 66 deselected | `raw/ci-job-105877771740-lane-evidence.txt` (CI's own log) |
| stand-alone | PR head `1fb955511` | **1 skipped** (0 items collected) | `raw/head-once-standalone.txt` |
| order-paired #1 | PR head `1fb955511` | **11 failed**, 15 passed, 66 deselected | `raw/head-run1-orderpaired.txt` |
| order-paired #2 | PR head `1fb955511` | **11 failed**, 15 passed, 66 deselected | `raw/head-run2-orderpaired.txt` |
| **order-paired** | **pre-PR base `d7c102559`** | **11 failed**, 15 passed, 66 deselected | `raw/base-orderpaired.txt` |
| stand-alone | origin/main `f2b3e91b2` | **16 passed** | `raw/main-once-standalone.txt` |
| **order-paired** | origin/main `f2b3e91b2` | **26 passed**, 0 failed, 66 deselected | `raw/main-run2-orderpaired.txt` |
| **order-paired** | head **rebased** onto origin/main `c0c1ed4dd` | **26 passed**, 0 failed, 66 deselected | `raw/rebased-orderpaired.txt` |

`d7c102559` is `git merge-base origin/main 1fb955511` — the branch point. It is the PR's tree
**minus all three of its commits**, so the result there is attributable to the PR's *base*, not to
the PR.

The 11 failing node ids are byte-identical across the CI log, head runs #1/#2, and the base:

```
tests/test_event_log.py::TestCrashRecovery::test_scan_incomplete_all_complete
tests/test_event_log.py::TestCrashRecovery::test_scan_incomplete_missing
tests/test_event_log.py::TestCrashRecovery::test_scan_incomplete_failed_card
tests/test_event_log.py::TestCrashRecovery::test_recover_from_last_completed_step
tests/test_event_log.py::TestCrashRecovery::test_recover_ignores_other_cards
tests/test_crash_recovery_e2e.py::test_crash_recovery_full_cycle
tests/test_crash_recovery_e2e.py::test_crash_recovery_multiple_checkpoints
tests/test_crash_recovery_e2e.py::test_crash_recovery_no_crash_all_complete
tests/test_crash_recovery_e2e.py::test_crash_recovery_card_failed_is_complete
tests/test_crash_recovery_e2e.py::test_crash_recovery_concurrent_cards_independent
tests/test_crash_recovery_e2e.py::test_scan_incomplete_card_never_created
```

## 4. Classification of the five `test_event_log.py::TestCrashRecovery` tests

All five: **PRE-EXISTING.** Not flaky (identical failures on two independent head runs, and on the
base). Not introduced (they are red at `d7c102559`, the merge-base, where none of the PR's commits
exist).

| test | base `d7c102559` (`raw/base-orderpaired.txt`) | PR head `1fb955511` (`raw/head-run2-orderpaired.txt`) | origin/main `f2b3e91b2` (`raw/main-run2-orderpaired.txt`) |
|---|---|---|---|
| `test_scan_incomplete_all_complete` | `FAILED ... AssertionError: assert ['c1'] == []` | `FAILED ... AssertionError: assert ['c1'] == []` | `PASSED` |
| `test_scan_incomplete_missing` | `FAILED ... AssertionError: assert ['c1', 'c2'] == ['c2']` | `FAILED ... AssertionError: assert ['c1', 'c2'] == ['c2']` | `PASSED` |
| `test_scan_incomplete_failed_card` | `FAILED ... AssertionError: assert ['c1'] == []` | `FAILED ... AssertionError: assert ['c1'] == []` | `PASSED` |
| `test_recover_from_last_completed_step` | `FAILED ... AssertionError: assert None == 's1'` | `FAILED ... AssertionError: assert None == 's1'` | `PASSED` |
| `test_recover_ignores_other_cards` | `FAILED ... AssertionError: assert None == 's1'` | `FAILED ... AssertionError: assert None == 's1'` | `PASSED` |

The six `test_crash_recovery_e2e.py` failures (`raw/base-orderpaired.txt` vs
`raw/head-run2-orderpaired.txt`) are the same root cause and the same status: PRE-EXISTING.

## 5. Does a mechanism connect the PR to these failures?

**No — and this is asserted from measurement, not from the names looking different.**

* The PR's diff is exactly two files (`raw/mechanism.txt` §4): `tortoise/assembly.py` (+101/−7) and
  `tests/test_assembly_pure.py` (+152). Neither the event log, its registry, nor any test fixture.
* The failure's real mechanism is a vocabulary mismatch that predates the PR: `append_event` stores
  the event `type` **verbatim**, while `tortoise/shared_state/event_log.py` recognises completions by
  the **camelCase** names `cardCompleted` / `cardFailed` / `stepCompleted`; the stale test files
  register and append **snake_case** names (`card_created`, `card_completed`, …), so every
  completion is invisible and every card reads as incomplete.
* **The decisive test of relatedness is the base commit**, and it fails identically there. A PR
  cannot introduce a failure that is already present in its own base.
* The repair on main is `b49f5d6a0` *"test(shared-state): 26 event-log/crash-recovery tests were
  never running (and 11 were stale) (#4229)"* — which rewrites precisely these test files and
  `tools/skip-guard.py` (its own title says "**and 11 were stale**", matching these 11).

The one real cross-file coupling in this area is the **shared collection context** (`sys.path`
mutation at module import), which is why the failure is order-dependent — but that coupling exists
identically on the base and is not introduced or touched by this PR.

## 6. What the rail needs

The rail's own diagnosis is right: the blocker is a missing **base-side measurement**, not a code
defect in the PR. Options, in order of strength:

1. **Accept a base-side measurement artifact** for the flagged file(s), taken in the **same lane and
   the same collection shape**. This directory is that artifact: `raw/base-orderpaired.txt` shows the
   11 flagged node ids red on `d7c102559` — the PR's merge-base — with the same lane
   (embedded) and the same order-pairing that CI uses. Where a main-side run genuinely cannot reach
   a node id, the **merge-base** is the correct baseline, and for a stale branch it is the *only*
   baseline that isolates the PR's own commits.
2. **Re-measure after the rebase.** Current `origin/main` carries #4229, so rebasing the three PR
   commits onto `origin/main` turns all 11 green (see §7). A rebase-then-remeasure is the cheapest
   correct remedy when the branch is only carrying stale *files*.
3. A rail-side pre-condition worth noting (not a request for an exemption): for diff-selected
   (tier-2) runs, "main carries no failure in file F" is **structurally unavailable**, because F is
   only selected when its surface changes; and on the pre-#4229 tree this particular file
   *module-skips standalone*, so even a direct main-side run records "1 skipped", not a failure.
   Absence of a measurement there is guaranteed to be forged, which is exactly why the rail's
   refusal was correct — and why option 1 (an explicit merge-base artifact) is the way through.

No test was weakened, skipped, or `xfail`ed to produce this result, and nothing here is an
exemption: the five tests are red on the base and green on main, and the branch needs main's fix.

## 7. Rebase and re-measure — the remedy

Done, published, and measured.

```
$ git rebase origin/main     # in a worktree at 1fb955511, from origin/main f2b3e91b2
Rebasing (1/3)...(2/3)...(3/3)
Successfully rebased and updated detached HEAD.
$ git log --oneline -4
c0c1ed4dd fix(tests): pin the resolver's excluded set to exactly {retracted} (#3317)
4d8b1c8c6 fix(assembly): address code-review round 2 on the resolver status guard (#3317)
081ebd9d4 fix(assembly): exclude retracted Objects from every resolver leg (#3317)
f2b3e91b2 fix(dr): the eligibility gate and the archive listing must report what they MEASURED ... (#4321)
```

The rebase is **content-faithful**: `git diff origin/main HEAD` over the PR's two files is
`253 insertions(+), 7 deletions(-)` — exactly the PR's own `+152 / +101 / −7`. No conflict markers.
The three PR commits are intact (`git log f2b3e91b2..HEAD`).

Pushed with a pinned lease:

```
git push --force-with-lease=refs/heads/fix/3317-object-resolver-status:1fb9555112dabb8e0026c8d032ce752275981f0c \
  origin HEAD:refs/heads/fix/3317-object-resolver-status
 + 1fb955511...c0c1ed4dd HEAD -> fix/3317-object-resolver-status (forced update)
```

Re-measured, same lane and same command as every other leg:

```
26 passed, 66 deselected in 176.33s          # raw/rebased-orderpaired.txt
```

All 11 flagged tests are green. `test_crash_recovery_e2e.py` is green too (same root cause).

**The PR's own test delta survives the rebase** (`raw/rebased-assembly-pure.txt`):
`tests/test_assembly_pure.py` → **90 passed, 11 skipped**, including
`test_resolver_excluded_statuses_stay_inside_the_recall_excluded_set PASSED`. The two *new*
docker-lane tests (`test_resolver_docker_excludes_retracted_object`,
`test_walker_explicit_id_renders_retracted_status_verbatim`) **SKIP** in this lane — see §8.

**State:** `fix/3317-object-resolver-status` is now at **`c0c1ed4dd`** (rebased onto
`origin/main f2b3e91b2`). Not marked ready; not merged. The rail can re-measure at that head, where
the main-side baseline now exists by construction (the branch contains main).

## 8. What could NOT be verified here

* **The PR's two new docker-lane tests** (`test_resolver_docker_excludes_retracted_object`,
  `test_walker_explicit_id_renders_retracted_status_verbatim`) — they require the docker lane
  (`docker://:falkordb@localhost:6379/tortoise_test_matrix`); in the embedded lane they skip with
  `docker lane unavailable`. They are the PR's decisive new coverage and are **unverified by this
  lane**. The stale-base failures adjudicated above are pure tests and are unaffected by this gap.
* **The `test (a)` half** — this adjudication covered the `test (b)` half, which is the half that
  carried the failures. `test (a)` was green in the same CI run.
* The order-paired invocation is a **reduction** of the CI half (300+ files) to the minimal prefix
  that reproduces the collection condition. It matches CI's *shape* (embedded lane, the trigger
  module imported first, same `-m`/`-p`/`--timeout` flags) but is not the full half; the identical
  failing node ids and messages against CI's own log are the check that the reduction is faithful.
* Two order-paired attempts (one on main, one on head) hit `pytest-timeout` on conftest's embedded
  hygiene sweep at 300 s under this box's ~237 embedded servers. Both are retained
  (`raw/main-run1-orderpaired-FAILED-hygiene-timeout.txt`,
  `raw/head-run0-orderpaired-FAILED-hygiene-timeout.txt`) and the measurement was re-taken with
  `TORTOISE_REAPER_MIN_UPTIME=86400`.
