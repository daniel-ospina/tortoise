"""#13526 — the refusal's FACTS must reach ``health.quota``, not a 160-char slice.

WHY THIS EXISTS. ``health.quota`` is the one machine-readable field the write
mandate tells a lane to read before it writes (``AGENTS.md`` → the graph rule).
Measured on the deployed graph, 2026-11-24::

    {"resource": "points", "state": "closed",
     "points_used": null, "points_limit": null, "points_remaining": null,
     "error": "the ruled-dimension allowance gate raises for this write
               (QuotaExceededError: Team graph_storage allowance reached
               (102 MB on the pro plan, used 329). Upgrade your plan to increase
               it, or reduce what you hold resident. The hard stop is 15"}

Three defects, all of them in the REPORT and none of them in the meter:

1. **The threshold is WRONG.** The gate composes ``"The hard stop is 153 MB
   measured (1.5x the cap) because this figure is an ESTIMATE whose sampling
   error is UNMEASURED …"``. ``str(exc)[:160]`` lands inside the figure, so the
   block published **15** where the gate said **153** — an order of magnitude
   low, with nothing marking that anything had been removed.
2. **The dimension is misnamed.** The refusal came from ``graph_storage`` (MB);
   the block's only resource was ``"points"`` (the door's resource), so a reader
   goes looking at node counts for a byte cap.
3. **The figures are ``null``** although ``QuotaExceededError`` carried
   ``resource``/``used``/``limit``/``estimated`` all along — ``quota.py``'s own
   docstring (#4614) says a refusal that "exists only as prose is not a
   distinguishable state", and then this arm threw the fields away.

The cap itself is NOT touched here, and neither is the estimate rule: the meter
is a documented LOWER BOUND and the 102 MB pro figure is a derived pricing
number whose revision is the owner's (``product/pricing.json``). What is fixed
is what the product TELLS a caller about the refusal it just made.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tortoise import pricing
from tortoise.allowance_gate import (
    DIMENSIONS,
    MeterReading,
    enforce_allowances,
    reset_signal_state_for_tests,
)
from tortoise.quota import QuotaExceededError, org_quota_state, quota_state_refused

ORG = "team-13526"

#: The pro-tier cap and the banded hard stop the gate composes its refusal at,
#: READ FROM THE ARTIFACT at runtime — a restated literal would leave this file
#: green through a pricing revision while asserting a threshold the deployed gate
#: no longer composes, on the exact field this change exists to make truthful.
PRO_CAP_MB = float(pricing.storage_limits("pro")["max_graph_storage_mb"])

#: The MULTIPLIER is read from the gate's own band, for the same reason as the
#: cap and one the cap's derivation does not cover (fresh review of #13526): the
#: gate composes its compared threshold as ``allowance * cap_band.refuse_at``
#: (``allowance_gate`` :1736), so a restated ``1.5`` here would keep asserting
#: 153 through a band revision and red this file over a CORRECT change — a false
#: red on the report layer this suite exists to make truthful.
_STORAGE_BAND = next(d for d in DIMENSIONS if d.dimension == "graph_storage")
PRO_HARD_STOP_MB = PRO_CAP_MB * _STORAGE_BAND.cap_band.refuse_at

#: The figure measured on the deployed graph when the defect was found.
MEASURED_USED_MB = 329.0

#: How old this file's meter reading may be. Generous next to the 300 s offset the
#: fixture uses, and far below `allowance_gate.STALE_AFTER_SECONDS` — the point is
#: to catch a LITERAL date (thousands of seconds old within a day of merging, or
#: negative when future-dated), not to squeeze the offset.
_FRESH_FIXTURE_MAX_AGE_S = 360


def test_the_cap_under_test_is_the_artifact_number_not_a_literal():
    """`PRO_CAP_MB` is derived so the 1.5x arithmetic survives a price revision.

    ⛔ And that derivation has to be PINNED, or the comment claims a protection
    nobody has: replacing the expression with a literal (`= 120.0`) left this
    whole file green (9 passed), because the constant is both the gate input and
    the expectation — self-referential on its own.
    """
    assert float(pricing.storage_limits("pro")["max_graph_storage_mb"]) == PRO_CAP_MB


def _limits(**overrides) -> dict:
    return {"org_id": ORG, "tier": "pro", "max_points": None,
            "max_api_keys": None, "max_sessions": None, "max_users": None,
            "max_graphs": None, "max_graph_storage_mb": PRO_CAP_MB,
            **overrides}


def _single_sample_meter(used: float):
    """A meter whose reading carries NO measured dispersion — the deployed shape.

    ``repeats`` is None, so the gate takes its single-sample arm and composes the
    refusal this test is about (``… the reading comes from a single sample``).

    ⛔ ``measured_at`` is RELATIVE TO THE RUN, never a literal date (#9127, third
    recurrence): this dimension is bounded by ``allowance_gate.STALE_AFTER_SECONDS``
    (24 h), and ``_stale()`` treats a reading past that bound as stale — which
    leaves the cap UNARMED and the gate serving the write, so the refusal this file
    pins stops being composed at all. The literal this replaces
    (``2026-11-24T02:00:00+00:00``) was inside the bound when it merged
    (`da4b76a5851c`, 2026-11-23) and reddened the required `test (e)` shard exactly
    one staleness bound later at 2026-11-25T02:00Z, with no commit in between. A
    fixture that must be FRESH is written as an offset from now, always: the bound
    is the thing under test, so the fixture must not be racing it.

    ⛔ And the RULE is asserted, not just described (P3 on this repair's review):
    a bare docstring left the class open — a future-dated literal
    (``2027-06-01T00:00:00+00:00``) passed all ten tests, because a date in the
    future is a bomb with a later fuse and ``_stale()`` cannot see a negative age.
    So the age is bounded on BOTH sides below. A literal lands outside this window
    within minutes of being written and stays outside forever after (a future-dated
    one is outside immediately); the 300 s offset above cannot drift out from under
    it. The window is deliberately a little wider than the offset (60 s of headroom)
    because the two ``now`` reads sit microseconds apart inside one call.
    """
    measured_at = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    age = (datetime.now(UTC) - datetime.fromisoformat(measured_at)).total_seconds()
    assert -60 <= age <= _FRESH_FIXTURE_MAX_AGE_S, (
        f"this fixture's reading is {age:.0f}s old; it must be written as an offset "
        f"from now (-60s <= age <= {_FRESH_FIXTURE_MAX_AGE_S}s — the window is a "
        "little wider than the 300 s offset it expects), never as a literal date. "
        "A literal is a calendar bomb: past the 24 h bound the cap goes UNARMED, the "
        "gate SERVES the write, and the refusal these tests pin is never composed "
        "— which reddens the required `test (e)` shard one bound after it merges, "
        "with no commit in between (#9127, third recurrence).")
    return lambda _org: MeterReading(
        used=used,
        measured_at=measured_at,
        estimated=True,
        note="SAMPLING ESTIMATE that excludes the HNSW vector index",
    )


def test_the_fixture_reading_is_fresh_and_derived_from_the_clock():
    """The pin that makes this FILE's own class of red un-repeatable (#14646).

    The three cases below assert what the refusal SAYS, so none of them can
    notice that the reading they hand the gate has aged out — they are what
    breaks when it does. That is exactly how this file went red on every PR
    with no commit in between: ``measured_at`` was a literal, and 24 h after
    the date baked into it the gate took its ``stale`` arm (cap UNARMED, write
    SERVED, ``state: "open"``).

    So the FIXTURE is pinned, on two arms, and the second is the one a
    behavioural check cannot replace:

    * behavioural — the reading is inside the gate's own freshness window,
      which is the condition under which the dimension is armed at all;
    * structural — the value that REACHES the gate is clock-derived, so it
      fails at the EDIT instead of a day later.

    ⛔ The structural arm asserts the VALUE THAT REACHES THE GATE, not the
    binding and not the quoting. Two weaker arms came before it, both measured
    open with all 11 tests green:

    * a regex requiring a quote right after `=` — beaten by ``f"2026-…"`` (no
      quote after `=`) and by a module-level ``_FIXTURE_MEASURED_AT`` the
      function's own source never shows;
    * the same parse walking only the local ``measured_at = …`` binding — beaten
      by leaving that binding derived and hardcoding the instant one line below
      it, at the ``MeterReading(measured_at=…)`` construction site. The binding
      stayed a clock call, the in-fixture age check above read the LOCAL and
      stayed green, and only the value handed to the gate was a literal.

    So the arm resolves the ``MeterReading`` keyword through the fixture's own
    scope — plain and annotated assignments, the last one winning as Python
    does, plus one hop into a module-level constant OR a module-level helper
    function — and requires that the resolved expression reach a clock. That is
    the strictest statement the shape allows and it is also the least
    over-strict: an annotated binding and a ``def _now_iso()`` helper are
    ordinary ways to write this correctly, and both are accepted.
    """
    import ast
    import inspect

    from tortoise.allowance_gate import STALE_AFTER_SECONDS

    #: Attribute calls that READ a clock, whichever module carries them. Kept
    #: narrow deliberately: `isoformat`/`fromisoformat`/`strptime`/`strftime` are
    #: FORMATTERS, and a literal formatted at import time must not read as derived.
    #: ⛔ An earlier revision also accepted ANY call whose receiver was the
    #: `datetime`/`time` module, to cover `time.strftime` and
    #: `datetime.fromtimestamp` "without listing them". Measured, that re-opened the
    #: hole from the other side: `datetime.fromisoformat("2026-11-25T12:00:00+00:00")`
    #: — a formatter over a hardcoded instant — satisfied it. Both shapes it was
    #: meant to cover are already reached by the RECURSION below whenever their
    #: argument is a clock (`time.strftime(fmt, time.gmtime())`,
    #: `datetime.fromtimestamp(time.time())`), and when their argument is a literal
    #: they SHOULD be rejected. So the blanket rule is strictly worse than recursion.
    clock_attrs = {"now", "utcnow", "today", "gmtime", "localtime", "time",
                   "time_ns"}

    def walk_resolved(node, scope, seen=frozenset()):
        """Yield every node in `node`, following `scope` Names transitively.

        Resolution is transitive (bounded by `seen`) rather than one hop at the
        root, because the ordinary correct shapes nest it: `measured_at` names a
        module constant, which names the expression — or names a helper FUNCTION
        whose body holds the clock.
        """
        yield node
        if isinstance(node, ast.Name):
            if node.id in scope and node.id not in seen:
                yield from walk_resolved(scope[node.id], scope, seen | {node.id})
            return
        for child in ast.iter_child_nodes(node):
            yield from walk_resolved(child, scope, seen)

    def reaches_a_clock(node, scope):
        """True when `node` READS a clock."""
        return any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr in clock_attrs
            for child in walk_resolved(node, scope))

    def pins_a_calendar(node, scope):
        """True when a clock-derived value is then pinned to a fixed DATE.

        `datetime.now(UTC).replace(year=2026, month=11, day=24, hour=2, …)`
        reads a clock and then discards it: the result is a constant that is
        fresh for one staleness window and reds the file one bound later — the
        #9127 bomb wearing the fix's clothes. So a `replace` naming a calendar
        component is not derived. `replace(microsecond=0)` stays accepted: the
        reading still moves with the clock.

        ⛔ Residual, stated rather than papered over: this is a static guard on
        the PLAUSIBLE shapes, not a proof. String surgery on a derived value
        (slicing an ``isoformat()`` and splicing a date back in) is not detected
        here. The behavioural arm below is the backstop — it rejects any reading
        outside the freshness window, so such a shape reds within one window
        instead of waiting for the calendar, and a future-dated instant is red
        immediately.
        """
        return any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "replace"
            and any(keyword.arg in {"year", "month", "day"}
                    for keyword in child.keywords)
            for child in walk_resolved(node, scope))

    fn = next(
        node for node in ast.parse(inspect.getsource(_single_sample_meter)).body
        if isinstance(node, ast.FunctionDef))

    # Scope, in Python's own resolution order: module globals first, then the
    # function's locals overriding them. The LAST binding of a name wins, which
    # is what a rebind does at runtime — a derived value shadowed by a later
    # literal below it is still a literal.
    scope: dict[str, ast.expr] = {}
    module = ast.parse(inspect.getsource(inspect.getmodule(_single_sample_meter)))
    for body in (module.body, fn.body):
        for stmt in body:
            if isinstance(stmt, ast.Assign):
                targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
                value = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                targets, value = [stmt.target.id], stmt.value
            else:
                continue
            for name in targets:
                scope[name] = value
    # A helper function is a legitimate place for the clock, at module level OR
    # defined inside the fixture; register its body so `def _now_iso(): return
    # datetime.now(UTC)…` resolves wherever it is written.
    for body in (module.body, fn.body):
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope.setdefault(stmt.name, stmt)

    constructions = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "MeterReading"]
    assert constructions, (
        "`_single_sample_meter` no longer constructs a `MeterReading` directly, "
        "so this arm cannot see the reading it exists to pin and would pass "
        "vacuously (#14646). Teach it the new shape rather than deleting it: "
        "what must be pinned is the value the gate is handed.")
    handed = [
        keyword.value
        for construction in constructions
        for keyword in construction.keywords
        if keyword.arg == "measured_at"]
    assert handed, (
        "no `MeterReading(measured_at=…)` keyword found inside "
        "`_single_sample_meter`, so this arm cannot see the reading the gate is "
        "handed and would pass vacuously (#14646)")
    undated = [
        value for value in handed if not reaches_a_clock(value, scope)]
    pinned = [
        value for value in handed
        if reaches_a_clock(value, scope) and pins_a_calendar(value, scope)]
    assert not undated, (
        "the fixture hands the gate a reading that derives from no clock — "
        f"{', '.join(ast.unparse(value) for value in undated)} — so the gate "
        "ages it by "
        f"STALE_AFTER_SECONDS ({STALE_AFTER_SECONDS} s) and will SERVE instead of "
        "arming this dimension 24 h later, reddening this file with no commit in "
        "between (#9127, third recurrence). Derive it from the clock "
        "(`datetime.now(UTC) - timedelta(minutes=5)`) and hand THAT value to "
        "`MeterReading`.")
    assert not pinned, (
        "the fixture READS a clock and then discards it — "
        f"{', '.join(ast.unparse(value) for value in pinned)} pins a calendar "
        "component, so the value the gate receives is a constant that is fresh "
        "for one window and reds this file one "
        f"STALE_AFTER_SECONDS ({STALE_AFTER_SECONDS} s) later with no commit in "
        "between. That is the #9127 bomb wearing the fix's clothes: derive an "
        "OFFSET from now (`datetime.now(UTC) - timedelta(minutes=5)`) instead "
        "of pinning a date with `.replace(...)`.")

    reading = _single_sample_meter(MEASURED_USED_MB)(ORG)
    age = (datetime.now(UTC) - datetime.fromisoformat(reading.measured_at)).total_seconds()
    assert -60 <= age <= _FRESH_FIXTURE_MAX_AGE_S, (
        f"the fixture's RETURNED reading is {age:.0f}s old — outside this "
        f"fixture's own {_FRESH_FIXTURE_MAX_AGE_S}s window. A literal written "
        "TODAY passes the gate's "
        f"{STALE_AFTER_SECONDS}s bound and takes its `stale` arm a bound later "
        "(cap UNARMED, write served), so every case below would assert a refusal "
        "the gate never composes. This arm is the behavioural half and it is "
        "deliberately EXACTLY as tight as the fixture's own assert: the "
        "structural arm above has now been beaten twice, and this one is what "
        "still catches a same-day literal if it is beaten a third time "
        "(#14646)")


def _armed_gate(monkeypatch, used: float) -> None:
    """Make ``org_quota_state``'s gate call run the REAL gate over an armed cap.

    ``org_quota_state`` passes no ``meters=`` (the production path), so the
    over-cap reading is injected at that seam — the same seam D11/D12 use —
    rather than by patching the exception, which would let this test pass on a
    message the gate never composes.
    """
    import tortoise.allowance_gate as ag

    def _gate(limits, *, org_id=None, resource="points", truth_carried=False):
        return enforce_allowances(
            limits, org_id=org_id, resource=resource,
            meters={"graph_storage": _single_sample_meter(used)},
            truth_carried=truth_carried)

    monkeypatch.setattr(ag, "enforce_allowances", _gate)
    reset_signal_state_for_tests()


def test_the_refusal_publishes_the_dimension_and_the_figures_the_gate_compared(
        monkeypatch):
    """RED on origin/main: every one of these five fields was absent or ``None``.

    ``points_used``/``points_limit`` stay ``None`` and that is honest rather than
    a gap — the gate refuses BEFORE the count, so no points figure was ever
    compared, and publishing one would be a number the refusal never used.
    """
    _armed_gate(monkeypatch, MEASURED_USED_MB)

    state = org_quota_state(_limits(), "points")

    assert state["state"] == "closed"
    assert state["points_used"] is None, (
        "no point count was taken — the gate refuses above it")
    assert state["dimension"] == "graph_storage", (
        "the dimension that refused must name itself; the block's `resource` is "
        f"the DOOR ('points'), got dimension={state['dimension']!r}")
    assert state["dimension_used"] == pytest.approx(MEASURED_USED_MB)
    assert state["dimension_limit"] == pytest.approx(PRO_HARD_STOP_MB), (
        "the limit must be the threshold the gate COMPARED (the 1.5x hard "
        f"stop), got {state['dimension_limit']!r}")
    assert state["dimension_unit"] == "MB", (
        "a bare number is not a diagnosis; the unit comes from the declared row")
    assert state["dimension_estimated"] is True, (
        "the meter is a documented ESTIMATE — the block may not present it as "
        "exact")


def test_the_refusal_text_is_not_cut_inside_the_hard_stop_figure(monkeypatch):
    """RED on origin/main: the message ended at ``"The hard stop is 15"``.

    This is the sharpest half of the defect — a bare slice does not shorten a
    refusal, it CORRUPTS it, and the corruption is a plausible-looking number
    (15) in the slot where the threshold belongs (153). Both the figure and the
    ESTIMATE caveat that follows it have to survive, because the caveat is the
    gate's own honesty contract (#13102 P1-3).
    """
    _armed_gate(monkeypatch, MEASURED_USED_MB)

    error = org_quota_state(_limits(), "points")["error"]

    assert f"The hard stop is {PRO_HARD_STOP_MB:g}" in error, (
        "the report cut the gate's refusal inside the figure: published "
        f"{error[-80:]!r}")
    assert "UNMEASURED" in error, (
        "the estimate caveat is the part that stops a reader treating the "
        f"figure as exact; error={error!r}")
    from tortoise.quota import QUOTA_REASON_TRUNCATION_MARK

    assert not error.endswith(QUOTA_REASON_TRUNCATION_MARK), (
        "the gate's own composed refusal must fit inside the bound — a marked "
        "cut here means the bound was lowered below it")


def test_an_over_long_reason_is_cut_with_a_marker_and_never_in_silence():
    """The bound still exists; what it may no longer do is cut without saying so.

    ``str(exc)[:160]`` had no marker, so a reader had no way to know the
    sentence continued. A cut that announces itself is a different fact from a
    complete answer, which is the whole difference this marker carries.
    """
    # Imported HERE, not at module scope: on origin/main these names do not
    # exist, and a collection error would hide the fact that the BEHAVIOURAL
    # cases in this file fail on their own assertions there (they do — see the
    # first two tests, which reproduce the deployed block verbatim).
    from tortoise.quota import (
        QUOTA_REASON_MAX_CHARS,
        QUOTA_REASON_TRUNCATION_MARK,
        bounded_reason,
    )

    long_reason = "x" * (QUOTA_REASON_MAX_CHARS * 3)

    bounded = bounded_reason(long_reason)

    assert bounded.endswith(QUOTA_REASON_TRUNCATION_MARK)
    assert len(bounded) <= QUOTA_REASON_MAX_CHARS + len(
        QUOTA_REASON_TRUNCATION_MARK)
    # A short reason is untouched: no marker on something that was not cut.
    assert bounded_reason("short") == "short"


def test_a_refusal_without_structured_facts_keeps_the_honest_none_shape():
    """A ``QuotaCheckError`` is a FAULT, not a measured refusal — no figures.

    The five keys must still be PRESENT, because a consumer reads
    ``quota["dimension"]`` without first proving which arm built the block; on
    this arm the answer is ``None``, never a fabricated dimension.
    """
    state = quota_state_refused("points", "the gate is unavailable")

    assert state["state"] == "closed"
    for key in ("dimension", "dimension_used", "dimension_limit",
                "dimension_unit", "dimension_estimated"):
        assert key in state, f"{key} must exist on every refusal shape"
        assert state[key] is None


def test_the_facts_come_from_the_exception_the_gate_actually_raised(
        monkeypatch):
    """The fields are the gate's OWN, not a re-count and not a re-parse.

    ``quota.py`` documents that the refusal's numbers "must be the ones THIS
    gate compared"; re-deriving them from the message would be a second source
    of truth for a figure that already has one. Proven by patching the seam with
    an exception whose prose and fields DISAGREE, then asserting the block
    reports the FIELDS.
    """
    import tortoise.allowance_gate as ag

    def _gate(*_a, **_k):
        raise QuotaExceededError(
            "prose that says the hard stop is 999 and used is 1",
            resource="postgres_storage", used=12.5, limit=7.25,
            estimated=None)

    monkeypatch.setattr(ag, "enforce_allowances", _gate)

    state = org_quota_state(_limits(), "points")

    assert state["dimension"] == "postgres_storage"
    assert state["dimension_used"] == 12.5
    assert state["dimension_limit"] == 7.25
    assert state["dimension_estimated"] is None, (
        "an EXACT figure must not be labelled an estimate")
    assert state["dimension_unit"] == "bytes", (
        "the unit is read from the declared row, not assumed to be MB")


# ── shape stability: EVERY arm carries the five keys, not only the refusal ──
#
# ⛔ Why this is a separate test from the four above (review finding, P2): the
# five ruled-dimension keys arrive on a refusal through `quota_state_refused`'s
# keyword arguments, but on every OTHER arm through a `**_NO_DIMENSION_FACTS`
# literal spread — and a consumer doing `quota["dimension"]` cannot know which
# arm built the block. Measured on the change itself: deleting the three spreads
# left `tests/test_13526_quota_refusal_figures.py`,
# `tests/test_11892_health_quota.py` and `tests/test_quota.py` all green (117
# passed) while the documented "present on every state" was false — the false
# pass this test exists to remove. Each arm is driven through its OWN entry.

DIMENSION_KEYS = ("dimension", "dimension_used", "dimension_limit",
                  "dimension_unit", "dimension_estimated")


def _silent_gate(monkeypatch):
    """A gate that ADMITS — so the arms below are reached, not short-circuited."""
    import tortoise.allowance_gate as ag

    monkeypatch.setattr(ag, "enforce_allowances", lambda *a, **k: None)


def _unlimited(_org, _resource, **_kwargs):
    raise AssertionError("an UNLIMITED resource must not be counted")


def test_no_reason_on_this_surface_can_be_cut_without_the_marker(monkeypatch):
    """EVERY reason-producing arm is bounded by the marker helper, not just one.

    ⛔ Measured on the change itself (review finding, P2): reverting any of the
    seven OTHER ``bounded_reason`` call sites in ``quota.py``, or either of the
    two in ``mcp_server.py`` (the limits-unresolved arm and the hand-built
    fallback), back to a bare ``str(exc)[:120]``/``[:160]`` left this file green
    (7 passed) — so the change's thesis was enforced at one arm of NINE. Two arms
    are driven here for BEHAVIOUR (the count arm, and the resolution arm inside
    ``_org_quota_block``), and a source pin holds the rest: a future arm added
    with a bare slice is red on arrival.

    The source pin is intentional and narrow — it scans the two modules this
    change owns, not the repo. ``test_13829_cache_cap_at_fold.py`` pins its own
    seam the same way.
    """
    import pathlib
    import re

    from tortoise.quota import QUOTA_REASON_MAX_CHARS, QUOTA_REASON_TRUNCATION_MARK

    long_text = "x" * (QUOTA_REASON_MAX_CHARS * 2)

    # (a) the COUNT arm — an exception that is not a quota refusal at all.
    _silent_gate(monkeypatch)

    def _explode(*_a, **_k):
        raise RuntimeError(long_text)

    counted = org_quota_state(_limits(max_points=5), "points",
                              count_runner=_explode)
    assert counted["state"] == "closed"
    assert QUOTA_REASON_TRUNCATION_MARK in counted["error"], (
        f"the count arm cut its reason silently: {counted['error'][:80]!r}")
    assert len(counted["error"]) < QUOTA_REASON_MAX_CHARS + 200, (
        "the mark must come with a BOUND, not instead of one")

    # (b) the CARRY arm inside the health block.
    import contextvars

    import tortoise.mcp_server as mcp
    import tortoise.quota as quota_mod
    from tortoise import mcp_auth

    var = contextvars.ContextVar("test_13526_carry", default=None)
    var.set(ORG)
    monkeypatch.setattr(mcp_auth, "_current_org_id", var)
    empty = contextvars.ContextVar("test_13526_limits", default=None)
    monkeypatch.setattr(mcp_auth, "_current_org_limits", empty)

    # The RESOLUTION arm: `_current_org_limits` is empty, so `_compute` resolves,
    # and the resolver raises. That is the arm a broken control-plane read lands
    # in on every request, so it is the one worth holding to the marker.
    def _resolve_fails(*_a, **_k):
        raise RuntimeError(long_text)

    monkeypatch.setattr(quota_mod, "resolve_org_limits", _resolve_fails)
    carried = mcp._org_quota_block()
    assert carried["state"] == "closed"
    assert QUOTA_REASON_TRUNCATION_MARK in carried["error"], (
        f"the carry arm cut its reason silently: {carried['error'][:80]!r}")

    # (c) the source pin: no bare ``str(<exc>)[:N]`` slice may come back.
    root = pathlib.Path(__file__).resolve().parent.parent
    bare = re.compile(r"str\(\s*(?:exc|e|err)\s*\)\s*\[\s*:\s*\d+\s*\]")
    offenders = []
    for rel in ("tortoise/quota.py", "tortoise/mcp_server.py"):
        for lineno, line in enumerate((root / rel).read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if bare.search(code):
                offenders.append(f"{rel}:{lineno}")
    assert not offenders, (
        "a bare exception slice is an unmarked cut on a health surface — use "
        f"`bounded_reason(...)` (or, in the fallback, `_fallback_reason`): {offenders}")


def test_every_state_shape_carries_the_five_dimension_keys(monkeypatch):
    from tortoise.quota import _NO_DIMENSION_FACTS, quota_state_unavailable

    _silent_gate(monkeypatch)
    limits = {"org_id": ORG, "max_points": 5}

    # 1. `open` / UNLIMITED — the arm that returns before counting at all.
    unlimited = org_quota_state({**limits, "max_points": None}, "points",
                                count_runner=_unlimited)
    assert unlimited["state"] == "open"
    assert unlimited["points_limit"] is None

    # 2. `open` / under the cap, and 3. `closed` / AT the cap. ⛔ The counts
    #    SIT ON THE BOUNDARY (limit 5: 4 under, 5 at) rather than far from it:
    #    counts of 1 and 10 left `if used >= limit:` mutable to `>` or to
    #    `>= limit + 4` with this file green (9 passed) — both false-PASS
    #    directions, because `enforce_org_limit` raises at exactly `used >=
    #    limit` (`quota.py:1673`), so the report would publish `open` while
    #    every write at the cap is refused.
    under = org_quota_state(limits, "points",
                            count_runner=lambda *_a, **_k: 4)
    reached = org_quota_state(limits, "points",
                              count_runner=lambda *_a, **_k: 5)
    assert under["state"] == "open", (
        "an under-cap count is an OPEN door; membership in {open, closed} would "
        f"pass a boundary that reports 'closed' early, got {under!r}")
    assert reached["state"] == "closed", (
        f"a count AT the cap is refused by the gate, got {reached!r}")

    # 4. `unknown` / `not_applicable` — the shared unmeasured helper.
    measuring = quota_state_unavailable("points", "the count did not finish")

    for name, block in (("unlimited", unlimited), ("under-cap", under),
                        ("at-cap", reached), ("unavailable", measuring)):
        missing = [k for k in DIMENSION_KEYS if k not in block]
        assert not missing, (
            f"the {name} block is missing {missing} — a consumer reading "
            f"quota['dimension'] must not have to know which arm built it")
        assert block["dimension"] is None, (
            f"the {name} arm measured no dimension; a value here would be a "
            f"refusal this report never made")
    assert set(DIMENSION_KEYS) <= set(_NO_DIMENSION_FACTS)


def test_the_health_block_fallback_arm_carries_the_same_keys(monkeypatch):
    """`_org_quota_block`'s outer `except` spells the keys LITERALLY (#13526).

    That is deliberate — the arm exists for the case where `tortoise.quota`
    could not be imported, so importing its helper would fail with the module it
    stands in for — and the cost of spelling them by hand is that nothing pins
    them unless this test does. Driven through the real entry: a tenant context,
    and the bounded runner raising.
    """
    import contextvars

    import tortoise.mcp_server as mcp
    import tortoise.quota as quota_mod
    from tortoise import mcp_auth

    def _ctx(value):
        var = contextvars.ContextVar("test_13526", default=None)
        var.set(value)
        return var

    def _boom(*_a, **_k):
        raise RuntimeError("the bounded runner escaped its own contract")

    monkeypatch.setattr(mcp_auth, "_current_org_id", _ctx(ORG))
    monkeypatch.setattr(mcp_auth, "_current_org_limits", _ctx(None))
    monkeypatch.setattr(quota_mod, "run_bounded_quota_state", _boom)

    block = mcp._org_quota_block()

    assert block["state"] == "unknown", block
    missing = [k for k in DIMENSION_KEYS if k not in block]
    assert not missing, (
        f"the hand-built fallback is missing {missing} — the documented shape "
        f"breaks exactly on the arm nothing runs in normal operation")
    assert block["dimension"] is None

    # And the arm may not silently CUT: `str(exc)[:160]` was the same defect one
    # line over (review finding, P2), so a long fault has to arrive MARKED.
    from tortoise.mcp_server import _QUOTA_FALLBACK_REASON_MARK

    def _long(*_a, **_k):
        raise RuntimeError("x" * 5000)

    monkeypatch.setattr(quota_mod, "run_bounded_quota_state", _long)
    long_block = mcp._org_quota_block()
    assert long_block["state"] == "unknown"
    assert long_block["error"].endswith(_QUOTA_FALLBACK_REASON_MARK), (
        f"a long fault was cut without saying so: {long_block['error'][-60:]!r}")
    assert len(long_block["error"]) < 3000, "the fallback must stay BOUNDED"

    # ⛔ And it must stay the SAME bound, not a locally-invented tighter one:
    # the comment above `_fallback_reason` claims this test holds the duplicate
    # to its original (review finding, P2 — setting `_QUOTA_FALLBACK_REASON_MAX`
    # to 100 left everything green).
    import tortoise.mcp_server as mcp_mod
    from tortoise.quota import (
        QUOTA_REASON_MAX_CHARS,
        QUOTA_REASON_TRUNCATION_MARK,
    )

    assert mcp_mod._QUOTA_FALLBACK_REASON_MAX == QUOTA_REASON_MAX_CHARS
    assert mcp_mod._QUOTA_FALLBACK_REASON_MARK == QUOTA_REASON_TRUNCATION_MARK


def test_the_facts_survive_to_the_PUBLISHED_health_block(monkeypatch):
    """The claim is about ``health.quota``, so it must be pinned THERE (P1).

    ⛔ Why this exists (review finding, P1): every other case in this file asserts
    ``org_quota_state``, which is the wiring one layer BELOW the published
    surface. Measured: filtering the ``dimension*`` keys out of
    ``_org_quota_block``'s real return (a five-word comprehension in
    ``mcp_server.py``) left this file, ``test_11892_health_quota.py`` and
    ``test_10749_health_tail_under_transport_bound.py`` all green — 117 passed —
    i.e. the exact defect in the issue could be reintroduced with a fully green
    suite. The two ``_org_quota_block`` cases that DID exist cover the resolution
    arm and the hand-built fallback, never the gate-refusal arm a real tenant
    hits.

    Driven through the real block, the real carry and the real bounded runner;
    the gate is the real gate over an armed, over-cap ``graph_storage`` reading,
    so no store and no SDK is touched (the refusal happens above the count).
    """
    import contextvars

    import tortoise.mcp_server as mcp
    from tortoise import mcp_auth

    org = contextvars.ContextVar("test_13526_block_org", default=None)
    org.set(ORG)
    carried = contextvars.ContextVar("test_13526_block_limits", default=None)
    carried.set({"org_id": ORG, "tier": "pro", "max_points": None})
    monkeypatch.setattr(mcp_auth, "_current_org_id", org)
    monkeypatch.setattr(mcp_auth, "_current_org_limits", carried)
    # The carry is what puts the configured allowance into the dict the gate
    # evaluates; only MISSING keys are filled, so this dict is the arm.
    monkeypatch.setattr(
        mcp, "_carry_allowances",
        lambda limits, resource: {**limits, "max_graph_storage_mb": PRO_CAP_MB})
    _armed_gate(monkeypatch, MEASURED_USED_MB)

    block = mcp._org_quota_block()

    assert block["state"] == "closed"
    assert block["resource"] == "points", (
        "the asked-about resource keeps its meaning; the refused DIMENSION is "
        "published separately")
    assert block["dimension"] == "graph_storage", (
        "the published block lost the dimension that refused — this is the "
        "defect the issue reports, one layer below the surface that showed it")
    assert block["dimension_limit"] == pytest.approx(PRO_HARD_STOP_MB)
    assert block["dimension_used"] == pytest.approx(MEASURED_USED_MB)
    assert block["dimension_unit"] == "MB"
    assert f"The hard stop is {PRO_HARD_STOP_MB:g}" in block["error"], (
        f"the published figure was cut again: {block['error'][-80:]!r}")
