# PR #3577 / head `418cd7804` — the new `test (b)` + `test-slow (b)` reds: adjudication

**Verdict: BOTH reds are PRE-EXISTING. Neither is this PR's.** The `test (b)` red is **#4457's exact
signature** — and **#4457 has since been FIXED on `main`** (`80901786b`, #4517, issue CLOSED), which the
PR head does **not** contain because it is based on an obsolete `main` (`cbb1754a2`, this head is **42 commits
behind** `origin/main`). The `test-slow (b)` red is a **flaky race** that reproduces on the PR head, its base,
**and** `origin/main`. No test was skipped, `xfail`ed, env-gated or weakened.

Raw output of every run below is committed in this directory. This dir lives on the evidence branch
`docs/3577-j6-embedded-reds-adjudication` so the PR head's recorded review is **not** invalidated by a head move.

## 1. The failing test ids — from the run's `junit.xml` ARTIFACT (not the log)

Run **`35658732479`** (`pull_request`, head `418cd7804`). The workflow log is not a usable source on this repo
(it echoes its own script and yields only an unattributable `FAILED may` token) — the ids below are the observed
set from the run's uploaded `junit.xml`, extracted verbatim into
[`ci-run-35658732479-junit-failures.txt`](ci-run-35658732479-junit-failures.txt).

| job | job id | junit artifact | totals | failing ids |
|---|---|---|---|---|
| `test (b)` — **EMBEDDED** lane | `106529553904` | `10667423513` | 3 failed / 6473 tests / 101 skipped | `tests/test_rebuild_recreate_content_parity.py::test_bare_reemit_supersedes_revision` · `::test_intermediate_derived_write_wins` · `::test_bare_reemit_still_folds_annotator_dim` |
| `test-slow (b)` — **EMBEDDED** lane | `106529553726` | `10667195687` | 1 failed / 714 tests / 1 skipped | `tests/test_backfill_sources.py::test_e2e8_concurrent_backfill_and_index` |

`python-ci-gate` has no failure of its own — its log shows the aggregate: the matrix results string
`success success failure failure …` → *"matrix leg failed or was cancelled — merge blocked"* (job `106545580842`).

### Lane identification (both jobs are EMBEDDED)

The `changes` job's selection is `{"surfaces": ["api","core","eval","sdk"], "full": false, "carve_out_run": true,
…}`. On the tier-2 shape (`full=false`) the workflow's `Compute docker URI` step leaves `TORTOISE_DB_URI` **empty**
and sets `TORTOISE_TEST_CARVE_OUT=1` (`python-ci.yml`, tier-2 else-branch) — the **embedded** lane. So every
reproduction below runs with `env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1` and the same file set / marker
filter (`-m 'not track_b and not live'`) as the job.

`test (b)` for this run checked out `refs/remotes/pull/3577/merge` = **`f60fd81`** = *"Merge `418cd7804` into
`cbb1754a2`"* — i.e. the head merged into the **old** `main` tip `cbb1754a2` (job log, checkout step). Since the
head already carries `cbb1754a2` as a parent, the merge-ref **tree == the head tree**, so the in-lane head run
below is the exact CI revision's content.

## 2. Does it match #4457's signature? YES — the same three tests, the same assertion

#4457's signature (`docs/evidence/3770-embedded-lane-preexisting-failures/`) is **3 failures in
`tests/test_rebuild_recreate_content_parity.py`** — `test_bare_reemit_supersedes_revision`,
`test_intermediate_derived_write_wins`, `test_bare_reemit_still_folds_annotator_dim` — with
`AssertionError: embedding derives from content and must not drift independently`, failing identically on the PR,
its base and main's own commits in the **embedded** lane while **passing in the docker** lane. The `test (b)`
junit ids and messages are **an exact match** (same 3 nodeids, same assertion).

## 3. In-lane reproduction — main vs branch (raw output committed here)

`.venv/bin/python -m pytest tests/test_rebuild_recreate_content_parity.py -v --timeout=300 -p no:cacheprovider
-m 'not track_b and not live' -r fEs --junitxml=… -o junit_family=xunit1`, embedded lane.

| revision | what it is | embedded-lane result | raw log |
|---|---|---|---|
| `418cd7804` | **PR head** | **3 failed**, 11 passed, 1 xfailed | `test-rebuild-parity-head-418cd7804.embedded.txt` |
| `cbb1754a2` | **CI's merge base** (old `main` tip) | **3 failed**, 11 passed, 1 xfailed | `test-rebuild-parity-base-cbb1754a2.embedded.txt` |
| `b99c7a03b` | parent of the fix commit on `main` | **3 failed**, 11 passed, 1 xfailed | `test-rebuild-parity-b99c7a03b.embedded.txt` |
| `80901786b` | **`main`'s fix: `fix(projection): rebuild_all's derived embedding write must land on the embedded engine (#4457) (#4517)`** | **14 passed**, 1 xfailed | `test-rebuild-parity-80901786b.embedded.txt` |
| `e34626874` | current `origin/main` | **14 passed**, 1 xfailed | `test-rebuild-parity-main-e34626874.embedded.txt` |
| head + current `origin/main` (local merge `adf2c431b`) | the branch **after** re-merging current `main` | **14 passed**, 1 xfailed | `test-rebuild-parity-branchmergedmain-adf2c431b.embedded.txt` |

**Read the table:** the 3 reds are present on the head **and on the CI's own merge base** (so they are not the
PR's), and they are **gone on `main`** — pinned to the exact commit `80901786b` (parent red, fix green).
Merging current `main` into the branch clears them (last row). Issue **#4457 is CLOSED** (updated
`2026-09-21T22:47:22Z`, closed by #4517).

**Classification (each):** `test_bare_reemit_supersedes_revision` = **PRE-EXISTING** (also fixed on main);
`test_intermediate_derived_write_wins` = **PRE-EXISTING**; `test_bare_reemit_still_folds_annotator_dim` =
**PRE-EXISTING**. Mechanism (per #4457): `rebuild_all` writes `content` unconditionally but leaves the
pre-supersession `embedding` on the re-created node; #4517's fix makes the derived embedding write land on the
**embedded** engine.

## 4. The `test-slow (b)` red — `test_e2e8_concurrent_backfill_and_index` is a FLAKE (present on base, head AND main)

Reproduced the single nodeid in the embedded lane, alternating revisions to control for host load. The failure is
`assert bf["created"] + bf["skipped"] == 1` → `assert (0 + 0) == 1` at `tests/test_backfill_sources.py:977` — a gap
in the barrier-released backfill/index race accounting, not a deterministic bug.

| revision | runs | pass | fail |
|---|---|---|---|
| `cbb1754a2` (CI base) | 6 | 3 | **3** (`run5`, `run6`, `run7`) |
| `418cd7804` (PR head) | 7 | 4 | **3** (`run2`, `run3`, `run5`) |
| `e34626874` (`origin/main`) | 10 | 9 | **1** (`run7`) |

The flake is present on **all three** revisions — **not introduced by this PR** (which does not touch
`tortoise/sdk.py::backfill_sources` or the index/backfill path at all). Raw logs:
`test-backfill-concurrent-{base-cbb1754a2,head-418cd7804,main-e34626874}.runN.embedded.txt`.

**Classification:** FLAKY (race), pre-existing on base and main.

## 5. Read-path interaction check — no regression of #4158, #4304/#4395

This PR changes the **read path** (`tortoise/session_reinjection.py` new; `retrieval.py`, `rerank.py`,
`search_engine.py`, `coverage_loop.py`). Explicitly checked:

- **#4158 (reader window / byte ceiling, `fix(ask): resolve the reader-window caps in tandem and make the byte
  ceiling honest (#4105)`)** — the PR **does not touch** any of the files that implement it:
  `git diff --name-only cbb1754a2..HEAD | grep -E 'ask_lane|assembly|monitoring'` is **empty**. Its changes to
  `retrieval.py` are the pool session-key extraction (`session_key_of`) + the shared `guard_and_recap_pool`
  dedup/hold ordering; `dedup_pool`'s default key and `_is_turn_point` are behaviour-preserving refactors.
  In-lane hermetic run of the #4158 tests (`test_ask_retrieval_budget.py`, plus the assembly/d3/rerank suites)
  — **80 passed on the head and 80 passed on the base** (identical):
  `test-readpath-interaction-head-418cd7804.embedded.txt`, `test-readpath-interaction-base-cbb1754a2.embedded.txt`.
- **#4304 / #4395 (episodic turn embeddings on the turn write path)** — the PR does **not touch the turn write
  path**; its read side only *consumes* turn points (`TURN_POINT_KIND = "event"`, `_is_turn_point`, and the
  `session_reinjection` fetch). `tests/test_turn_embedding_write_path_4194.py` is in the 80-passed hermetic set on
  both head and base.
- The PR's own new tests ran in the CI `test (b)` junit and passed (the only 3 failures are the #4457 parity file).

## 6. Is the branch clear for the rail? NO — and here is exactly what blocks it

The merge rail (`scripts/admin-merge.sh` + `scripts/ci_exemption.py decide`) baselines the PR's failing ids against
**main's own runs**. Main's runs are the **push/full-matrix** shape → **docker** `test (b)`, which does not expose
the embedded-only parity bug, so main's failing-set for those 3 ids is **empty by construction** and the rail
would read them as **new failures** and refuse the merge — even though they are `main`'s own bug, already fixed
there. *(This is the brief's point: "main carries no failure in F" is unavailable by construction when F is only
selected because its surface changed.)*

**The unblock is a re-merge of current `origin/main`, not an exemption.** The branch is **42 commits behind**
`origin/main`; `main` carries the fix (`80901786b`). Row 6 of the table proves the parity file goes **3 failed →
14 passed** when current `main` is merged in. The correct sequence:

1. Re-merge (or rebase onto) **current** `origin/main` — the parity reds disappear.
2. **Re-record the review** — the head changes, and `ai-review-gate`'s HMAC marker is head-bound, so it is
   invalidated by design (see `J6-land-or-close-parked-PR.md` Step 3). Do **not** edit the PR body after recording.
3. Push; CI re-runs against the new base. The `test-slow` flake may still flake (it does on `main` too); the
   rail's own re-run classification is the mechanism for that, not a code change.

The PR body's own review attestation is at `418cd7804`; **this evidence branch does not move that head** — nothing
was pushed to `fix/2513-retrieval-evidence`.

## What could NOT be verified

- The CI `test-slow (a)` leg (which contains the other `test_backfill_sources`-adjacent slow files) is not in the
  failing set; only the single nodeid above is red in `test-slow (b)`.
- Docker-lane confirmation of the parity pass on `main` was **not re-run here** — #4457's own artifact
  (`docs/evidence/3770-embedded-lane-preexisting-failures/`) already records head-embedded-red /
  main-docker-green, and `main` no longer exhibits the bug in any lane.
- Host load was ~181 during these runs; local embedded-lane runs can suffer redislite startup faults under load
  (the #4105 finding). The parity runs did not show that fault; the backfill flake is a distinct race
  (`created=0, skipped=0`), not a startup fault. The `test_ask_retrieval_budget` interaction suite is hermetic and
  its 80/80 pass is unaffected.
