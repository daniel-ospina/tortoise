"""#1890: the terminal-state invite-ghost sweep must have a LIVE caller.

``TortoiseSDK.sweep_invite_ghost_memberships`` (#1908) was correct and tested —
and reachable only from ``graph-scripts/backfill_invite_ghost_members.py``,
which an operator runs by hand. So an invite that expired by the clock AFTER
that one-time backfill kept its ghost ``invite-{iid}`` placeholder in
``list_members`` forever, and the "currently caller-less" method the issue
names stayed caller-less. This is the #4241 shape: the repair was never wrong,
it just never ran.

That shapes what these tests assert. A test that only *defined* the caller, or
imported it and checked it existed, would have been green for the whole life of
the bug — so the scheduling assertions drive the REAL runners
(``_run_boot_sweeps`` and ``_event_retention_loop``) and assert the SDK call
arrived from them.

Registry-only is also a contract, not an optimisation: Supabase mode must never
construct the registry graph (#669), so the negative is asserted on
``_make_sdk`` being *reached for* — a raise-based sentinel would be vacuous,
because the caller swallows every Exception.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from tortoise import hosted_api as ha


class _FakeSDK:
    """Records the sweep call; optionally raises to exercise the guard."""

    def __init__(self, calls: list[str], raises: Exception | None = None):
        self.calls = calls
        self._raises = raises
        self.sweeps = 0

    def sweep_invite_ghost_memberships(self) -> dict:
        self.calls.append("sweep_invite_ghost_memberships")
        self.sweeps += 1
        if self._raises is not None:
            raise self._raises
        return {"found": 2, "ghosts": 1, "deleted": 1}


def _patch(monkeypatch, calls, *, supabase=False, raises=None):
    """Fake SDK factory + mode switch; returns the _make_sdk fetch counter."""
    from tortoise import supabase_control as sc

    fetches: list[int] = []
    sdk = _FakeSDK(calls, raises=raises)

    def _make_sdk(*_a, **_k):
        fetches.append(1)
        return sdk

    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: supabase)
    monkeypatch.setattr(ha, "_make_sdk", _make_sdk)
    return fetches


def test_the_sweep_runs_on_the_registry_and_reports_what_it_deleted(
        monkeypatch, caplog):
    calls: list[str] = []
    _patch(monkeypatch, calls)

    with caplog.at_level(logging.INFO, logger="tortoise.hosted_api"):
        ha._sweep_invite_ghosts()

    assert calls == ["sweep_invite_ghost_memberships"], (
        "the caller must reach the #1908 sweep — not re-implement it")
    assert any("invite ghost sweep deleted 1" in r.getMessage()
               for r in caplog.records), (
        "a sweep that deleted rows must say so; a silent GC is "
        "indistinguishable from 'nothing to do'")


def test_supabase_mode_never_constructs_the_registry(monkeypatch):
    """#669: the registry graph must not be built in Supabase mode.

    Assert the NEGATIVE (no SDK constructed), not the absence of a raise: the
    caller swallows every Exception, so a raise-based sentinel would pass
    against the bug.
    """
    calls: list[str] = []
    fetches = _patch(monkeypatch, calls, supabase=True)

    ha._sweep_invite_ghosts()

    assert fetches == [], "Supabase mode must not reach for the registry"
    assert calls == [], "Supabase mode must not run the registry sweep"


def test_a_failing_sweep_logs_and_does_not_raise(monkeypatch, caplog):
    """Best-effort: the log is half the contract — without it a GC failure is
    invisible and the runner's guard would swallow the reason."""
    calls: list[str] = []
    _patch(monkeypatch, calls, raises=RuntimeError("registry unreachable"))

    with caplog.at_level(logging.WARNING, logger="tortoise.hosted_api"):
        ha._sweep_invite_ghosts()  # must not raise

    assert any("invite ghost sweep failed" in r.getMessage()
               for r in caplog.records), caplog.records


# The sibling sweeps are neutralised in the runner tests below: this file owns
# the invite-ghost leg, and the guard properties of the others are covered by
# test_cost_allocation.py::_BOOT_SWEEPS.
_SIBLINGS = ("_sweep_events", "_purge_deleted_orgs",
             "_purge_deleted_accounts", "_sweep_oauth_retention",
             "_reconcile_metering_periods")


def test_the_boot_runner_arms_the_sweep(monkeypatch):
    """Drive the REAL runner: the scheduling property, not the step.

    A direct call to ``_sweep_invite_ghosts()`` cannot observe whether it is
    scheduled at all — which is exactly the defect.
    """
    calls: list[str] = []
    _patch(monkeypatch, calls)

    async def _inline(fn, *, name, timeout=None):  # no threads in this test
        return fn()

    monkeypatch.setattr(ha, "run_on_daemon_worker", _inline)
    for attr in _SIBLINGS:
        monkeypatch.setattr(ha, attr, lambda *_a, **_k: None)

    asyncio.run(ha._run_boot_sweeps())

    assert calls == ["sweep_invite_ghost_memberships"], (
        "the boot runner did not arm the invite-ghost sweep — #1890 is that "
        "the sweep had no live caller")


def test_the_periodic_loop_also_arms_the_sweep(monkeypatch):
    """The periodic leg matters more than boot: a long-lived process boots once.

    Boot alone would sweep only at deploy time — the same one-shot shape as the
    operator-run backfill script that caused #1890. So assert the sweep is
    reached from the loop as well.
    """
    calls: list[str] = []
    _patch(monkeypatch, calls)

    async def _inline(fn, *, name, timeout=None):  # no threads in this test
        return fn()

    async def _noop_async(*_a, **_k):
        return None

    monkeypatch.setattr(ha, "run_on_daemon_worker", _inline)
    for attr in _SIBLINGS:
        # Sync sweeps: an async stub here would hand `_inline` a coroutine it
        # never awaits.
        monkeypatch.setattr(ha, attr, lambda *_a, **_k: None)
    monkeypatch.setattr(ha, "_refresh_cost_allocation", _noop_async)

    async def _one_pass():
        task = asyncio.create_task(ha._event_retention_loop(0.0))
        # Let the loop reach its first guarded step, then stop it.
        for _ in range(200):
            await asyncio.sleep(0)
            if calls:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_one_pass())

    assert calls == ["sweep_invite_ghost_memberships"], (
        "the hourly retention loop did not arm the invite-ghost sweep — a "
        "boot-only sweep is the same one-shot shape as the operator script")
