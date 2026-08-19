---
title: "Post-Merge Validation Budget Fix — Stop Re-Running python-ci's Slow Files"
type: engineering
domain: platform
doc_status: draft
created: 2026-08-18
subjects.team: epistemic-team
aboutSubjects: tortoise-ci, post-merge-validation
aboutObjects: ci-workflow, post-merge-validation, tiered-selection, slow-files, budget
---

<!-- research-path: issue-scoping comment on #1439 (double-diamond, full-diamond-verify 1 cycle) -->

# Post-Merge Validation Budget Fix — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Bring post-merge-validation.yml's `validate` job under its 60m budget so post-merge runs complete with `pytest exit code: 0` inside the cap (issue #1439 O/I/T indicator a) instead of being cancelled/red-by-design on every merge.

**Team:** epistemic-team
**Role:** (none set)

**Key empirical facts (verified 2026-08-18, live run history in #1439 body):** the last 10+ post-merge runs are all cancelled at the 60m job cap; run 32104818556 (the #1371 merge itself) had pytest killed at 58m31s with `TORTOISE_FAST_ATEXIT=1` active. Root cause: the `validate` job re-runs the FULL suite serially under a 60m cap, duplicating coverage that python-ci.yml already provides on the SAME merge commit (push-to-main trigger) — including the 21 slow files (config/ci-surfaces.yml `slow_files:`) that python-ci runs in parallel in `test-slow` (~36-38m wall). Removing that duplicated runtime leaves post-merge ≈ python-ci's fast gate (measured budget ~28-32m incl. setup vs the 50m in-step watchdog) — under the 60m cap with margin.

**Architecture:** Single workflow file changed; additive steps + one pytest-argument change. No new deps (PyYAML availability on the runner is proven in production — python-ci's `changes` job runs `yaml.safe_load` via tools/ci_selection.py on every event). Every ported block is verbatim in-repo precedent from python-ci.yml's `test` job (survived #798/#880/#964/#1371). Preserved byte-identical: step id `tests`, job id `validate`, the #1430 watchdog block (set +e → heartbeat → `timeout -s INT -k 10 50m` → guaranteed tail → WATCHDOG banner on rc 124/137/2 → `exit $rc`), the #1438 `exit_code` GITHUB_OUTPUT write, the Comment-result step (postmerge-verdict.js tri-state), the Fail-the-check step, triggers, concurrency, permissions, `timeout-minutes: 60`.

### Pattern Research

> **Findings date:** 2026-08-18
> **Gate skipped** — zero third-party deps (PyYAML + coreutils timeout/stdbuf are ubuntu-latest-provisioned and already proven in python-ci.yml). In-repo precedent is complete: the python-ci.yml `test` job's slow-file exclusion (#798/#880/#1371), HF cache/pre-cache (#964/#1211), and orphan-reap (#1005) blocks are all battle-tested with documented failure modes in that file's header.

### Integration Surface Map

Skipped per writing-plans skip rule — no integration boundaries (pure CI config). The step-wiring surface is covered by the scoping wiring table: `steps.tests.outcome` → comment step; `steps.tests.outputs.exit_code` → #1438 verdict; job id `validate`; trigger `pull_request [closed]` + `merged == true`; concurrency `postmerge-validation`; `timeout-minutes: 60`.

### Journey Test Map

n/a — no user-facing journeys. Verification = the exclusion actually removes the 21 slow files (file-list construction run locally) + YAML parse + `bash -n` of the run block + post-merge run green under cap (observed post-merge).

**Tech Stack:** GitHub Actions (YAML + bash), PyYAML (proven on runner), coreutils `timeout`/`stdbuf`.

---

## Task 1: Exclude the slow files from the `tests` step's pytest invocation

**Intent:** post-merge stops re-running the 21 slow files that python-ci's `test-slow` job already covers on the same merge commit; pytest still runs `tests/` wholesale so new files stay covered.

**Acceptance:** The `Run tests` step's run block gains a `SLOW_IGNORES` construction that reads `config/ci-surfaces.yml` (`yaml.safe_load`, `manifest.get("slow_files", [])` — KeyError-safe) and emits `--ignore=tests/<file>` per slow file; the pytest invocation appends the unquoted `$SLOW_IGNORES`. Step `name:`/`id: tests` unchanged; watchdog/exit-code/tail logic untouched.

**Files:** `.github/workflows/post-merge-validation.yml`

## Task 2: Port python-ci's HF embedding-model cache + pre-cache steps

**Intent:** deterministic embeddings and a bounded stalled-download case. The repo-wide cache key (`hf-embedding-cache-v1-ubuntu-latest`) is already populated by python-ci, so the cache restore makes the pre-cache a no-op on most runs; on a cache miss the pre-cache step downloads with `continue-on-error: true` + `timeout-minutes: 6` + 3×5s retries so a stalled TCP download can never stall the run step to the watchdog (#964-class).

**Acceptance:** The `Cache HF embedding model` step (actions/cache, same key) and the `Pre-cache embedding model` step (verbatim port incl. the `HF_HUB_OFFLINE=1` cached-check + retry loop) are inserted between Install and Run tests, mirroring python-ci's order.

## Task 3: Pin HF offline env on the `tests` step

**Acceptance:** The `Run tests` step gains `env: HF_HUB_OFFLINE: "1"` and `TRANSFORMERS_OFFLINE: "1"` (mirrors python-ci's test job — tests must never reach huggingface.co mid-suite).

## Task 4: Port python-ci's stale-redislite-orphan reap step

**Intent:** the fast-file set (now post-merge's main body) includes the reaper tests (`test_reaper`, `test_reaper_orphan`) which fail on shared runners polluted with stale redislite tempdirs (#1005). Cheap parity port keeps runs green.

**Acceptance:** The `Clean stale redislite orphans` step (verbatim port: `pkill -f "redis-server unixsocket"` + `find "$TMPD" ... -mmin +30 -exec rm -rf` + best-effort `|| true`) is inserted after Install, before the HF cache steps, matching python-ci's order.

## Task 5: Update comments to reflect the exclusion

**Acceptance:** The header comment and the watchdog block comment (currently: "post-merge runs the FULL suite including the slow files") state that the slow files are excluded and covered by python-ci's test-slow; the budget claim is updated.

---

## Rejected Alternatives (documented in the scoping comment)

1. **Two-job split mirroring python-ci (test + test-slow):** heavier infra, still re-runs test-slow serially (pure duplication of python-ci's test-slow), zero coverage gain. Would be better only if python-ci did NOT cover the same commit — it does.
2. **Raise job cap to 90m:** does not fix the root duplication (still re-runs ~36-38m of slow files serially), burns runner time, and per #1430 any cap change must keep the in-step watchdog (90m would, but runs would stay red-by-design on slow runners, just later).
3. **Also exclude env-broken `tests/test_agent_signup.py`:** not a budget driver (58.5m run never hit `--maxfail=20`; 17 tests, no redis-dependency markers in this job) and post-merge is its only always-on surface (python-ci's push-matrix halves exclude it; only tier-2 api-PR selections run it). Excluding would narrow post-merge's residual coverage.

**Follow-ups (not absorbed):** reaper-flake class is mitigated here by the orphan-reap port; if it recurs, file separately. `test_agent_signup` live-redis coverage is a pre-existing gap (tracked via #647 class issues).
