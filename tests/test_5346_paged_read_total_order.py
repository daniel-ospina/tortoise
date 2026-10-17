"""#5346 — a PAGED read must order by a TOTAL key, or the pages are not adjacent.

``sdk.paginated_query`` (and therefore the MCP tools ``tortoise_query`` with
``offset=``/``page=`` and ``tortoise_paginated_query``) enumerated Points with
``ORDER BY n.createdAt DESC SKIP $skip LIMIT $limit``. ``createdAt`` is **not
unique** — one capture stamps a whole session of turns with a single timestamp
— and with no unique tiebreaker the tie group's row order is not stable across
the SEPARATE query each page is. Consecutive windows are then not adjacent
ranges of ONE order, so a caller that walks ``page=1..N`` until
``hasMore`` is False duplicates some rows and **silently skips others**, while
``hasMore`` (computed from the ``count(n)`` total) still reports the enumeration
complete. A scan that walks pages and reports "0 found" is reporting on a set it
never fully read — the false-negative class #4911 exists to remove.

``search_sessions``' legacy keyword fallback (reachable with an ``offset`` when
``has_semantic`` is False — an embedded graph without embeddings, or one whose
Event FTS index is absent) paged on the same defect through ``e.startedAt``.

The fix is a unique tiebreaker — ``id(n)`` / ``id(e)``, the internal node id —
which makes the order total. ``n.id`` is deliberately NOT used: Point ids are
not unique (colliding ids are a tested shape), so it leaves a residual tie.

Test doctrine (Class B): every test states (1) the value whose presence makes it
fail and (2) that the fixture reaches that value.

Run with (embedded — the carve-out / tier-2 shape):
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
    tests/test_5346_paged_read_total_order.py -q -p no:cacheprovider
Run with (docker — the full-matrix shape):
  TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
    .venv/bin/python -m pytest tests/test_5346_paged_read_total_order.py -q
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.sdk import TortoiseSDK


#: The falsifying fixture size. Measured on the pre-fix head (FalkorDB graph
#: module 4.20.x AND embedded falkordblite), 3,000 Points sharing one
#: ``createdAt`` with ``limit=500``: 3,000 rows returned over 6 pages but only
#: **1,505** distinct ids — 1,495 never read. ``limit=100`` lost 298 of 600.
#: 500 keeps the loss far from zero on either backend while the seed stays one
#: ``UNWIND`` statement.
N_TIED = 3000
PAGE = 500


@pytest.fixture
def sdk():
    """SDK on a fresh DB, one per test (embedded; redirects under a URI)."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_5346_test_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _q(sdk: TortoiseSDK, query: str, params: dict | None = None) -> list:
    r = sdk._get_proj().g.query(query, params=params or {})
    return r.result_set if r else []


def _kind() -> str:
    """A pointKind unique to one test — isolates a shared server graph."""
    return f"k5346_{uuid.uuid4().hex[:12]}"


def _seed_tied_points(sdk: TortoiseSDK, kind: str, n: int) -> list[str]:
    """``n`` Points sharing ONE ``createdAt`` — the tie group paging breaks on.

    Written in one ``UNWIND`` because the point of the fixture is the SHARED
    timestamp: ``create_point`` stamps its own ``createdAt``, which would make
    every row unique and the fixture unable to reach the value under test.
    """
    _q(
        sdk,
        "UNWIND range(1,$n) AS i "
        "CREATE (n:Point {id:'p'+toString(i), pointKind:$k, content:'c', "
        "is_operator:false, status:'live', createdAt:$ts})",
        params={"n": n, "k": kind, "ts": "2026-01-01T00:00:00Z"},
    )
    return [f"p{i}" for i in range(1, n + 1)]


def _sweep(sdk: TortoiseSDK, kind: str, page: int) -> tuple[list[str], int]:
    """Walk ``skip = 0, page, 2*page, …`` until ``hasMore`` is False."""
    seen: list[str] = []
    skip = 0
    total = None
    while True:
        r = sdk.paginated_query(kind=kind, skip=skip, limit=page)
        total = r["total"]
        seen.extend(row["id"] for row in r["results"])
        if not r["hasMore"]:
            return seen, total
        skip += page


# ── The falsifier ────────────────────────────────────────────────────

def test_paged_sweep_reads_every_row_exactly_once(sdk):
    """FALSIFIER: a full page walk must return ``total`` DISTINCT ids.

    Value that makes it fail: the shared ``createdAt`` of 3,000 Points, with
    ``limit`` (500) smaller than the tie group and the group straddling every
    page boundary. The fixture reaches it — one ``UNWIND`` writes all 3,000
    with the identical timestamp. On the pre-fix head the walk returned 3,000
    rows whose ids were only 1,505 distinct (1,495 never read), and
    ``hasMore`` still went False — so this assertion cannot pass by accident,
    and no ``total``/``hasMore`` value in the response reveals the loss.
    """
    kind = _kind()
    expected = set(_seed_tied_points(sdk, kind, N_TIED))

    rows, total = _sweep(sdk, kind, PAGE)

    assert total == N_TIED
    assert len(rows) == N_TIED, "a page walk must return exactly one row per Point"
    assert len(set(rows)) == N_TIED, (
        f"page walk returned {len(rows)} rows but only {len(set(rows))} distinct ids"
    )
    assert set(rows) == expected, "every seeded Point must be read, and no phantom row"


def test_page_windows_partition_one_order(sdk):
    """The windows of two DIFFERENT page sizes must concatenate to ONE order.

    Value that makes it fail: the same shared ``createdAt``, read twice at
    ``limit=500`` and ``limit=1000``. The fixture reaches it — the seed is
    unchanged between the two sweeps. ``(createdAt, id(n))`` is a total order,
    so both sweeps are the same sequence of the same rows and the two
    concatenations are EQUAL. That equality is what "the windows are adjacent"
    means, and it is strictly stronger than counting distinct ids: a walk that
    dropped and repeated the SAME rows symmetrically would still be caught.
    """
    kind = _kind()
    _seed_tied_points(sdk, kind, N_TIED)

    by_500, _ = _sweep(sdk, kind, PAGE)
    by_1000, _ = _sweep(sdk, kind, PAGE * 2)

    assert by_500 == by_1000


# ── Selectivity: the small-group case must keep its contract ─────────

def test_small_tie_group_pages_without_overlap_or_loss(sdk):
    """A 3-Point tie group paged at 2 must split 2 + 1 with no overlap.

    Value that makes it fail: three Points sharing one ``createdAt`` read at a
    page size of 2, i.e. one boundary INSIDE the tie group. The fixture reaches
    it — the seed writes all three with the identical timestamp. This pins the
    paging CONTRACT at a boundary the fix must not trade away: a "fix" that
    deduplicated by dropping a row, or that made the last page short, fails
    here even though the large-tie-group sweep might still look whole.
    """
    kind = _kind()
    _seed_tied_points(sdk, kind, 3)

    page1 = sdk.paginated_query(kind=kind, skip=0, limit=2)
    page2 = sdk.paginated_query(kind=kind, skip=2, limit=2)

    assert page1["total"] == 3 and page2["total"] == 3
    first = {row["id"] for row in page1["results"]}
    second = {row["id"] for row in page2["results"]}
    assert len(first) == 2 and len(second) == 1
    assert first.isdisjoint(second)
    assert first | second == {"p1", "p2", "p3"}
    assert page1["hasMore"] is True and page2["hasMore"] is False


# ── The same defect, second paged read ───────────────────────────────

def test_search_sessions_legacy_fallback_pages_without_loss(sdk, monkeypatch):
    """``search_sessions``' legacy fallback must visit every session once.

    Value that makes it fail: ``e.startedAt`` shared by 3,000 ``AgentSession``
    Events, swept with ``offset=0, 500, 1000, …``. The fixture reaches it —
    one ``UNWIND`` writes all 3,000 with the identical ``startedAt``, and
    ``tortoise_fts_query`` is patched to contribute nothing, which is the
    documented condition for the legacy fallback (``has_semantic`` False: an
    embedded graph without embeddings, or one whose Event FTS index is absent).
    Without that patch the hybrid branch answers and the paged query under test
    is never reached. On the pre-fix head the sweep returned 3,000 rows whose
    ``eventId``s were only 2,105 distinct — 895 never read.
    """
    n = N_TIED
    _q(
        sdk,
        "UNWIND range(1,$n) AS i "
        "CREATE (e:Event {eventId:'e'+toString(i), eventKind:'AgentSession', "
        "name:$q + ' ' + toString(i), startedAt:$ts})",
        params={"n": n, "q": "zzprobe5346", "ts": "2026-01-01T00:00:00Z"},
    )
    monkeypatch.setattr(sdk, "tortoise_fts_query", lambda *a, **k: [])

    seen: list[str] = []
    offset = 0
    while True:
        rows = sdk.search_sessions("zzprobe5346", limit=PAGE, offset=offset)
        seen.extend(row.get("eventId") for row in rows)
        if len(rows) < PAGE:
            break
        offset += PAGE

    assert len(seen) == n
    assert len(set(seen)) == n, (
        f"offset sweep returned {len(seen)} rows but only {len(set(seen))} distinct ids"
    )
