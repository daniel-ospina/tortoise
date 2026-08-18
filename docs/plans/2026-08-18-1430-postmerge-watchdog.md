---
title: "Post-Merge Validation In-Step Watchdog — Implementation Plan"
type: engineering
domain: capability
doc_status: draft
created: 2026-08-18
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: github-actions-workflow, ci, post-merge-validation
---

<!-- research-path: none — zero new deps (in-repo precedent: python-ci.yml test job watchdog) -->

# Post-Merge Validation In-Step Watchdog — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Give post-merge-validation.yml's `tests` step the same in-step watchdog + guaranteed-summary treatment as python-ci.yml's test job — a runner-level timeout/cancel can never end the job silently (the #798 mode).

**Team:** epistemic-team
**Role:** (none set)

**Architecture:** Verbatim transplant of python-ci.yml's battle-tested watchdog dance (set +e → 5-min heartbeat grepping the log → `timeout -s INT -k 10 50m` on pytest writing to `/tmp/pmv-pytest.log` → capture rc → kill heartbeat → guaranteed tail → WATCHDOG banner on rc 124/137/2 → `pytest exit code: $rc` → `exit $rc`) into the existing `tests` step. One file changed; `id: tests` and all downstream consumers (`steps.tests.outcome` → comment step + Fail-the-check step) preserved; warn-only semantics untouched.

**Key empirical facts (verified 2026-08-18, live run history):** the last 10+ post-merge runs are all cancelled at the 60m job cap; run 32104818556 (the #1371 merge itself) had pytest killed at 58m31s with `TORTOISE_FAST_ATEXIT=1` active — the suite is over budget and the watchdog fires on ~100% of runs **by design** (honest red with evidence replaces silent cancel + false FAILED comment). Making runs pass is budget work (issue #1439), explicitly out of scope here.

### Pattern Research

> **Findings date:** 2026-08-18
> **Gate skipped** — plan touches zero third-party deps (coreutils `timeout`/`stdbuf` are ubuntu-latest-provisioned, already proven in python-ci.yml). In-repo precedent is complete: the python-ci.yml `test` job block survived #798 (silent cancel), #880 (banner rc 124/137/2), #964 (heartbeat), #1371 (FAST_ATEXIT) — its failure modes are known and documented in that file's header.

### Integration Surface Map

Skipped per writing-plans skip rule — the plan has no integration boundaries (pure CI config). The step-wiring surface is covered by the scoping wiring table (`steps.tests.outcome` consumers, job-cap interplay, comment-step behavior).

### Journey Test Map

n/a — no user-facing journeys.

**Tech Stack:** GitHub Actions (YAML + bash), coreutils `timeout`/`stdbuf` on ubuntu-latest.

---

## Task 1: Replace the `tests` step run block with the watchdog dance

**Intent:** The `tests` step gains an in-step watchdog so the runner's 60m job kill can never lose the summary silently (#798 mode).

**Acceptance:** The step's `run:` block is replaced per the exact block below; `name:` ("Run tests (embedded suite, per-test timeout)") and `id: tests` are byte-identical; no other step, job id, trigger, or `timeout-minutes: 60` changes.

**Files:**
- Modify: `.github/workflows/post-merge-validation.yml` (the `tests` step's `run:` block + the header comment note)

**Step 1: Replace the run block**

Replace the current body (which is `set -o pipefail` + `python -m pytest tests/ -q --timeout=90 ... 2>&1 | tail -25`) with:

```yaml
      - name: Run tests (embedded suite, per-test timeout)
        id: tests
        run: |
          set +e
          # #1430: in-step watchdog + guaranteed summary (the #798 mode).
          # Transplants python-ci.yml's proven block: a shell-level `timeout`
          # (SIGINT, then SIGKILL 10s later if pytest ignores INT mid-test)
          # instead of relying on the runner's job-level kill, which cancels
          # the step and loses the summary silently. After the kill the step
          # CONTINUES: re-prints the log tail, prints pass/fail counts and
          # the real exit code. 50m in-step + ~2-6m setup < 60m job cap (a
          # literal 60m in-step would fire AFTER the runner kill). The suite
          # is over budget today (#1439), so the WATCHDOG banner firing is
          # the expected honest-red state — never silent.
          ( while true; do
              sleep 300
              p=$(grep -c ' PASSED' /tmp/pmv-pytest.log 2>/dev/null || true)
              f=$(grep -c ' FAILED' /tmp/pmv-pytest.log 2>/dev/null || true)
              echo "HEARTBEAT: pytest still running — $p passed, $f failed so far"
            done ) &
          HB_PID=$!
          # 90s per-test → 300s: 90 false-killed test_control_plane's ~90s
          # setup in python-ci (#798) — and post-merge runs the FULL suite
          # including test_control_plane. --durations=15 shows the slowest
          # 15 tests in the summary tail.
          timeout -s INT -k 10 50m stdbuf -oL python -m pytest tests/ -v --timeout=300 -p no:cacheprovider -m 'not track_b' --ignore=tests/e2e --maxfail=20 -rfE --durations=15 > /tmp/pmv-pytest.log 2>&1
          rc=$?
          kill $HB_PID 2>/dev/null || true
          wait $HB_PID 2>/dev/null || true
          echo "==================== pytest summary (tail) ===================="
          tail -n 300 /tmp/pmv-pytest.log
          # 124 = timeout SIGINT kill; 137 = -k 10 SIGKILL after pytest
          # ignored INT mid-test; 2 = pytest's own SIGINT summary — the
          # count banner must print for all three (#880).
          if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ] || [ "$rc" -eq 2 ]; then
            passed=$(grep -c ' PASSED' /tmp/pmv-pytest.log)
            failed=$(grep -c ' FAILED' /tmp/pmv-pytest.log)
            errored=$(grep -c ' ERROR' /tmp/pmv-pytest.log)
            echo "==================== WATCHDOG: pytest killed after 50m ($passed passed, $failed failed, $errored errored so far) — last test lines above ===================="
          fi
          echo "==================== pytest exit code: $rc ===================="
          exit $rc
```

**Load-bearing details (do not regress):**
- `set +e` is the FIRST line — GitHub runs steps under `bash -eo pipefail`; without it, `timeout` returning 124 aborts the step BEFORE the tail/banner/exit-code print (silent #798, reintroduced).
- Do NOT copy python-ci's `$FILES` / `needs.changes` / `matrix.half` machinery — nonexistent in this workflow; the target stays `tests/ -m 'not track_b' --ignore=tests/e2e`.
- The step's final `exit $rc` propagates the real code → `steps.tests.outcome` = 'failure' on rc 124/137/2/1 → comment step + fail step behave as today (verified: a step failure keeps `if: always()` successors running; a JOB cancel is what kills them).
- Log path `/tmp/pmv-pytest.log` (distinct from python-ci's `/tmp/pytest.log`).

**Step 2: Update the header comment**

Add a `#1430` line to the workflow header note block (e.g. next to the per-test-timeout note): in-step watchdog #1430, mirrors python-ci's test job. No behavior change.

**Step 3: Diff-review against python-ci.yml**

Confirm the transplant differs from python-ci's `test` job block ONLY in the 4 documented deviations: watchdog 50m (vs 45m — full suite vs half), target `tests/` (vs `$FILES`), `--ignore=tests/e2e` retained, no HF env vars (no pre-cache step here; setting `HF_HUB_OFFLINE=1` would break the embedding download post-merge legitimately needs).

## Task 2: Static + behavioral validation

**Intent:** Prove the YAML parses, the shell block is syntactically valid, and the rc paths behave before committing.

**Acceptance:** All three validations below pass; the rc-path simulation exercises rc 0/1/124.

**Files:**
- Test: (none in repo — validations run from the worktree shell)

**Step 1: YAML parse**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/post-merge-validation.yml'))"`
Expected: no exception. (Note: `yaml.safe_load` parses the top-level `on:` key as `True` — a YAML 1.1 quirk; GitHub's parser handles it fine. This is expected, not an error.)

**Step 2: bash -n on the run block**

Extract the `tests` step's `run:` block (strip 10-space YAML indentation) and run `bash -n` on it. The block contains no `${{ }}` expressions, so no substitution is needed. Expected: exit 0, no output.

**Step 3: rc-path simulation (local, shrunk timeouts)**

Copy the block to a scratch dir; replace `50m` with `3s` and `-k 10` with `-k 2`; point pytest at fixture files. Run three probes:
- hang fixture (a test sleeping 60s) → expect HEARTBEAT lines, tail, `WATCHDOG: pytest killed after 3s (...)`, `pytest exit code: 124`, non-zero rc
- failing fixture → expect tail with failure, NO banner, `pytest exit code: 1`
- passing fixture → expect tail with counts, `pytest exit code: 0`

Expected: all three rc paths behave per the design (banner only on 124/137/2; real exit code always).

## Task 3: Commit via commit-workflow

**Intent:** Ship the change through the mandatory review gate.

**Acceptance:** A DRAFT PR with the change; self-review + verifier review posted; PR marked ready; NOT merged (issue direction: fix/1430-postmerge-watchdog branch, draft PR, do not merge).

**Files:**
- Modify: (nothing new — branch + PR)

**Step 1:** Run the commit-workflow skill (branch `fix/1430-postmerge-watchdog` off origin/main, commit with message file, draft PR with body file, review gate, mark ready, update issue labels).

## Acceptance Criteria (from scoping, micro)

- AC1: Green run → `pytest exit code: 0` + tail; PR gets ✅ PASSED comment (currently unreachable until #1439 — the every-run WATCHDOG banner is the expected honest-red state today).
- AC2: Watchdog kill → HEARTBEAT lines + tail + WATCHDOG banner with counts + `pytest exit code: 124`; step red; job completes <60m; comment posts FAILED (as today) — now with evidence.
- AC3: Real failure (rc 1) / crash (rc 134) → real exit code prints, NO WATCHDOG banner.
- AC4: `id: tests`, comment step, fail step, job cap 60m, triggers byte-identical.
- AC5: `yaml.safe_load` passes; run block passes `bash -n`; rc-path simulation passes.
- AC6: `--timeout=300` in place; no spurious test_control_plane 90s kills.
