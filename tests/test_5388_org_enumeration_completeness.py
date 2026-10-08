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
        assert order == "id", "a stable order is required for page walking"
        eff = min(limit or self.page_size, self.page_size)
        off = offset or 0
        page = self.rows[off:off + eff]
        self.calls.append((off, self.state_total if count_exact else None))
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
        assert [c[0] for c in cp.calls][:2] == [0, MAX], cp.calls

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

    def test_count_exact_is_requested_once_not_per_page(self, monkeypatch):
        """The total is a property of the FILTER, so one exact count suffices —
        asking every page would charge the server a COUNT per page."""
        cp = FakeControlPlane(1500, state_total=1500)
        _run(monkeypatch, cp, require_complete=True)
        asked = [c for c in cp.calls if c[1] is not None]
        assert len(asked) == 1, cp.calls


def ha_pages() -> int:
    from tortoise import hosted_api as ha_mod
    return ha_mod._ORG_ENUMERATION_MAX_PAGES
