<!-- research-path: docs/plans/2026-09-22-3981-metering-operator-alert.md -->

# Metering operator alert (AlertStore incident) Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make the #3981 ruling's third leg real — a dropped metering increment (unresolvable
window) and an unenforceable cohort cap each open a deduped **AlertStore** incident (GitHub issue +
Telegram), not just a log line.

**Team:** organisation-design-team
**Issue:** #3981 (owner ruling SETTLED 2026-09-18: PROCEED AND ALERT; no new user-facing refusal)

**Architecture:** A new `tortoise/operator_alert.py` owns one generic dispatcher over the **existing**
`AlertStore` surface: it resolves the store on the caller's thread (monkeypatch-deterministic, cheap),
applies a process-local per-`(kind, org)` throttle + in-flight latch, and submits the network-bound
`open_incident_state` to a small bounded `ThreadPoolExecutor`. The two reporters
(`metering.report_unmetered_increment`, `cohort_cost.report_unenforceable_cap`) keep their synchronous
ERROR log and add one never-raising dispatcher call. One **declared** ungated builder seam
(`hosted_api._incident_alert_store`) replaces the split between `_analytics_alert_store` (ungated) and
`_cc._alert_store` (sweep-gated) — fixing the D5a divergence for the cap-firing kind too. No request
semantics change; no new refusal.

**Depends on / parallel map:** Task 1 → {2, 3, 4}; {2,3} → 6 → 8; Tasks 5, 7 have no dependencies.
Dispatchable-now set: 1, 5, 7; then 2/3/4 in parallel. (Task 4 imports the module Task 1 creates
from the suite-wide `conftest.py`, so it is NOT dependency-free — the field is the parallelism map.)

### Pattern Research

> **Gate skipped** — the plan touches **zero third-party dependencies** (stdlib `logging`/`threading`/
> `concurrent.futures`/`time` + in-repo `tortoise.alert_store`/`hosted_api`). Step A (prior-research
> intake) is honoured by the scope's `### Axis Research` block: pitfalls framing fired (Grafana /
> incident.io / rootly alerting best practices) with provenance; the in-repo canonical precedent for
> the DISPATCH leg is `mcp_server._emit_mcp_tool_call_telemetry` (`tortoise/mcp_server.py:177`) with its
> join seam `_flush_mcp_telemetry` (`~:240`). Deliberate divergence: a **module-level bounded pool**
> with a synchronous `join_operator_alerts`, not the precedent's `loop.run_in_executor`/daemon-thread +
> async flush — an operator alert is rare, so a lazy pool would add per-call cost for no benefit, and
> the bounded pool is what makes the storm bound real.

### Integration Surface Map

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes (≥2) |
|---|---------|------|-----------|-----------|----------|------------------------|
| 1 | `AlertStore.open_incident_state(kind, subject, detail)` | External (GitHub+Telegram via R2 dedup) | Out | Integration (real AlertStore over `MemoryStorage` + fake transports) | `OpenOutcome{FILED,DEDUP,SUPPRESSED}`; JSON-able detail | R2/GitHub/Telegram raise; kind suppressed; dedup hit; store `None` |
| 2 | `hosted_api._incident_alert_store()` (NEW, the declared ungated builder) + `operator_alert.alert_store()` | In-repo seam → config env | Out | Unit (**via the `real_operator_alert_store` opt-out fixture, not the autouse patch**) | `AlertStore \| None`; env-only, no network at construction | no `DR_ISSUES_PAT` → `None`; unusable `R2_*`/no `TORTOISE_BACKUP_STORAGE` → `None`; `BACKUP_SWEEP_ENABLED` off must NOT matter; `operator_alert.alert_store` returning `None` unconditionally must be RED |
| 3 | `metering.report_unmetered_increment(lane, org_id, error)` | In-repo (6 call sites) | In | Unit + integration (real inverted anchor → real `ha._record_write_op`) | never raises; logs ERROR; now also dispatches | `org_id` empty (MCP/stdio) → shared `_` subject + one throttle key; store `None`; store raising; must not 500 the request; must not emit `"UNMETERED INCREMENT"` from the new module |
| 4 | `cohort_cost.report_unenforceable_cap(org_id, error)` | In-repo (1 call site) | In | Unit + integration | never raises; logs ERROR; now also dispatches | store `None`/raising; must not turn the pre-spend gate into a refusal |
| 5 | `cohort_cost.file_cohort_cost_incident` (cap firing) | In-repo | Out | Integration | now resolves the **ungated** channel | behavior change (files when the sweep is off) — documented + tested |
| 6 | Throttle + in-flight latch + bounded pool (`operator_alert`) | State / Concurrent | Internal | Unit | ≤1 attempt per `(kind, org)` per window; maps bounded; **admitted-but-unsettled reservations** ≤ `_MAX_INFLIGHT` | wedged worker (the per-key latch self-heals via `_due_locked`; the RESERVATION deliberately does not — a wedged worker holds its pool slot); lost update; unbounded queue; pool shutdown dropping queued alerts (logged) |
| 7 | `tests/conftest.py` autouse isolation | Test seam | Internal | Test-only (**pinned T18-shape**) | `operator_alert.alert_store` patched to `None`; state reset; pool joined at teardown; `real_operator_alert_store` opt-out | a worker outliving the test (prevented by join + tracked handles); fixture deleted → cross-test throttle leak and real store construction |
| 8 | `docs/ops/registry-backup-dr.md` triage table | Docs | Out | Review | one row per kind + exact manual-close procedure | undocumented kind; row names a posture with no mechanism |
| 9 | `tests/test_analytics_fallback_alert.py` real-store leg | Test seam | Internal | Test-only | restores `ha._analytics_alert_store` | it must NOT un-isolate `operator_alert` — pinned by the Task-1 T18-shape test (`tests/test_operator_alert.py`), since nothing edits `tests/test_analytics_fallback_alert.py` |
| 10 | `backup_config.load_alert_config` / `_analytics_alert_store` D5a docstrings | Docs (in-code) | Internal | Review | build chain + seam map name the new builder | stale chain reference (must be updated in this PR) |

### Bug Pattern Flags
- **Race conditions** (surface 6): throttle/latch mutated from caller and pool threads → one
  `threading.Lock`; state written only by the attempt that owns the in-flight token; the admission
  reservation is taken and released under that same lock, so the shed decision is **atomic with**
  admission and cannot be raced by two callers; maps bounded.
- **Silent function skips** (surfaces 3/4): the whole issue is a silent drop → the RED test asserts the
  AlertStore incident (GitHub issue + Telegram), never merely a log record.
- **Conditional guards** (surface 2): `store is None` must degrade to the log, stay throttled, and
  never raise; both sides tested.
- **Stale monkeypatch / cross-test leak** (surface 7): store resolved on the caller thread; handle kept
  tracked on timeout and the fixture fails loudly if the pool does not drain.

### Verification Plan

- **Unit** (`tests/test_operator_alert.py`, new): dispatch reaches the injected store (**each incident
  assertion calls `oa.join_operator_alerts()` first — the dispatch is asynchronous**); `store is None`
  degrades and stays throttled; never raises when the store raises; long window on "on record", short
  window on failure/suppressed; in-flight latch self-heals; **attempt-ownership** (a stale worker that
  finishes after a newer attempt must NOT write `_ATTEMPT`); the store is resolved on the CALLER thread
  (ident assertion); global in-flight cap sheds (Event-gated fake + monkeypatched small `_MAX_INFLIGHT`);
  **the admission bound survives a reap** (the P1 target — see Task 1 Step 1); a reservation is
  released on all three settle paths; maps bounded and **LRU-evicted with
  `_MAX_KEYS`/`_PRUNE_ABOVE` monkeypatched small AND `move_to_end` genuinely pinned** (at 1_000 keys
  against a 1024 cap eviction is unobservable, so the small-cap test must force the eviction path
  itself — see Task 1 Step 1); `join_operator_alerts` returns 0.
- **Unit — the real builder** (`tests/test_operator_alert.py`): request the `real_operator_alert_store`
  fixture (the `_operator_alert_isolation` autouse patch would otherwise return `None` and make the
  test unsatisfiable), patch `tortoise.hosted_backup.MemoryStorage` via `ha._backup_storage` + the three
  egress callables, and assert the REAL `operator_alert.alert_store()` is non-`None` with
  `BACKUP_SWEEP_ENABLED` unset and `None` without `DR_ISSUES_PAT` (the T14 pattern). Kills a mutation
  that dead-wires the production entry point (M5).
- **Unit — the unusable object store** (SEPARATE test, **must NOT patch `ha._backup_storage`** — with it
  patched to `MemoryStorage` the `R2_*` env is never consulted and the assertion cannot fail): request
  `real_operator_alert_store`, set `DR_ISSUES_PAT`/`GH_REPO`/`TELEGRAM_*`, delete all four `R2_*`, unset
  `TORTOISE_BACKUP_STORAGE`, and assert the real `oa.alert_store() is None` (`R2Storage.__init__` is
  what raises, `tortoise/hosted_backup.py:848-868`; the plan's earlier bundling of this case with the
  `MemoryStorage`-patched case was self-contradictory).
- **Unit — the delegation pin** (`tests/test_operator_alert.py`): monkeypatch
  `ha._incident_alert_store` to a sentinel and assert `ha._analytics_alert_store() is sentinel` and
  `oa.alert_store() is sentinel` (using `real_analytics_alert_store`/`real_operator_alert_store`).
  Without it, reverting either delegation to an inlined COPY of the builder body stays green — the
  exact split this PR removes — so the seam-map claim would rest on the diff alone.
- **Test-seam pin — T18 shape, so it actually reds** (`tests/test_operator_alert.py`): install a FULL
  alert env (`DR_ISSUES_PAT`/`GH_REPO`/`TELEGRAM_*`) + `_backup_storage` → `MemoryStorage`, assert
  `ha._incident_alert_store() is not None` (the box COULD build a real store), then assert
  `oa.alert_store() is None` under the autouse fixture. Deleting the fixture makes the second
  assertion fail on every machine (a bare `is None` would pass on CI where no creds exist).
- **Integration** (`tests/test_metering_window_admission.py`): real inverted anchor → real
  `ha._record_write_op` → real `AlertStore` over fakes → GitHub issue + Telegram asserted. RED if the
  alert does not fire. Plus a loop-thread (executor) branch test.
- **Integration** (`tests/test_cohort_cost_cap.py`): the unenforceable window files
  `COHORT_CAP_UNENFORCEABLE`; a cap firing still files `COHORT_COST_CAP` (now also when the sweep is
  off — explicitly tested); the GitHub-search adoption path is not confused.
- **Regression**: `test_metering.py`, `test_metering_period_window.py`,
  `test_metering_window_admission.py`, `test_cohort_cost_cap.py`, `test_alert_store.py`,
  `test_analytics_fallback_alert.py`, `test_notify.py`.
- **Mutation evidence** (mandatory, recorded in the PR): (M1) delete the `alert_operator` call in
  `report_unmetered_increment` → new integration test RED; (M2) make `alert_operator` a no-op → RED;
  (M3) move `alert_store()` resolution into `_run` → `test_alert_store_resolves_on_the_caller_thread`
  RED; (M4) drop the `_INFLIGHT` token check in `_run` → `test_a_stale_worker_cannot_own_the_state` RED;
  (M5) make `operator_alert.alert_store()` return `None` unconditionally → the real-builder unit test
  RED (and it must NOT be GREEN via any injected-store test); **(M6) revert the shed gate to
  `len(_HANDLES) >= _MAX_INFLIGHT` (the state this plan replaced) →
  `test_the_admission_bound_survives_a_reap` RED** — the P1 target, and the one mutation that proves
  the bound is real rather than age-reaped away; **(M7) delete the `_ATTEMPT.move_to_end(key)` line →
  the LRU-eviction test RED** — the mutation the old LRU test could not catch because the expiry sweep
  trimmed every entry before eviction could be reached. (The site M7 names is the re-attempt refresh in
  `alert_operator`; `_run` carries the same call for the completion path.)
- **Skipped / why:** no E2E/Playwright (no user-facing change — the ruling forbids a new user surface);
  no pgTAP (no SQL/migration); no UX verification (UX_RATING=low).

**Tech Stack:** Python 3.12, stdlib only; pytest embedded carve-out lane
(`TORTOISE_TEST_CARVE_OUT=1`), `tortoise.hosted_backup.MemoryStorage`, `tests/fake_control_plane`.
(There is no `tests/hosted_backup` module — `MemoryStorage` lives in `tortoise/hosted_backup.py:967`.)

---

### Task 1: `tortoise/operator_alert.py` + the declared ungated builder

**Intent:** One generic, never-raising way to turn an absorbed failure into an AlertStore incident,
off the request path and repeat-suppressed, over one **declared** alert-channel seam.
**Acceptance:** `alert_operator(kind, org_id, detail)` opens the incident via the injected store,
returns a handle, never raises; `join_operator_alerts()` settles everything; `hosted_api._incident_alert_store()`
is the single ungated builder and `_analytics_alert_store`/`cohort_cost._alert_store` delegate to it.

**Depends on:** —
**Files:**
- Create: `tortoise/operator_alert.py`
- Modify: `tortoise/hosted_api.py` (add `_incident_alert_store`; make `_analytics_alert_store` delegate)
- Modify: `tortoise/cohort_cost.py` (`_alert_store` delegates)
- Test: `tests/test_operator_alert.py`

**Step 1: Write the failing tests** — dispatch files an incident (**call `oa.join_operator_alerts()`
before every assertion on the fake — the dispatch is asynchronous; without the join each of these is
flaky under load**); `store is None` → no filing, no raise, stays throttled; store raising → no raise;
`FILED`/`DEDUP` arm the long window, `SUPPRESSED`/failure the short one; in-flight latch self-heals
after `_INFLIGHT_STALE_S`;
- `test_a_stale_worker_cannot_own_the_state` (**the M4 target**): gate attempt #1 in an Event-blocked
  fake store; monkeypatch `_INFLIGHT_STALE_S` AND `_RETRY_WINDOW_S` small (aging only the latch leaves
  attempt #1's provisional `_ATTEMPT` entry throttling the key for the full default 60 s, so #2 would
  never be admitted); dispatch #2 (it must be admitted) and **await #2's handle**; only THEN release
  #1; assert #1's completion did NOT write `_ATTEMPT[key]` (the entry still reflects #2) and the store
  saw exactly two calls. Ordering matters: under the mutation (guard deleted) the LAST writer wins, so
  releasing #1 **before** #2 writes would leave #2's value in place and read GREEN — the stale worker
  must finish after the current owner has written;
- store resolution on the CALLER thread (the fake records `threading.get_ident()`);
- the global cap sheds: monkeypatch `_MAX_INFLIGHT` to a small value, use an Event-gated fake so no
  worker drains (`len` cannot oscillate — without the gate this test is scheduling-flaky), dispatch
  N+1, assert the extra returns `None` and logs the shed warning, then release + join;
- **`test_the_admission_bound_survives_a_reap` — the P1 target**: same Event-gated fake, small
  `_MAX_INFLIGHT`, small `_INFLIGHT_STALE_S` and small `_RETRY_WINDOW_S`, **and `_SWEEP_EVERY=1`** —
  a bare `sleep` does not run `_prune_locked`, so with the default amortisation the reap never fires
  and the mutation reads GREEN (both gates admit); the test must also assert
  `len(oa._HANDLES) == 0` **as a precondition** before the N+1 dispatch, so it cannot pass for the
  wrong reason. Dispatch N **distinct** keys (all admitted, all wedged, each holding a reservation),
  sleep past `_INFLIGHT_STALE_S` so `_reap_locked` drops every handle and `_due_locked` would lift
  each per-key latch, then dispatch a NEW key N+1 and assert it is **STILL shed** (`None` + the shed
  warning) and that the store saw nothing new. Under the old `len(_HANDLES)` gate this reads GREEN
  (N+1 admitted) while the executor queue grows without limit across reaps — the vacuous bound. Then
  release the gate and **poll `oa._RESERVED` to 0** (bounded deadline) rather than
  `join_operator_alerts()`, which returns 0 immediately here because the reap already dropped every
  handle it would have waited on — the one case where the join seam cannot speak for the pool;
- **a reservation is released on ALL THREE settle paths**, each leaving `oa._RESERVED == 0`: worker
  completion (`_run`'s `finally` releases it, so `join_operator_alerts()` then reading the counter is
  deterministic — a done-**callback** release would race `Future.set_result`, which notifies waiters
  before invoking callbacks), `alert_store()` → `None`, and submit failure (monkeypatch `oa._POOL` with
  a stub whose `submit` raises). A leaked reservation is a permanently-lost slot, so each path is
  asserted separately rather than as one end-to-end flow. Also cancel a queued future (Event-gated
  pool) and assert its reservation is released by `_forget`'s `cancelled()` branch;
- maps bounded + **LRU-evicted, and `move_to_end` actually pinned**: monkeypatch `_MAX_KEYS`/
  `_PRUNE_ABOVE` small, `_SWEEP_EVERY=1` (the sweep is amortised out of the hot path, so a test must
  request it) and `_RETRY_WINDOW_S` **large** so nothing expires — otherwise the expiry sweep trims
  every entry first and the eviction path is never reached, which is exactly the unpinned test this
  replaces; force the re-touch by monkeypatching `_due_locked` → `True`, **fill to EXACTLY
  `_MAX_KEYS`** (not `_MAX_KEYS + 2` — the eviction runs at the START of a dispatch, so inserting
  `_MAX_KEYS + 2` keys evicts the FIRST before the re-dispatch can re-touch it, and the re-dispatch
  then re-INSERTS it at the tail, making `move_to_end` unobservable — the vacuous predecessor), then
  re-dispatch the OLDEST and assert it is now at the tail, then insert TWO more keys (the count must
  exceed the cap before the inserting dispatch's prune) and assert the OLDEST survives and the
  SECOND-oldest is gone. Gate the store so `_run`'s completion path cannot refresh the key and mask
  the mutation. Deleting the `_ATTEMPT.move_to_end(key)` on the **re-attempt path in
  `alert_operator`** (and, for completeness, the completion-path one in `_run`) must turn this RED —
  the re-touched key is then still least-recently-*inserted* and `popitem(last=False)` evicts it;
  `join_operator_alerts()` returns 0;
- the REAL `operator_alert.alert_store()` via the `real_operator_alert_store` fixture: non-`None` with
  the sweep off, `None` without `DR_ISSUES_PAT`, and `None` with `DR_ISSUES_PAT` set but no usable
  object store (all four `R2_*` deleted, `TORTOISE_BACKUP_STORAGE` unset) — the M5 target.

**Step 2: Run** `TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_operator_alert.py -q`
→ FAIL (module missing).

**Step 3: Implement.**

`tortoise/hosted_api.py` — the declared seam (place next to `_analytics_alert_store`), and make
`_analytics_alert_store` delegate to it:

```python
def _incident_alert_store():
    """THE alert-channel builder for operator incidents — gated on ALERT creds only.

    Deliberately NOT gated on ``BACKUP_SWEEP_ENABLED`` (#3820 D5a): the sweep switch
    decides whether backups RUN, never whether an incident is VISIBLE. When the
    sweep is enabled its config is used as-is; otherwise ``load_alert_config``
    reads the alert credentials ungated. Returns ``None`` when there is no issue
    filer (``DR_ISSUES_PAT`` unset) or the object store cannot be built; the
    caller then keeps its log line. Env-only: no network at construction.

    SEAM MAP (one builder, several names — for a reader, not for a caller):
      * ``_incident_alert_store`` — the chokepoint holding the ALERT-only policy.
        Production builders must route through it; the raw chain
        ``_backup_config_safe() -> _alert_store_from`` bypasses the policy and is
        a known set of follow-ups (Task 7).
      * ``_analytics_alert_store`` / ``operator_alert.alert_store`` — TEST PATCH
        POINTS (independent, so each plane is isolated on its own); both delegate
        here and neither adds policy.
      * ``cohort_cost._alert_store`` — retained as a stable internal API for its
        module (its only patcher, the ``incidents`` fixture, now patches
        ``operator_alert.alert_store`` instead).
      * ``_alert_store_from(cfg)`` — the pure constructor from an already-loaded
        config; it dereferences ``cfg`` and must never be handed ``None``.
    """
    try:
        cfg = _backup_config_safe()
        if cfg is None:
            from tortoise.backup_config import load_alert_config
            cfg = load_alert_config()
        if cfg is None:
            return None
        return _alert_store_from(cfg)
    except Exception as e:  # absence of a channel is not a loss
        _logger.warning("incident alert store unavailable: %s", e)
        return None


def _analytics_alert_store():
    """The AlertStore for sink incidents (#3820). Delegates to the shared seam."""
    return _incident_alert_store()
```

`tortoise/cohort_cost.py`:

```python
def _alert_store():
    """The AlertStore, or None when no alert channel is configured.

    Delegates to the operator-alert seam so a cap-firing incident files on a
    sweep-disabled deployment too (#3981; D5a class) — the pre-existing gating
    left the money-capping alert invisible by default. Indirection kept callable
    so tests substitute a real AlertStore over fake transport.
    """
    from tortoise.operator_alert import alert_store
    return alert_store()
```

`tortoise/operator_alert.py`:

```python
"""Operator alerts for failures we absorb (#3981) — the incident channel.

The #3981 ruling (2026-09-18, "PROCEED AND ALERT") requires three things: the
request proceeds, the increment is recorded as unmeterable, and an OPERATOR
ALERT FIRES. This module is the third leg: it submits an absorbed failure to the
repo's existing dual-channel sink (:class:`tortoise.alert_store.AlertStore` —
GitHub issue + Telegram, create-once dedup per (kind, subject)) through a bounded
thread pool (``_POOL``), so the filing (network) work runs off the caller's thread.

Design:
- The reporters run on the request path (some inline on the event loop) and a
  broken anchor drops an increment on EVERY write, so the filing is
  repeat-suppressed per (kind, org) and dispatched to a small bounded pool.
- The store is resolved on the CALLER's thread and passed into the worker.
  Resolution is env-only (no network). Resolving it inside the worker would run
  after the caller's monkeypatch was undone — bypassing the suite-wide alert
  isolation and able to file a real issue.
- The dispatcher mirrors ``mcp_server._emit_mcp_tool_call_telemetry``
  (``tortoise/mcp_server.py:177``; join seam ``_flush_mcp_telemetry``, ``:242``) — off-loop, tracked
  handles, a join seam — with a bounded pool for an alert: an unbounded thread per dropped increment
  is exactly the storm this exists to avoid.

Nothing here changes request semantics: the callers already absorb the failure
and serve the request.
"""

from __future__ import annotations

import atexit
import collections
import concurrent.futures
import logging
import threading
import time
from typing import Any

_logger = logging.getLogger("tortoise.operator_alert")

#: Repeat-suppression windows (seconds). A recorded incident holds the long
#: window; an attempt that recorded NOTHING re-arms on the short one, so a
#: transient channel outage delays the first alert by at most a minute. The
#: condition is persistent and AlertStore already dedups the INCIDENT, so this
#: suppresses ATTEMPTS — it is not a persistence gate.
_ALERT_WINDOW_S = 900.0
_RETRY_WINDOW_S = 60.0
#: A worker that outlives this is assumed wedged. The reservation it holds is
#: released at the sites listed on ``_RESERVED``.
_INFLIGHT_STALE_S = 120.0
#: Global dispatch bound: a sweep-scale outage drops for many orgs at once.
_MAX_INFLIGHT = 32
#: Map bounds: keys outside the windows are pruned; a hard cap evicts oldest.
_MAX_KEYS = 1024
_PRUNE_ABOVE = 256

_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="operator-alert")

_LOCK = threading.Lock()
#: Least-recently-attempted first: eviction must not reset the throttle of the
#: persistently-failing keys a sweep-scale outage produces while keeping cold keys.
_ATTEMPT: "collections.OrderedDict[tuple[str, str], tuple[float, float]]" = collections.OrderedDict()
_INFLIGHT: dict[tuple[str, str], float] = {}                # key -> attempt token
#: ADMITTED-but-unsettled dispatches. Taken under _LOCK at admission and released at
#: these sites: `_run`'s `finally` once a started dispatch settles, `_forget` when a
#: future was cancelled before `_run` started (the done-callback `alert_operator`
#: registers), and `alert_operator`'s two pre-pool failure paths (no store, rejected
#: submit). `_reap_locked` does not decrement it. The gate is `_RESERVED >=
#: _MAX_INFLIGHT`.
_RESERVED = 0
#: future -> submitted ts. Used by `join_operator_alerts` and by _reap_locked for
#: dead-handle housekeeping — NOT by the shed gate (that reads _RESERVED).
_HANDLES: dict[Any, float] = {}
#: The full-map sweep is amortised housekeeping, not the gate: the admission decision
#: is O(1) (`_due_locked` + `_RESERVED`), so the O(n) scan runs once per _SWEEP_EVERY
#: admissions (and whenever _ATTEMPT exceeds _PRUNE_ABOVE) instead of on every
#: hot-path write. It prunes `_ATTEMPT` and ages dead handles out of `_HANDLES`; it
#: does not touch `_INFLIGHT`.
_SWEEP_EVERY = 64
_SINCE_SWEEP = 0


def _shutdown_pool() -> None:
    """Drop queued alerts at exit — LOUDLY. cancel_futures discards work with no
    log and no incident, which is the silent-drop class this module exists to
    remove; at least leaving a WARNING makes the loss visible in the shutdown tail
    (the transports are 15s-bounded, so the pending set is normally tiny)."""
    with _LOCK:
        # Not a drop/running split — cancel_futures only cancels the QUEUED ones;
        # the running ones are abandoned to interpreter exit. Say that, not "dropped".
        pending = sum(1 for f in _HANDLES if not f.done())
    if pending:
        _logger.warning(
            "operator alerts unsettled at shutdown (pending=%d; queued cancelled, "
            "running abandoned)", pending)
    _POOL.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown_pool)


def alert_store():
    """The shared UNGATED alert-channel builder, or ``None``.

    Delegates to ``hosted_api._incident_alert_store`` — the one declared seam
    gated on alert credentials only, never on ``BACKUP_SWEEP_ENABLED`` (#3981,
    #3820 D5a). Tests patch THIS name (or the seam) to inject a fake.
    """
    from tortoise import hosted_api as _ha
    return _ha._incident_alert_store()


def file_operator_incident(store, kind: str, org_id: str | None, detail: dict) -> bool:
    """Open (or re-use) the incident; True iff one is ON RECORD. Never raises.

    ``open_incident_state`` — NOT ``open_incident``: that bool is True only for
    a FILED call, so a DEDUP hit (an incident IS on record) would read as "not
    on record" and re-arm the short window. ``store`` is resolved by the caller.

    The on-record rule (``outcome in {FILED, DEDUP}``, i.e. ``is not SUPPRESSED``)
    is ALSO implemented by ``hosted_api._analytics_open_incident``, and
    ``tests/test_operator_alert.py::test_on_record_predicate_parity`` pins the two
    equal over every :class:`OpenOutcome` member. Extracting one shared helper is
    deferred (filed as a follow-up).
    """
    if store is None:
        return False
    try:
        from tortoise.alert_store import OpenOutcome
        return store.open_incident_state(kind, org_id or "", dict(detail)) in (
            OpenOutcome.FILED, OpenOutcome.DEDUP)
    except Exception:  # noqa: BLE001 — the alert must never raise
        _logger.warning("operator incident filing failed (kind=%s)", kind,
                        exc_info=True)
        return False


def _reap_locked(now: float) -> None:
    """Drop dead handles past the stale bound — map housekeeping, not the gate.

    Does not touch ``_RESERVED``.
    """
    for f, ts in list(_HANDLES.items()):
        if now - ts > _INFLIGHT_STALE_S:
            _HANDLES.pop(f, None)


def _release_locked() -> None:
    """Release one admission reservation (caller holds ``_LOCK``); floors at 0."""
    global _RESERVED
    if _RESERVED > 0:
        _RESERVED -= 1


def _prune_locked(now: float) -> None:
    """Amortised map housekeeping — never the admission decision (see _RESERVED)."""
    if len(_ATTEMPT) > _PRUNE_ABOVE:
        for k, (ts, window) in list(_ATTEMPT.items()):
            if now - ts >= window:
                _ATTEMPT.pop(k, None)
        while len(_ATTEMPT) > _MAX_KEYS:
            _ATTEMPT.popitem(last=False)          # least-recently-attempted
    for k, started in list(_INFLIGHT.items()):
        if now - started > _INFLIGHT_STALE_S:
            _INFLIGHT.pop(k, None)
    _reap_locked(now)


def _due_locked(key: tuple[str, str], now: float) -> bool:
    started = _INFLIGHT.get(key)
    if started is not None:
        if now - started <= _INFLIGHT_STALE_S:
            return False
        _INFLIGHT.pop(key, None)          # wedged worker self-heals
    last = _ATTEMPT.get(key)
    return last is None or now - last[0] >= last[1]


def _run(store, key, kind, org_id, detail, token) -> None:
    try:
        try:
            on_record = file_operator_incident(store, kind, org_id, detail)
        except Exception:  # noqa: BLE001 — belt-and-braces
            on_record = False
        with _LOCK:
            if _INFLIGHT.get(key) != token:
                return                    # a newer attempt owns the state
            _INFLIGHT.pop(key, None)
            _ATTEMPT[key] = (time.monotonic(),
                             _ALERT_WINDOW_S if on_record else _RETRY_WINDOW_S)
            _ATTEMPT.move_to_end(key)
    finally:
        # Release the admission reservation HERE, not in the done-callback:
        # Future.set_result notifies waiters BEFORE invoking done callbacks, so a
        # callback release races the joining thread and makes `join → _RESERVED == 0`
        # flaky. `finally` runs before the future is marked done.
        with _LOCK:
            _release_locked()


def _forget(fut) -> None:
    """Drop one dispatch's handle; release its reservation if the future was
    cancelled before ``_run`` started.

    A started future releases its reservation in ``_run``'s ``finally``.
    """
    with _LOCK:
        _HANDLES.pop(fut, None)
        if fut.cancelled():
            _release_locked()


def alert_operator(kind: str, org_id: str | None, detail: dict | None = None):
    """Fire-and-forget operator alert; returns the handle or None. Never raises."""
    global _SINCE_SWEEP, _RESERVED
    key = (kind, org_id or "")
    now = time.monotonic()
    shed = False
    with _LOCK:
        _SINCE_SWEEP += 1
        if _SINCE_SWEEP >= _SWEEP_EVERY:
            _SINCE_SWEEP = 0
            _prune_locked(now)
        if not _due_locked(key, now):
            return None
        _ATTEMPT[key] = (now, _RETRY_WINDOW_S)   # provisional; _run re-arms
        _ATTEMPT.move_to_end(key)
        # The admission bound: a reservation taken with the decision and released at
        # the sites listed on _RESERVED.
        if _RESERVED >= _MAX_INFLIGHT:
            shed = True                          # bounded: shed, keep throttled
        else:
            token = now
            _RESERVED += 1
            _INFLIGHT[key] = token
    if shed:
        _logger.warning("operator alert shed — dispatch queue full (kind=%s)", kind)
        return None
    try:
        store = alert_store()                    # CALLER thread — deterministic
    except Exception:
        store = None
    if store is None:
        with _LOCK:
            _INFLIGHT.pop(key, None)
            _release_locked()
        _logger.warning("operator alert not filed — no alert channel (kind=%s)", kind)
        return None
    try:
        fut = _POOL.submit(_run, store, key, kind, org_id, dict(detail or {}), token)
    except Exception:  # pool shutting down — never drop the alert silently
        with _LOCK:
            _INFLIGHT.pop(key, None)
            _release_locked()
        _logger.warning("operator alert dispatch failed (kind=%s)", kind,
                        exc_info=True)
        return None
    # Register the settle callback BEFORE recording the handle, and record it only
    # while the future is unsettled (both under _LOCK, so _forget cannot interleave):
    # a future that finishes in this window would otherwise leave a dead handle in
    # _HANDLES forever (and, if cancelled, leak its reservation).
    fut.add_done_callback(_forget)
    with _LOCK:
        if not fut.done():
            _HANDLES[fut] = time.monotonic()
    return fut


def join_operator_alerts(timeout: float = 5.0) -> int:
    """Wait up to *timeout* for in-flight dispatches; returns the UNSETTLED count.

    ``0`` means every tracked dispatch settled within the timeout. A handle aged past
    ``_INFLIGHT_STALE_S`` is dropped from ``_HANDLES``, so a wedged worker can be
    untracked here while still holding its pool thread and its reservation. A
    timed-out handle stays tracked. Never raises.
    """
    with _LOCK:
        handles = list(_HANDLES)
    deadline = time.monotonic() + timeout
    unsettled = 0
    for h in handles:
        try:
            h.result(max(0.0, deadline - time.monotonic()))
        except concurrent.futures.TimeoutError:
            unsettled += 1
            continue
        except Exception:
            pass
        with _LOCK:
            _HANDLES.pop(h, None)
    return unsettled


def reset_operator_alert_state_for_tests() -> None:
    """Clear the process-local throttle/latch maps (test state, not the pool)."""
    with _LOCK:
        _ATTEMPT.clear()
        _INFLIGHT.clear()
```

**Step 4: Run** the tests → PASS.

**Step 5: Commit.**

---

### Task 2: wire `metering.report_unmetered_increment`

**Intent:** The dropped increment dispatches the incident.
**Acceptance:** the reporter still logs ERROR and never raises; it now also dispatches
`("UNMETERED_INCREMENT", org_id, {"lane": lane, "error_type": type(error).__name__})`.

**Depends on:** Task 1
**Files:**
- Modify: `tortoise/metering.py` (`report_unmetered_increment`, ~:364-392)
- Modify: `tortoise/hosted_api.py` — `_alert_unmetered` (`:4559-4578`) has an import-guard fallback that
  logs `"UNMETERED INCREMENT (#3981)…"` directly and returns when
  `from tortoise.metering import report_unmetered_increment` itself fails (a partial deploy, or
  metering broken). That path drops the increment with NO incident, so the "every drop alerts" contract
  is only true if this fallback also dispatches. **DECISION (made here, not deferred): wire it** — call
  `operator_alert.alert_operator(UNMETERED_INCREMENT_KIND, org_id, {"lane": lane,
  "error_type": type(error).__name__})` in that branch, under its own guarded import (it must not
  re-raise in the branch whose whole purpose is that metering is unavailable; `alert_operator` is
  never-raising, and its own import failure is why the guard wraps the import, not the call). The other
  option — an accepted log-only residual — is rejected because it would leave the one path where the
  ledger AND the reporter are both down with no operator signal at all, and the six-site coverage
  inventory this plan pins ("the six swallow sites are the six lanes") would be incomplete on exactly
  the partial-deploy case that most needs
  it. Task 2's test asserts this branch (`monkeypatch` `sys.modules["tortoise.metering"]` to `None` or
  raise on import, then call `ha._alert_unmetered` → a recording fake store sees the incident).
- Test: `tests/test_metering_window_admission.py`

Add inside the existing `contextlib.suppress(Exception)` body, after the `_logger.error`:

```python
    with contextlib.suppress(Exception):  # the alert must never raise
        from tortoise.operator_alert import alert_operator
        alert_operator(UNMETERED_INCREMENT_KIND, org_id,
                       {"lane": lane, "error_type": type(error).__name__})
```

Declare the kind constant **in `operator_alert.py`** (mirroring `cohort_cost.UNENFORCEABLE_INCIDENT_KIND`) —
NOT in `metering.py`. The import-guard fallbacks that most need it exist *because* importing
`tortoise.metering` failed, so a constant living there is unreachable exactly where it is used and the
guarded import would swallow the `NameError` into a silent drop. All six swallow sites now call ONE
entry point, `operator_alert.alert_unmetered_increment(lane, org_id, error)`, which owns the kind, the
message-free detail vocabulary, and the never-raising contract — a bare literal at each site would let
a typo diverge from the Task-5 runbook row with every test still green.

**Detail is a fixed vocabulary, deliberately message-free:** the incident body lands in a durable
GitHub issue, so it carries no raw exception text. `notify.redact_safe` is NOT relied on for
disclosure control (it strips only three named secrets + `://user:pass@` URIs, not org data). The
detailed failure mode stays in the ERROR log (and Sentry); the durable issue is a stable pointer.
Update the docstring to say the incident — not just the log — is the operator signal.

Test step (**this task, not Task 6**): a direct unit test patches `operator_alert.alert_store` → a
recording fake, calls `report_unmetered_increment("write_op", "org-x", QuotaCheckError("x"))`, then
calls `operator_alert.join_operator_alerts()` **before** asserting (**the dispatch is asynchronous —
without the join this is flaky under load**), and asserts exactly `("UNMETERED_INCREMENT", "org-x")`
plus the `{lane, error_type}` detail, and that the call never raised. Commit.

---

### Task 3: wire `cohort_cost.report_unenforceable_cap`

**Intent:** The unenforceable pre-spend cap dispatches its own incident.
**Acceptance:** same shape, kind `COHORT_CAP_UNENFORCEABLE`, detail `{"error_type": ...}` (same
message-free rule); the pre-spend gate raises nothing new.

**Depends on:** Task 1
**Files:**
- Modify: `tortoise/cohort_cost.py` (add `UNENFORCEABLE_INCIDENT_KIND`, wire `report_unenforceable_cap`)
- Test: `tests/test_cohort_cost_cap.py`

`UNENFORCEABLE_INCIDENT_KIND = "COHORT_CAP_UNENFORCEABLE"` — deliberately **not** a superstring of
`INCIDENT_KIND = "COHORT_COST_CAP"`, so the R2-unreachable GitHub-search adoption path cannot confuse
the two. Add the same suppressed `alert_operator` call. **Also** verify (Task 5/6) that
`file_cohort_cost_incident` now resolves the ungated channel via the delegated `_alert_store`.

Test step (**this task**): a direct unit test patches `operator_alert.alert_store` → recording fake,
calls `report_unenforceable_cap("org-x", QuotaCheckError("x"))`, then `join_operator_alerts()` before
asserting `COHORT_CAP_UNENFORCEABLE` + `{error_type}` and no raise. Commit.

---

### Task 4: suite-wide test isolation

**Intent:** A worker must never outlive a test's monkeypatch and reach real R2/GitHub/Telegram, and an
analytics test restoring `_analytics_alert_store` must not un-isolate this plane.
**Acceptance:** every test resets `operator_alert` state, patches `operator_alert.alert_store` → `None`
independently, asserts the pool drained at teardown, and **a test pins the isolation** so deleting the
fixture reds (cross-test throttle leak + real store construction).

**Depends on:** Task 1 (the fixture imports `tortoise.operator_alert`; a missing module would red the
ENTIRE suite at fixture setup)
**Files:**
- Modify: `tests/conftest.py` (next to `_analytics_alert_isolation`, ~:1065)

```python
_REAL_OPERATOR_ALERT_STORE = None


@pytest.fixture(autouse=True)
def _operator_alert_isolation(monkeypatch):
    """Never let a test build a real #3981 operator incident.

    Patches the operator plane's OWN seam (``operator_alert.alert_store``) so it is
    isolated INDEPENDENTLY of ``_analytics_alert_isolation`` — notably, a test that
    restores the real analytics builder (``real_analytics_alert_store``) must not
    thereby un-isolate this plane. Resets the throttle/latch and asserts the pool
    drained, so a worker cannot run after the test (and cannot write state into the
    next test). A test that needs the REAL builder requests
    ``real_operator_alert_store`` — a test that calls it under the autouse patch
    would silently get ``None`` and could never satisfy its own assertion.
    """
    global _REAL_OPERATOR_ALERT_STORE
    import tortoise.operator_alert as oa
    if _REAL_OPERATOR_ALERT_STORE is None:
        _REAL_OPERATOR_ALERT_STORE = oa.alert_store
    monkeypatch.setattr(oa, "alert_store", lambda: None)
    oa.reset_operator_alert_state_for_tests()
    yield
    # Honest limit: a handle aged past _INFLIGHT_STALE_S is dropped from _HANDLES, so a
    # genuinely wedged worker is untracked here and this join cannot speak for it (its
    # reservation is deliberately still held). This asserts the normal case — nothing
    # an individual test dispatched is still running when it ends.
    assert oa.join_operator_alerts(timeout=5.0) == 0, "operator-alert pool did not drain"


@pytest.fixture
def real_operator_alert_store(monkeypatch):
    """OPT OUT of ``_operator_alert_isolation`` for the builder-under-test."""
    import tortoise.operator_alert as oa
    assert _REAL_OPERATOR_ALERT_STORE is not None, (
        "real builder not captured — _operator_alert_isolation must run first"
    )
    monkeypatch.setattr(oa, "alert_store", _REAL_OPERATOR_ALERT_STORE)
    return _REAL_OPERATOR_ALERT_STORE
```

---

### Task 5: runbook triage rows

**Intent:** Each kind is documented where an operator triages, with an honest channel/posture.
**Acceptance:** `docs/ops/registry-backup-dr.md` triage table has `UNMETERED_INCREMENT` and
`COHORT_CAP_UNENFORCEABLE`, **and** the previously-missing `COHORT_COST_CAP` row (now also ungated).
**Each row must state the exact close procedure, not just "manual":** delete
`ops/alerts/{KIND}/{org_or_underscore}.json` **and** close the GitHub issue, in that order. Naming a
"manual-close posture" without the mechanism is a silent-missed-alert trap: nothing in `tortoise/`
calls `resolve_incident` for these kinds, and because create-once dedup treats a surviving R2 object as
"already on record" (DEDUP), an operator who closes only the issue leaves the object arming the long
window while Telegram (which pushes only on FILED) never fires again — the next real outage files
nothing. (The alternative — one generic `POST /v1/internal/alerts/resolve {kind, subject}` — is a bigger
surface; the delete-object procedure is the minimal correct one and is what the row must say.)

**Depends on:** —
**Files:**
- Modify: `docs/ops/registry-backup-dr.md` (§Alert taxonomy + triage, ~:445-465)

---

### Task 6: the RED integration tests (the mandated deliverable)

**Intent:** Prove the operator alert fires end-to-end, and that it is RED when it does not.
**Acceptance:** real inverted anchor → real writer → real `AlertStore` over `MemoryStorage` + fakes →
GitHub issue titled `[DR] UNMETERED_INCREMENT — <org>` + Telegram.

**Depends on:** Tasks 2, 3
**Files:**
- Modify: `tests/test_metering_window_admission.py`
- Modify: `tests/test_cohort_cost_cap.py`

`tests/test_metering_window_admission.py`:
- Make `_unmetered_lanes` robust **without** a logger-name filter (a name filter would blind it to
  `ask_lane`): keep the message predicate and skip records lacking a `lane=` token, so a future record
  containing the marker without a lane cannot `IndexError`.
- New `test_dropped_increment_files_an_operator_incident(request, monkeypatch)`: build the real
  `reg_org` fixture, set a real inverted anchor, patch `operator_alert.alert_store` → a real `AlertStore`
  over `MemoryStorage` + fake transports, call `ha._record_write_op({"org_id": tid, "tier": "pro"})`
  (directly — the hosted endpoint exceeds the wait budget on a loaded box), `oa.join_operator_alerts()`,
  then assert a `[DR] UNMETERED_INCREMENT` title was filed and `telegram` non-empty.
- New `test_alert_store_resolves_on_the_caller_thread(monkeypatch)`: patch `operator_alert.alert_store`
  with a fake recording `threading.get_ident()`; dispatch from the TEST thread inside an
  `asyncio.run(...)`-created loop (exercising the pool from a loop-thread caller) and assert every
  recorded ident equals the test thread's. **This is the M3 target.**
- New `test_ambiguous_org_ids_share_one_incident_and_throttle`: `org_id=None`/`""` collapse to ONE R2
  dedup key (`…/UNMETERED_INCREMENT/_.json`) and one throttle key; **the incident SUBJECT is empty**
  (`[DR] UNMETERED_INCREMENT`, no ` — org`) — assert `len(issues) == 1` and the throttle, not a
  `_`-suffixed title (which AlertStore never renders).

`tests/test_cohort_cost_cap.py`:
- `incidents` fixture: patch `operator_alert.alert_store` → the real store (replacing the
  `_cc._alert_store` patch, since `_cc._alert_store` now delegates to it). One seam covers cap firing
  and the unenforceable alert.
- `test_unresolvable_window_is_served_and_alerted_never_500`: call `oa.join_operator_alerts()` before
  asserting; **DELETE the now-contradictory final `assert not incidents.issues, "an unenforceable cap
  is NOT a cap firing — it must not raise a cap incident"` and rewrite that docstring sentence** (the
  kind distinguishes them now, not the absence of an incident). Use **membership** assertions (not list
  equality): no title contains `[DR] COHORT_COST_CAP`; a title contains `[DR] COHORT_CAP_UNENFORCEABLE`;
  and a title contains `[DR] UNMETERED_INCREMENT` — on this path the capture's ledger write also raises
  (`_emit_capture_ledger` → `record_capture_usage` → `_require_period` → the `capture_ledger` lane), so
  the second incident is expected.
- Keep `test_over_cap_refusal_is_observable_as_an_alert_incident` green.
- Add `test_cap_firing_files_with_the_sweep_disabled` — **pin the REAL builder, not the injected seam
  (routing it through `incidents` makes it GREEN even if `_cc._alert_store` is reverted to the gated
  body, voiding the regression guard).** Do NOT use the `incidents` fixture; **request
  `real_operator_alert_store` (the autouse `_operator_alert_isolation` would otherwise substitute
  `lambda: None` and the test could never pass on the correct tree)**: `monkeypatch.delenv(
  "BACKUP_SWEEP_ENABLED")`, set `DR_ISSUES_PAT`/`GH_REPO`/`TELEGRAM_*`, patch `ha._backup_storage` →
  `tortoise.hosted_backup.MemoryStorage` + `gi.create_issue`/`gi.search_open_incident`/
  `tp.send_message`, assert the
  precondition `ha._backup_config_safe() is None` **and** that `_cc.file_cohort_cost_incident(org, {})`
  reaches a FILED issue — mirroring `test_analytics_fallback_alert.py::
  test_t14_alert_channel_is_not_gated_on_the_backup_sweep`.

**Step:** run the files; then the mutation checks.

---

### Task 7: file the extra issues (no code; each must produce a live issue number verified in Task 8)

**Depends on:** —
- **PRE-EXISTING BUG (file, do not fix here — #3820 D5a class):** `hosted_api.backups_rebaseline`
  (`~:25084`) does `_alert_store_from(_backup_config_safe())`; `_backup_config_safe()` returns `None`
  whenever `BACKUP_SWEEP_ENABLED` is off (the production default), and `_alert_store_from` dereferences
  `cfg.gh_repo` (`:24536`) → `AttributeError` **after** the recovery writes (`:25080-25082`) but before
  **both** resolves (`:25085-25086`, `DATA_LOSS_CANDIDATE` and `SIZE_GUARD_ABORT`). The issue text must
  state three things accurately: (i) both kinds are blocked, not just `DATA_LOSS_CANDIDATE`; (ii) the
  reachable posture is a **lingering incident predating the disable** — the watcher does not start when
  `cfg is None` (`:1291`) and `run_backup_sweep`, the only `DATA_LOSS_CANDIDATE` producer, 503s without
  the sweep (`:24675`) — so do NOT claim the incident FIRES in the sweep-off posture; (iii) the writes
  at `:25080-25082` SUCCEED before the raise, so the operator sees a 500 for an action that actually
  re-baselined and whose only missing step is the resolve — intended fix: route through
  `_incident_alert_store()` and guard the resolves with `if alerts:`. Untested — `test_dr_endpoints.py`
  uses the sweep-on `dr_env`.
- `notify.file_incident` AND `notify.py:245-252` (the inline `abuse_suspended` block) are both
  sweep-gated (D5a-class) — file ONE issue scoped to **every raw `_backup_config_safe()` →
  `_alert_store_from` site**: `notify.py:245-252`, `hosted_api.py:1372`, `:24717`, `:25084`, `:25363`.
  (The abuse path rebuilds the chain rather than calling `file_incident`, so fixing the filer alone
  would not fix it.)
- The metering threshold-event docstring claims a non-existent alerting pipeline (`metering.py:66-69`).
- **Leg 2 has a live tracking artifact:** file an issue for "a dropped window-resolution increment is
  indistinguishable from a zero increment on the ledger" (the `_require_period` drop), citing that
  #3824 (CLOSED 2026-09-19) shipped the capture-cost `unattributed` counter
  (`hosted_api.py:22292`) — a different ledger/surface — and that four `metering.py` docstrings
  (`:349/475/691/858`) point at #3824 without a mechanism; correct those pointers in this PR or in the
  new issue.
- The read-path zero views (`metering.py:788`, `:1029`) are display-only and file no incident.
- **Reviewer #5 (advisory) recorded verdicts:** (a) *unify-contract-keep-drivers* — file an issue to
  declare the incident kinds once and assert runbook ⇄ code set equality. **The issue must state the
  durable-key hazard:** the kind string IS the R2 key (`ops/alerts/{kind}/{org}.json`, create-once /
  delete-to-resolve), so renaming a kind orphans its dedup object and a recurrence files a SECOND issue
  while the stale object is the only thing arming the long window. It must also enumerate the runbook
  gaps (`BILLING_SEND_FAILED`, `INVITE_SEND_FAILED`, and `abuse_suspended`, which
  violates the SCREAMING_CASE convention). The kinds live as constants in five modules and ~50 bare
  literals across seven — confirm the deferral (a refactor inside this PR can silently corrupt incident
  identity). (b) *keep separate with reason* — `notify.file_incident` stays a billing-scoped driver
  (unifying its gated channel is the D5a issue above); `operator_alert.file_operator_incident` is the
  generic filer with tri-state semantics. (c) The `search_open_incident` adoption path verifies only
  the org suffix (not the kind); accepted residual — the kind names are not substrings and the R2 dedup
  object is exact; file if the registry-kind work lands. (d) `file_operator_incident` re-implements the
  tri-state on-record rule that `_analytics_open_incident` (`hosted_api.py:22112-22150`) already owns:
  note in Task 1 that the two must move together, or extract the rule into one helper.
- `search_open_incident`'s org-suffix-only verification (above) becomes a real risk if any future kind
  pair prefix-collides — covered by the kind-registry issue.

---

### Task 8: full verification + mutation evidence

**Depends on:** all
Run one process per file (the known redislite interaction), backgrounded + polled:
`test_operator_alert.py`, `test_metering.py`, `test_metering_period_window.py`,
`test_metering_window_admission.py`, `test_cohort_cost_cap.py`, `test_alert_store.py`,
`test_analytics_fallback_alert.py`, `test_notify.py`. Record OAM1–OAM7 outcomes (see the appended
implementation record; the predecessor plan said "M1–M5", which was both short and colliding with the
M1–M4 already used inside `tests/test_metering_window_admission.py`) and every Task-7 issue number in
the PR body.

**PR body must state the #3981 closure posture explicitly:** this PR delivers leg 3 only; the ruling's
leg 2 (the increment is recorded as unmeterable on the ledger) is deferred to the new Task-7 child, so
use **`Refs #3981`**, not `Closes #3981` — `Closes` would close the issue with its representation leg
living only in a child. **Also update the two stale cross-references in the same PR:**
`backup_config.load_alert_config`'s docstring (the build chain gains `_incident_alert_store`), and
`_analytics_alert_store`'s D5a rationale (now owned by the new seam).

<!-- plan-review: cycles=5, status=capped, version=2.3.0 -->
<!-- plan-review cycle 5 (lane-owner-authorized, ONE pass, 2026-09-22): NOT CLEAN — 4 P1 survive
     (2 vacuous mutation tests, 2 unwired fallback lanes, unreachable kind constant). No fixes applied;
     stopped per the authorization bound. See operations/logs/cycle-status.yaml pass_5. -->

---

## Implementation record (round 2, 2026-09-22) — lane ruling (A), plan pass → implementation

The lane ruling (issue #3981 comment 5782137018, option **A**) converted this from a plan pass to an
implementation directive: **scope reduction REJECTED**, **no 6th plan pass**, go to implementation →
code review → PR. The surviving cycle-5 P1s were fixed here and each is proven by a **runnable RED
mutation**, not by prose.

### The 4 required P1 fixes

1. **OAM6 genuinely RED.** `test_the_admission_bound_survives_a_reap` now sets `_SWEEP_EVERY=1` and
   asserts `len(_HANDLES) == 0` as an explicit precondition before the N+1 dispatch. Observed RED with
   the gate reverted to `len(_HANDLES) >= _MAX_INFLIGHT` (`assert <Future …> is None` fails).
2. **OAM7 genuinely RED.** `test_lru_eviction_pins_move_to_end` fills to EXACTLY `_MAX_KEYS`, re-touches
   the oldest (asserting it moved to the tail), then inserts two more. Observed RED with the re-attempt
   path's `move_to_end` deleted (`re-touch must move_to_end` fails).
3. **Both remaining fallback lanes wired.** `mcp_server._alert_unmetered` and `ask_lane.run_ask_lane`
   step 7 now dispatch through `operator_alert.alert_unmetered_increment`, joining the `hosted_api`
   branch. The six-site coverage inventory (`test_the_six_swallow_sites_are_the_six_lanes`) holds on
   the partial-deploy case it was written for.
4. **The kind constant is reachable where it matters.** `UNMETERED_INCREMENT_KIND` is declared in
   `tortoise/operator_alert.py` (importable when `tortoise.metering` is not) and every site calls the
   shared `alert_unmetered_increment` entry point, so the guarded import can no longer swallow a
   `NameError` into a silent drop.
5. **Mutation-id collision resolved.** Ids are `OAM1..OAM7` — namespaced away from the `M1..M4` inside
   `tests/test_metering_window_admission.py`; Task 8's "M1–M5" is superseded above.

### Additional fixes taken here

* **P2 — `operator_alert.alert_store()` must not import `hosted_api`.** New light module
  `tortoise/alert_channel.py` owns the ONE policy and the ONE constructor; `hosted_api._incident_alert_store`
  is the hosted leg that injects its own (test-patched, cache-keyed) `_backup_config_safe` /
  `_backup_storage` factories, and the stdio leg calls it with no factories. Pinned by
  `test_the_light_leg_never_imports_the_hosted_app` (subprocess) and
  `test_both_legs_build_an_identical_channel` (parity).
* **P2 — `reset_operator_alert_state_for_tests` now clears `_RESERVED`, `_HANDLES` and `_SINCE_SWEEP`**
  as well as the two maps, so a leaked reservation cannot silently shrink a later test's budget.
* **P2 — runbook close order now matches `AlertStore.resolve_incident`** (close the issue FIRST, then
  delete the dedup object). The earlier "delete, then close" wording re-created the silent-loss window
  in the operator's hands.
* **P2 — on-record predicate parity pinned** over every `OpenOutcome` member
  (`test_on_record_predicate_parity`); extraction of a shared helper is filed (#4781).
* **P2 — kind constants ⇄ runbook rows pinned** (`test_kind_constants_match_the_runbook`).
* **P2 — recurrence guard for the raw `_backup_config_safe()/load_config() -> _alert_store_from` sites**
  (`test_no_new_raw_alert_channel_builder_sites`, AST-counted; declared residuals 4 + 2, filed #4778).
* **P2 — D5a retrofit audit has a named owner** (filed #4782, assigned).

### Task-7 issues filed

* **#4777** — pre-existing bug: `backups_rebaseline` builds its channel from the sweep-gated chain; BOTH
  resolves blocked, the reachable posture is a lingering predating incident, and the recovery writes
  SUCCEED before the raise.
* **#4778** — every raw builder site bypasses the ALERT-only policy (D5a class), with the recurrence guard.
* **#4779** — leg 2 of the ruling: a dropped window-resolution increment is indistinguishable from a zero
  increment on the ledger; four stale `metering.py` pointers to the closed #3824 to correct.
* **#4780** — declare the incident kinds once + assert runbook ⇄ code set equality, with the durable-key
  hazard (the kind string IS the R2 dedup key) and the runbook gaps (`BILLING_SEND_FAILED`,
  `INVITE_SEND_FAILED`, `abuse_suspended`).
* **#4781** — extract the on-record predicate into one helper.
* **#4782** — D5a retrofit audit of already-unalerted cap firings (owner assigned).

### Mutation evidence

`operations/logs/3981-mutation-evidence.log` — the mutation, the exact command and the observed pytest
failure for each of OAM1–OAM7, per the standing rule that a mutation test never observed RED is not
evidence.

### Implementation record (round 3 — rebase onto `origin/main`, review round 1 fixes)

The branch was 56 commits behind `origin/main` (`68a947248`) and written against a base that predated
it. Review round 1 found that the seam had been built against a **stale `_alert_store_from` contract**,
which is merge-blocking, so the branch was rebased onto main and adapted rather than left to conflict:

* **`_alert_store_from(cfg, writer=None)` keeps main's #3127/#2844 contract.** Main's version carries
  `writer` (defaulting to `WRITER_APP`) and builds with `issue_open=gi.issue_is_open_checked` /
  `default_writer=writer`; the pre-rebase seam had none of it, so `_alert_store_from(cfg,
  writer=WRITER_WATCHER)` (`hosted_api.py:1326`) would have lost the authority check after merge.
  `alert_channel.alert_store_from` now takes and forwards `writer`, and the delegate is signature-exact.
* **`operator_alert.alert_store()` PREFERS the hosted leg when `tortoise.hosted_api` is already
  imported.** The light leg builds a fresh `R2Storage` — and a fresh boto3 client — per call, which is
  right for the once-per-window alert this module dispatches but wrong for `cohort_cost._alert_store`,
  which asks on every cap-firing capture; routing it to the light leg re-created the #3968 cost. The
  light leg is still what answers on the MCP stdio path, where `hosted_api` was never imported (pinned
  by the subprocess test), and `alert_store` still never imports it.
* **`light_storage` enforces the Fly durability guard.** It re-implemented the
  `TORTOISE_BACKUP_STORAGE` seam without `hosted_api`'s `FLY_APP_NAME` refusal, so a Fly process
  reaching it via the light leg would have quietly accepted in-memory alert-dedup state — the #101
  loss class. Enforced per call there (stronger than the import-time guard, which the light leg can
  bypass by never importing `hosted_api`).
* **`_run` checks dispatch ownership BEFORE filing.** A superseded attempt no longer spends a network
  call on an incident its successor is already filing, nor reaches a store that may be tearing down;
  the post-write re-check stays, because a newer attempt can start while this one is in flight.
* **`alert_channel.reset_memory_storage_for_tests()`** is called by `_operator_alert_isolation`: the
  light leg's `MemoryStorage` singleton is process-wide, so a title filed by one test stayed
  "already filed" for the next — a latent DEDUP collision.
* Cycle-1 parity test de-vacuumed: it built the light leg via `oa.alert_store()`, which now resolves to
  the hosted leg, so it was comparing hosted-to-hosted. It builds `alert_channel.incident_alert_store()`
  explicitly. A new test pins the prefer-hosted rule itself.
* Docstring corrections: the cited test name, the stale `hosted_api.py` line range (replaced by the
  symbol `hosted_api._analytics_open_incident`, which cannot rot), and the claim that
  `hosted_api._incident_alert_store` and `operator_alert.alert_store` are interchangeable patch points.

OAM6/OAM7 were **re-verified RED** after the rebase (`operations/logs/3981-mutation-evidence.log`).

### Implementation record (round 4 — review cycle 2 fixes)

Cycle 2 found one regression the cycle-1 fix itself introduced, plus three coverage gaps:

* **P1 — the pre-check could silently drop an admitted alert.** `_prune_locked` swept
  `_INFLIGHT` unconditionally, with no successor required. A worker admitted before saturating the
  pool, queued past `_INFLIGHT_STALE_S` (120 s), then started, found its token gone with nobody to
  replace it, and returned *before* filing — no incident, no log. That is the exact silent-drop class
  this module exists to remove, reached under the storm it exists for. `_prune_locked` no longer
  touches `_INFLIGHT`.
* **P2 — the writer forwarding was unpinned.** Forcing `alert_store_from` to ignore `writer` passed
  179 tests, so the merge-blocking #3127/#2844 authority contract could be dropped again with green
  CI. `test_alert_store_from_forwards_the_writer` now pins both legs (default `WRITER_APP`, explicit
  `WRITER_WATCHER`).
* **P2 — the isolation reset covered only the light leg.** `hosted_api._MEMORY_BACKUP_STORE` is the
  singleton `_backup_storage` returns under `TORTOISE_BACKUP_STORAGE=memory`, so the DEDUP collision
  the fixture prevents was still reachable through the hosted builder. The fixture resets it too, but
  only when that module is already loaded (the fixture must not import the hosted app for every test),
  and `test_reset_clears_the_light_leg_dedup_store` pins that the reset is not a no-op.
* **P2 — the Fly refusal's effect was described wrongly.** `light_storage` raises, and
  `incident_alert_store` catches it, so on Fly the effect is "no channel plus a WARNING naming the
  reason" — not a surfaced failure. The docstring now says that, since claiming a refusal that the
  caller never sees is precisely the self-referential prose that re-stales. A follow-up covers the
  hosted branch's import-time-only check.

Four mutations observed RED (`operations/logs/3981-mutation-evidence.log`).

### Implementation record (round 5 — review cycle 3 fix: the shed ordering)

Cycle 3 showed round 4's fix was **incomplete**, and the mechanism was precise enough to pin:

* **P1 — the shed path could still drop an admitted alert.** `_due_locked` POPPED the latch and the
  *same* `alert_operator` call could then decide it was over `_MAX_INFLIGHT` and shed, installing
  **no** successor. The worker already admitted for that key then returned at `_run`'s ownership check
  without filing: the incident gone, with only a misleading "dispatch queue full" warning for the
  *repeat* call. The round-4 docstrings claiming the clear and the install were atomic were therefore
  false, which is itself the lesson — a claim about the mechanism belongs pinned by a test, not
  asserted in prose. The shed bound is now decided **before** `_due_locked` may pop a latch. A shed
  also no longer writes `_ATTEMPT`: a shed is not an attempt, and writing it made a later `_due_locked`
  read a window this call never consumed.
* **P2 — the two non-owner latch pops** (`store is None`, `_POOL.submit` failure) are token-guarded.
  `alert_store()` runs outside `_LOCK`, so a caller stalled past `_INFLIGHT_STALE_S` could otherwise
  pop a latch that a concurrent dispatch had just re-installed. Reservation release is unchanged.

`test_a_shed_never_clears_an_admitted_latch` pins the P1: it saturates the pool, ages the latch, and
re-dispatches the same key so the *re-dispatch* is the shed one — the only shape that exposes it. Its
store records only after its gate opens, so "reached the store" is a real signal, not "started". Red
under the reverted order (`operations/logs/3981-mutation-evidence.log`).

### Implementation record (round 6 — review cycle 4 fixes, no P0/P1)

Cycle 4 found no P0/P1: the cycle-3 fix held and its pin was load-bearing. Five P2s, all dispositioned:

* **Shed-log amplification (fixed).** With the bound decided first and `_ATTEMPT` no longer written on
  a shed, every same-key call under saturation re-logged — 50 calls produced 50 WARNINGs where the old
  order produced zero. The shed path runs on the caller's path and saturates exactly during a
  sweep-scale outage, so the alert mechanism would have amplified the storm it reports.
  `_SHED_LOG_INTERVAL_S = 60` (reset per test), and the suppressed count is deliberately not carried:
  the log budget must not grow with the outage either.
* **`_due_locked`'s stale-latch pop was coupled to constant ordering (fixed).** It popped the latch and
  could then return `False` (throttled), clearing it with no successor — unreachable only because
  `_INFLIGHT_STALE_S` (120) > `_RETRY_WINDOW_S` (60). The window is now checked FIRST and the pop
  happens only on the admitting path, so correctness no longer rests on that ordering — which a future
  tuning change could have silently broken. This also removes the `_run` docstring's dependence on it.
* **The queued-shape pin was overstated (fixed).** `test_a_shed_never_clears_an_admitted_latch` had the
  store record *before* blocking, so both workers were already past the ownership check and its final
  assertion passed under the mutation. It now wedges all four pool threads with blockers and queues the
  target, so the drop shape is real: under the reverted order the target's latch is gone and it never
  reaches the store. The store records only after its gate opens.
* **The two token guards were unpinned (fixed).** `test_a_stalled_resolver_does_not_clear_a_successors_latch`
  parks a caller in `alert_store()` past `_INFLIGHT_STALE_S`, admits a same-key successor, then lets the
  stalled caller return `None` — asserting the successor's latch survives. Without the guard
  `_INFLIGHT == {}` and the successor's incident is dropped.
* **The `_INFLIGHT` bound claim was wrong in the “paired with a reservation” direction (fixed).** A
  future cancelled before `_run` (shutdown's `cancel_futures=True`) is released by `_forget` while its
  latch waits out `_INFLIGHT_STALE_S`; the docstring now states the bound as `_RESERVED` plus those
  cancelled latches, and why that is shutdown/test-only.

Two mutations observed RED (`operations/logs/3981-mutation-evidence.log`).

### Implementation record (round 7 — review cycle 5 fix: the monotonic sentinel)

* **P1 — the shed-log rate limit could suppress every shed warning.** `_LAST_SHED_LOG = 0.0` was used as
  a "never logged" sentinel and compared against `time.monotonic()`, whose reference point is explicitly
  undefined. On a clock reporting < 60 s (a fresh boot, a per-process monotonic clock) `now - 0.0 <
  interval` is true, and because the early return does not advance the field, EVERY shed was suppressed
  until the clock passed the interval — a fail-open in the one signal this module exists to surface, and
  a deterministic failure of the tests that assert it. The sentinel is now `None` (with the reset
  matching), which is the shape the three constants around it should be read with in mind: a sentinel
  must never be a value the measured clock could legitimately report.
* **P2 — two docstrings asserted a universal the code does not hold.** `_run` and `_prune_locked` both
  claimed "a missing token means a successor WILL file". `_prune_locked` does not sweep `_INFLIGHT`.
* **P2 — the reset line was unpinned.** `test_reset_clears_every_piece_of_state` now sets
  `_LAST_SHED_LOG` and asserts the reset clears it; without the reset line it is RED
  (`operations/logs/3981-mutation-evidence.log`).
* **P2 — the queued-shape test's preconditions sat outside its `try`.** A precondition failure would
  leave four workers wedged on a 30 s gate, turning one clear assertion into a thread leak plus a
  misleading teardown error. The preconditions are now inside the `try` whose `finally` opens the gate.

### Implementation record (round 8 — review cycle 6 fixes)

Cycle 6 returned **not clean** on three P2s, and two of them were the same lesson this lane keeps
re-learning — a claim about a guarantee is the thing that re-stales:

* **The false universal survived in a third place.** `alert_operator`'s admission comment still said
  "a successor that WILL run" 60 lines from where the same sentence had just been corrected.
* **"Never lost without a trace" was itself falsified.** A queued alert is the future most likely to
  have been REAPED from `_HANDLES` (aged past `_INFLIGHT_STALE_S` behind wedged workers), so
  `_shutdown_pool`'s pending-handle count cannot see it and `cancel_futures=True` discarded it with no
  warning at all — the silent drop arriving through the shutdown door. `_forget` now logs the
  cancellation, which is the only place that can see it.
* **The cycle-5 P1 was unpinned.** Nothing exercised `_log_shed`, so reverting its sentinel handling was
  invisible on any host whose `time.monotonic()` exceeds 60 s — exactly the hosts CI uses. The new test
  passes `now` in, so the small-epoch case is deterministic everywhere.

Both new pins were verified RED (`operations/logs/3981-mutation-evidence.log`). 39 tests pass.

### Implementation record (round 9 — review cycle 7 fixes)

Cycle 7 found no P0/P1 and three P2s, the first of which was the same claim failing a third time:

* **"never lost without a trace" was DELETED, not qualified again.** It is false — a kind paused in
  `ops/suppression.json` returns `SUPPRESSED` with no line at all, by design — and it had already
  re-staled twice while I reworded it in place and re-deployed it at a new site. A self-referential
  claim about a guarantee does not improve with better narration; the fix is deletion.
* **The cancellation line no longer asserts a cause it cannot know.** `_forget` fires for pool
  shutdown, a test's cancelling pool, and `cancel_futures` alike, so "(pool shutdown)" was a guess
  printed as fact. It now states only what is observed, and the docstring says why the cause is
  unavailable here.
* **The cancellation pin now pins exclusivity.** It asserted only that some line appeared, so hoisting
  the warning out of the `cancelled()` guard — logging every settle — left the suite green. It now also
  asserts that a future which RAN stays silent (RED under exactly that mutation).

Three mutations observed RED (`operations/logs/3981-mutation-evidence.log`).

### Implementation record (round 10 — review cycle 8, deletion sweep)

Cycle 8 found the successor-*admission* half of the `_prune_locked` universal false in a real path:
`alert_operator` resolves the store OUTSIDE `_LOCK` and its two pre-pool failure paths pop the
caller's latch with no successor installed, so "a stale latch is cleared only by `_due_locked`" and
"clearing one always ADMITS a successor" both fail. The fix is deletion, not narrowing:

* `operator_alert.py` — deleted the universal/invariant clauses about latch clearing and admission:
  `_prune_locked` (the whole "cleared only by `_due_locked` … always ADMITS" body), `_due_locked`'s
  "ONLY on the admitting path" + ordering rationale, `_run`'s "a missing token means a successor was
  ADMITTED", `alert_operator`'s "only ever handed to a successor that is ADMITTED", `_forget`'s
  NEVER/exactly-one/only-place phrasing, `_reap_locked`/`_release_locked`/`_SWEEP_EVERY`'s
  counterfactual invariants, and the `alert_store`/`alert_unmetered_increment` over-claims. What
  remains states the observable fact of each path; the release sites are the `_RESERVED` enumeration.
* The plan doc's mechanism claims — the round-4/5/7/8/9 records and the Task-1 listing — carry the
  same deletions.

**Two P2s.**

* `config/ci-surfaces.yml`: `test_operator_alert.py` dual-registered under `surfaces.api` (it holds
  two uniquely-`hosted_api` pins, and an exact `api` match REPLACES the `core` fallback, so a
  `hosted_api.py`-only change did not run it).
* `tests/test_metering_window_admission.py`: the fence docstring's "Every operator alert is a
  `_alert_unmetered(...)` / `report_unmetered_increment(...)` call" was a false universal — the
  shared entry point `alert_unmetered_increment(lane, ...)` and `alert_operator(kind, ...)` match
  neither regex. The universal is deleted; the fence is described as scanning the lane tokens passed
  through the two lane-carrying helpers.

Mutation evidence for the earlier rounds is in `operations/logs/3981-mutation-evidence.log`; this
round is comment/docstring + registration only (AST-stripped digest equality).
