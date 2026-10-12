"""#4242 — the metering ledger's WINDOW follows the authoritative bound.

WHAT THIS FILE IS FOR. Every period-keyed writer upserts on
``(org_id, period_start)`` with an ``ON CONFLICT … DO UPDATE`` that used to
update the **counters only**: ``period_end`` was written once, on the INSERT,
and never again. A row minted while the org's anchor was a ``± 1 month``
APPROXIMATION (20260918000001's ``end - 1 month`` backfill, 20260919000001's
``start + 1 month`` repair) therefore kept that window after the authoritative
``current_period_end`` arrived on ``organizations``.

WHY THAT IS A SPEND-CEILING DEFECT, NOT COSMETIC. ``metering_cohort_spend`` is
a half-open **overlap** of the supplied window (``period_start < p_period_end
AND period_end > p_period_start``), and the window it is handed is the
REQUESTING org's (``cohort_cost.enforce_cohort_cost_cap`` →
``metering._current_period``). A row whose ``period_end`` is shorter than the
period it actually meters can therefore fall OUTSIDE a later read window its
spend belongs to. On a spend ceiling an under-read is fail-OPEN — the cap fires
later than it should. For an annual plan the repaired ``+1 month`` window is
short by ~11 months.

WHAT THE TESTS HERE MUST THEREFORE ASSERT. Not that the SQL contains a
statement, but that the CAP INPUT is corrected. ``test_..._closes_the_fail_open_
...`` drives the REAL writer (``metering.record_ask_usage`` via the Supabase
seam, with the anchor read from the control plane) and the REAL reader
(``metering.get_cohort_spend_usd`` → the ``metering_cohort_spend`` seam) over a
window that begins AFTER the approximated end — the read the sticky row is
invisible to. A test that only asserted the migration text would have been
green for the entire life of this bug.

THE STATIC HALF IS THE DRIFT GUARD, and it is the reason
``test_every_period_keyed_rpc_carries_the_refresh`` walks the MIGRATION
DIRECTORY rather than naming one file: these RPCs are re-created in place by
later migrations (20260915 → 20260918 → 20261001000001/4/5), so a FUTURE
migration that re-creates any of them without the refresh would silently
re-open this defect. Resolving the NEWEST definition per function makes that a
test failure instead.

#4242 was filed against THREE RPCs. Two period-keyed writers landed afterwards
— ``metering_increment_embedding`` (20261001000004) and
``metering_set_graph_storage`` (20261001000005) — and both carried the SAME
omission, so both are covered here and in the SQL suite.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest

#: An annual plan's authoritative period, and the ``start + 1 month``
#: approximation 20260919000001 derives when only the start is stored.
ANNUAL_START = "2026-03-01T00:00:00+00:00"
APPROX_END = "2026-04-01T00:00:00+00:00"
AUTHORITATIVE_END = "2027-03-01T00:00:00+00:00"

#: A read window that begins AFTER the approximated end — the cohort check
#: taken later in the annual period, which is exactly the read the sticky row
#: falls out of.
LATE_WINDOW_START = "2026-04-02T00:00:00+00:00"

_ORG = "org-4242-annual"


def _period(start_iso: str, end_iso: str):
    from tortoise.metering import MeteringPeriod
    return MeteringPeriod(start=datetime.fromisoformat(start_iso),
                          end=datetime.fromisoformat(end_iso))


@pytest.fixture
def supabase_mode(monkeypatch):
    """Force the Supabase control-plane lane with a swappable fake."""
    import tortoise.supabase_control as sc

    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    return sc


def _plane(monkeypatch, sc, org_anchor_end: str = AUTHORITATIVE_END):
    """A fake control plane whose ONE org carries the AUTHORITATIVE anchor.

    The anchor is authoritative (the end the webhook finally supplied) while
    the LEDGER row is seeded with the approximated window by the test — which
    is precisely the state the defect leaves behind: ``organizations``
    corrected, the already-written ``metering_records`` row not.
    """
    from tests.fake_control_plane import FakeControlPlane

    plane = FakeControlPlane({
        "organizations": [{"id": _ORG, "subscription_id": "sub_4242_annual",
                           "current_period_start": ANNUAL_START,
                           "current_period_end": org_anchor_end}],
        "metering_records": [],
    })
    monkeypatch.setattr(sc, "get_control_plane", lambda: plane)
    return plane


def _seed_sticky_row(plane, fn: str, extra: dict | None = None):
    """Write the row the way the defect left it: the approximated window."""
    plane.rpc(fn, {"p_org_id": _ORG, "p_period_start": ANNUAL_START,
                   "p_period_end": APPROX_END, **(extra or {})})
    rows = plane.tables["metering_records"]
    assert len(rows) == 1, rows
    assert rows[0]["period_end"] == APPROX_END
    return rows[0]


# ── The fail-open assertion: the CAP INPUT, driven end-to-end ───────────────


def test_the_next_increment_closes_the_fail_open_read(supabase_mode, monkeypatch):
    """#4242 → mutation: drop ``period_end = EXCLUDED.period_end`` from
    ``metering_increment_ask``'s ``DO UPDATE`` (this file's subject, minus the
    SQL text).

    The sticky row is seeded with the approximated window; then the REAL writer
    runs with the authoritative anchor, and the REAL reader is asked for the
    cohort's spend over a window that begins AFTER the approximated end.

    RED: the reader returns 0.0 (the string would read "this cohort has spent
    nothing"), the row's ``period_end`` stays at the approximation, or the cost
    is REPLACED rather than accumulated. GREEN: 3.50 + 1.50 = 5.00, visible to a
    late-window cohort read — the ceiling sees the spend it is meant to bound.
    """
    from tortoise.metering import get_cohort_spend_usd, record_ask_usage

    plane = _plane(monkeypatch, supabase_mode)
    row = _seed_sticky_row(plane, "metering_increment_ask",
                           {"p_calls": 1, "p_cost_usd": 3.50})

    # The REAL writer. Its window comes from ``_current_period`` → the org's
    # anchor (AUTHORITATIVE_END), NOT from this test — which is what makes the
    # refresh the authority's, not the test's.
    record_ask_usage(_ORG, calls=1, cost_usd=1.50)

    assert row["period_end"] == AUTHORITATIVE_END, (
        "the ledger window is STICKY: the increment bumped the counters and "
        "left the approximated period_end in place (#4242)")
    assert row["ask_cost_usd"] == pytest.approx(5.0), (
        "the window refresh must not REPLACE the counters")

    # The reader is the cap's own seam, over the window whose START is after
    # the approximated end.
    spent = get_cohort_spend_usd(
        [_ORG], _period(LATE_WINDOW_START, AUTHORITATIVE_END))
    assert spent == pytest.approx(5.0), (
        f"the cohort read returned {spent} for a window beginning after the "
        "APPROXIMATED end — the sticky window under-reads the cohort, which is "
        "fail-OPEN on a spend ceiling (#4242)")


def test_the_control_read_is_discriminating(supabase_mode, monkeypatch):
    """The negative control for the assertion above: a row LEFT with the
    approximated window is invisible to the same late-window read.

    Without this, the test above could be passing because the reader ignores
    the window entirely rather than because the window was corrected.

    RED: the late-window read counts an un-refreshed row (the assertion above
    would then be vacuous).
    """
    from tortoise.metering import get_cohort_spend_usd

    plane = _plane(monkeypatch, supabase_mode)
    # Seeded straight through the seam and NEVER incremented again — the row
    # the fix cannot heal (#8005: healed by the next increment, not by this
    # change; a deploy-time backfill is the larger, fail-direction-bearing
    # decision that issue owns).
    _seed_sticky_row(plane, "metering_increment_ask",
                     {"p_calls": 1, "p_cost_usd": 3.50})

    assert get_cohort_spend_usd(
        [_ORG], _period(LATE_WINDOW_START, AUTHORITATIVE_END)) == 0.0, (
        "an un-refreshed row must be INVISIBLE to this read — otherwise the "
        "fail-open assertion above is not measuring the window at all")


# ── Every lane, through the fake that stands in for the shipped SQL ─────────

#: The six period-keyed writers and the extra body keys each lane needs.
_PERIOD_KEYED_LANES = [
    ("metering_increment", {"p_n": 1, "p_nodes_written": 0}),
    ("metering_increment_ask", {"p_calls": 1, "p_cost_usd": 1.0}),
    ("metering_increment_capture_cost", {"p_calls": 1, "p_cost_usd": 1.0}),
    ("metering_increment_embedding", {"p_calls": 1, "p_texts": 1, "p_chars": 1}),
    # #4489: the retrieval lane joined the period-keyed set and inherits the
    # #4242 window refresh. Without this entry the static drift guard's
    # hardcoded lane list would be blind to it (the defect it exists to stop).
    ("metering_increment_retrieval", {"p_queries": 1, "p_leg_runs": 1}),
    ("metering_set_graph_storage", {"p_total_mb": 1.0}),
]


@pytest.mark.parametrize("fn,extra", _PERIOD_KEYED_LANES,
                         ids=[n for n, _ in _PERIOD_KEYED_LANES])
def test_the_fake_refreshes_the_window_on_every_lane(fn, extra):
    """#4242 → mutation: update four of the five lanes.

    ``tests/fake_control_plane.py`` is the ONLY behavioural proxy for these
    RPCs in CI (the SQL never executes in the Python lane), and its own comment
    records that a fake mirroring the intended semantics cannot falsify them —
    so a partial fix in the fake would report the defect as fixed for the lane
    it missed. This drives the fake's real ``rpc`` dispatch and asserts the
    WINDOW is refreshed on the ``ON CONFLICT`` path while the row stays single.

    RED: any lane that keeps the approximated ``period_end``, or a refresh that
    mints a second row instead of updating the one the PK identifies.
    """
    from tests.fake_control_plane import FakeControlPlane

    plane = FakeControlPlane({"metering_records": []})
    row = _seed_sticky_row(plane, fn, extra)

    plane.rpc(fn, {"p_org_id": _ORG, "p_period_start": ANNUAL_START,
                   "p_period_end": AUTHORITATIVE_END, **extra})

    assert len(plane.tables["metering_records"]) == 1, (
        f"{fn} created a SECOND row for the same period_start — the upsert "
        "target is (org_id, period_start)")
    assert row["period_end"] == AUTHORITATIVE_END, (
        f"{fn} left period_end at the approximated window (#4242)")


# ── The drift guard: the NEWEST definition of each RPC must carry it ────────

_MIG_DIR = (Path(__file__).resolve().parent.parent / "supabase" / "migrations")

_REFRESH_RE = re.compile(r"period_end\s*=\s*EXCLUDED\.period_end")


def _newest_definition(name: str) -> tuple[str, str]:
    """The migration that defines ``public.<name>`` LAST, in filename order —
    the definition production actually runs (supabase applies every migration
    in order; a later ``CREATE`` replaces an earlier one)."""
    hits: list[tuple[str, str]] = []
    create_re = re.compile(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+public\." + re.escape(name)
        + r"\s*\(")
    for path in sorted(_MIG_DIR.glob("*.sql")):
        text = path.read_text()
        if create_re.search(text):
            hits.append((path.name, text))
    assert hits, f"no migration defines public.{name}()"
    return hits[-1]


def _body_of_declaration(text: str, name: str) -> str:
    """The SQL body of the LAST declaration of ``public.<name>`` in a file,
    from its ``CREATE`` to the ``$$;`` that closes it."""
    create_re = re.compile(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+public\." + re.escape(name)
        + r"\s*\(")
    start = 0
    for m in create_re.finditer(text):
        start = m.start()
    assert start or create_re.match(text), f"{name} not declared in the file"
    tail = text[start:]
    end = tail.find("$$;")
    assert end != -1, f"unterminated $$ body for public.{name}"
    return tail[:end]


@pytest.mark.parametrize("name", [n for n, _ in _PERIOD_KEYED_LANES])
def test_every_period_keyed_rpc_carries_the_refresh(name):
    """#4242 → mutation: re-create any of these RPCs in a later migration
    WITHOUT ``period_end = EXCLUDED.period_end`` in its ``DO UPDATE``.

    This walks the migration directory instead of naming a file because these
    functions are re-created in place by later migrations (20260915 → 20260918
    → 20261001000001/4/5). A future lane that re-cuts one of them — the exact
    move that made #4242 stale against its own body (three RPCs, not five) —
    re-opens the defect unless the refresh rides along.

    RED: the newest definition lacks the refresh, its ``ON CONFLICT`` target is
    not the window start, or it has no ``DO UPDATE`` at all (an INSERT-only
    writer cannot refresh anything).
    """
    filename, text = _newest_definition(name)
    body = _body_of_declaration(text, name)

    assert "ON CONFLICT (org_id, period_start)" in body, (
        f"the newest definition of {name} ({filename}) does not upsert on the "
        "window start — the ledger's identity is the period boundary")
    assert "DO UPDATE" in body, (
        f"the newest definition of {name} ({filename}) has no DO UPDATE — an "
        "INSERT-only writer cannot correct a window it already wrote")
    assert _REFRESH_RE.search(body), (
        f"the newest definition of {name} ({filename}) does not refresh "
        "period_end on conflict — an approximated window written by that row is "
        "STICKY and can fall outside a later cohort read (fail-open, #4242)")
