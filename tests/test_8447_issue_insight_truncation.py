"""#8447 — `issue_insight` must MARK a cut answer, never truncate silently.

The contract (issue #8447):

    A bounded operation must either return complete results, or mark the result
    as INCOMPLETE. It must never return a silently truncated result that is
    indistinguishable from a complete one.

The surface under test is ``TortoiseSDK.issue_insight`` -> the MCP tool
``tortoise_issue_insight`` -> ``GET /v1/issue-insight``. It is the read whose
ENTIRE JOB is to say *"there is more in the graph"*, and it cut its answer at
``_issue_insight_rank(...)[:limit]`` — shipped default ``limit=2`` — with no
signal anywhere on the response. Worse than incomplete: its ``insight``
sentence rendered the **post-cut** length as the count, so a 12-hit read at
``limit=2`` said, in prose, *"2 graph hit(s) relate to this issue"*. That is a
WRONG statement wearing a success code, not merely a short one.

Asserted on the RESPONSE VALUE only — never on a log or a message string:

(a) a cut result is DETECTABLE by the caller, and the count it reports is the
    PRE-cut population;
(b) a genuinely complete result is NOT marked truncated (no false positives);
(c) a ``truncated: true`` over a SATURATED candidate pool withholds
    ``total_available`` — a floor never wears a total's clothes;
(d) an ``insight`` that claims ABSENCE (``"No graph matches"``) cannot be read
    as proof when the read itself was incomplete.

Lane: embedded (``TORTOISE_DB_PATH``), no LLM, no corpus.
"""
from __future__ import annotations

import pytest

from tortoise.truncation import (
    TOTAL_AVAILABLE_FIELD,
    TRUNCATED_FIELD,
)

QUERY = "zebra migration claim"


@pytest.fixture()
def sdk(tmp_path, monkeypatch, force_sparse_tfidf):
    """Embedded store for the marker's end-to-end assertions.

    `force_sparse_tfidf` (tests/conftest.py) pins the embedder state: these tests
    pin EXACT pool sizes, and a box with the `embeddings` extra present would let
    the dense leg widen the union past the pool bound and redden them for an
    unrelated environment difference (the #2573/#2772 rule).
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    # `total_available` is emitted only when the candidate pool proves it saw
    # EVERY match, and the pool bound is `max(limit*5, 20)`; the retrieval
    # window underneath is `max(limit*2, TORTOISE_POOL_FLOOR)`. A box exporting
    # a small floor would silently redden the "detectable" test for an unrelated
    # shell variable.
    monkeypatch.delenv("TORTOISE_POOL_FLOOR", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "insight.db"))
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


class TestIssueInsightTruncation:
    def test_a_cut_result_is_detectable_by_the_caller(self, sdk):
        """(a) DETECTABLE — 12 matches, `limit=2`, and the response says so."""
        _seed(sdk, 12)
        assert _store_matches(sdk) == 12

        resp = sdk.issue_insight(QUERY, limit=2)

        # the delivered rows are a STRICT SUBSET ...
        assert len(resp["data_points"]) == 2 < 12
        # ... and the caller can TELL, with the exact population behind the cap.
        assert resp[TRUNCATED_FIELD] is True
        assert resp[TOTAL_AVAILABLE_FIELD] == 12

    def test_the_reported_count_is_the_pre_cut_population(self, sdk):
        """The prose count was post-cut: a 12-hit read said "2 graph hit(s)"."""
        _seed(sdk, 12)

        resp = sdk.issue_insight(QUERY, limit=2)

        assert len(resp["data_points"]) == 2
        assert resp["insight"].startswith("12 graph hit(s) relate"), resp["insight"]

    def test_a_complete_result_is_not_marked(self, sdk):
        """(b) no false positive — 3 matches, `limit=5`, nothing was dropped."""
        _seed(sdk, 3)
        assert _store_matches(sdk) == 3

        resp = sdk.issue_insight(QUERY, limit=5)

        assert len(resp["data_points"]) == 3
        assert resp[TRUNCATED_FIELD] is False
        assert TOTAL_AVAILABLE_FIELD not in resp

    def test_exactly_at_limit_is_complete(self, sdk):
        """The issue's `exactly-at-limit` boundary: nothing was cut, so nothing is marked."""
        _seed(sdk, 2)

        resp = sdk.issue_insight(QUERY, limit=2)

        assert len(resp["data_points"]) == 2
        assert resp[TRUNCATED_FIELD] is False
        assert TOTAL_AVAILABLE_FIELD not in resp

    def test_limit_plus_one_flips_the_marker(self, sdk):
        """The issue's `limit+1` boundary — the ONE row past the cap is the whole difference."""
        _seed(sdk, 3)

        at_limit = sdk.issue_insight(QUERY, limit=2)
        assert at_limit[TRUNCATED_FIELD] is True
        assert at_limit[TOTAL_AVAILABLE_FIELD] == 3

    def test_a_saturated_pool_withholds_the_total(self, sdk):
        """(c) 25 matches, pool bound 20 — a floor must never wear a total's clothes.

        The pool itself was cut, so matches past it were never read and the exact
        population is UNKNOWN. Absence of `total_available` is the honest answer;
        publishing `20` — or `25`, the store's true count — would be the same
        defect one level down, and the prose is qualified to match.
        """
        _seed(sdk, 25)
        assert _store_matches(sdk) == 25

        resp = sdk.issue_insight(QUERY, limit=2)

        assert resp[TRUNCATED_FIELD] is True
        assert TOTAL_AVAILABLE_FIELD not in resp
        assert resp["insight"].startswith("at least "), resp["insight"]

    def test_a_pool_at_exactly_its_bound_is_not_marked_cut(self, sdk):
        """⛔ A pool that came back AT its bound was not cut by it.

        20 matches, `limit=2` → pool bound 20, and the retrieval path keeps every
        row and reports COMPLETE. A local `len(pool) >= pool_limit` test would
        call that cut — a false positive that withholds an exact total (20) and
        trains callers to ignore the marker. The marker's own recorded proof is
        the only sound source, and it says: exact.
        """
        _seed(sdk, 20)
        assert _store_matches(sdk) == 20

        resp = sdk.issue_insight(QUERY, limit=2)

        assert len(resp["data_points"]) == 2
        assert resp[TRUNCATED_FIELD] is True          # 20 > 2: the cut fired
        assert resp[TOTAL_AVAILABLE_FIELD] == 20      # ... and the total is EXACT
        assert resp["insight"].startswith("20 graph hit(s)"), resp["insight"]

    def test_absence_is_not_claimed_over_an_unread_store(self, sdk):
        """(d) `total_points == 0` is the only path that proves absence."""
        resp = sdk.issue_insight(QUERY, limit=2)

        assert resp["no_prior_knowledge"] is True
        # every response path carries the marker — a missing key is a producer
        # bug, not "complete" (`truncation.truncation_field` refuses to guess).
        assert resp[TRUNCATED_FIELD] is False

    def test_every_response_path_carries_the_marker(self, sdk):
        """`repo_not_indexed` and `no matches` are responses too, and they make
        POSITIVE claims — the marker must ride them, or the caller cannot tell
        "nothing matched" from "I could not read it".
        """
        _seed(sdk, 3)

        resp = sdk.issue_insight(QUERY, repo="an-org/a-repo-that-was-never-indexed",
                                 limit=5)
        assert resp["repo_not_indexed"] is True
        assert resp[TRUNCATED_FIELD] is False

    def test_no_matches_is_marked_when_the_pool_was_cut(self, sdk):
        """(d) `"No graph matches"` is a POSITIVE claim of absence.

        The gate drops every hit here (one shared token, no EP evidence), so
        `data_points` is empty on BOTH sides of the boundary — the ONLY thing
        that distinguishes "nothing relates" from "I cut the pool before I could
        tell" is the marker. Without it this body reads as proof of absence over
        an incomplete read.
        """
        for i in range(25):
            sdk.create_point(kind="statement",
                             content=f"zebra unrelated inventory item {i:02d}",
                             entity_type="point")

        cut = sdk.issue_insight(QUERY, limit=2)
        assert cut["insight"] == "No graph matches for this issue title."
        assert cut["data_points"] == []
        # 25 > the pool bound (20): matches past it were never read.
        assert cut[TRUNCATED_FIELD] is True
        assert TOTAL_AVAILABLE_FIELD not in cut

        # the SAME shape below the pool bound is genuinely complete
        sdk2 = sdk
        for i in range(3):
            sdk2.create_point(kind="statement",
                              content=f"yak unrelated inventory item {i:02d}",
                              entity_type="point")
        whole = sdk2.issue_insight("yak migration claim", limit=2)
        assert whole["insight"] == "No graph matches for this issue title."
        assert whole[TRUNCATED_FIELD] is False
        assert TOTAL_AVAILABLE_FIELD not in whole
