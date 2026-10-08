"""#5388 — org enumeration must distinguish COMPLETE from TRUNCATED.

Pre-fix, `_iter_registered_orgs` requested ``limit == max_rows`` and treated a
FULL page as "possibly truncated". That was wrong in both directions:

* a genuinely complete 1000-org fleet was rejected as incomplete, and
* on a deployment whose ``max_rows`` was LOWER than the requested bound, the
  server returned a short page with no signal, so the detection silently
  stopped working.

The fix makes completeness a first-class property read from the SERVER
(``Content-Range`` via ``Prefer: count=exact``) and WALKS the pages.
"""
from __future__ import annotations

from tortoise.supabase_control import _content_range_total

MAX = 1000


# ── the header parser ──────────────────────────────────────────────────────

class TestContentRangeTotal:
    """`Content-Range: <first>-<last>/<total>` — the only completeness signal."""

    def test_parses_a_real_total(self):
        assert _content_range_total("0-24/3574") == 3574

    def test_parses_a_zero_offset_total(self):
        assert _content_range_total("*/3574") == 3574

    def test_star_total_is_none_not_zero(self):
        """`*` means the server did NOT count; collapsing it to 0 would report
        an empty fleet and PRUNE every org."""
        assert _content_range_total("0-24/*") is None

    def test_absent_header_is_none(self):
        assert _content_range_total(None) is None
        assert _content_range_total("") is None

    def test_malformed_is_none_not_a_guess(self):
        for bad in ("0-24", "0-24/abc", "/", "garbage"):
            assert _content_range_total(bad) is None, bad


# ── the walk ───────────────────────────────────────────────────────────────

class FakeControlPlane:
    """Serves `organizations` pages and states a total like PostgREST would."""

    def __init__(self, total_rows: int, *, state_total: int | None,
                 page_size: int = MAX):
        self.rows = [{"id": f"org-{i:05d}", "name": f"O{i}"}
                     for i in range(total_rows)]
        # `state_total` models what the SERVER says (may under/over-state, or be
        # None when it does not answer). It is deliberately independent of the
        # rows actually served.
        self.state_total = state_total
        self.page_size = page_size
        self.calls: list[tuple[int, int | None]] = []

    def query_with_total(self, table, *, select=None, filters=None, method="GET",
                         json_body=None, order=None, limit=None, offset=None,
                         count_exact=False, **kw):
        assert order == "id", (
            "keyset paging is only sound if the cursor column is the sort key"
        )
        rows = self.rows
        last = None
        for col, op, val in (filters or []):
            if col == "id" and op == "gt":
                last = val
        if last is not None:
            rows = [r for r in rows if r["id"] > last]
        eff = min(limit or self.page_size, self.page_size)
        page = rows[:eff]
        self.calls.append((last, self.state_total if count_exact else None))
        return page, self.state_total


def _run(monkeypatch, cp, *, require_complete: bool):
    import tortoise.hosted_api as ha_mod
    import tortoise.supabase_control as sc

    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    return ha_mod._iter_registered_orgs(require_complete=require_complete)


class TestEnumerationCompleteness:

    def test_exactly_the_page_size_is_complete_not_truncated(self, monkeypatch):
        """THE regression #5388 names: a complete 1000-org fleet must be
        ACCEPTED. Pre-fix this returned None (a full page == 'truncated')."""
        cp = FakeControlPlane(MAX, state_total=MAX)
        got = _run(monkeypatch, cp, require_complete=True)
        assert got is not None, "a complete 1000-org fleet must not be refused"
        assert len(got) == MAX
        assert got[0] == {"org_id": "org-00000", "name": "O0"}

    def test_walks_past_the_first_page(self, monkeypatch):
        """1500 orgs = two pages; the walk must collect BOTH."""
        cp = FakeControlPlane(1500, state_total=1500)
        got = _run(monkeypatch, cp, require_complete=True)
        assert got is not None and len(got) == 1500
        assert [c[0] for c in cp.calls][:2] == [None, "org-00999"], cp.calls

    def test_truncated_server_count_fails_closed(self, monkeypatch):
        """The server says 1500 but only serves 1000: the fleet could NOT be
        confirmed, so a completeness-critical caller must get None."""
        cp = FakeControlPlane(MAX, state_total=1500)
        assert _run(monkeypatch, cp, require_complete=True) is None

    def test_truncated_is_still_returned_for_a_best_effort_caller(self, monkeypatch):
        """The retention sweep must process what it got rather than purge
        nothing for the whole fleet."""
        cp = FakeControlPlane(MAX, state_total=1500)
        got = _run(monkeypatch, cp, require_complete=False)
        assert got is not None and len(got) == MAX

    def test_no_total_with_a_short_page_is_complete(self, monkeypatch):
        """Server states no total (no count=exact support): a SHORT page is the
        only remaining signal for 'that was everything'."""
        cp = FakeControlPlane(37, state_total=None)
        got = _run(monkeypatch, cp, require_complete=True)
        assert got is not None and len(got) == 37

    def test_no_total_with_full_pages_to_the_cap_fails_closed(self, monkeypatch):
        """Server states no total AND every page is full: the page cap is NOT
        proof of completeness, so refuse rather than prune on a guess."""
        cp = FakeControlPlane(MAX * 500, state_total=None)
        assert _run(monkeypatch, cp, require_complete=True) is None
        assert len(cp.calls) == ha_pages(), (
            f"should stop at the page cap, not walk 500 pages: {len(cp.calls)}"
        )

    def test_a_short_page_with_a_stated_total_is_not_the_end(self, monkeypatch):
        """THE case the issue names: a per-request cap LOWER than our limit.

        The server serves 500 rows per request but states a 1500 total. A short
        page must NOT be read as end-of-data when the server has said more rows
        exist — stopping there enumerates only the first page of a larger fleet,
        which is precisely the deployment shape #5388 exists to fix.
        """
        cp = FakeControlPlane(1500, state_total=1500, page_size=500)
        got = _run(monkeypatch, cp, require_complete=True)
        assert got is not None, "a per-request cap must not truncate the fleet"
        assert len(got) == 1500, (
            f"must walk past the server's per-request cap (got {len(got)})"
        )
        assert [c[0] for c in cp.calls][:3] == [None, "org-00499", "org-00999"], \
            cp.calls

    def test_count_exact_is_requested_once_not_per_page(self, monkeypatch):
        """The total is a property of the FILTER, so one exact count suffices —
        asking every page would charge the server a COUNT per page."""
        cp = FakeControlPlane(1500, state_total=1500)
        _run(monkeypatch, cp, require_complete=True)
        asked = [c for c in cp.calls if c[1] is not None]
        assert len(asked) == 1, cp.calls


class TestWindowShiftRace:
    """#5388: a shifting page window must NEVER certify an incomplete fleet.

    Offset pagination walks a MOVING window. A concurrent INSERT whose `id`
    sorts before the cursor shifts every later page, so one row is served twice
    and an ORIGINAL is skipped — yet the duplicate still counts toward the
    stated total. The walk would then report complete and the cost refresh
    would PRUNE the skipped org, which is the exact harm #5388 exists to
    prevent. Keyset paging (`id > last`) is immune: the cursor is the last id
    we actually SAW, which no concurrent write can move.
    """

    def test_a_shifted_window_cannot_skip_an_org(self, monkeypatch):
        class ShiftingCP:
            """Serves a stable set, but prepends a NEW row on the 2nd request
            — which is what an offset cursor would trip over."""

            def __init__(self, n):
                self.base = [{"id": f"o{i:06d}", "name": None} for i in range(n)]
                self.calls = 0
                self.seen_cursors = []

            def query_with_total(self, table, *, select=None, filters=None,
                                 method="GET", json_body=None, order=None,
                                 limit=None, offset=None, count_exact=False,
                                 **kw):
                self.calls += 1
                last = None
                for col, op, val in (filters or []):
                    if col == "id" and op == "gt":
                        last = val
                self.seen_cursors.append(last)
                view = list(self.base)
                if self.calls >= 2:
                    view = [{"id": "o000000a", "name": None}, *view]
                if last is not None:
                    view = [r for r in view if r["id"] > last]
                return view[:limit], len(self.base)

        n = MAX + 1000
        cp = ShiftingCP(n)
        got = _run(monkeypatch, cp, require_complete=True)
        assert got is not None, "the walk must still confirm a stable fleet"
        ids = [r["org_id"] for r in got]
        assert len(ids) == len(set(ids)) == n, (
            f"every org exactly once: got {len(ids)} rows, {len(set(ids))} "
            "distinct"
        )
        # The cursor must be the last SAW id, never a count.
        assert cp.seen_cursors[0] is None and cp.seen_cursors[1] is not None

    def test_a_duplicate_cannot_satisfy_the_stated_total(self, monkeypatch):
        """Completeness counts DISTINCT ids: if the server repeats a row, the
        count must not be reachable by duplication."""
        class RepeatingCP:
            """Always serves the same full page, and claims a LARGER total."""

            def __init__(self, total):
                self.page = [{"id": f"o{i:06d}", "name": None}
                             for i in range(MAX)]
                self.total = total

            def query_with_total(self, table, **kw):
                # Deliberately IGNORES the cursor: re-serves the same full page
                # on every call. Round 3 found that honouring the cursor made
                # this test VACUOUS — page 2 came back empty, so the
                # distinct-id count was never exercised and swapping it for a
                # raw row count left every test green. A server that re-serves
                # rows it already sent is exactly the case the distinct count
                # exists for.
                return self.page, self.total

        # 2*MAX claimed, but only MAX distinct rows will ever be served.
        cp = RepeatingCP(MAX * 2)
        assert _run(monkeypatch, cp, require_complete=True) is None, (
            "a total that no amount of walking satisfies must fail closed"
        )


class TestEndOfWalkSignals:
    """Round 3: the walk must stop only on an EMPTY page.

    Two weaker signals were each a fail-OPEN in the destructive caller
    (`_refresh_cost_allocation` prunes every org absent from the enumeration),
    and each is pinned below by the exact shape that defeats it.
    """

    class _Server:
        """Pages by keyset over a mutable fleet, with configurable pathologies.

        `cap`         — per-request row cap (may be BELOW the requested limit)
        ``stated``      — 'fleet' = total on page 1 only, as PostgREST does;
                          'remaining' = a (cursor-filtered) total on every page
        ``insert_after``— rows (id, name) that appear once page 1 is served

        ``order`` IS honoured: the client asks for ``order="id"`` and a fake
        that returns insertion order instead is not a faithful double — keyset
        paging's whole soundness rests on the cursor being the SORT key, and an
        unsorted double silently hid a real break (round 3: page 2 came back
        with the un-served originals first, so an early break looked harmless).
        """

        def __init__(self, ids, *, cap=None, stated="fleet",
                     insert_after=None):
            self.rows = [{"id": i, "name": None} for i in ids]
            self.cap = cap
            self.stated = stated
            self.insert_after = list(insert_after or [])
            self.calls = 0

        def query_with_total(self, table, **kw):
            self.calls += 1
            if self.calls == 2:
                # The race: these arrive AFTER page 1's count was taken and
                # AFTER page 1 was served. Inserting them on the SAME call as
                # the count would make the total include them and hide the bug
                # (a fixture defect of the same class as #5543's tier=None).
                self.rows.extend({"id": i, "name": None}
                                  for i in self.insert_after)
            last = None
            for col, op, val in (kw.get("filters") or []):
                if col == "id" and op == "gt":
                    last = val
            view = [r for r in self.rows if last is None or r["id"] > last]
            order = kw.get("order")
            if order:
                view.sort(key=lambda r: r.get(order) or "")
            limit = kw.get("limit")
            bounds = [b for b in (limit, self.cap) if b is not None]
            size = min(bounds) if bounds else None
            page = view[:size] if size is not None else view
            if self.stated is None:
                return page, None
            if self.stated == "fleet":
                # PostgREST emits `*/` (no total) unless count=exact was asked.
                return page, (len(self.rows) if kw.get("count_exact") else None)
            # "remaining": a re-stated count of the cursor-filtered view.
            return page, len(view)

    def test_a_stale_total_cannot_end_the_walk(self, monkeypatch):
        """A page-1 count is a SNAPSHOT; `seen` grows with later inserts.

        Enough inserts sorting after the cursor push the distinct count past
        the stale total, and if that ends the walk the unserved originals are
        pruned.
        """
        n = MAX + 500
        ids = [f"o{i:06d}" for i in range(n)]
        # Insert ids that sort strictly between o000999 and o001000 (any suffix
        # keeps it there: the discriminating char is index 3), and are DISTINCT
        # — a repeated insert id inflates the row count without advancing the
        # distinct set, which would make the assertion below unfalsifiable.
        inserts = [f"o000999{i:04d}" for i in range(1000)]
        cp = self._Server(ids, cap=MAX, insert_after=inserts)
        got = _run(monkeypatch, cp, require_complete=True)
        assert got is not None, "a fleet that IS reachable must not fail closed"
        served = {r["org_id"] for r in got}
        missing = [i for i in ids if i not in served]
        assert not missing, (
            f"every ORIGINAL org must be served, missing {len(missing)} "
            f"(stale page-1 total ended the walk at {cp.calls} page(s))"
        )

    def test_a_short_page_without_a_total_is_not_the_end(self, monkeypatch):
        """#5388's own headline deployment: per-request cap BELOW our limit.

        No stated total, so a short page is the only local signal — and it is
        the WRONG one. At most one extra request is paid for a small fleet.
        """
        n = 1500
        ids = [f"o{i:06d}" for i in range(n)]
        cp = self._Server(ids, cap=500, stated=None)
        got = _run(monkeypatch, cp, require_complete=True)
        assert got is not None
        assert {r["org_id"] for r in got} == set(ids), (
            f"a capped server with no total must be walked to exhaustion, "
            f"got {len(got)} of {n} in {cp.calls} page(s)"
        )

    def test_a_later_page_total_cannot_replace_the_fleet_total(
            self, monkeypatch):
        """A cursor-filtered total counts the REMAINING rows, not the fleet.

        Accepting it satisfies `seen >= total` almost immediately and stops
        the walk with rows unfetched while reporting COMPLETE.
        """
        n = 1500
        ids = [f"o{i:06d}" for i in range(n)]
        cp = self._Server(ids, cap=500, stated="remaining")
        got = _run(monkeypatch, cp, require_complete=True)
        assert got is not None
        assert {r["org_id"] for r in got} == set(ids), (
            f"a later page's total must never replace the fleet count, got "
            f"{len(got)} of {n} in {cp.calls} page(s)"
        )

    def test_a_later_page_total_cannot_mask_a_shortfall(self, monkeypatch):
        """The fleet count is a CONSISTENCY CHECK, not just a label.

        When the server states a fleet total larger than it will ever serve,
        `complete` must fail closed. Letting a later page's (smaller, cursor-
        filtered) count replace it makes the check compare against a number the
        walk trivially satisfies, so a real shortfall is reported COMPLETE and
        the destructive caller prunes the orgs that were never served. This is
        the case the sticky first total exists for; without this test that
        guard could be deleted with the suite still green (a mutation found
        exactly that).
        """
        n = 900
        ids = [f"o{i:06d}" for i in range(n)]

        class LyingCP(self._Server):
            def query_with_total(self, table, **kw):
                page, _ = super().query_with_total(table, **kw)
                # Fleet claims 1500; only 900 exist and the tail states the
                # shrinking remainder (500 -> 400 -> 0).
                return page, (1500 if self.calls == 1 else len(page))

        cp = LyingCP(ids, cap=500, stated="remaining")
        assert _run(monkeypatch, cp, require_complete=True) is None, (
            "a fleet total the walk can never satisfy must fail closed, not be "
            "overwritten by a later page's smaller remainder"
        )


def ha_pages() -> int:
    from tortoise import hosted_api as ha_mod
    return ha_mod._ORG_ENUMERATION_MAX_PAGES


# ── the REAL seam's wiring ─────────────────────────────────────────────────

class _StubResp:
    status_code = 200
    content = b"[]"

    def __init__(self, headers):
        self.headers = headers

    def json(self):
        return []


class _StubHTTP:
    """Records the outgoing headers so the Prefer wiring is observable."""

    def __init__(self, headers):
        self._headers = headers
        self.seen: list[dict] = []

    def get(self, url, params=None, headers=None, **kw):
        self.seen.append(dict(headers or {}))
        return _StubResp(self._headers)


def _real_cp(content_range: str):
    """A REAL SupabaseControlPlane with only its transport stubbed."""
    from tortoise.supabase_control import SupabaseControlPlane
    cp = object.__new__(SupabaseControlPlane)
    cp._url = "https://stub.supabase.co"
    cp._key = "stub-key"
    cp._http = _StubHTTP({"Content-Range": content_range})
    return cp


class TestRealSeamWiring:
    """The production mechanism itself — not a fake's reimplementation.

    Without these, deleting the `Prefer: count=exact` send or the
    `Content-Range` parse leaves every test green: the whole change would be
    unverified while the fake-driven tests kept passing.
    """

    def test_count_exact_sends_prefer_and_parses_the_total(self):
        cp = _real_cp("0-999/1500")
        _rows, total = cp.query_with_total("organizations", count_exact=True)
        assert total == 1500, "the server's total must reach the caller"
        assert cp._http.seen[0].get("Prefer") == "count=exact", (
            "without Prefer: count=exact PostgREST reports `*/\u002a`, i.e. no "
            "usable total, so the header is the whole point"
        )

    def test_the_total_is_not_requested_when_not_asked_for(self):
        """An exact count costs the server a COUNT, so it is opt-in."""
        cp = _real_cp("0-0/*")
        _rows, total = cp.query_with_total("organizations")
        assert total is None
        assert "Prefer" not in cp._http.seen[0]

    def test_query_still_returns_only_the_row_list(self):
        """The wrapper must keep every existing caller's shape."""
        cp = _real_cp("0-999/1500")
        assert cp.query("organizations") == []

    def test_a_response_without_headers_does_not_raise(self):
        """The fail-open guard: an AttributeError here would be swallowed by
        the callers' best-effort except and turn a COMPLETE enumeration into an
        EMPTY one."""
        from tortoise.supabase_control import SupabaseControlPlane

        class _NoHeaders:
            status_code = 200
            content = b"[]"  # a real attribute, `headers` genuinely ABSENT

            def json(self):
                return []

        class _HTTP:
            def get(self, url, params=None, headers=None, **kw):
                return _NoHeaders()

        cp = object.__new__(SupabaseControlPlane)
        cp._url, cp._key = "https://stub.supabase.co", "k"
        cp._http = _HTTP()
        rows, total = cp.query_with_total("organizations", count_exact=True)
        assert rows == [] and total is None
