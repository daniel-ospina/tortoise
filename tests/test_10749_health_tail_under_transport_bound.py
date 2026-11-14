"""#10749 — the MCP health tool's own worst case must fit the deadline it is served under.

THE DEFECT. ``tortoise_health`` runs a DEEP probe inline, with an allowance resolved from
``monitoring.probe_setup_timeout()`` (20.0 s default, ceiling 300.0), while
``_await_under_mcp_wait_bound`` serves EVERY tool under ``mcp_auth._TRANSPORT_WAIT_BOUND_S``
(10.0 s) — no allowlist, no exemption, and **no transport exemption either**: on stdio and on the
self-hosted apps no arrival stamp exists, and the seam still binds the FULL bound
(``remaining = float(_mcp_auth._TRANSPORT_WAIT_BOUND_S)``). The handler's bounded tail is

    probe_setup_timeout() + PROBE_TIMEOUT + GRAPH_SIZE_TIMEOUT + QUOTA_STATE_TIMEOUT
  = 20.0 + 1.5 + 1.5 + 3.0 = 26.0 s                                 (2.6x the bound)

so a REACHABLE store is refused with the transport's own "-32009 wait budget exceeded" text,
which reads as "the queue is busy" and says nothing about the tool's budget.

MEASURED (2026-11-13, deployed process, from a box with the credential — issue comment
6862096657): the tool answered **2 of 27** attempts; the single timed success took a **10.77 s**
round trip and spent **6.96 s** of it in the DB probe, i.e. ~3.8 s of the bound is gone before
the probe's first byte, so refusal is certain once ``db.latency_ms`` exceeds ~6.2 s. The store
sat at 6.89-6.96 s, which is exactly the "occasionally ok, mostly refused" outcome observed.

WHAT THIS FILE PINS. The fix is shape B of the issue (clamp at the seam — no surface change):
the allowance the handler SPENDS is ``min(probe_setup_timeout(), remaining - reserve)`` where
``remaining`` is the deadline ``_await_under_mcp_wait_bound`` itself bound into
``_WAIT_BOUND_DEADLINE`` (so it covers stdio and self-hosted HTTP, where there is no arrival
stamp), ``reserve`` is the other three bounded phases plus a delivery margin, and the clamp is
VISIBLE on the report — as a BOOLEAN discriminator, because the duration beside it can be a
falsy ``0.0`` — so a clamped (unobserved) probe cannot be misread as a broken store (#3683's
``ok=None`` / ``status="unknown"``).
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from tortoise import mcp_auth, mcp_server, monitoring, quota

_BOUND = float(mcp_auth._TRANSPORT_WAIT_BOUND_S)


def _reserve() -> float:
    """The three other bounded phases plus the delivery margin the clamp must leave behind."""
    return (monitoring.PROBE_TIMEOUT
            + monitoring.GRAPH_SIZE_TIMEOUT
            + quota.QUOTA_STATE_TIMEOUT
            + mcp_server._HEALTH_TAIL_MARGIN_S)


@pytest.fixture(autouse=True)
def _http_transport():
    """Drive the tool body directly, under the transport mode it actually runs in.

    ``_safe`` fail-closes on ``_transport_mode is None`` (the #236 gate), and the stdio arm
    further refuses once ``TORTOISE_API_KEY`` is present — which is true on every cell that
    carries the hosted credential. The ``http`` arm delegates auth to the transport, which is
    what a tool-body test wants. Same fixture as tests/test_10602_query_limit_bound.py.
    """
    from tortoise.mcp_auth import (  # noqa: I001
        _current_org_id, _current_org_limits, _transport_mode,
    )
    _transport_mode.set("http")
    _current_org_id.set(None)
    _current_org_limits.set(None)
    yield
    _transport_mode.set(None)
    _current_org_id.set(None)
    _current_org_limits.set(None)


@pytest.fixture(autouse=True)
def _default_setup_knob(monkeypatch):
    """Pin the allowance arithmetic to the DEFAULT it claims, not to this box's env (#10749).

    ``monitoring.probe_setup_timeout()`` reads ``TORTOISE_PROBE_SETUP_TIMEOUT`` at CALL time and
    the repo-root ``.env`` is loaded at import, so a box configured with a perfectly valid
    ``TORTOISE_PROBE_SETUP_TIMEOUT=4`` would red ``test_the_unclamped_tail_outgrows_the_transport_bound``
    (a 10.0 s tail is not ``>`` 10.0 s) and the arithmetic cases — a false block on a legitimate
    configuration, not a product defect. Same guard as
    ``tests/test_selfhost_health_probe_executor.py``.
    """
    monkeypatch.delenv("TORTOISE_PROBE_SETUP_TIMEOUT", raising=False)


@pytest.fixture(autouse=True)
def _no_seam_binding():
    """Start every test from "the seam never ran", so a test opts IN to a deadline explicitly.

    ``_WAIT_BOUND_DEADLINE`` is a module-level ContextVar; without this the value one test binds
    would leak into the next and make the unclamped-arm test pass for the wrong reason.
    """
    token = mcp_server._WAIT_BOUND_DEADLINE.set(None)
    yield
    mcp_server._WAIT_BOUND_DEADLINE.reset(token)


def _capture(monkeypatch, report=None):
    """Patch the report's collaborators and capture the allowance the handler actually spends.

    ``report=`` substitutes the value ``monitoring.metrics`` returns, so a test can drive the
    handler against a HEALTHY probe (``observed=True``, ``status="ok"``) as well as the clamped
    one. That distinction is load-bearing: the clamp is applied to BOTH, and only one of them is
    a degradation.
    """
    seen: dict = {}

    def fake_metrics(**kwargs):
        seen.update(kwargs)
        if report is not None:
            return dict(report)
        # The default IS the clamp's own output shape: unobserved AND carrying the
        # setup-timeout spelling, which is what the counter's gate requires (#12482 cycle 7,
        # adopted from the peer lane). A bare ``observed=False`` is also what a saturated worker
        # backlog produces, and that arm must NOT count — see
        # ``test_a_backlog_refusal_is_unobserved_but_is_not_the_clamp_s_costing``.
        return {"status": "unknown",
                "db": {"ok": None, "observed": False,
                       "error": f"{monitoring._PROBE_SETUP_TIMEOUT_MSG}0.00s"}}

    monkeypatch.setattr(monitoring, "metrics", fake_metrics)
    monkeypatch.setattr(mcp_server, "_org_quota_block",
                        lambda sdk, **kwargs: {"state": "unknown", **kwargs})
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: object())
    return seen


def test_the_unclamped_tail_outgrows_the_transport_bound():
    """The defect itself, in one line: 26.0 s of allowance inside a 10.0 s bound.

    This assertion is deliberately the OPPOSITE of what the fix guarantees — it documents why
    a clamp has to exist. If a future change brings the four constants under the bound on their
    own, this test reds, and that is the signal to delete the clamp rather than keep both.
    """
    tail = (monitoring.probe_setup_timeout()
            + monitoring.PROBE_TIMEOUT
            + monitoring.GRAPH_SIZE_TIMEOUT
            + quota.QUOTA_STATE_TIMEOUT)
    assert tail > mcp_auth._TRANSPORT_WAIT_BOUND_S, (
        f"tail {tail}s is no longer larger than the transport bound "
        f"{mcp_auth._TRANSPORT_WAIT_BOUND_S}s — the clamp may no longer be needed"
    )


def test_the_clamped_allowance_fits_the_remaining_deadline():
    """The invariant this issue asks for: health budget <= transport bound - margin."""
    bound = mcp_auth._TRANSPORT_WAIT_BOUND_S
    allowance = mcp_server._health_setup_allowance(bound)
    # ⛔ `allowance + _reserve() <= bound` is an IDENTITY at a full bound — the allowance is
    # DEFINED as `bound − reserve`, so the sum is `bound` for any reserve at all (measured across
    # margins 0.25/0.5/1.0/2.0: always 10.0). It is kept only as documentation; the falsifiable
    # form is the DIRECTED one, which reds if the allowance stops subtracting the reserve.
    assert allowance == pytest.approx(bound - _reserve())
    assert allowance < bound, (
        "the allowance consumed the whole bound — there is nothing left for the tool's own "
        "bounded phases, which is the refusal this change exists to remove"
    )


def test_the_clamp_never_answers_a_negative_allowance():
    """A spent deadline yields 0.0, never a negative timeout (which would raise in the probe)."""
    assert mcp_server._health_setup_allowance(0.0) == 0.0
    assert mcp_server._health_setup_allowance(1.0) == 0.0


def test_a_generous_deadline_keeps_the_3143_allowance():
    """The clamp only ever REDUCES: #3143's cold-start allowance survives when it fits."""
    assert (mcp_server._health_setup_allowance(mcp_auth._TRANSPORT_WAIT_BOUND_S + 300.0)
            == monitoring.probe_setup_timeout())


def test_the_margin_is_a_real_reserve_not_zero():
    """A zero margin would make the issue's own target (`<= bound − margin`) vacuous.

    Every other assertion on the margin derives it from the same constant the implementation
    uses, so those move together: setting `_HEALTH_TAIL_MARGIN_S = 0.0` would leave them all
    green while the handler has NO room left for serialisation and delivery after its last
    bounded phase — which is the refusal this change exists to remove, back at the margin.
    """
    assert mcp_server._HEALTH_TAIL_MARGIN_S >= 0.25, (
        "the delivery margin was reduced to a value that leaves no room for the response "
        "itself; the clamp then re-introduces the -32009 refusal it was added to prevent"
    )
    # ⛔ `_reserve() >= phases + 0.25` reduced to `phases + margin >= phases + 0.25` — the same
    # predicate as the assertion above, algebra on this file only (fresh review, round 5 P3). The
    # falsifiable form is against the IMPLEMENTATION's reserve, which is what the handler spends.
    phases = (monitoring.PROBE_TIMEOUT + monitoring.GRAPH_SIZE_TIMEOUT
              + quota.QUOTA_STATE_TIMEOUT)
    assert mcp_server._health_tail_reserve() - phases == pytest.approx(
        mcp_server._HEALTH_TAIL_MARGIN_S), (
        "the implementation's reserve does not carry the delivery margin on top of the three "
        "bounded phases — the tail it plans for is not the tail the docstring describes"
    )
    # ⛔ `allowance + _reserve() == bound` is a TAUTOLOGY at a full bound (same reason as above):
    # it holds for any reserve, so it can never report a partially-accounted deadline. The
    # falsifiable form is against a deadline the reserve actually fits inside.
    # ⛔ Saturation needs `remaining >= knob + reserve` (26.5 s at the shipped constants) — a
    # deadline the transport bound can never grant, which is the point.
    generous = monitoring.probe_setup_timeout() + _reserve() + 1.0
    assert (mcp_server._health_setup_allowance(generous)
            == pytest.approx(monitoring.probe_setup_timeout())), (
        "with room to spare the allowance must saturate at the knob, never keep growing"
    )


def test_the_reserve_leaves_room_inside_the_bound():
    """A static guard on the four constants: a future bump must not make the reserve unfittable.

    If ``PROBE_TIMEOUT + GRAPH_SIZE_TIMEOUT + QUOTA_STATE_TIMEOUT + margin`` ever reaches the
    transport bound, the clamp degenerates to "always 0" and the tool could never report a real
    probe again — silently. This reds first.
    """
    assert _reserve() < mcp_auth._TRANSPORT_WAIT_BOUND_S


def test_a_direct_in_process_call_keeps_the_unclamped_allowance(monkeypatch):
    """``None`` from the seam means the seam NEVER RAN (a direct call), not "no deadline".

    This is the one state where #3143's allowance is still the right answer: nothing is enforcing
    a transport bound, so there is nothing to clamp under. The two existing direct-call tests in
    ``tests/test_monitoring.py`` rely on exactly this.
    """
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    assert mcp_server._WAIT_BOUND_DEADLINE.get() is None
    seen = _capture(monkeypatch)

    mcp_server.tortoise_health()

    assert seen["setup_timeout"] == monitoring.probe_setup_timeout()


def test_stdio_the_seam_still_binds_the_full_transport_bound(monkeypatch):
    """RED on the pre-review head: the clamp was skipped whenever no arrival stamp existed.

    On stdio AND on the self-hosted apps no stamp is written (only ``hosted_api``'s middleware
    writes one), but ``_await_under_mcp_wait_bound`` still binds the FULL bound there. The pre-fix
    helper read the absent stamp as "no deadline" and spent the unclamped 20.0 s inside a 10.0 s
    bound — #10749's defect preserved on a first-class transport, with the test blessing it.
    """
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    token = mcp_server._WAIT_BOUND_DEADLINE.set(
        time.monotonic() + float(mcp_auth._TRANSPORT_WAIT_BOUND_S))
    try:
        seen = _capture(monkeypatch)
        report = mcp_server.tortoise_health()
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    bound = mcp_auth._TRANSPORT_WAIT_BOUND_S
    assert seen["setup_timeout"] <= bound - _reserve()
    assert seen["setup_timeout"] < monitoring.probe_setup_timeout()
    assert report.get("setup_timeout_clamped") is True


def test_the_handler_clamps_under_a_live_transport_deadline(monkeypatch):
    """On HTTP the allowance is clamped to the REMAINING deadline, not the full one."""
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: time.monotonic())
    seen = _capture(monkeypatch)

    mcp_server.tortoise_health()

    spent = seen["setup_timeout"]
    assert spent <= mcp_auth._TRANSPORT_WAIT_BOUND_S - _reserve()
    assert spent < monitoring.probe_setup_timeout()
    assert spent >= 0.0


def test_an_aged_arrival_stamp_reduces_the_allowance(monkeypatch):
    """The clamp must SUBTRACT elapsed request time — the measured case had 3.8 s already spent.

    A clamp that ignored elapsed time (``min(probe_setup_timeout(), bound - reserve)``) passes
    every other test in this file, because they all use ``time.monotonic()`` — the one arrival
    value where "spend what is left" and "spend the full bound" are the same number. At the
    deployment this issue measured, remaining was ~6.2 s, so an elapsed-ignoring clamp grants
    3.5 s, the tail becomes 10.0 s, and the -32009 refusal returns silently green.
    """
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: time.monotonic() - 3.0)
    seen = _capture(monkeypatch)

    mcp_server.tortoise_health()

    bound = mcp_auth._TRANSPORT_WAIT_BOUND_S
    assert seen["setup_timeout"] <= bound - 3.0 - _reserve()
    assert seen["setup_timeout"] != pytest.approx(bound - _reserve(), abs=0.10), (
        "the allowance ignored the ~3 s already spent on this request"
    )


def test_the_report_says_it_was_clamped_with_a_truthy_discriminator(monkeypatch):
    """A clamped probe reports ``unknown`` for a reason; the report must name that reason.

    Without the marker a clamped probe is indistinguishable from a broken store — #3143's
    symptom in a new dress, which is the one thing the issue says shape B must not reintroduce.
    The BOOLEAN is what a reader tests: the duration beside it is legitimately ``0.0`` on a spent
    deadline, and ``if report.get("setup_timeout_clamped_s")`` would read the most severe clamp
    as "not clamped".
    """
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: time.monotonic())
    _capture(monkeypatch)

    report = mcp_server.tortoise_health()

    assert report["setup_timeout_clamped"] is True
    assert report["setup_timeout_clamped_s"] <= mcp_auth._TRANSPORT_WAIT_BOUND_S - _reserve()
    assert report["setup_timeout_clamped_s"] < monitoring.probe_setup_timeout()


def test_a_spent_deadline_still_reads_as_clamped(monkeypatch):
    """The 0.0 case — the most severe clamp — must not read as "not clamped" (falsy duration)."""
    token = mcp_server._WAIT_BOUND_DEADLINE.set(time.monotonic())
    try:
        _capture(monkeypatch)
        report = mcp_server.tortoise_health()
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    assert report["setup_timeout_clamped_s"] == 0.0
    assert report["setup_timeout_clamped"] is True


def test_an_unclamped_report_carries_no_clamp_marker(monkeypatch):
    """The marker is a claim about THIS call: it must be absent when nothing was clamped."""
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    _capture(monkeypatch)

    report = mcp_server.tortoise_health()

    assert "setup_timeout_clamped" not in report
    assert "setup_timeout_clamped_s" not in report


def test_a_clamped_probe_is_unknown_not_degraded():
    """The clamp's safety claim, driven through the REAL ``monitoring.metrics``.

    Every handler-level test above replaces ``monitoring.metrics`` with a fake, so they would all
    stay green if ``metrics(setup_timeout=0.0)`` ever raised, blocked, or reported ``degraded`` —
    a false alarm on a HEALTHY store, which is the failure this whole change exists to avoid. This
    test pins the chain the clamp depends on: an allowance of 0.0 makes the probe UNOBSERVED, and
    ``metrics`` maps that to ``ok=None`` / ``status="unknown"`` (#3683), never ``degraded``.
    """
    class SlowColdStart:
        """A reachable store whose projection cold-start outlives any allowance given."""

        def _get_proj(self):
            time.sleep(0.4)
            raise AssertionError("the probe should have given up before this returned")

        def _cache_meta(self, *a, **k):
            return None

    started = time.monotonic()
    report = monitoring.metrics(sdk=SlowColdStart(), setup_timeout=0.0)
    elapsed = time.monotonic() - started

    assert elapsed < 3.0, f"a 0.0 allowance still spent {elapsed:.2f}s (the bound is 10.0s)"
    assert report["status"] == "unknown"
    assert report["db"]["ok"] is None
    assert report["db"]["observed"] is False


@pytest.mark.asyncio
async def test_the_seam_binds_its_deadline_where_the_tool_body_can_read_it(monkeypatch):
    """The P1's actual mechanism: the number the SEAM enforces is the number the body reads.

    The clamp is only correct if it clamps under the deadline ``_await_under_mcp_wait_bound``
    itself computed. Re-deriving it in the body diverged on the no-arrival-stamp arm (stdio and
    the self-hosted apps), which is how the first version of this change left #10749 unfixed on
    those transports. This drives the REAL seam with a stand-in inner handler, so removing the
    binding — or resetting it before the task is created — reds here.
    """
    seen: dict = {}

    async def fake_inner(name, arguments, **kwargs):
        seen["deadline"] = mcp_server._WAIT_BOUND_DEADLINE.get()
        seen["remaining_after_bind"] = mcp_server._mcp_remaining_wait_bound()
        return {"ok": True}

    monkeypatch.setattr(mcp_server, "_original_call_tool", fake_inner)
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    monkeypatch.setattr(mcp_server, "_wait_bound_refused", lambda: False)
    monkeypatch.setattr(mcp_server, "_hold_mcp_dispatch_after_request", lambda *a, **k: None)

    before = time.monotonic()
    await mcp_server._await_under_mcp_wait_bound(
        "tortoise_health", {}, version=None, task_meta=None)
    after = time.monotonic()

    assert seen["deadline"] is not None, (
        "the seam did not bind a deadline for the tool body to clamp under — on stdio and "
        "self-hosted HTTP that leaves tortoise_health spending its unclamped 26.0 s tail"
    )
    assert before + _BOUND - 0.5 <= seen["deadline"] <= after + _BOUND + 0.5, (
        "the seam bound something other than the absolute deadline it enforces"
    )
    # The tolerance is TIGHT on purpose (cycle-5 review, P3): a 5.0 s wobble around a 10.0 s bound
    # would tolerate the deadline being half the bound off — i.e. it would stay green for most of
    # the arithmetic errors it is supposed to catch. The bracket is the real one: the deadline is
    # computed inside the await, so it lies between `before` and `after`.
    assert seen["remaining_after_bind"] == pytest.approx(_BOUND, abs=1.0), (
        "the body did not read back approximately the full bound"
    )


def test_time_spent_after_the_seam_binds_reduces_the_allowance(monkeypatch):
    """A DEADLINE, not a duration — the fix must deduct whatever the dispatch already spent.

    The seam's own ``wait_for`` clock starts when it binds, so a duration captured there grants an
    allowance the handler no longer has. Measured on the cycle-2 head with a 4.0 s in-dispatch spend
    (the slot ``_resolve_graph_argument``'s control-plane lookup occupies): the duration shape
    granted the full ``bound - reserve`` and the worst case became ``4.0 + 3.5 + 1.5 + 1.5 + 3.0 =
    13.5 s`` inside a 10.0 s bound — the ``-32009`` refusal this change exists to remove, re-armed
    silently. This pins the deadline read at the point of USE.
    """
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    token = mcp_server._WAIT_BOUND_DEADLINE.set(time.monotonic() + _BOUND)
    try:
        time.sleep(2.0)          # the dispatch spending time before the handler clamps
        seen = _capture(monkeypatch)
        mcp_server.tortoise_health()
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    assert seen["setup_timeout"] <= _BOUND - 2.0 - _reserve(), (
        "the allowance ignored the ~2 s spent after the seam bound its deadline; the handler "
        "then claims time it no longer has and the transport refuses the call"
    )


def test_a_clamp_raises_the_aggregate_counter_and_an_unclamped_call_does_not(monkeypatch):
    """The counter is the only aggregate that keeps "health is degraded to unknown" answerable.

    The clamp moves a degradation from a transport REFUSAL (counted by
    ``wait_bound_breaches``) to an honest ``status="unknown"`` (counted by nothing else), so an
    unguarded counter is exactly the uncounted-symptom condition that finding was about.

    ⚠️ The incremented arm is the UNOBSERVED one (``db["observed"] is False``): since the cycle-5
    review the counter counts the degradation, not the application of the clamp. The companion
    test below pins the other half — a healthy probe through a clamped allowance must NOT count.
    """
    baseline = monitoring.health_setup_clamped_count()

    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    _capture(monkeypatch)
    mcp_server.tortoise_health()                       # unclamped arm
    assert monitoring.health_setup_clamped_count() == baseline, (
        "an UNCLAMPED health call incremented the clamp counter"
    )

    token = mcp_server._WAIT_BOUND_DEADLINE.set(time.monotonic() + _BOUND)
    try:
        _capture(monkeypatch)
        mcp_server.tortoise_health()                   # clamped arm
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    assert monitoring.health_setup_clamped_count() == baseline + 1, (
        "a clamped health call did not increment the clamp counter"
    )


def test_a_healthy_probe_on_a_clamped_call_does_not_raise_the_counter(monkeypatch):
    """The counter counts the DEGRADATION, not the clamp being applied (cycle-5 review, P2).

    The clamp is applied on essentially EVERY seam-served call: while the seam enforces the bound
    ``remaining <= 10.0 s``, while an unclamped 20.0 s allowance needs ``remaining > 26.5 s``. A
    counter incremented at the point of APPLICATION therefore reads 100 % on a perfectly healthy
    process — it becomes an MCP-health-call counter — and a genuine degradation still moves
    nothing distinguishable. This is the pin that tells the two apart: a clamped call whose probe
    ANSWERED (``observed=True``, ``status="ok"``) must leave the counter alone.
    """
    healthy = {"status": "ok", "db": {"ok": True, "observed": True, "latency_ms": 50.0}}
    baseline = monitoring.health_setup_clamped_count()

    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    token = mcp_server._WAIT_BOUND_DEADLINE.set(time.monotonic() + _BOUND)
    try:
        _capture(monkeypatch, report=healthy)
        report = mcp_server.tortoise_health()
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    assert report["setup_timeout_clamped"] is True, (
        "the clamp was not applied at all — this test is not exercising the clamped arm, so it "
        "would pass for the wrong reason"
    )
    assert monitoring.health_setup_clamped_count() == baseline, (
        "a HEALTHY probe that answered through a clamped allowance incremented the degradation "
        "counter; the key would then read 100% on every healthy process and could not report a "
        "degradation at all"
    )


def test_an_OBSERVED_probe_carrying_the_setup_spelling_does_not_raise_the_counter(monkeypatch):
    """Term 2 of the counter's conjunction — ``db["observed"] is False`` — is load-bearing ALONE.

    ⛔ Reported in the round-2 review as unpinned, and it was: the healthy fixture carries no
    ``error`` key, so deleting the ``observed`` term left all 42 cases green while the counter began
    counting a shape it is documented to exclude. The shape is reachable, not hypothetical —
    ``_probe_once``'s "callable raised and ``str(e)`` was empty ⇒ synthesize the setup spelling"
    arm returns ``observed=True`` WITH ``_PROBE_SETUP_TIMEOUT_MSG``, so the spelling term alone
    cannot separate it. Counting it would tell an operator the clamp cost an observation for a cold
    start that ANSWERED WITH A FAILURE — a store problem, not a deadline problem.
    """
    observed_failure = {
        "status": "degraded",
        "db": {"ok": False, "observed": True, "latency_ms": 5000.0,
               "error": f"{monitoring._PROBE_SETUP_TIMEOUT_MSG}5.0s"},
    }
    baseline = monitoring.health_setup_clamped_count()

    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    token = mcp_server._WAIT_BOUND_DEADLINE.set(time.monotonic() + _BOUND)
    try:
        _capture(monkeypatch, report=observed_failure)
        report = mcp_server.tortoise_health()
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    assert report["setup_timeout_clamped"] is True, (
        "the clamped arm was not exercised, so this test would pass for the wrong reason")
    assert monitoring.health_setup_clamped_count() == baseline, (
        "an OBSERVED failure carrying the setup spelling incremented the clamp counter — the "
        "counter would then report an observation the clamp never cost, and the remedy it names "
        "(#12481) is the wrong one for a store that failed on its own")


def test_the_counter_read_never_raises_and_reports_unread_as_none(monkeypatch):
    """``None`` on a failed read — never a raise, and never a fabricated ``0``.

    ``health_setup_clamped_count`` is read by ``hosted_api.health()``, whose docstring promises "it
    never 5xxes"; its sibling ``wait_bound_breach_counts`` is wrapped for exactly that reason. A
    ``0`` on failure would be worse than the raise in the direction that matters: ``0`` is the
    HEALTHY reading ("no clamp cost an observation"), so a fabricated one is indistinguishable
    from the answer the channel exists to give.
    """
    def unreadable(_counter):
        raise RuntimeError("registry unreadable")

    monkeypatch.setattr(monitoring, "_counter_val", unreadable)

    assert monitoring.health_setup_clamped_count() is None, (
        "an unreadable counter was folded to a value that reads as a measured zero"
    )


def test_metrics_publishes_the_clamp_counter_as_an_int(monkeypatch):
    """The key is what an operator reads on the standalone /health and /metrics surfaces.

    Without this assertion the key can be dropped from ``metrics()`` with the whole suite green —
    the per-call boolean is pinned by five tests, its aggregate twin by none.
    """
    report = monitoring.metrics()
    assert "health_setup_clamped" in report
    assert isinstance(report["health_setup_clamped"], int)

    # ⛔ Existence + `isinstance(int)` accepts a CONSTANT (fresh review, round 3, P2): `0` is the
    # healthy reading, so a hardcoded 0 would satisfy both assertions while the channel reported
    # "no clamp cost an observation" forever. The published value must be the accessor's.
    monkeypatch.setattr(monitoring, "health_setup_clamped_count", lambda: 4242)
    assert monitoring.metrics()["health_setup_clamped"] == 4242, (
        "the published key is not the live counter — a constant would read healthy forever")


def test_the_hosted_health_body_publishes_the_clamp_counter(monkeypatch):
    """The hosted app builds its OWN body — ``metrics()``'s key never reaches that surface.

    ``wait_bound_breaches`` is published there explicitly because it is "the surface an operator
    curls FIRST"; the clamp makes that counter go flat by design, so its positive pair must be
    published on the same body or the operator's first curl cannot see the degradation at all.

    ⚠️ Asserted on the BODY, not a source grep: ``inspect.getsource`` is satisfied by a mention (a
    comment, or the key nested under another block), so that version stayed green if the top-level
    key the runbook's ``jq '.health_setup_clamped'`` reads were renamed or moved to ``null``. Same
    shape as ``tests/test_10602_wait_bound_breach_visible.py``, which asserts on the served body.
    """
    import asyncio

    from tortoise import hosted_api

    body = asyncio.run(hosted_api.health())

    assert "health_setup_clamped" in body
    assert isinstance(body["health_setup_clamped"], int)

    # ⛔ Same gap as the `metrics()` twin above: a hardcoded `0` passes both assertions, and `0` is
    # the reading that means "nothing was clamped". The body must carry the LIVE counter.
    monkeypatch.setattr(monitoring, "health_setup_clamped_count", lambda: 4242)
    live = asyncio.run(hosted_api.health())
    assert live["health_setup_clamped"] == 4242, (
        "the hosted /health body publishes a constant for the clamp counter — the operator's "
        "first curl would read healthy forever while the degradation went uncounted")


def test_the_hosted_health_body_survives_a_monitoring_module_without_the_counter(monkeypatch):
    """A MISSING attribute, not a raising call — the one failure the callee cannot guard.

    ``health_setup_clamped_count`` never raises (pinned directly above), so wrapping the CALL looks
    like belt-and-braces. It is not (fresh review, P3): a stale or partially written ``monitoring``
    module has no such attribute at all, and an unguarded ``_monitoring.health_setup_clamped_count()``
    then raises ``AttributeError`` out of ``health()`` — measured, over a body whose own docstring
    promises "it never 5xxes: a dead DB is 'degraded', never a killed process". Every sibling read
    on that body is wrapped for this exact reason.

    The published shape is ``int | null``, so ``null`` is inside the contract: a read that could not
    happen reports nothing measured, never a fabricated ``0`` (which is the HEALTHY reading).
    """
    import asyncio

    from tortoise import hosted_api

    monkeypatch.delattr(monitoring, "health_setup_clamped_count")
    body = asyncio.run(hosted_api.health())

    assert body["health_setup_clamped"] is None, (
        f"an unreadable counter was published as {body['health_setup_clamped']!r}; only None "
        "means 'nothing measured' — 0 is a measured zero and is the healthy reading")
    assert "status" in body, "the health body lost its `status` key on the unreadable-counter path"


def test_a_stale_quota_module_cannot_turn_the_health_answer_into_an_error(monkeypatch):
    """Planning-time imports must be as forgivable as the ``#11892`` block's guarded one.

    ``_health_tail_reserve`` and ``_health_quota_budget`` each read ``quota.QUOTA_STATE_TIMEOUT``,
    and both run OUTSIDE any ``try`` in ``tortoise_health``'s tail (fresh review, P3). A bare
    ``from tortoise.quota import QUOTA_STATE_TIMEOUT`` at a stale module therefore raised
    ``ImportError``, which ``_safe`` turned into an error payload with **no ``status`` key at all** —
    the clamp's own arithmetic fail-closing on the instrument that reports whether the product is up,
    on a box where the pre-fix code answered normally. Measured on the reviewer's head.
    """
    import sys
    import types

    stale = types.ModuleType("tortoise.quota")
    stale.run_bounded_quota_state = quota.run_bounded_quota_state
    monkeypatch.setitem(sys.modules, "tortoise.quota", stale)

    # Both PLANNING sites first: they are the unguarded ones the finding names, and they run
    # outside any `try` in the handler's tail.
    reserve = mcp_server._health_tail_reserve()
    assert reserve == pytest.approx(
        monitoring.PROBE_TIMEOUT + monitoring.GRAPH_SIZE_TIMEOUT
        + mcp_server._QUOTA_STATE_TIMEOUT_FALLBACK_S + mcp_server._HEALTH_TAIL_MARGIN_S), (
        "the reserve did not fall back to the documented quota constant")

    token = mcp_server._WAIT_BOUND_DEADLINE.set(time.monotonic() + 10.0)
    try:
        _capture(monkeypatch)
        report = mcp_server.tortoise_health()
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    assert isinstance(report, dict) and "status" in report, (
        "a stale `tortoise.quota` cost the health answer its `status` key — the clamp's "
        f"planning imports are unguarded: {report!r}")


def test_the_quota_timeout_fallback_is_the_documented_constant():
    """The fallback must not drift away from the value it stands in for.

    ``_QUOTA_STATE_TIMEOUT_FALLBACK_S`` is a literal (3.0) used only when ``tortoise.quota`` cannot
    be imported, so no ordinary test reaches it and a retune of ``quota.QUOTA_STATE_TIMEOUT`` would
    leave the broken-deploy path reserving the wrong phase bound forever. This is the pin that makes
    the duplication safe; it is deliberately NOT a claim that the literal is a second source of
    truth.
    """
    assert pytest.approx(quota.QUOTA_STATE_TIMEOUT) == mcp_server._QUOTA_STATE_TIMEOUT_FALLBACK_S, (
        "the stale-quota fallback no longer equals quota.QUOTA_STATE_TIMEOUT — retune it in the "
        "same commit that moves the constant")


def test_the_quota_deadline_reaches_the_bound_that_enforces_it(monkeypatch):
    """The quota budget must arrive at `run_bounded_quota_state`, not just beside it.

    ⛔ The handler computes `_health_quota_budget()` and passes it to `_org_quota_block`, whose ONLY
    consumer of the number is the `timeout=` on `run_bounded_quota_state` (fresh review, round 3,
    P2). Every existing case stops one step earlier — three of them replace `_org_quota_block`
    outright and the rest read `report["quota"]["timeout"]`, which is the handler's KWARG. With the
    forwarding dropped (`run_bounded_quota_state(_compute)`) all 43 cases stayed green while a
    dispatch with 0.5 s left spent the full `QUOTA_STATE_TIMEOUT` in the LAST phase — restoring the
    exact `-32009` this change exists to remove. This case reaches the forwarding.
    """
    seen: dict = {}

    def fake_bounded(fn, timeout=None):
        seen["timeout"] = timeout
        return {"resource": "points", "state": "unknown", "points_used": None,
                "points_limit": None, "points_remaining": None, "error": "recorder"}

    # Function-local import (`from tortoise.quota import run_bounded_quota_state` inside the
    # block), so the patch goes on the MODULE, which is what the call resolves at call time.
    monkeypatch.setattr(quota, "run_bounded_quota_state", fake_bounded)
    # The block is a no-op WITHOUT a tenant org id (stdio/selfhost → `not_applicable`, no forwarding
    # to reach), so the deadline only exists on a tenant request. `_compute` is deliberately never
    # called by the recorder: the forwarding is what is under test, not the resolution inside it.
    token = mcp_auth._current_org_id.set("org-test-10749")
    try:
        block = mcp_server._org_quota_block(object(), timeout=0.4)
    finally:
        mcp_auth._current_org_id.reset(token)

    assert seen.get("timeout") == 0.4, (
        "the quota deadline never reached the bound that enforces it — the last phase would then "
        "spend its own full constant and overrun the dispatch, which is the defect this change "
        "removes")
    assert block["error"] == "recorder", (
        f"the recorded bound's own return did not become the block: {block!r}")


def test_the_quota_budget_shrinks_by_what_the_earlier_phases_actually_spent(monkeypatch):
    """The last phase's budget is measured at ITS OWN start — that is what bounds the total.

    ⛔ Summing the budgets each phase is GRANTED is not the realised total, and an earlier version
    of this case made exactly that mistake: with `metrics` stubbed to return instantly, the quota
    phase legitimately sees `remaining − margin` (~3.5 s of a 4.0 s deadline) and the four figures
    add up to 6.0 s — a sum that can never be SPENT, because the phase that would spend it starts
    later, when the earlier spend has already happened. This drives the real relationship: make the
    stubbed probe+count consume 2.0 s of a 4.0 s deadline and the quota budget must drop by that
    2.0 s, which a budget computed once at handler start could not do (fresh review of cycle 5
    round 2, P2 — the mutant is "compute `_health_quota_budget()` next to the allowance").
    """
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)

    def slow_metrics(**kwargs):
        seen.update(kwargs)
        time.sleep(2.0)                       # probe + count spending the middle of the deadline
        # The default IS the clamp's own output shape: unobserved AND carrying the
        # setup-timeout spelling, which is what the counter's gate requires (#12482 cycle 7,
        # adopted from the peer lane). A bare ``observed=False`` is also what a saturated worker
        # backlog produces, and that arm must NOT count — see
        # ``test_a_backlog_refusal_is_unobserved_but_is_not_the_clamp_s_costing``.
        return {"status": "unknown",
                "db": {"ok": None, "observed": False,
                       "error": f"{monitoring._PROBE_SETUP_TIMEOUT_MSG}0.00s"}}

    seen: dict = {}
    monkeypatch.setattr(monitoring, "metrics", slow_metrics)
    monkeypatch.setattr(mcp_server, "_org_quota_block",
                        lambda sdk, **kwargs: {"state": "unknown", **kwargs})
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: object())

    token = mcp_server._WAIT_BOUND_DEADLINE.set(time.monotonic() + 4.0)
    try:
        report = mcp_server.tortoise_health()
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    budget = report["quota"]["timeout"]
    margin = mcp_server._HEALTH_TAIL_MARGIN_S
    assert budget <= 4.0 - 2.0, (
        f"the quota phase was granted {budget}s after the earlier phases had already spent 2.0 s "
        f"of a 4.0 s deadline — its budget is not measured at its own start"
    )
    # ⛔ The MARGIN must be in the subtraction, not merely bracketed (fresh review, round 5, P3).
    # A `<= 4.0 - 2.0` upper bound and a `>= 0.5` floor both hold with the margin deleted, so
    # `max(0.0, remaining)` passed every case while the documented, twice-measured claim "the total
    # is exactly `remaining − the 0.5 s margin`" rested on a subtraction nothing would red. This
    # deadline does not saturate at `QUOTA_STATE_TIMEOUT` (1.5 < 3.0), so the margin is visible.
    assert budget == pytest.approx(4.0 - 2.0 - margin, abs=0.2), (
        f"the quota phase was granted {budget}s where `remaining − margin` is "
        f"{4.0 - 2.0 - margin}s — the delivery margin the runbook and `_health_quota_budget` both "
        f"document is not in the subtraction")
    assert seen["graph_size_timeout"] is not None


def test_the_planned_phases_fit_the_bound_when_the_reserve_fits(monkeypatch):
    """Above the reserve the PLAN must fit whole: allowance + all three phases <= remaining − margin.

    This is the property the loudest documented claim rests on ("the tail is
    `remaining − margin`"), stated where it is arithmetic rather than emergent: at or above
    `_health_tail_reserve()` the allowance, the probe, the count and the quota phase can all be
    granted at once and still fit. Below the reserve the count and the quota budgets are capped as
    their own starts arrive, which the case above pins.
    """
    reserve = _reserve()
    for remaining in (mcp_auth._TRANSPORT_WAIT_BOUND_S, reserve):
        allowance = mcp_server._health_setup_allowance(remaining)
        planned = (allowance + monitoring.PROBE_TIMEOUT + monitoring.GRAPH_SIZE_TIMEOUT
                   + quota.QUOTA_STATE_TIMEOUT)
        assert planned <= remaining - mcp_server._HEALTH_TAIL_MARGIN_S + 1e-6, (
            f"at {remaining:.2f}s the planned phases total {planned:.2f}s, more than the "
            f"{remaining - mcp_server._HEALTH_TAIL_MARGIN_S:.2f}s the bound leaves for them"
        )


def test_the_allowance_is_a_hard_step_at_the_reserve():
    """The cliff the docstring and the runbook quote as a NUMBER is pinned, not prose.

    ``_health_setup_allowance`` is ``max(0.0, min(knob, remaining - reserve))``, so the allowance is
    exactly 0.0 at the reserve and rises the moment the deadline is one tick longer. Five places now
    quote that threshold as ``6.5 s`` — ``mcp_server._health_setup_allowance`` (twice),
    ``monitoring.probe_setup_timeout``, ``monitoring.record_health_setup_clamp`` and the runbook's
    §4.1.1a (its arithmetic and the ``TORTOISE_PROBE_SETUP_TIMEOUT`` bullet); ``rg -n '6.5' <those three files>`` is the audit. Every OTHER case here derives the reserve from the constants,
    so moving ``QUOTA_STATE_TIMEOUT`` (3.0) would silently falsify all five while the suite stayed
    green (re-review, P3). This reds instead.

    The consequence the number buys: below the cliff a store answering in milliseconds still comes
    back UNOBSERVED, which is why the counter counts it (see the third/fourth causes in
    ``record_health_setup_clamp``).
    """
    reserve = mcp_server._health_tail_reserve()
    assert mcp_server._health_setup_allowance(reserve) == 0.0, (
        f"at the reserve ({reserve}s) the allowance must be a hard 0.0, not "
        f"{mcp_server._health_setup_allowance(reserve)}")
    assert mcp_server._health_setup_allowance(reserve + 0.01) > 0.0, (
        f"one tick above the reserve ({reserve}s) must grant a nonzero allowance — otherwise the "
        f"cliff prose is describing a plateau, not a step")
    assert reserve == pytest.approx(6.5, abs=1e-9), (
        f"the reserve moved to {reserve}s; the cliff quoted in `_health_setup_allowance`,\n"
        f"`probe_setup_timeout`, `record_health_setup_clamp` and the runbook's 4.1.1a all say\n"
        f"6.5 — re-derive those passages, do not just bump this constant")


def test_the_quota_phase_gets_only_what_is_left_of_the_deadline(monkeypatch):
    """The LAST bounded phase is the one that makes the tail bounded (#12482 cycle 5, P2).

    Measured before the fix: remaining 2.0 s -> the quota phase spent its full 3.00 s and the call
    was refused. Its budget is now the deadline's remaining life minus the delivery margin, so a
    slow probe cannot be followed by three more seconds of quota work on a spent dispatch.
    """
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    token = mcp_server._WAIT_BOUND_DEADLINE.set(time.monotonic() + 2.0)
    try:
        seen = _capture(monkeypatch)
        report = mcp_server.tortoise_health()
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    budget = report["quota"]["timeout"]
    assert budget is not None, "the quota phase was given no budget under a live deadline"
    assert budget <= 2.0, f"the quota phase may spend {budget}s of a 2.0 s deadline"
    assert budget > 0.0
    assert seen["setup_timeout"] == 0.0, "a 2.0 s deadline must clamp the allowance to zero"


def test_the_quota_phase_never_exceeds_its_own_constant_on_a_healthy_dispatch(monkeypatch):
    """Bounded by the deadline AND capped at `QUOTA_STATE_TIMEOUT` — both, not either (#12482 P2).

    A fast probe and count on a fresh 10.0 s dispatch leave almost the whole bound here (~9 s; the
    exact figure moves with how fast those two return, and matches the sibling figure in
    `_health_quota_budget`), and handing THIS phase all of it held the single quota worker slot for
    that long on a health read whose documented per-phase bound is 3.0 s. The deadline bound alone
    cannot see that: it is satisfied at any remaining value. Only the `min` with the phase's own
    constant keeps the documented figure true.
    """
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    token = mcp_server._WAIT_BOUND_DEADLINE.set(
        time.monotonic() + mcp_auth._TRANSPORT_WAIT_BOUND_S)
    try:
        _capture(monkeypatch)
        report = mcp_server.tortoise_health()
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    assert report["quota"]["timeout"] == pytest.approx(quota.QUOTA_STATE_TIMEOUT, abs=1e-6), (
        "on a fresh dispatch the quota phase was handed more than its own documented bound")


def test_the_graph_count_gets_only_what_is_left_after_the_probe(monkeypatch):
    """The count is the MIDDLE phase: its budget is the deadline minus the probe's ceiling.

    ⛔ At a FULL bound this case proves nothing: `min(1.5, 10 − 5.0 − 0.5)` saturates at
    `GRAPH_SIZE_TIMEOUT`, so a mutant that passed the constant would pass too (fresh review of
    cycle 5 round 2, P2). The deadline here is 3.0 s, where the expression does NOT saturate:
    allowance 0.0, probe ceiling 1.5, so the count gets `3.0 − 1.5 − 0.5 = 1.0`.
    """
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    token = mcp_server._WAIT_BOUND_DEADLINE.set(time.monotonic() + 3.0)
    try:
        seen = _capture(monkeypatch)
        mcp_server.tortoise_health()
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    assert seen["setup_timeout"] == 0.0, "a 3.0 s deadline must clamp the allowance to zero"
    assert seen["graph_size_timeout"] == pytest.approx(
        3.0 - monitoring.PROBE_TIMEOUT - mcp_server._HEALTH_TAIL_MARGIN_S, abs=0.15), (
        "the count's budget is not `remaining − probe ceiling − margin`; a constant would saturate "
        "at the full GRAPH_SIZE_TIMEOUT and hand the count time the deadline does not have"
    )
    assert seen["graph_size_timeout"] < monitoring.GRAPH_SIZE_TIMEOUT


def test_a_spent_deadline_still_hands_every_phase_a_zero_budget(monkeypatch):
    """A 0.0 budget must be a ZERO, never a negative timeout (which would raise in the phase).

    The handler then still answers: `metrics(graph_size_timeout=0.0)` fails the COUNT and reports
    it, and the quota block returns its `unknown` shape — neither is allowed to escape as an
    exception, which would lose the diagnosis as well as the answer.
    """
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    token = mcp_server._WAIT_BOUND_DEADLINE.set(time.monotonic())
    try:
        seen = _capture(monkeypatch)
        report = mcp_server.tortoise_health()
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    assert seen["setup_timeout"] == 0.0
    assert seen["graph_size_timeout"] == 0.0
    assert report["quota"]["timeout"] == 0.0
    assert isinstance(report, dict) and report.get("status") is not None


def test_an_unclamped_call_leaves_both_later_phases_at_their_own_budgets(monkeypatch):
    """``None`` must mean "nothing is enforcing a bound", never ``0.0`` — which would skip them."""
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    seen = _capture(monkeypatch)

    report = mcp_server.tortoise_health()

    assert "graph_size_timeout" not in seen, (
        "an unclamped call passed a graph-count budget; `metrics`' own default is the right "
        "answer when nothing is enforcing a deadline")
    assert report["quota"]["timeout"] is None


def test_a_raised_knob_cannot_widen_the_mcp_window(monkeypatch):
    """``TORTOISE_PROBE_SETUP_TIMEOUT`` is a CEILING over MCP, not the spend (#12482 cycle 5, P2).

    The knob is documented as the remedy for a large/cold graph, so an operator raising it must not
    be led to expect a longer MCP window: the tool still spends at most ``bound - reserve``. Without
    this pin, a future change that re-read the knob past the clamp would restore #10749 on exactly
    the deployments that need the clamp most — and ``test_mcp_tortoise_health_honors_env_override``
    would stay green, because it calls the tool with no seam bound (``remaining is None``), the one
    arm where the knob is unclamped.
    """
    monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", "60")
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    token = mcp_server._WAIT_BOUND_DEADLINE.set(time.monotonic() + _BOUND)
    try:
        seen = _capture(monkeypatch)
        report = mcp_server.tortoise_health()
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    assert monitoring.probe_setup_timeout() == 60.0
    assert seen["setup_timeout"] == pytest.approx(_BOUND - _reserve(), abs=0.2), (
        "a 60 s knob reached the handler: the transport bound is the ceiling, not the knob"
    )
    assert report["setup_timeout_clamped"] is True


def test_the_seam_does_not_leak_its_deadline_into_the_next_dispatch(monkeypatch):
    """The `finally: reset` is claimed in prose — pin it with a SECOND dispatch (#12482 P3).

    ⛔ The first version of this case asserted the caller's ContextVar after `asyncio.run` and
    COULD NOT FAIL: `asyncio.run` gives the coroutine its own Task with a COPY of the context, so a
    leak inside is invisible outside with or without the reset (fresh review of cycle 5 round 2,
    measured: the inner task read a leaked 123.0 while the caller still read None). The leak that
    matters is BETWEEN two dispatches on the same task, so both are awaited inside ONE
    `asyncio.run` and the second asserts it started clean. Without the reset the second sees its
    predecessor's deadline, clamps to ~0.0 and answers `unknown` for the life of that task.
    """
    seen: list = []

    async def fake_inner(name, arguments, **kwargs):
        seen.append(mcp_server._mcp_remaining_wait_bound() or 0.0)
        return {"ok": True}

    monkeypatch.setattr(mcp_server, "_original_call_tool", fake_inner)
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    monkeypatch.setattr(mcp_server, "_wait_bound_refused", lambda: False)
    monkeypatch.setattr(mcp_server, "_hold_mcp_dispatch_after_request", lambda *a, **k: None)

    import asyncio

    async def two_dispatches():
        await mcp_server._await_under_mcp_wait_bound(
            "tortoise_health", {}, version=None, task_meta=None)
        # The gap the reset exists for: a second dispatch on the SAME task, after the first
        # returned. Nothing between them may carry a deadline forward.
        seen.append(mcp_server._mcp_remaining_wait_bound())
        assert mcp_server._WAIT_BOUND_DEADLINE.get() is None, (
            "the first dispatch leaked its deadline into this task's context"
        )

    asyncio.run(two_dispatches())

    assert seen[0] == pytest.approx(_BOUND, abs=1.0), (
        "the first dispatch did not bind the deadline the tool body reads")
    assert seen[1] is None, (
        "a second dispatch in the same task inherited the first one's deadline — every later "
        "health call in that task would clamp to 0.0 and answer `unknown`"
    )


def test_a_clamped_unobserved_probe_is_not_reported_as_a_failed_one():
    """`graph_size_error` must not say "db probe failed" for a probe never given time (#12482 P3).

    The clamp makes ``db.ok is None`` / ``observed is False`` the ROUTINE shape on a slow store, and
    that arm used to report ``"…: db probe failed"`` on the same payload as
    ``setup_timeout_clamped`` — telling a consumer the store was broken, which is the exact reading
    the clamp marker exists to prevent. Driven through the REAL ``monitoring.metrics``.
    """
    class SlowColdStart:
        def _get_proj(self):
            time.sleep(0.4)
            raise AssertionError("the probe should have given up before this returned")

        def _cache_meta(self, *a, **k):
            return None

    report = monitoring.metrics(sdk=SlowColdStart(), setup_timeout=0.0)

    assert report["db"]["observed"] is False
    assert report["status"] == "unknown"
    assert "db probe failed" not in (report["graph_size_error"] or ""), (
        f"a clamped (never-observed) probe still reads as a failure: {report['graph_size_error']!r}"
    )
    assert "not observed" in (report["graph_size_error"] or "")
    assert "allowance clamped" in (report["graph_size_error"] or "")


def test_a_clamped_count_budget_actually_starves_the_real_count(monkeypatch):
    """`graph_size_timeout` must reach the REAL count, and a starved count is not a generic error.

    ⛔ Every other count-budget assertion in this file reads kwargs captured from a STUBBED
    `monitoring.metrics`, so replacing the seam — `GRAPH_SIZE_TIMEOUT if graph_size_timeout is None
    else graph_size_timeout` — with the bare constant left all 40 cases green (#12482 fresh review,
    P2). This drives the REAL `metrics()` with an OBSERVED probe, so the count phase is actually
    reached. Both halves are needed: a 0.0 budget must fail the count WITHOUT moving the generic
    `errors` counter (⛔ no aggregate counts it — `health_setup_clamped` requires an UNOBSERVED probe
    and the count is only reached when the probe was OBSERVED, so `graph_size_error` is its only
    signal, filed as #12600; and a healthy store must not raise a "total errors" alert), while an
    overrun of the count's OWN full budget still counts.
    """

    class ObservedProbe:
        """A store whose probe ANSWERS, so `metrics` proceeds to the graph count."""

        def _get_proj(self):
            proj = MagicMock()
            proj.g.query.return_value = MagicMock(result_set=[[1]])
            return proj

        def taxonomy(self):
            return {"Point": 7}

    counted: list[int] = []
    monkeypatch.setattr(monitoring, "record_error", lambda: counted.append(1))
    clamps_before = monitoring.health_setup_clamped_count()

    report = monitoring.metrics(sdk=ObservedProbe(), setup_timeout=5.0, graph_size_timeout=0.0)

    assert report["db"]["ok"] is True, (
        "the probe never answered, so the COUNT phase was not reached and this test would pass "
        "for the wrong reason")
    assert report["graph_size"] == 0
    # ⛔ THE FRAGMENT STARTS AT `graph_size count` ON PURPOSE (fresh review, P2): `docs/infra-runbook.md`
    # §4.1.1a tells an operator to match on `count exceeded its`, "because there is no bare
    # `graph_size exceeded its` in the payload" — and every assertion here used to start one token
    # later, at `exceeded its`, which BOTH spellings satisfy. Measured: mutating the producer to the
    # pre-fix bare `graph_size exceeded its` left this file 44/44 green, so the one anchor the
    # runbook relies on was pinned by nothing. Shortening these literals re-opens that gap.
    assert "graph_size count exceeded its 0.0s budget" in (
        report["graph_size_error"] or ""), (
        f"a 0.0 count budget did not fail the count: {report['graph_size_error']!r} — a constant "
        "passed instead of the seam would have counted the graph here")
    assert counted == [], (
        "a count CLAMPED to 0.0 by the transport deadline moved the generic `errors` counter — "
        "the store may be perfectly healthy, and NO aggregate counts this shape "
        "(`health_setup_clamped` requires an UNOBSERVED probe; `graph_size_error` is its only "
        "signal — #12600)")

    # The other half of the same gate: with NO caller budget the count's own full constant is the
    # budget, and overrunning THAT is a real error. GRAPH_SIZE_TIMEOUT is shrunk so the overrun is
    # provable in milliseconds rather than in 1.5 s of wall clock.
    class StalledCount(ObservedProbe):
        def taxonomy(self):
            time.sleep(0.5)
            return {"Point": 7}

    monkeypatch.setattr(monitoring, "GRAPH_SIZE_TIMEOUT", 0.2)
    unclamped = monitoring.metrics(sdk=StalledCount(), setup_timeout=5.0)

    assert "graph_size count exceeded its 0.2s budget" in (
        unclamped["graph_size_error"] or "")
    assert counted == [1], (
        "an overrun of the count's OWN full budget stopped counting as an error — the gate is "
        "supposed to drop only the clamp-starved case")

    # ⛔ The predicate is "was the budget REDUCED", not "was a kwarg passed" (fresh review of
    # round 2, P1). On a full 10.0 s bound the handler passes the constant ITSELF — allowance 3.5,
    # probe ceiling 5.0, `10 − 5.0 − 0.5` saturates at `GRAPH_SIZE_TIMEOUT` — so a gate keyed on
    # `graph_size_timeout is not None` silently stopped counting real overruns on the very tool
    # this change exists for. Both sides are pinned here so neither wording can return.
    counted.clear()
    explicit_full = monitoring.metrics(sdk=StalledCount(), setup_timeout=5.0,
                                      graph_size_timeout=monitoring.GRAPH_SIZE_TIMEOUT)
    assert "graph_size count exceeded its 0.2s budget" in (
        explicit_full["graph_size_error"] or "")
    assert counted == [1], (
        "an EXPLICIT full-constant budget (exactly what the handler passes at a full transport "
        "bound) overran and did not count — the gate keys on the kwarg's presence instead of on "
        "the budget having been reduced")

    counted.clear()
    reduced = monitoring.metrics(sdk=ObservedProbe(), setup_timeout=5.0,
                                graph_size_timeout=0.1)
    assert "graph_size count exceeded its 0.1s budget" in (
        reduced["graph_size_error"] or "")
    assert counted == [], (
        "a budget the deadline REDUCED below the constant still moved the generic `errors` "
        "counter — NO aggregate counts this shape (`health_setup_clamped` requires an UNOBSERVED "
        "probe, and this count is only reached when the probe was OBSERVED), so "
        "`graph_size_error` is its only signal (#12600)")
    assert monitoring.health_setup_clamped_count() == clamps_before, (
        "the clamp counter moved for a STARVED COUNT. It cannot legitimately do so: it requires an "
        "UNOBSERVED probe (`observed is False` + the setup spelling) while this count is only "
        "reached when the probe was OBSERVED with `ok is True`. ⛔ Which means the docs must not "
        "claim `health_setup_clamped` covers this shape — nothing in aggregate does, and the "
        "per-call `graph_size_error` is its only signal (fresh review, round 4)")


def test_a_backlog_refusal_on_a_clamped_call_names_capacity_not_the_clamp(monkeypatch):
    """The parenthetical must agree with the counter on the very same payload (#12482
    fresh review, P2).

    A clamp is applied on essentially EVERY seam-served call (`remaining <= 10.0` makes
    `setup_timeout < probe_setup_timeout()` true), so testing the clamp comparison first labelled a
    saturated worker's refusal **"allowance clamped"** and sent an operator to #12481 for a
    capacity problem — while `record_health_setup_clamp`, which gates on the same spelling, did NOT
    count that refusal. Driven through the REAL `monitoring.metrics` because the vocabulary lives
    there; `probe_db` is stubbed so the two unobserved spellings can be compared side by side.
    """
    backlog = {"ok": None, "observed": False, "latency_ms": 0.0,
               "error": "probe worker backlog full (submission refused)"}
    starved = {"ok": None, "observed": False, "latency_ms": 0.0,
               "error": f"{monitoring._PROBE_SETUP_TIMEOUT_MSG}0.00s"}

    monkeypatch.setattr(monitoring, "probe_db", lambda *a, **k: dict(backlog))
    refused = monitoring.metrics(sdk=object(), setup_timeout=0.0)   # 0.0 < the knob ⟹ clamp arm
    assert "refused the submission" in (refused["graph_size_error"] or ""), (
        f"a saturated worker's refusal was not named: {refused['graph_size_error']!r}")
    assert "allowance clamped" not in (refused["graph_size_error"] or ""), (
        "a BACKLOG REFUSAL on a clamped call read 'allowance clamped' — that sends the operator "
        "to #12481 for a capacity problem, and contradicts the counter on the same payload")

    monkeypatch.setattr(monitoring, "probe_db", lambda *a, **k: dict(starved))
    clamped = monitoring.metrics(sdk=object(), setup_timeout=0.0)
    assert "allowance clamped" in (clamped["graph_size_error"] or ""), (
        "the clamp arm stopped naming the clamp for a probe that really was starved: "
        f"{clamped['graph_size_error']!r}")


def test_a_low_knob_IS_the_spend_and_says_so(monkeypatch):
    """Below the ceiling the knob is the SPEND, so "raising it buys nothing" would be false.

    Measured with the documented minimum (`TORTOISE_PROBE_SETUP_TIMEOUT=1.5`) on a full 10.0 s
    bound: the allowance RECEIVES the knob verbatim (1.5 s < `bound − reserve` = 3.5 s), so no
    clamp marker fires and there is nothing for the transport bound to have taken away. The
    docstrings now say the knob "never widens past `bound − reserve`" rather than "never reaches
    the spend", and this case pins the arm the seam can actually reach with a legal low knob.
    """
    monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", str(monitoring.PROBE_SETUP_TIMEOUT_MIN))
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    token = mcp_server._WAIT_BOUND_DEADLINE.set(
        time.monotonic() + mcp_auth._TRANSPORT_WAIT_BOUND_S)
    try:
        seen = _capture(monkeypatch)
        report = mcp_server.tortoise_health()
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    assert seen["setup_timeout"] == pytest.approx(monitoring.PROBE_SETUP_TIMEOUT_MIN), (
        "a knob below `bound − reserve` must be handed over verbatim, not clamped further")
    assert report.get("setup_timeout_clamped") is not True, (
        "nothing was taken away, so the clamp marker must be absent — an operator reading it "
        "would conclude the bound is the constraint when the knob is")


def test_an_UNCLLAMPED_allowance_is_not_labelled_clamped_either(monkeypatch):
    """The third arm: an allowance was passed, but no clamp was applied (#12482 cycle 5, round 3 P2).

    ⛔ The round-2 fix keyed the wording on the ARGUMENT BEING PRESENT, which is true for every
    caller that passes any allowance — the unclamped one included. Measured with a LEGAL
    `TORTOISE_PROBE_SETUP_TIMEOUT=1.5` (the documented minimum) and a cold start slower than the
    knob: the handler applied NO clamp (``setup_timeout_clamped`` absent, counter flat) yet the
    message said "allowance clamped", which is the marker an operator is taught to look for and
    which was not on the payload. The wording now compares the allowance to the knob, the way the
    handler's own decision does.
    """
    monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", str(monitoring.PROBE_SETUP_TIMEOUT_MIN))

    class SlowColdStart:
        def _get_proj(self):
            time.sleep(monitoring.PROBE_SETUP_TIMEOUT_MIN + 0.6)   # outlives the UNCLAMPED knob
            raise AssertionError("the probe should have given up before this returned")

        def _cache_meta(self, *a, **k):
            return None

    allowance = monitoring.probe_setup_timeout()      # exactly the knob: nothing was taken away
    report = monitoring.metrics(sdk=SlowColdStart(), setup_timeout=allowance)

    assert report["db"]["observed"] is False
    error = report["graph_size_error"] or ""
    assert "not observed" in error
    assert "clamped" not in error.replace("not clamped", ""), (
        f"an allowance equal to the knob was labelled as clamped: {error!r} — the operator is sent "
        "to #12481 for a store that never answered inside its FULL budget")
    assert "not clamped" in error


def test_the_REAL_hop_hands_the_tool_body_a_clamped_allowance(monkeypatch):
    """END TO END ACROSS THE THREAD HOP — the mechanism the whole fix stands on (#12482, round 4).

    ⛔ The first cut of this case was LOOP-ONLY and its docstring said otherwise. It stubbed
    ``_original_call_tool`` with a coroutine that called ``tortoise_health()`` directly, so the sync
    body ran on the event loop: measured, ``dispatch coroutine thread id == TOOL BODY thread id ==
    MainThread`` — no hop crossed (fresh review, round 4 P3). It did pin ``ensure_future``'s context
    copy, which is why it reddened on a ``None`` deadline, but NOT the thing the source comment at
    ``mcp_server._await_under_mcp_wait_bound`` warns about.

    The mechanism is worth pinning because it is silent: the tool bodies are SYNC callables and
    fastmcp runs them via ``anyio.to_thread.run_sync``, which is what propagates the ContextVar onto
    the worker thread. A dispatch change to a bare ``loop.run_in_executor`` would NOT copy the
    context (cpython#78195), the body would read ``None``, #10749's 26 s tail would come back and
    every other case in this file would stay green — they read the ContextVar on the loop.

    So this case now awaits the REAL ``_original_call_tool`` (no stub) and asserts (a) the body ran
    on a DIFFERENT thread than the caller, and (b) what it read there was the clamped allowance.
    """
    import asyncio
    import threading

    seen: dict = {}

    def fake_metrics(**kwargs):
        seen.update(kwargs)
        seen["thread"] = threading.get_ident()
        return {"status": "ok", "db": {"ok": True, "observed": True, "latency_ms": 50.0}}

    monkeypatch.setattr(monitoring, "metrics", fake_metrics)
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    monkeypatch.setattr(mcp_server, "_wait_bound_refused", lambda: False)
    monkeypatch.setattr(mcp_server, "_hold_mcp_dispatch_after_request", lambda *a, **k: None)
    monkeypatch.setattr(mcp_server, "_check_mcp_op_budget", _async_noop)
    monkeypatch.setattr(mcp_server, "_enforce_mcp_tool_scope", lambda *a, **k: None)
    monkeypatch.setattr(mcp_server, "maybe_record_mcp_read", lambda *a, **k: None)
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: object())
    monkeypatch.setattr(mcp_server, "_org_quota_block",
                        lambda sdk, **kwargs: {"state": "unknown", **kwargs})
    monkeypatch.setattr(mcp_server, "_emit_mcp_tool_call_telemetry", lambda *a, **k: None)

    main_thread = threading.get_ident()
    raw = asyncio.run(mcp_server._wrapped_call_tool("tortoise_health", {}))
    # The real chain returns FastMCP's ToolResult; unwrap to the body's own JSON.
    if hasattr(raw, "content"):
        import json
        result = json.loads(raw.content[0].text)
    else:
        result = raw

    assert isinstance(result, dict) and result.get("status") is not None
    assert "setup_timeout" in seen, "the tool body never reached monitoring.metrics()"
    assert seen["thread"] != main_thread, (
        "the tool body ran on the event loop — this case is no longer exercising the thread hop, "
        "which is the ONLY thing that propagates the seam's deadline into a sync tool body"
    )
    assert seen["setup_timeout"] < monitoring.probe_setup_timeout(), (
        "on the worker thread the body read the UNCLAMPED allowance: the deadline did not survive "
        "the hop, which restores #10749's 26 s tail inside a 10 s bound")
    assert result["setup_timeout_clamped"] is True


async def _async_noop(*args, **kwargs):
    return None


def test_an_unobserved_probe_with_no_allowance_at_all_is_not_labelled_clamped():
    """``serve_health`` calls ``metrics()`` with no ``setup_timeout`` — there is nothing to clamp.

    ⛔ The clamped wording must be CONDITIONAL on the seam (#12482 cycle 5, round 2 P2). On the
    standalone ``/health`` the same ``observed=False`` shape arrives from a slow cold start inside
    the shared 1.5 s budget, or from a #2850 saturated-backlog refusal, and naming a clamp there
    told an operator to read ``setup_timeout_clamped`` for a payload that never carries it.
    """
    class SlowColdStart:
        def _get_proj(self):
            time.sleep(monitoring.PROBE_TIMEOUT + 0.6)   # outlives the DEFAULT per-attempt budget
            raise AssertionError("the probe should have given up before this returned")

        def _cache_meta(self, *a, **k):
            return None

    report = monitoring.metrics(sdk=SlowColdStart(), setup_timeout=None)

    assert report["db"]["observed"] is False
    error = report["graph_size_error"] or ""
    assert "not observed" in error
    assert "clamped" not in error, (
        f"a surface with no allowance reported a clamp it cannot apply: {error!r}")


def test_a_backlog_refusal_is_unobserved_but_is_not_the_clamp_s_costing(monkeypatch):
    """`observed=False` ALONE is not "the clamp cost the answer" — the spelling is part of the gate.

    A saturated single-slot worker REFUSES the submission (`_WorkerBacklogFull`) and
    `monitoring._probe_once` reports that as ``observed=False`` with the WORKER'S OWN message, not
    the setup-timeout spelling (monitoring.py, the backlog arm of the cold-start probe). The store
    there may be perfectly fast: the identical call would come back ``unknown`` under the unclamped
    20.0 s allowance too, so the clamp cost nothing.

    Counting it would make the aggregate lie in the one direction that changes the remedy — an
    operator reading a rising ``health_setup_clamped`` would narrow the allowance when the real fix
    is probe capacity. That is why the gate also requires ``_PROBE_SETUP_TIMEOUT_MSG``.
    """
    backlog = {
        "status": "unknown",
        "db": {"ok": None, "observed": False, "latency_ms": 0.0,
               "error": "probe worker backlog full (submission refused)"},
    }
    baseline = monitoring.health_setup_clamped_count()

    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    token = mcp_server._WAIT_BOUND_DEADLINE.set(time.monotonic() + _BOUND)
    try:
        _capture(monkeypatch, report=backlog)
        report = mcp_server.tortoise_health()
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    assert report["setup_timeout_clamped"] is True, (
        "the clamped arm was not exercised, so this test would pass for the wrong reason"
    )
    assert monitoring.health_setup_clamped_count() == baseline, (
        "a BACKLOG REFUSAL (unobserved, but not because the allowance ran out) incremented the "
        "degradation counter — the aggregate then rises while the clamp cost nothing, and the "
        "operator is sent to the wrong remedy"
    )


def test_an_overrun_setup_timeout_is_unobserved_and_does_count(monkeypatch):
    """The other half of the same gate: an UNOBSERVED probe whose error IS the setup spelling counts.

    Without this the spelling condition could be over-tightened (e.g. to an equality test, or to
    the genuine-overrun arm only) and the counter would silently stop counting the degradation it
    exists for — a guard that suppresses the signal it was added to protect.
    """
    timed_out = {
        "status": "unknown",
        "db": {"ok": None, "observed": False, "latency_ms": 0.0,
               "error": f"{monitoring._PROBE_SETUP_TIMEOUT_MSG}3.49s"},
    }
    baseline = monitoring.health_setup_clamped_count()

    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    token = mcp_server._WAIT_BOUND_DEADLINE.set(time.monotonic() + _BOUND)
    try:
        _capture(monkeypatch, report=timed_out)
        mcp_server.tortoise_health()
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    assert monitoring.health_setup_clamped_count() == baseline + 1, (
        "an unobserved probe whose error is the setup-timeout spelling did NOT increment the "
        "counter — the spelling gate is too tight and the degradation it exists to report is lost"
    )


@pytest.mark.asyncio
async def test_the_seam_binds_the_REMAINING_bound_not_a_fresh_one(monkeypatch):
    """The seam must bind ``now + REMAINING``, not ``now + _TRANSPORT_WAIT_BOUND_S`` (cycle-7, P3).

    The no-arrival-stamp test cannot tell those apart, because on that arm
    ``remaining == _TRANSPORT_WAIT_BOUND_S`` exactly — so binding a FRESH full bound instead of the
    request's leftover one stayed GREEN under every test in this file (measured: the mutation
    ``_WAIT_BOUND_DEADLINE.set(monotonic() + _TRANSPORT_WAIT_BOUND_S)`` left the whole file passing).
    That regression re-creates the aged-request overrun the production case measured — ~3.8 s of the
    bound is already gone before the tool body runs — and would silently restore #10749 there.

    This drives the REAL seam with a stamp 3.0 s in the past, so a fresh-full-bound binding lands
    3.0 s later than the deadline the seam is about to enforce on the caller.
    """
    seen: dict = {}

    async def fake_inner(name, arguments, **kwargs):
        seen["deadline"] = mcp_server._WAIT_BOUND_DEADLINE.get()
        return {"ok": True}

    monkeypatch.setattr(mcp_server, "_original_call_tool", fake_inner)
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: time.monotonic() - 3.0)
    monkeypatch.setattr(mcp_server, "_wait_bound_refused", lambda: False)
    monkeypatch.setattr(mcp_server, "_hold_mcp_dispatch_after_request", lambda *a, **k: None)

    before = time.monotonic()
    await mcp_server._await_under_mcp_wait_bound(
        "tortoise_health", {}, version=None, task_meta=None)
    after = time.monotonic()

    assert seen["deadline"] is not None, "the seam bound no deadline at all on the aged-stamp arm"
    assert seen["deadline"] == pytest.approx(before + _BOUND - 3.0, abs=0.5), (
        "the seam bound a fresh full transport bound instead of the ~7.0 s this request has LEFT; "
        "the tool body then clamps under a deadline the seam will not honour, and the -32009 "
        "refusal returns silently on exactly the aged requests #10749 measured"
    )
    assert seen["deadline"] < after + _BOUND, (
        "the bound deadline extends beyond a full bound from now — elapsed time was not deducted"
    )


def test_the_reserve_counts_every_phase_that_can_still_spend_time():
    """The PLANNING reserve must match the implementation's spelling, phase for phase.

    ``_health_setup_allowance`` computes ``remaining − _health_tail_reserve()``, and this file
    spells the same set independently: if the implementation drops or retunes a phase, the two
    disagree and every prose figure derived from the reserve is stale — which is the defect class
    that survived two review rounds here ("~6.5 s residual" then "~2 s residual": both wider than
    the code). Comparing the two spellings is what makes the guard falsifiable; measured, dropping
    ``PROBE_TIMEOUT`` from the implementation reds it.

    ⛔ It does NOT claim a residual window. An earlier version of this case asserted the tail could
    still overrun below the reserve — measured false after the per-phase bounds landed: at a 0.0
    allowance ``probe_db`` returns from its own setup branch without submitting the query, so the
    reserved ``PROBE_TIMEOUT`` is not spendable (fresh review of cycle 5 round 2, P3; the tool
    answers at 2.5 / 1.5 / 0.6 s of remaining deadline). The bound is a planning figure.
    """
    assert mcp_server._health_tail_reserve() == pytest.approx(_reserve()), (
        "the implementation's reserve no longer matches the independently-spelled phase set "
        "(PROBE_TIMEOUT + GRAPH_SIZE_TIMEOUT + QUOTA_STATE_TIMEOUT + margin); a phase was dropped "
        "or retuned, so every figure derived from it — the docstring's, the runbook's — is stale"
    )
    # ⛔ `_reserve() - margin == PROBE + GRAPH + QUOTA` is algebra on this file's OWN helper
    # (`_reserve()` is DEFINED as that sum plus the margin), so it cannot detect a dropped phase —
    # the assertion above does, from the implementation's value (fresh review, round 5 P3). What is
    # worth pinning here is that the phases the impl reserves for are the ones that still cost time.
    unavoidable = mcp_server._health_tail_reserve() - mcp_server._HEALTH_TAIL_MARGIN_S
    assert unavoidable == pytest.approx(
        monitoring.PROBE_TIMEOUT + monitoring.GRAPH_SIZE_TIMEOUT + quota.QUOTA_STATE_TIMEOUT
    ), "the implementation's reserve no longer equals the three bounded phases plus the margin"
    for _name, _phase in (("PROBE_TIMEOUT", monitoring.PROBE_TIMEOUT),
                          ("GRAPH_SIZE_TIMEOUT", monitoring.GRAPH_SIZE_TIMEOUT),
                          ("QUOTA_STATE_TIMEOUT", quota.QUOTA_STATE_TIMEOUT)):
        assert _phase > 0.0, (
            f"{_name} was zeroed — it no longer costs time, so it no longer belongs in the "
            "reserve and the docstring's version of it must be re-derived"
        )
    # A sub-reserve deadline floors the allowance at 0.0, never negative.
    assert mcp_server._health_setup_allowance(unavoidable - 0.1) == 0.0


def test_an_unattributable_unobserved_probe_does_not_count(monkeypatch):
    """No `error` at all ⇒ the counter cannot attribute the cause, so it does not count.

    The gate is `observed is False` AND `error` starting with `_PROBE_SETUP_TIMEOUT_MSG`, and
    `str(db.get("error") or "")` makes a MISSING or None error an empty string, which matches
    neither the setup spelling nor anything else. That is deliberate fail-closed attribution: an
    unattributable unobserved probe must not be recorded as "the clamp cost the answer", because
    that is the counter's whole justification.

    ⚠️ No case exercised this arm before now — every other fixture here supplies an error, so the
    branch was reachable only in production.
    """
    no_error = {"status": "unknown",
                "db": {"ok": None, "observed": False, "latency_ms": 0.0}}
    baseline = monitoring.health_setup_clamped_count()

    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: None)
    token = mcp_server._WAIT_BOUND_DEADLINE.set(time.monotonic() + _BOUND)
    try:
        _capture(monkeypatch, report=no_error)
        report = mcp_server.tortoise_health()
    finally:
        mcp_server._WAIT_BOUND_DEADLINE.reset(token)

    assert report.get("setup_timeout_clamped") is True, (
        "the clamped arm was not exercised, so this test would pass for the wrong reason"
    )
    assert monitoring.health_setup_clamped_count() == baseline, (
        "an unobserved probe with NO error was attributed to the clamp — the gate must fail "
        "closed on attribution rather than assume the clamp caused it"
    )
