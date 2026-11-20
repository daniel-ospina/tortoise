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
    PRE_HANDLER_PARTS,
    PRE_HANDLER_PART_SET,
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
    """One accumulation, two views — never two clocks reconciled later."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock["t"])

    stages = begin(started_at=1000.0)
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
    assert sum(parts.values()) == pytest.approx(spent["pre_handler"], abs=0.2)


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

    without = begin(started_at=0.0)
    without.note("sdk_open")
    without.mark(STAGE_EMBED_GET)
    parts = without.parts_ms()
    assert "read_lane" not in parts, (
        "a route that never handed work to the read lane must not have its "
        "pre-handler time published as a read-lane wait")


# ── 4. additive-only ────────────────────────────────────────────────────────

def test_an_unknown_part_is_refused_rather_than_published(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(rs.time, "monotonic", lambda: clock["t"])
    stages = begin(started_at=0.0)
    stages.note("not_a_part")
    clock["t"] += 0.5
    stages.mark(STAGE_EMBED_GET)
    assert "not_a_part" not in stages.parts_ms()
    assert len(stages.parts_ms()) == 1


def test_the_part_vocabulary_is_a_closed_set_of_named_parts():
    assert PRE_HANDLER_PARTS
    assert PRE_HANDLER_PART_SET == frozenset(PRE_HANDLER_PARTS)
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


def test_the_analytics_prop_is_allowlisted():
    """An unregistered prop is stripped in silence (#3821)."""
    from tortoise import hosted_api as ha
    assert "read_pre_handler_parts" in ha._ALLOWED_ANALYTICS_PROPS


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
