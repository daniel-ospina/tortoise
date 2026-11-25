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

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


def _measured_at_argument(construction: ast.Call) -> ast.expr | None:
    """The expression a ``MeterReading(...)`` construction hands over as the reading.

    Read from the KEYWORD when one names it, and BY POSITION otherwise:
    ``MeterReading`` is a plain dataclass whose field order puts ``measured_at``
    second (``tortoise/allowance_gate.py``), so ``MeterReading(used, ts, True)``
    is ordinary, correct Python. Reading only the keyword REDDENED exactly that
    correct fixture (review of #14646 at ``fc870945fc``: *"a POSITIONAL
    ``MeterReading(...)`` construction reds the freshness pin with correct
    code"*) — a pin that fires on a shape that is right is the failure mode this
    whole arm was built to avoid, from the other side.

    ``None`` means the reading is NOT visible in the call (fewer than two
    positional arguments, or a ``*args`` splat in that slot). The caller's
    vacuity assertion fires on ``None``, so an unreadable construction fails
    CLOSED rather than passing silently.
    """
    for keyword in construction.keywords:
        if keyword.arg == "measured_at":
            return keyword.value
    if len(construction.args) < 2:
        return None
    second = construction.args[1]
    if isinstance(second, ast.Starred):
        return None
    return second


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
    #: ⛔ TZ-SAFE, mirroring `allowance_gate._stale` (`if measured.tzinfo is None:
    #: measured = measured.replace(tzinfo=UTC)`). A naive spelling —
    #: `datetime.utcnow().isoformat()`, which the movement arm's `_Shifted` docstring
    #: names as a supported correct fixture — is armed by the gate exactly like an
    #: aware one, so THIS check must not be the thing that rejects it, and above all
    #: must not reject it with `can't subtract offset-naive and offset-aware
    #: datetimes` (measured: both arms died there instead of judging the value).
    _read = datetime.fromisoformat(measured_at)
    age = (datetime.now(UTC) - (_read if _read.tzinfo else _read.replace(tzinfo=UTC))).total_seconds()
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


def _dotted_name(node: ast.expr) -> str | None:
    """The dotted spelling of a Name/Attribute expression (``time.gmtime``)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted_name(node.value)
        return f"{inner}.{node.attr}" if inner else None
    return None


def _clock_aliases() -> dict[str, str]:
    """Module-level names in THIS file, and the clock spelling each resolves to.

    The behavioural arm patches the module's ``datetime`` and ``time`` globals. A
    fixture may instead reach the clock through a name bound at MODULE scope, and
    the STRUCTURAL arm in this same file accepts those — `_defines_a_body` says so
    (*"`_t = time` is a module ALIAS, not a definition: discounting it would red a
    correct fixture that spells a clock read `_t.gmtime()`"*) and `reaches_a_clock`
    does too (*"`_now = datetime.now` — an ordinary spelling — stays accepted"*).
    ⛔ Those two are named rather than cited by line: an earlier revision of this
    docstring quoted `:279-282` and `:299`, which had rotted 48 and 68 lines away
    from the sentences they claimed — the same defect class this PR re-derives 22
    addresses to remove, inside the file that ships the fix. Patching only the two
    globals left the arms DISAGREEING: the shift moved nothing, and the failure
    blamed a calendar pin the fixture did not have (#14646 P2).

    So the patch surface is DERIVED from the file's own source, the same discipline
    the structural arm's scope walk already uses. This does NOT reach a name bound
    inside the fixture's own body (``import time as _t`` written in the fixture),
    which stays one of the disclosed fail-closed shapes.
    """
    aliases: dict[str, str] = {}
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
        ):
            spelling = _dotted_name(stmt.value)
            if spelling:
                aliases.setdefault(stmt.targets[0].id, spelling)
        elif isinstance(stmt, ast.Import):
            for alias in stmt.names:
                aliases.setdefault(alias.asname or alias.name.split(".")[0], alias.name)
        elif isinstance(stmt, ast.ImportFrom) and stmt.module:
            for alias in stmt.names:
                aliases.setdefault(alias.asname or alias.name, f"{stmt.module}.{alias.name}")
    return aliases


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
    * structural — the value handed to the gate RESOLVES to an expression that
      reads a clock, with no hardcoded ISO instant text and no `.replace(...)`
      pinning a calendar component — so the shapes a lane actually writes are
      rejected when the edit lands rather than a day later. Source reading is a
      DIAGNOSTIC, not the guarantee: it has lost the arms race repeatedly, which
      is why ``test_the_fixture_reading_follows_the_clock`` below decides the
      property by measurement instead.

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

    ⛔ Reaching a clock is NOT on its own proof that the VALUE is derived, and the
    arm checks both halves because two shapes reach one and still hand the gate a
    literal — measured GREEN on the clock-only version:

    * ``_PINNED or datetime.now()`` (and the ``and`` form) — the literal is
      truthy, so it is the ``_PINNED`` that reaches the gate and the clock is dead
      code;
    * ``datetime.now() if False else "2026-…"`` — the same, on the dead branch.

    Both redden inside this fixture's own 360 s window rather than silently after
    24 h, so the consequence is BOUNDED, not a new silent class; but this arm's
    claim is *the value that reaches the gate*, so a resolved expression carrying
    a hardcoded ISO instant anywhere is rejected even when it also reads a clock.
    Nothing clock-derived carries one — a ``strftime`` FORMAT string is `%Y-%m-…`,
    and the scan matches digits.

    ⛔ And a BARE annotation is not a rebind: ``measured_at: str`` on its own
    leaves the running value untouched, so it must not shadow the binding above
    it. Treating it as the last binding made the arm store an ``ast`` value of
    ``None`` and crash on it, reddening a shape that is CORRECT — it now judges
    the value Python would actually hand over.
    """
    import ast
    import inspect
    import re

    from tortoise.allowance_gate import STALE_AFTER_SECONDS

    #: The fixture's own window must stay INSIDE the gate's bound, or a stale
    #: fixture passes every arm in this file. Asserted, not described: this was
    #: prose in the comment above `_FRESH_FIXTURE_MAX_AGE_S` and nothing pinned
    #: it (#14646).
    assert 0 < _FRESH_FIXTURE_MAX_AGE_S < STALE_AFTER_SECONDS

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
    #: …of which these are DUAL-PURPOSE: a clock read with NO positional argument,
    #: a pure CONVERTER with one. `time.gmtime(<baked epoch>)` produces a constant
    #: and is not derived; `time.gmtime()` is (#14646 cycle 3).
    clock_dual_attrs = {"gmtime", "localtime", "time", "time_ns"}

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

    def _defines_a_body(name, scope, seen=frozenset()):
        """True when `name` resolves to a function/class/lambda THIS MODULE defines.

        That is the only kind of receiver worth descending into. `_t = time` is a
        module ALIAS, not a definition: discounting it would red a correct fixture
        that spells a clock read `_t.gmtime()`, which is why the test is on the
        resolved BODY rather than on membership in `scope` (#14646 cycle 3).

        ⛔ An INSTANCE of such a body is a body too (#14646 P3-2): the resolution
        follows a CALL whose callee this module defines, so `_FROZEN = _FrozenClock()`
        is recognised as the stub it is rather than as a clock. Without the call
        arm the name resolves to an `ast.Call`, this returns False, and
        `_FROZEN.now(…)` is counted as a clock READ — which let a frozen stub
        spelled through a module variable through `assert not undated` and left it
        to red only in the behavioural window instead of at the EDIT. A callee this
        module does NOT define stays a clock (`_t = time; _t.gmtime()`), so the
        alias shape that must keep working still does.
        """
        node = scope.get(name)
        while True:
            if (isinstance(node, ast.Name) and node.id in scope
                    and node.id not in seen):
                seen = seen | {node.id}
                node = scope[node.id]
                continue
            if isinstance(node, ast.Call):
                callee = node.func
                if (isinstance(callee, ast.Name) and callee.id in scope
                        and callee.id not in seen):
                    seen = seen | {callee.id}
                    node = scope[callee.id]
                    continue
            break
        return isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))

    def reaches_a_clock(node, scope, seen=frozenset(), as_func=False):
        """True when `node` READS a clock.

        `as_func` marks the node as the CALLEE of a `Call`. That distinction is
        load-bearing twice over: a bare `time.gmtime` there is not itself a read
        (the arity rule belongs to the CALL, not the attribute), while a bare
        `datetime.now` there still has to reach its receiver, which is how
        `_now = datetime.now` — an ordinary spelling — stays accepted.
        """
        if isinstance(node, ast.Name):
            if node.id in scope and node.id not in seen:
                return reaches_a_clock(scope[node.id], scope, seen | {node.id})
            return False
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in clock_attrs
                # A dual-purpose attribute handed an instant is a CONVERTER; the
                # arguments below are still walked, so `time.gmtime(time.time())`
                # reads and `time.gmtime(<baked>)` does not.
                and not (node.args and func.attr in clock_dual_attrs)
                and not (
                    isinstance(func.value, ast.Name)
                    and _defines_a_body(func.value.id, scope))
            ):
                return True
            return (
                reaches_a_clock(func, scope, seen, as_func=True)
                or any(reaches_a_clock(arg, scope, seen) for arg in node.args)
                or any(reaches_a_clock(kw.value, scope, seen)
                       for kw in node.keywords))
        if isinstance(node, ast.Attribute):
            if (
                not as_func
                and node.attr in clock_attrs
                and not (
                    isinstance(node.value, ast.Name)
                    and _defines_a_body(node.value.id, scope))
            ):
                return True
            return reaches_a_clock(node.value, scope, seen)
        return any(
            reaches_a_clock(child, scope, seen)
            for child in ast.iter_child_nodes(node))

    def pins_a_calendar(node, scope):
        """True when a clock-derived value is then pinned to a fixed DATE.

        `datetime.now(UTC).replace(year=2026, month=11, day=24, hour=2, …)`
        reads a clock and then discards it: the result is a constant that is
        fresh for one staleness window and reds the file one bound later — the
        #9127 bomb wearing the fix's clothes. So a `replace` naming a calendar
        component is not derived. `replace(microsecond=0)` stays accepted: the
        reading still moves with the clock.

        ⛔ Residual, stated rather than papered over. This arm reads SOURCE, so
        what it can establish is narrow: that a clock read is PRESENT in the
        resolved value, that no ISO instant is written as TEXT, and that no
        `.replace` pins a calendar component. It cannot establish that the value
        FLOWS from the clock — `datetime.now(UTC).isoformat()[:10] +
        "T02:00:00+00:00"` satisfies all three, and so does a helper that reads a
        clock and discards it.

        That is precisely why the sibling arm
        ``test_the_fixture_reading_follows_the_clock`` exists: it shifts the clock
        a known distance and requires the value the gate is handed to move by
        exactly that much, which a composed or pinned value fails and a derived
        one cannot. **The movement arm is the guarantee; this one is the cheaper
        diagnostic that names the offending expression.** Two earlier revisions of
        this paragraph claimed the guard "fails at the EDIT", that only "string
        surgery" survived it, and that a future-dated instant was red immediately;
        all three were measured false on the head they shipped at.
        """
        return any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "replace"
            and any(keyword.arg in {"year", "month", "day"}
                    for keyword in child.keywords)
            for child in walk_resolved(node, scope))

    #: An ISO-8601 instant written into the source, matched on TEXT because that
    #: is the shape being banned. Digits, not `%Y`, so a `strftime` format string
    #: is never a hit.
    instant_text = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")

    def holds_an_instant(node, scope):
        """True when the resolved expression carries a hardcoded ISO instant."""
        return any(
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and instant_text.search(child.value) is not None
            for child in walk_resolved(node, scope))

    fn = next(
        node for node in ast.parse(inspect.getsource(_single_sample_meter)).body
        if isinstance(node, ast.FunctionDef))

    def _scope_of(*bodies) -> dict[str, ast.expr]:
        """The name -> expression map Python itself would resolve against.

        Scope, in Python's own resolution order: module globals first, then the
        function's locals overriding them. The LAST binding of a name wins, which
        is what a rebind does at runtime — a derived value shadowed by a later
        literal below it is still a literal.

        Hoisted out of the arm body so the SELF-CHECK below can resolve a
        SYNTHETIC module through exactly this rule instead of through a
        restatement of it (#14646 P3-2).
        """
        built: dict[str, ast.expr] = {}
        for body in bodies:
            for stmt in body:
                if isinstance(stmt, ast.Assign):
                    targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
                    value = stmt.value
                elif (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    # A bare `x: str` DECLARES; it does not rebind. Only an
                    # annotation carrying a value is a binding, and only a binding
                    # may shadow the one above it.
                    and stmt.value is not None
                ):
                    targets, value = [stmt.target.id], stmt.value
                else:
                    continue
                for name in targets:
                    built[name] = value
        # A helper function is a legitimate place for the clock, at module level OR
        # defined inside the fixture; register its body so `def _now_iso(): return
        # datetime.now(UTC)…` resolves wherever it is written. A CLASS is registered
        # for the same reason and one more: it is what `_defines_a_body` walks INTO
        # to tell a clock wrapper (`return datetime.now(UTC)`) from a frozen stub
        # (`return datetime(2026, …)`).
        for body in bodies:
            for stmt in body:
                if isinstance(
                        stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    built.setdefault(stmt.name, stmt)
        # An IMPORT-bound clock is a binding too (#14646 P3). `from time import time`
        # binds THIS module's name `time` to a reader, so `time()` is exactly as
        # derived as `time.time()` — but imports are not `Assign`s, so without this
        # the arm resolved `time` to nothing, called the reading "undated", and red
        # correct code with a message naming a cause it never measured. Each such
        # name is registered as the synthetic ATTRIBUTE it spells, so the same
        # `clock_attrs` / `clock_dual_attrs` rules judge it as the module form.
        for name, spelling in _clock_aliases().items():
            if name in built:
                continue
            receiver, _, attr = spelling.rpartition(".")
            if receiver and attr in (
                    "now", "utcnow", "today", "time", "time_ns", "gmtime", "localtime"):
                built[name] = ast.Attribute(
                    value=ast.Name(id=receiver, ctx=ast.Load()),
                    attr=attr, ctx=ast.Load())
        return built

    module = ast.parse(inspect.getsource(inspect.getmodule(_single_sample_meter)))
    scope = _scope_of(module.body, fn.body)

    # ⛔ SELF-CHECK of the receiver rule (#14646 P3-2), run against a SYNTHETIC
    # module so it measures the RULE and not this fixture's current spelling.
    # It exists because the rule has a direction that mutating THIS fixture can
    # only ever show once: `_defines_a_body` must recognise an INSTANCE of a body
    # this module defines, not only a plain function/class NAME. Before it did,
    # `_FROZEN = _FrozenClock()` resolved to an `ast.Call`, was not seen as a
    # body, and `_FROZEN.now(UTC)` was counted as a CLOCK READ — so a frozen stub
    # spelled through a module variable passed `assert not undated` and reddened
    # only the behavioural window instead of the edit. Both directions are
    # asserted below: the frozen instance must NOT read as a clock, and a wrapper
    # that really reads one must still be walked INTO and accepted (the fix must
    # not become over-strict and red a correct fixture).
    import textwrap

    synthetic = ast.parse(textwrap.dedent("""
        class _FrozenClock:
            @staticmethod
            def now(_tz=None):
                return datetime(2026, 11, 24, 2, 0, tzinfo=UTC)

        class _ClockReadingClock:
            @staticmethod
            def now(_tz=None):
                return datetime.now(_tz)

        _FROZEN = _FrozenClock()
        _LIVE = _ClockReadingClock()

        def _frozen_fixture():
            measured_at = _FROZEN.now(UTC).isoformat()
            return MeterReading(used=1.0, measured_at=measured_at,
                                estimated=True, note="n")

        def _live_fixture():
            measured_at = _LIVE.now(UTC).isoformat()
            return MeterReading(used=1.0, measured_at=measured_at,
                                estimated=True, note="n")
    """))
    def _synthetic_fixture(name):
        """The value `name` hands the gate, and the scope that resolves it.

        The scope is the module PLUS that fixture's own body, the same two
        layers the real arm uses — without the local layer the reading resolves
        to a bare `measured_at` Name and nothing can be said about it.
        """
        owner = next(
            node for node in synthetic.body
            if isinstance(node, ast.FunctionDef) and node.name == name)
        construction = next(
            node for node in ast.walk(owner)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "MeterReading")
        return (_measured_at_argument(construction),
                _scope_of(synthetic.body, owner.body))

    frozen_reading, frozen_scope = _synthetic_fixture("_frozen_fixture")
    live_reading, live_scope = _synthetic_fixture("_live_fixture")

    assert _defines_a_body("_FROZEN", frozen_scope), (
        "`_FROZEN = _FrozenClock()`, where `_FrozenClock` is defined in this "
        "module, is not recognised as a body — so `_FROZEN.now(…).isoformat()` "
        "counts as a CLOCK READ and a frozen stub spelled through a module "
        "variable passes `assert not undated`, reddening only the behavioural "
        "window instead of the edit (#14646 P3-2). Resolve a CALL whose callee "
        "this module defines: an instance of a local class is a local body.")
    assert _defines_a_body("_LIVE", live_scope), (
        "the same resolution must hold for a wrapper INSTANCE — otherwise the "
        "two spellings are judged by different rules and this check proves "
        "nothing about the one that matters")
    assert not reaches_a_clock(frozen_reading, frozen_scope), (
        "the arm counts a clock-NAMED call on a frozen-stub INSTANCE as "
        "clock-derived, which is the P3-2 bypass restated executably: the value "
        "handed to `MeterReading` derives from `datetime(2026, 11, 24, …)`, so "
        "it must be REJECTED at the edit. Walking the class body is what makes "
        "this work — a stub is refused because nothing in it reads a clock.")
    assert reaches_a_clock(live_reading, live_scope), (
        "the rule now discounts an instance receiver, so it MUST still walk INTO "
        "the class body: a wrapper that really reads the clock "
        "(`return datetime.now(_tz)`) is CORRECT code and rejecting it would "
        "red a correct fixture — the failure mode from the other side.")

    constructions = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "MeterReading"]
    assert constructions, (
        "`_single_sample_meter` no longer constructs a `MeterReading` directly, "
        "so this arm cannot see the reading it exists to pin and would pass "
        "vacuously (#14646). Teach it the new shape rather than deleting it: "
        "what must be pinned is the value the gate is handed.")
    # Keyword OR position — `MeterReading`'s own field order puts `measured_at`
    # second, and a positional construction is correct Python. An unreadable
    # construction (fewer than two positional args, a `*args` in that slot)
    # contributes nothing here, so the vacuity assertion below still fires on it.
    handed = [
        value for value in map(_measured_at_argument, constructions)
        if value is not None]
    assert handed, (
        "no `MeterReading(measured_at=…)` keyword, and no readable second "
        "positional argument, found inside `_single_sample_meter` — so this arm "
        "cannot see the reading the gate is handed and would pass vacuously "
        "(#14646)")
    undated = [
        value for value in handed if not reaches_a_clock(value, scope)]
    pinned = [
        value for value in handed
        if reaches_a_clock(value, scope) and pins_a_calendar(value, scope)]
    instant_carrying = [
        value for value in handed if holds_an_instant(value, scope)]
    assert not undated, (
        "the fixture hands the gate a reading that reaches no clock THIS ARM "
        "CAN SEE — which is not the same claim as 'derives from no clock': a "
        "clock reached through a helper in ANOTHER module, or a name this arm "
        "cannot resolve, reads the same way here and is CORRECT code, so this is "
        "a fail-closed red on the SOURCE arm, not a proof of a defect. "
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
    assert not instant_carrying, (
        "the fixture hands the gate a reading with a hardcoded ISO instant in it — "
        f"{', '.join(ast.unparse(value) for value in instant_carrying)} — so the "
        "instant, not the clock, is what the gate ages. Reaching a clock "
        "somewhere in the expression is not enough when a literal is what comes "
        "out of it (a dead `if`/`else` branch, or an `or`/`and` whose other "
        "operand is the truthy literal). Hand `MeterReading` the clock-derived "
        "VALUE itself.")

    reading = _single_sample_meter(MEASURED_USED_MB)(ORG)
    #: ⛔ TZ-SAFE at EVERY age site, mirroring `allowance_gate._stale`: a naive
    #: spelling (`datetime.utcnow().isoformat()`) is a CORRECT reading the gate
    #: arms, and this file must not reject it — least of all with `can't subtract
    #: offset-naive and offset-aware datetimes`, which names nothing about the
    #: rule. There are two of these computations (here and inside
    #: `_single_sample_meter`); both are normalised.
    _read = datetime.fromisoformat(reading.measured_at)
    age = (datetime.now(UTC) - (_read if _read.tzinfo else _read.replace(tzinfo=UTC))).total_seconds()
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


def test_the_fixture_reading_follows_the_clock(monkeypatch):
    """The SHAPE-INDEPENDENT half: the reading must MOVE when the clock moves.

    ``test_the_fixture_reading_is_fresh_and_derived_from_the_clock`` above decides
    by reading the fixture's SOURCE, and source-reading arms have now lost the
    arms race four times — each time measured open with the file green. One
    shape survives it at the head this test was added to, and it is why this
    test exists:

    * ``datetime.now(UTC).replace(**{"year": 2026, "month": 11})`` — a clock
      call, so a source arm calls it derived, but the calendar component is
      PINNED: it reads as ~now for the rest of this month and then ages out,
      which is the #9127/#14646 bomb with a later fuse. It escapes the source
      arm only because a ``**{…}`` splat gives ``keyword.arg is None``, so the
      arm's calendar check sees no keyword to inspect.

    ⛔ The NAMED form is NOT an example: the source arm's ``pins_a_calendar``
    reads ``{"year", "month", "day"}`` keywords and catches ``replace(day=1)``
    already (`a714ff2`) — but NOT ``replace(hour=2)``/``replace(minute=…)``, which
    is not in that set (measured: structurally green), so THIS arm is what covers
    the sub-day pins. An earlier revision of this paragraph listed
    ``replace(day=1)`` as a second escaping shape and that was measured FALSE — do
    not delete a component from the source arm's set on the strength of this test
    covering it.

    So this arm does not look at the source at all. It hands the fixture two
    clock readings a known distance apart and requires the value that REACHES
    ``MeterReading`` to move by exactly that distance. A pinned instant does not
    move; a pinned calendar component fails the year-crossing shift; a derived
    reading moves through both, by whatever route.

    ⛔ Deliberately FAIL-CLOSED, and it says so in the message: a clock reached
    through another module (``from tortoise.ids import now_iso``) or a constant
    computed at IMPORT time does not move under this patch, so it is refused
    here too, with the inline remedy named. A fail-closed red is defensible; a
    fail-closed red that asserts a falsehood is not.
    """
    import sys
    import time as _real_time

    from tortoise.allowance_gate import STALE_AFTER_SECONDS

    real_datetime = datetime
    module = sys.modules[__name__]

    def handed(offset: timedelta) -> datetime:
        """The reading ``_single_sample_meter`` gives the gate, `offset` from now."""

        class _Shifted(real_datetime):
            """`datetime`, shifted on EVERY clock read.

            Missing one of these manufactures a false red, not a safety net: a
            correct fixture using ``utcnow()`` would be told its clock read does
            not follow the clock.
            """

            @classmethod
            def now(cls, tz=None):
                return real_datetime.now(tz) + offset

            @classmethod
            def utcnow(cls):
                return real_datetime.utcnow() + offset

            @classmethod
            def today(cls):
                return real_datetime.today() + offset

        class _Time:
            """`time`, shifted — so ``time.time()``/``gmtime()`` follow the clock.

            An INSTANCE, not the class: ``__getattr__`` fires only for instance
            attribute access, so patching the class itself would make
            ``time.strftime`` an ``AttributeError`` and refuse a correct fixture.

            ⛔ And CALLABLE, because ``from time import time`` binds this NAME to
            a function: a non-callable stub turns a correct fixture into
            ``TypeError: object is not callable``, which never mentions the rule.
            """

            def __getattr__(self, attr):
                return getattr(_real_time, attr)

            def __call__(self) -> float:
                return _real_time.time() + offset.total_seconds()

            @staticmethod
            def time() -> float:
                return _real_time.time() + offset.total_seconds()

            @staticmethod
            def time_ns() -> int:
                """`time_ns` reads the clock too, so it shifts with the others."""
                return _real_time.time_ns() + int(offset.total_seconds() * 1_000_000_000)

            @staticmethod
            def gmtime(secs=None):
                return _real_time.gmtime(
                    _real_time.time() + offset.total_seconds() if secs is None else secs)

            @staticmethod
            def localtime(secs=None):
                return _real_time.localtime(
                    _real_time.time() + offset.total_seconds() if secs is None else secs)

        #: The shifted value each clock spelling must be replaced WITH. A READER
        #: (`now`, `time`, `gmtime`) becomes the bound shifted callable; a MODULE
        #: (`time`) and the `datetime` class stay a module/class so their other
        #: attributes keep working.
        replacements = {
            "datetime": _Shifted,
            "datetime.now": _Shifted.now,
            "datetime.utcnow": _Shifted.utcnow,
            "datetime.today": _Shifted.today,
            "time": _Time(),
            "time.time": _Time().time,
            "time.gmtime": _Time().gmtime,
            "time.localtime": _Time().localtime,
            "time.time_ns": _Time().time_ns,
        }
        patches = {"datetime": _Shifted, "time": _Time()}
        for name, spelling in _clock_aliases().items():
            replacement = replacements.get(spelling)
            if replacement is not None and name not in patches:
                patches[name] = replacement
        #: `monkeypatch` undoes every one of these at teardown, including the ones
        #: re-patched by the later `handed()` calls in the assertion loop.
        for name, value in patches.items():
            monkeypatch.setattr(module, name, value, raising=False)
        try:
            raw = _single_sample_meter(MEASURED_USED_MB)(ORG).measured_at
        except AssertionError as exc:
            #: ⛔ The fixture's OWN freshness assert runs inside the shifted clock, so
            #: when this arm cannot see a clock the reading does not move and THAT
            #: assert fires first — with "written as an offset from now … never as a
            #: literal date", which is false for a correctly written fixture that
            #: merely reaches the clock somewhere this patch cannot reach (another
            #: module, an import-time constant, a name bound inside the fixture). The
            #: disclosure below never printed, because the exception left `handed()`
            #: before the `moved` assertion. A fail-closed red is defensible; a
            #: fail-closed red that asserts a falsehood is not, so the real cause is
            #: named here rather than leaving the fixture's message to mislead.
            raise AssertionError(
                "this arm cannot SHIFT the clock the fixture reads, so it cannot "
                "judge it — and the fixture's own freshness check fired first, "
                "because a reading that does not move under a +6h shift looks 6h "
                f"stale. Underlying report: {exc}\n"
                "⛔ The three shapes this arm cannot shift, all CORRECT code: a clock "
                "reached through a helper in ANOTHER module "
                "(`from tortoise.ids import now_iso`), a constant computed at IMPORT "
                "time, and a clock name bound INSIDE the fixture's own body "
                "(`import time as _t`). Read the clock inline — "
                "`datetime.now(UTC) - timedelta(minutes=5)` — or bind it at module "
                "scope, which `_clock_aliases()` does reach.") from exc
        return real_datetime.fromisoformat(raw)

    base = handed(timedelta())
    #: TWO shifts, and the second crosses a year ON PURPOSE: a pinned calendar
    #: component tracks the clock under a six-hour shift and stops tracking it
    #: under a year-crossing one, which is the only cheap way to tell a pin from
    #: a read.
    for shift in (timedelta(hours=6), timedelta(days=405)):
        moved = (handed(shift) - base).total_seconds()
        assert abs(moved - shift.total_seconds()) <= 2, (
            "the fixture hands the gate a reading that does NOT follow the "
            f"clock: a shift of {shift.total_seconds():.0f}s moved it "
            f"{moved:.0f}s. Either it is pinned to an absolute instant — however "
            "it is spelled, and however many clock-named calls sit on the way to "
            "it — or to a calendar component (a `replace(year=…)`-style pin), "
            "which is the same bomb with a later fuse. The gate ages that reading "
            f"by STALE_AFTER_SECONDS ({STALE_AFTER_SECONDS} s), so 24 h after the "
            "instant baked into it the dimension goes UNARMED, the write is "
            "SERVED, and this file reds with no commit in between (#9127, third "
            "recurrence). Derive it — `datetime.now(UTC) - timedelta(minutes=5)` "
            "— and hand THAT to `MeterReading`.\n\n"
            "⛔ This arm cannot shift a clock it cannot see, so three shapes are "
            "refused here even though they are correct code: a read through a "
            "helper in ANOTHER module, a constant computed at IMPORT time, and a "
            "clock module imported INSIDE the fixture's own body (`import time "
            "as _t`), whose local name this patch cannot reach. That is a false "
            "red with a cheap remedy: read the clock inline — "
            "`datetime.now(UTC) - timedelta(minutes=5)` (#14646)."
            "⛔ MODULE-scope aliases are NOT in that list: `_t = time`, "
            "`_now = datetime.now`, `_clock = time.gmtime`, `import time as t` and "
            "`from time import time` are all patched by `_clock_aliases()`, so they "
            "move with the clock exactly as the structural arm says they should.")


def test_the_freshness_pin_reads_a_positional_construction():
    """A positional ``MeterReading(used, ts, …)`` is CORRECT and must be measured.

    ``MeterReading`` is a plain frozen dataclass and its field order puts
    ``measured_at`` second (``tortoise/allowance_gate.py``), so the positional
    form is ordinary Python. The arm read only ``keyword.arg == "measured_at"``,
    so that form collected nothing and the vacuity assertion fired — red on code
    that is RIGHT (review of #14646 at ``fc870945fc``, P3). It now falls back to
    the second positional argument, and this test pins that fallback, including
    the shapes that must still fail CLOSED (nothing readable → ``None``).

    NON-VACUITY: this reds on the pre-fix code — ``_measured_at_argument`` does
    not exist there — and the assertion on ``pre_fix`` below is the regression
    itself restated executably: the old keyword-only filter finds NOTHING in the
    positional call, while the helper finds the reading.
    """
    def call_of(source: str) -> ast.Call:
        node = ast.parse(source).body[0]
        assert isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        return node.value

    positional = call_of('MeterReading(used, measure(), True, "note")')
    keyworded = call_of('MeterReading(used=used, measured_at=measure(), estimated=True)')
    keyword_beats_position = call_of('MeterReading(used, measure(), measured_at=other())')
    too_short = call_of("MeterReading(used)")
    splatted = call_of("MeterReading(*args)")

    # The pre-fix predicate, verbatim: it is what this test exists to replace.
    pre_fix = [
        keyword.value for keyword in positional.keywords
        if keyword.arg == "measured_at"]
    assert pre_fix == [], (
        "this shape no longer reproduces the P3 (it carries a `measured_at` "
        "keyword), so the regression it documents would be measured against "
        "nothing")

    assert ast.unparse(_measured_at_argument(positional)) == "measure()"
    assert ast.unparse(_measured_at_argument(keyworded)) == "measure()"
    assert ast.unparse(_measured_at_argument(keyword_beats_position)) == "other()", (
        "a keyword names the reading explicitly, so it wins over the position; "
        "the arm must not prefer a positional argument the call overrides")
    assert _measured_at_argument(too_short) is None, (
        "with fewer than two positional arguments the reading is not visible, "
        "and `None` is what makes the caller's vacuity assertion fire — fail "
        "closed, never pass")
    assert _measured_at_argument(splatted) is None, (
        "`*args` in the second slot cannot be read as the reading; treat it as "
        "unreadable rather than guessing")


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
