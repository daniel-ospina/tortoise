"""#12322 — ``latest_readings`` orders by the INSTANT, never by the stored bytes.

The defect this file proves (measured at ``cd22562c24``, before the fix): the
read cut its page with

    ORDER BY coalesce(e.startedAt, e.createdAt, '') DESC, e.id DESC LIMIT $lim

i.e. it selected WHICH readings a judgement is computed from using the very
string comparison this module documents as wrong ~400 lines away
(``instant_at_or_after``: *"comparing the raw strings instead would order
``...+00:00`` against ``...Z`` lexically — a bug that reads as correct whenever
both sides happen to share a spelling"*). ``compare()`` then takes
``readings[0]`` as *the* newest (``goal_judgement.py:772``) and the dwell
window from ``readings[: target.dwell]`` (``:748``, ``:811``), so a mis-ordered
page IS the state the graph reports.

Two stored spellings whose instant order and byte order DISAGREE are enough to
crop the genuinely newest reading out of the page — no crowded history, no
``limit=1`` overflow:

* ``2026-11-12T01:00:00-05:00`` is 06:00Z, and its 12th byte is ``'1'``;
  ``2026-11-12T05:00:00Z`` is 05:00Z with ``'5'`` there, so ``DESC`` puts the
  OLDER row first and ``LIMIT 1`` keeps it.
* the ingest producer's ``2026/11/12 (Thu) 01:00`` sorts above every dashed row
  of the same date because ``'/'`` (0x2F) > ``'-'`` (0x2D) at index 4.

Every test here is written so that a regression reds it:

* reverting the ordering to the raw string reds the order/limit/judgement tests;
* re-introducing a Cypher-side ``LIMIT`` reds
  ``test_the_cap_is_applied_after_the_ordering`` (the two instant-newest rows are
  lexically SMALLEST two, so a page cut before the sort cannot contain them);
* restoring the "complete set" docstring claim reds the disclosure test.

Runnable embedded:
    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
        tests/test_12322_latest_readings_order.py -q -p no:cacheprovider
Runs in the docker lane too: the shared ``sdk_factory`` fixture is URI-aware.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager

import pytest

from tortoise.goal_judgement import Target, compare, latest_readings


@contextmanager
def _forces_tz(name: str):
    """Run a block under a forced timezone, restoring the ambient one.

    The SAME helper shape as ``tests/test_3982_date_only_utc.py`` (#3982), and
    load-bearing here rather than decorative: ``_created_sort_key`` reads a
    zone-less date-TIME in the READER's zone, so the producer fixture below is
    01:00Z on a UTC host and 05:00Z on a ``-04:00`` one — where it would TIE the
    dashed row and the id tie-break would decide. Pinning the zone makes the
    byte-vs-instant disagreement a property of the fixture instead of the host
    (#11646's residual: recorded, not fixed).
    """
    prev = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = prev
        time.tzset()


@pytest.fixture
def sdk(sdk_factory):
    s = sdk_factory()
    yield s
    s.close()


def _indicator(sdk, tag: str) -> dict:
    """A measured thing. ``tag`` keeps two worlds apart (name is the identity)."""
    return sdk.create_object(f"CI pipeline duration [{tag}]", objectKind="workflow")


def _reading(sdk, indicator, value, *, at, kind="measurement"):
    return sdk.create_event(
        f"CI run at {at!r}", kind, value=value, unit="minutes",
        startedAt=at, aboutObject=indicator["id"],
    )


# ── 1. the defect, at the smallest fixture that shows it ───────────────────


def test_limit_one_keeps_the_instant_newest_reading_not_the_lexically_first(sdk):
    """Two readings, one local offset and one ``Z``; the instant-newest is not
    the string-largest, and ``limit=1`` must still return it.

    RED before the fix: the raw-string ``ORDER BY`` + ``LIMIT 1`` returned the
    older row (value 5.0), because ``...T05:00:00Z`` sorts above
    ``...T01:00:00-05:00`` byte-wise.
    """
    indicator = _indicator(sdk, "offset")
    newer = _reading(sdk, indicator, 12.0, at="2026-11-12T01:00:00-05:00")  # 06:00Z
    _reading(sdk, indicator, 5.0, at="2026-11-12T05:00:00Z")  # 05:00Z
    # The premise, asserted so the fixture cannot silently stop disagreeing:
    assert "2026-11-12T05:00:00Z" > "2026-11-12T01:00:00-05:00"

    got = latest_readings(sdk, indicator["id"], limit=1)
    assert [r.reading_id for r in got] == [newer["id"]], (
        "limit=1 returned the reading whose stored BYTES sort first, not the "
        "instant-newest one"
    )
    assert [r.value for r in got] == [12.0]
    # …and the same disagreement is visible with the whole history, so the fix
    # is an ORDERING fix and not merely a wider page.
    assert [r.value for r in latest_readings(sdk, indicator["id"])] == [12.0, 5.0]


def test_the_judgement_is_computed_from_the_instant_newest_reading(sdk):
    """The consequence #11926 exists to remove: a judgement made from a stale
    measurement. ``12 >= 10`` is ``met``; answering from the 5.0 row says
    ``unmet``."""
    indicator = _indicator(sdk, "judgement")
    _reading(sdk, indicator, 12.0, at="2026-11-12T01:00:00-05:00")  # 06:00Z
    _reading(sdk, indicator, 5.0, at="2026-11-12T05:00:00Z")  # 05:00Z

    page = latest_readings(sdk, indicator["id"], limit=1)
    judgement = compare(page, Target(comparator=">=", threshold=10.0))
    assert (judgement.state, judgement.flipped) == ("met", True)
    assert judgement.reading is not None and judgement.reading.value == 12.0


def test_a_slash_spelled_reading_does_not_outrank_a_newer_dashed_one(sdk):
    """The producer's own shape. ``'/'`` > ``'-'`` at index 4, so the raw string
    puts a 01:00Z row above a 05:00Z row of the same date. RED before the fix.

    Zone-pinned on purpose: the producer spelling carries no offset, so
    ``_created_sort_key`` reads it in the reader's zone (#3982/#11646) — on a
    ``-07:00`` host its instant would be LATER than 05:00Z and there would be no
    disagreement left to prove. Under UTC the disagreement is exact.
    """
    indicator = _indicator(sdk, "producer")
    with _forces_tz("UTC"):
        _reading(sdk, indicator, 1.0, at="2026/11/12 (Thu) 01:00")  # 01:00Z
        newer = _reading(sdk, indicator, 9.0, at="2026-11-12T05:00:00Z")  # 05:00Z
        assert "2026/11/12 (Thu) 01:00" > "2026-11-12T05:00:00Z"
        # ⛔ The BYTE disagreement above is only half the premise: if the
        # producer spelling were UNPARSEABLE it would land in the last bucket
        # and ``limit=1`` would return the dashed row for the WRONG reason —
        # green, with ``_forces_tz`` inert. So pin the bucket too.
        from tortoise.search_engine import _created_sort_key

        assert _created_sort_key("2026/11/12 (Thu) 01:00")[0] == 0, (
            "the producer spelling must PARSE — an unparseable as-of sorts last "
            "and would satisfy the assertion below without an instant order"
        )

        got = latest_readings(sdk, indicator["id"], limit=1)
    assert [r.reading_id for r in got] == [newer["id"]]
    assert [r.value for r in got] == [9.0]


# ── 2. the cap is applied AFTER the ordering ──────────────────────────────


def test_the_cap_is_applied_after_the_ordering(sdk):
    """``limit`` is a CAP ON THE ORDERED LIST, never a pre-cut page.

    The two instant-newest readings here are also the two lexically SMALLEST, so a
    fix that keeps ``LIMIT $lim`` in Cypher and sorts afterwards still cannot
    return them — this is the assertion that distinguishes a real fix from a
    re-sort of a wrongly-cut page.
    """
    indicator = _indicator(sdk, "cap")
    # lexically greatest strings first in the query, instant-oldest:
    _reading(sdk, indicator, 1.0, at="2026-11-12T09:00:00Z")  # 09:00Z
    _reading(sdk, indicator, 2.0, at="2026-11-12T08:00:00Z")  # 08:00Z
    _reading(sdk, indicator, 3.0, at="2026-11-12T07:00:00Z")  # 07:00Z
    # lexically smallest (they begin "2026-11-12T00:"), instant-newest — a
    # -12:00 offset buys hours of instant for no byte rank:
    _reading(sdk, indicator, 8.0, at="2026-11-12T00:20:00-12:00")  # 12:20Z
    _reading(sdk, indicator, 9.0, at="2026-11-12T00:30:00-12:00")  # 12:30Z

    page = latest_readings(sdk, indicator["id"], limit=2)
    assert [r.value for r in page] == [9.0, 8.0]
    # The full read is ordered newest-first over every spelling present.
    assert [r.value for r in latest_readings(sdk, indicator["id"])] == [
        9.0, 8.0, 1.0, 2.0, 3.0,
    ]


def test_ties_are_broken_deterministically_by_reading_id(sdk):
    """Same instant, two readings: the order must be TOTAL, not merely stable.

    The assertion is the tie-break DIRECTION (descending ``reading_id``, the
    ``e.id DESC`` the Cypher used to carry), not just "two reads agree": a stable
    sort over an id-ASCENDING page satisfies mere repeatability while pinning
    nothing. THIS test reds when the third element of ``_newest_first_key`` is
    deleted — the page order then shows through as ascending id, and the
    ``sorted(..., reverse=True)`` assertion fails — which is precisely the
    regression it exists for (a peer test asserting only repeatability would stay
    green).
    """
    indicator = _indicator(sdk, "tie")
    a = _reading(sdk, indicator, 4.0, at="2026-11-12T05:00:00Z")
    b = _reading(sdk, indicator, 6.0, at="2026-11-12T00:00:00-05:00")  # also 05:00Z
    got = latest_readings(sdk, indicator["id"])
    assert len(got) == 2
    assert {r.reading_id for r in got} == {a["id"], b["id"]}
    assert [r.reading_id for r in got] == sorted([a["id"], b["id"]], reverse=True)
    # Determinism, not a particular direction: two reads agree.
    assert [r.reading_id for r in latest_readings(sdk, indicator["id"])] == [
        r.reading_id for r in got
    ]


def test_the_read_pages_rather_than_relying_on_one_result_set(sdk, monkeypatch):
    """The history is fetched by keyset pages, so a server-side
    ``RESULTSET_SIZE`` cap cannot silently truncate it in store order (#4233).

    The page size is retuned to 2 here — the pattern
    ``tests/test_9226_dump_paging_v6.py`` uses — so paging is exercised without
    seeding a thousand events.

    TWO assertions, because either alone is weak: (1) the FULL read returns all 5
    rows, which reds a read that stops after ONE page at this page size (a single
    unbounded query would also return all 5 — which is what assertion (3) exists
    for); (2) a ``limit=2`` read on the SECOND
    indicator (``mixed``, offset spellings) returns the two INSTANT-newest values,
    which are also the two lexically SMALLEST ("2026-11-12T00:20:00-12:00" and
    "…T00:30:00-12:00" = 12:20Z / 12:30Z). That reds a restore of the pre-fix
    single ``LIMIT $lim`` query: asked for 2 rows it would have answered with the
    byte-LARGEST two, 1.0 (09:00Z) and 2.0 (08:00Z), which are NOT the two
    instant-newest (the instants ascend 3.0@07:00Z < 2.0@08:00Z < 1.0@09:00Z <
    8.0@12:20Z < 9.0@12:30Z). Every spelling in (1) is ``Z``, so (1) alone could not tell the old
    query from the new one at ``limit=100``; (2) is what makes the ORDERING part
    of this pin, not only the paging.

    ⛔ AND (3) A QUERY-COUNT PIN, because (1) and (2) both survive the paging
    being deleted: ONE unbounded query over 5 rows returns the same five rows in
    the same order, which is exactly the regression that would re-introduce
    #4233's silent store-order truncation. With the page size retuned to 2, a
    paged read of 5 rows issues **3 PAGE** queries plus the ONE aggregate
    `count(e)` the truncation guard needs (4 in total); a single unbounded read
    issues 1 page query plus the aggregate. Counting is done on the SDK's own
    `g.query`, so it cannot be satisfied by a different route.

    ⚠️ Disclosed bound on what this pins: a lane container reports
    ``RESULTSET_SIZE = -1``, so the server-side cap the paging exists for cannot
    be reproduced here. This test pins the paging SHAPE (that the read really
    pages, and that the bound is applied after the ordering), not the cap itself.
    """
    import tortoise.goal_judgement as gj

    indicator = _indicator(sdk, "paging")
    for hour in (1, 2, 3, 4, 5):
        _reading(sdk, indicator, float(hour), at=f"2026-11-12T0{hour}:00:00Z")

    # Count only the READ: the fixture's own writes go through the same client,
    # so the counting stub is installed after seeding and the tally cleared.
    calls: list[str] = []
    real_g = sdk._get_proj().g

    class _Counting:
        def query(self, cypher, **kwargs):
            calls.append(cypher)
            return real_g.query(cypher, **kwargs)

    monkeypatch.setattr(gj, "_READINGS_PAGE_SIZE", 2)
    monkeypatch.setattr(sdk._get_proj(), "g", _Counting(), raising=False)

    got = latest_readings(sdk, indicator["id"])
    assert len(got) == 5, "a page cut lost readings — the read is not paging"
    assert [r.value for r in got] == [5.0, 4.0, 3.0, 2.0, 1.0]
    page_calls = [c for c in calls if "LIMIT $batch" in c]
    count_calls = [c for c in calls if "RETURN count(e)" in c]
    assert len(page_calls) == 3, (
        f"5 readings at a page size of 2 must take 3 page queries, took "
        f"{len(page_calls)} — a single unbounded query re-opens #4233's silent "
        "truncation"
    )
    assert all("id(e) >= $cursor" in c for c in page_calls)
    # …and the independent count the truncation guard compares against is asked
    # for exactly once, as an aggregate.
    assert len(count_calls) == 1

    # …and the same read with offset spellings whose INSTANT order inverts their
    # BYTE order, so the two newest are the two lexically SMALLEST rows.
    mixed = _indicator(sdk, "paging-mixed")
    _reading(sdk, mixed, 1.0, at="2026-11-12T09:00:00Z")
    _reading(sdk, mixed, 2.0, at="2026-11-12T08:00:00Z")
    _reading(sdk, mixed, 3.0, at="2026-11-12T07:00:00Z")
    _reading(sdk, mixed, 8.0, at="2026-11-12T00:20:00-12:00")  # 12:20Z
    _reading(sdk, mixed, 9.0, at="2026-11-12T00:30:00-12:00")  # 12:30Z
    assert [r.value for r in latest_readings(sdk, mixed["id"], limit=2)] == [9.0, 8.0]


def test_a_negative_limit_is_refused_rather_than_silently_sliced(sdk):
    """A negative bound is a caller error, not a bound.

    ``out[: -1]`` drops the OLDEST reading while reading as "everything" — the
    same silent-bound class this function exists to remove (#11823/#12322), so it
    is refused instead of sliced.
    """
    indicator = _indicator(sdk, "negative")
    _reading(sdk, indicator, 9.0, at="2026-11-12T05:00:00Z")
    with pytest.raises(ValueError, match="non-negative cap"):
        latest_readings(sdk, indicator["id"], limit=-1)


def test_a_degenerate_page_size_is_refused_by_name(sdk, monkeypatch):
    """A page size below 1 is a configuration error, and it must be named.

    ⛔ The failure it prevents is a MISATTRIBUTION, not silence. ``LIMIT 0``
    returns an empty page on every page, so without this guard a MEASURED
    indicator would reach the truncation check and raise ``RuntimeError`` ("a page
    came back short") — blaming a server cap for a local misconfiguration — while
    an indicator with NO readings would return ``[]``, which is the correct
    answer. (The independent ``count(e)`` guard means "nothing was ever measured"
    can no longer be reported for an indicator that has readings; that claim was
    true before that guard landed and is not true now.) Refused by NAME, so the
    operator is told which constant to fix.
    """
    import tortoise.goal_judgement as gj

    indicator = _indicator(sdk, "degenerate")
    _reading(sdk, indicator, 9.0, at="2026-11-12T05:00:00Z")
    monkeypatch.setattr(gj, "_READINGS_PAGE_SIZE", 0)
    with pytest.raises(ValueError, match="_READINGS_PAGE_SIZE must be >= 1"):
        latest_readings(sdk, indicator["id"])


def test_a_doubled_about_object_edge_yields_one_reading_not_two(sdk, monkeypatch):
    """A second `aboutObject` edge from the same event must not double a reading
    — and must not lose one either.

    MEASURED (this is the fact the cursor's comment rests on): with two
    `aboutObject` edges from one event to one indicator, `RETURN count(r)` is 2
    but `RETURN id(e), e.id` is **1 row** — the pattern is scoped by node PAIR,
    not by relationship. That is why a single-column `id(e)` cursor is sufficient
    and why no Python de-duplication is layered on top (one keyed on `reading_id`
    could DROP a distinct reading that legitimately shares an `e.id`).

    This is a PROPERTY pin, not a mutant-killer: it stays green under either
    cursor shape because the engine already returns one row per event. It would
    red if the query were ever rewritten to traverse the RELATIONSHIP
    (`MATCH ()-[r:aboutObject]->()`, which returns a row per edge) or if a
    de-duplication step were added with the wrong key.
    """
    import tortoise.goal_judgement as gj

    monkeypatch.setattr(gj, "_READINGS_PAGE_SIZE", 2)
    indicator = _indicator(sdk, "doubled")
    first = _reading(sdk, indicator, 9.0, at="2026-11-12T05:00:00Z")
    for hour in (1, 2, 3):
        _reading(sdk, indicator, float(hour), at=f"2026-11-12T0{hour}:00:00Z")
    sdk._get_proj().g.query(
        "MATCH (e:Event), (o) WHERE e.id = $eid AND o.id = $iid "
        "CREATE (e)-[:aboutObject]->(o)",
        params={"eid": first["id"], "iid": indicator["id"]},
    )
    # The premise, measured in the same graph the read runs against:
    edges = sdk._get_proj().g.query(
        "MATCH (e:Event)-[r:aboutObject]->(o) WHERE e.id = $eid AND o.id = $iid "
        "RETURN count(r)",
        params={"eid": first["id"], "iid": indicator["id"]},
    ).result_set
    assert edges[0][0] == 2, "the doubled edge was not created, so nothing is pinned"

    got = latest_readings(sdk, indicator["id"])
    assert len(got) == 4, "a doubled edge duplicated or dropped a reading"
    assert len({r.reading_id for r in got}) == 4


def test_a_truncated_page_set_raises_instead_of_answering_from_survivors(
    sdk, monkeypatch
):
    """A server-side cap below the page size truncates EVERY page, and the rows it
    costs are ARBITRARY BY INSTANT — pages ascend by ``id(e)``, which is creation
    order only while nothing has been deleted (the engine REUSES a freed node id;
    measured on a lane: ids 2,3,4 for A,B,C, delete B, create D ⇒ D holds id 3),
    and a backfilled row can be instant-OLD while landing last. So continuing
    would compute the state from an incomplete set — the defect #12322 removes
    arriving through the read's own transport (#4233).

    Reproduced without a server cap (a lane reports ``RESULTSET_SIZE = -1``) by
    truncating the PAGE query's rows in the client; the ``count(e)`` aggregate is
    left honest, exactly as it is under a real cap.
    """
    import tortoise.goal_judgement as gj

    indicator = _indicator(sdk, "truncated")
    for hour in (1, 2, 3, 4, 5):
        _reading(sdk, indicator, float(hour), at=f"2026-11-12T0{hour}:00:00Z")

    real_g = sdk._get_proj().g

    class _TruncatingPage:
        def __init__(self, rows):
            self.result_set = rows

    class _TruncatingServer:
        def query(self, cypher, **kwargs):
            res = real_g.query(cypher, **kwargs)
            if "LIMIT $batch" in cypher:
                return _TruncatingPage(res.result_set[:1])  # a cap of 1 row
            return res

    monkeypatch.setattr(gj, "_READINGS_PAGE_SIZE", 2)
    monkeypatch.setattr(sdk._get_proj(), "g", _TruncatingServer(), raising=False)
    with pytest.raises(RuntimeError, match="came back short"):
        latest_readings(sdk, indicator["id"])


def test_a_reading_whose_event_carries_no_id_still_orders_without_raising(sdk):
    """``reading_id`` may be absent on an outside-written event; the order key
    coerces it (``str(reading_id or "")``) so a null id is a TIE-BREAK value, not
    a ``TypeError`` inside ``sort``.

    ⛔ SAME INSTANT ON PURPOSE. The tuple is ``(bucket, measure, reading_id)``, so
    a fixture whose two readings name DIFFERENT instants decides on ``measure``
    and never reaches the third element — the coercion would then be unpinned.
    MEASURED at ``a60a64db15``: with the id-less row at ``01:00Z`` and the dated
    one at ``05:00Z``, deleting the coercion (``reading.reading_id``) left all 14
    tests GREEN; at a shared instant it raises
    ``TypeError: '<' not supported between instances of 'str' and 'NoneType'``.
    """
    indicator = _indicator(sdk, "no-id")
    _reading(sdk, indicator, 9.0, at="2026-11-12T05:00:00Z")
    sdk._get_proj().g.query(
        "MATCH (o) WHERE o.id = $iid "
        "CREATE (e:Event {value: 1.0, unit: 'minutes', "
        "startedAt: '2026-11-12T05:00:00Z', eventKind: 'measurement'})"
        "-[:aboutObject]->(o)",
        params={"iid": indicator["id"]},
    )
    got = latest_readings(sdk, indicator["id"])
    assert [r.value for r in got] == [9.0, 1.0]
    assert got[1].reading_id is None


# ── 3. an unparseable instant sorts LAST, deterministically ─────────────

def test_a_reading_with_no_parseable_instant_sorts_after_every_dated_one(sdk):
    """``_created_sort_key``'s unparseable bucket sorts LAST, deterministically.

    It must: an as-of nobody can place cannot PROVE it is recent, so it must
    never displace a dated reading as ``readings[0]`` — while still being
    returned rather than dropped (dropping is ``since``'s job, and only when a
    boundary is declared).
    """
    indicator = _indicator(sdk, "undated")
    _reading(sdk, indicator, 9.0, at="2026-11-12T05:00:00Z")
    _reading(sdk, indicator, 1.0, at="not-an-instant")
    got = latest_readings(sdk, indicator["id"])
    assert [r.value for r in got] == [9.0, 1.0]


# ── 4. the ``since`` filter composes with the ordering ──────────────────


def test_since_excludes_by_instant_on_the_correctly_ordered_list(sdk):
    """#11926's boundary filter still excludes by INSTANT, on the ordered list.

    ⚠️ This is a REGRESSION pin, not a defect proof: it PASSES on the pre-fix
    code too (two rows fit any page, and `since` filtering is order-independent),
    so it cannot testify that the ordering was fixed. What it pins is that the
    fix did not change the boundary's meaning for an INTERIOR boundary — no
    reading here sits exactly on it.

    The two boundary EDGES are pinned in ``tests/test_11926_derived_invalidation.py``
    (not in this file): equality still admits
    (``tests/test_11926_derived_invalidation.py:821``, the assertion) and an
    incomparable boundary excludes everything (``since="not-an-instant"`` → ``[]``).
    """
    indicator = _indicator(sdk, "since")
    _reading(sdk, indicator, 12.0, at="2026-11-12T01:00:00-05:00")  # 06:00Z
    _reading(sdk, indicator, 5.0, at="2026-11-12T05:00:00Z")  # 05:00Z
    kept = latest_readings(
        sdk, indicator["id"], since="2026-11-12T05:30:00+00:00"
    )
    assert [r.value for r in kept] == [12.0]


# ── 5. the disclosure (#11823's class, documentation form) ────────────────


def test_the_docstring_discloses_the_cap_and_claims_no_complete_set():
    """``limit`` binds this read and a list cannot report that it was hit, so
    the docstring must SAY so — the same disclosure ``live_judgements`` carries
    ("⛔ ``limit`` IS A CAP, NOT A COURTESY").

    ⛔ EACH ASSERTION IS TIED TO THE SENTENCE THAT CARRIES ITS CLAIM, because a
    membership check over the WHOLE doc is satisfied by unrelated prose: at
    ``00d1ef7e16`` both of the mutations below passed this test.

    * deleting the whole ``limit IS A CAP`` paragraph stayed green — ``"cap"``
      was matched by a *different* paragraph ("a server-side result-set **cap**
      cannot silently truncate it in store order");
    * rewording the opening back to a complete-set claim ("Every reading
      recorded about ``indicator_id``…") stayed green — the check matched ONE
      historical literal, so any other wording of the same overclaim was
      invisible.

    So the two disclosures are asserted by their OWN literals, and the
    no-complete-set rule is enforced from BOTH sides — a denylist on the
    summary line and a denylist over the whole doc — because each side alone was
    measured to miss the other's mutant:

    * an opening-only rule (this test's previous shape) stayed green on
      *"Up to all readings recorded about ``indicator_id`` (``limit`` caps the
      list), newest first."* and on the historical sentence pasted back as a
      LATER paragraph;
    * a bounding-phrase ALLOWLIST over-fitted — it red-flagged the honest
      *"The ``limit`` most recent readings about…"* and *"No more than
      ``limit``…"*;
    * ``doc.split(". ")[0]`` used prose punctuation to find the summary, so an
      abbreviation truncated "the opening" (it is now the first LINE).

    ⚠️ Disclosed over-block: the doc-wide list bans *"the readings recorded
    about"*, so a future paragraph opening *"The readings recorded about an
    indicator are ordered…"* would red this test for a non-defect. That is the
    accepted direction — a false green here re-opens #11823, a false red is one
    line for the next editor — and it is why the failure message names the
    phrase it found.
    """
    raw = latest_readings.__doc__ or ""
    low = " ".join(raw.split()).lower()
    assert "is a cap, not a courtesy" in low, (
        "the docstring must disclose ``limit`` as a CAP in that sentence — a "
        "bare occurrence of the word 'cap' elsewhere in the doc is not the "
        "disclosure (#11823)"
    )
    assert "does not bound the fetch" in low, (
        "the docstring must disclose that ``limit`` bounds the ANSWER and not "
        "the FETCH — that is the half a caller reasons about cost from"
    )
    # The summary LINE, not a prose-delimited fragment: the sentence a caller
    # reads as the contract is the first line of the docstring.
    opening = raw.strip().splitlines()[0].lower()
    assert "limit" in opening, (
        "the summary line is what a caller reads as the contract, so it must "
        "name the bound"
    )
    overclaim = [w for w in ("every", "all ", "complete", "entire") if w in opening]
    assert not overclaim, (
        f"the summary line claims a COMPLETE set over a bounded read "
        f"({overclaim}) — #12322 exists because that claim was made and is false"
    )
    pasted = [
        p
        for p in (
            "the readings recorded about",
            "every reading recorded about",
            "all readings recorded about",
            "all of the readings about",
            "complete set of readings",
            # MEASURED gaps (cycle 7): these three wordings survived the four
            # literals above when appended as a later paragraph.
            "full history",
            "whole history",
            "entire history",
            # MEASURED gaps (cycle 8): six more wordings that survived the list
            # above when appended as a later paragraph.
            "every reading about",
            "all readings about",
            "the complete history",
            "every measurement about",
            "in full",
            "no reading is omitted",
        )
        if p in low
    ]
    assert not pasted, (
        f"a COMPLETE-set claim reappeared in the docstring ({pasted}); the "
        "summary rule alone cannot see a claim pasted into a later paragraph. "
        "⚠️ This list is an ENUMERATION and will never be complete — a new "
        "wording of the same claim can evade it. It is fail-closed on every "
        "spelling measured so far (14 of them); the direction to extend is this "
        "tuple, and the direction to distrust is a green run over a REWORDED "
        "docstring."
    )
