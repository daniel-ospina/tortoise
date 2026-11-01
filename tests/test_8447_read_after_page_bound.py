"""#8447 row #6 — an out-of-domain page ask is REFUSED, never silently clamped.

The defect, measured at ``7df48ec7c`` (``tortoise/event_store.py:895``)::

    limit = max(1, min(int(limit), 1000))

With 1001 events in the store, ``read_after(proj, 0, limit=5000)`` returned
1000 rows and said nothing, and ``sdk.poll_events(limit=5000)`` returned
``{'events': [...1000 rows...], 'next_cursor': ...}``. A client that asked for
5000 and got 1000 reads ``len(events) < limit`` as *the stream is drained* —
1 event withheld in this reproduction, 4000 in the general case, with no field
anywhere saying so. That is exactly the contract #8447 pins: a bounded
operation returns complete results or says it is incomplete; it never returns a
silently truncated page that is indistinguishable from a complete one.

The bound itself is not new, and it is not invented here — it was declared in
two places and silently clamped in two others:

* declared: ``GET /v1/events`` — ``Query(100, ge=1, le=MAX_PAGE)`` (already
  REFUSES 422) — and ``read_after``'s own docstring ("default 100, max 1000");
* clamped: this seam (the defect) and ``webhook_delivery``'s caller, which
  carried its own ``min(..., 1000)`` — now derived from ``MAX_PAGE`` like the
  REST route (one instrument, #1178), with its ``more_pending`` comparison
  moved onto that same clamped page instead of the unclamped batch.

These tests pin both directions: an out-of-domain ask refuses (so no path can
clamp it silently), and an in-domain ask still returns its rows (so the refusal
is not over-broad). Three tests are the no-false-refusal guards — they pass
under the clamp-restore mutation (``MAX_PAGE`` left in place), which is a
narrower claim than "they pass on the pre-fix tree": two of them name
``MAX_PAGE``, which does not exist at ``7df48ec7c``, so against that tree they
ERROR.
"""
from datetime import UTC, datetime

import pytest

from tortoise import event_store


def test_the_rest_route_declares_the_seam_s_bound():
    """#1178: the route's declared maximum IS the seam's, not a second literal.

    A drift here is not cosmetic — a route that accepts a larger ``limit`` than
    ``read_after`` refuses hands the seam an out-of-domain page and answers 400
    to a request its own validator called legal.
    """
    from tortoise.hosted_api import app

    params = app.openapi()["paths"]["/v1/events"]["get"]["parameters"]
    limit = next(p for p in params if p["name"] == "limit")
    schema = limit["schema"]
    assert (schema.get("minimum"), schema.get("maximum")) == (
        1, event_store.MAX_PAGE), schema


def _seed(proj, n: int) -> None:
    """Create `n` :GraphEvent rows in one query.

    Direct Cypher rather than `n` × `append_event` calls: this file is about
    the READ seam's parameter domain, and the dedup test in
    `tests/test_event_store.py` seeds the same way. `read_after` needs no
    index (its `seq` ordering comes from ORDER BY, not the range index).

    ``ts`` is stamped LIVE, not fixed: `poll_events` lazily runs
    `purge_expired(30d)` once per process, and a fixed 2026-01-01 stamp makes
    every seeded row retention bait — the at-cap poll test then returns `[]`
    whenever it happens to be the first poll in the process (measured).
    """
    proj.g.query(
        "UNWIND range(1, $n) AS i CREATE (:GraphEvent {seq: i, "
        "event_id: 'e' + toString(i), type: 'PointAdded', "
        "ts: $ts, payload: '{}'})",
        params={"n": int(n),
                "ts": datetime.now(UTC).isoformat()},
    )


def test_over_limit_ask_refuses_rather_than_clamping(sdk_factory, tmp_path):
    """5000 asked, 1001 available → REFUSED.

    Pre-fix this returned 1000 rows (a page the caller cannot tell from "the
    stream ends at 1000"): the assertion below is that the ask fails instead.
    """
    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    _seed(proj, event_store.MAX_PAGE + 1)
    assert len(event_store.read_after(proj, 0, limit=event_store.MAX_PAGE)) == \
        event_store.MAX_PAGE, "the store holds more than one full page"

    with pytest.raises(ValueError) as exc:
        event_store.read_after(proj, 0, limit=5000)
    msg = str(exc.value)
    assert "1000" in msg and "5000" in msg, msg
    assert "clamped" in msg, msg


def test_a_fractional_ask_refuses_instead_of_being_floored(sdk_factory, tmp_path):
    """`limit=2.9` used to serve 2 rows — the caller's ask, silently shrunk.

    `int(limit)` flooring is the same defect in miniature: the page is shorter
    than the ask for a reason nothing reports, and the refusal message would
    name a value the caller never passed (`5000.7` → "got 5000").
    """
    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    _seed(proj, 5)
    for bad in (2.9, "5000"):
        with pytest.raises(ValueError) as exc:
            event_store.read_after(proj, 0, limit=bad)
        assert "whole number" in str(exc.value), str(exc.value)
    # values int() also floors but `isinstance(x, float)` does NOT catch
    from decimal import Decimal

    with pytest.raises(ValueError) as exc:
        event_store.read_after(proj, 0, limit=Decimal("2.9"))
    assert "2.9" in str(exc.value), str(exc.value)
    # and `inf` is the seam's refusal, not an OverflowError a ValueError
    # caller would miss
    with pytest.raises(ValueError):
        event_store.read_after(proj, 0, limit=float("inf"))
    # an integral float is NOT refused (no false refusal)
    assert len(event_store.read_after(proj, 0, limit=3.0)) == 3


def test_a_duplicate_event_id_inside_a_full_page_shrinks_it(sdk_factory, tmp_path):
    """A short page is STILL not proof the stream is drained — pinned, not fixed.

    Dedup runs AFTER the query's ``LIMIT``: 5 rows with a duplicate at position
    3 return 3 distinct rows for ``limit=4``, while ``seq=5`` exists and was
    never delivered. This is pre-existing and structurally separate from
    #8447's clamp (the state is normally prevented by the unique constraint),
    so it is documented in `read_after`'s docstring and pinned here rather than
    left as "the clamp was the only way to get a short page".
    """
    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    _seed(proj, 5)
    # make seq=3 a DUPLICATE of seq=2's event_id — the state the dedup defends
    # against (tests/test_event_store.py::test_read_dedups_duplicate_event_ids
    # builds the same one)
    proj.g.query("MATCH (e:GraphEvent {seq: 3}) SET e.event_id = 'e2'")

    page = event_store.read_after(proj, 0, limit=4)
    assert [e["seq"] for e in page] == [1, 2, 4]
    assert len(page) < 4, "a short page — the very shape that reads as drained"
    tail = event_store.read_after(proj, page[-1]["seq"], limit=4)
    assert [e["seq"] for e in tail] == [5], "...and rows beyond it DID exist"


def test_domain_low_end_refuses(sdk_factory, tmp_path):
    """`limit=0` / negative are out of domain too.

    Pre-fix `max(1, ...)` silently served 1 row for an ask of 0 — the same
    silent rewrite of the caller's ask, in the other direction. The other
    bounded tool in this repo already refuses it (`limit must be >= 1`).
    """
    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    _seed(proj, 3)
    for bad in (0, -1):
        with pytest.raises(ValueError) as exc:
            event_store.read_after(proj, 0, limit=bad)
        assert "between 1 and 1000" in str(exc.value), str(exc.value)


def test_at_the_cap_is_legal_and_returns_a_full_page(sdk_factory, tmp_path):
    """`limit=MAX_PAGE` is IN contract — it must not be refused.

    Exactly-at-limit is the boundary the refusal must not swallow: the caller
    asked for one page of the size it is allowed to ask for, and the page is
    honest because the cursor continues.
    """
    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    _seed(proj, event_store.MAX_PAGE + 1)

    page = event_store.read_after(proj, 0, limit=event_store.MAX_PAGE)
    assert len(page) == event_store.MAX_PAGE
    assert page[-1]["seq"] == event_store.MAX_PAGE


def test_in_contract_ask_returns_its_rows(sdk_factory, tmp_path):
    """The ordinary short page is untouched (no over-broad refusal)."""
    sdk = sdk_factory(tmp_path)
    proj = sdk._get_proj()
    _seed(proj, 3)

    page = event_store.read_after(proj, 0, limit=100)
    assert [e["seq"] for e in page] == [1, 2, 3]


def test_poll_events_refuses_the_same_ask(sdk_factory, tmp_path):
    """The SDK's one pass-through `limit` inherits the refusal.

    `poll_events` hands the caller's `limit` straight to `read_after`
    (`sdk.py`), so this is the path a user reaches from Python and from
    `tortoise_events_poll`. Pre-fix it answered `{'events': [...1000...],
    'next_cursor': ...}` — a shortened page with no marker, and
    `len(events) < limit` reading as "drained".
    """
    sdk = sdk_factory(tmp_path)
    _seed(sdk._get_proj(), event_store.MAX_PAGE + 1)

    with pytest.raises(ValueError) as exc:
        sdk.poll_events(limit=5000)
    assert "between 1 and 1000" in str(exc.value), str(exc.value)


def test_poll_events_at_the_cap_still_answers(sdk_factory, tmp_path):
    """`poll_events(limit=1000)` keeps paging exactly as before."""
    sdk = sdk_factory(tmp_path)
    _seed(sdk._get_proj(), event_store.MAX_PAGE + 1)

    reply = sdk.poll_events(limit=event_store.MAX_PAGE)
    assert len(reply["events"]) == event_store.MAX_PAGE
    assert reply["next_cursor"]  # a page of the allowed size, continued by cursor
