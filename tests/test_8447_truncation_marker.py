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

Lane: **EMBEDDED, always** — this module's own ``sdk`` fixture deletes ``TORTOISE_DB_URI`` and
sets ``TORTOISE_DB_PATH`` (``tests/conftest.py``'s session gate still has to be satisfied, hence
``TORTOISE_TEST_CARVE_OUT=1``). Measured on this box with ``EmbeddingModel.get()`` returning
``None`` (no embeddings extra): the whole module passes, so a KEYWORD-ONLY lane is enough since
#10560. The backend is not a variable here — the fixture pins it — so the ONE gate that decides
which scorer owns the lexical slot is ``TORTOISE_BM25_LEXICAL`` (DEFAULT ON, #10240), and the floor
test PINS it: the file's verdict does not depend on the shell's value of that knob. The pin is what
makes that true, so a future test added here must pin it too rather than inherit it.

⛔ **The DB backend is pinned; the DENSE LEG is NOT — it is a lane variable, and that gap has
already cost a CI red.** CI's ``test (b)`` shard installs the embeddings extra, so there
``EmbeddingModel.get()`` returns a model: the k-NN dense leg answers EVERY query (no threshold), a
read therefore has a non-empty pool, and the degraded TF-IDF tiers (guarded by ``if not
raw_results:``) are never reached. A keyword-only box (this one) reaches them every time. Measured
at ``45faa1661``: ``TestAnArmThatServedTheSlotIsNotALostLeg::test_the_legacy_corpus_fallback_is_still_lost``
red on CI with ``this test must exercise the LEGACY tier; got []`` while passing here — the same
test, green on one lane and unable to reach its subject on the other. Its fix is the #1625
precedent in ``tests/test_fallback_snapshot.py`` (an autouse ``_no_embedder`` fixture pinning the
DENSE leg off), and it is scoped to the class that needs the fallback tiers: the other classes here
are lane-tolerant (measured: this was the ONLY red this module produced on the dense-capable
lane), and pinning a dense leg off where a test says nothing about it would quietly weaken that
test. A future test that asserts anything about WHICH degraded tier answered owes the same pin.

The module's two
pre-existing reds at ``main`` ``5b1888a6e`` were GREEN at ``83a22b2a7`` on a private docker lane with
``--extra embeddings`` (the intervening ``search_engine`` change is ``6b8793351``, #10506), but that
lane is not a PRECONDITION — it was where the OLD fixture happened to produce a band, and the old
fixture was the defect.
``TestAScoreFloorIsNotProofOfCompleteness::test_a_read_with_a_score_floor_never_publishes_a_total``
derives a strict score band from the seeded store. Its seed used to be five points whose content
differed only by an index, and five identical rows on a one-token query TIE under both shipped
scorers with no dense leg at all (measured ``0.0870113 ×5`` with the arm on, ``1.0 ×5`` with the
graph scorer), so the test red on a property of the RANKING rather than on the behaviour it pins;
since #10560 it is a TWO-TIER seed (three rows carrying both query terms, two carrying one), so the
band is a property of the MATCH SHAPE — a document matching both query terms outranks one matching
a single term in the lexical scorers we ship. The DENSE leg is fused in the same RRF and its rank
is not monotone in term coverage, so a dense ordering that inverts the band reds here LOUDLY (the
five scores are printed) rather than silently weakening the pin.
``TestAnArmThatServedTheSlotIsNotALostLeg``
needs the #8488 BM25 arm to actually be gated by ``TORTOISE_BM25_LEXICAL=0`` for its control test. On
a COLD first touch
of the embeddings model the hermetic egress guard (#4387) blocks and retries the HuggingFace HEAD
requests, which can push an early test past its timeout; warm the model once before collecting
evidence (only a lane that HAS a dense leg can be affected — see above).
"""
from __future__ import annotations

import asyncio
import re

import pytest

from tortoise.truncation import (
    TOTAL_AVAILABLE_FIELD,
    TRUNCATED_FIELD,
    mark_truncation,
    truncation_field,
)

# ── the marker's own contract ────────────────────────────────────────────

#: A line coordinate in a census cell, in EITHER spelling the doc has used:
#: contiguous (``hosted_api.py:10209``) and split across two code spans
#: (`` `config/surface-manifest.yml` (`:121-143`) ``). The filename must be
#: followed by the coordinate, allowing only the punctuation that separates
#: them — a closing backtick, whitespace, and the opening paren/bracket.
#:
#: MODULE level, not a class attribute, because it is not this class's private
#: business: `tests/test_8447_c3_ingest_door.py` asks the SAME question of its
#: own docstrings and imports this predicate rather than re-spelling it. Two
#: predicates for one question drift, and the drift is invisible in the worst
#: direction — a citation one matches and the other misses is RED here and GREEN
#: there.
_LINE_CITATION = re.compile(
    r"[\w./-]+\.(?:py|ya?ml)"
    r"[\s`'\"]*"          # a closing code span and/or whitespace
    r"[(\[]?"             # the split form's opening paren/bracket
    r"[\s`'\"]*"
    r":\d[\w-]*")        # the coordinate itself


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
    # #8447 (review round 3, P3): `exact` needs the candidate set to be SHORTER
    # than the retrieval window, and the window is `max(limit*2,
    # TORTOISE_POOL_FLOOR)`. A box exporting a small floor would silently turn
    # the "detectable" test RED for an unrelated shell variable.
    monkeypatch.delenv("TORTOISE_POOL_FLOOR", raising=False)
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
            def _get_proj(self):
                # #10602 review P1: `/v1/search` builds its SDK with the
                # projection attached on the construction lane, so a fake that
                # substitutes `_data_sdk` must model the attach.
                return object()

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


# ── round-3 findings: the fixes must be PINNED, not merely present ────────

class TestTheIncompleteThirdState:
    """A saturated-then-filtered read is INCOMPLETE, not complete (#8447 r3)."""

    def test_saturation_plus_a_filter_is_never_complete(self, sdk):
        """500 eligible, window saturated, filter leaves 5, limit=10."""
        _seed(sdk, 12)
        sink: dict = {}
        # str_limit = max(limit*2, floor) = 4 here, so the window is saturated;
        # excluding the statuses of most Points then shrinks the visible set.
        sdk.tortoise_fts_query("zebra", limit=10, pool_size=4,
                               exclude_status=["statement"], _truncation_out=sink)
        marker = truncation_field(sink)
        assert marker[TRUNCATED_FIELD] is True, (
            "a saturated window a filter shrank must not launder into COMPLETE")
        assert TOTAL_AVAILABLE_FIELD not in marker

    def test_incomplete_is_its_own_input_to_the_marker(self):
        sink: dict = {}
        mark_truncation(sink, returned=0, candidates=0, incomplete=True)
        assert truncation_field(sink) == {"truncated": True}


class TestMarkerHygiene:
    """A reused caller-owned sink must not carry a stale total."""

    def test_a_complete_verdict_clears_the_previous_total(self):
        sink: dict = {}
        mark_truncation(sink, returned=5, candidates=20, exact=True)
        assert truncation_field(sink) == {"truncated": True, "total_available": 20}
        mark_truncation(sink, returned=3, candidates=3, exact=True)
        assert truncation_field(sink) == {"truncated": False}

    def test_an_unprovable_verdict_clears_the_previous_total(self):
        sink: dict = {}
        mark_truncation(sink, returned=5, candidates=20, exact=True)
        mark_truncation(sink, returned=5, candidates=20, exact=False)
        assert truncation_field(sink) == {"truncated": True}


class TestUnreadStoreIsNeverComplete:
    """0 rows over a store that never answered is the THIRD state, not the first."""

    def test_store_that_will_not_open_is_marked_incomplete(self, sdk, monkeypatch):
        def _boom():
            raise RuntimeError("DB health check failed on open")

        monkeypatch.setattr(sdk, "_get_proj", _boom)
        sink: dict = {}
        # Both sinks, exactly as the hosted route wires them: the store-failure
        # arm abstains (rather than raising) only when a status sink asked for
        # the abstention, so a marker-only caller still sees the exception.
        rows = sdk.tortoise_fts_query("zebra", limit=5, _truncation_out=sink,
                                      read_status_out={})
        assert rows == []
        assert truncation_field(sink)[TRUNCATED_FIELD] is True


class TestThePaginationControlIsLive:
    """Pair the control: paged reads are unmarked, cut reads ARE marked.

    The reachable-vs-unreachable rule fails in TWO directions, so it is asserted
    in both: if the marker starts firing on paginated reads, or stops firing on
    a cut search, one of these goes RED.
    """

    def test_paged_walk_is_unmarked_and_the_same_shape_cut_IS_marked(self, sdk):
        _seed(sdk, 5)
        paged = sdk.paginated_query(limit=2, skip=0)
        assert TRUNCATED_FIELD not in paged        # reachable -> never marked

        sink: dict = {}
        sdk.tortoise_fts_query("zebra", limit=2, _truncation_out=sink)
        assert truncation_field(sink)[TRUNCATED_FIELD] is True   # cut -> marked


# ── the marker's scope: "the CAP only" is the wrong reading ──────────────

class TestADownstreamDropIsNotComplete:
    """``truncated`` answers "did I get everything this read matched?" (#8447).

    Round 3 left this as a P3 on the field's DOCUMENTED meaning ("true when
    ``limit`` cut the result"). The behaviour is the conservative one and must
    stay: a row lost DOWNSTREAM of the cap — a ``min_confidence`` floor, a hit
    whose entity read came back empty — is exactly as unreachable as one lost at
    the cap, so narrowing the flag to the cap would answer a 3-match read with
    ``truncated: False`` over 0 delivered rows: a positive claim of completeness
    on an incomplete answer, the defect this module exists to prevent. The fix
    is therefore in the CONTRACT (module docstring, surface manifest, PR body),
    and this test pins the behaviour that contract now describes — so the
    tempting "make the flag cap-only" repair goes RED here instead of shipping.
    """

    def test_a_row_dropped_after_the_cap_is_never_reported_complete(self, sdk):
        _seed(sdk, 5)
        # limit=10 over 5 matches: the cap cuts NOTHING. `min_confidence=1.0`
        # then drops every row at delivery time — the reviewer's construction
        # ("a 3-match store, limit=10, one hit's entity fetch failing").
        sink: dict = {}
        rows = sdk.tortoise_fts_query("zebra", limit=10, min_confidence=1.0,
                                      _truncation_out=sink)
        assert rows == []
        marker = truncation_field(sink)
        assert marker[TRUNCATED_FIELD] is True, (
            "0 rows over a 5-match store is INCOMPLETE, whichever stage dropped "
            "them — a cap-only reading would call this COMPLETE")
        # ... and the count it could see is still published, because nothing
        # narrowed the candidate window: the population was the whole match set.
        assert marker[TOTAL_AVAILABLE_FIELD] == 5

    def test_the_cap_alone_is_not_the_only_cause_that_marks(self, sdk):
        """Control: the same store, nothing dropped, is positively COMPLETE."""
        _seed(sdk, 5)
        sink: dict = {}
        rows = sdk.tortoise_fts_query("zebra", limit=10, _truncation_out=sink)
        assert len(rows) == 5
        assert truncation_field(sink) == {TRUNCATED_FIELD: False}


class TestAScoreFloorIsNotProofOfCompleteness:
    """``threshold`` narrows the union post-window, so ``exact`` must know (#8447).

    Review P2 named ``threshold > 0`` among the shrinkers the completeness proof
    omitted. A score floor discards fused rows AFTER the legs filled their
    ``str_limit`` windows, so a surviving set SHORTER than the window proves
    nothing about the match population — and the count it leaves is a FLOOR.
    """

    def test_a_read_with_a_score_floor_never_publishes_a_total(self, sdk,
                                                              monkeypatch):
        # #10560: pin WHICH scorer owns the lexical slot, because the fixture's
        # score SCALE is not the same in both regimes — measured on this seed,
        # the default-ON BM25 arm gives plain BM25 arithmetic
        # (0.626008 ×3 / 0.087011 ×2 — the second value is exactly the term's
        # IDF over five documents, the first adds the second term's IDF) while
        # the shipped graph scorer gives (3.0 ×3 / 1.0 ×2). Under the arm-off
        # scale the probe-derived floor lands at 2.0, outside ``threshold``'s
        # documented 0.0-1.0 range, so the read raises ``ValueError`` instead of
        # exercising the cap — a red that reads as a product regression to a lane
        # that gated the arm off. Pinning ON is the SHIPPED default (#10240), so
        # this makes the fixture explicit rather than environment-dependent; the
        # assertion below then states the invariant it depends on instead of
        # assuming it. ⚠️ BM25 scores are unbounded ABOVE, so ``floor <= 1.0`` is
        # a property of THIS fixture (a small corpus), not of the arm.
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "1")
        # #10560: the fixture must produce a STRICT score band, and the band must
        # not be an accident of one scorer's arithmetic. It used to be seeded as
        # five identical rows on a one-token query, which ties under EVERY
        # scorer we ship (measured: five rows at rrf 0.0870113769896297 under the
        # default-ON BM25 arm, and all-equal under the graph scorer too) — so
        # `rrf[2] > rrf[3]` was an assertion about the fusion's score spread, not
        # about the cap, and it went red the moment the spread collapsed.
        # Two tiers instead: three rows carrying BOTH query terms, two carrying
        # one. A document that matches both terms outranks a document that
        # matches one in the LEXICAL scorers we ship (rarity/term-coverage in
        # BM25, the per-term count in the graph scorer), so the band is a
        # property of the fixture's MATCH SHAPE rather than of one scorer's
        # numbers. Honest bound: the dense leg is fused with the lexical one and
        # its rank is NOT monotone in term coverage, so on a live dense leg an
        # inversion of the band is possible — it reds loudly below (the five
        # scores are printed) rather than silently weakening the pin.
        for i in range(3):
            sdk.create_point(kind="statement",
                             content=f"zebra migration claim number {i:02d}",
                             entity_type="point")
        for i in range(2):
            sdk.create_point(kind="statement",
                             content=f"zebra sighting report number {i:02d}",
                             entity_type="point")
        # Derive the floor from the scores this store ACTUALLY produces, so the
        # test drops a provable strict subset instead of hard-coding a magic
        # number that a fusion tweak would silently turn vacuous.
        probe = sdk.tortoise_fts_query("zebra migration", limit=10)
        rrf = sorted((r["scores"]["rrf"] for r in probe), reverse=True)
        assert len(rrf) == 5
        assert rrf[2] > rrf[3], (
            "fixture invariant: the two-tier corpus must produce a strict band "
            f"(both-term rows above single-term rows); got {rrf}")
        floor = (rrf[2] + rrf[3]) / 2      # keeps the top 3 of 5, drops 2
        assert 0.0 < floor <= 1.0, (
            "fixture invariant: the derived floor must be a legal ``threshold`` "
            f"(0.0-1.0); got {floor} from {rrf} — this fixture no longer models "
            "a score floor, which means the lexical scorer's score scale moved "
            "(the arm is pinned ON above, and this module's fixture is always "
            "embedded, so the scale cannot come from the other backend)")

        sink: dict = {}
        rows = sdk.tortoise_fts_query("zebra migration", limit=2, threshold=floor,
                                      _truncation_out=sink)
        assert len(rows) == 2              # the cap cuts one of the 3 survivors
        marker = truncation_field(sink)
        assert marker[TRUNCATED_FIELD] is True
        assert TOTAL_AVAILABLE_FIELD not in marker, (
            "the score floor discarded 2 of the 5 matches before the window was "
            "measured, so the 3 survivors are a FLOOR — publishing them as an "
            "exact total is the defect one level down")

    def test_the_same_read_without_a_floor_still_proves_its_total(self, sdk):
        """Control: the proof is withheld because of the FLOOR, not by default."""
        _seed(sdk, 5)
        sink: dict = {}
        sdk.tortoise_fts_query("zebra", limit=2, _truncation_out=sink)
        marker = truncation_field(sink)
        assert marker[TRUNCATED_FIELD] is True
        assert marker[TOTAL_AVAILABLE_FIELD] == 5


# ── review round 5: the three gaps the proof still had ───────────────────

def _point_ids(sdk, n: int) -> list[str]:
    """Real ids from the store, so a driven hit row still fetches its content."""
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.content CONTAINS 'zebra' RETURN n.id").result_set
    ids = [row[0] for row in rows]
    assert len(ids) >= n, f"store holds {len(ids)} points, need {n}"
    return ids[:n]


def _chain(hits, entry):
    """A ``degradation_chain`` double that answers with ``hits`` and RECORDS.

    The real chain records its per-leg verdicts into the ``leg_trace`` the SDK
    passes it (``reason="timeout"`` for a leg still running at the collective
    deadline) — that trace is the only evidence distinguishing "the leg never
    answered" from "the leg answered that it found nothing", so the double has
    to produce it too.

    ``TORTOISE_BM25_LEXICAL`` is pinned OFF at the call sites: the shipped BM25
    arm re-scores the ``fts`` slot AFTER the chain returns, so with it on the
    double's rows would be replaced by a real lexical read and the test would
    assert on rows it never controlled.
    """
    def _fake(graph, query, kind, query_vec, strategies, **kwargs):
        trace = kwargs.get("leg_trace")
        if trace is not None and entry is not None:
            trace.append(dict(entry))
        return {"fts": list(hits)} if hits else {}
    return _fake


class TestALegThatNeverAnsweredIsNotComplete:
    """A leg that was asked and never answered drops rows exactly as a cap does.

    `degradation_chain` only returns the legs that produced rows, so its result
    dict cannot say whether a missing leg matched nothing or never ran. The leg
    trace can — and the completeness proof must read it, or a timed-out vector
    leg leaves a read claiming an EXACT total of a lower bound.
    """

    def test_a_leg_that_timed_out_withholds_the_total(self, sdk, monkeypatch):
        _seed(sdk, 20)
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
        hits = [(pid, 1.0 - i / 100) for i, pid in enumerate(_point_ids(sdk, 10))]
        monkeypatch.setattr(
            "tortoise.search_engine.degradation_chain",
            _chain(hits, {"leg": "structural", "ran": True, "degraded": True,
                          "reason": "timeout", "count": 0}))

        sink: dict = {}
        rows = sdk.tortoise_fts_query("zebra", limit=5, _truncation_out=sink)

        assert len(rows) == 5
        marker = truncation_field(sink)
        assert marker[TRUNCATED_FIELD] is True
        assert TOTAL_AVAILABLE_FIELD not in marker, (
            "the structural leg never answered, so the 10 rows the sparse leg "
            "returned are a LOWER BOUND on a 20-match store — publishing them "
            "as an exact total is the defect this marker exists to prevent")

    def test_a_leg_that_ran_and_found_nothing_is_an_ANSWER(self, sdk, monkeypatch):
        """Control: the fold keys on the leg's VERDICT, never on key-absence.

        A leg that ran and matched nothing leaves no key in ``raw_results``
        either, and it is a complete answer — withholding the total there would
        trade one blind spot for a permanent false positive.
        """
        _seed(sdk, 20)
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
        hits = [(pid, 1.0 - i / 100) for i, pid in enumerate(_point_ids(sdk, 10))]
        monkeypatch.setattr(
            "tortoise.search_engine.degradation_chain",
            _chain(hits, {"leg": "structural", "ran": True, "degraded": False,
                          "reason": "empty_results", "count": 0}))

        sink: dict = {}
        sdk.tortoise_fts_query("zebra", limit=5, _truncation_out=sink)

        marker = truncation_field(sink)
        assert marker[TRUNCATED_FIELD] is True          # 10 candidates, 5 shipped
        assert marker[TOTAL_AVAILABLE_FIELD] == 10, (
            "every asked leg answered — the window is the whole match set this "
            "read could see, so the total is exact and must be published")


class TestAWindowThatFilledItselfIsIncomplete:
    """Saturation IS the proof; it does not need a filter on top (#8447 r5)."""

    def test_a_pool_smaller_than_the_limit_cannot_report_complete(self, sdk, monkeypatch):
        """`pool_size` is an EXACT override and may sit BELOW `limit`."""
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
        _seed(sdk, 20)
        sink: dict = {}
        # pool_size=2 -> str_limit=2 -> every leg is asked for 2 rows and each
        # leg's window saturates, so the handful of delivered rows are a handful
        # of the 20 that matched — and the union stays far below `limit`.
        rows = sdk.tortoise_fts_query("zebra", limit=10, pool_size=2,
                                      _truncation_out=sink)

        assert 0 < len(rows) < 10
        marker = truncation_field(sink)
        assert marker[TRUNCATED_FIELD] is True
        assert TOTAL_AVAILABLE_FIELD not in marker


class TestTheDegradedArmsMarkACut:
    """Pin the TF-IDF arms: the round-1 P1 fix was held by inspection only."""

    @staticmethod
    def _all_legs_failed(monkeypatch):
        # Every retrieval leg produces nothing while the STORE is reachable —
        # the state the degraded arms exist for. Before round 5 no test drove it
        # on a reachable store, so deleting both arms' marker writes kept the
        # whole file green. The BM25 arm is pinned OFF: it re-scores the ``fts``
        # slot AFTER the chain returns, so with it on the read never reaches the
        # TF-IDF arms at all.
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
        monkeypatch.setattr("tortoise.search_engine.degradation_chain",
                            lambda *a, **k: {})

    def test_a_full_page_from_the_fallback_is_never_complete(self, sdk, monkeypatch):
        self._all_legs_failed(monkeypatch)
        _seed(sdk, 20)
        sink: dict = {}
        rows = sdk.tortoise_fts_query("zebra", limit=5, _truncation_out=sink)

        assert len(rows) == 5
        marker = truncation_field(sink)
        assert marker[TRUNCATED_FIELD] is True, (
            "5 rows out of a 20-match store answered by the TF-IDF fallback is "
            "the round-1 P1: it must not read COMPLETE")
        assert TOTAL_AVAILABLE_FIELD not in marker

    def test_a_short_page_from_the_same_fallback_is_complete(self, sdk, monkeypatch):
        """Control: the same arm, a page that was not full, IS complete."""
        self._all_legs_failed(monkeypatch)
        _seed(sdk, 20)
        sink: dict = {}
        rows = sdk.tortoise_fts_query("zebra", limit=50, _truncation_out=sink)

        assert len(rows) == 20
        assert truncation_field(sink) == {TRUNCATED_FIELD: False}


# ── review round 6: which verdicts ANSWER, who OWNS an entry, and WHERE the
# fold is consulted ───────────────────────────────────────────────────────

def _chain_on(leg, hits, entry):
    """A ``degradation_chain`` double putting ``hits`` on ``leg``.

    ``_chain`` (round 5) hard-codes the ``fts`` slot; these tests need the rows
    to survive while a DIFFERENT verdict is recorded, which is the whole point —
    the shape under test is "rows arrived, and a leg's own trace says it never
    answered".
    """
    def _fake(graph, query, kind, query_vec, strategies, **kwargs):
        trace = kwargs.get("leg_trace")
        if trace is not None and entry is not None:
            trace.append(dict(entry))
        return {leg: list(hits)} if hits else {}
    return _fake


class TestTheAnsweredVerdictIsKeyedOnTheLeg:
    """``index_missing`` answers REACHABILITY; it does not answer COMPLETENESS.

    ``read_status._ANSWERED_REASONS`` is correct where it lives ("the server
    replied: no such index" proves the store was reached). The completeness
    proof asks a different question — did this leg DROP matches the read would
    otherwise have delivered? — and for the LEXICAL leg the answer is yes: the
    rows exist and that index is the only route to them.
    """

    @staticmethod
    def _entry(reason, leg="fts"):
        return {"leg": leg, "ran": True, "degraded": True,
                "reason": reason, "count": 0}

    def test_a_missing_lexical_index_is_not_an_answer(self):
        from tortoise.truncation import lost_leg_owners

        assert lost_leg_owners(
            {"fts": True}, [self._entry("index_missing")]) == {"fts"}, (
            "the lexical leg raised and returned nothing while the rows it "
            "would have matched still exist — the read never searched them")

    def test_the_recorded_answer_set_is_not_rewritten(self):
        """The narrowing is in the PROOF, not in read_status's own list."""
        from tortoise.read_status import _ANSWERED_REASONS

        assert "index_missing" in _ANSWERED_REASONS, (
            "read_status still needs index_missing as an answer to ITS question"
            " (was the store reached?) — narrowing that list would be the fix "
            "applied in the wrong home")

    def test_a_leg_that_was_not_asked_is_never_lost(self):
        """A declared capability gap is not a drop (the #8447 docstring rule)."""
        from tortoise.truncation import lost_leg_owners

        assert lost_leg_owners(
            {"fts": False}, [self._entry("query_failed")]) == set()
        assert lost_leg_owners(
            {"vector": True}, [self._entry("no_embeddings", "vector")]) == set(), (
            "no vectors exist, so the dense leg lost nothing by finding none")

    def test_the_answer_reasons_are_still_answers(self):
        from tortoise.truncation import lost_leg_owners

        for reason in ("ok", "empty_results"):
            assert lost_leg_owners({"fts": True}, [self._entry(reason)]) == set()

    def test_the_arms_entries_are_attributed_to_the_arm_not_the_slot(self):
        """Provenance, not the slot name both lexical scorers share.

        The #8488 arm records under ``leg="fts"`` too. Its ``breaker_open``
        branch writes NO ``mechanism`` and NO ``declined`` key, so a key-based
        filter would leave exactly that branch mis-read as the shipped leg
        failing.
        """
        from tortoise.truncation import lost_leg_owners

        shipped = self._entry("breaker_open")
        arm = self._entry("breaker_open")
        assert lost_leg_owners({"fts": True}, [shipped, arm], skip_from=1) == {
            "fts"}, "the SHIPPED leg never answered; the arm's entry after the "\
                     "mark says nothing about it"
        assert lost_leg_owners({"fts": True}, [arm], skip_from=0) == set(), (
            "the shipped leg answered and every row shipped — the arm's own "
            "failure is not a drop")


class TestAMissingLexicalIndexPublishesNoTotal:
    """P2-a, end to end: the read's lexical half never searched."""

    def test_a_dense_only_read_over_an_absent_index_is_not_complete(
            self, sdk, monkeypatch):
        _seed(sdk, 20)
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
        hits = [(pid, 1.0 - i / 100) for i, pid in enumerate(_point_ids(sdk, 3))]
        monkeypatch.setattr(
            "tortoise.search_engine.degradation_chain",
            _chain_on("vector", hits,
                      {"leg": "fts", "ran": True, "degraded": True,
                       "reason": "index_missing", "count": 0}))

        sink: dict = {}
        rows = sdk.tortoise_fts_query("zebra", limit=10, _truncation_out=sink)

        assert len(rows) == 3
        marker = truncation_field(sink)
        assert marker[TRUNCATED_FIELD] is True, (
            "3 rows over a 20-match store whose fulltext index is absent is NOT "
            "a complete answer: the lexical leg never searched")
        assert TOTAL_AVAILABLE_FIELD not in marker, (
            "publishing 3 as the exact total would be the lower bound wearing a "
            "total's clothes that truncation.py forbids")

    def test_the_healthy_seam_still_proves_an_exact_total(self, sdk, monkeypatch):
        """Control: the same double, an answered lexical leg — total published."""
        _seed(sdk, 20)
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
        hits = [(pid, 1.0 - i / 100) for i, pid in enumerate(_point_ids(sdk, 10))]
        monkeypatch.setattr(
            "tortoise.search_engine.degradation_chain",
            _chain_on("fts", hits,
                      {"leg": "fts", "ran": True, "degraded": False,
                       "reason": "ok", "count": len(hits)}))

        sink: dict = {}
        sdk.tortoise_fts_query("zebra", limit=5, _truncation_out=sink)

        marker = truncation_field(sink)
        assert marker[TRUNCATED_FIELD] is True
        assert marker[TOTAL_AVAILABLE_FIELD] == 10


class TestTheLexicalArmsOwnFailureIsNotTheShippedLegs:
    """P2-b, end to end: the #8488 arm DEGRADES; it never fails a read.

    ``run_bm25_query`` records under the same ``leg="fts"`` slot and its
    contract is "the leg degrades, never fails a read" — it returns `[]` and the
    shipped leg's rows survive untouched. Both of its failure branches are
    driven here: a breaker refusal (writes neither ``mechanism`` nor
    ``declined``) and an internal raise (writes ``mechanism="bm25"``).
    """

    def test_the_arm_tripping_its_breaker_does_not_mark_a_complete_read(
            self, sdk, monkeypatch):
        _seed(sdk, 3)
        monkeypatch.delenv("TORTOISE_BM25_LEXICAL", raising=False)  # default ON
        import tortoise.search_engine as _se

        _real_allow = _se._breaker_allow
        monkeypatch.setattr(
            _se, "_breaker_allow",
            lambda name: False if name == "bm25" else _real_allow(name))

        sink: dict = {}
        rows = sdk.tortoise_fts_query("zebra", limit=10, _truncation_out=sink)

        assert len(rows) == 3, "the shipped lexical leg's rows must survive"
        assert truncation_field(sink) == {TRUNCATED_FIELD: False}, (
            "every row the read matched was delivered; marking it truncated is "
            "the false positive that makes the field meaningless")

    def test_the_arm_raising_does_not_mark_a_complete_read(self, sdk, monkeypatch):
        _seed(sdk, 3)
        monkeypatch.delenv("TORTOISE_BM25_LEXICAL", raising=False)  # default ON

        def _boom(*_a, **_k):
            raise RuntimeError("bm25 exploded")

        monkeypatch.setattr("tortoise.bm25.bm25_rank", _boom)

        sink: dict = {}
        rows = sdk.tortoise_fts_query("zebra", limit=10, _truncation_out=sink)

        assert len(rows) == 3
        assert truncation_field(sink) == {TRUNCATED_FIELD: False}

    def test_a_shipped_leg_failure_alongside_the_arm_is_still_lost(
            self, sdk, monkeypatch):
        """The exclusion is by PROVENANCE — it must not swallow the real one."""
        _seed(sdk, 20)
        monkeypatch.delenv("TORTOISE_BM25_LEXICAL", raising=False)  # default ON
        import tortoise.search_engine as _se

        _real_allow = _se._breaker_allow
        monkeypatch.setattr(
            _se, "_breaker_allow",
            lambda name: False if name == "bm25" else _real_allow(name))
        hits = [(pid, 1.0 - i / 100) for i, pid in enumerate(_point_ids(sdk, 5))]
        monkeypatch.setattr(
            "tortoise.search_engine.degradation_chain",
            _chain_on("vector", hits,
                      {"leg": "fts", "ran": True, "degraded": True,
                       "reason": "query_failed", "count": 0}))

        sink: dict = {}
        sdk.tortoise_fts_query("zebra", limit=10, _truncation_out=sink)

        marker = truncation_field(sink)
        assert marker[TRUNCATED_FIELD] is True, (
            "the SHIPPED lexical leg raised and returned nothing — excluding "
            "the arm's entry must not exclude the leg's own")
        assert TOTAL_AVAILABLE_FIELD not in marker


class TestAnEarlyReturnStillConsultsTheFold:
    """P3: two returns leave the read BEFORE the exactness proof.

    Both shipped ``truncated: false`` over a read whose sparse leg never
    answered — 0 rows marked COMPLETE over a store that was only half read.
    """

    def test_a_floored_dense_leg_over_a_failed_sparse_leg_is_not_complete(
            self, sdk, monkeypatch):
        _seed(sdk, 20)
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
        # The #4028 floor is the only way this arm floors the dense leg, and
        # `floored_legs` is only handed to the chain when it is active.
        monkeypatch.setenv("TORTOISE_VECTOR_MIN_SIMILARITY", "0.9")

        def _fake(graph, query, kind, query_vec, strategies, **kwargs):
            floored = kwargs.get("floored_legs")
            if floored is not None:
                floored.add("vector")
            trace = kwargs.get("leg_trace")
            if trace is not None:
                trace.append({"leg": "fts", "ran": True, "degraded": True,
                              "reason": "query_failed", "count": 0})
            return {}

        monkeypatch.setattr("tortoise.search_engine.degradation_chain", _fake)

        sink: dict = {}
        rows = sdk.tortoise_fts_query("zebra", limit=10, _truncation_out=sink)

        assert rows == []
        assert truncation_field(sink)[TRUNCATED_FIELD] is True, (
            "an EMPTY answer over a read whose sparse leg never answered is a "
            "completeness claim over a store that was only half read")

    def test_no_fallback_applicable_still_consults_the_fold(
            self, sdk, monkeypatch):
        _seed(sdk, 20)
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
        monkeypatch.setattr(
            "tortoise.search_engine.degradation_chain",
            _chain_on("fts", [],
                      {"leg": "fts", "ran": True, "degraded": True,
                       "reason": "breaker_open", "count": 0}))

        sink: dict = {}
        rows = sdk.tortoise_fts_query("zebra", limit=10, entity_type="document",
                                      _truncation_out=sink)

        assert rows == []
        assert truncation_field(sink)[TRUNCATED_FIELD] is True


class TestAnArmThatServedTheSlotIsNotALostLeg:
    """The arm's provenance has a SECOND half: delivering proves the slot served.

    An absent fulltext index is the benign embedded state ``run_fts_query``
    documents, and it makes the lexical leg record ``index_missing`` on every
    read. When the #8488 arm — ON by default — re-reads the same slot from the
    graph and delivers the rows, no match was lost, so the read is COMPLETE.
    Judging the owner's ``index_missing`` alone marks every read on that store
    ``truncated: true`` with all rows delivered: the same false positive as
    judging the arm's own failure, one step further on.
    """

    @pytest.fixture(autouse=True)
    def _no_embedder(self, monkeypatch):
        """Pin the DENSE leg off so the degraded TF-IDF tiers are reachable.

        The reason is #1625's, measured here at ``45faa1661`` (PR #11468): the
        dense leg is k-NN with no similarity threshold, so it returns rows for
        EVERY query. A read that has any dense row never enters the
        ``if not raw_results:`` block, so on a lane that HAS an embedder (CI
        installs the embeddings extra) ``tfidf_snapshot`` / ``tfidf_legacy``
        are unreachable and a test that means to pin WHICH tier answered can
        only fail or go vacuous. With ``EmbeddingModel.get() -> None`` the
        vector strategy is never submitted (``sdk.py`` records ``no_embedder``)
        and the fallback fires deterministically — on every lane.

        ⛔ Do NOT replace this with a skip-when-the-dense-leg-is-live: the CI
        lane is the one where the fold is gated, so skipping there is exactly
        the silent hole this class exists to close.
        """
        from tortoise.embeddings import EmbeddingModel

        monkeypatch.setattr(EmbeddingModel, "get",
                            staticmethod(lambda load_timeout=None: None))

    @staticmethod
    def _no_fulltext_index(monkeypatch):
        """The shipped lexical leg raises on a missing index and returns []."""
        import tortoise.search_engine as se
        from tortoise.search_engine import _trace_entry

        def _raise_no_index(graph, query, *, entity_type="point", limit=20,
                            **kwargs):
            trace = kwargs.get("leg_trace")
            if trace is not None:
                trace.append(_trace_entry(
                    "fts", ran=True, degraded=True, reason="index_missing",
                    count=0))
            return []

        monkeypatch.setattr(se, "run_fts_query", _raise_no_index)

    def test_the_arm_delivering_the_slot_excuses_the_missing_index(
            self, sdk, monkeypatch):
        _seed(sdk, 3)
        monkeypatch.delenv("TORTOISE_BM25_LEXICAL", raising=False)  # default ON
        self._no_fulltext_index(monkeypatch)

        trace: list[dict] = []
        sink: dict = {}
        rows = sdk.tortoise_fts_query("zebra", limit=5, _truncation_out=sink,
                                      leg_trace=trace)

        assert len(rows) == 3
        # non-vacuous: the lexical leg really did fail on the absent index …
        assert any(e.get("reason") == "index_missing" for e in trace), trace
        # … and the ARM really did deliver the slot …
        assert any(e.get("mechanism") == "bm25" for e in trace), trace
        # … so every match this read could see was delivered.
        assert truncation_field(sink) == {TRUNCATED_FIELD: False}, (
            "the in-process arm re-read the whole lexical slot from the graph; "
            "marking this read truncated is the false positive that makes the "
            "field meaningless")

    def test_the_same_read_without_the_arm_is_still_lost(self, sdk, monkeypatch):
        """Control: no arm, no second route to the rows — the read IS incomplete."""
        _seed(sdk, 3)
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
        self._no_fulltext_index(monkeypatch)

        sink: dict = {}
        sdk.tortoise_fts_query("zebra", limit=5, _truncation_out=sink)

        assert truncation_field(sink)[TRUNCATED_FIELD] is True, (
            "with the arm off, nothing read the lexical half — the rows are "
            "unreachable and the read may not claim completeness")

    def test_the_legacy_corpus_fallback_is_still_lost(self, sdk, monkeypatch):
        """The SECOND degraded fallback tier, pinned on its own (#10560).

        `tfidf_snapshot` and `tfidf_legacy` are two different returns, and a
        green on the snapshot half does not constrain the legacy half: measured
        on the round-2 head (64 tests, this one absent), deleting ONLY the
        legacy fold left the whole file green; with this test present the same
        deletion gives `1 failed, 64 passed` and the failing one is this. The
        legacy tier is reached by making the cached snapshot unavailable
        (`get_or_build` -> None, the documented route into the legacy path), so
        the path under test is the legacy one.

        Mutation: delete the `_trunc["incomplete"] = bool(_lost_legs)` line in
        the `tfidf_legacy` branch → this reds; the snapshot test stays green.
        """
        _seed(sdk, 3)
        monkeypatch.setenv("TORTOISE_BM25_LEXICAL", "0")
        self._no_fulltext_index(monkeypatch)

        monkeypatch.setattr("tortoise.fallback_snapshot.get_or_build",
                            lambda *a, **k: None)

        trace: list[dict] = []
        sink: dict = {}
        rows = sdk.tortoise_fts_query("zebra", limit=5, _truncation_out=sink,
                                     leg_trace=trace)

        # The TIER is asserted, not assumed: the snapshot tier satisfies both
        # assertions below, so a refactor that hoists the function-local
        # ``get_or_build`` import to module level would leave this test green
        # while it exercised the OTHER fold — re-opening the hole it exists to
        # close, silently.
        _fallback_reasons = [e.get("reason") for e in trace
                             if e.get("leg") == "fallback"]
        assert "tfidf_legacy" in _fallback_reasons, (
            f"this test must exercise the LEGACY tier; got {_fallback_reasons}")
        assert "tfidf_snapshot" not in _fallback_reasons, (
            "the snapshot tier answered instead of the legacy one — the "
            f"legacy fold is then not what this test pins; got {_fallback_reasons}")
        # Non-vacuous: the legacy tier really answered (its rows are ranked from
        # the whole corpus), so `truncated: false` is not an empty-read artifact.
        assert rows, "fixture invariant: the legacy fallback must return rows"
        assert truncation_field(sink)[TRUNCATED_FIELD] is True, (
            "the legacy corpus fallback answers from the whole corpus, not from "
            "the match set the dead lexical leg owed this read — it may not "
            "publish completeness")


# ── #8447 (C2): the four boundary cases on the PAGINATION CONTROL ────────
#
# `paginated_query` is the contrast case: a row past the cap is REACHABLE, so it
# is legitimate pagination and must never be marked `truncated` — but "reachable"
# is only true if the four boundaries actually hold. Each test asserts the
# pagination instrument that replaces the marker (`total` / `hasMore`), because
# "returned 4" alone passes on a walk that cannot reach row 5.

class TestThePaginationControlBoundaries:
    def test_exactly_at_limit_is_one_page_and_not_more(self, sdk):
        """Boundary 1 — limit == total: one page, `hasMore` False, unmarked."""
        _seed(sdk, 4)
        page = sdk.paginated_query(limit=4, skip=0)
        assert len(page["results"]) == 4
        assert page["total"] == 4
        assert page["hasMore"] is False
        assert TRUNCATED_FIELD not in page

    def test_limit_plus_one_needs_a_second_page(self, sdk):
        """Boundary 2 — limit+1: the extra row is a page 2, not a loss."""
        _seed(sdk, 5)
        first = sdk.paginated_query(limit=4, skip=0)
        assert len(first["results"]) == 4
        assert first["hasMore"] is True
        second = sdk.paginated_query(limit=4, skip=4)
        assert len(second["results"]) == 1
        assert second["hasMore"] is False

    def test_page_two_of_two_is_disjoint_and_the_walk_is_complete(self, sdk):
        """Boundary 3 — page 2 of 2: reachable, disjoint, and the walk ends."""
        _seed(sdk, 5)
        first = sdk.paginated_query(limit=4, skip=0)
        second = sdk.paginated_query(limit=4, skip=4)
        ids = [r["id"] for r in first["results"] + second["results"]]
        assert len(ids) == len(set(ids)) == 5
        assert second["hasMore"] is False

    def test_offset_exhaustion_is_an_empty_page_that_says_complete(self, sdk):
        """Boundary 4 — skip == total: empty page, `hasMore` False, total STABLE.

        The instrument is the (`empty`, `hasMore: False`, `total` unchanged)
        triple; a caller that reads only the row count cannot tell an exhausted
        cursor from a truncated one, which is the whole contract.
        """
        _seed(sdk, 2)
        page = sdk.paginated_query(limit=2, skip=2)
        assert page["results"] == []
        assert page["hasMore"] is False
        assert page["total"] == 2
        assert TRUNCATED_FIELD not in page


# ── the census: the artifact #8447's remaining scope is READ FROM ────────
#
# Review of this change found BOTH defects this class pins, and both had
# already shipped once: the census row for hosted ``GET /v1/points`` still read
# **SILENT** after the code started MARKS (P2), and §5 sent C2's mutation
# evidence to a home that did not hold it — first "the issue" (no such comment
# existed), then this PR's body (P3). The census and §5 are what a lane auditing
# C2 reads before it derives anything, so a claim in them is asserted here
# rather than trusted to the review that wrote it.

class TestTheCensusNamesItsEvidenceHome:
    """`docs/architecture/BOUNDED-OPERATION-CONTRACT.md` must agree with the tree
    and point its evidence at a home that HAS the evidence."""

    @staticmethod
    def _doc_text() -> str:
        from pathlib import Path
        doc = (Path(__file__).resolve().parent.parent
               / "docs" / "architecture" / "BOUNDED-OPERATION-CONTRACT.md")
        assert doc.is_file(), f"the contract doc is missing: {doc}"
        return doc.read_text(encoding="utf-8")

    @classmethod
    def _census_section(cls) -> str:
        """§2 (the census + its park/deferral notes), the artifact a lane reads.

        Bounded by its own headings so a line citation elsewhere in the doc is
        out of this pin's scope — the census is what a lane auditing #8447
        starts from, and it is where the drift was measured.
        """
        text = cls._doc_text()
        assert "## 2. The census" in text, "§2 (the census) is gone"
        assert "## 3. `OVERRIDES:`" in text, "§3 (the departures) is gone"
        return text.split("## 2. The census", 1)[1].split(
            "## 3. `OVERRIDES:`", 1)[0]

    def test_the_pin_sees_the_split_form_too(self):
        """Positive control: the split spelling is a line coordinate TOO.

        The pin above is only worth its green if it can red. Its first version
        matched the filename and the digits as one contiguous run, so the form
        §2a actually carried —
        ``config/surface-manifest.yml` (`:121-143`)`` — slipped past it: the
        digits were still in the census, and the pin still said the census
        cited none. This asserts the pattern against that exact string (and
        against the symbol form that replaced it, which must NOT match), so
        narrowing the pattern reds here instead of silently reopening the hole.
        """
        assert _LINE_CITATION.findall(
            "a row in\n`config/surface-manifest.yml` (`:121-143`), **not** as an"), (
            "the census pin no longer sees the split spelling of a line "
            "coordinate, so it can pass over exactly the drift it exists to "
            "catch")
        assert not _LINE_CITATION.findall(
            "a row in `config/surface-manifest.yml` → "
            "`response_fields[tortoise_search].truncated`, **not** as an"), (
            "the census pin reds on the SYMBOL form it recommends — a pin its "
            "own suggested fix cannot satisfy is a trap")

    def test_the_census_cites_symbols_not_line_numbers(self):
        """A line number in the census goes stale on the next unrelated merge.

        Pre-fix every evidence cell was a coordinate (`hosted_api.py:10209`,
        `sdk.py:34655`, ...). They were exact when written and wrong 57 lines
        later — the merge from `origin/main` moved the code, not the doc — so a
        lane following the census was pointed into unrelated event code. That
        is the re-derive-from-a-stale-map failure (#6134) this doc exists to
        prevent, one field over from the state word this class already pins.

        The fix is the form the register in `tortoise/truncation.py` already
        uses and this PR's own scan pin already chose: cite `<module>::<name>`,
        which does not move when somebody inserts a blank line above it.

        ⚠️ BOTH spellings of the coordinate are caught, because the first
        version of this pin only saw the contiguous one. A file named inside a
        code span, with the line in its own parenthesised span —
        ``config/surface-manifest.yml` (`:121-143`)`` — is the same stale
        coordinate one keystroke apart, and it survived that pin while §2a
        carried it. :meth:`test_the_pin_sees_the_split_form_too` is the
        positive control: it reds if this pattern narrows back to contiguous.
        """
        stale = sorted(_LINE_CITATION.findall(self._census_section()))
        assert not stale, (
            "the census cites absolute line numbers, which go stale on every "
            "unrelated edit to the file they point into — cite "
            f"`<module>::<function>` instead: {stale}")

    def test_every_symbol_the_census_cites_resolves_in_the_tree(self):
        """A symbol citation is only staleness-proof if it EXISTS.

        The line-number form was replaced because it drifts; a symbol form has
        the opposite failure mode — a rename or a move leaves the census naming
        something that is not there. This resolves every `<module>::<name>` the
        census cites against the module's own AST (functions, classes and
        module-level bindings such as `TOOL_REGISTRY`), so a rename reds here
        instead of silently misdirecting a lane.
        """
        import ast
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        cited = sorted(set(re.findall(r"(tortoise/[\w/]+\.py)::([A-Za-z_]\w*)",
                                     self._census_section())))
        assert cited, (
            "the census cites no `<module>::<name>` evidence at all — the "
            "pointer form regressed to something this pin cannot check")

        unresolved: list[str] = []
        for module, name in cited:
            path = root / module
            if not path.is_file():
                unresolved.append(f"{module} (module does not exist)")
                continue
            bound: set[str] = set()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)):
                    bound.add(node.name)
                elif isinstance(node, ast.Assign):
                    bound.update(t.id for t in node.targets
                                 if isinstance(t, ast.Name))
                elif isinstance(node, ast.AnnAssign) and isinstance(
                        node.target, ast.Name):
                    bound.add(node.target.id)
            if name not in bound:
                unresolved.append(f"{module}::{name}")
        assert not unresolved, (
            "the census cites symbols the tree does not bind (renamed, moved or "
            f"invented), so its evidence cells point at nothing: {unresolved}")

    @staticmethod
    def _unresolved_symbol_citations(citations) -> list[str]:
        """The `<module>::<name>` citations this tree does not bind.

        Resolution is an AST walk over each cited module (functions, classes and
        module-level bindings such as `TOOL_REGISTRY`), so a rename or a move reds
        the caller instead of silently misdirecting a reader. Shared by the two
        artifacts that cite the census's evidence — §2 of the doc, and the
        `docs/00_index.md` row that routes a lane into it — because the same rot has
        to red in both places or the upstream one drifts again.
        """
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        unresolved: list[str] = []
        for module, name in citations:
            path = root / module
            if not path.is_file():
                unresolved.append(f"{module} (module does not exist)")
                continue
            bound: set[str] = set()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)):
                    bound.add(node.name)
                elif isinstance(node, ast.Assign):
                    bound.update(t.id for t in node.targets
                                 if isinstance(t, ast.Name))
                elif isinstance(node, ast.AnnAssign) and isinstance(
                        node.target, ast.Name):
                    bound.add(node.target.id)
            if name not in bound:
                unresolved.append(f"{module}::{name}")
        return unresolved

    def test_the_index_row_that_routes_readers_here_cites_no_line_numbers(self):
        """The front door must speak the citation form the census speaks.

        `docs/00_index.md`'s #8447 row is how a lane FINDS this census, and it
        kept `sdk.py:28434` / `sdk.py:27022` / `sdk.py:34398` /
        `event_store.py:974` after §2 itself was converted — the exact form the
        pin above forbids, one artifact upstream of it. The coordinates were
        exact when written, and `sdk.py` has already moved 695 lines under them
        once (`62e3f2f61` -> `d72983b99`, #10523), so a lane following them from
        the index lands wherever the last insertion put them. Same rule, same
        artifact family: cite `<module>::<name>`, and resolve it.
        """
        import re
        from pathlib import Path

        index = (Path(__file__).resolve().parent.parent / "docs" / "00_index.md")
        rows = [ln for ln in index.read_text(encoding="utf-8").splitlines()
                if "docs/architecture/BOUNDED-OPERATION-CONTRACT.md" in ln]
        assert len(rows) == 1, (
            "expected exactly ONE docs/00_index.md row routing a reader into the "
            f"census, found {len(rows)}")
        row = rows[0]

        stale = sorted(re.findall(r"[\w./-]+\.(?:py|ya?ml):\d[\w-]*", row))
        assert not stale, (
            "the index row cites absolute line numbers, which re-stale on the "
            "next insertion into the file they point into — cite "
            f"`<module>::<name>` instead: {stale}")

        cited = sorted(set(re.findall(r"(tortoise/[\w/]+\.py)::([A-Za-z_]\w*)",
                                     row)))
        assert cited, (
            "the index row names no `<module>::<name>` evidence this pin can "
            "check — the pointer form regressed to something unresolvable")
        unresolved = self._unresolved_symbol_citations(cited)
        assert not unresolved, (
            "the index row cites symbols the tree does not bind (renamed, moved "
            f"or invented), so a reader is sent to nothing: {unresolved}")

    def test_the_parked_summary_names_the_arm_the_census_measured(self):
        """§2a's opening claim must carry its own scope.

        Read alone, *"All six return a bare list"* is a claim about each whole
        tool, and it contradicted the ⚠️ two paragraphs above it: `tortoise_query`
        is censused on its `text=` arm, whose bare list is real, but its
        STRUCTURAL arm (`text=None`) returns `{"results", "suggestion"}` on one
        branch. A summary that over-reads its own disclosure is how the
        disclosure stops being read — the parked set is the arm, not the tool.
        """
        park = self._census_section().split("### 2a.", 1)[1]
        opening = park.split("All six return", 1)[1].split("\n\n", 1)[0]
        assert "bare list" in opening, (
            f"§2a no longer states what the parked surfaces return: {opening}")
        assert "arm" in opening or "text=" in opening, (
            "§2a's opening claim is not scoped to the arm/route the census "
            "measured, so it reads as a statement about each whole tool — "
            "`tortoise_query`'s structural arm returns `{'results', "
            "'suggestion'}` on one branch (see the ⚠️ above), not a bare list")

    def test_every_test_the_contract_doc_names_exists(self):
        """A pointer to a TEST is only a pointer if that test resolves.

        The other half of the same defect class: §5 sent C2's mutation evidence
        to a node id that existed nowhere in the tree, so a lane auditing C2
        had to decide whether the evidence was fabricated. The evidence home
        (the #8447 comment) now names the real ids, and §5 restates them — so a
        rename has to red here rather than silently orphan the pointer.

        Scoped to §5, where the evidence pointer lives: a node id is only
        checked where it is load-bearing, and §5 carrying none reds (the
        pre-fix state) instead of passing vacuously.
        """
        import ast
        import re
        from pathlib import Path

        text = self._doc_text()
        assert "## 5. Related" in text, "§5 (the evidence pointer) is gone"
        section = text.split("## 5. Related", 1)[1]

        cited = sorted(set(re.findall(
            r"(tests/[\w/]+\.py)::([A-Za-z_]\w*(?:::[A-Za-z_]\w*)?)", section)))
        assert cited, (
            "§5 names no resolvable test node, so its evidence pointer is prose "
            "a lane has to trust rather than grep")

        root = Path(__file__).resolve().parent.parent
        unresolved: list[str] = []
        for module, dotted in cited:
            path = root / module
            if not path.is_file():
                unresolved.append(f"{module} (module does not exist)")
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            found: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    found.add(node.name)
                    # a METHOD is cited `Class::method`, never as a bare name
                    found.update(f"{node.name}::{child.name}"
                                 for child in node.body
                                 if isinstance(child, (ast.FunctionDef,
                                                       ast.AsyncFunctionDef)))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.add(node.name)
            if dotted not in found:
                unresolved.append(f"{module}::{dotted}")
        assert not unresolved, (
            "§5 points at tests the tree does not define (renamed or "
            f"invented), so its evidence home cannot be grepped: {unresolved}")

    def test_the_hosted_points_row_says_marks_and_says_the_total_is_withheld(self):
        """The census row and the tree must say the same thing.

        Pre-fix this row read **SILENT** while `list_points` marked, so a lane
        reading the census would re-open work that had landed — the #6134 shape
        (a conclusion re-derived because the map was stale).
        """
        rows = [ln for ln in self._doc_text().splitlines()
                if ln.startswith("| `GET /v1/points` (hosted")]
        assert len(rows) == 1, (
            f"expected exactly ONE hosted `GET /v1/points` census row, found "
            f"{len(rows)}")
        row = rows[0]
        assert "**MARKS**" in row, f"the census row does not say MARKS: {row}"
        assert "**SILENT**" not in row, row
        # The third state is the point: a truncated page here states THAT more
        # exist, never HOW MANY (`exact=False`), so the row must say so.
        assert "exact=False" in row or "WITHHELD" in row, (
            f"the row does not record that `total_available` is withheld: {row}")

    def test_the_mutation_evidence_points_at_the_issue_not_a_pr_body(self):
        """§5 must send a lane to #8447, because that is where the record is.

        `AGENTS.md` -> DECISION RELAY: the answer lives on the artifact the next
        lane reads. A PR body is neither durable (it is edited, force-pushed and
        eventually buried) nor the record §2's census is written from — and this
        doc's own census is written FROM the issue.
        """
        text = self._doc_text()
        assert "## 5. Related" in text
        section = " ".join(text.split("## 5. Related", 1)[1].split())

        assert "MUTATION-MATRIX" in section, (
            "§5 no longer records the mutation matrix at all — C2's evidence "
            "claim went missing rather than moving home")
        assert "#8447" in section, section
        assert "mutation-evidence comment" in section, (
            f"§5's mutation evidence does not name a comment ON #8447: {section}")
        assert "'s body" not in section, (
            "§5 sends C2's mutation evidence to a PR body, which is not the "
            "artifact a lane reads (and was the P3 finding)")

    def test_the_mutation_claim_names_the_kill_set_it_actually_kills(self):
        """§5's M1/M2 arithmetic must match what the four tests assert.

        M2 is "probe removed, the marker KEPT": with the probe gone
        `candidates == returned`, so `mark_truncation` publishes
        `truncated: False` over a page that IS cut. That reds exactly the tests
        asserting `truncated: True` — 2 of the 4 M1 reds — so the kill sets are
        NESTED (M2 ⊂ M1; the mutation-testing literature calls M2 *subsumed* by
        M1) and the union is 4, not 6.

        §5 said "6 named tests red, disjoint sets". That is the direction that
        costs a lane real coverage: told the mutants are disjoint, a lane
        trimming the boundary set believes each mutant owns its own tests, drops
        one of M2's two reds, and loses the only discrimination against a marker
        that is present but lying. So the sets are DERIVED from the tests here
        instead of trusted — flip one test's `truncated` verdict and the doc's
        claim has to move with it.
        """
        import ast
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        src = (root / "tests" / "test_hosted_api.py").read_text(encoding="utf-8")
        cls = next((n for n in ast.parse(src).body
                    if isinstance(n, ast.ClassDef)
                    and n.name == "TestBoundedPointsListBoundaries"), None)
        assert cls is not None, (
            "the /v1/points boundary class is gone, so §5's mutation matrix has "
            "nothing left to be true about")

        #: M2 (probe removed) turns every page into the `truncated: False`
        #: answer, so it reds exactly the tests asserting `True`.
        asserting: dict[bool, set[str]] = {True: set(), False: set()}
        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef) or not fn.name.startswith("test_"):
                continue
            for verdict in re.findall(r'\["truncated"\] is (True|False)',
                                      ast.get_source_segment(src, fn) or ""):
                asserting[verdict == "True"].add(fn.name)
        assert asserting[True] and asserting[False], (
            "the boundary class no longer asserts BOTH marker directions, so it "
            f"cannot pin a lying marker: {asserting}")

        text = self._doc_text()
        assert "## 5. Related" in text, "§5 (the evidence pointer) is gone"
        bullets = [b for b in re.split(r"\n(?=- )",
                                      text.split("## 5. Related", 1)[1])
                   if "MUTATION-MATRIX" in b]
        assert len(bullets) == 1, (
            "expected exactly ONE §5 bullet carrying the mutation matrix, found "
            f"{len(bullets)}")
        claim = " ".join(bullets[0].split())

        assert "disjoint" not in claim.lower(), (
            "§5 calls the two mutants' kill sets disjoint. They are NESTED — M2 "
            "reds 2 of M1's 4 — so a lane reading 'disjoint' believes each "
            f"mutant owns its own tests and may drop one of M2's two: {claim}")

        named = set(re.findall(r"TestBoundedPointsListBoundaries::([A-Za-z_]\w*)",
                               claim))
        assert named == asserting[True], (
            "§5 names the wrong set as M2's kills. M2 (probe removed, marker "
            "kept) publishes `truncated: False` on a cut page, so it reds "
            f"exactly the `truncated: True` assertions {sorted(asserting[True])}; "
            f"the doc names {sorted(named)}")

        assert re.search(r"strict\s+subset|subsumed", claim, re.I), (
            "§5 no longer says M2's kill set is a strict subset of M1's (M2 is "
            "subsumed by M1) — the fact that stops 4 reds being counted as 6 "
            f"distinct: {claim}")


# ── the marker-site REGISTER: the docstring list a new site must join ────
#
# `tortoise/truncation.py`'s docstring is the authoritative register of the
# surfaces that write the marker, and the ⛔ rule beside it ("A new marker site
# joins this list") only means something if the list is TRUE. It has now shipped
# wrong TWICE as a cardinality claim — first "the only place the marker is
# written" (when `/v1/search` was already the second site), then "TWO sites meet
# it today" (when `sdk.py::issue_insight` had been a third site all along, and
# already had its own `truncated` row in the rendered surface doc). A lane
# auditing #8447 starts from this register, so a count that has quietly gone
# stale makes it conclude a real site does not write the marker — the same
# re-derived-conclusion defect as a stale census row, one level down.

class TestTheMarkerSiteRegisterIsComplete:
    """Every `mark_truncation` call site in the tree is NAMED in the register.

    The list is derived from the tree (an AST scan), never restated here, so
    this test cannot itself go stale: adding a marker site without adding it to
    the register reds, which is exactly what the docstring's ⛔ rule promises.
    That promise was exercised by the merge that landed `hosted_api.py::search`'s
    outage arm on `main` — a NEW site the register did not name, caught here and
    not by review.

    ⚠️ "Derived from the tree" includes the NAME the marker is called under.
    The tree already imports `truncation.mark_truncation` under four local
    aliases besides `_mark_truncation` (`_seed_mark`, `_lost_mark`,
    `_lost_mark2` bound by `tortoise_fts_query`; `_lost_mark` again in
    `hosted_api.py::search`), so a filter that
    hard-codes the two names it happens to recognise sees 3 of the 7 call sites
    and would leave the pin green while a NEW site was written in the aliasing
    pattern the tree already uses three times. The name set is therefore read
    off each module's own `ImportFrom` nodes (:meth:`_marker_aliases`), which is
    the only form a hard-coded list cannot keep up with.
    """

    @staticmethod
    def _module_docstring() -> str:
        import tortoise.truncation as truncation

        assert truncation.__doc__, "tortoise/truncation.py lost its docstring"
        return truncation.__doc__

    @staticmethod
    def _marker_aliases(tree) -> set[str]:
        """Every local name that binds `truncation.mark_truncation` in `tree`.

        Collected from the module's own `ImportFrom` nodes whose module ends in
        ``truncation`` — ``from tortoise.truncation import mark_truncation``,
        ``from .truncation import mark_truncation as _seed_mark`` and any other
        alias a future site invents — instead of a hard-coded name list.
        """
        import ast

        names = {"mark_truncation"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if module.split(".")[-1] != "truncation":
                continue
            for alias in node.names:
                if alias.name == "mark_truncation":
                    names.add(alias.asname or alias.name)
        return names

    @classmethod
    def _scan(cls, root=None) -> set[tuple[str, int, str, str]]:
        """`(module, lineno, name, outermost enclosing function)` per call site.

        Function granularity is what the register is written at, but the NAME
        the marker is called under is what proves the scan matched a given
        call: all three aliased sites live inside `tortoise_fts_query`, so the
        coarse `_call_sites()` set is identical under a two-name filter.
        """
        import ast
        from pathlib import Path

        if root is None:
            root = Path(__file__).resolve().parent.parent / "tortoise"
        root = Path(root)
        assert root.is_dir(), f"the package root is missing: {root}"
        found: set[tuple[str, int, str, str]] = set()
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = cls._marker_aliases(tree)
            parents = {child: node for node in ast.walk(tree)
                       for child in ast.iter_child_nodes(node)}
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (func.attr if isinstance(func, ast.Attribute)
                        else getattr(func, "id", ""))
                if name not in names:
                    continue
                owner = ""
                cursor = node
                while cursor in parents:
                    cursor = parents[cursor]
                    if isinstance(cursor, (ast.FunctionDef,
                                           ast.AsyncFunctionDef)):
                        owner = cursor.name
                found.add((path.relative_to(root.parent).as_posix(),
                           node.lineno, name, owner))
        return found

    @classmethod
    def _call_sites(cls, root=None) -> set[str]:
        """`<module>::<outermost enclosing function>` for every call site.

        The OUTERMOST def, not the innermost: a site inside a helper closure
        still belongs to the entry point a reader can find and grep for.
        """
        return {f"{module}::{owner}"
                for module, _lineno, _name, owner in cls._scan(root)}

    def test_the_scan_sees_every_marker_call_site_in_the_tree(self):
        """All SEVEN call sites, not the three a two-name filter can match.

        The tree calls the marker under `mark_truncation`, `_mark_truncation`,
        and four further alias bindings introduced by
        `from .truncation import mark_truncation as …` — three inside `sdk.py`
        and one in `hosted_api.py` (`search`'s out-of-`ImportFrom` alias
        `_lost_mark`, which `main` added after this register was written).
        Pre-fix the scan matched two hard-coded names, so the aliased calls were
        invisible to it — the mount point for a new site that would have gone
        unpinned while the class docstring promised it could not. Asserted by
        the alias NAME, not by line number, because a line pin breaks on every
        unrelated edit to `sdk.py` while an alias pin only breaks when a site is
        added or moved. The same alias NAME can legitimately appear in two
        modules, so the key is `(module, name)`.
        """
        from collections import Counter

        seen = Counter((module, name)
                       for module, _lineno, name, _owner in self._scan())
        assert seen == Counter({
            ("tortoise/hosted_api.py", "mark_truncation"): 1,
            ("tortoise/hosted_api.py", "_lost_mark"): 1,   # search, outage arm
            ("tortoise/sdk.py", "_mark_truncation"): 2,
            ("tortoise/sdk.py", "_seed_mark"): 1,     # as _seed_mark
            ("tortoise/sdk.py", "_lost_mark"): 1,     # as _lost_mark
            ("tortoise/sdk.py", "_lost_mark2"): 1,    # as _lost_mark2
        }), sorted(seen.items())

    def test_a_new_site_under_an_invented_alias_is_seen(self, tmp_path):
        """A marker site in a NEW function under a THIRD alias name reds.

        This is the exact escape the two-name filter allowed: `as _m` is the
        pattern the tree already uses three times, so it is what the next site
        will look like. Built in a throwaway tree so the assertion is about the
        SCAN, not about a site this repo happens to have today.
        """
        pkg = tmp_path / "tortoise"
        pkg.mkdir()
        (pkg / "new_marker_site.py").write_text(
            "from tortoise.truncation import mark_truncation as _m\n"
            "\n"
            "\n"
            "def a_new_bounded_read(sink, rows, limit):\n"
            "    _m(sink, returned=len(rows[:limit]), candidates=len(rows))\n"
            "    return rows[:limit]\n",
            encoding="utf-8")

        assert self._call_sites(pkg) == {
            "tortoise/new_marker_site.py::a_new_bounded_read"
        }

    def test_the_scan_finds_the_four_sites_the_register_names(self):
        """Guard against a vacuous scan: the AST really does see all four."""
        sites = self._call_sites()
        assert sites == {
            "tortoise/hosted_api.py::list_points",
            "tortoise/hosted_api.py::search",
            "tortoise/sdk.py::issue_insight",
            "tortoise/sdk.py::tortoise_fts_query",
        }, sites

    def test_every_marker_call_site_is_named_in_the_register(self):
        doc = self._module_docstring()
        missing = sorted(s for s in self._call_sites() if s not in doc)
        assert not missing, (
            "these surfaces write `truncated` but the REACHABILITY register in "
            "tortoise/truncation.py does not name them — a lane auditing #8447 "
            f"would conclude they are unmarked: {missing}")

    def test_issue_insight_is_registered_as_a_marker_site(self):
        """The site that was silently absent while the register said TWO.

        Pre-fix the docstring listed the search path and hosted `/v1/points`
        only, so `issue_insight` — a `[:limit]` cut with no offset and no
        cursor, on a tool that already publishes a `truncated` row — was
        invisible to anyone reading the register.
        """
        doc = self._module_docstring()
        assert "tortoise/sdk.py::issue_insight" in doc, (
            "the register omits `issue_insight`, which calls `_mark_truncation` "
            "on a plain `[:limit]` cut — no offset, no cursor")

    def test_the_register_states_no_cardinality_that_can_re_stale(self):
        """A count is a claim that goes stale; the site list cannot.

        Both shipped wordings are pinned out by their own claim form (the
        docstring is allowed to narrate that they went stale — it is not allowed
        to make the claim again).
        """
        doc = " ".join(self._module_docstring().split())  # unwrap the prose
        for stale in ("TWO sites meet it today",
                      "the only place the marker is written"):
            assert stale not in doc, (
                f"the register re-introduced the stale cardinality claim "
                f"{stale!r} — name the sites, let the AST scan keep the count")
