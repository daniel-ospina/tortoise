"""#3981 — ``tortoise/operator_alert.py``: the absorbed-failure incident channel.

Every assertion on a dispatch calls ``oa.join_operator_alerts()`` first: the
filing happens on a pool thread, so an assertion without the join races it (and
is flaky under load, not merely slow).

MUTATION IDS
------------
The mutations proven RED here are ``OAM1..OAM7`` — namespaced because
``tests/test_metering_window_admission.py`` already numbers its own ``M1..M4``
and two different "M4"s in one PR body is a reading hazard, not a shorthand.
``OAM6`` (the admission bound) and ``OAM7`` (``move_to_end``) are the two whose
predecessor tests were VACUOUS; each is proven RED by actually running the
mutation, and the command + output is recorded in the PR.
"""
from __future__ import annotations

import concurrent.futures
import itertools
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import tortoise.operator_alert as oa
from tortoise.alert_store import AlertStore, OpenOutcome
from tortoise.hosted_backup import MemoryStorage

ROOT = Path(__file__).resolve().parents[1]
_KIND = oa.UNMETERED_INCREMENT_KIND


# ── fakes ───────────────────────────────────────────────────────────────────


class _Channels:
    """Fake GitHub + Telegram transport (the ``test_alert_store`` shape)."""

    def __init__(self):
        self.issues: dict[int, str] = {}
        self.closed: list[int] = []
        self.telegram: list[str] = []
        self._next = 1

    def file_issue(self, title, body):
        n = self._next
        self._next += 1
        self.issues[n] = title
        return n

    def close_issue(self, number, comment=None):
        self.closed.append(number)

    def search_open(self, kind, org_id=""):
        return [n for n, t in self.issues.items()
                if f"[DR] {kind}" in t and n not in self.closed]

    def push_telegram(self, text):
        self.telegram.append(text)


def _real_store():
    ch = _Channels()
    store = AlertStore(
        MemoryStorage(), file_issue=ch.file_issue, close_issue=ch.close_issue,
        search_open=ch.search_open, push_telegram=ch.push_telegram,
        repo="daniel-ospina/tortoise", assignee="daniel-ospina")
    return store, ch


class _RecordingStore:
    """Records every call; returns a fixed outcome (or raises)."""

    def __init__(self, outcome=OpenOutcome.FILED, raise_exc=None):
        self.outcome = outcome
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, str, dict]] = []

    def open_incident_state(self, kind, org_id="", detail=None):
        self.calls.append((kind, org_id, dict(detail or {})))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.outcome


class _GatedStore:
    """Blocks every attempt on *gate*, so workers stay wedged on demand."""

    def __init__(self, gate):
        self.gate = gate
        self.calls: list[tuple[str, str]] = []

    def open_incident_state(self, kind, org_id="", detail=None):
        self.calls.append((kind, org_id))
        self.gate.wait(timeout=30)
        return OpenOutcome.FILED


class _IdentStore:
    def __init__(self):
        self.idents: list[int] = []

    def open_incident_state(self, kind, org_id="", detail=None):
        self.idents.append(threading.get_ident())
        return OpenOutcome.FILED


class _FirstCallGatedStore:
    """Blocks ONLY the first attempt — for the stale-ownership test."""

    def __init__(self):
        self.calls = 0
        self.first_gate = threading.Event()

    def open_incident_state(self, kind, org_id="", detail=None):
        self.calls += 1
        if self.calls == 1:
            self.first_gate.wait(timeout=30)
        return OpenOutcome.FILED


class _FactStore:
    def __init__(self, fact):
        self.fact = fact

    def open_incident_state(self, kind, org_id="", detail=None):
        return self.fact

    def suppression_active(self, kind):
        return self.fact is OpenOutcome.SUPPRESSED


# ── the dispatch contract ───────────────────────────────────────────────────


def test_dispatch_files_an_incident(monkeypatch):
    store, ch = _real_store()
    monkeypatch.setattr(oa, "alert_store", lambda: store)

    handle = oa.alert_operator(_KIND, "org-x", {"lane": "write_op"})
    assert handle is not None
    assert oa.join_operator_alerts() == 0

    assert list(ch.issues.values()) == [f"[DR] {_KIND} — org-x"]
    assert ch.telegram, "the incident must also push, not only file"


def test_join_returns_zero_when_nothing_is_in_flight():
    assert oa.join_operator_alerts(timeout=0.1) == 0


def test_store_none_degrades_and_stays_throttled(monkeypatch):
    monkeypatch.setattr(oa, "alert_store", lambda: None)
    assert oa.alert_operator(_KIND, "org-none", {}) is None
    assert oa.join_operator_alerts() == 0
    # the provisional window is still armed, so the storm is still suppressed
    assert oa._ATTEMPT[(_KIND, "org-none")][1] == oa._RETRY_WINDOW_S
    with oa._LOCK:
        assert oa._RESERVED == 0
        assert (_KIND, "org-none") not in oa._INFLIGHT


def test_a_raising_store_never_raises(monkeypatch):
    store = _RecordingStore(raise_exc=RuntimeError("github down"))
    monkeypatch.setattr(oa, "alert_store", lambda: store)
    assert oa.alert_operator(_KIND, "org-raise", {}) is not None
    assert oa.join_operator_alerts() == 0
    assert len(store.calls) == 1


@pytest.mark.parametrize("outcome,expected", [
    (OpenOutcome.FILED, oa._ALERT_WINDOW_S),
    (OpenOutcome.DEDUP, oa._ALERT_WINDOW_S),
    (OpenOutcome.SUPPRESSED, oa._RETRY_WINDOW_S),
])
def test_on_record_arms_the_long_window(monkeypatch, outcome, expected):
    store = _RecordingStore(outcome=outcome)
    monkeypatch.setattr(oa, "alert_store", lambda: store)
    oa.alert_operator(_KIND, "org-window", {})
    assert oa.join_operator_alerts() == 0
    assert oa._ATTEMPT[(_KIND, "org-window")][1] == expected


def test_a_failed_attempt_arms_the_short_window(monkeypatch):
    store = _RecordingStore(raise_exc=RuntimeError("down"))
    monkeypatch.setattr(oa, "alert_store", lambda: store)
    oa.alert_operator(_KIND, "org-fail", {})
    assert oa.join_operator_alerts() == 0
    assert oa._ATTEMPT[(_KIND, "org-fail")][1] == oa._RETRY_WINDOW_S


def test_the_inflight_latch_self_heals(monkeypatch):
    monkeypatch.setattr(oa, "_INFLIGHT_STALE_S", 0.02)
    monkeypatch.setattr(oa, "_RETRY_WINDOW_S", 0.01)
    monkeypatch.setattr(oa, "_SWEEP_EVERY", 10_000)
    gate = threading.Event()
    store = _GatedStore(gate)
    monkeypatch.setattr(oa, "alert_store", lambda: store)

    assert oa.alert_operator(_KIND, "org-latch", {}) is not None
    now = time.monotonic()
    with oa._LOCK:
        assert not oa._due_locked((_KIND, "org-latch"), now), "wedged → latch closed"
    time.sleep(0.05)
    # a LATER attempt on the same key is admitted despite the wedged worker
    assert oa.alert_operator(_KIND, "org-latch", {}) is not None
    gate.set()
    oa.join_operator_alerts()


def test_a_stale_worker_cannot_own_the_state(monkeypatch):
    """OAM4 — dropping the token check in ``_run`` turns this RED.

    Ordering is load-bearing: attempt #1's worker must finish AFTER the current
    owner (#2) has written ``_ATTEMPT``, because the last writer wins under the
    mutation. Releasing #1 first would leave #2's value in place and read GREEN.
    """
    monkeypatch.setattr(oa, "_INFLIGHT_STALE_S", 0.05)
    monkeypatch.setattr(oa, "_RETRY_WINDOW_S", 0.05)
    store = _FirstCallGatedStore()
    monkeypatch.setattr(oa, "alert_store", lambda: store)

    stale = oa.alert_operator(_KIND, "org-own", {})
    assert stale is not None
    time.sleep(0.08)  # age the latch so #2 is admitted
    current = oa.alert_operator(_KIND, "org-own", {})
    assert current is not None
    assert current.result(timeout=30) is None

    owner_value = oa._ATTEMPT[(_KIND, "org-own")]
    store.first_gate.set()          # only NOW does the stale worker finish
    assert stale.result(timeout=30) is None
    assert oa._ATTEMPT[(_KIND, "org-own")] == owner_value, (
        "the stale worker wrote state it no longer owned")
    assert store.calls == 2


def test_alert_store_resolves_on_the_caller_thread(monkeypatch):
    """OAM3 — resolving inside the worker turns this RED.

    The RESOLUTION is what must happen on the caller thread; the store's own
    methods legitimately run on the worker. So the probe records the thread that
    calls ``alert_store()``, not the one that reaches the store.
    """
    resolved_on: list[int] = []

    def _factory():
        resolved_on.append(threading.get_ident())
        return _IdentStore()

    monkeypatch.setattr(oa, "alert_store", _factory)
    caller = threading.get_ident()
    oa.alert_operator(_KIND, "org-ident", {})
    assert oa.join_operator_alerts() == 0
    assert resolved_on == [caller]


def test_the_global_cap_sheds(monkeypatch, caplog):
    monkeypatch.setattr(oa, "_MAX_INFLIGHT", 2)
    gate = threading.Event()
    store = _GatedStore(gate)
    monkeypatch.setattr(oa, "alert_store", lambda: store)

    assert oa.alert_operator(_KIND, "org-a", {}) is not None
    assert oa.alert_operator(_KIND, "org-b", {}) is not None
    with caplog.at_level(logging.WARNING, logger="tortoise.operator_alert"):
        assert oa.alert_operator(_KIND, "org-c", {}) is None
    assert any("shed" in r.getMessage() for r in caplog.records)
    gate.set()
    oa.join_operator_alerts()


def test_the_admission_bound_survives_a_reap(monkeypatch, caplog):
    """OAM6 — the P1 target, and the test whose predecessor was VACUOUS.

    A ``sleep`` does not run ``_prune_locked``, so with the default
    ``_SWEEP_EVERY`` the reap never fires and the mutation reads GREEN (both the
    live-handle gate and the reservation gate admit). ``_SWEEP_EVERY=1`` makes
    the next dispatch run the sweep, and the reaped-handle precondition is
    asserted explicitly so the mutation cannot pass for the wrong reason.
    """
    monkeypatch.setattr(oa, "_MAX_INFLIGHT", 2)
    monkeypatch.setattr(oa, "_INFLIGHT_STALE_S", 0.02)
    monkeypatch.setattr(oa, "_RETRY_WINDOW_S", 0.01)
    monkeypatch.setattr(oa, "_SWEEP_EVERY", 1)
    monkeypatch.setattr(oa, "_SINCE_SWEEP", 0)
    gate = threading.Event()
    store = _GatedStore(gate)
    monkeypatch.setattr(oa, "alert_store", lambda: store)

    assert oa.alert_operator(_KIND, "org-1", {}) is not None
    assert oa.alert_operator(_KIND, "org-2", {}) is not None
    with oa._LOCK:
        assert oa._RESERVED == 2
    time.sleep(0.05)
    with oa._LOCK:
        oa._prune_locked(time.monotonic())
        assert len(oa._HANDLES) == 0, "precondition: the reap dropped the handles"
        assert oa._RESERVED == 2, "the reservation is NOT reaped with the handle"

    with caplog.at_level(logging.WARNING, logger="tortoise.operator_alert"):
        assert oa.alert_operator(_KIND, "org-new", {}) is None, (
            "the bound must shed: 2 wedged workers still hold pool threads "
            "though their handles were reaped")
    assert any("shed" in r.getMessage() for r in caplog.records), [
        r.getMessage() for r in caplog.records]
    assert store.calls == [(_KIND, "org-1"), (_KIND, "org-2")], (
        "only the two admitted (wedged) dispatches may reach the store")

    gate.set()
    deadline = time.monotonic() + 30.0
    while oa._RESERVED and time.monotonic() < deadline:
        time.sleep(0.01)
    assert oa._RESERVED == 0, (
        "join cannot speak for a reaped worker — poll the reservation instead")


def test_reservation_released_on_worker_completion(monkeypatch):
    store, _ch = _real_store()
    monkeypatch.setattr(oa, "alert_store", lambda: store)
    assert oa.alert_operator(_KIND, "org-c1", {}) is not None
    assert oa.join_operator_alerts(timeout=30.0) == 0
    with oa._LOCK:
        assert oa._RESERVED == 0


def test_reservation_released_when_the_store_is_none(monkeypatch):
    monkeypatch.setattr(oa, "alert_store", lambda: None)
    assert oa.alert_operator(_KIND, "org-c2", {}) is None
    with oa._LOCK:
        assert oa._RESERVED == 0


def test_reservation_released_when_submit_fails(monkeypatch):
    class _DeadPool:
        def submit(self, *a, **k):
            raise RuntimeError("pool shut down")

    store, _ch = _real_store()
    monkeypatch.setattr(oa, "alert_store", lambda: store)
    monkeypatch.setattr(oa, "_POOL", _DeadPool())
    assert oa.alert_operator(_KIND, "org-c3", {}) is None
    with oa._LOCK:
        assert oa._RESERVED == 0
        assert (_KIND, "org-c3") not in oa._INFLIGHT


def test_reservation_released_when_a_queued_future_is_cancelled(monkeypatch):
    import concurrent.futures

    class _CancellingPool:
        def submit(self, *a, **k):
            fut: concurrent.futures.Future = concurrent.futures.Future()
            fut.cancel()
            return fut

    store, _ch = _real_store()
    monkeypatch.setattr(oa, "alert_store", lambda: store)
    monkeypatch.setattr(oa, "_POOL", _CancellingPool())
    assert oa.alert_operator(_KIND, "org-c4", {}) is not None
    with oa._LOCK:
        assert oa._RESERVED == 0, "a cancelled future never enters _run"
        assert not oa._HANDLES


def test_lru_eviction_pins_move_to_end(monkeypatch):
    """OAM7 — deleting ``_ATTEMPT.move_to_end`` on the re-attempt path REDs this.

    The predecessor test inserted ``_MAX_KEYS + 2`` keys and then re-dispatched
    the FIRST: the insert-time eviction had already removed it, and the
    re-dispatch re-INSERTED it at the tail, so ``move_to_end`` was unobservable.
    Here the map is filled to exactly the cap, the oldest is re-touched, and only
    THEN are two more keys inserted — the eviction runs at the START of a
    dispatch, so the count must exceed the cap before the following insert.
    Workers are Event-gated so the completion path in ``_run`` cannot mask the
    mutation by refreshing the key itself.
    """
    monkeypatch.setattr(oa, "_MAX_KEYS", 4)
    monkeypatch.setattr(oa, "_PRUNE_ABOVE", 1)
    monkeypatch.setattr(oa, "_SWEEP_EVERY", 1)
    monkeypatch.setattr(oa, "_SINCE_SWEEP", 0)
    monkeypatch.setattr(oa, "_RETRY_WINDOW_S", 10_000.0)
    monkeypatch.setattr(oa, "_due_locked", lambda key, now: True)
    gate = threading.Event()
    store = _GatedStore(gate)
    monkeypatch.setattr(oa, "alert_store", lambda: store)

    for i in range(4):
        assert oa.alert_operator(_KIND, f"org-{i}", {}) is not None
    assert list(oa._ATTEMPT) == [(_KIND, f"org-{i}") for i in range(4)]

    assert oa.alert_operator(_KIND, "org-0", {}) is not None
    assert list(oa._ATTEMPT)[-1] == (_KIND, "org-0"), "re-touch must move_to_end"

    oa.alert_operator(_KIND, "org-4", {})
    oa.alert_operator(_KIND, "org-5", {})

    assert (_KIND, "org-0") in oa._ATTEMPT, (
        "the re-touched key must survive eviction")
    assert (_KIND, "org-1") not in oa._ATTEMPT, (
        "the untouched oldest key must be the one evicted")
    gate.set()
    oa.join_operator_alerts()


def test_a_cancelled_dispatch_is_never_silent(caplog):
    """Pin the cancellation warning in ``_forget``.

    A queued alert is the future most likely to have been REAPED from ``_HANDLES``
    (aged past ``_INFLIGHT_STALE_S`` behind wedged workers), so ``_shutdown_pool``'s
    pending-handle count cannot see it: without this line, ``cancel_futures=True``
    discards it with no warning at all — the silent drop this module exists to remove,
    arriving through the shutdown door.
    """
    fut = concurrent.futures.Future()
    fut.cancel()
    ran = concurrent.futures.Future()
    ran.set_result(None)
    with caplog.at_level(logging.WARNING, logger="tortoise.operator_alert"):
        oa._forget(fut)
        after_cancel = len(caplog.records)
        oa._forget(ran)
    assert after_cancel == 1, "a cancelled dispatch must leave exactly one line"
    assert len(caplog.records) == after_cancel, (
        "a future that RAN must stay silent — logging every settle is not a report")
    assert "cancelled" in caplog.records[0].getMessage()


def test_the_shed_log_is_rate_limited_and_clock_agnostic(caplog):
    """Pin `_log_shed`: `None` = never logged, then one line per interval.

    The sentinel must not be a value the clock can legitimately report. `now` is
    passed in, so the small-epoch case (a fresh boot, a per-process monotonic clock)
    is DETERMINISTIC here — and it is invisible on a CI box whose monotonic clock
    exceeds the interval, which is why this test must not lean on the wall clock: with
    a `0.0` sentinel, `_log_shed(5.0)` is suppressed on exactly such a host while every
    other test stays green.
    """
    oa.reset_operator_alert_state_for_tests()
    with caplog.at_level(logging.WARNING, logger="tortoise.operator_alert"):
        oa._log_shed(5.0, _KIND)
        assert len(caplog.records) == 1, (
            "a small-epoch clock must not suppress the shed warning")
        oa._log_shed(10.0, _KIND)
        assert len(caplog.records) == 1, "inside the interval -> suppressed"
        oa._log_shed(5.0 + oa._SHED_LOG_INTERVAL_S, _KIND)
        assert len(caplog.records) == 2, "the interval elapsed -> logs again"


def test_reset_clears_every_piece_of_state(monkeypatch):
    monkeypatch.setattr(oa, "_RESERVED", 3)
    monkeypatch.setattr(oa, "_SINCE_SWEEP", 7)
    monkeypatch.setattr(oa, "_LAST_SHED_LOG", 123.0)
    with oa._LOCK:
        oa._HANDLES[object()] = 1.0
        oa._ATTEMPT[("K", "o")] = (0.0, 1.0)
        oa._INFLIGHT[("K", "o")] = 0.0
    oa.reset_operator_alert_state_for_tests()
    assert oa._RESERVED == 0
    assert oa._SINCE_SWEEP == 0
    assert oa._HANDLES == {}
    assert oa._ATTEMPT == {}
    assert oa._INFLIGHT == {}
    assert oa._LAST_SHED_LOG is None, (
        "the shed-log rate limit must reset, or one shedding test silences the rest")


# ── the channel seam ────────────────────────────────────────────────────────


def test_real_builder_is_not_gated_on_the_sweep(monkeypatch,
                                                real_operator_alert_store):
    monkeypatch.delenv("BACKUP_SWEEP_ENABLED", raising=False)
    monkeypatch.setenv("DR_ISSUES_PAT", "pat-test-only")
    monkeypatch.setenv("GH_REPO", "daniel-ospina/tortoise")
    monkeypatch.setenv("TORTOISE_BACKUP_STORAGE", "memory")
    assert oa.alert_store() is not None, "D5a: the sweep switch is not the gate"


def test_real_builder_is_none_without_a_pat(monkeypatch,
                                            real_operator_alert_store):
    monkeypatch.delenv("BACKUP_SWEEP_ENABLED", raising=False)
    monkeypatch.delenv("DR_ISSUES_PAT", raising=False)
    monkeypatch.setenv("TORTOISE_BACKUP_STORAGE", "memory")
    assert oa.alert_store() is None


def test_real_builder_is_none_without_a_usable_object_store(
        monkeypatch, real_operator_alert_store):
    """OAM5 — a dead-wired builder returns ``None`` here and REDs.

    Deliberately does NOT patch the object store: with a ``MemoryStorage``
    substitute installed the ``R2_*`` env is never consulted and the assertion
    could not fail.
    """
    monkeypatch.delenv("BACKUP_SWEEP_ENABLED", raising=False)
    monkeypatch.setenv("DR_ISSUES_PAT", "pat-test-only")
    monkeypatch.setenv("GH_REPO", "daniel-ospina/tortoise")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.delenv("TORTOISE_BACKUP_STORAGE", raising=False)
    for var in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                "R2_BUCKET"):
        monkeypatch.delenv(var, raising=False)
    assert oa.alert_store() is None, (
        "no usable object store means no dedup seam, so no channel")


def test_the_light_leg_never_imports_the_hosted_app():
    """The stdio path must not build FastAPI for a bookkeeping alert."""
    code = (
        "import os, sys\n"
        "os.environ.pop('DR_ISSUES_PAT', None)\n"
        "os.environ['TORTOISE_BACKUP_STORAGE'] = 'memory'\n"
        "import tortoise.operator_alert as oa\n"
        "oa.alert_store()\n"
        "assert 'tortoise.hosted_api' not in sys.modules, 'hosted_api imported'\n"
        "print('LIGHT-OK')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                         capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stderr
    assert "LIGHT-OK" in out.stdout


def test_both_legs_build_an_identical_channel(monkeypatch):
    """Parity: the light leg and the hosted leg file the SAME incident.

    Without this the two legs could drift (the split this PR removes), and the
    seam-map claim would rest on the diff alone. The light leg is built
    EXPLICITLY: ``oa.alert_store()`` cannot stand in for it here, because it
    prefers the hosted leg whenever ``tortoise.hosted_api`` is imported — as it
    is in this process.
    """
    from tortoise import alert_channel
    from tortoise import github_issue as gi
    from tortoise import hosted_api as ha
    from tortoise import telegram_push as tp

    monkeypatch.setenv("DR_ISSUES_PAT", "pat-test-only")
    monkeypatch.setenv("GH_REPO", "daniel-ospina/tortoise")
    monkeypatch.setenv("TORTOISE_BACKUP_STORAGE", "memory")
    monkeypatch.delenv("BACKUP_SWEEP_ENABLED", raising=False)
    # separate dedup stores, so the two legs cannot dedup against each other
    monkeypatch.setattr(ha, "_backup_storage", lambda: MemoryStorage())
    alert_channel.reset_memory_storage_for_tests()
    titles: list[str] = []
    monkeypatch.setattr(
        gi, "create_issue",
        lambda repo, pat, title, body, assignee=None: (
            titles.append(title) or len(titles)))
    monkeypatch.setattr(gi, "search_open_incident", lambda *a, **k: [])
    monkeypatch.setattr(tp, "send_message", lambda *a, **k: None)

    light = alert_channel.incident_alert_store()
    hosted = ha._incident_alert_store()
    assert light is not None and hosted is not None
    assert type(light) is type(hosted)
    assert light._repo == hosted._repo == "daniel-ospina/tortoise"

    light.open_incident_state(_KIND, "org-parity", {})
    hosted.open_incident_state(_KIND, "org-parity", {})
    assert titles == [f"[DR] {_KIND} — org-parity"] * 2, titles


def test_analytics_store_delegates_to_the_incident_seam(
        monkeypatch, real_analytics_alert_store):
    import tortoise.hosted_api as ha

    sentinel = object()
    monkeypatch.setattr(ha, "_incident_alert_store", lambda: sentinel)
    assert ha._analytics_alert_store() is sentinel


def test_cohort_cost_delegates_to_the_operator_store(monkeypatch):
    from tortoise import cohort_cost as cc
    sentinel = object()
    monkeypatch.setattr(oa, "alert_store", lambda: sentinel)
    assert cc._alert_store() is sentinel


def test_operator_store_prefers_the_hosted_leg_when_it_is_loaded(
        monkeypatch, real_operator_alert_store):
    """The hosted leg is the one holding the cached storage (#3968).

    ``cohort_cost._alert_store`` asks this per cap-firing capture, NOT once per
    window. Resolving it to the light leg would build a fresh ``R2Storage`` — and
    therefore a fresh boto3 client — on every call, re-creating exactly the
    #3968 cost the process-wide singleton exists to remove. The light leg stays
    the answer only where ``tortoise.hosted_api`` was never imported.
    """
    from tortoise import hosted_api as ha

    assert "tortoise.hosted_api" in sys.modules, (
        "precondition: this process has the hosted app loaded")
    sentinel = object()
    monkeypatch.setattr(ha, "_incident_alert_store", lambda: sentinel)
    assert oa.alert_store() is sentinel


def test_prune_never_sweeps_an_unsettled_latch(monkeypatch):
    """Pin: ``_prune_locked`` must NOT clear ``_INFLIGHT``.

    A latch swept with no successor lets a queued worker — admitted before, sat
    behind saturated workers past ``_INFLIGHT_STALE_S`` — start, find its token
    gone with nobody to replace it, and return WITHOUT filing: the silent drop
    this module exists to remove. ``_run``'s pre-check is only safe while a
    latch can be cleared by a successor (``_due_locked``) alone.
    """
    monkeypatch.setattr(oa, "_INFLIGHT_STALE_S", 0.02)
    gate = threading.Event()
    monkeypatch.setattr(oa, "alert_store", lambda: _GatedStore(gate))
    assert oa.alert_operator(_KIND, "org-latch", {}) is not None
    try:
        time.sleep(0.05)
        with oa._LOCK:
            oa._prune_locked(time.monotonic())
            assert oa._INFLIGHT.get((_KIND, "org-latch")) is not None, (
                "the sweep must leave the latch to the successor")
    finally:
        gate.set()
        oa.join_operator_alerts()


def test_a_shed_never_clears_an_admitted_latch(monkeypatch):
    """Pin: the shed bound must be decided BEFORE ``_due_locked`` self-heals.

    The production drop shape is a QUEUED worker, not a running one: all four pool
    threads are blocked, the target sits in the queue, and a same-key re-dispatch —
    for which ``_due_locked`` may self-heal the stale latch — is then SHED. If the
    bound were decided after that self-heal, the latch would be gone with no successor
    installed and the queued worker would return at ``_run``'s ownership check without
    ever reaching the store. The store records only AFTER its gate opens, so "recorded"
    means the worker actually got past that check.
    """

    class _BlockThenRecord:
        def __init__(self, ev):
            self.ev = ev
            self.calls: list[tuple[str, str]] = []
            self.entered = threading.Semaphore(0)

        def open_incident_state(self, kind, org_id="", detail=None):
            self.entered.release()
            self.ev.wait(timeout=30)
            self.calls.append((kind, org_id))
            return OpenOutcome.FILED

    # 4 pool threads, bound 5: four blockers wedge every thread, so the target key is
    # genuinely QUEUED, and the next dispatch is the shed one.
    monkeypatch.setattr(oa, "_MAX_INFLIGHT", 5)
    monkeypatch.setattr(oa, "_INFLIGHT_STALE_S", 0.02)
    monkeypatch.setattr(oa, "_RETRY_WINDOW_S", 0.01)
    gate = threading.Event()
    store = _BlockThenRecord(gate)
    monkeypatch.setattr(oa, "alert_store", lambda: store)

    try:
        for i in range(4):
            assert oa.alert_operator(_KIND, f"org-blk{i}", {}) is not None
        for _ in range(4):
            assert store.entered.acquire(timeout=10), (
                "precondition: all 4 pool threads are inside the store")
        assert oa.alert_operator(_KIND, "org-q", {}) is not None  # QUEUED behind them
        time.sleep(0.05)                      # age org-q's latch past the bound
        with oa._LOCK:
            assert oa._RESERVED == 5, "precondition: the bound is saturated"
        assert oa.alert_operator(_KIND, "org-q", {}) is None, (
            "precondition: the same-key re-dispatch is the shed one")
        with oa._LOCK:
            assert oa._INFLIGHT.get((_KIND, "org-q")) is not None, (
                "a shed must not clear a QUEUED worker's latch")
    finally:
        gate.set()
        oa.join_operator_alerts()
    assert (_KIND, "org-q") in store.calls, (
        "the queued worker must still file its incident")


def test_a_stalled_resolver_does_not_clear_a_successors_latch(monkeypatch):
    """Pin the token guard on the ``store is None`` path.

    ``alert_store()`` runs OUTSIDE ``_LOCK``. A caller that stalls there past
    ``_INFLIGHT_STALE_S`` must not pop the latch a concurrent same-key dispatch has
    since re-installed — popping it drops the successor's incident with nobody left to
    file, because the stalled caller has no store. Depends on the
    ``if _INFLIGHT.get(key) == token`` guard; without it the latch is cleared.
    """
    monkeypatch.setattr(oa, "_MAX_INFLIGHT", 8)
    monkeypatch.setattr(oa, "_INFLIGHT_STALE_S", 0.02)
    monkeypatch.setattr(oa, "_RETRY_WINDOW_S", 0.01)
    stall, hold, entered = threading.Event(), threading.Event(), threading.Event()
    nth = itertools.count(1)
    store = _GatedStore(hold)

    def _resolver():
        if next(nth) == 1:
            entered.set()
            stall.wait(timeout=30)      # parks the FIRST caller, outside _LOCK
            return None
        return store

    monkeypatch.setattr(oa, "alert_store", _resolver)
    first = threading.Thread(target=oa.alert_operator, args=(_KIND, "org-s", {}))
    first.start()
    try:
        assert entered.wait(timeout=10), (
            "precondition: the first caller is parked in the resolver")
        time.sleep(0.05)                # age the first caller's latch
        assert oa.alert_operator(_KIND, "org-s", {}) is not None, (
            "precondition: a successor is admitted")
        with oa._LOCK:
            succ = oa._INFLIGHT.get((_KIND, "org-s"))
        assert succ is not None, "precondition: the successor installed its latch"
        stall.set()
        first.join(timeout=10)
        assert not first.is_alive()
        with oa._LOCK:
            assert oa._INFLIGHT.get((_KIND, "org-s")) == succ, (
                "a stalled resolver must not clear the successor's latch")
    finally:
        stall.set()
        hold.set()
        oa.join_operator_alerts()


def test_reset_clears_the_light_leg_dedup_store(monkeypatch):
    """Pin: the autouse reset must actually drop the singleton.

    Without it a title filed by one test stays "already filed" for the next — a
    latent DEDUP collision, and an order-dependent suite.
    """
    from tortoise import alert_channel

    monkeypatch.setenv("TORTOISE_BACKUP_STORAGE", "memory")
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    first = alert_channel.light_storage()
    assert alert_channel.light_storage() is first, (
        "precondition: it is a process-wide singleton")
    alert_channel.reset_memory_storage_for_tests()
    assert alert_channel.light_storage() is not first, (
        "the reset must drop the singleton, or dedup state leaks across tests")


def test_alert_store_from_forwards_the_writer(monkeypatch):
    """Pin main's #3127/#2844 authority contract across the delegate.

    ``hosted_api.py:1326`` builds the watcher's store with
    ``_alert_store_from(cfg, writer=WRITER_WATCHER)``. If the delegate drops
    ``writer`` the whole watcher path acts as the app, the KIND_OWNERS check
    goes inert, and NOTHING else in the suite notices — hence this pin.
    """
    import types

    from tortoise import alert_channel
    from tortoise import hosted_api as ha
    from tortoise.alert_store import WRITER_APP, WRITER_WATCHER

    monkeypatch.setattr(ha, "_backup_storage", lambda: MemoryStorage())
    cfg = types.SimpleNamespace(
        gh_repo="daniel-ospina/tortoise", github_issues_pat="p",
        alert_assignee=None, telegram_bot_token="t", telegram_chat_id="c")

    assert ha._alert_store_from(cfg)._writer == WRITER_APP
    assert ha._alert_store_from(
        cfg, writer=WRITER_WATCHER)._writer == WRITER_WATCHER
    assert alert_channel.alert_store_from(
        cfg, storage_factory=lambda: MemoryStorage())._writer == WRITER_APP
    assert alert_channel.alert_store_from(
        cfg, storage_factory=lambda: MemoryStorage(),
        writer=WRITER_WATCHER)._writer == WRITER_WATCHER


def test_the_autouse_isolation_is_load_bearing(monkeypatch):
    """T18 shape: the second assertion is what REDs if the fixture is deleted.

    A bare ``is None`` would pass on CI (no creds), so the box is first shown to
    be ABLE to build a real store.
    """
    from tortoise import hosted_api as ha

    monkeypatch.setenv("DR_ISSUES_PAT", "pat-test-only")
    monkeypatch.setenv("GH_REPO", "daniel-ospina/tortoise")
    monkeypatch.setenv("TORTOISE_BACKUP_STORAGE", "memory")
    monkeypatch.delenv("BACKUP_SWEEP_ENABLED", raising=False)
    monkeypatch.setattr(ha, "_backup_storage", lambda: MemoryStorage())
    assert ha._incident_alert_store() is not None, (
        "precondition: this box COULD build a real channel")
    assert oa.alert_store() is None, (
        "the autouse isolation must still return None here")


def test_on_record_predicate_parity(monkeypatch):
    """The on-record rule must match ``hosted_api._analytics_open_incident``.

    Both implement "``outcome in {FILED, DEDUP}``, i.e. ``is not SUPPRESSED``"
    and must move together; a divergence on a fourth ``OpenOutcome`` member is
    the #3820 duplicate-rule defect re-created.
    """
    import tortoise.hosted_api as ha

    for fact in OpenOutcome:
        expected = fact is not OpenOutcome.SUPPRESSED
        assert oa.file_operator_incident(_FactStore(fact), "K", "o", {}) is expected
        monkeypatch.setattr(ha, "_analytics_alert_store",
                            lambda f=fact: _FactStore(f))
        assert ha._analytics_open_incident("fallback", None) is expected


#: The DECLARED residual raw builders — ``_backup_config_safe()`` /
#: ``load_config()`` -> ``_alert_store_from`` — that bypass the ALERT-only
#: policy in ``alert_channel.incident_alert_store`` (D5a class). Each is a
#: follow-up issue, not a licence for a seventh: a NEW site must route through
#: ``_incident_alert_store`` instead. Counted by AST call node, so a docstring
#: mention (``hosted_api._incident_alert_store``'s seam map has one) is not
#: mistaken for a call site.
_DECLARED_RAW_BUILDER_SITES = {"hosted_api.py": 4, "notify.py": 2}


def _raw_builder_call_sites() -> dict[str, int]:
    import ast

    sites: dict[str, int] = {}
    for path in sorted((ROOT / "tortoise").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        n = sum(
            1 for node in ast.walk(tree)
            if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name)
                 and node.func.id == "_alert_store_from")
                or (isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_alert_store_from")))
        if n:
            sites[path.name] = n
    return sites


def test_no_new_raw_alert_channel_builder_sites():
    """Recurrence guard for the D5a class this PR partly removes.

    The raw chain builds the channel WITHOUT the ALERT-only policy, so a site
    on it is silently sweep-gated. The sites below are the declared, filed
    residuals; a new one turns this RED, which is the point — the fix is to
    route it through the seam, not to bump the number here.
    """
    assert _raw_builder_call_sites() == _DECLARED_RAW_BUILDER_SITES, (
        "a raw alert-channel builder site was added or removed; the declared "
        "residual set is " + repr(_DECLARED_RAW_BUILDER_SITES))


def test_kind_constants_match_the_runbook():
    """Each new kind has a runbook row naming the manual-close mechanism."""
    from tortoise import cohort_cost as cc

    runbook = (ROOT / "docs/ops/registry-backup-dr.md").read_text(encoding="utf-8")
    rows = {ln.split("|")[1].strip(): ln
            for ln in runbook.splitlines() if ln.startswith("| ")
            and ln.count("|") >= 3}
    kinds = (oa.UNMETERED_INCREMENT_KIND, cc.UNENFORCEABLE_INCIDENT_KIND,
             cc.INCIDENT_KIND, oa.BILLING_NOTIFY_REFUSED_KIND)
    for kind in kinds:
        assert kind in rows, f"no runbook triage row for {kind}"
        row = rows[kind]
        assert "close the GitHub issue" in row and "then delete" in row, (
            f"the {kind} row must name the close-then-delete procedure: {row}")
    assert cc.UNENFORCEABLE_INCIDENT_KIND != cc.INCIDENT_KIND
    assert cc.INCIDENT_KIND not in cc.UNENFORCEABLE_INCIDENT_KIND
    assert cc.UNENFORCEABLE_INCIDENT_KIND not in cc.INCIDENT_KIND


def test_alert_unmetered_increment_helper_dispatches(monkeypatch):
    store = _RecordingStore()
    monkeypatch.setattr(oa, "alert_store", lambda: store)
    oa.alert_unmetered_increment("write_op", "org-h", ValueError("x"))
    assert oa.join_operator_alerts() == 0
    assert store.calls == [(_KIND, "org-h",
                            {"lane": "write_op", "error_type": "ValueError"})]


# ── BILLING_NOTIFY_REFUSED: the platform-scoped subject (#4456) ─────────────


def test_billing_notify_refused_incident_is_platform_scoped(monkeypatch):
    """#4456 plan: a refused billing notify files ONE incident per OUTAGE.

    ONE Resend account serves every team, and the shared telemetry pool makes
    a saturation CROSS-TENANT — a refusal per billing webhook for every tenant
    — so a per-org key would file N issues for one outage (the same reason
    ``notify.py`` passes ``""`` for a failed billing send). Dedup is
    ``(kind, subject)``: every org folds onto ``(kind, "")`` and the affected
    org travels in the detail.
    """
    store = _RecordingStore()
    monkeypatch.setattr(oa, "alert_store", lambda: store)
    oa.reset_operator_alert_state_for_tests()
    try:
        oa.alert_billing_notify_refused("org-a", "checkout.session.completed")
        oa.alert_billing_notify_refused(
            "org-b", "customer.subscription.updated")
        assert oa.join_operator_alerts() == 0
        keys = [k for k in oa._ATTEMPT
                if k[0] == oa.BILLING_NOTIFY_REFUSED_KIND]
        assert keys == [(oa.BILLING_NOTIFY_REFUSED_KIND, "")], (
            f"refusal incidents were keyed per org ({keys}) — one saturation "
            "would file one issue per tenant instead of one for the outage "
            "(#4456)"
        )
    finally:
        oa.reset_operator_alert_state_for_tests()
    assert store.calls == [(oa.BILLING_NOTIFY_REFUSED_KIND, "", {
        "op": "billing_notify",
        "event_type": "checkout.session.completed",
        "org_id": "org-a"})], store.calls
