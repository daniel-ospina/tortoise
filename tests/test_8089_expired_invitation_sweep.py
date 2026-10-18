"""#8089: the Supabase lane's clock-expiry sweep must have a LIVE caller.

``expires_at`` is a DERIVED terminal state in the control-plane lane — the
accept paths, the resend path, the invitee pending list and the Pro-capacity
read all compare it to now. The DEDUP could not: its authority is the 0008
partial unique index ``(org_id, email) WHERE status='pending'``, and a partial
index predicate must be IMMUTABLE, so ``expires_at > now()`` can never be part
of it. Nothing ever moved a clock-expired row out of ``pending`` either —
``invitation_expire`` is an admin action and this table had no sweep — so an
expired invite held its index entry FOREVER and 409'd every re-invite for that
address. That is #1890's defect (fixed there by deriving expiry in the registry
lane) reached through a mechanism no predicate edit can close.

So the fix has two halves, and this file pins the SECOND one: the terminal
state must be MATERIALISED for every other reader, on a schedule. The first
half (``invitation_mint`` retiring the row it is about to replace) is pinned in
``tests/test_supabase_control.py::TestExpiredInvitationRemint`` and is what
makes correctness independent of this sweep.

What these tests assert, and why in this shape:

* They drive the REAL runners (``_run_boot_sweeps`` and
  ``_event_retention_loop``) and assert the sweep ARRIVED there. A test that
  only called ``_sweep_expired_invitations()`` directly, or imported it and
  checked it existed, would have been green for the whole life of the bug —
  "the sweep was never wrong, it just never ran" (#4241/#1890 shape).
* The mode polarity is a contract, not an optimisation: the registry lane's
  invites live in the graph and were fixed by #1890, so Supabase-disabled mode
  must never construct the control plane. Asserted with a raise-based sentinel
  because the sweep itself swallows every Exception.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from tests.fake_control_plane import FakeControlPlane
from tortoise import hosted_api as ha
from tortoise import supabase_control as sc

#: Siblings the runners also arm. Neutralised so these tests own ONE step; the
#: guard properties of the others are covered by
#: ``test_cost_allocation.py::_BOOT_SWEEPS``.
_SIBLINGS = ("_sweep_events", "_purge_deleted_orgs",
             "_purge_deleted_accounts", "_sweep_oauth_retention",
             "_sweep_invite_ghosts", "_reconcile_metering_periods")


def _stale_row(*, row_id: str = "inv-stale", org_id: str = "org-1") -> dict:
    """A pending invitation that expired BY THE CLOCK and nothing else.

    ``status`` is deliberately left 'pending': that IS the defect state — no
    writer ever materialised the clock expiry, which is why the row kept
    occupying the partial unique index.
    """
    return {
        "id": row_id,
        "org_id": org_id,
        "email": "bob@example.com",
        "role": "member",
        "status": "pending",
        "expires_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        "accepted_at": None,
        "created_at": (datetime.now(UTC) - timedelta(days=7)).isoformat(),
    }


def _patch(monkeypatch, *, supabase: bool = True, rows: list[dict] | None = None):
    """Supabase-mode switch + control-plane double; returns (cp, sentinel log)."""
    cp = FakeControlPlane({"invitations": rows if rows is not None else []})
    fetches: list[int] = []

    def _get_cp(*_a, **_k):
        fetches.append(1)
        return cp

    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: supabase)
    monkeypatch.setattr(sc, "get_control_plane", _get_cp)
    return cp, fetches


def _neutralise_siblings(monkeypatch) -> None:
    async def _inline(fn, *, name, timeout=None):  # no threads in this test
        return fn()

    monkeypatch.setattr(ha, "run_on_daemon_worker", _inline)
    for attr in _SIBLINGS:
        monkeypatch.setattr(ha, attr, lambda *_a, **_k: None)


def test_the_boot_runner_arms_the_sweep(monkeypatch, caplog):
    """Drive the REAL boot runner: the scheduling property, not the step."""
    cp, fetches = _patch(monkeypatch, rows=[_stale_row()])
    _neutralise_siblings(monkeypatch)

    with caplog.at_level(logging.INFO, logger="tortoise.hosted_api"):
        asyncio.run(ha._run_boot_sweeps())

    assert fetches == [1], (
        "the boot runner did not arm the expired-invitation sweep — #8089 is "
        "that the sweep did not exist at all")
    assert cp.tables["invitations"][0]["status"] == "expired", (
        "the sweep ran but the stale row is still status='pending' — it would "
        "keep holding the partial unique index and 409 the re-invite")
    assert any("expired invitation sweep flipped 1" in r.getMessage()
               for r in caplog.records), (
        "the sweep must REPORT what it materialised — an operator needs the "
        "backlog figure, and a silent GC is indistinguishable from 'nothing "
        "to do' (#4872)")


def test_the_periodic_loop_also_arms_the_sweep(monkeypatch, caplog):
    """The periodic leg matters more than boot: a long-lived process boots once.

    Boot alone would sweep only at deploy time — the same one-shot shape as the
    operator-run backfill script that caused #1890.
    """
    cp, fetches = _patch(monkeypatch, rows=[_stale_row()])
    _neutralise_siblings(monkeypatch)

    async def _drive() -> None:
        task = asyncio.ensure_future(ha._event_retention_loop(0))
        try:
            for _ in range(500):
                if cp.tables["invitations"][0]["status"] == "expired":
                    break
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    with caplog.at_level(logging.INFO, logger="tortoise.hosted_api"):
        asyncio.run(_drive())

    assert fetches, "the periodic loop never ran the expired-invitation sweep"
    assert cp.tables["invitations"][0]["status"] == "expired"


def test_registry_mode_never_constructs_the_control_plane(monkeypatch):
    """#669/#1890 split: the registry lane's invite expiry is NOT this step.

    The sweep must be inert with Supabase disabled — a raise-based sentinel
    proves it is never even reached for, because the step swallows every
    Exception and a silent no-op would look identical.
    """

    def _boom(*_a, **_k):  # pragma: no cover - must never be reached
        raise AssertionError(
            "the expired-invitation sweep reached for the control plane in "
            "registry mode — the registry lane's invites are graph nodes "
            "owned by #1890's sweep")

    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: False)
    monkeypatch.setattr(sc, "get_control_plane", _boom)

    ha._sweep_expired_invitations()  # must not raise and must not fetch


def test_a_failing_sweep_logs_and_does_not_raise(monkeypatch, caplog):
    """Best-effort by construction: a sweep failure never kills its runner.

    The runners also guard each step (#5381), but the step's OWN swallow is
    what keeps a transient control-plane outage from being logged as a runner
    failure with no name attached.
    """

    class _Exploding(FakeControlPlane):
        def query(self, table, *a, **k):
            raise RuntimeError("Supabase unreachable (simulated)")

    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda *_a, **_k: _Exploding())

    with caplog.at_level(logging.WARNING, logger="tortoise.hosted_api"):
        ha._sweep_expired_invitations()  # must not raise

    assert any("expired invitation sweep failed" in r.getMessage()
               for r in caplog.records), caplog.records


def test_an_empty_backlog_is_a_quiet_no_op(monkeypatch, caplog):
    """Nothing stale → nothing logged. A per-cycle INFO would be noise."""
    _cp, fetches = _patch(monkeypatch, rows=[])

    with caplog.at_level(logging.INFO, logger="tortoise.hosted_api"):
        ha._sweep_expired_invitations()

    assert fetches == [1], "the sweep must still RUN on an empty backlog"
    assert not [r for r in caplog.records
                if "expired invitation sweep" in r.getMessage()]