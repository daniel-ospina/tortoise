<!-- research-path: docs/plans/2026-09-26-5049-verdicts-from-conditions.md -->

# #5049 — Verdicts from conditions, not from ambient machine state — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make a test verdict a function of the code under test and nothing else. Establish one
reusable contract (`tests/_verdict.py`) that expresses four of #5049's five refactor rules — rule
1 (no wall-clock expiry produces FAIL), rule 2 (deadline sufficiency is measured), rule 4
(process globals reset per test), rule 5 (host capability is a SKIP) — and apply it to a coherent
first set of the 31 members folded into #5049. Rule 3 (exact equality on a derived quantity) is a
numerical-tolerance surface named separately in "Remaining members"; it is not expressible as a
wait/skip contract and is deliberately not claimed here.

**Issue:** #5049 (`complexity:complex`, `tech-debt`) · **Branch:** `fix/5049-conditions-not-ambient`
· **Base:** `origin/main @ 99a98ddc5` · **Team:** organisation-design-team (repo default)
**Scoping artifact:** comment posted 2026-09-26 on #5049 (`<!-- issue-scoping:` + Wiring table).

**Architecture:** Add one pure helper module under `tests/` (`tests/_verdict.py`) that is
stdlib-only + `pytest`, imported at `tests/conftest.py` module level and registered in
`SHARED_MODULES` so a change to it runs the full matrix. Wire a suite-wide autouse per-test reset
of the declared process globals into `tests/conftest.py`, then convert the coherent first set of
named sites to use the contract instead of their local fixed budgets / ambient reads. No
production-code change.

### Pattern Research

> **Findings date:** 2026-09-26

**Library docs (preflight)** — no third-party deps in this plan. The helper is stdlib-only
(`time`, `re`, `shutil`, `subprocess`, `contextlib`, `dataclasses`) plus `pytest`, already the
repo's runner. `pytest-timeout>=2.4.0` is a declared test dependency but is deliberately **not**
used: a global per-test timeout *fails* a test, which is the shape #5049 rule 1 rejects.

**Library version & API surface** — skipped (zero third-party deps).
**Idiomatic usage patterns** — skipped (zero third-party deps).
**Library/framework pitfalls** — skipped (zero third-party deps).

> Gate skipped: zero third-party dependencies (in-repo test helpers + stdlib only).
> Prior research consumed: the 31 member bodies consolidated on #5049 (acceptance evidence), the
> repo's existing isolation fixtures (`_packs_env_isolation`, `_codex_home_isolation`,
> `_analytics_alert_isolation`, `_isolate_atexit_budget`) that this contract generalizes, and the
> existing wait/probe helpers it must unify rather than duplicate
> (`tests/test_capture_spool.py::_node_that_can_strip_ts` [22.7 flag floor],
> `tests/test_pi_capture_hooks.py::_node_supports_ts` [22.18 default-on floor]).

### Integration Surface Map

| # | Surface | Kind | Test layer | Failure modes (≥2) |
|---|---|---|---|---|
| S1 | `tests/_verdict.py` helper API | pure logic | unit (`tests/test_verdict_contract.py`) | condition never true; timeout exception escapes; insufficient recorded margin; Node absent / below floor / garbage `--version` |
| S2 | `tests/conftest.py` autouse per-test reset | pytest fixture lifecycle | unit + order test | global not cleared before a test; ambient env read; fixture runs before `monkeypatch` undo |
| S3 | Converted real-process sites (`tests/test_fork_safety_3845.py`, `tests/test_embedded_lifecycle.py`) | real subprocess + unix socket | integration (embedded carve-out; fork-safety is darwin-gated → unit-covered on Linux) | client socket timeout under load; parent-exit exceeds deadline; `ps` sampling unavailable |
| S4 | Node driver sites (`tests/test_admin_origin_redirect.py`, `tests/test_provisioning_edge_function.py`, `tests/test_blog_agent_delete_guard.py`) | subprocess Node driver | integration (host-capability gated; wiring pin in the unit file) | Node absent; Node < 22.7; probe returns garbage/nonzero |
| S5 | CI selection manifests (`config/ci-surfaces.yml`, `tools/ci_selection.py::SHARED_MODULES`, durations map) | static config gates | `tests/test_ci_selection.py` | new test file unregistered → integrity RED; conftest module-level helper not in SHARED_MODULES → under-selection RED |

**Bug-pattern flags:** S3 is a real-process surface — the verdict must rest on structural
observables (parked child, fork counter, parent returncode), and a deadline expiry is
INCONCLUSIVE. S4 must not turn a *deliberate* absence-as-failure policy into a silent skip
(blog-guard keeps `absent="fail"`). S5 is mandatory, not advisory: an unregistered test file makes
`tests/test_ci_selection.py` red on the PR.

### Verification Plan

- **Unit** (fast lane): `tests/test_verdict_contract.py` — the helper's own behaviour, including
  the sabotage legs (below).
- **Integration** (embedded carve-out): the two converted sites. `test_fork_safety_3845.py` is
  darwin-gated, so its conversion is **unit-covered** and its integration lane is advisory; the
  Linux-runnable leg is the contract primitive.
- **Regression**: the order test proves the autouse reset removed the #4913/#4017 ordering
  dependence; with the reset removed it goes RED.
- **Static**: `uv run pytest tests/test_ci_selection.py -q` proves the manifest edits.
- No UX / config / content / research domains apply.

**Tech Stack:** Python 3.12, pytest 9.1.1, stdlib only.

---

## Scope of THIS increment (bounded)

Establishes the contract and converts a **coherent first set**: the process-global sites
(#4913, the #4017 ambient-env hazard) and the load-sensitive timeout sites (#4742, #4739),
plus the Node host-capability gate (#4916). Everything else is enumerated under **Remaining
members** with its home and is deliberately NOT touched (owner frame: fix the root; no per-flake
guards; keep CI lean).

### Non-goals

- No production-code change. No retry-on-flake. No new per-test skip/allow-list **as a flake
  workaround** — the contract is one shared mechanism, and the fork-safety `# allow-skipped:`
  outcome budget in `config/ci-expected-nodeids/*.txt` is a pre-existing CI gate, not a new one.
- No conversion of the `#4873/#4883/#4816` family (mechanism unconfirmed — the issue says so).
- No touch of `#4746`, `#4966`, `#4958` (explicitly left separate).
- No conversion of `tests/test_fork_slot_wedge_3845.py` (#4742's second file) — listed as
  remaining; converting it needs the same `_CHILD_WAIT_S`/`recover_fork_slot` treatment and would
  double this increment's real-process surface.

### Design decisions

- **D1 — Contract, not per-site patch.** One helper; a per-flaky-test guard or a blanket retry is
  rejected by the owner frame. No recorded decision is contradicted: `pytest-timeout` is a
  FAIL-producing global timeout, which rule 1 rejects.
- **D2 — Deadline expiry is INCONCLUSIVE; sufficiency is measured against a RECORDED value.**
  `Deadline(budget_s, recorded_s)` compares against a **static, checked-in** endpoint time — never
  a live measurement, because a live sample on a loaded host would itself produce the FAIL rule 1
  forbids. `HarnessDefect` (a FAIL) fires only on a *static* insufficiency; an ambient/live
  insufficiency is INCONCLUSIVE with the measured value named.
- **D3 — Process globals reset suite-wide.** The reset runs **before** every test (so an inherited
  armed global cannot reach a test body) and after. The conftest fixture is authoritative; the
  pre-existing per-module `_isolate_atexit_budget` remains (documented as belt-and-braces) and is
  NOT removed here to avoid perturbing a module mid-flight.
- **D4 — Host capability is a SKIP; the floor is per-invocation.** `NODE_FLOOR_STRIP_TYPES = (22,7)`
  for `--experimental-strip-types` drivers, `NODE_FLOOR_DEFAULT_ON = (22,18)` for the bare
  `node --test` invocation. The blog-delete guard's deliberate `absent="fail"` policy is preserved;
  only a *present-but-too-old* Node SKIPs.
- **D5 — #4017 is re-scoped to what is true on this base.** Verified: `_post_ask` and
  `tests/test_ask_api.py` were removed (#3929); `ask_lane.py:438,848` now *fail loud* when
  `TORTOISE_API_URL` is set; the live ambient-production vector is
  `tortoise/sdk.py:21919` (`_post_commit` base URL). The reset is kept because it makes local == CI
  and closes a real production endpoint, but the justification is corrected.
- **D6 — Non-goals held.** No conversion of the three unconfirmed-mechanism members, no
  `#4746/#4966/#4958`.

---

### Task 1: The verdict contract module + its tests (TDD)

**Intent:** Give every test one vocabulary for "the property was observed", "the property could
not be observed in the deadline", and "the harness itself is defective", so a load-dependent
outcome cannot borrow the FAIL verdict.

**Acceptance:** `tests/_verdict.py` exists, is stdlib-only + `pytest`, and exposes `wait_for`,
`inconclusive`, `inconclusive_on_timeout`, `Deadline`, `HarnessDefect`, `host_capability`,
`node_meets`, `require_node_floor`, `NODE_FLOOR_STRIP_TYPES`, `NODE_FLOOR_DEFAULT_ON`,
`PROCESS_GLOBALS`, `reset_process_globals`. `tests/test_verdict_contract.py` covers every API and
the sabotage legs.

**Files:**
- Create: `tests/_verdict.py`
- Create: `tests/test_verdict_contract.py`
- Modify: `tools/ci_selection.py` (add `tests/_verdict.py` to `SHARED_MODULES`)
- Modify: `config/ci-surfaces.yml` (register `test_verdict_contract.py` under `core` + durations)

**Step 1: Write the failing tests** — `test_verdict_contract.py`: `wait_for` False on a
never-true predicate / True on a late-true one; the deadline path raises `pytest.skip.Exception`
with `INCONCLUSIVE` and the deadline named; **sabotage leg** — the same input under a bare
`assert` raises `AssertionError` (the old shape this replaces); an insufficient *recorded*
`Deadline` raises `HarnessDefect`; an ambient/live "measured" insufficiency yields INCONCLUSIVE
(patched transport observed → proves no live measurement); `node_meets` boundaries
(`v22.6.9`→skip, `v22.7.0`→ok, `v22.7`→ok, `v22.7.0-rc.1`→skip, `v24`→ok, `""`/garbage/None→skip).
**Step 2: Run — expect `ModuleNotFoundError`.**
**Step 3: Implement `tests/_verdict.py`.**
**Step 4: Run — expect PASS; then run `tests/test_ci_selection.py` — expect PASS.**
**Step 5: Commit.**

### Task 2: Suite-wide process-global reset

**Intent:** #4913's exit-seam budget is a once-armed process global and the ambient
`TORTOISE_API_URL` makes the suite non-hermetic (ask lane fails loud; commit lane targets
production). Both must be reset per test so an order-dependent outcome is impossible.

**Acceptance:** an autouse fixture `_process_global_isolation` in `tests/conftest.py` resets
`embedded_lifecycle._atexit_deadline` to `None` and deletes `TORTOISE_API_URL` (via
`monkeypatch.delenv`, raising=False) **before and after** every test; a deterministic order test
proves the reset, including the `monkeypatch.setattr` leg.

**Files:**
- Modify: `tests/conftest.py` (fixture + module-level `from tests._verdict import ...`)
- Test: `tests/test_verdict_contract.py`

**Step 1: Write the failing order tests** — (a) test A arms `_atexit_deadline` directly, test B
asserts `None` at body start; (b) test A arms via `monkeypatch.setattr`, test B asserts `None`;
(c) parametrized over `PROCESS_GLOBALS` so a shrinking registry goes RED.
**Step 2: Run — expect FAIL** (the global carries / registry incomplete).
**Step 3: Add the fixture.** Audit: `grep -rn "TORTOISE_API_URL" tests/` — the ask-lane files
already `delenv` it themselves; no test asserts the ambient presence.
**Step 4: Run — expect PASS; run a representative ask-lane file with the var exported — expect
unchanged.**
**Step 5: Commit.**

### Task 3: Convert the load-sensitive timeout sites

**Intent:** #4742 (fork-safety: a 4 s client socket timeout read as a wedge regression) and
#4739 (SIGTERM stdio child: a fixed wait budget read as "guard did not fire") must report
INCONCLUSIVE on deadline expiry, never FAIL.

**Acceptance:** in `tests/test_fork_safety_3845.py` a client `TimeoutError` no longer escapes as
an exception and the mutation control reports inconclusive (not FAIL, not a silent pass) when its
race does not fire; a `ps`-sampling failure is a distinct inconclusive state, not "no children";
`tests/test_embedded_lifecycle.py`'s `proc.wait(timeout=45)` + `pytest.fail(...)` sites route
through `wait_for` → INCONCLUSIVE. `_assert_server_dies_with_parent` is deliberately **left as a
FAIL guard** (the parent has already exited there, so "server still alive" is a real defect; the
FAIL leg is preserved).

**Files:**
- Modify: `tests/test_fork_safety_3845.py`
- Modify: `tests/test_embedded_lifecycle.py`
- Test: `tests/test_verdict_contract.py::test_inconclusive_on_timeout_*`

**Step 1: Write the contract-level failing test** for the conversion primitive.
**Step 2: Run — expect FAIL.**
**Step 3: Convert.** `_total_forks` / socket-bearing probes wrapped; the non-firing control and a
timeout on it → INCONCLUSIVE; keep `hung`/`forks` structural assertions as FAIL.
**Step 4: Run the converted sites in the carve-out lane — expect PASS/SKIP, never FAIL on load.**
**Step 5: Commit.**

### Task 4: Node host-capability SKIP gate

**Intent:** #4916 — Node driver sites guard only on absence; on Node < 22.7 with
`--experimental-strip-types` they RED instead of SKIP.

**Acceptance:** one shared floor declaration; the flag sites SKIP on a too-old Node; the
blog-delete guard keeps `absent="fail"` while a too-old Node SKIPs; a wiring pin proves each
converted site gates (probe patched too-old → `Skipped`; host version → driver still invoked).

**Files:**
- Modify: `tests/test_admin_origin_redirect.py`, `tests/test_provisioning_edge_function.py`,
  `tests/test_blog_agent_delete_guard.py`
- Test: `tests/test_verdict_contract.py::test_node_floor_*`

**Step 1: Write the failing probe/wiring tests.**
**Step 2: Run — expect FAIL.**
**Step 3: Implement + apply**; correct the stale `22.18` docstring on the provisioning site (it
passes the flag → the recorded flag floor is 22.7, matching
`tests/test_capture_spool.py::_node_that_can_strip_ts`).
**Step 4: Run — expect PASS.**
**Step 5: Commit.**

---

## Remaining members (not in this increment)

| Member | Home | Why deferred |
|---|---|---|
| #4497 redislite RDB schedule in subprocess fixtures | `tests/_embedded.py` + spawn sites | needs a child-process hook or per-site `serverconfig`; **not** reset per test (subprocesses don't import conftest) |
| #4499 health-truth flake | `tests/test_selfhost.py` | needs a decision (stub probe vs liveness/readiness split) |
| #4512 exact-embedding equality (**rule 3**) | `tests/test_rebuild_recreate_content_parity.py` | numerical tolerance/pin — a different contract surface, not expressible as a wait/skip |
| #4601 single-flight timing window | `tests/test_github_index_lifecycle.py` | needs an exposed synchronization seam |
| #4627 first-commit 10 s transport bound | `tortoise/hosted_api.py` + test | touches the recorded #3834 ruling → owner decision |
| #4717 order-dependent BFF clickthrough | `tests/e2e/test_auth_bff_clickthrough.py` | needs the leaking precondition identified |
| #4736 DR `APP_DOWN` 20 s vs ~17 s | `.github/scripts/registry-cron.sh` | CI-script surface; needs a measured endpoint ceiling |
| #4737 welcome-e2e 15 s vs ~10 s | `tests/e2e/test_welcome_page.py` | live-network; measure/derive budget |
| #4741 session-verify seam-fire | `tests/test_session_verify.py` | same contract, follow-up conversion |
| #4742 second file (`test_fork_slot_wedge_3845.py`) | `_CHILD_WAIT_S`, `recover_fork_slot` budgets | same contract, follow-up |
| #4945 control-plane 504 budget | `tests/test_github_index_lifecycle.py` + wait budget | needs enqueue-before-walk product change |
| #3297 live-embedder precondition | `tests/test_issue_insight.py` | pin the sparse leg |
| #3298 fresh-DB reopen runner flake | `tests/test_ingest.py` | mechanism unconfirmed |
| #4018 4-way concurrent encode | `tests/test_ask_api.py`→`test_ask*.py` | concurrency/embedder-lock surface |
| #4072/#4060 non-hermetic supersede test | `tests/test_write_consolidation.py` | per-test DB fixture for `TestMcpHandlers` |
| #4148 reaper flake | `tests/test_reaper.py` | private tempdir + spawn diagnostics |
| #4234/#4374 wall-clock detach bounds | `tests/test_cursor_capture_hook.py`, `tests/test_codex_capture_hook.py` | rule-1 conversion, follow-up |
| #4244 conftest sweep cost | `tests/conftest.py` | pass-1 skip / cached probe |
| #4277 `.env` defeats delenv | settings fixtures | point `env_file` at nothing in tests |
| #4397 registry cleared without restore | `tests/test_shared_state_events*.py` | instance fixed; class needs a guard |
| #4447/#4464 boot-sweep fault race | `tests/test_oauth_token_fault.py` | shape-scoped faults (one already fixed on main) |
| #4826 `test (a)` hangs | CI workflow | needs a job-level timeout |
| #4879 stale redislite socket replay | `tests/_embedded.py` / redislite guard | upstream redislite registry guard |
| #4026 shared `tortoise_test_matrix` | test DB provisioning | per-lane DB isolation |
| **#5049 comment 2026-09-25** `test_capture_session.py` (warm vs flushed DB) | `tests/test_capture_session.py` | verdict from reused graph state → #4026 |
| **#5049 comment 2026-09-25** `test_kind_eval_set.py` (concurrent pytest) | `tests/test_kind_eval_set.py` | verdict from a shared DB under concurrency → #4026 |

### Rule-1 driver inventory (the `wait_for` family the contract unifies)

The contract's `wait_for` is adopted at the converted sites only. The pre-existing predicate
waiters — `tests/test_index_docs_api.py`, `tests/test_github_index_lifecycle.py`,
`tests/test_4314_inert_hooks.py`, `tests/test_agent_signup.py`,
`tests/test_agent_signup_idempotency.py` (five `_wait_for(predicate, timeout)` copies);
`tests/test_embedded_lifecycle.py::{_wait_server_dead, _wait_for_registry_redis,
_wait_ready_file}`; `tests/e2e/auth/bff_test_helpers.py::wait_for_port`; and the per-file `_wait*`
copies in `test_embedded_concurrency.py`, `test_cursor_capture_hook.py`,
`test_session_capture_e2e.py`, `test_index_cli.py`, `test_selfhost.py` — are **out of scope** and
remain. They are enumerated so the next lane sees the remaining class rather than assuming the
contract closed it.

## Acceptance criteria (this increment)

- [ ] `tests/_verdict.py` exists; the contract is the single home for the **converted** sites (rules 1, 2, 4, 5); rule 3 is named separately, and the remaining rule-1 waiters are enumerated below.
- [ ] No converted site can produce FAIL from a wall-clock expiry — it SKIPs and names the deadline.
- [ ] The autouse reset removes the #4913/#4017 ordering dependence (proved by order tests incl. the `monkeypatch` leg).
- [ ] A too-old/absent-Node host SKIPs at the converted driver sites, with per-invocation floors.
- [ ] Manifest edits land (`config/ci-surfaces.yml` + `SHARED_MODULES`) so `test_ci_selection.py` is green.
- [ ] Remaining members enumerated with their home; CI stays lean (one contract file + one test file).

## Review status (plan-review, bounded at 2 cycles)

Two review cycles ran (4 proportional reviewers + the conditional Duplication/Architecture reviewer,
fresh `task` contexts). Cycle 1 found real defects — the CI-selection manifests were missing, the
`#4017` premise was stale, converting every `#2203` site would have masked a genuine regression,
the fork-safety verdict rested on the wrong assertion, and the `monkeypatch` order leg was vacuous.
Those were fixed and the plan re-reviewed. Cycle 2 confirmed the substantive fixes and returned
P1/P2 residuals, all of which are **documented, not chased**, per the owner's bound on review cycles
(no scope expansion in response to review):

| Residual | Disposition |
|---|---|
| `tests/test_fork_slot_wedge_3845.py` (#4742's second file) unconverted | Remaining members — same contract, follow-up |
| `test_pi_capture_hooks.py` / `test_capture_spool.py` node probes not migrated onto the shared floor | Remaining members — this increment declares only the flag floor (22.7); the 22.18 default-on band is the follow-up that must carry the 23.x discontinuity |
| `_assert_server_dies_with_parent` keeps a 30 s wall-clock FAIL | Deliberate: the parent has already exited there, so "server still alive" is the defect; the FAIL leg is preserved (rule 1 governs the *parent-exit* waits only) |
| `config/ci-expected-nodeids/*.txt` `allow-skipped: test_fork_safety_3845.py=2` | Unchanged: the converted skips fire only under load, not in a normal lane run; the outcome budget is the pre-existing gate, not a new allow-list |
| per-module `_isolate_atexit_budget` retained alongside the suite fixture | Documented belt-and-braces; the conftest fixture is authoritative and resets before every test, which the order test pins |
| `#4277` (`.env` can re-supply a deleted key) | Remaining members — acceptance wording narrowed to `os.environ` is clear; the production-endpoint closure is not claimed while `#4277` is deferred |

Exit reason: **capped (2 cycles)** — remaining issues acknowledged and carried into the PR body,
not silently closed. Per `AGENTS.md` §Hard Cap this is an **escalation** exit, not a clean one.

<!-- plan-review: cycles=2, status=capped, version=2.3.0 -->
