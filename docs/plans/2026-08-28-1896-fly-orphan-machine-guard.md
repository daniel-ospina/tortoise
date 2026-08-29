<!-- research-path: scoping artifact on issue #1896 (2026-08-28, prior-session design research verified against live Fly Machines API + flyctl source; scope/plan folded into this doc; plan-review cycle 1 folded 2026-08-28) -->

# Fly Machine Fleet Guard Implementation Plan (#1896)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Production traffic must never again be served by an orphaned (non-Fly-Launch) or crash-looping Fly machine — the 2026-08-28 incident (orphan `080d6e1a0d2928` crash-looping `gunicorn: command not found` → exit 127 → max restart count of 3 → stopped, causing 10–15s dashboard hangs) is eliminated and guarded so it cannot recur.

**Team:** epistemic-team

**Architecture:** Two-layer prevention. (1) Ops fix (already shipped this session): the orphan machine `080d6e1a0d2928` is **destroyed** — `fly machines list -a tortoise-y4mjjq` shows only the healthy Launch-managed machine `8654509b634758` (process group `app`, started). (2) Fail-closed pre-deploy gate: a repo-local check script (`.github/scripts/check-fly-machines-guard.py`, Python stdlib only — urllib + json + tomllib, no new dependency) runs in `deploy-hosted.yml` before `flyctl deploy` and exits non-zero if any machine is an orphan (empty/unknown process group — flyctl's canonical `ProcessGroup()` resolution) or, for traffic-serving groups, crash-looping (flyctl's exact `isConstantlyRestarting()` heuristic, with one deliberate deviation documented below). Data source: **Fly Machines API** `GET /apps/{app}/machines` with the existing `FLY_API_TOKEN` repo secret (verified live this session — the same token the secrets-set step already uses; no new secret needed). Exit contract mirrors the migration-drift gate precedent (#1095): 0 clean / 1 guard failed (remediation messages) / 2 could-not-determine (fail-closed — a missing token or API error fails the deploy, never silently skips).

### Pattern Research

> **Findings date:** 2026-08-28

**Library docs (preflight)** — no third-party deps in plan (Python stdlib urllib/json/tomllib only; Python 3.11+ ships `tomllib`; ubuntu-latest runners ship Python 3.12). Skipped.

**Fly Machines API surface** — verified live this session against the running fleet (`flyctl machines list -a tortoise-y4mjjq --json`) + flyctl source (master):
- Canonical: `GET https://api.machines.dev/v1/apps/{app}/machines` with `Authorization: Bearer <token>` returns the machines array (no pagination on the list endpoint). Each machine carries `config.metadata.fly_process_group` (the Launch process group), `state` (started/stopped/destroyed/destroying), `image_ref`, and an `events` array (newest-first).
- flyctl's `ProcessGroup()` (fly-go `machine_types.go`): `config.metadata["fly_process_group"]` → `config.metadata["process_group"]` → `""` (empty = "Found machines that aren't part of Fly Launch"). Group `app` is the default when `fly.toml` has no `[processes]` section (verified: our `fly.toml` has no `[processes]`).
- flyctl's `isConstantlyRestarting()` (internal/machine/leasable_machine.go:293-311, source-verified this session): take the FIRST `type == "exit"` event in `machine.Events` (newest-first → most recent exit); flag iff `request.restart_count > 1 && request.exit_event.exit_code != 0 && !request.exit_event.requested_stop && request.exit_event.restarting`. JSON field names (fly-go struct tags): `exit_event`, `exit_code`, `requested_stop`, `restarting`, `restart_count`.
- **Fleet scoping** (fly-go `IsActive()`): flyctl excludes machines with `state ∈ {destroyed, destroying}` from the active fleet — the list endpoint DOES return them, so the guard must skip them too (a retained destroyed machine with crash history must not flag every deploy — it's already gone; `fly machines destroy` cannot clear it).
- **Host-unreachable shape** (fly-go `Machine.GetConfig()`, source-verified): when `host_status != "ok"`, the API OMITS `config` and returns `incomplete_config` ("config can't be fully retrieved"); flyctl's `GetConfig()` falls back to `IncompleteConfig`. The guard must do the same — best-effort orphan-check via `incomplete_config.metadata` + skip the crash-check for that machine (warn) — and exit 2 ONLY when both `config` and `incomplete_config` are absent. This is a documented legitimate response state, NOT shape drift.
- Known pitfall: flyctl's `restarting` condition means a machine that crash-looped and then TERMINALLY STOPPED (the incident's "machine has reached its max restart count of 3" → stopped) is NOT flagged by flyctl — flyctl only waits on live machines. **Deliberate deviation (documented in the script docstring + this plan): DROP the `restarting` condition** so the stopped-after-crash-loop terminal state is also flagged. Consequences documented: (a) `restart_count` is per-restart-episode, not lifetime-cumulative (flyctl's own smoke-wait would permanently fail any machine restarted ≥2× ever if it were cumulative), so a machine that recovered after a crash-loop is flagged only when its most recent exit still shows the crash signature — the conservative direction for a pre-deploy gate; (b) platform/operator-initiated stops record `requested_stop=true` AND `exit_code=0` (community evidence: fly.io auto-stop/deploy/`fly machine stop` events), so keeping `!requested_stop` does not false-flag operator stops — the terminal crash-give-up stop retains the crash's `requested_stop=false`.
- The healthy machine's `exit`-less event list, and the transient deploy-time `fly_app_release_command` machine (fly.toml `[deploy] release_command`), must NOT be flagged: internal groups are in the allowed set AND excluded from the crash-loop leg (they are transient/non-traffic — a crashed release-command machine is a deploy-surface problem the deploy itself reports, and crash-flagging it would deadlock the fix deploy).
- **Residual gaps (documented, not blocked):** (a) label-spoof corner — a machine carrying a Launch-looking group (`app`/internal) created outside Launch passes both legs if not crash-looping; a `fly_platform_version == "v2"` precondition could close it, but `flyctl run.go` stamps neither group nor platform version so the common manual path is caught by the empty-group leg, and the corner is not a security boundary; (b) a machine stuck in `destroying` is skipped forever (safe — not serving traffic; the deploy surfaces a hung destroy as an error); (c) the deviation's terminal-stop envelope (`requested_stop=false` on crash-give-up) is incident-evidence-backed (exit 127 crash signature at terminal stop, issue #1896) but not scratch-app-empirically re-verified — an open verification item: if a future observation shows give-up stops carry `requested_stop=true`, add a `state == 'stopped'` + crash-signature rule.

### Integration Surface Map

| Surface | Test Layer | Expected Verification |
|---------|-----------|----------------------|
| `.github/scripts/check-fly-machines-guard.py` (new) | unit (pytest) | exit 0 clean / 1 orphan-or-crash-loop / 2 could-not-determine; hermetic fixtures via `FLY_MACHINES_FILE`/`FLY_TOML` env seams + a local stub HTTP server via `FLY_API_URL` |
| `deploy-hosted.yml` gate step | CI (push to main / workflow_dispatch) | fail-closed before `flyctl deploy` on orphan or crash-loop; missing `FLY_API_TOKEN` → exit 2 → deploy fails; incident-fix bypass via `skip-fly-machines-guard` input/var emits `::warning::` and never bypasses exit-2 |
| production fleet | ops | `flyctl machines list -a tortoise-y4mjjq` shows only Launch-managed machine(s); live dry-run of the script exits 0 |

### Tech Stack

Python 3.11+ stdlib (`urllib.request`, `json`, `tomllib`), GitHub Actions, pytest (script tests). No new third-party dependency, no new repo secret.

---

### Task 0: Preflight — orphan machine already destroyed (tracked, no action)

**Intent:** The incident root cause is eliminated BEFORE the guard ships; the guard's job is preventing recurrence, not fixing the fleet (which is already clean).
**Acceptance:** `flyctl machines list -a tortoise-y4mjjq` shows zero machines with empty process group.
**Files:**
- None (verification only)

**Step 1:** Verify the fleet: `flyctl machines list -a tortoise-y4mjjq` → **confirmed this session**: only `8654509b634758` (process group `app`, state `started`). The orphan `080d6e1a0d2928` is gone (`fly machines destroy 080d6e1a0d2928 -a tortoise-y4mjjq` was executed in the prior session).

**Step 2:** Confirm the Machines API + token work (the guard's live data path): the same JSON the script will parse is visible in `flyctl machines list --json` output (metadata `fly_process_group: "app"`, `events` array present). No code change.

---

### Task 1: `check-fly-machines-guard.py` script + hermetic tests

**Intent:** The load-bearing fail-closed fleet detector — one script both the CI gate and the operator's live dry-run call.
**Acceptance:** Script exits 0 (clean), 1 (orphan or traffic-group crash-loop, naming each violating machine + remediation), 2 (could-not-determine: missing token / API error / unparseable response / malformed machine / missing fly.toml); tested hermetically via `FLY_MACHINES_FILE`/`FLY_TOML`/`FLY_API_URL`/`FLY_API_TOKEN` env seams.
**Files:**
- Create: `.github/scripts/check-fly-machines-guard.py`
- Create: `tests/test_fly_machines_guard.py`

**Step 1: Write the failing tests** (following `tests/test_migration_drift_gate.py`'s subprocess + env-seam pattern; fixtures built from the verbatim real machine JSON shape captured in Task 0 — `config.metadata.fly_process_group`, `events[].request.exit_event.{exit_code,requested_stop,restarting}`, `events[].request.restart_count`, `state`):

```python
# tests/test_fly_machines_guard.py — full source in the implementation task
def test_clean_single_machine_exit_zero()          # group 'app', no exit event → 0
def test_orphan_empty_process_group_blocks()       # group absent/empty → 1 + destroy hint
def test_orphan_unknown_process_group_blocks()     # group 'worker' not in allowed set → 1
def test_processes_section_expands_allowed()       # fly.toml [processes] adds groups → 0
def test_release_command_machine_allowed()         # fly_app_release_command transient → 0
def test_crash_loop_blocks()                       # restart_count=3, exit 127, not stopped → 1
def test_requested_stop_not_crash_loop()           # operator-stopped (requested_stop=true) → 0
def test_restart_count_one_not_crash_loop()        # restart_count=1 → 0
def test_exit_code_zero_not_crash_loop()           # exit_code=0 → 0
def test_stopped_after_crash_loop_blocks()         # DEVIATION: restarting=false + crash signature → 1
def test_stopped_crash_loop_still_flagged()        # same signature, state 'stopped' → 1 (boundary)
def test_destroyed_machine_ignored()               # crash-signature exit on state 'destroyed' → 0 (flyctl IsActive)
def test_release_command_crash_not_crash_flagged() # fly_app_release_command crash exit → NOT crash-flagged (transient) → 0
def test_first_crash_not_flagged()                 # restart_count=1 boundary (mid-loop, deploy-replaced) → 0
 def test_most_recent_exit_wins()                   # events newest-first; newest exit clean, older crash → 0
def test_machine_missing_config_exit_2()           # no config AND no incomplete_config → 2 (fail-closed)
def test_incomplete_config_fallback_ok()           # host_status 'unreachable' + incomplete_config group 'app' → 0
def test_incomplete_config_empty_group_blocks()    # incomplete_config empty metadata → 1 (orphan)
def test_machine_bad_exit_event_exit_2()           # first exit event lacks request.exit_event → 2 (fail-closed)
def test_events_not_list_exit_2()                  # events present but not a list → 2 (fail-closed)
def test_metadata_not_dict_exit_2()                # config.metadata present but not a dict → 2 (fail-closed)
def test_incident_replay()                         # orphan 080d6e1a0d2928 + healthy → 1, names orphan
def test_empty_fleet_clean()                       # empty machines list → 0 + "nothing to guard" notice
def test_missing_token_exit_2()                    # no FLY_API_TOKEN, no FLY_MACHINES_FILE → 2
def test_missing_fly_toml_exit_2()                 # FLY_TOML → nonexistent → 2
def test_api_error_exit_2()                        # stub HTTP 500 → 2, stderr names the status
def test_query_error_exit_2()                      # stub returns JSON object (not list) → 2
def test_live_api_clean()                          # stub returns clean fixture → 0
```

**Step 2: Run to verify they fail** — `uv run pytest tests/test_fly_machines_guard.py -v` → FAIL (script absent).

**Step 3: Implement the script** (key logic):
- Config: `tomllib.load(fly.toml)` → app name (env override `FLY_APP`); allowed process groups = `[processes]` keys, or default `{'app'}` when no `[processes]` section, **always unioned** with Fly-internal groups `{'fly_app_release_command', 'fly_app_console', 'fly_app_test_machine_command'}`. **Traffic groups** (crash-loop check scope) = the `[processes]` keys or `{'app'}` default — internal groups are orphan-checked only.
- `FLY_TOML` default resolved from the script location (`REPO_ROOT = Path(__file__).resolve().parent.parent.parent` — the check-migration-drift precedent), so the operator's live dry-run works from any CWD.
- Machines source: `FLY_MACHINES_FILE` env seam (test) → else `GET {FLY_API_URL}/apps/{app}/machines` with `Authorization: Bearer {FLY_API_TOKEN}` (default `FLY_API_URL=https://api.machines.dev/v1`; `timeout=30` per attempt, **3 attempts with 2s/4s exponential backoff** (`2^(attempt+1)`, overridable via `FLY_GUARD_MAX_ATTEMPTS`) — matching the deploy step's demonstrated tolerance for transient Fly API races; fail-closed exit 2 after exhaustion). Type-assert the response is a JSON array.
- **Skip destroyed machines**: `state ∈ {destroyed, destroying}` → skip before both checks (flyctl `IsActive()`; the list endpoint returns them).
- **Fail-closed shape validation** (per machine): machine not a dict, `config` present-but-not-a-dict, `config.metadata` present-but-not-a-dict, or `events` present-but-not-a-list → exit 2 "cannot determine machines state". **`config` absent → fall back to `incomplete_config`** (fly-go `GetConfig()` semantics — the documented `host_status != "ok"` response shape): orphan-check from `incomplete_config.metadata`, SKIP the crash-check for that machine (warn "crash-loop detection not evaluated: host unreachable"). Exit 2 only when BOTH `config` and `incomplete_config` are absent. Wrap per-machine evaluation in a catch-all: any unexpected exception → exit 2 with `::error::` (a rename/drift of the API shape must fail the deploy, never silently pass — the orphan leg is fail-closed by construction, the crash-loop leg must be too).
- **Orphan detection** (flyctl `ProcessGroup()`): `config.metadata.fly_process_group` → `config.metadata.process_group` → `''`. Empty group → ORPHAN (not part of Fly Launch). Non-empty group NOT in allowed set → ORPHAN (fail-closed on unexpected groups).
- **Crash-loop detection** (flyctl `isConstantlyRestarting()`, source-verified; traffic groups only): iterate `events`; take the FIRST `type == 'exit'`; flag iff `request.restart_count > 1 AND request.exit_event.exit_code != 0 AND NOT request.exit_event.requested_stop`. If the first exit event exists but `request`/`request.exit_event` is missing or not a dict → **exit 2** (could-not-determine — never silently pass). **Deliberate deviation:** do NOT require `exit_event.restarting` — the stopped-after-crash-loop terminal state must flag. Missing `exit_code` → Go zero-value semantics (0 → no flag).
- **Fail-closed error handling**: missing `FLY_API_TOKEN` (when no `FLY_MACHINES_FILE`) → exit 2; HTTP error (print status code + body excerpt), URL error/timeout, JSON decode error, non-list response, missing `FLY_TOML` → exit 2 with "cannot determine machines state". Emit GitHub `::error::` annotations from the CI-consumed paths.
- Exit contract: 0 clean — print "OK: N machine(s)" (empty list → "0 machines — nothing to guard" notice, exit 0: the deploy creates machines); 1 violations — print each with `fly machines destroy <id> -a <app>` remediation + `fly logs` hint for crash-loops; 2 could-not-determine (missing token / API error / malformed machine / unparseable response).

**Step 4: Run to verify pass** — `uv run pytest tests/test_fly_machines_guard.py -v` → PASS.

**Step 5: Commit.**

---

### Task 2: Wire the fail-closed gate into `deploy-hosted.yml`

**Intent:** Every app deploy is gated on fleet health before code ships — the guard is the recurrence-prevention half of #1896.
**Acceptance:** Gate step runs after the migration-drift gate (#1095 precedent), strictly before `flyctl deploy`; FAIL-CLOSED (missing token / API error → exit 2 → deploy fails); incident-fix bypass mirrors the `skip-db-health-gate` (#1719) / `skip-pack-smoke` (#1929) convention — `skip-fly-machines-guard` workflow_dispatch input + `vars.SKIP_FLY_MACHINES_GUARD` lane, `::warning::` emitted, bypasses ONLY the exit-1 violation check, never exit-2.
**Files:**
- Modify: `.github/workflows/deploy-hosted.yml`

**Step 1:** Add `FLY_API_TOKEN` to the "Verify secrets exist" step's hard-required checks (mirroring the `SUPABASE_ACCESS_TOKEN` gate precedent — a deploy must never ship with the guard silently unarmed because the token is missing).

**Step 2:** Add the `skip-fly-machines-guard` input (mirror `skip-pack-smoke`: `required: false`, `default: 'false'`, `type: boolean`; `vars.SKIP_FLY_MACHINES_GUARD` lane) with the bypass-warning step emitting `::warning::`.

**Step 3:** Insert the gate step after the migration-drift gate step, before "Set all app secrets" (on push-triggered runs `inputs` is an empty object → `inputs.skip-fly-machines-guard` is falsy → the gate runs; `vars.SKIP_FLY_MACHINES_GUARD` is the documented push bypass — byte-identical to the live `skip-db-health-gate` pattern):

```yaml
      # Fly machine guard (#1896): fail-closed check that no orphan or
      # crash-looping machine serves production traffic. The 2026-08-28
      # incident: orphan 080d6e1a0d2928 (empty process group) crash-looped
      # (exit 127, max restart count of 3) → 10-15s dashboard hangs. The
      # flag's remediation (fly machines destroy) is safe: a crash-looping
      # machine is not serving traffic, and the deploy creates replacements.
      # Incident-fix bypass: skip-fly-machines-guard input / SKIP_FLY_MACHINES_GUARD
      # var (mirrors skip-db-health-gate #1719) — exit-2 (could-not-determine)
      # is NEVER bypassable.
      - name: Check Fly machines (orphan + crash-loop guard, fail-closed)
        if: ${{ ! (inputs.skip-fly-machines-guard || vars.SKIP_FLY_MACHINES_GUARD == 'true') }}
        run: python3 .github/scripts/check-fly-machines-guard.py
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
          FLY_APP: tortoise-y4mjjq
      - name: Warn when Fly machine guard bypassed (#1896)
        if: ${{ inputs.skip-fly-machines-guard || vars.SKIP_FLY_MACHINES_GUARD == 'true' }}
        run: |
          echo "::warning::Fly machine guard SKIPPED (skip-fly-machines-guard) — orphan/crash-loop machines will NOT block this deploy. Exit-2 via missing FLY_API_TOKEN can never be bypassed (token hard-required in Verify-secrets); an API-error exit-2 IS bypassed by this skip, same accepted risk as skip-db-health-gate (#1719)."
```

`FLY_APP` is set explicitly so the checked app always equals the deployed app, independent of fly.toml drift.

**Step 4:** Verify the workflow has no early-exit path before deploy (single `deploy-api` job after `packaging-smoke`, no matrix — confirmed in review of the current file). Update the workflow header comment: deploys are now also gated on fleet health.

**Step 5:** Commit.

---

### Task 3: Verify + live dry-run + reconcile

**Intent:** Prove the guard passes on today's (clean) fleet and catches the incident replay; confirm the hermetic suite is green.
**Acceptance:** All hermetic tests pass (62 at final review — the plan's 28-test list is the initial sketch; test-review cycles extended it with type-drift, shape-validation, and retry-seam coverage); live dry-run of the script against the real fleet exits 0; `tests/` suite green (docker lane or carve-out lane per AGENTS.md).
**Files:**
- Test: `tests/test_fly_machines_guard.py` (incident fixture)

**Step 1:** Live dry-run: `FLY_API_TOKEN=... python3 .github/scripts/check-fly-machines-guard.py` → **expect exit 0** (only Launch-managed `app` machine, no crash-loop).

**Step 2:** Run the hermetic suite: `uv run pytest tests/test_fly_machines_guard.py -v` → PASS; then the full suite per the repo's testing lane (`TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' uv run pytest tests/ -q` — the new tests are subprocess+fixture hermetic and pass in both lanes).

**Step 3:** Commit.

---

### Task 4: Scope/plan review + merge (commit-workflow)

**Intent:** The standard-tier review gates (2 parallel plan verifiers; commit-workflow code-review gate) validate the design and the diff before merge.
**Acceptance:** Both plan verifiers return NO ISSUES FOUND; PR reviewed and merged via commit-workflow.
**Files:**
- None

**Step 1:** Dispatch 2 parallel plan verifiers (task sub-agents) against this plan doc + the issue body; fix-and-reverify loop until both return NO ISSUES FOUND.

**Step 2:** Run commit-workflow end-to-end (preflight → parallel-check implement → commit → draft PR → code-review gate → merge → cleanup).

**Step 3:** Post-merge verification: the gate is a deploy-time check, so the fleet dry-run (Task 3) IS the verification; no runtime smoke required (the PR touches no runtime code).

---

## Rejected Alternatives & Out-of-Scope (documented decisions)

- **flyctl+jq one-liner** (run `fly machines list --json | jq` in the gate): rejected — the Python script mirrors flyctl's exact source-level heuristics with hermetic subprocess tests per the repo's drift-gate pattern, gives the operator a reusable dry-run tool, and needs no runner-side `jq`/`flyctl` binary in the gate step.
- **Stale-image detection** (allowed-group machine running a legacy image, no crash tell): out-of-scope for #1896 — the incident's orphan had an EMPTY group (caught by the orphan leg); a "group app but wrong image" machine is a distinct failure class (potential follow-up issue).
- **Scheduled cron leg** (daily guard run for non-workflow deploys): out-of-scope — deploys are workflow-mediated by convention (the orphan predates #660 token scoping); the operator's live dry-run (Task 3) is the manual-path check. The script is deliberately standalone so a cron job can be added later with zero new code.
- **Terminal-stop `requested_stop` semantics**: kept `!requested_stop` per the verified flyctl design — platform/operator stops record `exit_code=0` + `requested_stop=true` (community evidence), so the signature `exit_code != 0 && restart_count > 1 && !requested_stop` does not false-flag them; the crash-give-up stop retains the crash's `requested_stop=false`. Documented in the script docstring for future maintainers.
