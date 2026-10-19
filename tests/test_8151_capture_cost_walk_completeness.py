"""#8151 — the live capture-cost pull must never end on a SHORT page.

PostgREST truncates a response at the deployment's ``db-max-rows`` (a project
setting; a proxy in front of it can cap it too). ``fetch_rows`` used to treat a
page shorter than the size it ASKED for as the end of the walk:

    if len(batch) < page:      # <-- end-of-walk signal (WRONG)
        return rows

so on any deployment tuned below 1000 rows it reported ``p50``/``p95`` over PAGE
ONE — silently, with no error and no signal. ``p95`` is the number this tool
exists to report, and it is exactly the number a truncated window biases.

This is the fail-OPEN class #5388 removed from ``hosted_api._iter_registered_orgs``
("a page-boundary end signal was wrong whenever a server's per-request cap is
below our ``limit``"); the fix never reached this caller. The sound rule is the
one #4038 implemented as ``SupabaseControlPlane.query_all``: a keyset walk on a
unique immutable cursor that ends ONLY on an EMPTY page, with the server's own
total from ``Content-Range`` (``Prefer: count=exact``) allowed to DOWNGRADE the
walk but never to promote it.

These tests drive the REAL ``fetch_rows`` through a PostgREST double that speaks
the wire protocol (``select`` projection, ``eq``/``gte``/``gt`` filters, ``order``,
``limit``, ``Prefer: count=exact`` + ``Content-Range``) and CAPS every response
below the page size the walk requests — i.e. it reproduces the deployment the
old code silently truncated on.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import pytest

PROD_URL = "https://ybetwichurajbfswfeqa.supabase.co"
PROD_KEY = "prod-service-role-key"

_CURSOR = "id"


def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", PROD_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", PROD_KEY)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)


def _rows(count: int) -> list[dict]:
    """``count`` rows of the shape ``fetch_rows`` selects, ids ascending."""
    stamp = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    return [
        {
            "id": f"id-{i:04d}",
            "event_name": "capture_cost",
            "org_id": "org-1",
            "properties": {"session_id": f"s-{i}", "calls": 1},
            "created_at": stamp,
        }
        for i in range(count)
    ]


class _CappedResp:
    """One PostgREST response, carrying the header the walk certifies from."""

    def __init__(self, payload: list, content_range: str):
        self.status_code = 200
        self.headers = {"Content-Range": content_range}
        self._payload = payload
        # ``query_with_total`` reads ``.content`` to tell "no rows" from "no
        # body"; a double without it would be indistinguishable from an empty
        # response and would hide the page it just served.
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload


class _CappedClient:
    """Stands in for ``httpx.Client``: PostgREST's wire protocol, capped pages.

    ``cap`` is the deployment's ``db-max-rows`` (or a proxy's cap): every
    response is truncated to that many rows NO MATTER what page size the caller
    asked for. That is the condition the old end-of-walk signal mistook for the
    end of the data.
    """

    rows: ClassVar[list[dict]] = []
    cap: ClassVar[int] = 1000
    #: Ids the store CLAIMS in its total but never serves — a server that skips
    #: rows mid-walk. The walk must refuse to certify rather than publish a
    #: lower bound as the window.
    unserved: ClassVar[set[str]] = set()
    gets: ClassVar[list[dict]] = []

    def __init__(self, *args, **kwargs):
        self.init_kwargs = kwargs

    @staticmethod
    def _matches(row: dict, params: dict) -> bool:
        for column in ("event_name", "created_at", _CURSOR):
            condition = params.get(column)
            if not condition:
                continue
            op, _, value = condition.partition(".")
            actual = row.get(column)
            if actual is None:
                return False
            if op == "eq" and actual != value:
                return False
            if op == "gte" and not actual >= value:
                return False
            if op == "gt" and not actual > value:
                return False
        return True

    def get(self, url, params=None, headers=None, **kwargs):
        params = dict(params or {})
        headers = dict(headers or {})
        type(self).gets.append(
            {"url": url, "params": params, "headers": headers})
        wanted = [c for c in (params.get("select") or "").split(",") if c]
        matched = [r for r in type(self).rows if self._matches(r, params)]
        # The server's TOTAL is a property of the FILTER, counted before this
        # page's own truncation (that is the whole point of the signal).
        total = len(matched)
        matched.sort(key=lambda r: r[_CURSOR],
                     reverse=str(params.get("order", "")).endswith(".desc"))
        limit = int(params.get("limit") or 1000)
        page = [r for r in matched[: min(limit, type(self).cap)]
                if r[_CURSOR] not in type(self).unserved]
        stated = total if headers.get("Prefer") == "count=exact" else "*"
        body = [{c: r.get(c) for c in wanted} if wanted else dict(r)
                for r in page]
        return _CappedResp(body, f"0-{max(len(page) - 1, 0)}/{stated}")


def _capped(monkeypatch, rows, *, cap=1000, unserved=()):
    _CappedClient.rows = rows
    _CappedClient.cap = cap
    _CappedClient.unserved = set(unserved)
    _CappedClient.gets = []
    monkeypatch.setattr("httpx.Client", _CappedClient)
    return _CappedClient


# ── the defect ─────────────────────────────────────────────────────────────


def test_a_page_capped_below_the_requested_size_does_not_end_the_walk(
        monkeypatch):
    """THE regression test. On the pre-fix shape this returns 3 rows (page one)
    with no error — the reported distribution is then computed over 30% of the
    window. Mutation that reds the fixed shape: any end-of-walk test that keys
    on the page being SHORTER than the size requested."""
    from tools import capture_cost_report as report

    _env(monkeypatch)
    client = _capped(monkeypatch, _rows(10), cap=3)

    rows = report.fetch_rows(7)

    assert [r["id"] for r in rows] == [
        f"id-{i:04d}" for i in range(10)], (
        "the walk stopped on a page the deployment had capped below the "
        f"requested size — {len(rows)} of 10 rows returned")
    assert len(client.gets) >= 4, (
        "a capped deployment needs one request per capped page plus the empty "
        f"page that ends the walk; saw {len(client.gets)}")


def test_the_walk_keeps_the_columns_the_report_reads(monkeypatch):
    """The cursor is a new column in the select, not a replacement: the report
    reads ``org_id``/``properties``/``created_at`` off these rows."""
    from tools import capture_cost_report as report

    _env(monkeypatch)
    _capped(monkeypatch, _rows(4), cap=2)

    rows = report.fetch_rows(7)

    assert len(rows) == 4, rows
    for row in rows:
        assert {"org_id", "properties", "created_at"} <= set(row), row


def test_each_page_after_the_first_continues_from_the_last_id_served(
        monkeypatch):
    """A keyset walk must page on the cursor, not on an offset: an offset
    paginates a window that rows are still being WRITTEN into, so a concurrent
    insert makes one row get served twice and another be skipped."""
    from tools import capture_cost_report as report

    _env(monkeypatch)
    client = _capped(monkeypatch, _rows(9), cap=3)

    report.fetch_rows(7)

    first, second = client.gets[0]["params"], client.gets[1]["params"]
    assert _CURSOR not in first, first
    assert second[_CURSOR] == "gt.id-0002", second
    assert client.gets[2]["params"][_CURSOR] == "gt.id-0005", client.gets[2]


def test_the_server_total_is_asked_for_on_the_first_page_only(monkeypatch):
    """``count_exact`` costs the server a COUNT, and a cursor-filtered later
    page would state the REMAINDER — so only page 1 may state the fleet."""
    from tools import capture_cost_report as report

    _env(monkeypatch)
    client = _capped(monkeypatch, _rows(7), cap=3)

    report.fetch_rows(7)

    assert client.gets[0]["headers"].get("Prefer") == "count=exact", (
        client.gets[0]["headers"])
    for later in client.gets[1:]:
        assert "Prefer" not in later["headers"], later


# ── the explicit, bounded refusal ──────────────────────────────────────────


def test_a_walk_the_store_contradicts_is_refused_never_truncated(monkeypatch):
    """The store stated 10 matching rows and served 9. Publishing the 9 would
    be the same fail-OPEN one layer up, so the pull REFUSES (exit 2) instead."""
    from tools import capture_cost_report as report

    _env(monkeypatch)
    _capped(monkeypatch, _rows(10), cap=3, unserved={"id-0007"})

    with pytest.raises(ValueError, match="complete"):
        report.fetch_rows(7)


def test_a_walk_that_exhausts_its_page_budget_is_refused(monkeypatch):
    """A deployment capped at ONE row per response must not be reported as a
    window either: the walk is bounded, and exceeding the bound is a refusal,
    never a partial result presented as the window."""
    from tools import capture_cost_report as report

    _env(monkeypatch)
    _capped(monkeypatch, _rows(150), cap=1)

    with pytest.raises(ValueError, match="complete"):
        report.fetch_rows(7)


def test_an_unreachable_store_is_still_the_documented_exit_2_valueerror(
        monkeypatch):
    """The refusal above must not change the transport contract: a store that
    cannot be reached is a ``ValueError`` (exit 2), never a traceback."""
    from tools import capture_cost_report as report

    _env(monkeypatch)

    class _Boom(_CappedClient):
        def get(self, url, **kwargs):
            raise RuntimeError("connection refused")

    monkeypatch.setattr("httpx.Client", _Boom)

    with pytest.raises(ValueError, match="live pull failed"):
        report.fetch_rows(7)


# ── the shape that must not come back ──────────────────────────────────────


def test_the_short_page_end_of_walk_signal_is_gone_from_the_source():
    """Target from the issue: 0 occurrences of the short-page end signal.

    A behavioural test is the real gate (above); this is the fence that keeps
    the exact shape from being re-introduced in the hand-rolled walk this tool
    used to carry."""
    src = (Path(__file__).resolve().parent.parent
           / "tools" / "capture_cost_report.py").read_text(encoding="utf-8")
    assert "len(batch) < page" not in src
    assert "start += page" not in src
