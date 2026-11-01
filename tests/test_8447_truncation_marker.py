"""#8447 — a bounded read must MARK its result incomplete, never truncate silently.

The contract (issue #8447):

    A bounded operation must either return complete results, or mark the result
    as INCOMPLETE. It must never return a silently truncated result that is
    indistinguishable from a complete one.

The surface under test is the limit-capped hybrid search read
(``TortoiseSDK.tortoise_fts_query`` -> the hosted ``GET /v1/search`` body), the
one retrieval path that has NO offset and NO cursor: a row dropped at
``result_ids[:limit]`` is unreachable by any follow-up call, so an unmarked cut
there loses data permanently. The paginated path
(``paginated_query`` -> ``{results, total, hasMore}``) is the CONTROL: it is
legitimate pagination and must never be marked (asserted below).

Both directions are asserted, on the RESPONSE VALUE only — never on a log or a
message string:

(a) a truncated result is DETECTABLE by the caller;
(b) a genuinely complete result is NOT marked truncated (no false positives).

The boundary cases the issue names are covered as a set: exactly-at-limit,
limit+1, and page-2-of-2 (via the paginated control).

Lane: embedded (``TORTOISE_TEST_CARVE_OUT=1``) or any ``TORTOISE_DB_URI``
(``tests/conftest.py`` session gate).
"""
from __future__ import annotations

import asyncio

import pytest

from tortoise.truncation import (
    TOTAL_AVAILABLE_FIELD,
    TRUNCATED_FIELD,
    mark_truncation,
    truncation_field,
)

# ── the marker's own contract ────────────────────────────────────────────

class TestMarkerVocabulary:
    def test_field_names_are_their_own_and_not_a_status_term(self):
        """Truncation is NOT a fifth read-status term (#8447 / #7952 §17.2)."""
        from tortoise import status_vocabulary

        assert TRUNCATED_FIELD == "truncated"
        assert TOTAL_AVAILABLE_FIELD == "total_available"
        assert TRUNCATED_FIELD not in status_vocabulary.CLIENT_STATUS_TERMS
        assert TOTAL_AVAILABLE_FIELD not in status_vocabulary.CLIENT_STATUS_TERMS

    def test_cut_is_marked_and_the_total_is_exact_when_proved(self):
        sink: dict = {}
        mark_truncation(sink, returned=5, candidates=20, exact=True)
        assert truncation_field(sink) == {"truncated": True, "total_available": 20}

    def test_cut_without_proof_withholds_the_total(self):
        """A floor must never be published as a total (ES `relation: gte`)."""
        sink: dict = {}
        mark_truncation(sink, returned=5, candidates=120, exact=False)
        assert truncation_field(sink) == {"truncated": True}
        assert TOTAL_AVAILABLE_FIELD not in truncation_field(sink)

    def test_complete_is_positively_marked_complete(self):
        sink: dict = {}
        mark_truncation(sink, returned=3, candidates=3, exact=True)
        assert truncation_field(sink) == {"truncated": False}

    def test_exactly_at_limit_is_not_truncation(self):
        """Boundary: returned == candidates is a COMPLETE result, not a cut."""
        sink: dict = {}
        mark_truncation(sink, returned=10, candidates=10, exact=True)
        assert truncation_field(sink)[TRUNCATED_FIELD] is False

    def test_limit_plus_one_is_truncation(self):
        """Boundary: one row beyond the cap must flip the marker."""
        sink: dict = {}
        mark_truncation(sink, returned=10, candidates=11, exact=True)
        assert truncation_field(sink) == {"truncated": True, "total_available": 11}

    def test_missing_verdict_is_a_refusal_never_a_default_false(self):
        """An absent marker must not be read as 'complete' — that is the bug."""
        with pytest.raises(RuntimeError, match="truncation marker missing"):
            truncation_field({})
        with pytest.raises(RuntimeError, match="truncation marker missing"):
            truncation_field(None)


# ── end-to-end, on a real store ──────────────────────────────────────────

@pytest.fixture()
def sdk(tmp_path, monkeypatch):
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "trunc.db"))
    from tortoise.sdk import TortoiseSDK

    s = TortoiseSDK()
    try:
        yield s
    finally:
        s.close()


def _seed(sdk, n):
    for i in range(n):
        sdk.create_point(kind="statement",
                         content=f"zebra migration claim number {i:02d}",
                         entity_type="point")


def _store_matches(sdk, term="zebra"):
    return sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.content CONTAINS $t RETURN count(n)",
        params={"t": term}).result_set[0][0]


class TestEndToEndTruncation:
    def test_a_cut_result_is_detectable_by_the_caller(self, sdk):
        """(a) DETECTABLE — the store has 20 matches, limit=5, and the response says so."""
        _seed(sdk, 20)
        truth = _store_matches(sdk)
        assert truth == 20

        sink: dict = {}
        rows = sdk.tortoise_fts_query("zebra", limit=5, _truncation_out=sink)

        # the returned rows are a STRICT SUBSET ...
        assert len(rows) == 5 < truth
        # ... and the caller can TELL.
        marker = truncation_field(sink)
        assert marker[TRUNCATED_FIELD] is True
        assert marker[TOTAL_AVAILABLE_FIELD] == truth

    def test_a_complete_result_is_not_marked_truncated(self, sdk):
        """(b) NO FALSE POSITIVE — a read that returned the whole match set."""
        _seed(sdk, 3)
        truth = _store_matches(sdk)
        assert truth == 3

        sink: dict = {}
        rows = sdk.tortoise_fts_query("zebra", limit=5, _truncation_out=sink)

        assert len(rows) == truth
        assert truncation_field(sink)[TRUNCATED_FIELD] is False
        assert TOTAL_AVAILABLE_FIELD not in truncation_field(sink)

    def test_exactly_at_limit_is_complete(self, sdk):
        """Boundary: limit == match count is COMPLETE, not truncated."""
        _seed(sdk, 4)
        sink: dict = {}
        rows = sdk.tortoise_fts_query("zebra", limit=4, _truncation_out=sink)
        assert len(rows) == 4
        assert truncation_field(sink)[TRUNCATED_FIELD] is False

    def test_a_saturated_candidate_pool_withholds_the_total(self, sdk, monkeypatch):
        """A pool that filled its own window cannot prove the total."""
        _seed(sdk, 20)
        sink: dict = {}
        # pool_size=2 -> str_limit is 2, so 20 candidates saturate the window
        # and `total_available` may not be published as if it were exact.
        rows = sdk.tortoise_fts_query("zebra", limit=1, pool_size=2,
                                      _truncation_out=sink)
        assert len(rows) == 1
        marker = truncation_field(sink)
        assert marker[TRUNCATED_FIELD] is True
        assert TOTAL_AVAILABLE_FIELD not in marker

    def test_the_marker_is_off_for_a_caller_that_does_not_ask(self, sdk):
        """Additive: no sink, no behaviour change, no new row keys."""
        _seed(sdk, 5)
        rows = sdk.tortoise_fts_query("zebra", limit=2)
        assert len(rows) == 2
        assert TRUNCATED_FIELD not in rows[0]


class TestPaginationIsNotTruncation:
    """The distinguishing rule: paginated rows are REACHABLE, cut rows are not."""

    def test_the_paginated_path_reports_its_own_completeness_and_is_never_marked(
            self, sdk):
        _seed(sdk, 5)
        first = sdk.paginated_query(limit=2, skip=0)
        # it carries the explicit pagination contract instead of a marker ...
        assert set(first) == {"results", "total", "hasMore"}
        assert first["total"] == 5
        assert first["hasMore"] is True
        assert TRUNCATED_FIELD not in first

        # ... and page 2 of 2 is reachable, so nothing is lost.
        second = sdk.paginated_query(limit=2, skip=2)
        assert second["hasMore"] is True
        third = sdk.paginated_query(limit=2, skip=4)
        assert third["hasMore"] is False
        ids = [r["id"] for p in (first, second, third) for r in p["results"]]
        assert len(ids) == len(set(ids)) == 5


# ── the field actually reaches the shipped response ──────────────────────

class TestHostedResponseCarriesIt:
    """The marker must reach the BODY — a marker on a sink is a developer fact."""

    def _patch(self, monkeypatch, marker):
        import tortoise.hosted_api as hosted_api

        class _FakeSDK:
            def tortoise_fts_query(self, q, limit=10, read_status_out=None,
                                   _truncation_out=None, **_ignored):
                if _truncation_out is not None:
                    _truncation_out.update(marker)
                return [{"id": "p1", "content": "alpha", "pointKind": "statement"}]

            def close(self):
                pass

        monkeypatch.setattr(hosted_api, "_data_sdk", lambda org: _FakeSDK())
        monkeypatch.setattr(hosted_api, "_require_scope",
                            lambda org, scope, name: None)
        return hosted_api

    def test_a_truncated_read_says_so_on_the_body(self, monkeypatch):
        hosted_api = self._patch(
            monkeypatch, {"truncated": True, "total_available": 20})
        body = asyncio.run(hosted_api.search(q="alpha", limit=5, org={}))
        assert body["truncated"] is True
        assert body["total_available"] == 20
        assert body["count"] == 1  # the old field is unchanged, not repurposed

    def test_a_complete_read_says_complete_on_the_body(self, monkeypatch):
        hosted_api = self._patch(monkeypatch, {"truncated": False})
        body = asyncio.run(hosted_api.search(q="alpha", limit=5, org={}))
        assert body["truncated"] is False
        assert "total_available" not in body

    def test_a_silent_producer_is_a_500_not_a_clean_answer(self, monkeypatch):
        """The failure direction: an unmarked read must never read as complete."""
        hosted_api = self._patch(monkeypatch, {})
        with pytest.raises(RuntimeError, match="truncation marker missing"):
            asyncio.run(hosted_api.search(q="alpha", limit=5, org={}))
