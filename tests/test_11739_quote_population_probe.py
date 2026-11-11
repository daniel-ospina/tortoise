"""#11739 — the `quote` population probe's arithmetic and its fail-closed walk.

The probe (`docs/evidence/11739-quote-population/probes/quote_population.py`) publishes a
NUMBER that a later gate decision is taken on, so the two things that could make it wrong are
pinned here rather than trusted:

* the bucketing — a whitespace-only `quote` is NOT a quote, and an unreadable page is not a
  page of empties (the fail-open direction: `0% quoted` is a claim, not a missing read);
* the completeness guard — the walk refuses to publish a count unless it can show the pages
  partition the order, which is the #5346 loss class (a page walk that silently drops and
  repeats rows while `hasMore` still reports completion).

Every test names the mutation that reds it. No network here: the graph client is a fake, so
this file is safe in the docker lane and in CI.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PROBE = (REPO / "docs" / "evidence" / "11739-quote-population" / "probes"
         / "quote_population.py")

sys.path.insert(0, str(REPO))


def _load_probe():
    """Import the probe by PATH — its directory name (`11739-quote-population`) is not a
    legal module name, so a plain `import` cannot reach it."""
    spec = importlib.util.spec_from_file_location("quote_population_probe", PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


qp = _load_probe()


class _Page:
    """A fake MCP client: one canned `tortoise_query` page per (population, offset).

    Keyed on `include_retracted` as well as `offset` on purpose: the census asks for ONE
    pass that INCLUDES terminal rows (so a fake keyed on offset alone would silently serve
    the wrong population, which is how this fake was first written — the census test
    caught it when the census still read two populations).
    """

    def __init__(self, pages: dict[tuple[bool, int], dict]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, dict]] = []

    def tool(self, name: str, arguments: dict) -> object:
        self.calls.append((name, arguments))
        key = (bool(arguments.get("include_retracted")), int(arguments.get("offset", 0)))
        if key not in self.pages:
            raise AssertionError(f"unexpected page {key}: {sorted(self.pages)}")
        return self.pages[key]


def _page(rows: list[dict], total: int, has_more: bool) -> dict:
    return {"results": rows, "total": total, "hasMore": has_more}


# --------------------------------------------------------------------------- bucketing

@pytest.mark.parametrize("value, expected", [
    ({"quote": "put the kettle on"}, (True, 17)),      # a real verbatim span
    ({"quote": ""}, (False, 0)),
    ({"quote": "   \n\t "}, (False, 0)),               # whitespace carries no source words
    ({"quote": None}, (False, 0)),
    ({}, (False, 0)),                                  # absent key is NOT a quote
    ({"quote": 12345}, (False, 0)),                    # a non-string cannot be read as text
])
def test_has_quote_treats_only_real_text_as_a_quote(value, expected):
    """FAILS IF the emptiness test becomes `bool(row.get("quote"))` — which counts a
    whitespace-only quote as populated (the flattering direction) — or if an absent key
    starts counting as a zero-length quote."""
    assert qp._has_quote(value) == expected


def test_month_is_keys_the_capture_month_not_a_guess():
    """FAILS IF the month is derived with a lenient parse: an unusable stamp must land in
    `unknown` rather than silently in the first bucket."""
    assert qp._month({"createdAt": "2026-11-10T23:27:23Z"}) == "2026-11"
    assert qp._month({"createdAt": "not-a-date"}) == "unknown"
    assert qp._month({}) == "unknown"


def test_finish_publishes_no_percentage_over_an_empty_population():
    """FAILS IF a zero population yields `0.0%` instead of None — a percentage of nothing is
    the fabricated figure this probe must never print."""
    out = qp._finish({"empty": {
        "points": 0, "quote_non_empty": 0, "quote_empty_or_absent": 0,
        "with_vector": 0, "with_vector_quote_non_empty": 0, "_chars": []}})
    assert out["empty"]["quote_non_empty_pct"] is None
    assert out["empty"]["with_vector_quote_non_empty_pct"] is None
    assert out["empty"]["quote_chars"] is None


def test_finish_reports_the_two_ratios_separately():
    """The graph-wide ratio and the embedded-only ratio are DIFFERENT populations: the
    composed text only replaces an embedding input, so an unembedded point's empty quote is
    not a gap. FAILS IF one ratio is used for both."""
    bucket: dict = {}
    qp._bump(bucket, "k", non_empty=True, length=200, embedded=True)
    qp._bump(bucket, "k", non_empty=True, length=100, embedded=True)
    qp._bump(bucket, "k", non_empty=False, length=0, embedded=False)
    qp._bump(bucket, "k", non_empty=False, length=0, embedded=True)
    out = qp._finish(bucket)["k"]
    assert out["points"] == 4
    assert out["quote_non_empty_pct"] == 50.0
    # 3 embedded, 2 of them quoted
    assert out["with_vector"] == 3
    assert out["with_vector_quote_non_empty_pct"] == pytest.approx(66.67)
    assert out["quote_chars"] == {"min": 100, "median": 200, "max": 200}


# ----------------------------------------------------------------------- endpoint shaping

@pytest.mark.parametrize("raw, expected", [
    ("https://api.premiselabs.co", "https://api.premiselabs.co/mcp/"),
    ("https://api.premiselabs.co/", "https://api.premiselabs.co/mcp/"),
    ("https://api.premiselabs.co/mcp", "https://api.premiselabs.co/mcp/"),
    ("https://api.premiselabs.co/mcp/", "https://api.premiselabs.co/mcp/"),
])
def test_mcp_endpoint_appends_the_route_exactly_once(raw, expected):
    """FAILS IF the base URL is POSTed as-is: `TORTOISE_API_URL` is the API base, and the
    bare base answers `404 {"detail":"Not Found"}` — which the probe would report as an
    unreachable graph while the graph is healthy (measured while writing this)."""
    assert qp._mcp_endpoint(raw) == expected


# ------------------------------------------------------------------- the fail-closed walk

def test_walk_returns_rows_when_the_pages_partition_the_order():
    """FAILS IF `walk` stops RETURNING the rows the pages partitioned — the positive control
    for the four exit-4 arms below: a walk that refused everything would satisfy every refusal
    assertion and publish nothing."""
    rows = [{"id": "a", "quote": "x"}, {"id": "b"}]
    client = _Page({(True, 0): _page(rows, total=2, has_more=False)})
    walked, total, distinct = qp.walk(client)
    assert walked["rows"] == rows and total == 2 and distinct == 2
    assert walked["pages"] == 1


def test_walk_refuses_a_short_page_that_claims_more(monkeypatch):
    """FAILS IF the completeness guard is removed: a short page with `hasMore=true` means the
    windows are not adjacent ranges of ONE order (#5346), so the walk must exit 4 rather than
    publish a partial count that reads like a full one."""
    monkeypatch.setattr(qp, "PAGE", 3)
    client = _Page({(True, 0): _page([{"id": "a"}], total=5, has_more=True)})
    with pytest.raises(SystemExit) as exc:
        qp.walk(client)
    assert exc.value.code == 4


def test_a_later_short_page_that_claims_more_exits_four(monkeypatch):
    """FAILS IF the loop's short-page arm is removed (review cycle 19): `test_walk_refuses_a_
    short_page_that_claims_more` only drives PAGE 1, whose own guard fires first, so the arm
    inside the loop was unpinned — deleting it left the suite green apart from the digest
    tripwire. Early attribution is what it buys: without it a short page that still claims
    more costs a full extra round trip and only trips the end-of-walk count check."""
    monkeypatch.setattr(qp, "PAGE", 2)
    client = _Page({
        (True, 0): _page([{"id": "a"}, {"id": "b"}], total=5, has_more=True),
        (True, 2): _page([{"id": "c"}], total=5, has_more=True),
    })
    with pytest.raises(SystemExit) as exc:
        qp.walk(client)
    assert exc.value.code == 4


def test_walk_refuses_a_repeated_row(monkeypatch):
    """FAILS IF the distinct-id check is dropped: the SAME row served twice while `total`
    agrees is exactly how a lossy page walk reports completion (measured 390 of 21,281
    never read on the hosted graph, #5346)."""
    monkeypatch.setattr(qp, "PAGE", 1)
    client = _Page({(True, 0): _page([{"id": "a"}], total=2, has_more=True),
                    (True, 1): _page([{"id": "a"}], total=2, has_more=False)})
    with pytest.raises(SystemExit) as exc:
        qp.walk(client)
    assert exc.value.code == 4


def test_walk_refuses_a_count_that_disagrees_with_the_reported_total():
    """FAILS IF the walk publishes whatever it collected: a `total` of 3 read off 2 rows is
    a denominator the census would then divide by."""
    client = _Page({(True, 0): _page([{"id": "a"}, {"id": "b"}], total=3, has_more=False)})
    with pytest.raises(SystemExit) as exc:
        qp.walk(client)
    assert exc.value.code == 4


def test_walk_refuses_a_shape_that_is_not_paginated():
    """FAILS IF a bare list result (the un-paginated arm of `tortoise_query`) is accepted: it
    carries no `total`, so the denominator would be the page size — an off-by-page-size
    census that looks complete."""
    client = _Page({(True, 0): [{"id": "a"}]})
    with pytest.raises(SystemExit) as exc:
        qp.walk(client)
    assert exc.value.code == 4


# ----------------------------------------------------------------------------- the census

def test_census_measures_the_quote_population_and_its_two_denominators():
    """End-to-end over a fake graph: ONE pass WITH the terminal rows, per-kind buckets, and
    the embedded-only ratio. FAILS IF the terminal subset stops being excluded from the
    headline (the denominator would then include tombstones), if a second pass is
    reintroduced (the census would cost twice the deep SKIPs), or if a kind's absent quotes
    are dropped from the denominator instead of counted."""
    live_rows = [
        {"id": "p1", "pointKind": "statement", "quote": "a verbatim source span",
         "embedding": [0.1], "createdAt": "2026-11-01T00:00:00Z"},
        {"id": "p2", "pointKind": "statement", "quote": "   ",
         "embedding": [0.1], "createdAt": "2026-11-01T00:00:00Z"},
        {"id": "p3", "pointKind": "decision", "embedding": None,
         "createdAt": "2026-11-02T00:00:00Z"},
    ]
    all_rows = [*live_rows, {"id": "p4", "pointKind": "statement", "quote": "gone",
                             "embedding": None, "status": "superseded",
                             "createdAt": "2026-11-02T00:00:00Z"}]
    client = _Page({(True, 0): _page(all_rows, total=4, has_more=False)})

    report = qp.census(client)

    # ONE pass, terminal rows INCLUDED — both populations are derived from it
    assert [c[1].get("include_retracted") for c in client.calls] == [True]
    head = report["headline"]
    assert head["live_points"] == 3
    assert head["tombstones"] == 1
    assert head["quote_non_empty"] == 1                    # whitespace is not a quote
    assert head["quote_non_empty_pct"] == pytest.approx(33.33)
    assert head["points_with_vector"] == 2
    assert head["points_with_vector_and_quote"] == 1
    assert head["vector_quote_non_empty_pct"] == 50.0       # 1 of 2 embedded
    assert report["by_point_kind"]["statement"]["points"] == 2
    assert report["by_point_kind"]["decision"]["quote_non_empty_pct"] == 0.0
    # the names half's property presence is reported — its POSITIVE arm is pinned in
    # `test_census_counts_an_about_property_and_says_what_it_is_not`; this negative control
    # exists so a scan that starts reporting every row reds here.
    assert report["about_property_presence"]["points_with_any_about_property"] == 0
    assert "not_measured" in report["about_property_presence"]


# --------------------------------------------------- the wait-budget retry (once, only)

def _reply(text: str) -> dict:
    return {"result": {"content": [{"type": "text", "text": text}], "isError": False}}


def test_is_wait_budget_matches_the_server_notice_and_nothing_else():
    """FAILS IF the predicate widens to "any unparseable reply": a RETIRED-TOOL notice or
    a malformed payload must not be retried into silence — only the notice that says the
    query did not run."""
    notice = ("The server's wait budget for this request was exceeded before a response "
              "was ready. The work may still complete on the server.")
    assert qp._is_wait_budget([notice]) is True
    assert qp._is_wait_budget(["RETIRED TOOL: it still answers, but is not advertised"]) is False
    assert qp._is_wait_budget([]) is False


def test_tool_retries_a_wait_budget_page_exactly_once_then_returns_the_payload():
    """FAILS IF the retry is removed (a busy graph makes the walk fail one run in two) or
    if it loops (an unavailable graph must exit, not spin)."""
    notice = "The server's wait budget for this request was exceeded before a response was ready."
    replies = [_reply(notice), _reply('[{"id": "a"}]')]
    client = qp._Client("https://example.invalid/mcp/", "k")
    calls: list[str] = []

    def _call(method, params, timeout=180):
        calls.append(method)
        return replies.pop(0)

    client._call = _call
    assert client.tool("tortoise_query", {"limit": 1}) == [{"id": "a"}]
    assert len(calls) == 2


def test_tool_returns_none_when_the_wait_budget_misses_twice():
    """FAILS IF a second budget miss is retried again here (a probe that keeps asking the
    same heavy question reports nothing at all) — the signal is handed to the CALLER, which
    is the only layer that can ask for the same rows at a smaller size."""
    notice = "The server's wait budget for this request was exceeded before a response was ready."
    client = qp._Client("https://example.invalid/mcp/", "k")
    calls: list[str] = []

    def _call(method, params, timeout=180):
        calls.append(method)
        return _reply(notice)

    client._call = _call
    assert client.tool("tortoise_query", {"limit": 1}) is None
    assert len(calls) == 2          # one retry, then hand off — never a third attempt


# ------------------------------- the shrink ladder (bounded by MAX_RUNGS, 4 at PAGE=500)

class _BudgetThenPage:
    """A fake client whose `tortoise_query` misses the wait budget above a size threshold."""

    def __init__(self, rows: list[dict], ok_at_or_below: int) -> None:
        self.rows = rows
        self.ok_at_or_below = ok_at_or_below
        self.limits: list[int] = []

    def tool(self, name: str, arguments: dict) -> object:
        limit = int(arguments["limit"])
        self.limits.append(limit)
        if limit > self.ok_at_or_below:
            return None
        start = int(arguments.get("offset", 0))
        chunk = self.rows[start:start + limit]
        return {"results": chunk, "total": len(self.rows),
                "hasMore": start + limit < len(self.rows)}


def test_walk_shrinks_the_page_instead_of_giving_up():
    """FAILS IF a persistent wait budget ends the run: a page too heavy at one size is often
    fine smaller, and the SAME rows are still asked for (only `limit` moves). The artifact
    records the size it settled on, so a shrunk run is visible rather than silent."""
    rows = [{"id": f"p{i}"} for i in range(300)]
    client = _BudgetThenPage(rows, ok_at_or_below=250)
    walked, total, distinct = qp.walk(client)
    assert total == 300 and distinct == 300 and len(walked["rows"]) == 300
    assert client.limits[0] == qp.PAGE                      # asked at the declared size first
    assert client.limits[1] == qp.PAGE // 2                 # then half
    assert walked["final_page_size"] == qp.PAGE // 2


def test_the_ladder_actually_reaches_the_documented_floor():
    """FAILS IF the ladder is a bare `range(3)` of shifts: `max(MIN_PAGE, 500 >> 2)` is 125, so
    the documented `MIN_PAGE` rung was never asked for and its `break` was dead code (review
    cycle 3, finding 3). The floor must be a size the ladder actually tries."""
    assert qp._ladder(qp.PAGE) == [qp.PAGE, 250, 125, qp.MIN_PAGE]
    assert qp._ladder(qp.MIN_PAGE) == [qp.MIN_PAGE]
    assert qp._ladder(50) == [50]              # already below the floor: nothing to halve to
    assert len(qp._ladder(10 * qp.MIN_PAGE)) <= qp.MAX_RUNGS


def test_walk_exits_three_when_even_the_floor_misses(monkeypatch):
    """FAILS IF the ladder loops past its floor: a page that misses at MIN_PAGE is the graph
    not serving the query, and the run must end with 3 rather than spin."""
    monkeypatch.setattr(qp, "PAGE", 4 * qp.MIN_PAGE)
    client = _BudgetThenPage([{"id": f"p{i}"} for i in range(10)], ok_at_or_below=0)
    with pytest.raises(SystemExit) as exc:
        qp.walk(client)
    assert exc.value.code == 3
    assert client.limits == [4 * qp.MIN_PAGE, 2 * qp.MIN_PAGE, qp.MIN_PAGE]   # 3 rungs, no more


# ------------------------- the reading of `quote` is the PRODUCT's reading (finding 1)

def test_quote_text_mirrors_the_products_composer_reader():
    """FAILS IF the probe's reading of `quote` drifts from `embed_compose.quote_text`.

    `quote` is a declared flatten-LIST property, so a record can carry several spans. The
    probe keeps a local MIRROR (it runs under a bare `python3`, with no project install, so
    importing the composer would make the artifact unreproducible) and this case is what
    makes the mirror provably a mirror rather than a second opinion.
    """
    from tortoise.embed_compose import quote_text as product_reader

    shapes = [
        "a verbatim source span",
        "  padded   spans \n",
        "",
        "   ",
        None,
        0,
        ["first span", "second span"],
        ["first", "", "  ", "second"],
        ("tuple", "spans"),
        [],
        [{"not": "text"}],
        [123, "polyglot"],
    ]
    for shape in shapes:
        assert qp._quote_text(shape) == product_reader(shape), shape


def test_has_quote_reads_a_list_valued_quote_as_quoted():
    """FAILS IF a list-valued `quote` is classified as NO quote — which under-reported the
    population the composed text is about, and hid that class behind the emptiness test
    (review finding 1)."""
    non_empty, length = qp._has_quote({"quote": ["a verbatim span", "and more"]})
    assert non_empty is True
    assert length == len("a verbatim span and more")
    # ...and an EMPTY list is not a quote either
    assert qp._has_quote({"quote": []}) == (False, 0)


def test_quote_shape_names_each_class_so_other_cannot_hide():
    """FAILS IF the shape census collapses an unrecognised value into `absent_or_null`: the
    artifact must be able to show a class it does not understand rather than fold it into
    the emptiness arm."""
    assert qp._quote_shape("text") == "str"
    assert qp._quote_shape(["a", "b"]) == "list"
    assert qp._quote_shape(None) == "absent_or_null"
    assert qp._quote_shape({"weird": "object"}) == "other"
    assert qp._quote_shape(7) == "other"


# ---------------------------------- the aggregates the README leans on (finding 5)

def test_census_reports_chars_cap_shapes_month_and_about_presence():
    """FAILS IF any of these aggregates is dropped or its comparison flipped:

    * `quote_chars_all` (min/median/max/n) — the README's "median 137";
    * `at_cap` at `>= QUOTE_CAP` — the README's "2,260 at or above the cap" (the `>=` matters:
      a strict `== 200` count would be smaller, and the artifact states the basis);
    * `by_month` — the README's "one bucket, so no trend is claimed";
    * `quote_shapes` / `about_property_presence` (the POSITIVE arm).
    """
    q199, q200, q250 = "x" * 199, "y" * 200, "z" * 250
    # STORED 201 / COLLAPSED 199: the exact row that separates the two `at_cap` bases. Without
    # it this test asserts `_raw_length()` in isolation and the census could measure the
    # collapsed text without any test noticing (review cycle 5, finding 3 — verified by
    # flipping `_raw_length(...)` to `len(_quote_text(...))` in `census()`, which reds THIS
    # test and nothing else but the digest tripwire; no case count is recorded here, because
    # a hand-written count re-stales — `config/ci-surfaces.yml` states the same rule).
    qsplit = "w" * 199 + "  "
    rows = [
        {"id": "a", "pointKind": "statement", "quote": q199, "embedding": [0.1],
         "createdAt": "2026-11-01T00:00:00Z"},
        {"id": "b", "pointKind": "statement", "quote": q200, "embedding": [0.1],
         "createdAt": "2026-11-01T00:00:00Z"},
        {"id": "c", "pointKind": "statement", "quote": q250, "embedding": [0.1],
         "createdAt": "2026-12-04T00:00:00Z"},
        {"id": "d", "pointKind": "statement", "quote": ["span one", "span two"],
         "embedding": None, "createdAt": "2026-12-04T00:00:00Z"},
        {"id": "e", "pointKind": "statement", "quote": {"odd": "shape"},
         "embedding": None, "createdAt": "2026-12-04T00:00:00Z"},
        {"id": "f", "pointKind": "statement", "quote": None, "embedding": None,
         "createdAt": "2026-12-04T00:00:00Z", "aboutSubjects": "the kettle"},
        {"id": "g", "pointKind": "statement", "quote": qsplit, "embedding": None,
         "createdAt": "2026-12-04T00:00:00Z"},
    ]
    client = _Page({(True, 0): _page(rows, total=len(rows), has_more=False)})
    report = qp.census(client)

    # q199, q200, q250, "span one span two" and qsplit = 5 quoted rows (the odd shape is not)
    chars = report["quote_chars_all"]
    assert chars["n"] == 5
    assert chars["min"] == 17            # "span one span two"
    assert chars["max"] == 250
    # 200, 250 and qsplit(STORED 201, COLLAPSED 199) — the 199 does NOT count. A collapsed
    # basis would count 2 here, so this assertion is what pins the stored basis.
    assert chars["at_cap"] == 3
    assert chars["cap"] == qp.QUOTE_CAP
    # the two BASES are deliberately different: a 200-char STORED quote whose double space
    # collapses to 199 is a clamp hit that the composed length does not show (review
    # finding 1, second pass: measuring the collapsed text dropped 359 of 2,260).
    assert qp._raw_length("y" * 199 + " " * 1 + "y") == 201
    assert qp._raw_length(["abc", "de"]) == 5
    assert qp._raw_length(None) == 0
    # two months, so the README's "one bucket" claim is measurable rather than assumed
    assert sorted(report["by_month"]) == ["2026-11", "2026-12"]
    assert report["by_month"]["2026-11"]["quote_non_empty"] == 2
    assert report["quote_shapes"] == {"absent_or_null": 1, "list": 1, "other": 1, "str": 4}
    about = report["about_property_presence"]
    assert about["points_with_any_about_property"] == 1
    assert about["keys_seen"] == ["aboutSubjects"]


def test_census_counts_an_about_property_and_says_what_it_is_not():
    """The POSITIVE arm of the `about*` scan (review finding 6): FAILS IF the scan breaks
    (a typo in the prefix, or the whole block deleted) — the negative control alone could
    not move, because its rows carry no `about*` key at all."""
    rows = [{"id": "a", "pointKind": "statement", "quote": "text", "embedding": None,
             "createdAt": "2026-11-01T00:00:00Z", "aboutObjects": ["the kettle", "the pot"]}]
    client = _Page({(True, 0): _page(rows, total=1, has_more=False)})
    report = qp.census(client)
    assert report["about_property_presence"]["points_with_any_about_property"] == 1
    assert report["about_property_presence"]["keys_seen"] == ["aboutObjects"]
    # an EMPTY about* value is not a relation and must not be counted
    rows[0]["aboutObjects"] = []
    client = _Page({(True, 0): _page(rows, total=1, has_more=False)})
    assert qp.census(client)["about_property_presence"]["points_with_any_about_property"] == 0


def test_census_buckets_the_terminal_rows_by_status_from_the_same_walk():
    """FAILS IF the terminal rows the single walk already holds are THROWN AWAY (review
    finding 3): they are the only place a PRIOR quote state is observable, so dropping them
    left "provenance, not decay" with nothing behind it. Also pins that the split is on the
    DECLARED terminal set rather than "not live" — a `draft` is served by the default read,
    so a "not live" bucket counted one row in both populations (2,007 of them, measured) —
    and that the walk cost is reported (review finding 4)."""
    rows = [
        {"id": "a", "pointKind": "statement", "quote": "text", "embedding": None,
         "status": "live", "createdAt": "2026-11-01T00:00:00Z"},
        {"id": "b", "pointKind": "statement", "quote": "a retracted claim's span",
         "embedding": None, "status": "retracted", "createdAt": "2026-11-01T00:00:00Z"},
        {"id": "c", "pointKind": "statement", "embedding": None, "status": "superseded",
         "createdAt": "2026-11-01T00:00:00Z"},
        {"id": "d", "pointKind": "statement", "quote": "a draft is served by the default read",
         "embedding": None, "status": "draft", "createdAt": "2026-11-01T00:00:00Z"},
    ]
    client = _Page({(True, 0): _page(rows, total=4, has_more=False)})
    report = qp.census(client)

    # the walk read all four; the HEADLINE divides by the two non-terminal rows
    assert report["headline"]["live_points"] == 2      # live + draft
    assert report["headline"]["tombstones"] == 2       # retracted + superseded
    by_status = report["by_status_terminal"]
    assert sorted(by_status) == ["retracted", "superseded"]   # a draft is NOT terminal
    assert by_status["retracted"]["quote_non_empty"] == 1
    assert by_status["superseded"]["quote_non_empty_pct"] == 0.0
    assert report["terminal_statuses"] == sorted(qp.TERMINAL_STATUSES)
    assert report["pages_walked"] == 1
    assert report["rows_walked"] == 4
    # `by_month` covers EVERY row the walk read, terminal ones included (finding 2) — a
    # month census over half the walk is a claim the artifact could not support.
    assert report["by_month"]["2026-11"]["points"] == 4
    # `quote_shapes` covers the LIVE subset only (review cycle 5, finding 4): the two live
    # rows are `str`, and the retracted row's quote must NOT appear here — the README says so
    # in prose, and prose about scope is unpinned unless a terminal row's quote stays out.
    assert report["quote_shapes"] == {"str": 2, "list": 0, "absent_or_null": 0, "other": 0}


# --------------------------- the reply shapes must exit 3/4, never 1 (finding 2)

def test_a_missing_key_exits_three_and_never_reports_zero(monkeypatch, capsys):
    """FAILS IF `main()`'s no-key guard is deleted or weakened - the arm the README and
    `docs/00_index.md` quote verbatim: a missing key is NEVER an empty graph, so it exits 3
    rather than publishing `quote_non_empty: 0` (review cycle 17: no test exercised `main()`).
    """
    monkeypatch.delenv("TORTOISE_MCP_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    assert qp.main(["quote_population"]) == 3
    assert "NO-KEY" in capsys.readouterr().err


class _RawReply:
    """A client whose `_call` returns an arbitrary JSON value — the shape guards' seam."""

    value = None

    def __init__(self, value) -> None:
        self.value = value

    def _call(self, method: str, params: dict):
        return self.value

    def tool(self, name: str, arguments: dict, *a, **k):
        return qp._Client.tool(self, name, arguments, *a, **k)


@pytest.mark.parametrize("value", [[], "a newline-free notice", 7, None,
                          {"result": "not-an-object"},
                          {"result": {"content": ["a bare string block"]}},
                          {"result": {"content": "not-a-list"}},
                          {"result": {"content": [{"type": "text", "text": 7}]}},
                          {"result": {"content": [{"type": "text", "text": None}]}},
                          {"result": {"content": [{"type": "text", "text": {"a": 1}}]}}])
def test_a_parseable_reply_that_is_not_a_jsonrpc_object_exits_three(value):
    """FAILS IF `tool()` assumes any layer of the reply is a dict: `"error" in reply` on a
    list/str/int, `result.get(...)` on a string result, `c.get(...)` on a bare string content
    block, or `texts` holding a non-string `text` each raise TypeError/AttributeError and exit
    1 — OUTSIDE the documented 0/2/3/4 contract, so a caller keying on 3 or 4 reads an
    unrecognised verdict as success or as incompleteness (review cycles 14, 15 and 16, each
    reproduced in-process before its guard)."""
    client = _RawReply(value)
    with pytest.raises(SystemExit) as exc:
        client.tool("tortoise_query", {"limit": 1})
    assert exc.value.code == 3


@pytest.mark.parametrize("bad_id", [{"a": 1}, ["a"], {"x": {"y": 1}}])
def test_an_unhashable_row_id_exits_four(bad_id):
    """FAILS IF the page guard stops at "is a dict": `walk` dedups ids in a SET, so a dict or
    list `id` raises `TypeError: unhashable type` and the run exits 1 (review cycle 17,
    reproduced in-process). Exit 4 - the page arrived and looks like a page, it just cannot be
    read as identifiable rows."""
    client = _Page({(True, 0): _page([{"id": bad_id}], total=1, has_more=False)})
    with pytest.raises(SystemExit) as exc:
        qp.walk(client)
    assert exc.value.code == 4


def test_a_jsonrpc_error_envelope_exits_three():
    """FAILS IF the `error` arm stops being fail-closed (its polarity flipped to 4, or the arm
    deleted): a tool error means the query did not run, so the run must never publish a count
    from it (review cycle 17 - this arm was unpinned, and the digest tripwire cannot tell a
    flipped polarity from a re-synced one)."""
    client = _RawReply({"jsonrpc": "2.0", "id": 1,
                        "error": {"code": -32000, "message": "no such tool"}})
    with pytest.raises(SystemExit) as exc:
        client.tool("tortoise_query", {"limit": 1})
    assert exc.value.code == 3


@pytest.mark.parametrize("content", [[], [{"type": "text", "text": "<html>gateway</html>"}]])
def test_a_reply_with_no_json_payload_exits_three(content):
    """FAILS IF the no-payload arm is removed or its polarity flipped: a 2xx whose blocks carry
    no parseable JSON (an HTML gateway page, an empty content list) is a reply we could not
    read, and reading it as "0 quoted Points" is the failure the whole exit contract exists to
    prevent (review cycle 17)."""
    client = _RawReply({"jsonrpc": "2.0", "id": 1, "result": {"content": content}})
    with pytest.raises(SystemExit) as exc:
        client.tool("tortoise_query", {"limit": 1})
    assert exc.value.code == 3


def test_a_results_list_of_non_rows_exits_four():
    """FAILS IF the page guard checks only the CONTAINER: `results: ["row"]` is a reply we
    cannot read as rows, and `walk` would raise AttributeError on `row.get("id")` and exit 1
    (review cycle 14, reproduced before the guard). Exit 4 because the reply arrived and is
    shaped like a page — it just cannot be proven a partition of rows."""
    client = _Page({(True, 0): {"results": ["row"], "total": 1, "hasMore": False}})
    with pytest.raises(SystemExit) as exc:
        qp.walk(client)
    assert exc.value.code == 4


class _FakeHTTP:
    """A `urlopen` stand-in: one canned body, served as a context manager."""

    def __init__(self, body: str) -> None:
        self._body = body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def test_a_non_json_2xx_body_exits_three_not_one(monkeypatch):
    """FAILS IF the final `json.loads` sits outside the guard: an HTML gateway/error page in
    a 2xx raised `JSONDecodeError` and the process exited 1 — outside the documented
    contract, so a caller keying on 3 read an unrecognised verdict."""
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeHTTP("<html>nope</html>"))
    client = qp._Client("https://example.invalid/mcp/", "k")
    with pytest.raises(SystemExit) as exc:
        client._call("tools/call", {"name": "tortoise_query", "arguments": {}})
    assert exc.value.code == 3


class _UnreadableBody:
    """An `HTTPError` body whose `.read()` fails — a 504 whose body read times out.

    `close()` is not decoration: `HTTPError` wraps its fp in a `_TemporaryFileCloser` whose
    finalizer calls it, so a body without one raises an ignored AttributeError and every run
    emits a `PytestUnraisableExceptionWarning` — harmless today, a red under any
    `filterwarnings = error` (review cycle 19).
    """

    def read(self) -> bytes:
        import http.client

        raise http.client.IncompleteRead(b"")

    def close(self) -> None:
        return None


def test_an_http_error_whose_body_cannot_be_read_exits_three(monkeypatch, capsys):
    """FAILS IF the body read sits OUTSIDE a guard (review cycle 18: reverting cycle 17's fix
    left the suite green, because the digest tripwire cannot tell a reverted guard from a
    re-synced hash). A read that fails WHILE handling the `HTTPError` is still one transport
    verdict — exit 3 — not an uncaught exception exiting 1."""
    import urllib.error
    import urllib.request

    def boom(*a, **k):
        raise urllib.error.HTTPError("https://example.invalid/mcp/", 504, "Gateway Timeout",
                                     {}, _UnreadableBody())

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    client = qp._Client("https://example.invalid/mcp/", "k")
    with pytest.raises(SystemExit) as exc:
        client._call("tools/call", {"name": "tortoise_query", "arguments": {}})
    assert exc.value.code == 3
    err = capsys.readouterr().err
    assert "<unreadable error body>" in err
    assert "GRAPH-UNREACHABLE(HTTP 504)" in err


def test_a_readable_http_error_body_exits_three_and_is_reported(monkeypatch, capsys):
    """FAILS IF the `HTTPError` arm's polarity moves: 401/402/504 all mean "the query did not
    run", so the verdict is 3 (`never read as 0%`) and the body is what tells the operator
    which. Nothing else in the suite asserts either half (review cycle 18)."""
    import urllib.error
    import urllib.request

    def boom(*a, **k):
        raise urllib.error.HTTPError("https://example.invalid/mcp/", 402, "Payment Required",
                                     {}, _FakeBody(b'{"detail": "quota exceeded"}'))

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    client = qp._Client("https://example.invalid/mcp/", "k")
    with pytest.raises(SystemExit) as exc:
        client._call("tools/call", {"name": "tortoise_query", "arguments": {}})
    assert exc.value.code == 3
    err = capsys.readouterr().err
    assert "quota exceeded" in err


def test_a_transport_failure_exits_three(monkeypatch, capsys):
    """FAILS IF the transport arm is narrowed or dropped: a DNS/connection failure must be one
    verdict (3), named by its exception class, so the operator can tell unreachable from
    empty (review cycle 18 — `except Exception` narrowed to a specific type left the suite
    green, because the whole `_call` surface was unpinned)."""
    import urllib.error
    import urllib.request

    def boom(*a, **k):
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    client = qp._Client("https://example.invalid/mcp/", "k")
    with pytest.raises(SystemExit) as exc:
        client._call("tools/call", {"name": "tortoise_query", "arguments": {}})
    assert exc.value.code == 3
    assert "GRAPH-UNREACHABLE(URLError)" in capsys.readouterr().err


class _FakeBody:
    """A minimal `HTTPError`-compatible body: `.read()` returns canned bytes."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def close(self) -> None:  # `HTTPError`'s finalizer calls it; see `_UnreadableBody`
        return None


def test_page_one_without_a_total_exits_four():
    """FAILS IF the first-page shape guard checks `results` only: a reply missing `total`
    raised `KeyError` and exited 1."""
    client = _Page({(True, 0): {"results": [{"id": "a"}], "hasMore": False}})
    with pytest.raises(SystemExit) as exc:
        qp.walk(client)
    assert exc.value.code == 4


def test_a_later_page_that_is_not_a_page_exits_four(monkeypatch):
    """FAILS IF later pages go unchecked: a non-dict page raised `AttributeError` out of the
    walk (exit 1) instead of the contract's 4."""
    monkeypatch.setattr(qp, "PAGE", 1)
    client = _Page({(True, 0): _page([{"id": "a"}], total=2, has_more=True),
                    (True, 1): ["not a page"]})
    with pytest.raises(SystemExit) as exc:
        qp.walk(client)
    assert exc.value.code == 4


def test_terminal_statuses_mirror_the_products_own_constant():
    """FAILS IF the mirror drifts: `walk` includes every Point and the HEADLINE divides by
    the rows whose status is NOT in this set, so a status missing here would be counted as
    live (inflating no denominator but moving a tombstone into the headline population)."""
    from tortoise.live import TERMINAL_EXCLUDED_STATUSES

    assert frozenset(TERMINAL_EXCLUDED_STATUSES) == qp.TERMINAL_STATUSES


def test_quote_cap_mirrors_the_point_write_paths_own_constant():
    """FAILS IF the clamp the probe counts at drifts from the one the WRITE path applies.

    A Point's `quote` is stored as `_write_span(str(v).strip(), _MAX_QUOTE)`
    (`tortoise/extractor_v2.py`), so `_MAX_QUOTE` — not `_MENTION_CAPS["quote"]`, which is the
    MENTION builder's cap and which that module says is "not interchangeable" — is the basis
    `at_cap` must measure at. Both happen to be 200, and that coincidence is the hazard: a
    change to one alone would leave the census counting at a cap the write path no longer
    applies, with no artifact digest able to catch it (the probe itself would be unchanged).
    """
    from tortoise.extractor_v2 import _MAX_QUOTE

    assert qp.QUOTE_CAP == _MAX_QUOTE


# ------------------------------------------ cycle-2 findings: attribution and shapes

def test_quote_shapes_publishes_a_zero_for_every_class():
    """FAILS IF a class is omitted when empty: "0 list-valued" would then be an inference
    from a MISSING KEY rather than a count (review cycle 2, finding 7)."""
    rows = [{"id": "a", "pointKind": "statement", "quote": "text", "embedding": None,
             "createdAt": "2026-11-01T00:00:00Z"}]
    client = _Page({(True, 0): _page(rows, total=1, has_more=False)})
    shapes = qp.census(client)["quote_shapes"]
    assert shapes == {"str": 1, "list": 0, "absent_or_null": 0, "other": 0}


def test_a_non_integer_total_exits_four(monkeypatch):
    """FAILS IF `int(total)` is unguarded: a present-but-non-numeric `total` raised out of
    the walk and exited 1, outside the documented 0/2/3/4 contract (review cycle 2,
    finding 4)."""
    # `float("inf")` is the arm that needed its own exception class: `json.loads` accepts the
    # non-standard `Infinity` literal, and `int(inf)` raises OverflowError, not ValueError.
    for bad in ("abc", None, {"x": 1}, [1], float("inf"), float("nan")):
        client = _Page({(True, 0): {"results": [{"id": "a"}], "total": bad,
                                    "hasMore": False}})
        with pytest.raises(SystemExit) as exc:
            qp.walk(client)
        assert exc.value.code == 4, bad


def test_census_carries_provenance_binding_it_to_its_probe():
    """FAILS IF the artifact stops carrying (or binding) its provenance: `at_cap` is exactly
    the kind of cell a probe fix can flip — it moved by 359 during review — so a bundle that
    cannot name the probe that produced its numbers is unattributable (finding 3)."""
    rows = [{"id": "a", "pointKind": "statement", "quote": "text", "embedding": None,
             "createdAt": "2026-11-01T00:00:00Z"}]
    client = _Page({(True, 0): _page(rows, total=1, has_more=False)})
    prov = qp.census(client, endpoint="https://example.invalid/mcp/")["provenance"]
    assert prov["probe_sha256"] == qp._probe_digest()
    assert prov["endpoint"] == "https://example.invalid/mcp/"
    assert prov["measured_at"].endswith("Z") and len(prov["measured_at"]) == 20
    assert prov["head_sha"]


def test_the_committed_census_records_its_own_cost():
    """FAILS IF the artifact stops carrying the walk's wall clock.

    The README tells a reader to quote `duration_s` instead of a runtime range, because prose
    ranges went stale in review cycles 8 and 9 while the number itself moved with load. A
    README pointing at a field the artifact does not carry would be the same defect one step
    later, so the field is pinned here rather than described.
    """
    import json

    census_path = qp.pathlib.Path(qp.__file__).with_suffix("").parent.parent / "census.json"
    artifact = json.loads(census_path.read_text())
    assert isinstance(artifact["duration_s"], (int, float))
    assert artifact["duration_s"] > 0


def test_the_committed_census_was_produced_by_this_probe():
    """The bundle's own invariant, mechanically: the artifact's `probe_sha256` must equal the
    digest of the probe AS COMMITTED.

    FAILS IF the probe is edited without re-running the census — which is the drift that
    matters, because a probe fix can move a published number (it moved `at_cap` by 359). The
    repair is to re-run the probe (its cost is quoted in the bundle README — the walk's page
    count varies with load, so no estimate is repeated here), never to hand-edit the JSON.
    """
    import json

    census_path = qp.pathlib.Path(qp.__file__).with_suffix("").parent.parent / "census.json"
    artifact = json.loads(census_path.read_text())
    assert artifact["provenance"]["probe_sha256"] == qp._probe_digest(), (
        "census.json was produced by a DIFFERENT probe — re-run "
        "probes/quote_population.py; do not hand-edit the artifact")


# ------------------- the read path's SECOND conjunct: the legacy `outdated` flag

def test_census_excludes_flag_outdated_rows_from_the_headline():
    """FAILS IF the live subset is filtered on STATUS alone.

    The default read's predicate is `status not terminal` **AND**
    `coalesce(n.outdated, false) = false` (`search_engine._exclude_status_clause` →
    `live._terminal_excluded(…, include_outdated_flag=True)`), and `invalidate_point` sets the
    flag while LEAVING `status` untouched — so a status-only filter would count a flag-dead row
    as live, making "exactly the population the default read serves" false (review cycle 3,
    finding 2).
    """
    rows = [
        {"id": "a", "pointKind": "statement", "quote": "dead by the read path",
         "embedding": None, "status": None, "outdated": True,
         "createdAt": "2026-11-01T00:00:00Z"},
        {"id": "b", "pointKind": "statement", "quote": "alive", "embedding": None,
         "status": "live", "createdAt": "2026-11-01T00:00:00Z"},
    ]
    client = _Page({(True, 0): _page(rows, total=2, has_more=False)})
    report = qp.census(client)

    assert report["headline"]["live_points"] == 1          # only `b`
    assert report["headline"]["quote_non_empty"] == 1      # the flag-dead quote is NOT a live quote
    assert report["headline"]["tombstones"] == 1           # excluded, and attributed below
    flag = report["excluded_by_outdated_flag"]["outdated=true"]
    assert flag["points"] == 1
    assert flag["quote_non_empty"] == 1
    # a terminal ROW is not double-counted into the flag bucket
    assert "retracted" not in report["excluded_by_outdated_flag"]
