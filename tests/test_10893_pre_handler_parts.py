"""#10893 (second cycle) — a six-second ``pre_handler`` must name its mechanism.

The first cycle merged ``#14032``, which removed *probe start* work from the
request's accounting, and the criterion still failed: measured after the deploy,
``GET /v1/search`` reported ``pre_handler 5996.5 · embed_encode 4003.0 ·
retrieval 1199.0`` — the request waited **six seconds before its handler ran**.

``pre_handler`` is one number and three different mechanisms produce exactly it:

* the event loop was saturated by another request's synchronous work (the
  ``#2988``/``#3498`` class) — fix: get that work off the loop;
* the credential/org dependency stack was slow — fix: the dependency;
* the handler was waiting for a read-lane worker (``#13528``) — fix: the lane.

Fixing the wrong one is what the first cycle did, so the attribution has to be a
measurement. ``read_stages.PRE_HANDLER_PARTS`` is that measurement: the same
cursor that accumulates ``pre_handler`` also accumulates named intervals, so the
parts sum to the total by construction — no reconciliation, no second clock.

What this file pins, in order of what would break silently:

1. THE SUM — parts and total are one accumulation. A drift here would publish a
   decomposition that does not add up, i.e. an attribution an operator would read
   as fact while it silently loses seconds;
2. THE DIRECTION — ``note(p)`` closes the interval that ENDED, so a call site
   that notes *before* the work attributes it to the wrong part (and the wrong
   fix follows);
3. THE READ-LANE GUARD — the remainder is only ever called ``read_lane`` when the
   last note was the read hand-off. Anything else is ``other``, because naming an
   unmeasured interval after a mechanism is the inference this change removes;
4. ADDITIVE-ONLY — the wire body is untouched, the closed vocabularies still
   refuse unknown names, and an unbound process pays one ContextVar read;
5. THE WIRING — the four boundaries are actually noted in ``hosted_api``. A
   decomposition nobody feeds is a confident zero.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest

from tortoise import read_stages as rs
from tortoise.read_stages import (
    PRE_HANDLER_PART_SET,
    PRE_HANDLER_PARTS,
    STAGE_EMBED_GET,
    begin,
    note,
    request_stages,
    stage_view,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HOSTED = _REPO_ROOT / "tortoise" / "hosted_api.py"


# ── 1. the sum ──────────────────────────────────────────────────────────────

def test_the_parts_sum_to_pre_handler_exactly(monkeypatch):
    """One accumulation, two views — never two clocks reconciled later.

    ⛔ THE ORIGINS ARE DELIBERATELY NON-DEGENERATE: the transport ARRIVAL
    (``started_at``) is 10 s BEFORE the clock this request is constructed at.
    In production ``begin(started_at=t0)`` is called with the arrival while the
    object is built later (``hosted_api.py:6579``), so the interval the
    ``middleware`` part is defined as — *arrival → the handler task created* —
    is exactly that gap. Making the two origins the same instant (as this row
    used to) leaves the sum invariant true under the mutant the invariant
    exists to prevent: ``self._parts_t0 = now`` (``read_stages.py:359``).

    ⛔ A ``file:line`` cite in a docstring is the pointer a fixer follows to re-run
    the mutant, and this one had already gone stale: it read ``:335`` until the
    ``_NOT_MARKABLE`` block above it inserted exactly 24 lines (#14086 round-3
    review, R3-2). Re-measured at this head rather than adjusted by arithmetic.
    """
    clock = {"t": 1000.0}
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock["t"])

    stages = begin(started_at=990.0)
    for part, advance in (("middleware", 0.010), ("dispatch", 0.250),
                          ("sdk_open", 0.500), ("read_submit", 0.002),
                          ("read_lane", 1.250)):
        clock["t"] += advance
        stages.note(part)
    clock["t"] += 0.0
    stages.mark(STAGE_EMBED_GET)

    spent = stages.spent_ms()
    parts = stages.parts_ms()
    assert set(parts) == {"middleware", "dispatch", "sdk_open", "read_submit",
                          "read_lane"}
    # The arrival → construction interval is the FIRST part's whole content, so
    # a parts cursor started anywhere but at the arrival loses that 10 s. The
    # part is published in MILLISECONDS (`_close_part_unlocked` scales seconds
    # by 1000), so a 10 s gap is 10 000 ms and the middleware note adds 10.0 ms.
    # This is the row that kills `self._parts_t0 = now`.
    assert parts["middleware"] == pytest.approx(10_000.0 + 10.0, abs=0.2)
    assert sum(parts.values()) == pytest.approx(spent["pre_handler"], abs=0.2)


def test_the_DERIVED_stage_cannot_be_marked(monkeypatch):
    """``pre_handler`` is derived, never marked — the sum survives someone typing it.

    ⛔ THE INVARIANT WAS BREAKABLE THROUGH THE PUBLIC VOCABULARY (#14086 review
    F5). ``pre_handler`` IS in ``STAGES`` — readers group on it and both
    ``spent()`` and the breach row publish it — so ``mark()``'s closed-vocabulary
    guard ACCEPTED the name. A first ``mark("pre_handler")`` arms and banks the
    interval correctly, but it then leaves ``_stage == "pre_handler"``, so the
    NEXT mark takes ``mark()``'s ``else`` branch and adds a SECOND interval to
    ``_spent["pre_handler"]`` that no part covers: ``sum(parts) <
    pre_handler`` while ``snapshot()`` publishes both as if they reconciled.
    That is precisely the drift this file's item 1 exists to prevent, reached
    without touching the accumulation.

    Nothing called it (measured: no ``mark("pre_handler")`` call site in the
    tree), which is why the row drives the mark ITSELF instead of waiting for a
    caller: the claim is "by construction", and a construction that depends on
    nobody typing a name is not one.

    Non-vacuity: deleting the ``STAGE_PRE_HANDLER`` refusal in ``mark()`` reds
    this row and only this row — ``sum(parts)`` then reads 10250 against a
    ``pre_handler`` of 10750.
    """
    clock = {"t": 1000.0}
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock["t"])

    stages = begin(started_at=990.0)
    clock["t"] += 0.25
    stages.note("dispatch")
    # The shape the vocabulary accepts and the decomposition cannot survive.
    stages.mark(rs.STAGE_PRE_HANDLER)
    clock["t"] += 0.5
    stages.mark(STAGE_EMBED_GET)

    spent = stages.spent_ms()
    parts = stages.parts_ms()
    assert parts, "the read start must still publish a decomposition"
    assert sum(parts.values()) == pytest.approx(spent["pre_handler"], abs=0.2), (
        "marking the DERIVED stage opened a second `pre_handler` interval that "
        "no part covers — the parts no longer sum to the total")


def test_the_UNMARKED_state_cannot_be_marked(monkeypatch):
    """``unmarked`` means "no read stage was recorded" — a mark must not fake it.

    ⛔ THE MIRROR OF THE ROW ABOVE (#14086 round-2 review, F4). ``unmarked`` is
    also in ``STAGES``, and its own docstring defines it as *"no read stage was
    recorded before the bound expired"* — a state ``snapshot()`` publishes and a
    reader draws a conclusion from. On an ARMED timeline a ``mark("unmarked")``
    passes the closed-vocabulary guard, takes the ``else`` branch, closes the
    open stage and leaves ``_stage == "unmarked"`` — so a breached caller's body
    reports "no read stage was recorded" for a request whose stages WERE
    measured. That is the unsupported-inference shape this whole change exists to
    remove (#10893), reachable by a typo.

    Non-vacuity: dropping ``STAGE_UNMARKED`` from ``_NOT_MARKABLE`` reds this row
    and only it — ``current()`` then reads ``unmarked`` where ``embed_get`` was
    the stage actually entered.
    """
    clock = {"t": 1000.0}
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock["t"])

    stages = begin(started_at=1000.0)
    clock["t"] += 0.01
    stages.mark(STAGE_EMBED_GET)
    clock["t"] += 0.01
    stages.mark(rs.STAGE_UNMARKED)

    assert stages.current() == STAGE_EMBED_GET, (
        "`unmarked` is not a stage a read path enters — marking it reported "
        "'no read stage was recorded' for a request that measured one")
    # The harm surface is the PUBLISHED view, not just the accessor: `stages_ms`
    # carries closed stages only (`embed_get` is still open), while `stage` is
    # what a breach row hands an operator as the cause.
    assert stages.snapshot()["stage"] == STAGE_EMBED_GET


def test_a_request_with_no_notes_still_decomposes():
    """The remainder is published, under its own honest name."""
    stages = begin()
    stages.mark(STAGE_EMBED_GET)
    parts = stages.parts_ms()
    assert list(parts) == ["other"]
    assert sum(parts.values()) == pytest.approx(
        stages.spent_ms()["pre_handler"], abs=0.2)


# ── 2. the direction ────────────────────────────────────────────────────────

def test_a_note_closes_the_interval_that_ended(monkeypatch):
    """``note("sdk_open")`` says "up to now was the SDK open" — not "starts now"."""
    clock = {"t": 500.0}
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock["t"])

    stages = begin(started_at=500.0)
    clock["t"] += 2.0                      # the SDK open itself
    stages.note("sdk_open")
    clock["t"] += 0.001                    # trivial handler work
    stages.note("read_submit")
    stages.mark(STAGE_EMBED_GET)

    parts = stages.parts_ms()
    assert parts["sdk_open"] == pytest.approx(2000.0, abs=1.0)
    assert parts["read_submit"] == pytest.approx(1.0, abs=1.0)


# ── 3. the read-lane guard ──────────────────────────────────────────────────

def test_the_remainder_is_read_lane_only_after_the_hand_off(monkeypatch):
    """Two runs, one difference: whether the read hand-off was noted."""
    clock = {"t": 0.0}
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock["t"])

    with_hand_off = begin(started_at=0.0)
    with_hand_off.note("read_submit")
    clock["t"] += 1.0
    with_hand_off.mark(STAGE_EMBED_GET)
    assert set(with_hand_off.parts_ms()) == {"read_lane"}

    # ⛔ THE REMAINDER INTERVAL MUST BE NON-ZERO, or this row is inert: the
    # mutant it names (`"read_lane" if _last_part == "read_submit" else
    # "other"` → `"read_lane"`) banks `read_lane = 0.0` on a zero-length
    # interval and `parts_ms()`'s `ms > 0.0` filter hides it — the assertion
    # then reads identically with the guard present and deleted (#14086 F3).
    without = begin(started_at=0.0)
    without.note("sdk_open")
    clock["t"] += 1.0
    without.mark(STAGE_EMBED_GET)
    parts = without.parts_ms()
    assert set(parts) == {"sdk_open", "other"}, (
        "a route that never handed work to the read lane must not have its "
        "pre-handler time published as a read-lane wait")
    assert parts["other"] == pytest.approx(1000.0, abs=1.0)


# ── 4. additive-only ────────────────────────────────────────────────────────

def test_an_unknown_part_is_refused_rather_than_published(monkeypatch):
    """The closed vocabulary is load-bearing: one unknown key voids the PROP.

    ⛔ THE CLOCK ADVANCES BEFORE THE GUARDED NOTE, or this row is inert
    (#14086 F2): with the note landing at a zero-length interval, the guard
    present and the guard deleted publish the identical `{"other": …}` (the
    unknown part is filtered by `ms > 0.0`), so no mutant reds here. The
    downstream cost of a leaked name is not cosmetic — the emit site
    (`hosted_api._emit_wait_bound_breach`) drops the WHOLE
    `read_pre_handler_parts` prop when one key is outside the vocabulary, so
    one typo would silently void the entire decomposition.
    """
    clock = {"t": 0.0}
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock["t"])
    stages = begin(started_at=0.0)
    clock["t"] += 0.5
    stages.note("not_a_part")      # would bank 500.0 ms IF it were published
    clock["t"] += 0.5
    stages.mark(STAGE_EMBED_GET)
    parts = stages.parts_ms()
    assert "not_a_part" not in parts, (
        "an unknown part name published — the emit site would then drop the "
        "whole decomposition for this request")
    assert parts == {"other": 1000.0}, (
        "the refused note must not move the parts cursor: the whole interval "
        "belongs to the remainder")


def test_the_part_vocabulary_is_a_closed_set_of_named_parts():
    assert PRE_HANDLER_PARTS
    assert frozenset(PRE_HANDLER_PARTS) == PRE_HANDLER_PART_SET
    assert len(PRE_HANDLER_PART_SET) == len(PRE_HANDLER_PARTS)
    # Disjoint from the STAGE vocabulary: a part is a slice of `pre_handler`,
    # and publishing one under a stage name would make the two tables
    # indistinguishable to a dashboard grouping either of them.
    assert not (set(PRE_HANDLER_PARTS) & set(rs.STAGES))


def test_a_note_after_the_read_started_is_ignored(monkeypatch):
    """`pre_handler` is CLOSED at the first mark; a late note must not reopen it."""
    clock = {"t": 0.0}
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock["t"])
    stages = begin(started_at=0.0)
    clock["t"] += 0.1
    stages.note("dispatch")
    clock["t"] += 0.1
    stages.mark(STAGE_EMBED_GET)
    before = stages.parts_ms()
    clock["t"] += 5.0
    stages.note("read_lane")
    assert stages.parts_ms() == before
    assert "read_lane" not in before


def test_the_wire_view_still_withholds_the_decomposition(monkeypatch):
    """A caller cannot act on "time went by before my handler ran"."""
    clock = {"t": 0.0}
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock["t"])
    stages = begin(started_at=0.0)
    clock["t"] += 0.2
    stages.note("dispatch")
    stages.mark(STAGE_EMBED_GET)
    assert stages.parts_ms(), "the decomposition exists…"
    assert "pre_handler_parts" not in stages.wire_snapshot(), (
        "…and must never reach the refusal BODY")


def test_the_snapshot_carries_the_decomposition_to_its_publication_site(monkeypatch):
    """⛔ The ONE line that DELIVERS this change: `snapshot()`'s carrier.

    Every other row here calls `parts_ms()` directly, the wire row asserts the
    key's ABSENCE and the log row is fed a hand-built view — so deleting
    `view["pre_handler_parts"] = parts` would publish the decomposition
    NOWHERE (no analytics row, no breach log line) with the suite green
    (#14086 F4). pinned end to end at the analytics row below.
    """
    clock = {"t": 0.0}
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock["t"])
    stages = begin(started_at=0.0)
    clock["t"] += 0.5
    stages.note("dispatch")

    # OPEN timeline: ABSENT rather than empty — the parts cannot sum to a total
    # that has not closed yet, and an empty table reads as "measured, free".
    assert "pre_handler_parts" not in stages.snapshot()

    clock["t"] += 0.5
    stages.mark(STAGE_EMBED_GET)
    view = stages.snapshot()
    assert view["pre_handler_parts"] == {"dispatch": 500.0, "other": 500.0}
    assert view["pre_handler_parts"] == stages.parts_ms()
    assert sum(view["pre_handler_parts"].values()) == pytest.approx(
        view["stages_ms"]["pre_handler"], abs=0.2)


def test_an_unmeasured_pre_handler_publishes_no_part_table_at_all(monkeypatch):
    """ABSENT beats an empty table: "no boundary observed" ≠ "observed, free"."""
    clock = {"t": 0.0}
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock["t"])
    stages = begin(started_at=0.0)
    stages.mark(STAGE_EMBED_GET)     # zero-length pre_handler: every part is 0.0
    assert stages.parts_ms() == {}
    with request_stages(stages):
        view = stage_view()
    assert view is not None and "pre_handler_parts" not in view
    assert "pre_handler" not in view["stages_ms"]


def test_the_breach_row_publishes_the_decomposition_and_voids_it_on_a_leak(monkeypatch):
    """The prop NAME and the emit site's closed-vocabulary guard, end to end.

    `snapshot()` is one hop from the analytics row: `_emit_wait_bound_breach`
    re-validates every part name and drops the WHOLE prop when one is outside
    the vocabulary, so the guard and the carrier have to be pinned together.
    """
    from tortoise import hosted_api as ha

    captured: dict = {}
    monkeypatch.setattr(ha, "_track_analytics_event",
                        lambda org, ev, props: captured.update(props=dict(props)))
    monkeypatch.setattr(
        "tortoise.monitoring.control_plane_worker",
        lambda _name: type("W", (), {"submit": staticmethod(
            lambda fn: (fn(), _Done())[1])})())

    clock = {"t": 0.0}
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock["t"])
    stages = begin(started_at=0.0)
    clock["t"] += 0.5
    stages.note("dispatch")
    clock["t"] += 0.5
    stages.mark(STAGE_EMBED_GET)

    ha._emit_wait_bound_breach("org", "/v1/search", "GET", 24000,
                               stages=stages.snapshot())
    assert captured["props"]["read_pre_handler_parts"] == {
        "dispatch": 500.0, "other": 500.0}

    captured.clear()
    leaked = stages.snapshot()
    leaked["pre_handler_parts"] = dict(leaked["pre_handler_parts"],
                                       not_a_part=1.0)
    ha._emit_wait_bound_breach("org", "/v1/search", "GET", 24000,
                               stages=leaked)
    assert "read_pre_handler_parts" not in captured["props"], (
        "one unknown part must void the whole prop — the analytics lake is a "
        "top-level-key filter, so a key no reader groups on must never be "
        "published there")


def test_an_unbound_note_is_a_noop_and_publishes_nothing():
    note("dispatch")          # no timeline bound — must not raise
    assert stage_view() is None


def test_the_binding_carries_the_note_into_the_handler_task(monkeypatch):
    """The parts are noted on the LOOP, but the timeline is the seam's own."""
    clock = {"t": 0.0}
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock["t"])
    stages = begin(started_at=0.0)
    with request_stages(stages):
        clock["t"] += 0.4
        note("dispatch")
    # Still open (the read never started), so nothing is published yet.
    assert stages.parts_ms() == {}
    stages.mark(STAGE_EMBED_GET)
    assert stages.parts_ms()["dispatch"] == pytest.approx(400.0, abs=1.0)


# ── 5. the wiring ───────────────────────────────────────────────────────────

def _noted_parts_in(path: Path) -> set[str]:
    """Every literal part name passed to a ``note(...)`` call in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        fn = node.func
        name = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if name not in ("note", "_note_stage"):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found.add(arg.value)
    return found


def test_hosted_api_notes_every_boundary_it_owns():
    """A decomposition nobody feeds is a confident zero.

    ``hosted_api`` owns four of the five boundaries (``other`` is the
    remainder the timeline fills itself). Missing one is not a crash — it is a
    part that silently never appears, i.e. the blindness this change removes.
    """
    found = _noted_parts_in(_HOSTED)
    assert found == {"middleware", "dispatch", "sdk_open", "read_submit"}, (
        f"hosted_api notes {sorted(found)}; expected the four boundaries it owns")


def test_require_scope_notes_the_dispatch_boundary_before_any_early_return(
        monkeypatch):
    """Boundary 2 must be noted ABOVE ``_require_scope``'s early returns.

    The set-equality row above is a STATIC scan, so it stays green when
    ``_note_stage("dispatch")`` is moved below
    ``if org.get("legacy_full_access") or org.get("key_id") is None: return``
    (``hosted_api.py:10532``): the literal is still in the file, while the note
    never runs for the repo's own fixture org (``tests/test_hosted_api.py:82-90``
    sets ``legacy_full_access: True``). The whole arrival → SDK-open interval is
    then published as ``sdk_open`` — a six-second loop wait reads as a
    six-second graph call, and the fix that follows is the wrong one (#10893).
    This row DRIVES the boundary instead of scanning for it.
    """
    from tortoise import hosted_api as ha

    clock = {"t": 1000.0}
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock["t"])

    stages = begin(started_at=990.0)
    clock["t"] += 0.010
    with request_stages(stages):
        # The legacy/session-auth arm is the FIRST return, so a note placed
        # after it is unreachable for every legacy key.
        ha._require_scope({"legacy_full_access": True}, "graphs:read",
                           "search")
    stages.mark(STAGE_EMBED_GET)

    parts = stages.parts_ms()
    assert "dispatch" in parts, (
        "`_require_scope` returned without noting the `dispatch` boundary — a "
        "note placed below its legacy/session-auth early return never runs, so "
        "every legacy-key request's arrival → SDK-open wait is published as "
        "`sdk_open` (the attribution that picks the wrong fix)")
    assert set(parts) == {"dispatch"}, (
        f"the legacy arm closed {sorted(parts)} — `_require_scope` notes ONLY "
        "the dispatch boundary, so a second part here means the note moved to "
        "another boundary's site")
    # ⛔ The MAGNITUDE is deliberately not asserted HERE. On this arm the note
    # is inside a call the fixture cannot instrument, so `dispatch` is
    # `now - started_at` by construction: 10_010 ms under every placement that
    # keeps the presence assert above green — there is no separating mutant, and
    # a row that cannot red is not evidence (#14086 review, P2). The magnitude
    # IS pinned, discriminatingly, by
    # `test_the_dispatch_note_is_the_FIRST_statement_of_require_scope` below:
    # its fixture makes every `org.get` cost clock, so a note moved below the
    # early-return guard lands on a different number while presence stays green.


def test_the_dispatch_note_is_the_FIRST_statement_of_require_scope(
        monkeypatch):
    """The `dispatch` note must close the boundary BEFORE any org read.

    Why a second row: the legacy-arm row above pins only PRESENCE, and its
    magnitude is fixed by construction. `note("dispatch")` sits above
    ``_require_scope``'s early returns for that reason, but nothing stopped it
    being moved BELOW them while a PRESENCE assert stayed green. This row
    separates the placements on the MAGNITUDE: the org mapping costs 3 s of fake
    clock per `.get`, the guard reads it twice, so a note below the guard reports
    16_000 ms where the first-statement note reports 10_000 ms.

    Verified non-vacuous (executed): moving `_note_stage("dispatch")` in
    `_require_scope` below `if org.get("legacy_full_access") or
    org.get("key_id") is None:` reds this row on the MAGNITUDE
    (`assert 16000.0 == 10000.0 ± 0.5`) while THIS row's presence check stays
    GREEN — the note still fires for a scoped key, so only the value moves.
    That relative position is the discrimination the legacy row's
    by-construction magnitude assertion could not provide, because there the
    note either fires at the one reachable instant or not at all. (The sibling
    legacy row also reds under that mutant, but on PRESENCE: a legacy key's
    guard returns before a note placed below it.)
    """
    from tortoise import hosted_api as ha

    clock = {"t": 1000.0}
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock["t"])

    class _CostlyScopes(dict):
        """A scope mapping whose every read costs 3 s of the fake clock."""

        def get(self, key, default=None):
            clock["t"] += 3.0
            return super().get(key, default)

    org = _CostlyScopes({"key_id": "k_10893", "legacy_full_access": False,
                         "scopes": ["graphs:read"]})

    stages = begin(started_at=990.0)
    with request_stages(stages):
        # A SCOPED key: the legacy/session-auth early return is NOT taken, so
        # the note runs under either placement and only its POSITION differs.
        ha._require_scope(org, "graphs:read", "search")
    stages.mark(STAGE_EMBED_GET)

    parts = stages.parts_ms()
    assert "dispatch" in parts, (
        "the `dispatch` boundary was never closed for a scoped key")
    assert parts["dispatch"] == pytest.approx(10_000.0, abs=0.5), (
        f"the `dispatch` boundary closed at {parts['dispatch']:.1f} ms on a "
        "10_000 ms arrival → guard window: the note fired AFTER "
        "`_require_scope` read the org, so every scoped request's "
        "credential-check time is published under a part that did not spend "
        "it (the attribution that picks the wrong fix, #10893)")


def test_the_analytics_prop_is_allowlisted():
    """An unregistered prop is stripped in silence (#3821)."""
    from tortoise import hosted_api as ha
    assert "read_pre_handler_parts" in ha._ALLOWED_ANALYTICS_PROPS


class _Done:
    """Stand-in for the futures `_emit_wait_bound_breach` tracks."""

    def add_done_callback(self, _cb):
        return None

    def cancelled(self):
        return False

    def exception(self):
        return None


def test_the_log_suffix_carries_the_decomposition():
    from tortoise.read_stages import log_suffix
    view = {"stage": "embed_get",
            "stages_ms": {"pre_handler": 6000.0, "embed_get": 900.0},
            "pre_handler_parts": {"dispatch": 5996.5}}
    line = log_suffix(view)
    assert "pre_handler_parts=" in line
    assert "5996.5" in line
    # …and an unbound timeline keeps the pre-#10893 shape byte for byte.
    assert log_suffix(None) == ""
    assert log_suffix({}) == ""
    assert "pre_handler_parts" not in log_suffix(
        {"stage": "retrieval", "stages_ms": {"retrieval": 10.0}})
