<!-- research-path: tortoise#10237 — the issue comment carrying the `research: v1` marker -->
<!-- plan-review: cycles=1, status=clean, version=2.3.0 -->

# #10237 — the D14 leg's pytest log is its OWN, not the host's shared `/tmp`

**Level:** task · **Complexity:** standard (a CI-enforcement change whose failure mode is an
unattributable required check) · **Suite:** `tests/test_pytest_attribution.py`,
`tests/test_ci_guard_invocation.py`, `tests/test_ci_selection.py`, `tests/test_skip_guard.py`,
`tests/test_markers.py`, `tests/test_ci_timing.py`.

## 1. Confirmed problem

`test-d14-hosted-api` writes its pytest log and junit to **host-global** paths
(`/tmp/pytest-d14.log`, `/tmp/junit-d14.xml`, `python-ci.yml:3088`). `runs-on: ubuntu-latest`
resolves to the self-hosted pool (all 9 registered runners carry that label), and the pool runs
several runner *installs* on ONE host sharing ONE `/tmp`. Two concurrent D14 jobs on different
runners therefore write the same file, and whichever `tee` opens last truncates it underneath the
other.

The consequence is not cosmetic. Measured on run `40698118723`, job `122916471295`
(pool runner `pi-runner-vm-5`, 2026-10-28T16:57:53Z→17:08:08Z): the artifact
(`pytest-log-d14-hosted-api`, 153,421 bytes) carries

- the leg's header at bytes 0-641,
- an **18,643-byte NUL hole** at byte 642,
- the leg's own progress from `PASSED [ 76%]` on, ending in
  `= 5 failed, 197 passed, 39 skipped, 40411 deselected, 15 warnings, 3 errors in 550.72s (0:09:10) =`,
- eight attributable nodeids (5 `FAILED` + 3 `ERROR`) in that same tail.

`pytest-attribution.sh` counts the longest NUL run (`TRUNCATED=1`), calls the summary
**STALE**, and emits `::error::test leg INCOMPLETE — no pytest summary; failure set unobservable`.
That claim is **false for this leg** — the failure set was right there. The required
`python-ci-gate` then cannot tell whether those five failures belong to the PR or to `main`,
which is precisely the comparison `ci-introduced-failure` (#10238) exists to make.

Reproduced locally against the downloaded artifact: the STALE block + marker on the artifact, the
owned summary on a per-job copy.

**Falsification check:** if a co-tenant could still truncate this leg's log after the move, the
verdict would still read STALE. The check that would overturn the approach is a `TRUNCATED=1`
verdict on a `${RUNNER_TEMP}`-rooted log. **Confidence: 90.**

## 2. Alternatives considered

| # | Approach | Verdict |
|---|---|---|
| A | **Per-job `${RUNNER_TEMP}` paths for log + junit** (the fix `pytest-attribution.sh:130-136` names as "the durable fix"; the shape #10028 already landed for the `test` job) | **chosen** — removes the cause (no co-tenant can name the file), keeps every basename so downstream readers are untouched, and the mechanism is already proven in this workflow |
| B | Weaken the STALE predicate: trust a summary whose byte offset is *after* the last NUL hole | **rejected** — `TRUNCATED=1` is CORRECT here: the peer's header occupies bytes 0-641, so the leg's own `test session starts` banner is genuinely gone and signal (1) (empty session stack at EOF) is spuriously satisfied. Relaxing a deliberate false-clean guard to buy back a verdict is exactly the trade the file's asymmetry refuses |
| C | Make `pytest-attribution.sh` suppress the marker whenever `OBSERVABLE=1` (FAILED/ERROR lines present) | **rejected as the primary fix** — it would also silence the marker under a real watchdog kill (`rc=137`), where the tests that never ran are missing from the failure set and "unobservable" is TRUE. It treats the symptom (a wrong marker) and leaves the cause (a log two jobs share) |
| D | Pin the D14 leg off the pool (`runs-on: ubuntu-24.04`) | **rejected** — reverses the fallback decision (`python-ci.yml:1708`, `docs/ops/ci-runner-x64.md:26`) and this leg is cheap (221 s norm, #10212), so it is the wrong leg to spend a hosted-only pin on |

## 3. Complexity + domains

`standard` — a CI-enforcement change. Domains: **gate-enforcement** (the leg feeds the required
aggregate and the failure-set comparison), **ops-infra** (workflow + a self-hosted pool property).
No ontology, no UX, no product surface. Migration: none.

## 4. Implementation steps

1. `python-ci.yml`, D14 job: add a **first** step `Resolve per-job pytest artifact paths (#10237)`
   that resolves `base="${RUNNER_TEMP:?…}"` (fail-closed, no `/tmp` fallback) and writes
   `PYTEST_D14_LOG` / `PYTEST_D14_JUNIT` to `$GITHUB_ENV`. Test: `test_a_failing_pytest_still_reds_the_step[d14]`
   (asserts the reporter call names the token) + the step-status execution test.
2. Substitute the two tokens at all five use sites (pytest invocation, reporter, `PASSED` count,
   skip-guard log, skip-guard `--junitxml`) and the two upload paths (`${{ runner.temp }}/…`,
   legal at `steps.with`). Basenames unchanged. Test:
   `test_every_pytest_log_tail_is_followed_by_the_reporter` (floor stays 8).
3. `tests/test_pytest_attribution.py`: match the per-runner token as a SHAPE
   (`"$PYTEST_<NAME>"`) instead of `"$PYTEST_LOG"` exactly, and teach `_TEE_SITE_RE` the quoted
   form. Test: `test_the_per_runner_tee_site_is_counted_and_the_comment_above_it_is_not` (new).
4. `tests/test_ci_guard_invocation.py`: export the step's `$PYTEST_*` tokens into the sandbox
   (`pytest_artifact_env`), the way it already supplies `RUNNER_TEMP`. Test:
   `test_every_frozen_manifest_is_enforced_by_an_executed_step`.
5. `pytest-attribution.sh`: record that D14 followed the `test` job (comment only).

## 5. Wiring

| Touch point | Type | Covered by | Status |
|---|---|---|---|
| `python-ci-gate` → `test-d14-hosted-api` | required aggregate leg | workflow job (unchanged `needs:`) | ✅ |
| `pytest-attribution.sh --log` | reporter consumer | token substitution + `capture_site` shape match | ✅ |
| `tools/skip-guard.py` (`--junitxml`, positional log) | guard consumer | token substitution | ✅ |
| `upload-artifact` `pytest-log-d14-hosted-api` | measurement artifact | `${{ runner.temp }}/…` in `with.path` | ✅ |
| `tools/ci_timing.py` — "`junit*.xml` covers the D14 upload" | artifact reader | basename `junit-d14.xml` unchanged | ✅ |
| `ci-introduced-failure` (#10238) failure-set comparison | downstream verdict | the leg now contributes its 8 real nodeids instead of an unattributable marker | ✅ |
| `$GITHUB_ENV` reach | Actions semantics | resolve step is the job's FIRST step | ✅ |

## 6. Review Scope Contract

**Class:** gate-enforcement

**Risk surface** — what this change can break, each with the check that would catch it:
- **R1** the D14 leg stops reddening the job on a real red pytest (the re-raise is lost in the
  edit) → caught by `test_a_failing_pytest_still_reds_the_step[d14]`, which EXECUTES the step
  body under `bash -e` with a stub exiting 137 and asserts `rc == 137` exactly.
- **R2** the leg silently drops out of the attribution-coverage inventory because its capture is
  no longer a `/tmp` literal → caught by `test_every_pytest_log_tail_is_followed_by_the_reporter`
  (floor `tails >= 8`, zero slack) + `test_the_per_runner_tee_site_is_counted_and_the_comment_above_it_is_not`.
- **R3** the frozen `config/ci-expected-nodeids/embedded-only.txt` set stops being enforced,
  because the skip-guard invocation loses its log/junit path → caught by
  `test_ci_guard_invocation.py::test_every_frozen_manifest_is_enforced_by_an_executed_step`,
  which executes the step and reads the argv the guard ACTUALLY received.
- **R4** the workflow becomes invalid YAML / an illegal Actions expression → caught by
  `workflow-lint` (actionlint 1.7.12), run locally: exit 0.
- **R5** a downstream reader of the artifact basename breaks (`tools/ci_timing.py`) → basenames
  deliberately unchanged; `tests/test_ci_timing.py` green.

**Threat classes:** (not adversarial) — no attacker-reachable surface; a co-tenant CI job is a
*concurrency* hazard, not an adversary, and it is closed by per-job addressing rather than by a
verification gate.

**Out of scope — must NOT be raised** (note, file, do not chase):
- pre-existing failures outside this diff (notably `tests/test_tmpdir_hygiene.py`'s 5
  `TemporaryDirectory`/`shutil.rmtree` cases, which red on this host with zero peer suites and in
  a file this change does not touch);
- the other four pool-eligible legs still on `/tmp` literals — recorded on #7815;
- the routing half of #10237 (refused by `python-ci.yml:1708`; owned by #6897/#10161);
- `tests/test_tmpdir_hygiene.py` semantics, `tools/skip-guard.py`'s `/tmp` default, and #10045/#10212
  (why the D14 leg is slow).

**Acceptance:** every `R<n>` has its named evidence, and no open item remains on the declared surface.
