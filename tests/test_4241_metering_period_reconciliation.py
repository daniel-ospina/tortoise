"""#4241: the metering period-bound repair must have a PERIODIC caller.

The defect was not a wrong repair — `public.metering_repair_period_bounds()`
(#4216) is idempotent and bounded, and its own migration test covers it. The
defect was that it shipped with exactly ONE caller: the ``SELECT`` at the end
of its own migration. A subscription org whose anchor became unusable AFTER
that deploy was therefore never repaired, and stayed unmeterable (increments
dropped, the cohort cap unenforceable) until an unrelated
``customer.subscription.updated`` happened to supply both bounds.

That shapes what the tests here have to assert. A test that only *defined* the
caller, or that imported it and checked it existed, would have been green for
the entire life of the bug. So the assertions are on the CALL — and, for the
scheduling property, on the call arriving from the REAL runners
(`_run_boot_sweeps` and `_event_retention_loop`), not from a direct invocation.
"""
from __future__ import annotations

import asyncio
import contextlib

from tortoise import hosted_api as ha

# The function's ONLY ``RETURN NEXT`` emits an unusable-anchor reason
# (``supabase/migrations/20260919000001_metering_period_end_repair.sql:174``).
# A fabricated "we repaired this" row would assert against a shape the function
# cannot produce — and it is the same misreading the polarity pin below guards.
_UNUSABLE_REASON = (
    "subscription present but neither current_period_start nor "
    "current_period_end is stored")


class _FakeCP:
    """Minimal control plane that records RPC calls."""

    def __init__(self, rows=None, raises=None):
        self.calls: list[tuple[str, object]] = []
        self._rows = rows if rows is not None else []
        self._raises = raises

    def rpc(self, fn, body=None, *, representation=False):
        self.calls.append((fn, body))
        self.representation = representation
        if self._raises is not None:
            raise self._raises
        return self._rows


def _patch(monkeypatch, cp, *, enabled=True):
    from tortoise import supabase_control as sc

    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: enabled)
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)


def test_caller_is_a_noop_when_supabase_is_disabled(monkeypatch):
    """Registry/embedded mode must not reach for the control plane.

    Assert the NEGATIVE (no call), not the absence of a raise: the caller
    swallows every Exception, so a raise-based sentinel would be vacuous.
    """
    cp = _FakeCP()
    _patch(monkeypatch, cp, enabled=False)

    ha._reconcile_metering_periods()

    assert cp.calls == [], "registry mode must not fetch the control plane"


def test_caller_invokes_the_repair_rpc(monkeypatch):
    """The production call site must reach the repair function, by name."""
    cp = _FakeCP(rows=[{"org_id": "o1", "reason": _UNUSABLE_REASON}])
    _patch(monkeypatch, cp)

    ha._reconcile_metering_periods()

    assert [fn for fn, _body in cp.calls] == ["metering_repair_period_bounds"], (
        "the repair RPC was not called — this is the #4241 defect")
    # The function takes NO arguments; the body must be empty rather than a
    # guessed parameter, and `representation=True` is required because the
    # function RETURNS TABLE and the default `return=minimal` suppresses it
    # (#3665). NOTE what the retrieved body is: the orgs the repair FAILED to
    # fix — see the polarity pin below.
    assert cp.calls[0][1] == {}
    assert cp.representation is True


def test_an_unusable_anchor_is_reported_as_unrepaired_not_repaired(
        monkeypatch, caplog):
    """The returned rows are the orgs the repair could NOT fix.

    ``metering_repair_period_bounds`` derives the two missing bounds with plain
    ``UPDATE``s and emits a row ONLY from its final branch — the orgs whose
    anchor is unusable AND not derivable. ``…repair.sql:174`` is the function's
    ONLY ``RETURN NEXT``, and it ``RAISE WARNING``s that the org's increments
    are dropped and the cohort cap is unenforceable.

    So the body is the set the repair FAILED on. Reporting it as "repaired"
    inverts the meaning exactly when the news is worst — an operator reads
    "repaired 3 anchors" while three orgs sit unmeterable. This pins the
    polarity so it cannot silently invert again.
    """
    cp = _FakeCP(rows=[{"org_id": "org-unusable", "reason": _UNUSABLE_REASON}])
    _patch(monkeypatch, cp)

    with caplog.at_level("WARNING", logger="tortoise.hosted_api"):
        ha._reconcile_metering_periods()

    messages = [r.getMessage() for r in caplog.records]
    assert any("org-unusable" in m for m in messages), (
        "an org whose anchor could not be derived was not reported at all")
    assert any("could NOT derive" in m for m in messages), (
        "the unrepairable set must be reported as NOT derived")
    assert not any("repaired" in m for m in messages), (
        "the returned rows are the orgs the repair FAILED on — describing "
        "them as `repaired` reports the opposite of the truth")


def test_a_reconciliation_failure_is_reported_loudly(monkeypatch, caplog):
    """A broken reconciliation must not be silent (#4872 is the precedent).

    The whole class of bug this lane keeps finding is an enforcement-adjacent
    path whose failure is logged at `debug` and is therefore indistinguishable
    from "nothing to do". WARNING is the floor.
    """
    cp = _FakeCP(raises=RuntimeError("postgrest schema cache miss"))
    _patch(monkeypatch, cp)

    with caplog.at_level("WARNING", logger="tortoise.hosted_api"):
        ha._reconcile_metering_periods()  # must not raise

    assert any("metering period reconciliation failed" in r.getMessage()
               for r in caplog.records), (
        "a failed reconciliation was swallowed without a log record")


def test_the_repair_is_armed_by_the_real_boot_runner(monkeypatch):
    """Drive the REAL runner: the scheduling property, not the step.

    A direct call to `_reconcile_metering_periods()` cannot observe whether it
    is scheduled at all — which is exactly the bug. So this drives
    `_run_boot_sweeps` with the thread hop inlined and asserts the RPC fired.
    """
    cp = _FakeCP(rows=[])
    _patch(monkeypatch, cp)

    async def _inline(fn, *, name, timeout=None):  # no threads in this test
        return fn()

    monkeypatch.setattr(ha, "run_on_daemon_worker", _inline)
    # Neutralise the sibling sweeps: this test is about the metering step, and
    # the guard properties of the others are covered by
    # test_cost_allocation.py::_BOOT_SWEEPS.
    for attr in ("_sweep_events", "_purge_deleted_orgs",
                 "_purge_deleted_accounts", "_sweep_oauth_retention"):
        monkeypatch.setattr(ha, attr, lambda *a, **k: None)

    asyncio.run(ha._run_boot_sweeps())

    assert "metering_repair_period_bounds" in [fn for fn, _b in cp.calls], (
        "the boot runner did not arm the period-anchor repair — #4241 is that "
        "the repair had no caller")


def test_the_periodic_loop_also_arms_the_repair(monkeypatch):
    """The periodic leg matters more than boot: a long-lived process boots once.

    Boot alone would repair only at deploy time, which is the same one-shot
    shape as the migration SELECT that caused #4241. So assert the repair is
    reached from the loop as well.
    """
    cp = _FakeCP(rows=[])
    _patch(monkeypatch, cp)

    async def _inline(fn, *, name, timeout=None):  # no threads in this test
        return fn()

    monkeypatch.setattr(ha, "run_on_daemon_worker", _inline)
    for attr in ("_sweep_events", "_purge_deleted_orgs",
                 "_purge_deleted_accounts", "_sweep_oauth_retention",
                 "_refresh_cost_allocation"):
        monkeypatch.setattr(ha, attr, lambda *a, **k: None)

    async def _one_pass():
        task = asyncio.create_task(ha._event_retention_loop(0.0))
        # Let the loop reach its first guarded step, then stop it.
        for _ in range(50):
            await asyncio.sleep(0)
            if cp.calls:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_one_pass())

    assert "metering_repair_period_bounds" in [fn for fn, _b in cp.calls], (
        "the hourly retention loop did not arm the period-anchor repair — a "
        "boot-only repair is the same one-shot shape as the migration SELECT")
